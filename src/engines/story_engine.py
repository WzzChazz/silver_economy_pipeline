"""
story_engine.py  —  故事评分与入库引擎
重构要点：
  1. 五维评分提示词（痛点/真实性/共鸣面/新鲜度/改写潜力）
  2. httpx 替代 urllib，带指数退避重试
  3. 兜底 API 自动切换
  4. 标题党 / 低质内容预过滤
"""

import logging
import time
from typing import Optional

from dotenv import load_dotenv

from src.core import database
from src.core.llm import call_llm

load_dotenv()
logger = logging.getLogger(__name__)


# ---------- 预过滤：拦截低质内容 ----------
_SPAM_KEYWORDS = [
    "点击领取", "限时免费", "私信我", "加我微信",
    "转发抽奖", "评论区见", "标题党", "震惊",
]

def _is_spam(text: str) -> bool:
    text_lower = text.lower()
    hit = [kw for kw in _SPAM_KEYWORDS if kw in text_lower]
    if hit:
        logger.info(f"预过滤拦截（营销词）：{hit}")
        return True
    if len(text) < 80:
        logger.info("预过滤拦截（字数不足 80）")
        return True
    return False


# ---------- 五维评分提示词 ----------
_SCORE_PROMPT = """
你是专注「中老年家庭情感」赛道（视频号）的资深选题编辑。
目标是筛出【能被中老年人转发到家族群/朋友圈】、且能引向「老照片纪念视频」需求的正向温暖素材。
请对下面这段原始素材严格多维评分，总分 100 分（五维各 20 分满分）。

原始素材：
{story}

评分维度说明（每项 0-20 分）：
1. score_pain（情绪/转发驱动力）：情绪是否强到让人“想转给家人”（父母爱情、年代回忆、孝心触动）。
2. score_truth（真实性）：细节是否具体真实，有生活气息，像真人经历。
3. score_resonance（共鸣面）：多少比例的中老年/其子女会“说的就是我家”。
4. score_freshness（新鲜度/非套路）：是否区别于“正能量鸡汤”，有具体年代/物件锚点。
5. score_rewrite（改写潜力）：能否提炼金句，改写成 30-45 秒短视频爆款。

【限流红线·必须严判】若素材主体是 疾病/医院/看病/卖惨/独居孤独/老伴去世 等负向限流方向，
则 score_pain 与 score_freshness 直接给 ≤8 分（这类无转发基因、易限流，不要它）。

同时请判断故事属于以下哪一种【12大叙事原型】（严格选一）：
1. 失去型 2. 顿悟型 3. 和解型 4. 对比型（年轻时 vs 现在） 5. 发现型 6. 逆转型
7. 觉醒型 8. 遗憾型 9. 释怀型 10. 代沟型 11. 羁绊型（老友/老物件的陪伴） 12. 告别型

同时归类以下字段：
- theme：必须严格从以下 4 个方向中选一（父母爱情 / 金婚岁月 / 年代记忆 / 儿女孝心）
- emotion：两个字的情感总结（如：温情、怀念、感动）
- scene：故事核心场景（英文，如：home, wedding, park）
- persona：四字人物画像（如：恩爱老伴、慈祥母亲）
- reason：打分关键理由（一句话）

必须输出严格 JSON，不含任何多余文字：
{{
    "theme": "...",
    "narrative_type": "...",
    "emotion": "...",
    "scene": "...",
    "persona": "...",
    "score_pain": 整数,
    "score_truth": 整数,
    "score_resonance": 整数,
    "score_freshness": 整数,
    "score_rewrite": 整数,
    "reason": "..."
}}
"""


# ---------- 核心评估入口 ----------
def evaluate_story(raw_story: str, source: str = "unknown") -> Optional[dict]:
    """
    评估一段原始故事，高分自动入库。
    返回评分结果 dict，入库失败或低分返回 None。
    """
    # 预过滤
    if _is_spam(raw_story):
        return None

    logger.info(f"开始 AI 五维评分（来源：{source}，字数：{len(raw_story)}）")
    result = call_llm(_SCORE_PROMPT.format(story=raw_story), temperature=0.3, prefer="deepseek")
    if not result:
        logger.error("评分失败，跳过")
        return None

    scores = {
        "pain":      int(result.get("score_pain",      0)),
        "truth":     int(result.get("score_truth",     0)),
        "resonance": int(result.get("score_resonance", 0)),
        "freshness": int(result.get("score_freshness", 0)),
        "rewrite":   int(result.get("score_rewrite",   0)),
    }
    total = sum(scores.values())
    theme   = result.get("theme",   "life")
    narrative_type = result.get("narrative_type", "默认型")
    emotion = result.get("emotion", "未知")
    scene   = result.get("scene",   "general")
    persona = result.get("persona", "退休老人")
    reason  = result.get("reason",  "")

    logger.info(
        f"评分完成：总分={total} | 痛点={scores['pain']} 真实={scores['truth']} "
        f"共鸣={scores['resonance']} 新鲜={scores['freshness']} 改写={scores['rewrite']} "
        f"| {theme} / {narrative_type} / {emotion}"
    )

    # 入库阈值：总分 ≥ 75，且痛点 + 真实性均 ≥ 13（避免空洞高分）
    if total >= 75 and scores["pain"] >= 13 and scores["truth"] >= 13:
        database.init_db()
        inserted = database.insert_story(
            theme=theme,
            title=f"{emotion}故事",
            story=raw_story,
            emotion=emotion,
            source=source,
            narrative_type=narrative_type,
            scene=scene,
            persona=persona,
            scores=scores,
        )
        if inserted:
            stats = database.get_stats()
            logger.info(f"入库成功！库中总量：{stats['total']}，可用：{stats['ready']}")
        else:
            logger.info("故事已存在（语义重复），跳过入库")
    else:
        logger.info(f"评分不达标（总分={total} 痛点={scores['pain']} 真实={scores['truth']}），丢弃。理由：{reason}")

    result["total_score"] = total
    return result


# ---------- 批量评估（供 crawler 调用）----------
def batch_evaluate(stories: list[str], source: str = "unknown") -> list[dict]:
    results = []
    for i, story in enumerate(stories):
        logger.info(f"批量评估 {i+1}/{len(stories)}")
        r = evaluate_story(story, source=source)
        if r:
            results.append(r)
        time.sleep(1.5)  # 保护 API 限速
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    test = "老伴住院那天我才知道自己离不开她。平时她做饭洗衣，我什么都不管。现在她躺在病床上，我连微波炉都不会用。那一刻我觉得自己像个废人，蹲在走廊角落哭了很久，不敢让她看见。"
    evaluate_story(test, source="manual_test")
