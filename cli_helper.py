import json
import shutil
import subprocess

def get_cli_session(target_org=None):
    """
    Retrieves the access token and instance URL from the Salesforce CLI.
    Mimics the logic submitted in the simple-salesforce PR.
    """
    # 1. Verify CLI is installed
    sf_exec = shutil.which('sf') or shutil.which('sfdx')
    if not sf_exec:
        raise ValueError("Salesforce CLI not found. Please install 'sf' or 'sfdx'.")

    # 2. Build command
    cmd = [sf_exec, 'org', 'display', '--json']
    if target_org:
        cmd.extend(['--target-org', target_org])

    # 3. Execute
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, check=True
        )
    except subprocess.CalledProcessError as e:
        err_msg = e.stderr if e.stderr else str(e)
        raise ValueError(f"Salesforce CLI failed: {err_msg}")

    # 4. Parse Output
    try:
        data = json.loads(result.stdout)
        result_data = data.get('result', {})
        access_token = result_data.get('accessToken')
        instance_url = result_data.get('instanceUrl')

        if not access_token or not instance_url:
            raise ValueError("CLI output missing 'accessToken' or 'instanceUrl'.")

        return access_token, instance_url

    except json.JSONDecodeError:
        raise ValueError("Failed to parse JSON output from Salesforce CLI.")