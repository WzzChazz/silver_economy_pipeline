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

import edge_tts
from dotenv import load_dotenv

from src.core import database
from src.core.llm import call_llm

load_dotenv()
logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ASSETS_DIR = os.path.join(BASE_DIR, os.getenv("ASSETS_DIR", "assets"))
OUTPUT_DIR = os.path.join(BASE_DIR, os.getenv("OUTPUT_DIR", "output"))
BROLLS_DIR = os.path.join(ASSETS_DIR, "brolls")
BGMS_DIR   = os.path.join(ASSETS_DIR, "bgms")
FONT_PATH  = os.getenv("FONT_PATH", "Arial-Unicode-MS")

for d in [ASSETS_DIR, OUTPUT_DIR, BROLLS_DIR, BGMS_DIR]:
    os.makedirs(d, exist_ok=True)
for t in ["父母爱情", "金婚岁月", "年代记忆", "儿女孝心"]:
    os.makedirs(os.path.join(BROLLS_DIR, t), exist_ok=True)

SERIES_NAMES = ["《老照片里的爱情》", "《爸妈年轻时》", "《时光纪念册》", "《老来伴》", "《岁月如歌》"]


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
你是专注微信视频号「中老年家庭情感」赛道的顶级操盘手。
核心目标只有一个：让这条视频能被中老年用户【转发到家族群/朋友圈】，并引导有需求的子女私信做纪念视频。
请提炼历史高播放样本的底层逻辑（情绪共鸣、转发动机、安全感），应用到新创作，而不是照抄原句。

请把这段【真实高赞素材】改写为一条 30~45 秒的爆款短视频口播脚本：
"{story}"

人设 IP："{persona}"（固定旁白，性别已定：{narrator_gender}，全程保持一致，不得更改）
主题方向：{theme}
当前日期：{current_date}

【防侵权洗稿】必须打乱原故事的时间线、地名、姓名、职业等设定做二次艺术加工，只保留核心情绪共鸣点。

【硬性长度要求——最重要】
1. 口播正文总字数严格控制在 110~150 字之间（约 30~45 秒），宁短勿长。视频太长完播率必死。
2. 用 \\n 断句，每行不超过 10 字，总行数 8~12 行。

【完播率命门：前 3 秒钩子】
开头第一句（前 2 行）必须是一个【具体的画面/物件/动作】瞬间抓人，且制造悬念或情绪冲击。
例：“翻出妈妈年轻时的照片”“爸结婚那天就拍过一张”。
绝对禁止抽象说理开头（不能用“人到晚年才明白”）。
【限流红线】绝对禁止两类内容：
1. 医院/看病/生病/卖惨/独居孤独/老伴去世/“去了趟医院才明白”——负向、易限流、无转发基因。
2. 死亡紧迫感/晦气措辞：“趁父母还在/趁还来得及/再不做就来不及/子欲养而亲不待”——会被举报、抑制转发。
孝心一律用【惊喜 / 陪伴 / 感恩 / 节日】正向框架表达（如“给爸妈一个惊喜”“他们看了特别开心”），而非“怕失去”。

【转发基因——决定能不能起量】
内容必须让中老年人“想转给家人”。优先走以下正向、温暖、有年代感的方向（按主题适配腔调）：
   - 父母爱情：年代感，老物件（旧照片、旧毛衣、搪瓷缸），怀念温情。
   - 金婚岁月：相守一生的细节（一碗热面、起夜倒水），平实深情。
   - 年代记忆：那个年代的集体回忆（粮票、老挂历、黑白婚纱照），唤起共鸣。
   - 儿女孝心：子女视角的愧疚与陪伴（常年在外、给爸妈做点什么），引发“该多陪陪父母”的共鸣。

【结尾：只放转发钩子，不要在口播里导流（很重要）】
viral_text 最后 1~2 行只写一句“转发动机”话术，引导【站内转发】——这是起量命脉且平台安全。
口播正文【绝对不要】出现“主页找我/私信我/加微信”等导流词（口播里喊导流=营销号，会限流且没人转）。
转发钩子按 narrator_gender 适配视角：
   - 老人视角（male/female 老人自述）：引导老人把内容递给子女，如“家里有老照片的，转给孩子看看”。
   - 子女视角（儿女孝心类）：引导转给家人，如“有同感的，转给你的兄弟姐妹”。

【封面钩子】生成 3 版（cover_a/b/c），各 8 字内、分两行用 \\n，从以下选 3 种不同类型：
1.年龄型 2.反问型 3.场景代入型 4.对比冲突型 5.身份认同型 6.结论前置型 7.悬念留白型 8.数字具体型。

【评论区置顶文案 comment_hook】（软引流下沉到这里，比口播安全，只有感兴趣的人看）
结合日期/季节/节日，第一人称带情感，先引导互动（“评论区说说你和爸妈的故事”），
再轻轻带一句软引流（如“好多人问怎么做的，整理在主页了”），不得用“扣1/加微信”，不超过 40 字。

【画面描述词 image_prompt】一句 20 字内高清画面描述词用于 AI 绘图：
- 必须是中国老人面貌、有年代感与生活气息；动作与剧情物件强相关。
- 按 narrator_gender：male 写“中国老爷爷”，female 写“中国老奶奶”。
- 若出现文字物件（旧信、账本）须注明“中文手写体”。
示例：一位满头白发的中国老奶奶，捧着泛黄的黑白结婚照，神情温柔，写实风格。

参考历史爆款风格（Few-shot）：
{few_shot_examples}

输出严格 JSON（不含多余文字）：
{{
    "learning_analysis": "（内部复盘）本条如何设计转发动机和前3秒钩子",
    "viral_text": "正文 110~150字，\\n 断句，结尾只含转发钩子（不要导流词）",
    "image_prompt": "画面描述词",
    "cover_a": "类型X\\n封面",
    "cover_b": "类型Y\\n封面",
    "cover_c": "类型Z\\n封面",
    "comment_hook": "评论区置顶文案"
}}
"""

def generate_viral_script(account: dict = None) -> Optional[dict]:
    """从故事库拉取故事并改写为视频脚本"""
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

    result = call_llm(_SCRIPT_PROMPT.format(
        story=raw_story,
        persona=chosen_persona,
        narrator_gender=identity["narrator_gender"],
        theme=theme,
        current_date=current_date,
        few_shot_examples=few_shot_examples,
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
        "learning_analysis": "兜底：老照片+父母爱情，靠年代感共鸣驱动转发，结尾软引流私域代做。",
        "narrator_gender": identity["narrator_gender"],
        "series_title": series_title,
        "viral_text":  "翻出爸妈年轻时\n那张黑白照片\n那个年代没有婚纱\n一件的确良衬衫\n就是最好的体面\n他们没说过爱\n却把一辈子\n过成了情话\n这样的老照片\n配上音乐\n爸妈看了\n准乐开花\n家里有老照片的\n转给孩子看看",
        "image_prompt": "一对中国老夫妻捧着泛黄的黑白结婚照，神情温柔怀念，年代感，写实风格。",
        "cover_a":     "爸妈年轻时\n有多好看",
        "cover_b":     "那个年代\n的爱情",
        "cover_c":     "黑白照片\n藏着情话",
        "comment_hook": "你还留着爸妈年轻时的照片吗？评论区聊聊，想做同款的主页有教程❤️",
    }


# ============================================================
# TTS 生成
async def generate_audio(text: str, output_path: str, gender: str = "male"):
    import re
    # 移除大模型生成的 SSML 标签，防止破坏 edge-tts 底层 XML 结构导致 NoAudioReceived
    clean_text = re.sub(r'<[^>]+>', '', text)
    if gender == "female":
        voice = os.getenv("TTS_VOICE_MODEL_FEMALE", "zh-CN-liaoning-XiaobeiNeural")
        rate  = os.getenv("TTS_SPEECH_RATE", "-15%")
        pitch = os.getenv("TTS_PITCH", "-15Hz")
    else:
        voice = os.getenv("TTS_VOICE_MODEL", "zh-CN-YunjianNeural")
        rate  = os.getenv("TTS_SPEECH_RATE", "-10%")
        pitch = os.getenv("TTS_PITCH", "-10Hz")
    logger.info(f"TTS 生成中 (已清理 SSML)：voice={voice}, gender={gender}")
    communicate = edge_tts.Communicate(clean_text, voice, rate=rate, pitch=pitch)
    await communicate.save(output_path)
    logger.info(f"TTS 完成：{output_path}")


# ============================================================
# Whisper 字幕时间戳（升级 small 模型）
# ============================================================
_whisper_model = None  # 模型缓存：批量生产时只加载一次


def _get_whisper_model():
    """懒加载并缓存 Whisper small 模型，避免每条视频重复加载。"""
    global _whisper_model
    if _whisper_model is None:
        import whisper
        import imageio_ffmpeg
        import numpy as np

        def _load_audio(file, sr=16000):
            cmd = [
                imageio_ffmpeg.get_ffmpeg_exe(), "-nostdin", "-threads", "0",
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
    """Whisper 降级：均匀分配时间戳"""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
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
                "ffmpeg", "-y",
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
            "ffmpeg", "-y",
            *video_input,
            "-i", mixed_audio,
            "-vf", vf,
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
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
        f"drawtext=fontfile={FONT_PATH}:fontsize=110:fontcolor=gold:bordercolor=black:borderw=5:"
        f"text='{title_line1}':x=(w-text_w)/2:y=h*0.28,"
        f"drawtext=fontfile={FONT_PATH}:fontsize=110:fontcolor=gold:bordercolor=black:borderw=5:"
        f"text='{title_line2}':x=(w-text_w)/2:y=h*0.28+130,"
        f"drawtext=fontfile={FONT_PATH}:fontsize=55:fontcolor=white:bordercolor=black:borderw=3:"
        f"text='{series_title}':x=(w-text_w)/2:y=h*0.18,"
        "colormatrix=bt601:bt709"  # 暗角效果
    )

    cmd = [
        "ffmpeg", "-y",
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
    logger.info(f"评论钩子：{comment_hook}")
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

    # AI 背景图生成
    image_prompt = script_data.get("image_prompt", "")
    bg_source = None
    if image_prompt:
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

    # 输出评论钩子（供手动复制）
    if comment_hook:
        hook_path = os.path.join(output_dir, "comment_hook.txt")
        with open(hook_path, "w", encoding="utf-8") as f:
            f.write(comment_hook)
        logger.info(f"评论钩子已保存：{hook_path}")


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
