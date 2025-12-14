import json
import os
import sys
from simple_salesforce import Salesforce
from cli_helper import get_cli_session

CONFIG_FILE = 'permissions.json'
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

def main():
    print("--- Salesforce Permission Set Patcher ---")

    sf = get_salesforce_connection()

    # 1. Load Config
    try:
        with open(CONFIG_FILE, 'r') as f:
            field_configs = json.load(f)
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
    print("Processing updates...")
    
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
                if (current_rec['PermissionsRead'] != target_perms['PermissionsRead']) or \
                   (current_rec['PermissionsEdit'] != target_perms['PermissionsEdit']):
                    
                    sf.FieldPermissions.update(current_rec['Id'], {
                        'PermissionsRead': target_perms['PermissionsRead'],
                        'PermissionsEdit': target_perms['PermissionsEdit']
                    })
                    print(f"  [*] Updated {pset_name} on {field}")
                else:
                    # 2. No change needed (Values are already correct)
                    print(f"  [=] No change needed for {pset_name} on {field}")
            else:
                if target_perms['PermissionsRead'] is True:
                    sf.FieldPermissions.create({
                        'ParentId': pset_id,
                        'SobjectType': sobject,
                        'Field': field,
                        'PermissionsRead': target_perms['PermissionsRead'],
                        'PermissionsEdit': target_perms['PermissionsEdit']
                    })
                    print(f"  [+] Granted {pset_name} on {field}")
                else:
                    print(f"  [-] Skipped {pset_name} (Access is None and no record exists)")

    print("\n--- Completed ---")

if __name__ == "__main__":
    main()