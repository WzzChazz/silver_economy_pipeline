"""
story_engine.py  —  故事评分与入库引擎
重构要点：
  1. 五维评分提示词（痛点/真实性/共鸣面/新鲜度/改写潜力）
  2. httpx 替代 urllib，带指数退避重试
  3. 兜底 API 自动切换
  4. 标题党 / 低质内容预过滤
"""

import os
import json
import logging
import time
from typing import Optional

import httpx
from dotenv import load_dotenv

from src.core import database

load_dotenv()
logger = logging.getLogger(__name__)

# ---------- API 配置 ----------
def _get_llm_config(prefer: str = "deepseek") -> dict:
    """优先用 DeepSeek 评分（便宜），兜底用 Script LLM"""
    primary = {
        "api_key":  os.getenv("DEEPSEEK_API_KEY", ""),
        "base_url": os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
        "model":    os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
    }
    fallback = {
        "api_key":  os.getenv("SCRIPT_API_KEY", ""),
        "base_url": os.getenv("SCRIPT_BASE_URL", "https://api.openai.com/v1"),
        "model":    os.getenv("SCRIPT_MODEL", "gpt-4o-mini"),
    }
    cfg = primary if prefer == "deepseek" else fallback
    if not cfg["api_key"] or "your-" in cfg["api_key"]:
        logger.warning(f"{prefer} API Key 未配置，切换到备用")
        cfg = fallback
    return cfg


# ---------- 带重试的 LLM 调用 ----------
def _call_llm(
    prompt: str,
    system: str = "You output only valid JSON.",
    temperature: float = 0.3,
    prefer: str = "deepseek",
    max_retries: int = 3,
) -> Optional[dict]:
    cfg = _get_llm_config(prefer)
    url = f"{cfg['base_url']}/chat/completions"
    payload = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",   "content": prompt},
        ],
        "temperature": temperature,
    }
    headers = {
        "Content-Type":  "application/json",
        "Authorization": f"Bearer {cfg['api_key']}",
    }

    for attempt in range(max_retries):
        try:
            with httpx.Client(timeout=60) as client:
                resp = client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            content = content.replace("```json", "").replace("```", "").strip()
            return json.loads(content)
        except (httpx.HTTPStatusError, httpx.TimeoutException) as e:
            wait = 2 ** attempt
            logger.warning(f"LLM 调用失败（第 {attempt+1} 次）：{e}，{wait}s 后重试")
            time.sleep(wait)
        except json.JSONDecodeError as e:
            logger.error(f"JSON 解析失败：{e}")
            return None

    # 所有重试耗尽，尝试切换备用 API
    if prefer == "deepseek":
        logger.warning("DeepSeek 重试耗尽，切换 Script LLM")
        return _call_llm(prompt, system, temperature, prefer="script", max_retries=2)
    return None


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
你是专注 50-75 岁银发群体情感内容的资深选题编辑。
请对下面这段原始素材进行严格的多维度评分，总分 100 分（五维各 20 分满分）。

原始素材：
{story}

评分维度说明（每项 0-20 分）：
1. score_pain（痛点强度）：是否触及老年人核心痛点（老伴、儿女、孤独、健康、被遗忘感）
2. score_truth（真实性）：细节是否具体真实，有无生活气息，能否让人相信"这是真人经历"
3. score_resonance（共鸣面）：有多少比例的 50-75 岁老人能产生"说的就是我"的感觉
4. score_freshness（新鲜度）：故事角度是否独特，区别于"正能量鸡汤"套路
5. score_rewrite（改写潜力）：能否提炼出有力金句，改写后是否具有短视频爆款潜力

同时请判断故事属于以下哪一种【12大叙事原型】（严格选一）：
1. 失去型（老伴离世、搬离老屋） 2. 顿悟型（一件小事改变了看法） 3. 和解型（与子女/过去和好） 
4. 对比型（年轻时 vs 现在） 5. 发现型（老了才发现什么事情是对的） 6. 逆转型（以为是坏事却是好事）
7. 觉醒型（开始为自己而活） 8. 遗憾型（错过的人或事） 9. 释怀型（看开生死或财富）
10. 代沟型（与年轻人的观念冲突） 11. 羁绊型（老友/宠物/老物件的陪伴） 12. 告别型（面对衰老或疾病的体面）

同时归类以下字段：
- theme：必须严格从以下 4 个方向中选一（父母爱情 / 老伴故事 / 空巢 / 养老现实）
- emotion：两个字的情感总结（如：心酸、通透、感动）
- scene：故事核心场景（英文，如：hospital, home, park）
- persona：四字人物画像（如：独居老人、退休教师）
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
    result = _call_llm(_SCORE_PROMPT.format(story=raw_story), temperature=0.3)
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
