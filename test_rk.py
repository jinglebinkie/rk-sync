from playwright.sync_api import sync_playwright

def grab_screenshot():
    with sync_playwright() as p:
        browser = p.firefox.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()
        print("Navigating to Runkeeper login with Firefox...")
        page.goto("https://runkeeper.com/login", wait_until="networkidle")
        
        print(f"Landed on: {page.url}")
        print("Waiting 10 seconds...")
        page.wait_for_timeout(10000)
        
        # Accept cookies
        try:
            btn = page.locator("button:has-text('Accept All Cookies')")
            btn.click(timeout=3000)
            print("Accepted cookies.")
        except:
            print("No cookie banner found.")

        page.wait_for_timeout(3000)
            
        print("Taking screenshot to debug_firefox.png...")
        page.screenshot(path="debug_firefox.png", full_page=True)
        
        with open("debug_firefox.html", "w") as f:
            f.write(page.content())
            
        browser.close()

if __name__ == "__main__":
    grab_screenshot()
