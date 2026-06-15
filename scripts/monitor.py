"""
monitor.py  —  统一日志与告警模块
功能：
  1. 统一 logging 配置（文件 + 控制台双输出）
  2. 企业微信 Webhook 告警（错误级别自动推送）
  3. 系统健康检查（故事库余量告警）
"""

import logging
import logging.handlers
import os
from datetime import datetime

import httpx
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LOG_DIR  = os.path.join(BASE_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

WECOM_WEBHOOK = os.getenv("WECOM_WEBHOOK_URL", "")


# ============================================================
# 企微推送 Handler
# ============================================================
class WeComHandler(logging.Handler):
    """ERROR 及以上级别自动推送到企业微信"""

    def __init__(self, webhook_url: str):
        super().__init__(level=logging.ERROR)
        self.webhook_url = webhook_url

    def emit(self, record: logging.LogRecord):
        if not self.webhook_url:
            return
        try:
            msg = self.format(record)
            with httpx.Client(timeout=8) as client:
                client.post(self.webhook_url, json={
                    "msgtype": "text",
                    "text": {"content": f"[银发矩阵告警]\n{msg}"},
                })
        except Exception:
            pass  # 告警失败不影响主流程


# ============================================================
# 全局日志初始化
# ============================================================
def setup_logging(level: str = "INFO"):
    log_level = getattr(logging, level.upper(), logging.INFO)
    log_file  = os.path.join(LOG_DIR, f"silver_{datetime.now().strftime('%Y%m%d')}.log")

    fmt = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(log_level)
    root.handlers.clear()

    # 控制台
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)

    # 滚动文件（每天一个，保留 7 天）
    file_handler = logging.handlers.TimedRotatingFileHandler(
        log_file, when="midnight", backupCount=7, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    # 企微推送（ERROR 级别）
    if WECOM_WEBHOOK:
        wecom = WeComHandler(WECOM_WEBHOOK)
        wecom.setFormatter(fmt)
        root.addHandler(wecom)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("playwright").setLevel(logging.WARNING)
    logging.info(f"日志系统启动，级别={level}，文件={log_file}")


# ============================================================
# 系统健康检查
# ============================================================
def health_check() -> dict:
    """
    检查故事库余量，低于阈值推送告警。
    建议在 main.py 启动时调用。
    """
    from src.core import database
    database.init_db()
    stats = database.get_stats()
    ready = stats["ready_stories"]
    total = stats["total_stories"]

    status = "ok"
    warnings = []

    if ready < 10:
        msg = f"故事库告急：可用故事仅剩 {ready} 条，请立即运行采集！"
        logging.getLogger(__name__).error(msg)
        warnings.append(msg)
        status = "critical"
    elif ready < 30:
        msg = f"故事库余量偏低：可用 {ready} 条，建议尽快采集补充"
        logging.getLogger(__name__).warning(msg)
        warnings.append(msg)
        status = "warning"

    return {
        "status":   status,
        "total":    total,
        "ready":    ready,
        "warnings": warnings,
    }


if __name__ == "__main__":
    setup_logging("INFO")
    result = health_check()
    print(result)
