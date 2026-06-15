import json
from playwright.sync_api import sync_playwright

def run():
    responses_dump = []
    
    def handle_response(response):
        # 只关心返回 JSON 的请求
        if "application/json" in response.headers.get("content-type", ""):
            try:
                data = response.json()
                # 如果这个 JSON 里面有我们想要的数据特征（比如播放量、点赞，或者类似于 list 的结构）
                responses_dump.append({
                    "url": response.url,
                    "data": data
                })
            except:
                pass

    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(
            user_data_dir="/Users/mac/project/silver_economy_pipeline/storage/browser_profile",
            headless=False
        )
        page = browser.new_page()
        page.on("response", handle_response)
        
        page.goto("https://channels.weixin.qq.com/platform/post/list", timeout=60000, wait_until="domcontentloaded")
        page.wait_for_timeout(15000)
        
        with open("network_dump.json", "w", encoding="utf-8") as f:
            json.dump(responses_dump, f, ensure_ascii=False, indent=2)
            
        browser.close()

if __name__ == "__main__":
    run()
