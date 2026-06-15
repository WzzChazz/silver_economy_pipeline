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
import httpx
from dotenv import load_dotenv

from src.core import database

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
for t in ["父母爱情", "老伴故事", "空巢", "养老现实"]:
    os.makedirs(os.path.join(BROLLS_DIR, t), exist_ok=True)

SERIES_NAMES = ["《晚年心语》", "《人生下半场》", "《退休以后》", "《老来伴》", "《岁月如歌》"]


# ============================================================
# LLM 调用（文案改写 + 评论钩子）
# ============================================================
def _call_llm(prompt: str, temperature: float = 0.85) -> Optional[dict]:
    cfg = {
        "api_key":  os.getenv("SCRIPT_API_KEY", ""),
        "base_url": os.getenv("SCRIPT_BASE_URL", "https://api.openai.com/v1"),
        "model":    os.getenv("SCRIPT_MODEL",    "gpt-4o-mini"),
    }
    url = f"{cfg['base_url']}/chat/completions"
    payload = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": "You are a helpful assistant that outputs valid JSON."},
            {"role": "user",   "content": prompt},
        ],
        "temperature": temperature,
    }
    headers = {
        "Content-Type":  "application/json",
        "Authorization": f"Bearer {cfg['api_key']}",
    }
    for attempt in range(3):
        try:
            with httpx.Client(timeout=60) as client:
                resp = client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            content = content.replace("```json", "").replace("```", "").strip()
            return json.loads(content)
        except Exception as e:
            wait = 5 * (2 ** attempt)
            logger.warning(f"LLM 调用失败（{attempt+1}/3）：{e}，{wait}s 后重试")
            time.sleep(wait)
    return None


# ============================================================
# 文案生成（五段式脚本 + A/B 封面 + 评论钩子）
# ============================================================
_SCRIPT_PROMPT = """
你是专注微信视频号银发市场（50-75岁）的顶级操盘手。
你的核心任务是：深入剖析历史高播放量视频的成功密码（如情绪共鸣、互动钩子、安全感诉求等），将这些底层逻辑提炼出来，应用到新的创作中，而不是生硬地照抄原句。

请根据“真实市场反馈规律”，将这段【真实高赞老人故事】改写为爆款短视频脚本：
"{story}"

人设 IP："{persona}"
主题：{theme}
当前日期：{current_date}

【防侵权与隐私洗稿要求】（非常重要）
必须对原故事的核心时间线、具体地名、真实姓名、人物具体职业等设定进行二次艺术加工和打乱，彻底规避洗稿和侵权风险。只保留最核心的情感情绪共鸣点。

【脚本写作与情绪控制】
1. 第一人称，"{persona}"视角，保持“温和、有阅历、说大白话的中老年叙述者”腔调。
2. 五段式：开头钩子→中间共鸣→痛点升华→总结→互动话术。
3. 【强制要求：具体场景锚点】开头钩子必须包含一个具体的物件、地点或时间（例如：“那天翻出年轻时的旧大衣”、“昨晚给儿子打了个电话，没说两句就挂了”），绝对不能是抽象说理（不能说“人到晚年才明白”）。
【禁忌警告】：绝对不能使用“去了趟医院才明白/看透”这种已被严重用烂、容易被平台限流的陈词滥调！
4. 【差异化叙事腔调】请根据当前故事内容，自然适配以下四种方向之一的腔调：
   - 父母爱情：要有年代感，多提老物件（如旧毛衣、老照片），语气充满怀念与温情。
   - 老伴故事：要有岁月沉淀感，多描写琐碎日常（如一碗热面、起夜倒水），语气平实但深情。
   - 空巢老人：要突出空间对比和孤独感（如空荡的客厅、变冷的饭菜），语气略带落寞但不失释然。
   - 养老现实：直击痛点，具体到看病排队、带孙子的疲惫，语气要有一种看透世故后的通透与自得。
5. 所有断句用 \\n（每行不超过 10 字，行数足够撑 40 秒）。

【封面钩子要求】
生成 3 个封面钩子版本（cover_a/b/c），各 8 字内，分两行用 \\n。必须从以下 8 种类型中选择 3 种不同的类型：
1.年龄倒计时型（如：65岁以后才明白） 2.反问触痛型（如：你有没有想过） 3.场景代入型（如：去了一趟医院）
4.对比冲突型（如：年轻时拼命，老了发现） 5.身份认同型（如：退休老人才懂） 6.结论前置型（如：最后悔的不是穷）
7.悬念留白型（如：那天没忍住哭了） 8.数字具体型（如：70岁的3个秘密）

【评论区置顶文案】
生成评论区置顶文案（comment_hook）：结合当前日期/节气/季节，动态生成互动话术（可引导留言、转发特定人群、保存等，避免单一的"留个健康"），第一人称，带情感共鸣，不超过 40 字。

【画面描述词】
生成一句画面描述词（image_prompt）：基于当前故事核心场景，提炼一句 20 字以内的高清画面描述词用于 AI 绘图。
注意：
1. 必须具有中国老人的面貌特征，符合中国年代感和生活气息。
2. 人物动作必须与剧情物件强相关（例如剧情提到账本，画面必须是老人在看手写的中文账本，而不是看其他地方）。
【重要】：根据你设定的 narrator_gender，如果是 male，画面描述词中必须明确写“中国老爷爷”；如果是 female，必须明确写“中国老奶奶”。
3. 若画面中出现文字物件（如病历本、日记、账本等），必须强调上面是“中文手写体”。
示例：一个满头白发的中国老奶奶，正在低头看着手里泛黄的中文手写账本，神情落寞，写实风格。

参考以下历史爆款文案风格（Few-shot 示例）：
{few_shot_examples}

输出严格 JSON：
{{
    "learning_analysis": "（内部复盘）深刻剖析高播放量样本成功的原因，并说明本次新剧本如何巧妙吸收其底层心理逻辑（例如：痛点是如何设计的？互动诱饵是怎么抛的？）",
    "narrator_gender": "male 或者 female（大模型根据故事的主人公视角自行决定，并在后续生成中保持一致）",
    "viral_text": "正文，\\n 断句",
    "image_prompt": "画面描述词",
    "cover_a": "类型X\\n封面",
    "cover_b": "类型Y\\n封面",
    "cover_c": "类型Z\\n封面",
    "comment_hook": "动态评论区置顶文案"
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

    logger.info(f"拉取故事 ID={story_id} score={story_record['score']} persona={chosen_persona}")

    from datetime import datetime
    current_date = datetime.now().strftime("%Y-%m-%d %A")

    top_scripts = database.get_top_performing_scripts(limit=3)
    few_shot_examples = ""
    if top_scripts:
        for i, s in enumerate(top_scripts):
            few_shot_examples += f"爆款示例{i+1}（高播放量）：\n{s['viral_script']}\n\n"
    else:
        few_shot_examples = "暂无历史数据，请自由发挥。"

    result = _call_llm(_SCRIPT_PROMPT.format(
        story=raw_story,
        persona=chosen_persona,
        theme=theme,
        current_date=current_date,
        few_shot_examples=few_shot_examples,
    ), temperature=0.85)

    if not result:
        logger.warning("文案生成失败，使用兜底文案")
        return _fallback_script(story_id=story_id, theme=theme)

    result.update({
        "story_id":     story_id,
        "theme":        theme,
        "scene":        story_record.get("scene", "general"),
        "persona":      chosen_persona,
        "series_title": random.choice(SERIES_NAMES),
    })
    return result


def _fallback_script(story_id=None, theme="health") -> dict:
    return {
        "story_id":    story_id,
        "theme":       theme,
        "scene":       "home",
        "persona":     "退休老人",
        "learning_analysis": "基于健康主题的兜底生成，以高血压案例提醒同龄人关注身体。",
        "narrator_gender": "male",
        "series_title": random.choice(SERIES_NAMES),
        "viral_text":  "今天收拾屋子\n翻出了老伴十年前的旧大衣\n口袋里还装着\n当年给我买药的发票\n人到了这个年纪啊\n才彻底看透\n儿女再孝顺\n也代替不了那份朝夕相伴\n老伴在\n家就在\n老伴在\n心就安\n认同的朋友\n点个红心\n祝天下老夫老妻\n都能互相陪伴到老",
        "image_prompt": "一位满头白发的中国老爷爷，正在用老式血压计给自己量血压，眉头微皱，神情专注，写实风格。",
        "cover_a":     "今天收拾屋子\n才彻底看透",
        "cover_b":     "人到晚年\n最大的依靠是谁",
        "cover_c":     "老伴在\n家就在",
        "comment_hook": "岁月不饶人，老伴才是陪你走到最后的人。认同的留个赞🙏",
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
def get_whisper_timestamps(audio_path: str, text: str) -> list[dict]:
    logger.info("Whisper small 字幕对齐中...")
    try:
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
        model = whisper.load_model("small")
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
    import subprocess
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
        import random
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
