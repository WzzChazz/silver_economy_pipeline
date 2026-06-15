import os
import time
import logging
import re
from playwright.sync_api import sync_playwright

from src.core import database

logger = logging.getLogger(__name__)

def run_channels_rpa():
    # Setup paths
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    user_data_dir = os.path.join(base_dir, "storage", "browser_profile")
    os.makedirs(user_data_dir, exist_ok=True)
    
    with sync_playwright() as p:
        logger.info("启动本地浏览器进行视频号自动化抓取...")
        # 启动 Chromium，关闭无头模式，必须能让人扫码
        try:
            browser = p.chromium.launch_persistent_context(
                user_data_dir=user_data_dir,
                headless=False,
                channel="chrome", # 优先使用系统安装的 Chrome
                viewport={"width": 1280, "height": 800}
            )
        except Exception:
            # 回退到自带的 Chromium
            browser = p.chromium.launch_persistent_context(
                user_data_dir=user_data_dir,
                headless=False,
                viewport={"width": 1280, "height": 800}
            )
            
        page = browser.new_page()
        
        # 1. 登录验证
        page.goto("https://channels.weixin.qq.com/platform/login", timeout=60000, wait_until="domcontentloaded")
        
        logger.info("请检查浏览器：如果需要扫码，请在 60 秒内使用手机微信扫码登录。")
        try:
            # 等待跳转到首页或者动态管理页面
            page.wait_for_url("**/platform/home**", timeout=60000)
            logger.info("✅ 登录成功！")
        except Exception as e:
            logger.error("登录超时或页面未跳转。如果已经登录请忽略此错误...")
            
        if "login" in page.url:
            logger.error("❌ 未能成功登录，脚本退出。")
            browser.close()
            return

        # 2. 导航至视频动态管理页
        logger.info("正在进入视频数据列表页...")
        try:
            page.goto("https://channels.weixin.qq.com/platform/post/list", timeout=60000, wait_until="domcontentloaded")
        except Exception as e:
            logger.warning(f"进入列表页发生超时，尝试继续提取: {e}")
        
        # 等待列表加载
        try:
            page.wait_for_selector(".post-list-item, .post-item, table, .table-row", timeout=15000)
        except Exception:
            logger.warning("未检测到标准的已知列表元素，将尝试通过模糊探测抓取。")
            
        time.sleep(5) # 确保网络请求数据渲染完毕
        
        # [DEBUG] 打印并保存页面源码以供分析
        try:
            debug_html_path = os.path.join(base_dir, "debug_channels.html")
            with open(debug_html_path, "w", encoding="utf-8") as f:
                f.write(page.content())
            logger.info(f"已保存页面源码至 {debug_html_path}，用于针对性调试 DOM 结构")
            # 同时保存截图
            debug_png_path = os.path.join(base_dir, "debug_channels.png")
            page.screenshot(path=debug_png_path, full_page=True)
            logger.info(f"已保存页面截图至 {debug_png_path}")
        except Exception as e:
            logger.error(f"保存调试信息失败: {e}")
        
        # 3. 提取数据
        # 分析截图发现：微信视频号采用了 Wujie 微前端架构，数据渲染在 iframe 或 Shadow DOM 中
        # 我们遍历所有 frame 执行提取逻辑
        extractor_js = '''() => {
            const results = [];
            const rows = document.querySelectorAll('div, tr, li');
            const seen = new Set();
            
            rows.forEach(row => {
                const textContent = row.innerText || "";
                // 寻找包含日期的卡片：例如 "2026年06月14日 20:19"
                if (/\\d{4}年\\d{2}月\\d{2}日/.test(textContent) && textContent.length > 20 && textContent.length < 500) {
                    if (!seen.has(textContent)) {
                        seen.add(textContent);
                        results.push(textContent);
                    }
                }
            });
            return results;
        }'''
        
        videos_data = []
        for frame in page.frames:
            try:
                frame_results = frame.evaluate(extractor_js)
                if frame_results:
                    videos_data.extend(frame_results)
            except Exception as e:
                pass
        
        # 过滤掉包含关系的外层容器，只保留最内层的有效卡片
        filtered_data = []
        for text in videos_data:
            is_parent = False
            for other_text in videos_data:
                if text != other_text and other_text in text and len(other_text) > 20:
                    is_parent = True
                    break
            if not is_parent:
                filtered_data.append(text)
                
        logger.info(f"成功在页面探测到 {len(filtered_data)} 条潜在的视频记录。")
        
        success_count = 0
        for text in filtered_data:
            lines = [line.strip() for line in text.split('\\n') if line.strip()]
            if len(lines) < 3:
                continue
                
            # 找到包含日期的那一行的索引
            date_idx = -1
            for i, line in enumerate(lines):
                if re.search(r'\\d{4}年\\d{2}月\\d{2}日', line):
                    date_idx = i
                    break
                    
            if date_idx == -1 or date_idx == 0:
                continue
                
            title = lines[0] # 标题通常在时间上面
            stats_text = " ".join(lines[date_idx+1:]) # 时间下面的文本拼起来
            
            # 提取所有数字
            numbers = re.findall(r'\\d+', stats_text.replace(',', ''))
            
            views = 0
            likes = 0
            comments = 0
            shares = 0
            
            if len(numbers) >= 4:
                try:
                    views = int(numbers[0])
                    likes = int(numbers[1])
                    comments = int(numbers[2])
                    shares = int(numbers[3])
                except:
                    pass
            
            if views > 0 or likes > 0:
                # 微信后台可能会截断标题，取前10个字作为匹配凭证
                snippet = title[:10].replace("...", "").strip()
                logger.info(f"解析到数据 -> 标题片段: {snippet}... | 播放: {views} | 点赞: {likes} | 评论: {comments} | 转发: {shares}")
                
                updated = database.update_production_stats(snippet, views, likes, comments, shares)
                if updated:
                    success_count += 1
                    logger.info(f"  └─ 关联本地剧本成功！[UPDATED]")
                else:
                    logger.warning(f"  └─ 本地库未找到匹配该标题的视频。")
                    
        logger.info(f"\\n🎉 RPA 抓取完成！共成功同步了 {success_count} 条视频的真实数据。")
        browser.close()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_channels_rpa()
