import asyncio
import os
import sys

# Add project root to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Set voice model to a mature female voice before importing the engine
os.environ["TTS_VOICE_MODEL_FEMALE"] = "zh-CN-XiaoxiaoNeural"
os.environ["TTS_SPEECH_RATE"] = "+0%"
os.environ["TTS_PITCH"] = "+0Hz"

from src.engines.video_engine import generate_audio, create_video

async def main():
    text = """今天收拾旧衣柜
翻出一个老钱包
那是三十年前
他在厂里用的
里面夹着一张纸条
字都模糊了
写着老婆爱吃鱼
我以后中午不买肉了
省点钱周末给她炖
看完我愣了好久
他从来不说爱
但每一口省下的饭
都是最笨的深情
年轻时嫌他木讷
现在才懂
那个年代的男人
爱都在骨头里
老伴啊
这辈子有你在
我从不缺温暖
你身边也有这样的人吗
转发给那个默默对你好的人
别让爱藏在心里"""

    output_dir = "/Users/mr.wu/Project/silver_economy_pipeline/output/custom_user_video"
    os.makedirs(output_dir, exist_ok=True)
    
    audio_path = os.path.join(output_dir, "tts_voice.mp3")
    video_path = os.path.join(output_dir, "final_video.mp4")
    
    bg_source = "/Users/mr.wu/.gemini/antigravity-ide/brain/93749d5b-89e8-47ff-bb27-6388cc95c916/media__1781532262382.jpg"
    bgm_path = "/Users/mr.wu/Project/wechat_video_engine/assets/bgms/guzheng_erhu.mp3"
    
    print("Generating TTS...")
    await generate_audio(text, audio_path, gender="female")
    
    print("Creating Video...")
    create_video(
        audio_path=audio_path,
        text=text,
        output_path=video_path,
        theme="life",
        bgm_path=bgm_path,
        bg_source=bg_source
    )
    print(f"Video created successfully at: {video_path}")

if __name__ == "__main__":
    asyncio.run(main())
