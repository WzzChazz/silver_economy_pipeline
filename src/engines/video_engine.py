from __future__ import annotations
"""
video_engine.py  —  视频合成引擎 V3
重构要点：
  1. FFmpeg 直接合成替代 moviepy（提速 3-5x）
  2. 全 async 架构，统一 asyncio
  3. A/B 三版封面自动生成
  4. Whisper small 升级（中文更准）
  5. 评论区钩子文案自动生成
  6. 账号矩阵多人设支持
"""

import asyncio
import glob
import json
import logging
import os
import random
import subprocess
import tempfile
import time
from typing import Optional

from dotenv import load_dotenv

from src.core import database
from src.core.llm import call_llm
from src.engines.assets_fetcher import OBJECT_REGISTRY

load_dotenv()
logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ASSETS_DIR = os.path.join(BASE_DIR, os.getenv("ASSETS_DIR", "assets"))
OUTPUT_DIR = os.path.join(BASE_DIR, os.getenv("OUTPUT_DIR", "output"))
BROLLS_DIR = os.path.join(ASSETS_DIR, "brolls")
BGMS_DIR   = os.path.join(ASSETS_DIR, "bgms")
FONT_PATH  = os.getenv("FONT_PATH", "Arial-Unicode-MS")  # 字体「名」，给 ASS/libass 用


def _resolve_font_file() -> str:
    """解析一个真实存在的中文字体「文件路径」给 FFmpeg drawtext(封面)用。
    drawtext 的 fontfile= 必须是文件路径,不能是字体名,否则中文渲染成豆腐块。"""
    for c in [
        os.getenv("FONT_FILE", ""),
        "/Library/Fonts/Arial Unicode.ttf",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",       # Linux 常见中文字体
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    ]:
        if c and os.path.exists(c):
            return c
    logger.warning("未找到中文字体文件，封面文字可能渲染异常；可在 .env 设 FONT_FILE 指向 .ttf/.ttc")
    return FONT_PATH


FONT_FILE = _resolve_font_file()  # 字体「文件」，给 drawtext 用

# ffmpeg/ffprobe：优先用 .env 指定，其次项目内 bin/（静态二进制，绕过系统未装 ffmpeg），最后回退 PATH
_LOCAL_FFMPEG  = os.path.join(BASE_DIR, "bin", "ffmpeg")
_LOCAL_FFPROBE = os.path.join(BASE_DIR, "bin", "ffprobe")
FFMPEG  = os.getenv("FFMPEG_PATH")  or (_LOCAL_FFMPEG  if os.path.exists(_LOCAL_FFMPEG)  else "ffmpeg")
FFPROBE = os.getenv("FFPROBE_PATH") or (_LOCAL_FFPROBE if os.path.exists(_LOCAL_FFPROBE) else "ffprobe")

for d in [ASSETS_DIR, OUTPUT_DIR, BROLLS_DIR, BGMS_DIR]:
    os.makedirs(d, exist_ok=True)
for t in ["父母爱情", "金婚岁月", "年代记忆", "儿女孝心"]:
    os.makedirs(os.path.join(BROLLS_DIR, t), exist_ok=True)

SERIES_NAMES = ["《老照片里的爱情》", "《爸妈年轻时》", "《时光纪念册》", "《老来伴》", "《岁月如歌》"]

# 叙事结构库（与 _SCRIPT_PROMPT 结构库一一对应）。
# LLM 单条独立调用、无法感知上一条用了什么，故由代码按集数做确定性轮换指针，
# 强制结构循环，防止连续多条撞同一种开头（防连撞）。
NARRATIVE_STRUCTURES = [
    "A. 唤起型（你家是不是也有…）",
    "B. 悬念物件型（从有故事的老物件切入，正向悬念）",
    "C. 数字反差型（用具体年代数字制造反差）",
    "D. 第一人称自述（老人独白，情感浓）",
    "E. 今昔对比型（古今对照）",
    "F. 提问代入型（唤起回忆/倾诉欲）",
    "G. 纯白描叙事（零钩子，纯集体记忆情绪）",
]


def _pick_structure_hint(identity: dict) -> str:
    """按集数确定性轮换出本条优先结构；无集数时随机，保证多样性。"""
    episode = identity.get("episode")
    idx = (episode - 1) if episode else random.randrange(len(NARRATIVE_STRUCTURES))
    return NARRATIVE_STRUCTURES[idx % len(NARRATIVE_STRUCTURES)]


def _account_identity(account: dict = None, persona: str = "退休老人") -> dict:
    """按账号稳定派生 旁白性别 / 系列名 / 集数，保证同一账号人设、音色、系列连续不变。

    - 无 account（兜底）时给默认值。
    - gender/series 用 account_id 做稳定哈希，避免每条视频乱跳。
    - episode 用 account_matrix.post_count + 1（produce 流程每条后会自增）。
    """
    import hashlib
    if not account:
        return {"narrator_gender": "female", "series_title": SERIES_NAMES[0], "episode": None}

    seed = int(hashlib.md5(account["account_id"].encode()).hexdigest(), 16)
    gender = "female" if seed % 2 == 0 else "male"
    series = SERIES_NAMES[seed % len(SERIES_NAMES)]
    episode = (account.get("post_count") or 0) + 1
    return {"narrator_gender": gender, "series_title": series, "episode": episode}


# ============================================================
# 文案生成（五段式脚本 + A/B 封面 + 评论钩子）
# ============================================================
_SCRIPT_PROMPT = """
你是专注微信视频号「老照片·家庭回忆」赛道的顶级操盘手。
账号唯一价值主张：帮用户唤起并留住「爸妈年轻时 / 那个年代」的家庭记忆。
本条视频的真正目标不是“讲一个催泪故事”，而是让刷到的中老年用户【对号入座，想起自己家里那一张具体的老照片】——
对号入座 = 想转给家人（起量）+ 想自己也留一份（变现种子），这两件事是同一个动作的两面。

把下面这段【唤起型素材】改写成一条 15~25 秒的口播脚本（这是“勾起回忆”的引子，不要照抄）：
"{story}"

人设 IP："{persona}"（固定旁白，性别已定：{narrator_gender}，全程一致，不得更改）
主题方向：{theme}
当前日期：{current_date}

【硬性长度——最重要】
1. 口播正文总字数严格 60~90 字（约 15~25 秒），宁短勿长。数据证明超过 25 秒用户就划走。
2. 用 \\n 断句，每行不超过 10 字，总行数 5~8 行。

【叙事结构——核心规则：从结构库挑一种，严禁每条都用同一种开头】
绝对不要每条都用“你家是不是也有”这种开头——结构雷同 = 账号像机器套模板，刷三条就腻，平台判同质化限流。
下面是 7 种开头/叙事结构，请根据本条素材的特质，挑【一种】最贴的来写：
   A. 唤起型：“你家是不是也有…”——第二人称直接点用户，适合通用老照片。
   B. 悬念物件型：“这张照片我妈在箱底压了30年，我出嫁那天才翻出来…”——从有故事的物件切入制造悬念（悬念只能靠“未揭晓的温情”，绝不能靠“谁走了/谁病了”）。
   C. 数字反差型：“1983年我爸月薪38块，却…”——用具体年代数字制造反差。
   D. 第一人称自述：“我叫张桂兰，今年71…”——人设强、情感浓时用第一人称独白。
   E. 今昔对比型：“现在婚纱照拍几万张，我爸妈这辈子就一张黑白的”——古今对照。
   F. 提问代入型：“你还记得爸妈年轻时长什么样吗？”——唤起愧疚或倾诉欲。
   G. 纯白描叙事：“那个年代结婚，一辆二八自行车就把媳妇娶回家”——零钩子，纯集体记忆情绪。
【本条结构建议】本条请优先采用：{structure_hint}（若该结构与素材实在不搭可换一种，但绝不要用最近几条重复的结构）。
通用底线：禁抽象说理开头（不能用“人到晚年才明白…”）；除 G 外尽量让用户能“对号入座”到自己家。
【结构红线·所有结构通用】无论用哪种结构（尤其 B 悬念型、D 自述型），都绝对不许出现“去世/走了/病了/住院/最后一面/再也…”等死亡或卖惨字眼——这类必被限流且压转发。悬念与情感一律走【温情、惊喜、重逢、怀念】等正向方向。

【正文：情感价值 +（尽量）轻遗憾埋变现痛点】
中段给足年代感的情绪价值（那个年代没有婚纱钻戒，却笑得那么好看…）；
在不破坏所选结构的前提下，结尾前【轻轻】点一句“遗憾”：照片放久了泛黄、模糊、边角卷了（纯白描型 G 可省略）。
只埋痛点，绝不在口播里给解决方案、不提“修复/小程序/做视频”。

【限流红线·绝对禁止】
1. 医院/看病/生病/卖惨/独居孤独/老伴去世——负向、易限流、无转发基因。
2. 死亡紧迫感：“趁父母还在/趁还来得及/子欲养而亲不待”——会被举报、压转发。
孝心一律用【惊喜/陪伴/感恩/节日】正向框架。

【钩子——核心规则：动态匹配，宁缺毋滥，严禁每条都套同一套】
不要把“互动+追更+转发”三件套全塞进去。请你先判断这条内容的类型与情绪，
再从下面钩子库里【最多挑 1~2 个最贴的】放进结尾；情感特别浓的纯情绪片，可以一个钩子都不放，只靠内容。
切忌每条视频钩子雷同——雷同会被平台判为模板营销号而限流。

钩子库（按内容类型适配，挑最自然的，不要硬套）：
A. 互动钩子（报年份/报数字型，零成本最易接话）：
   结婚照→“你家爸妈哪年结的婚？评论区报个年份”；合影→“照片里你几岁？”；全家福→“你家全家福几口人？”；老物件→“你家还有这个吗？”
B. 追更钩子（主题合集+集齐心理，给“不关注会错过”的理由，关联系列名）：
   “这个《那个年代》系列我整理了一整套，关注我别错过下一张”“那个年代的结婚习俗我要讲完一整组”。
C. 转发钩子（站内转发，起量命脉）：按 narrator_gender 适配——
   老人自述→“家里有老照片的，转给孩子看看”；子女视角→“有同感的，转给兄弟姐妹”。

【口播红线】正文【绝对不要】出现“主页找我/私信/加微信/小程序/教程”等导流词（起号期口播喊导流=营销号，限流且没人转）。

【封面钩子】生成 3 版（cover_a/b/c），各 8 字内、两行用 \\n，从以下选 3 种不同类型：
1.年龄型 2.反问型 3.场景代入型 4.对比冲突型 5.身份认同型 6.结论前置型 7.悬念留白型 8.数字具体型。

【评论区带头评论 comment_hook】（这是你用小号置顶、给用户“示范怎么接话”的那条，第一人称、真诚、不超过 40 字）
{comment_hook_rule}

【画面描述词 image_prompt】一句 20 字内画面描述，主体是【一张有年代感的老照片/老物件】而非现画人脸：
- 突出“泛黄、黑白、胶片感、旧相册”等真实老照片质感，弱化具体人脸。
- 示例：泛黄的黑白结婚照躺在旧相册里，胶片颗粒感，怀旧暖调，写实。

参考历史高互动样本风格（Few-shot）：
{few_shot_examples}

输出严格 JSON（不含多余文字）：
{{
    "learning_analysis": "（内部复盘）本条用了哪种叙事结构（A~G）、勾起了哪张照片的对号入座、挑了哪个钩子、钩子密度是否克制",
    "viral_text": "正文 60~90字，\\n 断句，第二人称唤起开头+轻遗憾，结尾按内容动态挑0~2个钩子（不要导流词）",
    "image_prompt": "老照片/老物件画面描述词",
    "hooks_used": "本条实际用了哪些钩子（如：互动+转发 / 仅转发 / 无），便于人工核对钩子密度",
    "interaction_hook": "本条采用的互动提问（没用则空字符串）",
    "follow_hook": "本条采用的追更/集齐话术（没用则空字符串）",
    "cover_a": "类型X\\n封面",
    "cover_b": "类型Y\\n封面",
    "cover_c": "类型Z\\n封面",
    "comment_hook": "小号带头评论的示范文案"
}}
"""

# 变现阶段开关：按账号成长阶段控制评论区软引流强度
#   0 起号期：纯互动、不引流（默认，先养号攒信任）
#   1 起量期：互动 + 一句软引流（“做法整理在主页了”）
#   2 变现期：互动 + 更明确的承接引导
def _monetize_stage() -> int:
    try:
        return max(0, min(2, int(os.getenv("MONETIZE_STAGE", "0"))))
    except ValueError:
        return 0


def _comment_hook_rule(stage: int) -> str:
    if stage >= 2:
        return ("先引导互动（“评论区说说你和爸妈的故事”），再明确承接一句"
                "（如“想做同款的扣个‘想’，我看到回你”），但绝不用“扣1/加微信”这类违规词。")
    if stage == 1:
        return ("先引导互动（“评论区说说你和爸妈的故事”），再轻轻带一句软引流"
                "（如“好多人问怎么做的，整理在主页了”），不得用“扣1/加微信”。")
    return ("【起号期】只做纯情感互动，引导用户在评论区分享自己的故事或回忆"
            "（如“评论区说说你和爸妈年轻时的故事”），绝对不要出现任何引流/主页/做法/教程等字样。")


# ============================================================
# 内容模式：photo（老照片家庭情感，默认/第二阶段变现线）
#           object（老物件年代记忆，第一阶段规模化养号线）
# ============================================================
def _content_mode() -> str:
    return os.getenv("CONTENT_MODE", "photo").strip().lower()


_OBJECT_SERIES = ["《老物件里的年代》", "《那些年的老物件》", "《年代记忆》"]

_OBJECT_SCRIPT_PROMPT = """
你是专注微信视频号「老物件 · 年代记忆」赛道的顶级操盘手。
核心目标:让中老年用户看到一件老物件就被勾起集体回忆,【想转给家人/在评论区报年代】。

本期老物件:【{object_cn}】（{era}；细节:{sensory}）
人设旁白:固定一位{narrator_gender}声中老年讲述者,温和、有阅历、说大白话。
当前日期:{current_date}

【真实性红线·非常重要】
- 这是【集体回忆/年代讲述】,不是某个真人的真实经历。一律用"那个年代/家家户户/咱们小时候"这种**集体视角**,
  绝对不要编造"这是我妈的/我家的"具体真人真事(会涉编造+不可信)。
- 画面用的是【真实老物件图】,所以文案只讲物件和年代,不要描述具体人脸。

【限流红线】禁止 医院/疾病/卖惨/独居/去世,禁止"趁还在/子欲养而亲不待"等死亡紧迫感措辞。基调温暖、怀旧、带点会心一笑。

【硬性长度】口播正文 60~90 字(约 25~35 秒),\\n 断句,每行≤10 字,8~12 行。

【前 3 秒钩子】第一句必须直接点出这件老物件 + 一个具体细节,瞬间唤起"我家也有过"。
例:"这个搪瓷缸\\n你家肯定也有过"。禁止抽象说理开头。

【结尾钩子——动态挑 1~2 个,别全塞,雷同会被判模板号限流】
A. 互动钩子(最易接话):"你家还有这个吗?评论区报个年代" / "你是哪年的?报一下"。
B. 追更钩子:关联系列名,给"不关注会错过"的理由:"老物件我整理了一整套,关注别错过下一件"。
C. 转发钩子:"家里有这物件的,转给孩子看看那个年代"。
口播正文【绝对不要】出现"主页/私信/加微信/小程序/教程"等导流词。

【评论区带头评论 comment_hook】(小号置顶示范,第一人称、真诚、≤40字)
{comment_hook_rule}

【封面钩子】3 版(cover_a/b/c),各 8 字内、两行 \\n,挑 3 种不同类型(年龄型/反问型/场景代入/对比/身份认同/结论前置/悬念/数字)。

参考历史高互动样本(Few-shot):
{few_shot_examples}

输出严格 JSON(不含多余文字):
{{
    "learning_analysis": "(内部复盘)本条用了哪件物件勾对号入座、挑了哪个钩子、钩子是否克制",
    "viral_text": "正文 60~90字,\\n 断句,集体回忆视角,结尾动态挑0~2个钩子(不要导流词)",
    "image_prompt": "{object_cn}的真实物件特写描述(仅作备用,实际用真实图)",
    "hooks_used": "本条实际用了哪些钩子(如:互动+转发 / 仅互动 / 无)",
    "interaction_hook": "本条采用的互动提问(没用则空字符串)",
    "follow_hook": "本条采用的追更话术(没用则空字符串)",
    "cover_a": "类型X\\n封面",
    "cover_b": "类型Y\\n封面",
    "cover_c": "类型Z\\n封面",
    "comment_hook": "小号带头评论示范文案"
}}
"""


def _pick_object(identity: dict) -> dict:
    """按集数轮换老物件，避免短期重复；无集数则随机。"""
    episode = identity.get("episode")
    if episode:
        return OBJECT_REGISTRY[(episode - 1) % len(OBJECT_REGISTRY)]
    return random.choice(OBJECT_REGISTRY)


def _object_fallback_script(obj: dict, identity: dict, series_title: str) -> dict:
    return {
        "story_id": None,
        "theme": "年代记忆",
        "scene": obj["slug"],
        "persona": "年代讲述人",
        "narrator_gender": identity["narrator_gender"],
        "series_title": series_title,
        "learning_analysis": "兜底：集体回忆视角讲老物件，结尾互动+转发钩子（起号期不导流）。",
        "viral_text": (
            f"这个{obj['cn']}\n你家肯定也有过\n{obj['sensory']}\n那个年代\n"
            f"家家都离不开它\n如今再看见\n眼眶就热了\n你家还留着吗\n评论区报个年代\n家里有的\n转给孩子看看"
        ),
        "image_prompt": f"{obj['cn']}的真实物件特写，年代感，写实",
        "hooks_used": "互动+转发",
        "interaction_hook": "你家还有这个吗？评论区报个年代",
        "follow_hook": "",
        "cover_a": f"{obj['cn']}\n你家还有吗",
        "cover_b": "那个年代\n的老物件",
        "cover_c": "暴露年龄\n系列",
        "comment_hook": f"这个{obj['cn']}我太有印象了，你家是哪年的？评论区聊聊",
    }


def _generate_object_script(account: dict = None) -> Optional[dict]:
    """老物件年代记忆模式：围绕一件真实老物件生成集体回忆口播，不依赖故事库。"""
    persona = (account.get("persona") if account else None) or "年代讲述人"
    identity = _account_identity(account, persona)
    # 系列名走老物件系列
    seed = sum(ord(c) for c in (account["account_id"] if account else "default"))
    series_base = _OBJECT_SERIES[seed % len(_OBJECT_SERIES)]
    series_title = f"{series_base} 第{identity['episode']}期" if identity.get("episode") else series_base

    obj = _pick_object(identity)
    from datetime import datetime
    current_date = datetime.now().strftime("%Y-%m-%d %A")
    stage = _monetize_stage()
    logger.info(f"[物件模式] 老物件={obj['cn']}({obj['slug']}) 性别={identity['narrator_gender']} "
                f"系列={series_title} MONETIZE_STAGE={stage}")

    top_scripts = database.get_top_performing_scripts(limit=3)
    few_shot = "".join(
        f"爆款示例{i+1}：\n{s['viral_script']}\n\n" for i, s in enumerate(top_scripts)
    ) or "暂无历史数据，请自由发挥。"

    result = call_llm(_OBJECT_SCRIPT_PROMPT.format(
        object_cn=obj["cn"], era=obj["era"], sensory=obj["sensory"],
        narrator_gender=identity["narrator_gender"],
        current_date=current_date,
        comment_hook_rule=_comment_hook_rule(stage),
        few_shot_examples=few_shot,
    ), system="You are a helpful assistant that outputs valid JSON.",
       temperature=0.85, prefer="script")

    if not result:
        logger.warning("[物件模式] 文案生成失败，使用兜底")
        return _object_fallback_script(obj, identity, series_title)

    result.update({
        "story_id": None,
        "theme": "年代记忆",
        "scene": obj["slug"],          # 让 _pick_background 匹配 brolls/年代记忆/<slug>/
        "persona": persona,
        "narrator_gender": identity["narrator_gender"],
        "series_title": series_title,
    })
    return result


def generate_viral_script(account: dict = None) -> Optional[dict]:
    """从故事库拉取故事并改写为视频脚本（photo 模式）；object 模式走老物件年代记忆。"""
    if _content_mode() == "object":
        return _generate_object_script(account)

    database.init_db()

    persona    = account["persona"]    if account else None
    theme_focus = account.get("theme_focus") if account else None

    story_record = database.get_story_for_persona(
        persona=persona,
        theme=theme_focus,
        min_score=75,
    )

    if not story_record:
        logger.warning("故事库暂无可用内容，使用兜底文案")
        return _fallback_script()

    story_id = story_record["id"]
    raw_story = story_record["story"]
    theme     = story_record["theme"]
    chosen_persona = story_record.get("persona") or persona or "退休老人"

    # 账号绑定的稳定身份（性别/系列/集数），避免人设乱跳
    identity = _account_identity(account, chosen_persona)

    logger.info(
        f"拉取故事 ID={story_id} score={story_record['score']} persona={chosen_persona} "
        f"性别={identity['narrator_gender']} 系列={identity['series_title']} 第{identity['episode']}期"
    )

    from datetime import datetime
    current_date = datetime.now().strftime("%Y-%m-%d %A")

    top_scripts = database.get_top_performing_scripts(limit=3)
    few_shot_examples = ""
    if top_scripts:
        for i, s in enumerate(top_scripts):
            few_shot_examples += f"爆款示例{i+1}（高播放量）：\n{s['viral_script']}\n\n"
    else:
        few_shot_examples = "暂无历史数据，请自由发挥。"

    stage = _monetize_stage()
    logger.info(f"变现阶段 MONETIZE_STAGE={stage}（0起号/1起量/2变现）")
    structure_hint = _pick_structure_hint(identity)
    logger.info(f"本条叙事结构（轮换防连撞）：{structure_hint}")
    result = call_llm(_SCRIPT_PROMPT.format(
        story=raw_story,
        persona=chosen_persona,
        narrator_gender=identity["narrator_gender"],
        theme=theme,
        current_date=current_date,
        few_shot_examples=few_shot_examples,
        comment_hook_rule=_comment_hook_rule(stage),
        structure_hint=structure_hint,
    ), system="You are a helpful assistant that outputs valid JSON.",
       temperature=0.85, prefer="script")

    if not result:
        logger.warning("文案生成失败，使用兜底文案")
        return _fallback_script(story_id=story_id, theme=theme, identity=identity)

    # 集数后缀让系列可“追更”
    series_title = identity["series_title"]
    if identity["episode"]:
        series_title = f"{series_title} 第{identity['episode']}期"

    result.update({
        "story_id":        story_id,
        "theme":           theme,
        "scene":           story_record.get("scene", "general"),
        "persona":         chosen_persona,
        "narrator_gender": identity["narrator_gender"],
        "series_title":    series_title,
    })
    return result


def _fallback_script(story_id=None, theme="父母爱情", identity=None) -> dict:
    """兜底文案：走转发型「父母爱情/老照片」方向，结尾含转发钩子 + 软引流。"""
    identity = identity or _account_identity(None)
    series_title = identity["series_title"]
    if identity.get("episode"):
        series_title = f"{series_title} 第{identity['episode']}期"
    return {
        "story_id":    story_id,
        "theme":       theme,
        "scene":       "home",
        "persona":     "退休老人",
        "learning_analysis": "兜底：第二人称唤起『你家那张老照片』，轻点泛黄遗憾，结尾仅互动+转发钩子（起号期不导流）。",
        "narrator_gender": identity["narrator_gender"],
        "series_title": series_title,
        # 短文案（约18秒）：第二人称唤起开头 + 轻遗憾 + 互动钩子 + 转发钩子
        "viral_text":  "你家是不是也有\n一张爸妈年轻时\n的黑白合影\n那个年代没婚纱\n却笑得真好看\n可惜放久了\n都泛黄模糊了\n你家爸妈\n哪年结的婚\n评论区报个年份\n有老照片的\n转给孩子看看",
        "image_prompt": "泛黄的黑白结婚照躺在旧相册里，胶片颗粒感，怀旧暖调，写实。",
        "hooks_used":   "互动+转发",
        "interaction_hook": "你家爸妈哪年结的婚？评论区报个年份",
        "follow_hook":  "",
        "cover_a":     "爸妈年轻时\n有多好看",
        "cover_b":     "那个年代\n的爱情",
        "cover_c":     "黑白照片\n藏着情话",
        # 小号带头评论（给用户示范怎么接话），起号期纯情感、不导流
        "comment_hook": (
            "我家是1986年的，爸妈那会儿才二十出头，你家呢？❤️"
            if _monetize_stage() == 0 else
            "我家是1986年的，你家呢？好多人问这种老照片咋做的，整理在主页啦❤️"
        ),
    }


# ============================================================
# TTS 生成（F5 真人克隆音 / edge-tts 双后端，TTS_BACKEND 切换）
# ============================================================
VOICES_DIR = os.path.join(ASSETS_DIR, "voices")
_f5_model = None  # 模型缓存：批量生产只加载一次


def _get_f5_model():
    """懒加载并缓存 F5-TTS 模型。"""
    global _f5_model
    if _f5_model is None:
        os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")  # HF 镜像，防模型下载失败
        from f5_tts.api import F5TTS
        _f5_model = F5TTS()
        logger.info("F5-TTS 模型加载完成（已缓存）")
    return _f5_model


def _ref_voice(gender: str):
    """按性别取参考音 (audio_path, ref_text)；缺失逐级回退 gender→female→male。"""
    exts = ("wav", "mp3", "m4a", "flac")
    for g in [gender, "female", "male"]:
        txt = os.path.join(VOICES_DIR, f"{g}.txt")
        if not os.path.exists(txt):
            continue
        for c in sorted(glob.glob(os.path.join(VOICES_DIR, f"{g}.*"))):
            if c.rsplit(".", 1)[-1].lower() in exts:
                with open(txt, encoding="utf-8") as f:
                    return c, f.read().strip()
    return None, None


def _f5_generate(text: str, output_path: str, gender: str):
    """F5-TTS 克隆音合成（同步、重计算，由 generate_audio 用线程池调用）。"""
    import torch
    import torchaudio
    import torchaudio.functional as AF

    ref_audio, ref_text = _ref_voice(gender)
    if not ref_audio:
        raise RuntimeError(f"参考音缺失：请在 {VOICES_DIR} 放 {gender}.wav/.mp3 + 同名 .txt")

    model = _get_f5_model()
    logger.info(f"F5 克隆音推理中：ref={os.path.basename(ref_audio)} gender={gender}")
    wav, sr, _ = model.infer(ref_file=ref_audio, ref_text=ref_text, gen_text=text)

    waveform = torch.tensor(wav).unsqueeze(0)
    steps = float(os.getenv("F5_PITCH_STEPS", "2.5"))
    if steps:
        waveform = AF.pitch_shift(waveform, sr, n_steps=steps)

    # F5 输出 wav；主流程音频路径是 .mp3，先存临时 wav 再 ffmpeg 转码
    tmp_wav = output_path + ".f5.wav"
    torchaudio.save(tmp_wav, waveform, sr)
    subprocess.run([FFMPEG, "-y", "-i", tmp_wav, output_path],
                   check=True, capture_output=True)
    os.remove(tmp_wav)


def _minimax_generate(text: str, output_path: str, gender: str):
    """MiniMax 海螺语音合成（云端，Intel mac 无障碍；直接返回 mp3，无需 ffmpeg 转码）。"""
    import httpx
    api_key  = os.getenv("MINIMAX_API_KEY", "")
    group_id = os.getenv("MINIMAX_GROUP_ID", "")
    if not api_key or not group_id:
        raise RuntimeError("MiniMax 需配置 MINIMAX_API_KEY 与 MINIMAX_GROUP_ID")

    if gender == "female":
        voice_id = os.getenv("MINIMAX_VOICE_FEMALE", "Chinese (Mandarin)_Wise_Women")
    else:
        voice_id = os.getenv("MINIMAX_VOICE_MALE", "Chinese (Mandarin)_Gentleman")
    model = os.getenv("MINIMAX_MODEL", "speech-01-turbo")

    url = f"https://api.minimax.chat/v1/t2a_v2?GroupId={group_id}"
    payload = {
        "model": model,
        "text": text,
        "stream": False,
        "voice_setting": {
            "voice_id": voice_id,
            "speed": float(os.getenv("MINIMAX_SPEED", "1.0")),
            "vol": 1.0,
            "pitch": int(os.getenv("MINIMAX_PITCH", "0")),
        },
        "audio_setting": {"sample_rate": 32000, "bitrate": 128000, "format": "mp3"},
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    logger.info(f"MiniMax 合成中：voice={voice_id} gender={gender}")
    with httpx.Client(timeout=60) as client:
        resp = client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
    data = resp.json()
    audio_hex = (data.get("data") or {}).get("audio")
    if not audio_hex:
        raise RuntimeError(f"MiniMax 无音频返回：{data.get('base_resp', data)}")
    with open(output_path, "wb") as f:
        f.write(bytes.fromhex(audio_hex))


async def _to_thread(func, *args):
    """Python 3.8 兼容：替代 3.9+ 的 asyncio.to_thread（把同步重计算丢线程池）。"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, func, *args)


async def generate_audio(text: str, output_path: str, gender: str = "male"):
    """TTS 总入口。TTS_BACKEND ∈ {edge, minimax, f5}；云/克隆失败自动降级 edge-tts。"""
    import re
    # 移除大模型可能生成的 SSML 标签，防止破坏 edge-tts XML 导致 NoAudioReceived
    clean_text = re.sub(r'<[^>]+>', '', text).strip()

    backend = os.getenv("TTS_BACKEND", "edge").lower()
    if backend == "minimax":
        try:
            await _to_thread(_minimax_generate, clean_text, output_path, gender)
            logger.info(f"MiniMax 语音完成：{output_path}")
            return
        except Exception as e:
            logger.error(f"MiniMax 失败，降级 edge-tts：{e}")
    elif backend == "f5":
        try:
            await _to_thread(_f5_generate, clean_text, output_path, gender)
            logger.info(f"F5 克隆音完成：{output_path}")
            return
        except Exception as e:
            logger.error(f"F5 克隆音失败，降级 edge-tts：{e}")

    # edge-tts（默认 / F5 兜底）—— 懒加载，F5-only 环境无需安装 edge_tts
    import edge_tts
    if gender == "female":
        voice = os.getenv("TTS_VOICE_MODEL_FEMALE", "zh-CN-liaoning-XiaobeiNeural")
        rate  = os.getenv("TTS_SPEECH_RATE", "-15%")
        pitch = os.getenv("TTS_PITCH", "-15Hz")
    else:
        voice = os.getenv("TTS_VOICE_MODEL", "zh-CN-YunjianNeural")
        rate  = os.getenv("TTS_SPEECH_RATE", "-10%")
        pitch = os.getenv("TTS_PITCH", "-10Hz")
    logger.info(f"TTS=edge 生成中：voice={voice}, gender={gender}")
    communicate = edge_tts.Communicate(clean_text, voice, rate=rate, pitch=pitch)
    await communicate.save(output_path)
    logger.info(f"edge-tts 完成：{output_path}")


# ============================================================
# Whisper 字幕时间戳（升级 small 模型）
# ============================================================
_whisper_model = None  # 模型缓存：批量生产时只加载一次


def _get_whisper_model():
    """懒加载并缓存 Whisper small 模型，避免每条视频重复加载。"""
    global _whisper_model
    if _whisper_model is None:
        import whisper
        import numpy as np

        def _load_audio(file, sr=16000):
            cmd = [
                FFMPEG, "-nostdin", "-threads", "0",
                "-i", file, "-f", "s16le", "-ac", "1",
                "-acodec", "pcm_s16le", "-ar", str(sr), "-"
            ]
            out = subprocess.run(cmd, capture_output=True, check=True, timeout=60).stdout
            return np.frombuffer(out, np.int16).flatten().astype(np.float32) / 32768.0

        whisper.audio.load_audio = _load_audio
        _whisper_model = whisper.load_model("small")
        logger.info("Whisper small 模型加载完成（已缓存）")
    return _whisper_model


def get_whisper_timestamps(audio_path: str, text: str) -> list[dict]:
    logger.info("Whisper small 字幕对齐中...")
    try:
        model = _get_whisper_model()
        result = model.transcribe(audio_path, fp16=False, language="zh", word_timestamps=True)
    except Exception as e:
        logger.warning(f"Whisper 加载或执行失败：{e}。若资源有限建议更换为 API 版本或 whisper.cpp，当前退回均匀分配时间戳")
        return _uniform_timestamps(audio_path, text)

    phrases = [p.strip() for p in text.split("\n") if p.strip()]
    char_times = []
    for seg in result.get("segments", []):
        for w in seg.get("words", []):
            word = w["word"].strip()
            if not word:
                continue
            dur = w["end"] - w["start"]
            tpc = dur / max(len(word), 1)
            for i, ch in enumerate(word):
                char_times.append({
                    "char":  ch,
                    "start": w["start"] + i * tpc,
                    "end":   w["start"] + (i + 1) * tpc,
                })

    if not char_times:
        return _uniform_timestamps(audio_path, text)

    timestamps = []
    char_idx = 0
    for phrase in phrases:
        clean = "".join(c for c in phrase if c.isalnum() or "\u4e00" <= c <= "\u9fff")
        need = max(len(clean), len(phrase))
        start_t = end_t = None
        matched = 0
        while matched < need and char_idx < len(char_times):
            ct = char_times[char_idx]
            if start_t is None:
                start_t = ct["start"]
            end_t = ct["end"]
            if ct["char"].isalnum() or "\u4e00" <= ct["char"] <= "\u9fff":
                matched += 1
            char_idx += 1
        if start_t is not None:
            timestamps.append({"text": phrase, "start": start_t, "end": end_t})

    logger.info(f"Whisper 对齐完成：{len(timestamps)} 句")
    return timestamps


def _uniform_timestamps(audio_path: str, text: str) -> list[dict]:
    """Whisper 降级：均匀分配时间戳（不看实际语音，字幕极易与配音错位）。"""
    logger.warning(
        "字幕走【均匀分配】降级 —— 未用 Whisper 逐字对齐，字幕大概率与配音对不上。"
        "请确认已安装 openai-whisper 且能加载 small 模型（详见 docs/装机与配音指南.md C 节）。"
    )
    result = subprocess.run(
        [FFPROBE, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", audio_path],
        capture_output=True, text=True
    )
    try:
        duration = float(result.stdout.strip())
    except Exception:
        duration = 40.0

    phrases = [p.strip() for p in text.split("\n") if p.strip()]
    total_chars = sum(len(p) for p in phrases) or 1
    tpc = duration / total_chars
    timestamps = []
    cur = 0.0
    for phrase in phrases:
        dur = len(phrase) * tpc
        timestamps.append({"text": phrase, "start": cur, "end": cur + dur})
        cur += dur
    return timestamps


# ============================================================
# AI 背景生成与 FFmpeg 视频合成
# ============================================================
def generate_ai_background(image_prompt: str) -> Optional[str]:
    """调用智谱 AI 生成剧情匹配的背景图"""
    zhipu_api_key = os.getenv("ZHIPU_API_KEY")
    if not zhipu_api_key or "your-zhipu-api-key" in zhipu_api_key:
        logger.info("未配置有效的 ZHIPU_API_KEY，跳过 AI 绘图，使用本地素材")
        return None
    
    try:
        from zhipuai import ZhipuAI
        client = ZhipuAI(api_key=zhipu_api_key)
        logger.info(f"正在调用智谱 CogView-3 生成背景图: {image_prompt}")
        response = client.images.generations(
            model="cogview-3-plus",
            prompt=image_prompt,
        )
        image_url = response.data[0].url
        
        import urllib.request
        import uuid
        save_dir = os.path.join(BROLLS_DIR, "ai_generated")
        os.makedirs(save_dir, exist_ok=True)
        out_path = os.path.join(save_dir, f"bg_{uuid.uuid4().hex[:8]}.jpg")
        urllib.request.urlretrieve(image_url, out_path)
        logger.info(f"AI 背景图已保存: {out_path}")
        return out_path
    except ImportError:
        logger.error("未安装 zhipuai 库，请执行 pip install zhipuai")
        return None
    except Exception as e:
        logger.error(f"智谱 AI 绘图失败: {e}")
        return None


# 年代空镜 prompt 库：刻意【不含人脸】（人脸是AI假感的测谎点），只画年代物件/场景，竖构图、胶片质感
BG_SCENE_PROMPTS = {
    "父母爱情": [
        "褪色的旧情书和一支钢笔放在木桌上，泛黄信纸，暖黄灯光，怀旧，无人物，写实摄影，竖构图",
        "一件的确良衬衫挂在老式木衣架上，斑驳墙面，年代感，无人物，写实摄影，竖构图",
        "旧搪瓷缸和一把铜钥匙放在木桌上，暖黄灯光，怀旧，无人物，写实摄影，竖构图",
    ],
    "金婚岁月": [
        "两把并排的旧藤椅放在老屋窗前，夕阳暖光，温情，无人物，写实摄影，竖构图",
        "一碗冒热气的汤面放在斑驳木桌上，家常温暖，怀旧，无人物，写实摄影，竖构图",
        "缝补过的旧毛衣和老花镜放在一起，柔和光线，年代感，无人物，写实摄影，竖构图",
    ],
    "年代记忆": [
        "墙上老式挂历特写，泛黄纸张，八十年代风格，无人物，写实摄影，竖构图",
        "粮票和布票特写，泛黄旧纸，浓郁年代感，无人物，写实摄影，竖构图",
        "老式黑白电视机放在木柜上，怀旧客厅一角，无人物，写实摄影，竖构图",
    ],
    "儿女孝心": [
        "一个旧帆布书包挂在木门后，怀旧暖调，无人物，写实摄影，竖构图",
        "一件小孩的旧棉袄叠放在老木箱上，怀旧暖调，无人物，写实摄影，竖构图",
        "一摞旧小学课本和铁皮铅笔盒放在木桌上，怀旧光线，无人物，写实摄影，竖构图",
    ],
}


def generate_background_library(themes: list = None, n_per_theme: int = 3) -> list:
    """用 CogView 批量生成【年代空镜】背景，存入 brolls/{主题}/ 供 produce 复用。

    预生成（而非每条现画）：省钱、可人工筛选、可复用；只画物件不画人脸，降低 AI 假感。
    """
    zhipu_api_key = os.getenv("ZHIPU_API_KEY")
    if not zhipu_api_key or "your-" in zhipu_api_key:
        logger.error("未配置有效 ZHIPU_API_KEY，无法生成背景素材库")
        return []
    import httpx
    import urllib.request
    import uuid
    api_url = "https://open.bigmodel.cn/api/paas/v4/images/generations"
    headers = {"Authorization": f"Bearer {zhipu_api_key}", "Content-Type": "application/json"}
    themes = themes or list(BG_SCENE_PROMPTS.keys())
    saved = []
    for theme in themes:
        prompts = BG_SCENE_PROMPTS.get(theme, [])[:n_per_theme]
        theme_dir = os.path.join(BROLLS_DIR, theme)
        os.makedirs(theme_dir, exist_ok=True)
        for p in prompts:
            for attempt in range(4):
                try:
                    logger.info(f"CogView 生成背景：{theme} | {p[:24]}…")
                    resp = httpx.post(api_url, headers=headers, timeout=90,
                        json={"model": "cogview-3-plus", "prompt": p, "watermark_enabled": False})
                    resp.raise_for_status()
                    url = resp.json()["data"][0]["url"]
                    out = os.path.join(theme_dir, f"bg_{uuid.uuid4().hex[:8]}.jpg")
                    urllib.request.urlretrieve(url, out)
                    saved.append(out)
                    logger.info(f"已保存背景：{out}")
                    break
                except Exception as e:
                    if "429" in str(e) and attempt < 3:
                        wait = 20 * (attempt + 1)
                        logger.warning(f"智谱限流 429，{wait}s 后重试（{attempt+1}/3）")
                        time.sleep(wait)
                        continue
                    logger.error(f"背景生成失败（{theme}）：{e}")
                    break
            time.sleep(6)  # 智谱限流（RPM）：每张间隔，避免 429
    logger.info(f"背景素材库生成完成，共 {len(saved)} 张")
    return saved


def _pick_background(theme: str, scene: str = "") -> Optional[str]:
    """优先 topic/scene → topic → 全局兜底"""
    # 1. topic/scene 精确匹配
    if scene:
        exact = glob.glob(os.path.join(BROLLS_DIR, theme, scene, "*.mp4")) + \
                glob.glob(os.path.join(BROLLS_DIR, theme, scene, "*.jpg"))
        if exact:
            return random.choice(exact)
    # 2. topic 级别
    topic_files = glob.glob(os.path.join(BROLLS_DIR, theme, "*.mp4")) + \
                  glob.glob(os.path.join(BROLLS_DIR, theme, "*.jpg"))
    if topic_files:
        return random.choice(topic_files)
    # 3. 全局兜底
    global_files = glob.glob(os.path.join(BROLLS_DIR, "*.mp4")) + \
                   glob.glob(os.path.join(BROLLS_DIR, "*.jpg"))
    if global_files:
        return random.choice(global_files)
    return None


def _pick_bgm() -> Optional[str]:
    files = glob.glob(os.path.join(BGMS_DIR, "*.mp3"))
    return random.choice(files) if files else None


def _build_ass_subtitles(timestamps: list[dict], font_path: str) -> str:
    """生成 ASS 字幕文件内容（FFmpeg 内嵌字幕）"""
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, Italic, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font_path},95,&H00FFFFFF,&H00000000,&H80000000,1,0,1,4,0,2,80,80,760,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    def _sec_to_ass(s: float) -> str:
        h = int(s // 3600)
        m = int((s % 3600) // 60)
        sec = s % 60
        return f"{h}:{m:02d}:{sec:05.2f}"

    lines = []
    for ts in timestamps:
        start = _sec_to_ass(ts["start"])
        end   = _sec_to_ass(ts["end"] + 0.05)
        text  = ts["text"].replace("\n", "\\N")
        lines.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}")

    return header + "\n".join(lines)


def create_video(
    audio_path: str,
    text: str,
    output_path: str,
    theme: str = "life",
    scene: str = "",
    bgm_path: str = None,
    bg_source: str = None,
):
    """核心：FFmpeg 合成视频（字幕内嵌，可选 BGM）"""
    bg = bg_source or _pick_background(theme, scene)
    if not bg:
        logger.warning("无背景素材，尝试下载兜底图")
        bg = _download_fallback_bg()

    timestamps = get_whisper_timestamps(audio_path, text)

    with tempfile.TemporaryDirectory() as tmp:
        ass_path = os.path.join(tmp, "subs.ass")
        with open(ass_path, "w", encoding="utf-8") as f:
            f.write(_build_ass_subtitles(timestamps, FONT_PATH))

        # 临时音频：主音 + BGM 混音
        mixed_audio = os.path.join(tmp, "mixed.aac")
        if bgm_path and os.path.exists(bgm_path):
            subprocess.run([
                FFMPEG, "-y",
                "-i", audio_path,
                "-i", bgm_path,
                "-filter_complex",
                "[1:a]volume=0.4,aloop=loop=-1:size=2e+09[bgm];[0:a][bgm]amix=inputs=2:duration=first",
                "-c:a", "aac", "-b:a", "128k",
                mixed_audio,
            ], check=True, capture_output=True)
        else:
            mixed_audio = audio_path

        # 视频合成动态反同质化滤镜
        hflip = "hflip," if random.random() > 0.5 else ""
        brightness = random.uniform(-0.05, 0.05)
        saturation = random.uniform(0.9, 1.1)
        eq_filter = f"eq=brightness={brightness:.2f}:saturation={saturation:.2f},"

        is_video = bg.lower().endswith(".mp4")
        if is_video:
            video_input = ["-stream_loop", "-1", "-i", bg]
            vf = (
                f"{hflip}{eq_filter}"
                "scale=1080:1920:force_original_aspect_ratio=increase,"
                "crop=1080:1920,"
                "setsar=1,"
                f"ass={ass_path}"
            )
        else:
            video_input = ["-loop", "1", "-i", bg]
            vf = (
                f"{hflip}{eq_filter}"
                "scale=1080:1920:force_original_aspect_ratio=increase,"
                "crop=1080:1920,"
                "zoompan=z='min(zoom+0.0008,1.3)':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1080x1920:fps=24,"
                f"ass={ass_path}"
            )

        cmd = [
            FFMPEG, "-y",
            *video_input,
            "-i", mixed_audio,
            "-vf", vf,
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2",  # 视频号标准：立体声 44.1kHz，防部分播放器/平台无声
            "-shortest",
            "-movflags", "+faststart",
            output_path,
        ]
        logger.info(f"FFmpeg 合成中：{output_path}")
        subprocess.run(cmd, check=True, capture_output=True)
        logger.info(f"视频合成完成：{output_path}")


def _download_fallback_bg() -> str:
    path = os.path.join(ASSETS_DIR, "auto_bg.jpg")
    if not os.path.exists(path):
        import urllib.request
        url = "https://images.unsplash.com/photo-1499856871958-5b9627545d1a?q=80&w=1080&h=1920&fit=crop"
        try:
            urllib.request.urlretrieve(url, path)
        except Exception as e:
            logger.error(f"兜底背景下载失败：{e}")
    return path


# ============================================================
# A/B 三版封面生成（FFmpeg 文字叠加）
# ============================================================
def generate_cover(
    bg_source: str,
    title_text: str,
    series_title: str,
    output_path: str,
):
    """单版封面：FFmpeg drawtext 实现"""
    title_line1, title_line2 = (title_text.split("\n") + [""])[:2]
    is_video = bg_source.lower().endswith(".mp4")
    input_args = ["-i", bg_source] if is_video else ["-loop", "1", "-t", "1", "-i", bg_source]

    drawtext = (
        f"drawtext=fontfile='{FONT_FILE}':fontsize=110:fontcolor=gold:bordercolor=black:borderw=5:"
        f"text='{title_line1}':x=(w-text_w)/2:y=h*0.28,"
        f"drawtext=fontfile='{FONT_FILE}':fontsize=110:fontcolor=gold:bordercolor=black:borderw=5:"
        f"text='{title_line2}':x=(w-text_w)/2:y=h*0.28+130,"
        f"drawtext=fontfile='{FONT_FILE}':fontsize=55:fontcolor=white:bordercolor=black:borderw=3:"
        f"text='{series_title}':x=(w-text_w)/2:y=h*0.18,"
        "colormatrix=bt601:bt709"  # 暗角效果
    )

    cmd = [
        FFMPEG, "-y",
        *input_args,
        "-vf",
        f"scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
        f"colorlevels=rimin=0:rimax=0.9:gimin=0:gimax=0.9:bimin=0:bimax=0.9,"
        f"{drawtext}",
        "-frames:v", "1",
        output_path,
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        logger.info(f"封面生成：{output_path}")
    except subprocess.CalledProcessError as e:
        logger.error(f"封面生成失败：{e.stderr.decode()}")


def generate_ab_covers(
    bg_source: str,
    script_data: dict,
    series_title: str,
    output_dir: str,
) -> list[str]:
    """生成 A/B/C 三版封面供对比测试"""
    covers = []
    for variant in ["a", "b", "c"]:
        title = script_data.get(f"cover_{variant}", script_data.get("cover_a", ""))
        if not title:
            continue
        out = os.path.join(output_dir, f"cover_{variant}.jpg")
        generate_cover(bg_source, title, series_title, out)
        covers.append(out)
    logger.info(f"A/B/C 三版封面生成完成：{covers}")
    return covers


# ============================================================
# 发布助手：把所有钩子聚合成一份手动发布清单
# ============================================================
def _write_publish_guide(output_dir: str, script_data: dict, series_title: str):
    """把口播、封面、互动/追更/转发钩子、小号带头评论聚合成 publish_guide.txt。

    手动发布时照这份"剧本"操作：发什么文案、评论区先用小号发哪条、怎么引导追更。
    钩子由 LLM 按内容动态挑选（可能为空），这里只忠实呈现本条实际生成了什么。
    """
    stage = _monetize_stage()
    stage_name = {0: "起号期（纯情感·不引流）", 1: "起量期（评论区软引流）", 2: "变现期（明确承接）"}.get(stage, "起号期")

    interaction = (script_data.get("interaction_hook") or "").strip()
    follow      = (script_data.get("follow_hook") or "").strip()
    comment     = (script_data.get("comment_hook") or "").strip()
    hooks_used  = (script_data.get("hooks_used") or "未标注").strip()

    lines = [
        f"# 发布助手  |  {series_title}  |  阶段：{stage_name}",
        "=" * 50,
        "",
        "【1. 口播正文 / 视频文案】",
        script_data.get("viral_text", ""),
        "",
        "【2. 本条实际采用的钩子】（核对密度，避免每条都雷同）",
        f"  hooks_used：{hooks_used}",
        "",
        "【3. 评论区第一步——用小号置顶这条『带头评论』】",
        f"  {comment if comment else '（本条无，纯情感片可不带）'}",
        "  ↑ 银发用户需要被示范怎么接话，发完手动置顶，并主动回复前几条真实评论。",
        "",
        "【4. 互动引导】（若口播已含则无需重复）",
        f"  {interaction if interaction else '（本条未设互动钩子）'}",
        "",
        "【5. 追更引导】（给『不关注会错过』的理由）",
        f"  {follow if follow else '（本条未设追更钩子）'}",
        "",
        "【6. 封面三版 A/B 测试】",
        f"  A：{script_data.get('cover_a','')}".replace("\n", " "),
        f"  B：{script_data.get('cover_b','')}".replace("\n", " "),
        f"  C：{script_data.get('cover_c','')}".replace("\n", " "),
    ]
    if stage == 0 and (interaction or follow or comment):
        lines += ["", "※ 起号期提醒：口播与评论区均不得出现主页/小程序/教程等导流词，只做情感互动。"]

    guide_path = os.path.join(output_dir, "publish_guide.txt")
    with open(guide_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    logger.info(f"发布助手已保存：{guide_path}（hooks={hooks_used}）")


# ============================================================
# 主流程
# ============================================================
async def main(account: dict = None):
    script_data = generate_viral_script(account=account)
    if not script_data:
        logger.error("脚本生成失败，退出")
        return

    story_id    = script_data.get("story_id")
    theme       = script_data["theme"]
    scene       = script_data.get("scene", "general")
    viral_text  = script_data["viral_text"]
    series_title = script_data["series_title"]
    persona     = script_data.get("persona", "退休老人")
    comment_hook = script_data.get("comment_hook", "")

    logger.info(f"\n{'='*50}")
    logger.info(f"系列：{series_title}  人设：{persona}  主题：{theme}")
    if "learning_analysis" in script_data:
        logger.info(f"【大模型复盘反思】\n{script_data['learning_analysis']}\n")
    logger.info(f"脚本：\n{viral_text}")
    logger.info(f"封面A：{script_data.get('cover_a', '')}  B：{script_data.get('cover_b', '')}  C：{script_data.get('cover_c', '')}")
    logger.info(f"钩子密度：{script_data.get('hooks_used', '未标注')}")
    logger.info(f"互动钩子：{script_data.get('interaction_hook', '') or '（无）'}")
    logger.info(f"追更钩子：{script_data.get('follow_hook', '') or '（无）'}")
    logger.info(f"带头评论：{comment_hook}")
    logger.info(f"{'='*50}\n")

    # 路径准备：按日期和主题生成专属隔离包
    from datetime import datetime
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H%M%S")
    output_dir = os.path.join(OUTPUT_DIR, date_str, theme, f"{time_str}_{story_id or 'fallback'}")
    os.makedirs(output_dir, exist_ok=True)

    audio_path  = os.path.join(output_dir, "viral_voice.mp3")
    video_path  = os.path.join(output_dir, "viral_output.mp4")
    bgm_path    = _pick_bgm()

    # 保存文案剧本
    with open(os.path.join(output_dir, "script.json"), "w", encoding="utf-8") as f:
        json.dump(script_data, f, ensure_ascii=False, indent=2)

    # TTS
    gender = script_data.get("narrator_gender", "male")
    await generate_audio(viral_text, audio_path, gender)

    # 背景图：物件模式优先用真实老物件图(不调 AI，避免赝品)；photo 模式才走 AI 绘图
    image_prompt = script_data.get("image_prompt", "")
    bg_source = None
    if _content_mode() == "object":
        bg_source = _pick_background(theme, scene)
        if not bg_source:
            logger.warning(f"[物件模式] brolls/{theme}/{scene}/ 无真实素材，先跑 fetch-assets 下载；本条暂用兜底图")
    elif image_prompt:
        bg_source = generate_ai_background(image_prompt)
    if not bg_source:
        bg_source = _pick_background(theme, scene) or _download_fallback_bg()

    # 视频合成 (包含动态运镜)
    create_video(audio_path, viral_text, video_path, theme=theme, scene=scene, bgm_path=bgm_path, bg_source=bg_source)

    # A/B/C 封面
    generate_ab_covers(bg_source, script_data, series_title, output_dir)

    # 数据闭环记录
    if story_id:
        database.mark_story_used(story_id)
        prod_id = database.record_production(
            story_id=story_id,
            viral_script=viral_text,
            cover_title=script_data.get("cover_a", ""),
            persona=persona,
            cover_variant=json.dumps({
                "a": script_data.get("cover_a", ""),
                "b": script_data.get("cover_b", ""),
                "c": script_data.get("cover_c", ""),
            }),
        )
        logger.info(f"数据闭环记录完成，生产 ID：{prod_id}")

    # 发布助手：把口播/封面/各类钩子聚合成一份手动发布清单
    # （钩子生成了却不落盘 = 等于没做。这份文件是你手动发布时照着抄的"剧本"）
    _write_publish_guide(output_dir, script_data, series_title)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    # 多账号矩阵示例：为每个活跃账号各生成一条视频
    database.init_db()
    accounts = database.get_accounts(active_only=True)
    if accounts:
        for acc in accounts:
            logger.info(f"\n>>> 账号：{acc['account_id']}  人设：{acc['persona']}")
            asyncio.run(main(account=acc))
            database.increment_account_post(acc["account_id"])
            time.sleep(2)
    else:
        # 没有配置账号矩阵时，直接生成
        asyncio.run(main())
