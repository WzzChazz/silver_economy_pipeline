import sys
import os
import sqlite3

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from src.core import database

def inject_mock_data():
    database.init_db()
    with database.get_connection() as conn:
        c = conn.cursor()
        script = "人到晚年，最大的财富到底是什么？钱再多，也不如平平安安。认同的朋友点个红心，把健康长寿带给家人"
        # 伪造一个爆款记录
        c.execute('''
            INSERT INTO production_history 
            (story_id, persona, viral_script, cover_title, views, likes, comments, shares, watch_rate)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (999, '通用人设', script, '人到晚年，最大的财富到底是什么？', 6001, 15, 3, 10, 0.45))
        conn.commit()
        print("✅ 成功注入 6001 播放量爆款历史记录！")

if __name__ == "__main__":
    inject_mock_data()
