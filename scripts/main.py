"""
main.py  —  银发矩阵系统主入口
用法：
  python main.py crawl        # 立即采集一次（旧链路）
  python main.py generate     # 生成唤起型素材入库（推荐，替代采集）
  python main.py produce      # 生成一条视频（所有活跃账号）
  python main.py report       # 输出选题效果报告
  python main.py schedule     # 启动完整调度器（采集 + 数据回传）
  python main.py health       # 查看故事库健康状态
  python main.py setup        # 初始化数据库 + 添加示例账号
"""

import asyncio
import sys
import os
import time

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from monitor   import setup_logging, health_check
from src.core           import database


def cmd_setup():
    """初始化数据库，并插入示例账号矩阵"""
    database.init_db()
    # 示例：三个账号，各主攻一个新主题方向（正向人设，不踩独居/空巢红线）
    sample_accounts = [
        ("account_001", "恩爱老伴", "父母爱情"),
        ("account_002", "怀旧老人", "年代记忆"),
        ("account_003", "孝心儿女", "儿女孝心"),
    ]
    for acc_id, persona, theme in sample_accounts:
        database.upsert_account(acc_id, persona, theme)
    print("✅ 数据库初始化完成，已添加 3 个示例账号")
    print(database.get_stats())


def cmd_crawl():
    """立即触发一次多渠道采集"""
    from src.engines.crawler_engine import run_daily_crawl
    run_daily_crawl()


def cmd_genbg():
    """用 CogView 批量生成年代空镜背景素材库（存 brolls/{主题}/ 供 produce 复用）。

    用法：python main.py genbg [每主题张数]
    """
    from src.engines.video_engine import generate_background_library
    n = 3
    if len(sys.argv) >= 3 and sys.argv[2].isdigit():
        n = int(sys.argv[2])
    saved = generate_background_library(n_per_theme=n)
    print(f"✅ 背景素材库生成完成，共 {len(saved)} 张")


def cmd_generate():
    """生成唤起型素材入库（替代纯采集，零侵权 + 强对号入座）。

    用法：python main.py generate [每主题条数]
    """
    from src.engines.story_engine import generate_evocative_stories
    database.init_db()
    n = 3
    if len(sys.argv) >= 3 and sys.argv[2].isdigit():
        n = int(sys.argv[2])
    accepted = generate_evocative_stories(n_per_theme=n)
    print(f"✅ 唤起型素材生成完成，入库候选 {len(accepted)} 条")
    print(database.get_stats())


def cmd_produce():
    """为所有活跃账号各生成一条视频"""
    from src.engines.video_engine import main as video_main
    database.init_db()
    accounts = database.get_accounts(active_only=True)
    if not accounts:
        print("⚠️ 未配置账号矩阵，将使用默认人设生成")
        asyncio.run(video_main())
    else:
        for acc in accounts:
            print(f"\n>>> 账号：{acc['account_id']}  人设：{acc['persona']}")
            asyncio.run(video_main(account=acc))
            database.increment_account_post(acc["account_id"])
            time.sleep(2)


def cmd_report():
    """生成并打印选题效果报告"""
    from src.api.performance_api import generate_performance_report
    database.init_db()
    report = generate_performance_report()
    print(report)


def cmd_schedule():
    """启动完整后台调度器"""
    from src.engines.crawler_engine  import start_scheduler as crawl_scheduler
    from src.api.performance_api import start_scheduler as perf_scheduler

    database.init_db()
    hc = health_check()
    print(f"系统健康状态：{hc}")

    # 数据回传在后台运行
    perf_scheduler()

    # 采集调度器阻塞主线程
    print("调度器已启动，按 Ctrl+C 退出")
    crawl_scheduler()


def cmd_health():
    database.init_db()
    result = health_check()
    print(f"\n📊 系统健康报告")
    print(f"状态：{result['status'].upper()}")
    print(f"故事库总量：{result['total']}")
    print(f"可用数量：{result['ready']}")
    if result["warnings"]:
        for w in result["warnings"]:
            print(f"⚠️  {w}")


def cmd_scrape():
    """启动本地 RPA 抓取视频号数据并更新库"""
    from src.api.channels_rpa import run_channels_rpa
    database.init_db()
    run_channels_rpa()


COMMANDS = {
    "setup":    cmd_setup,
    "crawl":    cmd_crawl,
    "generate": cmd_generate,
    "genbg":    cmd_genbg,
    "produce":  cmd_produce,
    "report":   cmd_report,
    "schedule": cmd_schedule,
    "health":   cmd_health,
    "scrape":   cmd_scrape,
}


if __name__ == "__main__":
    setup_logging("INFO")

    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        sys.exit(0)

    cmd = sys.argv[1]
    COMMANDS[cmd]()
