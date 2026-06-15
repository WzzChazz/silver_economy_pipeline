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
    
    # 尝试获取全体文字
    text = ""
    count = page.locator("body").count()
    for i in range(count):
        text += page.locator("body").nth(i).inner_text() + "\n\n===BODY SPLIT===\n\n"
        
    with open("body_text.txt", "w", encoding="utf-8") as f:
        f.write(text)
        
    browser.close()
