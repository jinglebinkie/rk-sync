import os.path
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

# We only need read-only access to Drive
SCOPES = ['https://www.googleapis.com/auth/drive.readonly']

def main():
    creds = None
    # Check if token.json already exists
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
        
    # If no valid credentials, log the user in
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists('credentials.json'):
                print("❌ ERROR: credentials.json not found!")
                print("Please download it from Google Cloud Console and place it in this directory.")
                return

            flow = InstalledAppFlow.from_client_secrets_file(
                'credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
            
        # Save the token.json for future runs (and for the container!)
        with open('token.json', 'w') as token:
            token.write(creds.to_json())
            print("✅ Success! token.json has been saved in this directory.")

if __name__ == '__main__':
    main()
