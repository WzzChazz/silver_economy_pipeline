import re
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch_persistent_context(
        user_data_dir="/Users/mac/project/silver_economy_pipeline/storage/browser_profile",
        headless=False
    )
    page = browser.new_page()
    page.goto("https://channels.weixin.qq.com/platform/post/list", timeout=60000, wait_until="domcontentloaded")
    page.wait_for_timeout(10000)
    
    text = ""
    for idx, frame in enumerate(page.frames):
        try:
            body = frame.locator("body")
            if body.count() > 0:
                text += f"===FRAME {idx} : {frame.url}===\n"
                text += body.first.inner_text() + "\n\n"
        except Exception as e:
            text += f"Error in frame {idx}: {e}\n"
            
    with open("body_text.txt", "w", encoding="utf-8") as f:
        f.write(text)
        
    browser.close()
