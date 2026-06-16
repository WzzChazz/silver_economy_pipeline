"""
database.py  —  故事库核心数据层
重构要点：
  1. 五维评分字段替换单一 score
  2. WAL 模式 + 连接池防止并发锁表
  3. 语义向量去重替换 SequenceMatcher
  4. 完播率动态权重回写接口
  5. 账号矩阵多人设支持
"""

import sqlite3
import os
import json
import logging
from contextlib import contextmanager
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.path.join(BASE_DIR, "data", "story_pool.db")
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

# ---------- 语义向量（懒加载，避免启动慢）----------
_embed_model = None

def _get_embed_model():
    global _embed_model
    if _embed_model is None:
        try:
            from sentence_transformers import SentenceTransformer
            _embed_model = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")
            logger.info("语义向量模型加载成功")
        except Exception as e:
            # ImportError（未安装）或 OSError（torch/ffmpeg 动态库缺失）等均降级到字面去重
            logger.warning(f"语义向量模型不可用（{type(e).__name__}），退回字面去重模式：{e}")
    return _embed_model


def _cosine_similarity(a: list, b: list) -> float:
    import math
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


# ---------- 连接池（WAL 模式）----------
@contextmanager
def get_connection():
    conn = sqlite3.connect(DB_PATH, timeout=15, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------- 建表 / 迁移 ----------
def init_db():
    logger.info(f"初始化数据库: {DB_PATH}")
    with get_connection() as conn:
        c = conn.cursor()

        # 主故事库
        c.execute("""
            CREATE TABLE IF NOT EXISTS story_pool (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                theme         TEXT    NOT NULL,
                title         TEXT,
                story         TEXT    NOT NULL,
                emotion       TEXT,
                scene         TEXT,
                persona       TEXT,
                source        TEXT,
                narrative_type TEXT,
                -- 五维评分（各20分，合计100）
                score_pain       INTEGER DEFAULT 0,
                score_truth      INTEGER DEFAULT 0,
                score_resonance  INTEGER DEFAULT 0,
                score_freshness  INTEGER DEFAULT 0,
                score_rewrite    INTEGER DEFAULT 0,
                score            INTEGER GENERATED ALWAYS AS (
                    score_pain + score_truth + score_resonance + score_freshness + score_rewrite
                ) STORED,
                -- 使用与权重
                used_count    INTEGER DEFAULT 0,
                theme_weight  REAL    DEFAULT 1.0,
                -- 语义向量（JSON 存储 float list）
                embedding     TEXT,
                created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 平滑迁移旧表
        c.execute("PRAGMA table_info(story_pool)")
        existing = {row[1] for row in c.fetchall()}
        migrations = {
            "score_pain":      "INTEGER DEFAULT 0",
            "score_truth":     "INTEGER DEFAULT 0",
            "score_resonance": "INTEGER DEFAULT 0",
            "score_freshness": "INTEGER DEFAULT 0",
            "score_rewrite":   "INTEGER DEFAULT 0",
            "theme_weight":    "REAL DEFAULT 1.0",
            "embedding":       "TEXT",
            "narrative_type":  "TEXT",
        }
        for col, col_type in migrations.items():
            if col not in existing:
                logger.info(f"数据库升级：添加字段 {col}")
                c.execute(f"ALTER TABLE story_pool ADD COLUMN {col} {col_type}")

        # 旧 score 字段兼容：将旧单一分拆为五维均分
        if "score" in existing and "score_pain" not in existing:
            c.execute("""
                UPDATE story_pool SET
                    score_pain      = score / 5,
                    score_truth     = score / 5,
                    score_resonance = score / 5,
                    score_freshness = score / 5,
                    score_rewrite   = score / 5
                WHERE score_pain = 0 AND score > 0
            """)

        # 生产记录表
        c.execute("""
            CREATE TABLE IF NOT EXISTS production_history (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                story_id      INTEGER,
                persona       TEXT,
                viral_script  TEXT NOT NULL,
                cover_title   TEXT,
                cover_variant TEXT,
                views         INTEGER DEFAULT 0,
                likes         INTEGER DEFAULT 0,
                comments      INTEGER DEFAULT 0,
                shares        INTEGER DEFAULT 0,
                watch_rate    REAL    DEFAULT 0.0,
                created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 账号矩阵表
        c.execute("""
            CREATE TABLE IF NOT EXISTS account_matrix (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id  TEXT    NOT NULL UNIQUE,
                persona     TEXT    NOT NULL,
                theme_focus TEXT,
                post_count  INTEGER DEFAULT 0,
                active      INTEGER DEFAULT 1
            )
        """)

        # 主题权重快照表（数据飞轮用）
        c.execute("""
            CREATE TABLE IF NOT EXISTS theme_performance (
                theme        TEXT PRIMARY KEY,
                avg_watch    REAL DEFAULT 0.0,
                avg_likes    REAL DEFAULT 0.0,
                sample_count INTEGER DEFAULT 0,
                updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        logger.info("数据库初始化完成")


# ---------- 写入故事 ----------
def insert_story(
    theme: str,
    title: str,
    story: str,
    emotion: str,
    source: str,
    narrative_type: str = "默认型",
    scene: str = None,
    persona: str = None,
    scores: dict = None,
) -> bool:
    """
    入库前先做语义去重；scores 结构：
    {"pain": 20, "truth": 18, "resonance": 17, "freshness": 15, "rewrite": 16}
    """
    if scores is None:
        scores = {"pain": 0, "truth": 0, "resonance": 0, "freshness": 0, "rewrite": 0}

    # 向量在连接外算（CPU 密集，避免占着 DB 连接）
    embedding_vec = None
    model = _get_embed_model()
    if model:
        embedding_vec = model.encode(story).tolist()
    embedding_json = json.dumps(embedding_vec) if embedding_vec else None

    # 原型限流 + 去重 + 入库共用一个连接 / 一个事务
    with get_connection() as conn:
        # --- 叙事原型比例限制 ---
        if narrative_type:
            total_count = conn.execute("SELECT COUNT(*) FROM story_pool").fetchone()[0]
            if total_count > 50:
                type_count = conn.execute(
                    "SELECT COUNT(*) FROM story_pool WHERE narrative_type = ?",
                    (narrative_type,),
                ).fetchone()[0]
                if type_count / total_count > 0.2:
                    logger.warning(f"叙事原型同质化拦截：'{narrative_type}' 占比已超过 20%")
                    return False

        # --- 去重 ---
        if embedding_vec is not None:
            rows = conn.execute(
                "SELECT embedding FROM story_pool ORDER BY id DESC LIMIT 100"
            ).fetchall()
            for row in rows:
                if row["embedding"]:
                    old_vec = json.loads(row["embedding"])
                    if _cosine_similarity(embedding_vec, old_vec) > 0.85:
                        logger.warning("语义去重拦截：故事与库内内容相似度过高")
                        return False
        else:
            # 降级：字面 difflib
            import difflib
            rows = conn.execute(
                "SELECT story FROM story_pool ORDER BY id DESC LIMIT 50"
            ).fetchall()
            for row in rows:
                if difflib.SequenceMatcher(None, story, row["story"]).ratio() > 0.85:
                    logger.warning("字面去重拦截")
                    return False

        conn.execute("""
            INSERT INTO story_pool
                (theme, title, story, emotion, scene, persona, source, narrative_type,
                 score_pain, score_truth, score_resonance, score_freshness, score_rewrite,
                 embedding)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            theme, title, story, emotion, scene, persona, source, narrative_type,
            scores["pain"], scores["truth"], scores["resonance"],
            scores["freshness"], scores["rewrite"],
            embedding_json,
        ))

    total = sum(scores.values())
    logger.info(f"故事入库成功：theme={theme} score={total}")
    return True


# ---------- 读取故事（动态加权）----------
def get_story_for_persona(
    persona: str = None,
    theme: str = None,
    min_score: int = 75,
) -> Optional[dict]:
    """
    按人设 / 主题定向拉取，融合五维分、使用衰减、主题权重和随机性。
    score > 90 允许复用 10 次，否则 3 次。
    """
    with get_connection() as conn:
        where_clauses = [
            "used_count < (CASE WHEN score > 90 THEN 10 ELSE 3 END)",
            f"score >= {min_score}",
        ]
        params = []
        if persona:
            where_clauses.append("persona = ?")
            params.append(persona)
        if theme:
            where_clauses.append("theme = ?")
            params.append(theme)

        where_sql = " AND ".join(where_clauses)
        row = conn.execute(f"""
            SELECT id, theme, title, story, emotion, scene, persona,
                   score, score_pain, score_truth, score_resonance,
                   score_freshness, score_rewrite, theme_weight
            FROM story_pool
            WHERE {where_sql}
            ORDER BY
                (score * 0.5 * theme_weight)
                + ((10 - used_count) * 3)
                + (score_pain * 0.3)
                + ABS(RANDOM() % 15)
            DESC
            LIMIT 1
        """, params).fetchone()

    if not row:
        return None
    return dict(row)


def mark_story_used(story_id: int):
    with get_connection() as conn:
        conn.execute(
            "UPDATE story_pool SET used_count = used_count + 1 WHERE id = ?",
            (story_id,)
        )


# ---------- 生产记录 ----------
def record_production(
    story_id: int,
    viral_script: str,
    cover_title: str,
    persona: str = None,
    cover_variant: str = None,
) -> int:
    with get_connection() as conn:
        cur = conn.execute("""
            INSERT INTO production_history
                (story_id, viral_script, cover_title, persona, cover_variant)
            VALUES (?, ?, ?, ?, ?)
        """, (story_id, viral_script, cover_title, persona, cover_variant))
        return cur.lastrowid


def update_performance(
    production_id: int,
    views: int,
    likes: int,
    comments: int,
    shares: int,
    watch_rate: float = None,
):
    """视频号数据回传，同时更新主题权重飞轮。

    watch_rate 为 None 时（如 RPA 网页抓取拿不到完播率）只更新点赞侧信号，
    不污染 avg_watch，避免把对应主题的 theme_weight 误归零。
    """
    with get_connection() as conn:
        # watch_rate 缺省则保留原值
        conn.execute("""
            UPDATE production_history
            SET views=?, likes=?, comments=?, shares=?,
                watch_rate = COALESCE(?, watch_rate)
            WHERE id=?
        """, (views, likes, comments, shares, watch_rate, production_id))

        # 取出对应故事的 theme，更新主题权重
        row = conn.execute("""
            SELECT sp.theme FROM production_history ph
            JOIN story_pool sp ON ph.story_id = sp.id
            WHERE ph.id = ?
        """, (production_id,)).fetchone()

        if not row:
            return
        theme = row["theme"]

        if watch_rate is not None:
            # 完播率 + 点赞双信号滑动平均
            conn.execute("""
                INSERT INTO theme_performance (theme, avg_watch, avg_likes, sample_count)
                VALUES (?, ?, ?, 1)
                ON CONFLICT(theme) DO UPDATE SET
                    avg_watch    = (avg_watch * sample_count + ?) / (sample_count + 1),
                    avg_likes    = (avg_likes * sample_count + ?) / (sample_count + 1),
                    sample_count = sample_count + 1,
                    updated_at   = CURRENT_TIMESTAMP
            """, (theme, watch_rate, likes, watch_rate, likes))

            # 用完播率归一化更新 story_pool.theme_weight（缺测时维持默认 1.0）
            conn.execute("""
                UPDATE story_pool
                SET theme_weight = (
                    SELECT COALESCE(NULLIF(avg_watch, 0) / 100.0 * 2.0, 1.0)
                    FROM theme_performance WHERE theme = story_pool.theme
                )
                WHERE theme = ?
            """, (theme,))
        else:
            # 无完播率：仅滑动平均点赞数，不动 avg_watch / theme_weight
            conn.execute("""
                INSERT INTO theme_performance (theme, avg_watch, avg_likes, sample_count)
                VALUES (?, 0.0, ?, 1)
                ON CONFLICT(theme) DO UPDATE SET
                    avg_likes    = (avg_likes * sample_count + ?) / (sample_count + 1),
                    sample_count = sample_count + 1,
                    updated_at   = CURRENT_TIMESTAMP
            """, (theme, likes, likes))


# ---------- 账号矩阵 ----------
def get_accounts(active_only: bool = True) -> list:
    with get_connection() as conn:
        q = "SELECT * FROM account_matrix"
        if active_only:
            q += " WHERE active = 1"
        return [dict(r) for r in conn.execute(q).fetchall()]


def upsert_account(account_id: str, persona: str, theme_focus: str = None):
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO account_matrix (account_id, persona, theme_focus)
            VALUES (?, ?, ?)
            ON CONFLICT(account_id) DO UPDATE SET
                persona = excluded.persona,
                theme_focus = excluded.theme_focus
        """, (account_id, persona, theme_focus))


def increment_account_post(account_id: str):
    with get_connection() as conn:
        conn.execute(
            "UPDATE account_matrix SET post_count = post_count + 1 WHERE account_id = ?",
            (account_id,)
        )


def get_top_performing_scripts(limit: int = 3) -> list[dict]:
    """获取完播率最高的爆款视频脚本作为 Few-shot 示例"""
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT viral_script, cover_title, watch_rate, views
            FROM production_history
            WHERE viral_script IS NOT NULL AND watch_rate > 0
            ORDER BY watch_rate DESC, views DESC
            LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]


# ---------- 统计 ----------
def get_stats() -> dict:
    """返回故事库和账号统计信息，含主题分布与完播率飞轮快照。

    key 命名以调用方（monitor / performance_api / story_engine）为准：
    total / ready / total_accounts / active_accounts / by_theme / theme_performance
    """
    with get_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM story_pool")
        total = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM story_pool WHERE used_count < 5 AND score >= 75")
        ready = c.fetchone()[0]

        c.execute("SELECT COUNT(*) FROM account_matrix")
        total_accounts = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM account_matrix WHERE active=1")
        active_accounts = c.fetchone()[0]

        # 主题分布（条数 + 均分）
        by_theme = [
            {"theme": r["theme"], "cnt": r["cnt"], "avg_score": r["avg_score"] or 0.0}
            for r in c.execute("""
                SELECT theme, COUNT(*) AS cnt, AVG(score) AS avg_score
                FROM story_pool
                GROUP BY theme
                ORDER BY cnt DESC
            """).fetchall()
        ]

        # 主题完播率飞轮快照
        theme_performance = [
            dict(r) for r in c.execute("""
                SELECT theme, avg_watch, avg_likes, sample_count
                FROM theme_performance
                ORDER BY avg_watch DESC
            """).fetchall()
        ]

    return {
        "total": total,
        "ready": ready,
        "total_accounts": total_accounts,
        "active_accounts": active_accounts,
        "by_theme": by_theme,
        "theme_performance": theme_performance,
    }


def update_production_stats(title_snippet: str, views: int, likes: int = 0, comments: int = 0, shares: int = 0) -> bool:
    """RPA 网页抓取入口：按标题/文案片段模糊匹配生产记录，复用 update_performance 驱动飞轮。

    网页端拿不到完播率，watch_rate 传 None（只更新点赞侧信号）。
    """
    with get_connection() as conn:
        search_pattern = f"%{title_snippet}%"
        row = conn.execute(
            "SELECT id FROM production_history "
            "WHERE cover_title LIKE ? OR viral_script LIKE ? "
            "ORDER BY created_at DESC LIMIT 1",
            (search_pattern, search_pattern),
        ).fetchone()

    if not row:
        return False

    update_performance(
        production_id=row["id"],
        views=views, likes=likes, comments=comments, shares=shares,
        watch_rate=None,
    )
    return True


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    init_db()
    print(get_stats())
