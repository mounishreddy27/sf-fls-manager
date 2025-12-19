import json
import os
import sys
import logging
from simple_salesforce import Salesforce
from cli_helper import get_cli_session

# CONFIGURATION
CONFIG_FILE = 'permissions.json'
TARGET_ORG = 'alias'
LOG_FILE = 'fls_patcher.log'

# ==========================================
# SETUP LOGGING
# ==========================================
# Create a custom logger
logger = logging.getLogger("FLS_Patcher")
logger.setLevel(logging.INFO)

# A. File Handler (Detailed with Timestamps)
file_handler = logging.FileHandler(LOG_FILE, mode='w', encoding='utf-8')
file_fmt = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
file_handler.setFormatter(file_fmt)
logger.addHandler(file_handler)

# B. Console Handler (Clean Output)
console_handler = logging.StreamHandler(sys.stdout)
console_fmt = logging.Formatter('%(message)s')
console_handler.setFormatter(console_fmt)
logger.addHandler(console_handler)

def get_boolean_perms(access_level):
    access_level = access_level.lower()
    if access_level == 'edit':
        return {'PermissionsRead': True, 'PermissionsEdit': True}
    elif access_level == 'read':
        return {'PermissionsRead': True, 'PermissionsEdit': False}
    else:
        return {'PermissionsRead': False, 'PermissionsEdit': False}

def get_salesforce_connection():
    logger.info("Attempting login via Salesforce CLI...")
    if TARGET_ORG:
        token, instance = get_cli_session(TARGET_ORG)
    else:
        token, instance = get_cli_session()
    
    sf = Salesforce(instance_url=instance, session_id=token)
    logger.info(f"SUCCESS: Logged in via CLI ({instance})")
    return sf

def chunker(seq, size):
    return (seq[pos:pos + size] for pos in range(0, len(seq), size))

def main():
    logger.info("--- Salesforce Permission Set Patcher ---")

    sf = get_salesforce_connection()
    
    # Use RAW Session for stability
    session = sf.session

    # Construct the FULL URL for Standard API
    full_url = sf.base_url + "composite/sobjects"
    logger.info(f"Using Composite SObject URL: {full_url}\n")

    # 1. Load Config
    try:
        with open(CONFIG_FILE, 'r') as f:
            raw_configs = json.load(f)

        # Deduplication
        unique_map = {item['field']: item for item in raw_configs}
        field_configs = list(unique_map.values())

        if len(raw_configs) != len(field_configs):
            logger.warning(f"⚠️ Removed {len(raw_configs) - len(field_configs)} duplicate fields from input.")

    except FileNotFoundError:
        logger.error(f"Error: {CONFIG_FILE} not found.")
        return

    # 2. Preparation - Collect Names & Fields
    all_pset_names = set()
    all_target_fields = set()

    for item in field_configs:
        all_target_fields.add(item['field'])
        all_pset_names.update(item['access_rules'].keys())

    if not all_pset_names:
        logger.error("No permission sets found in config.")
        return

    # 3. Bulk Query - Smart ID Resolution (API Name, Label, & Profile)
    logger.info(f"Resolving IDs for {len(all_pset_names)} names (Profiles & Permission Sets)...")
    
    safe_names = [x.replace("'", "\\'") for x in all_pset_names]
    name_clause = "('" + "','".join(safe_names) + "')"

    # Query matching API Name, Label, or Profile Name
    ps_query = f"""
        SELECT Id, Name, Label, Profile.Name, IsOwnedByProfile 
        FROM PermissionSet 
        WHERE Name IN {name_clause} 
           OR Label IN {name_clause}
           OR Profile.Name IN {name_clause}
    """
    
    ps_records = sf.query(ps_query)['records']
    pset_name_to_id = {}
    
    # Helper to track duplicates (Two perm sets with same Label?)
    found_labels = {} 

    for rec in ps_records:
        ps_id = rec['Id']
        api_name = rec['Name']
        label = rec['Label']
        profile_rec = rec.get('Profile') 
        
        # 1. Match against PROFILE Name (Priority 1)
        if rec['IsOwnedByProfile'] and profile_rec:
            p_name = profile_rec['Name']
            if p_name in all_pset_names:
                pset_name_to_id[p_name] = ps_id
        
        # 2. Match against API Name (Priority 2)
        if api_name in all_pset_names:
            pset_name_to_id[api_name] = ps_id

        # 3. Match against LABEL (Priority 3 - User Friendly)
        if label in all_pset_names:
            # Check for Ambiguity
            if label in found_labels and found_labels[label] != ps_id:
                logger.warning(f"  [!] AMBIGUITY: Multiple sets share the label '{label}'. Using the first one found.")
            else:
                pset_name_to_id[label] = ps_id
                found_labels[label] = ps_id

    # Report results
    logger.info(f"  [+] Resolved {len(pset_name_to_id)} out of {len(all_pset_names)} targets.")
    
    missing = all_pset_names - set(pset_name_to_id.keys())
    if missing:
        logger.warning(f"  [!] MISSING: Could not find these in Org: {missing}")
        logger.warning("      (Check for typos. If using Permission Sets, try the API Name.)")
        
    found_ids = list(pset_name_to_id.values())

    # 4. Bulk Query - Existing Field Permissions
    logger.info(f"Snapshotting existing permissions for {len(all_target_fields)} fields...")
    if not found_ids:
        logger.error("No valid Permission Sets found. Exiting.")
        return

    ids_in_clause = "('" + "','".join(found_ids) + "')"
    fields_in_clause = "('" + "','".join(all_target_fields) + "')"

    fp_query = f"""
        SELECT Id, ParentId, Field, PermissionsRead, PermissionsEdit 
        FROM FieldPermissions 
        WHERE ParentId IN {ids_in_clause} 
        AND Field IN {fields_in_clause}
    """
    
    existing_perms_records = sf.query_all(fp_query)['records']
    existing_perms_map = {}
    
    for rec in existing_perms_records:
        key = (rec['ParentId'], rec['Field'])
        existing_perms_map[key] = rec

    # 5. Process Logic
    update_batch = []
    create_batch = []

    logger.info("\n--- Processing Rules ---")
    
    for item in field_configs:
        field = item['field']
        sobject = item['sobject']
        rules = item['access_rules']

        for pset_name, access_level in rules.items():
            if pset_name not in pset_name_to_id:
                logger.warning(f"  [!] Skipped {pset_name}: Not found in Org.")
                continue
                
            pset_id = pset_name_to_id[pset_name]
            target_perms = get_boolean_perms(access_level)
            composite_key = (pset_id, field)
            
            if composite_key in existing_perms_map:
                current_rec = existing_perms_map[composite_key]
                
                # Check if change is needed
                if (current_rec['PermissionsRead'] != target_perms['PermissionsRead']) or \
                   (current_rec['PermissionsEdit'] != target_perms['PermissionsEdit']):
                    
                    update_batch.append({
                        "attributes": {"type": "FieldPermissions"},
                        "Id": current_rec['Id'],
                        "PermissionsRead": target_perms['PermissionsRead'],
                        "PermissionsEdit": target_perms['PermissionsEdit']
                    })
                    logger.info(f"  [*] Queued Update: {pset_name} on {field}")
                else:
                    logger.info(f"  [=] No change needed for {pset_name} on {field}")
            else:
                if target_perms['PermissionsRead'] is True:
                    create_batch.append({
                        "attributes": {"type": "FieldPermissions"},
                        "ParentId": pset_id,
                        "SobjectType": sobject,
                        "Field": field,
                        "PermissionsRead": target_perms['PermissionsRead'],
                        "PermissionsEdit": target_perms['PermissionsEdit']
                    })
                    logger.info(f"  [+] Queued Create: {pset_name} on {field}")
                else:
                    logger.info(f"  [-] Skipped {pset_name} (Access is None and no record exists)")

    # 6. Execute Updates (PATCH)
    if update_batch:
        logger.info(f"\n--- Committing {len(update_batch)} Updates ---")

        for batch in chunker(update_batch, 200):
            try:
                # Use RAW SESSION (Bypasses 'SFType' errors)
                response = session.request(
                    method='PATCH',
                    url=full_url,
                    json={'allOrNone': False, 'records': batch},
                    headers=sf.headers
                )
                
                if response.status_code != 200:
                    logger.error(f"  [!] HTTP {response.status_code}: {response.text}")
                    continue

                results = response.json()
                for res in results:
                    if res['success']:
                        logger.info(f"  [✓] Updated: {res['id']}")
                    else:
                        logger.error(f"  [!] Error: {res['errors']}")
                        
            except Exception as e:
                logger.critical(f"  [!] Critical Batch Error: {e}")

    # 7. Execute Creates (POST)
    if create_batch:
        logger.info(f"\n--- Committing {len(create_batch)} Creates ---")
        
        for batch in chunker(create_batch, 200):
            try:
                # Use RAW SESSION (Bypasses 'SFType' errors)
                response = session.request(
                    method='POST',
                    url=full_url,
                    json={'allOrNone': False, 'records': batch},
                    headers=sf.headers
                )
                
                if response.status_code != 200:
                    logger.error(f"  [!] HTTP {response.status_code}: {response.text}")
                    continue

                results = response.json()
                for res in results:
                    if res['success']:
                        logger.info(f"  [✓] Created: {res['id']}")
                    else:
                        logger.error(f"  [!] Error: {res['errors']}")

            except Exception as e:
                logger.critical(f"  [!] Critical Create Error: {e}")
    
    logger.info("\n--- Completed ---")
    logger.info("\n==================================")
    logger.info(" 📊 API USAGE REPORT")
    logger.info("==================================")
    
    try:
        limits_data = sf.limits()
        api_requests = limits_data.get('DailyApiRequests', {})
        max_calls = api_requests.get('Max', 0)
        remaining = api_requests.get('Remaining', 0)
        used_today = max_calls - remaining

        logger.info(f"Daily Limit:   {max_calls}")
        logger.info(f"Remaining:     {remaining}")
        logger.info(f"Used Today:    {used_today}")
        
    except Exception as e:
        logger.error(f"Could not fetch limits: {e}")

if __name__ == "__main__":
    main()