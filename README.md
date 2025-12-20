# sf-fls-manager
Manage Salesforce Field-Level Security (FLS) using JSON configuration. Features secure, password-less authentication via Salesforce CLI integration.

**CLI-First Design:** Unlike traditional scripts that require hardcoding passwords, this tool integrates natively with the Salesforce CLI (`sf` or `sfdx`). It automatically uses your active local session for secure, password-less authentication.

## 🚀 Features
* **Secure Auth:** Utilises your active Salesforce CLI session (no secrets in code).
* **JSON-Driven:** Define permissions in a readable config file (`permissions.json`).
* **Bulk Optimized:** Fetches all necessary IDs and permissions in just **2 SOQL queries**, regardless of how many fields you are patching.
* **Idempotent:** Only sends API updates if the permission actually needs to change.

## 📦 Prerequisites

### 1. Environment & Tools
1.  **Python 3.x**
2.  **Salesforce Extensions for VS Code**
    * You likely already have this installed.
    * **Authorization:**
        1. Press `Ctrl + Shift + P` (or `Cmd + Shift + P` on Mac).
        2. Type **"SFDX: Authorize an Org"**.
        3. Select your login URL (Production/Sandbox).
        4. **Important:** When asked, enter an **Alias** (e.g., `MySandbox`).

## 2. Salesforce User Permissions
The user running the script must have **System Administrator** access or a profile/permission set with the following specific permissions:

* **API Enabled:** Required to connect via REST API.
* **View Setup and Configuration:** 
* **Manage Profiles and Permission Sets:** Required to provide/update permissions.
    *Note: Standard Salesforce security rules apply. Without this administrative permission, the API will reject updates with an `INSUFFICIENT_ACCESS` error.*

## ⚙️ Installation & Setup

1.  **Clone the repo:**
    ```bash
    git clone https://github.com/mounishreddy27/sf-fls-manager.git
    cd sf-fls-manager
    ```

2.  **Install requirements:**
    ```bash
    pip install -r requirements.txt
    ```

3.  **Configure Target Org:**
    Open `main.py` and update the `TARGET_ORG` variable at the top:
    * **Set the Alias:** `TARGET_ORG = "MySandbox"` (Matches the alias you entered in VS Code)
    * **Or Default:** `TARGET_ORG = ""` (Uses your default org)

## ⚙️ Usage

1. **Define your rules** in `permissions.json`:
   You can assign permissions to **Profiles** (use the standard Profile Name e.g., System Administrator) or **Permission Sets** (use either their API Name or Label).

   ```json
   [
     {
       "sobject": "Account",
       "field": "Account.Active__c",
       "access_rules": {
         "System Administrator": "Edit",     // Profile Name
         "HR_Manager_Access": "Read",        // Permission Set API Name (Recommended)
         "Marketing Team": "Edit"            // Permission Set Label
       }
     }
   ]
   ```

2.  **Run the script:**
    ```bash
    python main.py
    ```

## 📝 Configuration Rules
* **Edit:** Grants `PermissionsRead=true` and `PermissionsEdit=true`.
* **Read:** Grants `PermissionsRead=true` and `PermissionsEdit=false`.
* **None:** Grants `PermissionsRead=false` and `PermissionsEdit=false`.

## ❓ Troubleshooting
* **Error: Authentication Failed / CLI Error**
  * If the script fails to log in, your Refresh Token may have expired.
  * **Fix:** Press `Ctrl+Shift+P` in VS Code and run **"SFDX: Authorize an Org"** to re-authenticate (or run `sf org login web` in the terminal).

## 🛡️ License
MIT License