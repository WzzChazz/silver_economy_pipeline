from __future__ import annotations
"""
crawler_engine.py  —  多渠道故事采集引擎
重构要点：
  1. 知乎 + 小红书 + 微信公众号三渠道
  2. 随机 UA + 代理池轮换，降低封禁风险
  3. APScheduler 定时任务（每日自动运行）
  4. 采集结果直接送 story_engine 评分入库
"""

import os
import time
import random
import logging
import urllib.parse
from typing import Optional

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
from apscheduler.schedulers.blocking import BlockingScheduler
from dotenv import load_dotenv

from src.engines import story_engine

load_dotenv()
logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PROFILE_DIR = os.path.join(BASE_DIR, "data", "browser_profile")
os.makedirs(PROFILE_DIR, exist_ok=True)

# ---------- 采集参数 ----------
KEYWORDS = [
    "退休后最大的感受",
    "人老了最怕什么",
    "晚年独居是什么感受",
    "老伴去世之后",
    "空巢老人的真实生活",
    "60岁以后才明白",
    "父母老了我才懂",
    "一个人住的老人",
]

# 随机 UA 池
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
]

def _get_proxies() -> list:
    raw = os.getenv("PROXY_LIST", "")
    return [p.strip() for p in raw.split(",") if p.strip()]


def _browser_args(proxy: str = None) -> dict:
    args = {
        "user_data_dir": PROFILE_DIR,
        "headless":      False,
        "viewport":      {"width": 1280, "height": 800},
        "user_agent":    random.choice(_USER_AGENTS),
        "args":          ["--disable-blink-features=AutomationControlled"],
    }
    if proxy:
        args["proxy"] = {"server": proxy}
    return args


# ============================================================
# 知乎采集
# ============================================================
def _fetch_zhihu(keyword: str, limit: int = 5) -> list[str]:
    logger.info(f"[知乎] 开始采集关键词：{keyword}")
    proxies = _get_proxies()
    proxy = random.choice(proxies) if proxies else None
    stories = []

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(**_browser_args(proxy))
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        # 随机鼠标移动，模拟真人
        page.mouse.move(random.randint(100, 600), random.randint(100, 400))

        encoded = urllib.parse.quote(keyword)
        page.goto(f"https://www.zhihu.com/search?type=content&q={encoded}")

        try:
            page.wait_for_selector(".List-item", timeout=30000)
        except PlaywrightTimeout:
            logger.warning("[知乎] 页面加载超时（可能需要扫码登录）")

        # 模拟阅读滚动
        for _ in range(4):
            page.mouse.wheel(0, random.randint(1500, 2500))
            page.wait_for_timeout(random.randint(1500, 3000))

        answers = page.query_selector_all(".RichText.ztext")
        logger.info(f"[知乎] 发现 {len(answers)} 条内容")

        for ans in answers:
            if len(stories) >= limit:
                break
            text = ans.inner_text().strip()
            if 100 < len(text) < 2000:
                stories.append(text)

        ctx.close()

    logger.info(f"[知乎] 采集完成，有效 {len(stories)} 条")
    return stories


# ============================================================
# 小红书采集
# ============================================================
def _fetch_xiaohongshu(keyword: str, limit: int = 5) -> list[str]:
    logger.info(f"[小红书] 开始采集关键词：{keyword}")
    proxies = _get_proxies()
    proxy = random.choice(proxies) if proxies else None
    stories = []

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(**_browser_args(proxy))
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        encoded = urllib.parse.quote(keyword)
        page.goto(f"https://www.xiaohongshu.com/search_result?keyword={encoded}&source=web_search_result_notes")

        try:
            page.wait_for_selector(".note-item", timeout=25000)
        except PlaywrightTimeout:
            logger.warning("[小红书] 页面加载超时（可能需要扫码）")
            ctx.close()
            return stories

        for _ in range(3):
            page.mouse.wheel(0, random.randint(1000, 2000))
            page.wait_for_timeout(random.randint(1500, 2500))

        note_links = page.query_selector_all(".note-item a")
        logger.info(f"[小红书] 发现 {len(note_links)} 篇笔记")

        opened = 0
        for link in note_links:
            if len(stories) >= limit or opened >= limit * 2:
                break
            try:
                href = link.get_attribute("href")
                if not href:
                    continue
                url = f"https://www.xiaohongshu.com{href}" if href.startswith("/") else href
                detail_page = ctx.new_page()
                detail_page.goto(url, timeout=20000)
                detail_page.wait_for_timeout(random.randint(1500, 2500))

                # 正文内容
                desc = detail_page.query_selector("#detail-desc")
                if desc:
                    text = desc.inner_text().strip()
                    # 小红书常有 Emoji 开头，过滤太碎的
                    clean = "".join(c for c in text if "\u4e00" <= c <= "\u9fff" or c.isalnum() or c in "，。！？、…")
                    if 80 < len(clean) < 1500:
                        stories.append(clean)
                        logger.info(f"[小红书] 采集到笔记，字数：{len(clean)}")
                detail_page.close()
                opened += 1
                time.sleep(random.uniform(1.5, 3.0))
            except Exception as e:
                logger.warning(f"[小红书] 详情页打开失败：{e}")
                continue

        ctx.close()

    logger.info(f"[小红书] 采集完成，有效 {len(stories)} 条")
    return stories


# ============================================================
# 微信公众号采集（通过搜狗微信搜索）
# ============================================================
def _fetch_weixin(keyword: str, limit: int = 5) -> list[str]:
    logger.info(f"[微信公众号] 开始采集关键词：{keyword}")
    proxies = _get_proxies()
    proxy = random.choice(proxies) if proxies else None
    stories = []

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(**_browser_args(proxy))
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        encoded = urllib.parse.quote(keyword)
        page.goto(f"https://weixin.sogou.com/weixin?type=2&query={encoded}&ie=utf8")

        try:
            page.wait_for_selector(".news-list", timeout=20000)
        except PlaywrightTimeout:
            logger.warning("[微信] 搜狗页面加载超时")
            ctx.close()
            return stories

        article_links = page.query_selector_all(".news-list li .txt-box a")
        logger.info(f"[微信] 发现 {len(article_links)} 篇文章")

        for link in article_links:
            if len(stories) >= limit:
                break
            try:
                href = link.get_attribute("href")
                if not href:
                    continue
                detail = ctx.new_page()
                detail.goto(href, timeout=25000)
                detail.wait_for_timeout(random.randint(1500, 2500))

                # 微信文章正文一般在 #js_content 或 .rich_media_content
                content = detail.query_selector("#js_content") or detail.query_selector(".rich_media_content")
                if content:
                    text = content.inner_text().strip()
                    # 去掉常见公众号噪声（关注/点赞提示）
                    lines = [l.strip() for l in text.split("\n") if len(l.strip()) > 10
                             and "点击关注" not in l and "长按识别" not in l]
                    clean = "\n".join(lines[:30])  # 只取前 30 行，避免太长
                    if 100 < len(clean) < 2500:
                        stories.append(clean)
                        logger.info(f"[微信] 采集到文章，字数：{len(clean)}")
                detail.close()
                time.sleep(random.uniform(2.0, 4.0))
            except Exception as e:
                logger.warning(f"[微信] 文章打开失败：{e}")
                continue

        ctx.close()

    logger.info(f"[微信] 采集完成，有效 {len(stories)} 条")
    return stories


# ============================================================
# 渠道调度器
# ============================================================
CHANNEL_MAP = {
    "zhihu":        _fetch_zhihu,
    "xiaohongshu":  _fetch_xiaohongshu,
    "weixin":       _fetch_weixin,
}

def run_daily_crawl(
    keywords: list[str] = None,
    channels: list[str] = None,
    limit_per_kw: int = 3,
):
    """主采集任务，供调度器和手动调用"""
    if keywords is None:
        keywords = KEYWORDS
    if channels is None:
        channels = list(CHANNEL_MAP.keys())

    logger.info(f"日常采集任务启动：{len(keywords)} 个关键词 × {len(channels)} 个渠道")
    total_evaluated = 0

    for kw in keywords:
        for ch in channels:
            fetch_fn = CHANNEL_MAP.get(ch)
            if not fetch_fn:
                continue
            logger.info(f"{'='*50}\n渠道={ch} 关键词={kw}")
            try:
                texts = fetch_fn(kw, limit=limit_per_kw)
            except Exception as e:
                logger.error(f"渠道 {ch} 采集异常：{e}")
                continue

            for text in texts:
                logger.info(f"送审内容（字数={len(text)}）")
                story_engine.evaluate_story(text, source=f"{ch}_{kw}")
                total_evaluated += 1
                time.sleep(random.uniform(1.5, 3.0))

    logger.info(f"日常采集完成，共送审 {total_evaluated} 条")


# ============================================================
# APScheduler 定时任务
# ============================================================
def start_scheduler():
    """每天凌晨 2:00 自动采集，后台运行"""
    scheduler = BlockingScheduler(timezone="Asia/Shanghai")
    scheduler.add_job(
        run_daily_crawl,
        trigger="cron",
        hour=2,
        minute=0,
        id="daily_crawl",
        name="每日银发故事采集",
        max_instances=1,
        replace_existing=True,
    )
    logger.info("定时采集调度器已启动（每日 02:00 执行）")
    try:
        scheduler.start()
    except KeyboardInterrupt:
        logger.info("调度器手动停止")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    # 手动触发一次采集（测试用）
    run_daily_crawl(
        keywords=["退休后最大的感受", "老伴去世之后"],
        channels=["zhihu"],
        limit_per_kw=2,
    )
