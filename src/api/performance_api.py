from __future__ import annotations
"""
performance_api.py  —  视频号数据回传与飞轮驱动
功能：
  1. 定时拉取视频号真实表现数据（完播率、点赞、分享）
  2. 写回 production_history + 更新 theme_weight 飞轮
  3. 自动生成选题报告（哪个主题/人设最能爆）
"""

import logging
import os
import json
from datetime import datetime, timedelta
from typing import Optional

import httpx
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv

from src.core import database

load_dotenv()
logger = logging.getLogger(__name__)

# 视频号开放平台配置（需自行申请）
WEIXIN_APPID     = os.getenv("WEIXIN_APPID", "")
WEIXIN_SECRET    = os.getenv("WEIXIN_SECRET", "")
WECOM_WEBHOOK    = os.getenv("WECOM_WEBHOOK_URL", "")

_access_token_cache: dict = {}


# ============================================================
# 视频号 Open API 对接
# ============================================================
def _get_access_token() -> Optional[str]:
    """获取微信 access_token（带缓存，有效期 2h）"""
    now = datetime.now().timestamp()
    if _access_token_cache.get("expires_at", 0) > now:
        return _access_token_cache["token"]

    if not WEIXIN_APPID or not WEIXIN_SECRET:
        logger.warning("未配置微信 AppID/Secret，跳过数据回传")
        return None

    try:
        with httpx.Client(timeout=15) as client:
            resp = client.get(
                "https://api.weixin.qq.com/cgi-bin/token",
                params={
                    "grant_type": "client_credential",
                    "appid":  WEIXIN_APPID,
                    "secret": WEIXIN_SECRET,
                },
            )
            data = resp.json()
            token = data.get("access_token")
            if token:
                _access_token_cache["token"]      = token
                _access_token_cache["expires_at"] = now + data.get("expires_in", 7200) - 60
                return token
    except Exception as e:
        logger.error(f"获取 access_token 失败：{e}")
    return None


def _fetch_finder_data(token: str, days_ago: int = 1) -> list[dict]:
    """
    拉取视频号近期视频数据。
    实际 API 端点以微信开放平台文档为准，此处为示意结构。
    """
    end_date   = datetime.now()
    start_date = end_date - timedelta(days=days_ago)

    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(
                "https://api.weixin.qq.com/channels/ec/finder/video/list/get",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "start_time": int(start_date.timestamp()),
                    "end_time":   int(end_date.timestamp()),
                    "page_size":  50,
                },
            )
            data = resp.json()
            
            # API 权限回退与告警逻辑（针对视频号严格的接口权限管控）
            if data.get("errcode", 0) != 0:
                err_msg = str(data)
                logger.error(f"微信 API 调用失败: {err_msg}")
                if "api unauthorized" in err_msg.lower() or "48001" in err_msg or "41008" in err_msg:
                    logger.warning(
                        "⚠️ 权限警告：当前 AppID 无视频号数据查询权限。\n"
                        "💡 推荐平替方案：请使用 RPA 工具（如 Playwright）编写脚本，定时无头模拟登录「视频号创作者中心 (creator.weixin.qq.com)」，从网页端直接抓取核心数据并写入 database。"
                    )
                return []
                
            return data.get("video_list", [])
    except Exception as e:
        logger.error(f"拉取视频号数据失败：{e}")
        return []


# ============================================================
# 数据回写
# ============================================================
def sync_performance_data(days_ago: int = 1):
    """
    主同步任务：拉取视频号数据 → 匹配本地 production_history → 更新飞轮
    """
    token = _get_access_token()
    if not token:
        logger.info("无 token，跳过同步")
        return

    video_list = _fetch_finder_data(token, days_ago)
    if not video_list:
        logger.info("暂无新数据")
        return

    updated = 0
    with database.get_connection() as conn:
        for video in video_list:
            # 通过描述文字匹配 production_history（实际可用视频ID关联）
            title       = video.get("title", "")
            views       = int(video.get("view_count",    0))
            likes       = int(video.get("like_count",    0))
            comments    = int(video.get("comment_count", 0))
            shares      = int(video.get("share_count",   0))
            watch_rate  = float(video.get("watch_rate",  0))  # 完播率 0-100

            # 尝试匹配
            row = conn.execute("""
                SELECT id FROM production_history
                WHERE cover_title LIKE ? AND views = 0
                ORDER BY created_at DESC LIMIT 1
            """, (f"%{title[:10]}%",)).fetchone()

            if row:
                database.update_performance(
                    production_id=row["id"],
                    views=views,
                    likes=likes,
                    comments=comments,
                    shares=shares,
                    watch_rate=watch_rate,
                )
                updated += 1

    logger.info(f"数据同步完成：更新 {updated} 条记录")
    if updated > 0:
        _send_alert(f"视频号数据同步：{updated} 条记录已更新，飞轮权重已调整")


# ============================================================
# 选题效果报告
# ============================================================
def generate_performance_report() -> str:
    """生成 Markdown 格式的选题效果报告"""
    stats = database.get_stats()
    lines = [
        f"# 银发矩阵选题效果报告",
        f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        f"## 故事库状态",
        f"- 总量：{stats['total']} 条",
        f"- 可用（score≥75 未耗尽）：{stats['ready']} 条",
        "",
        "## 主题分布",
    ]
    for t in stats.get("by_theme", []):
        lines.append(f"- {t['theme']}：{t['cnt']} 条，均分 {t['avg_score']:.1f}")

    lines += ["", "## 主题完播率排行（数据飞轮）"]
    for p in stats.get("theme_performance", []):
        lines.append(
            f"- {p['theme']}：完播率 {p['avg_watch']:.1f}%  "
            f"均点赞 {p['avg_likes']:.0f}  样本 {p['sample_count']} 条"
        )

    report = "\n".join(lines)

    # 保存到文件
    base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    out_path = os.path.join(base, "output", "performance_report.md")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(report)

    logger.info(f"选题报告已生成：{out_path}")
    return report


# ============================================================
# 企业微信告警推送
# ============================================================
def _send_alert(message: str):
    if not WECOM_WEBHOOK:
        return
    try:
        with httpx.Client(timeout=10) as client:
            client.post(WECOM_WEBHOOK, json={
                "msgtype": "text",
                "text":    {"content": f"[银发矩阵] {message}"},
            })
    except Exception as e:
        logger.warning(f"企微推送失败：{e}")


# ============================================================
# 定时调度
# ============================================================
def start_scheduler():
    """
    后台调度：
      - 每天 06:00 同步昨日视频号数据
      - 每周一 09:00 生成选题报告
    """
    scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
    scheduler.add_job(
        sync_performance_data,
        trigger="cron", hour=6, minute=0,
        id="daily_sync", name="每日数据回传",
        max_instances=1, replace_existing=True,
    )
    scheduler.add_job(
        generate_performance_report,
        trigger="cron", day_of_week="mon", hour=9, minute=0,
        id="weekly_report", name="每周选题报告",
        max_instances=1, replace_existing=True,
    )
    scheduler.start()
    logger.info("数据回传调度器启动（每日 06:00 同步，每周一 09:00 报告）")
    return scheduler


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    database.init_db()
    # 手动触发：生成报告
    print(generate_performance_report())
    # 手动触发：同步近 3 天数据
    sync_performance_data(days_ago=3)
