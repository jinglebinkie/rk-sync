import os
import time
import json
import io
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from playwright.sync_api import sync_playwright
from surrealdb import Surreal

# --- CONFIGURATION ---
DRIVE_FOLDER_ID = os.getenv("DRIVE_FOLDER_ID")
RK_USER = os.getenv("RUNKEEPER_EMAIL")
RK_PASS = os.getenv("RUNKEEPER_PASS")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "600")) # 10 mins
DB_PATH = "/data/sync_history.db" # FOR MIGRATION ONLY
SURREAL_URL = os.getenv("SURREAL_URL", "ws://surrealdb:8000/rpc")
SURREAL_USER = os.getenv("SURREAL_USER", "rk_admin")
SURREAL_PASS = os.getenv("SURREAL_PASS", "rk_pass_123")
DB_NAME = "rk_sync"
NS_NAME = "jinglebinkie"

# --- DATABASE LOGIC (SurrealDB + SQLite Migration) ---
def migrate_old_db(sdb):
    if os.path.exists(DB_PATH):
        import sqlite3
        print("📁 SQLite found. Starting migration to SurrealDB...")
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        try:
            cur.execute("SELECT file_id FROM uploads")
            rows = cur.fetchall()
            for row in rows:
                file_id = row[0]
                # Check if it exists in Surreal defensively
                try:
                    res = sdb.query("SELECT * FROM uploads WHERE file_id = $fid", {"fid": file_id})
                    exists = False
                    if isinstance(res, list) and len(res) > 0 and isinstance(res[0], dict):
                        exists = bool(res[0].get("result"))
                    elif isinstance(res, list):
                        exists = len(res) > 0
                    
                    if not exists:
                        sdb.create("uploads", {"file_id": file_id, "migrated": True, "ts": time.time()})
                        print(f"📦 Migrated {file_id}")
                except Exception as e:
                    print(f"⚠️ Error checking record {file_id}: {e}")
            print(f"✅ Migration complete. {len(rows)} records processed.")
        except Exception as e:
            print(f"⚠️ Migration warning: {e}")
        finally:
            conn.close()

def is_uploaded(sdb, file_id):
    try:
        res = sdb.query("SELECT * FROM uploads WHERE file_id = $fid", {"fid": file_id})
        if isinstance(res, list) and len(res) > 0 and isinstance(res[0], dict):
            # Version 1.x structure: [{'result': [...], 'status': 'OK'}]
            return bool(res[0].get("result"))
        elif isinstance(res, list):
            # Fallback for simpler list-of-records format
            return len(res) > 0
        return False
    except Exception as e:
        print(f"⚠️ is_uploaded check failed: {e}")
        return False

def mark_as_uploaded(sdb, file_id, filename):
    sdb.create("uploads", {
        "file_id": file_id,
        "filename": filename,
        "ts": time.time(),
        "status": "success"
    })

# --- GOOGLE DRIVE LOGIC ---
def get_drive_service():
    creds = Credentials.from_authorized_user_file('/app/secrets/token.json')
    return build('drive', 'v3', credentials=creds)

def get_or_create_archive_folder(service):
    query = f"name = 'archived-and-uploaded' and mimeType = 'application/vnd.google-apps.folder' and '{DRIVE_FOLDER_ID}' in parents and trashed = false"
    results = service.files().list(q=query).execute()
    files = results.get('files', [])
    if files:
        return files[0]['id']
    else:
        print("📁 Creating archive folder in Google Drive...")
        folder_metadata = {
            'name': 'archived-and-uploaded',
            'mimeType': 'application/vnd.google-apps.folder',
            'parents': [DRIVE_FOLDER_ID]
        }
        folder = service.files().create(body=folder_metadata, fields='id').execute()
        return folder.get('id')

def archive_file_in_drive(service, file_id, archive_folder_id):
    try:
        # Retrieve the current parents to remove them
        file = service.files().get(fileId=file_id, fields='parents').execute()
        previous_parents = ",".join(file.get('parents'))
        # Move the file
        service.files().update(
            fileId=file_id,
            addParents=archive_folder_id,
            removeParents=previous_parents,
            fields='id, parents'
        ).execute()
        print(f"📦 File moved to archive folder.")
    except Exception as e:
        print(f"⚠️ Error archiving file: {e}")

# --- RUNKEEPER UPLOAD LOGIC ---
def upload_to_runkeeper(file_path):
    with sync_playwright() as p:
        # Launch browser (headless by default)
        # Firefox bypasses the ASICS WAF much better than Chromium
        browser = p.firefox.launch()
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        
        # Load cookies
        cookie_path = '/app/secrets/rk_cookies.json'
        if not os.path.exists(cookie_path):
            raise Exception(f"❌ Missing {cookie_path}! Did you seal the cookie secret?")
            
        with open(cookie_path, 'r') as f:
            cookies = json.load(f)
            
            normalized_cookies = []
            for c in cookies:
                # 1. Standard Playwright format (name/value)
                if "name" in c and "value" in c:
                    normalized_cookies.append(c)
                    continue
                
                # 2. "Name raw" / "Content raw" format from User's export tool
                if "Name raw" in c and "Content raw" in c:
                    # Strip protocol and path from domain field
                    host = str(c.get("Host raw", "runkeeper.com")).replace("https://", "").replace("http://", "").split("/")[0]
                    # We inject TWICE: once for the exact domain and once for the wildcard .domain 
                    # to make sure the browser sees it regardless of Playwright's strict matching.
                    clean_host = host.lstrip(".")
                    wildcard_host = "." + clean_host
                    
                    for h in [clean_host, wildcard_host]:
                        nc = {
                            "name": str(c["Name raw"]),
                            "value": str(c["Content raw"]),
                            "domain": h,
                            "path": c.get("Path raw", "/"),
                        }
                        # Handle secure/httpOnly flags if present
                        if "Send for raw" in c:
                            nc["secure"] = str(c["Send for raw"]).lower() == "true"
                        if "HTTP only raw" in c:
                            nc["httpOnly"] = str(c["HTTP only raw"]).lower() == "true"
                        
                        normalized_cookies.append(nc)

            print(f"🍪 Hammering {len(normalized_cookies)} cookies into the browser context...")
            context.add_cookies(normalized_cookies)
            
        page = context.new_page()

        print(f"🤖 Warming up Bypassing Login with Cookie...")
        
        # 1. Visit the home page first
        page.goto("https://runkeeper.com/home", wait_until="networkidle")
        home_text = page.text_content("body").lower()
        
        if "log in" in home_text and "sign up" in home_text:
            if "id.asics.com" in page.url:
                raise Exception("❌ Dead Cookie! Logged out by ASICS. Please export a fresh session (All Domains).")
            print("⚠️ Session looks weak, but trying upload anyway...")
        else:
            print("✨ Session verified! Successfully bypassed login.")
            
        # 2. Go to the actual upload page
        page.goto("https://runkeeper.com/new/activity", wait_until="networkidle")
        print(f"📍 Landed on: {page.url}")
        
        # --- HANDLE COOKIE CONSENT (OneTrust) ---
        try:
            # Look for the "Accept" button on the OneTrust banner
            accept_btn = page.locator("#onetrust-accept-btn-handler")
            if accept_btn.is_visible(timeout=5000):
                print("🍪 Clearing cookie consent banner...")
                accept_btn.click()
                page.wait_for_timeout(1000) # Let it fade out
        except Exception:
            pass # No banner, or it's already gone

        try:
            # Look for the "Get started" button with ID multiFilesUpload
            page.wait_for_selector('button#multiFilesUpload', timeout=20000)
            print("✨ Found Upload Button!")
        except Exception:
            raise Exception(f"❌ Timed out waiting for upload button. URL: {page.url}")

        print(f"📂 Uploading {file_path}...")
        
        # Select the file by clicking the "Get started" button and then handling the file chooser
        try:
            with page.expect_file_chooser() as fc_info:
                # Use force=True to bypass any sneaky overlays
                page.click('button#multiFilesUpload', force=True)
            file_chooser = fc_info.value
            file_chooser.set_files(file_path)
            print("📤 File selected via chooser.")
        except Exception as e:
            # Fallback to the direct hidden input if the button click wasn't necessary or didn't work
            print(f"⚠️ File chooser failed ({e}), trying direct input...")
            page.set_input_files('input[type="file"]', file_path)
        
        # Wait for "Next" or "Done" button. After selection, Runkeeper usually processes and then shows a "Next" button.
        try:
            # New Runkeeper UI often has a "Next" button after selection
            # We use a combined selector for whatever button comes up next
            page.wait_for_selector('button:has-text("Next"), button:has-text("Done"), button:has-text("Save")', timeout=30000)
            
            # Click "Next" if it's there
            if page.locator('button:has-text("Next")').is_visible():
                page.click('button:has-text("Next")')
                print("⏭️ Clicked Next.")
                page.wait_for_timeout(2000) # Wait for final screen

            # Click "Done" or "Save"
            final_btn = page.locator('button:has-text("Done"), button:has-text("Save")').first
            if final_btn.is_visible():
                final_btn.click()
                print("💾 Clicked Done/Save.")
            
            print(f"✅ Upload successful.")
        except Exception as e:
            print(f"⚠️ Finalization step failed or wasn't needed: {e}")
            
        browser.close()

# --- MAIN LOOP ---
def main():
    drive = get_drive_service()
    
    # Using context manager for SurrealDB connection (most robust pattern)
    try:
        with Surreal(SURREAL_URL) as sdb:
            print("🚀 SurrealDB Connected!")
            sdb.signin({"username": SURREAL_USER, "password": SURREAL_PASS})
            sdb.use(NS_NAME, DB_NAME)
            
            # Run migration inside the connection
            migrate_old_db(sdb)
            
            while True:
                try:
                    archive_folder_id = get_or_create_archive_folder(drive)
                    
                    print("🔍 Checking Google Drive for new GPX files...")
                    query = f"'{DRIVE_FOLDER_ID}' in parents and name contains '.gpx' and trashed = false"
                    results = drive.files().list(q=query, fields="files(id, name)").execute()
                    files = results.get('files', [])

                    for f in files:
                        f_id = f['id']
                        f_name = f['name']

                        if not is_uploaded(sdb, f_id):
                            print(f"✨ New file detected: {f_name}")
                            
                            # Download temporarily
                            request = drive.files().get_media(fileId=f_id)
                            local_path = f"/tmp/{f_name}"
                            with open(local_path, "wb") as fh:
                                downloader = MediaIoBaseDownload(fh, request)
                                done = False
                                while not done:
                                    _, done = downloader.next_chunk()

                            # Execute Browser Upload
                            try:
                                upload_to_runkeeper(local_path)
                                # Mark in SurrealDB
                                mark_as_uploaded(sdb, f_id, f_name)
                                # Move to archive folder in Google Drive
                                archive_file_in_drive(drive, f_id, archive_folder_id)
                            except Exception as e:
                                print(f"❌ Worker Error: {e}")
                            finally:
                                if os.path.exists(local_path):
                                    os.remove(local_path)

                    print(f"💤 Sleeping for {POLL_INTERVAL}s...")
                    time.sleep(POLL_INTERVAL)

                except Exception as e:
                    print(f"❌ Main Loop Error: {e}")
                    time.sleep(30)
    except Exception as e:
        print(f"❌ Global SurrealDB Error: {e}")
        time.sleep(10)

if __name__ == "__main__":
    main()