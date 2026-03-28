from playwright.sync_api import sync_playwright

def grab_screenshot():
    with sync_playwright() as p:
        browser = p.firefox.launch(
            headless=True
        )
        page = browser.new_page(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        print("Navigating to Runkeeper login...")
        page.goto("https://runkeeper.com/login", wait_until="networkidle")
        
        print(f"Landed on: {page.url}")
        print("Waiting 3 seconds for ASICS/cookie redirects...")
        page.wait_for_timeout(3000)
        
        print("Taking screenshot...")
        page.screenshot(path="runkeeper_login.png", full_page=True)
        
        # Also grab the raw HTML text so we can see the buttons
        with open("runkeeper_login.html", "w") as f:
            f.write(page.content())
            
        print("Done! Look closely at runkeeper_login.png and runkeeper_login.html.")
        browser.close()

if __name__ == "__main__":
    grab_screenshot()
