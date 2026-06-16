from __future__ import annotations
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

评分维度说明（每项 0-20 分）。本赛道核心是「照片唤起度」与「转发驱动力」——
能让用户对号入座、想起自家一张具体老照片，并想转给家人的素材，才有起量 + 引向纪念视频的价值：
1. score_pain（转发驱动力）★：看完想不想立刻转给爸妈/子女？转发动机越强越高分。
2. score_truth（年代真实质感）：是否有具体年代生活气息（物件、习俗、场景），不空泛。
3. score_resonance（照片唤起度）★：能让多少人“想起我家也有这样一张照片/这个物件”。这是最关键维度。
4. score_freshness（年代锚点具体度）：有没有具体的年份/物件/场景锚点（粮票、的确良、二八自行车、老挂历），而非泛泛抒情。
5. score_rewrite（短视频改写潜力）：能否提炼成 15-25 秒、第二人称唤起式的短口播。

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
def evaluate_story(raw_story: str, source: str = "unknown", force_theme: str = None) -> Optional[dict]:
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
    # 生成路径锁定主题：唤起型素材（老物件/年代感）会被评分 LLM 几乎全判成"年代记忆"，
    # 导致按账号主题拉取时其它主题没货，故 generate 传入的 force_theme 优先。
    theme   = force_theme or result.get("theme",   "life")
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

    # 入库阈值：总分 ≥ 75，且【转发驱动力 + 照片唤起度】均 ≥ 13
    # （换轴：这两维才是本赛道的命门，避免"真实但没人想转、没人对号入座"的空洞高分入库）
    if total >= 75 and scores["pain"] >= 13 and scores["resonance"] >= 13:
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


# ---------- 唤起型素材生成（替代纯采集，零侵权 + 强对号入座）----------
# 主题 → 年代物件/场景锚点池，喂给 LLM 提高多样性，避免每条都"黑白结婚照"
_THEME_ANCHORS = {
    "父母爱情": ["的确良衬衫", "二八自行车", "黑白结婚照", "搪瓷缸", "钢笔与情书", "粮票"],
    "金婚岁月": ["一碗热汤面", "起夜倒的水", "缝了又补的毛衣", "老花镜", "并排的旧藤椅"],
    "年代记忆": ["老挂历", "粮票布票", "黑白电视机", "收音机", "煤油灯", "搪瓷脸盆"],
    "儿女孝心": ["小时候的全家福", "妈妈做的第一件衣服", "爸爸扛在肩上的背影", "老相册", "压岁钱的红纸包"],
}

_GENERATE_PROMPT = """
你是「老照片·家庭回忆」赛道的资深选题策划。请围绕主题【{theme}】生成 {n} 条【唤起型素材】。

【什么是唤起型素材——最重要】
不是讲某个具体个人的独特故事，而是写【一代人的共同经历】，让千万中老年用户看完都觉得"说的就是我家"。
每条必须锚定一件【那个年代的具体物件/场景】（可参考：{anchors}，也可自选同年代物件），
并指向一张【用户家里大概率真的有】的老照片或画面，强到让人想翻出自家相册。

【硬性要求】
1. 每条 120~220 字，平实、有具体年代细节，不要金句堆砌、不要抒情口号。
2. 正向温暖：怀念 / 相守 / 惊喜 / 陪伴 / 年代质感。
3. 【绝对红线】禁止出现 去世/走了/病了/住院/卖惨/独居孤独/"最后一面" 等任何负向或死亡字眼——这类会限流、压转发。
4. {n} 条之间物件/角度要错开，不要雷同。

严格输出 JSON（不含多余文字）：
{{
    "stories": ["第一条素材...", "第二条素材...", ...]
}}
"""


def generate_evocative_stories(
    themes: list[str] = None,
    n_per_theme: int = 3,
    source: str = "generated",
) -> list[dict]:
    """围绕年代物件/集体记忆【生成】唤起型素材，逐条过 evaluate_story 评分入库。

    取代"爬别人故事再洗稿"：零侵权、强对号入座、天然契合换轴后的评分体系。
    """
    themes = themes or list(_THEME_ANCHORS.keys())
    database.init_db()
    accepted = []
    for theme in themes:
        anchors = "、".join(_THEME_ANCHORS.get(theme, []))
        logger.info(f"生成唤起型素材：主题={theme}，目标 {n_per_theme} 条")
        result = call_llm(
            _GENERATE_PROMPT.format(theme=theme, n=n_per_theme, anchors=anchors),
            system="You are a helpful assistant that outputs valid JSON.",
            temperature=0.9,
            prefer="script",  # 生成用强模型，质量优先
        )
        if not result or "stories" not in result:
            logger.warning(f"主题 {theme} 生成失败或无 stories 字段，跳过")
            continue
        for story in result["stories"]:
            if not isinstance(story, str) or len(story.strip()) < 80:
                continue
            r = evaluate_story(story.strip(), source=f"{source}_{theme}", force_theme=theme)
            if r:
                accepted.append(r)
            time.sleep(1.0)  # 保护 API 限速
    logger.info(f"唤起型素材生成完成：送审通过并入库候选 {len(accepted)} 条")
    return accepted


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    test = "老伴住院那天我才知道自己离不开她。平时她做饭洗衣，我什么都不管。现在她躺在病床上，我连微波炉都不会用。那一刻我觉得自己像个废人，蹲在走廊角落哭了很久，不敢让她看见。"
    evaluate_story(test, source="manual_test")
