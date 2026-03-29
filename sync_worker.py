import os
import time
import json
import sqlite3
import io
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from playwright.sync_api import sync_playwright

# --- CONFIGURATION ---
DRIVE_FOLDER_ID = os.getenv("DRIVE_FOLDER_ID")
RK_USER = os.getenv("RUNKEEPER_EMAIL")
RK_PASS = os.getenv("RUNKEEPER_PASS")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "600")) # 10 mins
DB_PATH = "/data/sync_history.db"

# --- DATABASE SETUP (To prevent duplicate uploads) ---
def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS uploads (file_id TEXT PRIMARY KEY)")
    conn.commit()
    return conn

def is_uploaded(conn, file_id):
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM uploads WHERE file_id = ?", (file_id,))
    return cur.fetchone() is not None

def mark_as_uploaded(conn, file_id):
    conn.execute("INSERT INTO uploads (file_id) VALUES (?)", (file_id,))
    conn.commit()

# --- GOOGLE DRIVE LOGIC ---
def get_drive_service():
    # Mount your token.json into the container via Secret/ConfigMap
    creds = Credentials.from_authorized_user_file('/app/secrets/token.json')
    return build('drive', 'v3', credentials=creds)

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
                    if 'sameSite' in c and (c['sameSite'] == 'no_restriction' or c['sameSite'] == 'None'):
                        c['sameSite'] = 'None'
                    normalized_cookies.append(c)
                    continue
                
                # 2. "Name raw" / "Content raw" format from User's export tool
                if "Name raw" in c and "Content raw" in c:
                    # Strip protocol and path from domain field
                    host = str(c.get("Host raw", "runkeeper.com")).replace("https://", "").replace("http://", "").split("/")[0]
                    # Playwright expects domains to lead with . for cross-subdomain usage
                    if host and not host.startswith("."):
                        host = "." + host
                        
                    nc = {
                        "name": str(c["Name raw"]),
                        "value": str(c["Content raw"]),
                        "domain": host,
                        "path": c.get("Path raw", "/"),
                    }
                    
                    # Optional: handle secure/httpOnly flags if present
                    if "Send for raw" in c:
                        nc["secure"] = str(c["Send for raw"]).lower() == "true"
                    if "HTTP only raw" in c:
                        nc["httpOnly"] = str(c["HTTP only raw"]).lower() == "true"
                    
                    # Optional: handle expires
                    expires_raw = c.get("Expires raw")
                    if expires_raw:
                        try:
                            nc["expires"] = float(expires_raw)
                        except:
                            pass
                            
                    # Optional: handle sameSite
                    samesite = str(c.get("SameSite raw", "")).lower()
                    if samesite in ["no_restriction", "none"]:
                        nc["sameSite"] = "None"
                    elif samesite in ["strict", "lax"]:
                        nc["sameSite"] = samesite.capitalize()
                        
                    normalized_cookies.append(nc)

            print(f"🍪 Injecting {len(normalized_cookies)} cookies for Runkeeper/ASICS...")
            context.add_cookies(normalized_cookies)
            
        page = context.new_page()

        print(f"🤖 Warming up Bypassing Login with Cookie...")
        
        # 1. Visit the home page first (this "warms up" the session and checks if we're logged in)
        page.goto("https://runkeeper.com/home", wait_until="networkidle")
        home_text = page.text_content("body").lower()
        
        if "log in" in home_text and "sign up" in home_text:
            # Let's check the URL. If it redirected to id.asics.com, we are definitely logged out.
            if "id.asics.com" in page.url:
                raise Exception("❌ Dead Cookie! Session expired on ASICS. Please export a fresh session (All Domains).")
            print("⚠️ Session looks weak (landing page shows Login), but trying upload anyway...")
        else:
            print("✨ Session verified! Successfully bypassed login.")
            
        # 2. Go to the actual upload page
        page.goto("https://runkeeper.com/importActivities", wait_until="networkidle")
        print(f"📍 Landed on: {page.url}")
        
        if "agony of de feet" in page.content().lower():
             raise Exception("❌ 404 Error! Runkeeper doesn't see your session. Make sure you exported ASICS cookies too!")

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
                page.click('button#multiFilesUpload')
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
            page.wait_for_selector('button:has-text("Next")', timeout=15000)
            page.click('button:has-text("Next")')
            print("⏭️ Clicked Next.")
        except Exception:
            pass # Maybe it went straight to Done
            
        # Click "Done" or "Save" based on the 2026 UI layout
        page.wait_for_selector('button:has-text("Done")', timeout=15000)
        page.click('button:has-text("Done")')
        
        print(f"✅ Upload successful.")
        browser.close()

# --- MAIN LOOP ---
def main():
    conn = init_db()
    drive = get_drive_service()
    
    while True:
        try:
            print("🔍 Checking Google Drive for new GPX files...")
            query = f"'{DRIVE_FOLDER_ID}' in parents and name contains '.gpx' and trashed = false"
            results = drive.files().list(q=query, fields="files(id, name)").execute()
            files = results.get('files', [])

            for f in files:
                f_id = f['id']
                f_name = f['name']

                if not is_uploaded(conn, f_id):
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
                        mark_as_uploaded(conn, f_id)
                    finally:
                        if os.path.exists(local_path):
                            os.remove(local_path)

        except Exception as e:
            print(f"❌ Worker Error: {e}")

        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    main()