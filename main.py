import json
import os
import sys
from simple_salesforce import Salesforce
from cli_helper import get_cli_session

# CONFIG_FILE = 'permissions.json'
CONFIG_FILE = r"H:\_gen_d\permissions.json"
TARGET_ORG = 'sflwc'

def get_boolean_perms(access_level):
    access_level = access_level.lower()
    if access_level == 'edit':
        return {'PermissionsRead': True, 'PermissionsEdit': True}
    elif access_level == 'read':
        return {'PermissionsRead': True, 'PermissionsEdit': False}
    else:
        return {'PermissionsRead': False, 'PermissionsEdit': False}

def get_salesforce_connection():
    print("Attempting login via Salesforce CLI...")
    # 1. Check if the variable has a real value
    if TARGET_ORG:
        # If yes, pass it explicitly
        token, instance = get_cli_session(TARGET_ORG)
    else:
        # If no, uses the default org
        token, instance = get_cli_session()
    sf = Salesforce(instance_url=instance, session_id=token)
    print(f"SUCCESS: Logged in via CLI ({instance})")
    return sf

# Helper to chunk into groups of 200 (API Limit)
def chunker(seq, size):
    return (seq[pos:pos + size] for pos in range(0, len(seq), size))

def main():
    print("--- Salesforce Permission Set Patcher ---")

    sf = get_salesforce_connection()

    # Construct the FULL URL
    # sf.base_url already ends with a slash '/'
    full_url = sf.base_url + "composite/sobjects"
    print(f"Using Composite SObject URL: {full_url}\n")
    # 1. Load Config
    try:
        with open(CONFIG_FILE, 'r') as f:
            raw_configs = json.load(f)

        # DEDUPLICATION LOGIC
        # Create a dictionary to remove duplicates based on the "field" name
        unique_map = {item['field']: item for item in raw_configs}
        field_configs = list(unique_map.values())

        if len(raw_configs) != len(field_configs):
            print(f"⚠️ Removed {len(raw_configs) - len(field_configs)} duplicate fields from input.")

    except FileNotFoundError:
        print(f"Error: {CONFIG_FILE} not found.")
        return

    # 2. Preparation - Collect Names & Fields
    all_pset_names = set()
    all_target_fields = set()

    for item in field_configs:
        all_target_fields.add(item['field'])
        all_pset_names.update(item['access_rules'].keys())

    if not all_pset_names:
        print("No permission sets found in config.")
        return

    # 3. Bulk Query - Permission Set IDs
    print(f"Resolving IDs for {len(all_pset_names)} Permission Sets...")
    ps_name_str = "('" + "','".join(all_pset_names) + "')"
    ps_query = f"SELECT Id, Name FROM PermissionSet WHERE Name IN {ps_name_str}"
    ps_records = sf.query(ps_query)['records']
    pset_name_to_id = {rec['Name']: rec['Id'] for rec in ps_records}
    found_ids = list(pset_name_to_id.values())

    # 4. Bulk Query - Existing Field Permissions
    print(f"Snapshotting existing permissions for {len(all_target_fields)} fields...")
    if not found_ids:
        print("No valid Permission Sets found. Exiting.")
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
    # Lists to hold our "bulk" changes
    update_batch = []
    create_batch = []

    print("\n--- Processing Rules ---")
    
    for item in field_configs:
        field = item['field']
        sobject = item['sobject']
        rules = item['access_rules']

        for pset_name, access_level in rules.items():
            if pset_name not in pset_name_to_id:
                print(f"  [!] Skipped {pset_name}: Not found in Org.")
                continue
                
            pset_id = pset_name_to_id[pset_name]
            target_perms = get_boolean_perms(access_level)
            composite_key = (pset_id, field)
            
            if composite_key in existing_perms_map:
                current_rec = existing_perms_map[composite_key]
                
                # Check if change is needed
                if (current_rec['PermissionsRead'] != target_perms['PermissionsRead']) or \
                    (current_rec['PermissionsEdit'] != target_perms['PermissionsEdit']):
                    
                    # ADD TO LIST (Don't update yet)
                    update_batch.append({
                        "attributes": {"type": "FieldPermissions"},
                        "Id": current_rec['Id'],
                        "PermissionsRead": target_perms['PermissionsRead'],
                        "PermissionsEdit": target_perms['PermissionsEdit']
                    })
                    print(f"  [*] Queued Update: {pset_name} on {field}")
                else:
                    # 2. No change needed (Values are already correct)
                    print(f"  [=] No change needed for {pset_name} on {field}")
            else:
                if target_perms['PermissionsRead'] is True:
                    # ADD TO CREATE LIST
                    create_batch.append({
                        "attributes": {"type": "FieldPermissions"},
                        "ParentId": pset_id,
                        "SobjectType": sobject,
                        "Field": field,
                        "PermissionsRead": target_perms['PermissionsRead'],
                        "PermissionsEdit": target_perms['PermissionsEdit']
                    })
                    print(f"  [+] Queued Create: {pset_name} on {field}")
                    print(f"  [+] Granted {pset_name} on {field}")
                else:
                    print(f"  [-] Skipped {pset_name} (Access is None and no record exists)")

    session = sf.session
    # 1. Handle Updates (Using Composite SObject Collections)
    # This endpoint allows up to 200 updates per call
    if update_batch:
        print(f"\n--- Committing {len(update_batch)} Updates ---")

        for batch in chunker(update_batch, 200):
            try:
                # 2. USE RAW SESSION (Version Agnostic)
                response = session.request(
                    method='PATCH',
                    url=full_url,
                    json={'allOrNone': False, 'records': batch},
                    headers=sf.headers # CRITICAL: Must pass auth headers manually
                )
                
                if response.status_code != 200:
                    print(f"  [!] HTTP {response.status_code}: {response.text}")
                    continue

                for res in response.json():
                    if res['success']:
                        print(f"  [✓] Updated: {res['id']}")
                    else:
                        print(f"  [!] Error: {res['errors']}")
                # print(f"DEBUG CHECK: Type of sf is {type(sf)}")
                # # We use sf.request directly to hit the Collections API
                # response = sf.request(
                #     method='PATCH', 
                #     url="https://diksuchi-dev-ed.develop.my.salesforce.com/services/data/v63.0/composite/sobjects", 
                #     json={'allOrNone': False, 'records': batch}
                # )
                
                # # Check for errors in the response list
                # for res in response:
                #     if not res['success']:
                #         print(f"  [!] Error updating {res['id']}: {res['errors']}")
                #     else:
                #         print(f"  [✓] Updated {res['id']}")
                        
            except Exception as e:
                print(f"  [!] Critical Batch Error: {e}")

    # 2. Handle Creates
    # Simple-Salesforce 'sf.bulk' is great for inserts if you have many
    # Or use the same composite/sobjects POST method for smaller batches
    if create_batch:
        print(f"\n--- Committing {len(create_batch)} Creates ---")
        for batch in chunker(create_batch, 200):
            try:
                # 2. USE RAW SESSION (Version Agnostic)
                response = session.request(
                    method='POST',
                    url=full_url,
                    json={'allOrNone': False, 'records': batch},
                    headers=sf.headers # CRITICAL: Must pass auth headers manually
                )
                
                if response.status_code != 200:
                    print(f"  [!] HTTP {response.status_code}: {response.text}")
                    continue

                for res in response.json():
                    if res['success']:
                        print(f"  [✓] Created: {res['id']}")
                    else:
                        print(f"  [!] Error: {res['errors']}")
                # print(f"DEBUG CHECK: Type of sf is {type(sf)}")
                # response = sf.request(
                #     method='POST', 
                #     url="https://diksuchi-dev-ed.develop.my.salesforce.com/services/data/v63.0/composite/sobjects", 
                #     json={'allOrNone': False, 'records': batch}
                # )
                # for res in response:
                #     if not res['success']:
                #         print(f"  [!] Error creating: {res['errors']}")
                #     else:
                #         print(f"  [✓] Created {res['id']}")
            except Exception as e:
                print(f"  [!] Critical Create Error: {e}")
    
    print("\n--- Completed ---")
    print("\n==================================")
    print(" 📊 API USAGE REPORT")
    print("==================================")
    
    try:
        # Use the official method to fetch limits
        limits_data = sf.limits()
        
        # Extract specific API metrics
        api_requests = limits_data.get('DailyApiRequests', {})
        max_calls = api_requests.get('Max', 0)
        remaining = api_requests.get('Remaining', 0)
        used_today = max_calls - remaining

        print(f"Daily Limit:   {max_calls}")
        print(f"Remaining:     {remaining}")
        print(f"Used Today:    {used_today}")
        
    except Exception as e:
        print(f"Could not fetch limits: {e}")

if __name__ == "__main__":
    main()