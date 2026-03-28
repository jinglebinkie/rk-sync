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
        page = context.new_page()

        print(f"🤖 Attempting login for {RK_USER}...")
        page.goto("https://runkeeper.com/login", wait_until="networkidle")
        
        # Handle the ASICS OneTrust Cookie Banner if it appears
        try:
            cookie_btn = page.locator("button:has-text('Accept All Cookies')")
            cookie_btn.click(timeout=5000)
            print("🍪 Accepted cookies.")
        except Exception:
            pass
            
        page.wait_for_selector('input[type="email"]', timeout=15000)
        page.fill('input[type="email"]', RK_USER)
        
        # ASICS OneID often requires clicking "Continue" before password appears
        try:
            # Check if password is on the same page
            page.fill('input[type="password"]', RK_PASS, timeout=3000)
            page.locator('button[type="submit"]').click()
        except:
            # Otherwise click "Continue" first
            page.locator('button:has-text("Continue"), button:has-text("Next")').first.click()
            page.wait_for_selector('input[type="password"]', timeout=10000)
            page.fill('input[type="password"]', RK_PASS)
            page.locator('button[type="submit"]').click()
        
        # Wait for the dashboard or a known logged-in element
        page.wait_for_selector('.nav-item-user', timeout=15000)

        print(f"📂 Uploading {file_path}...")
        # Navigate to the bulk import page (faster than the '+' button wizard)
        page.goto("https://runkeeper.com/importActivities")
        
        # Select the file
        page.set_input_files('input[type="file"]', file_path)
        
        # Click "Done" or "Save" based on the 2026 UI layout
        page.wait_for_selector('button:has-text("Done")', timeout=10000)
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