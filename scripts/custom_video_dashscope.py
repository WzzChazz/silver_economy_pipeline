import os
import sys
import dashscope
from dashscope.audio.tts_v2 import VoiceEnrollmentService, SpeechSynthesizer
import subprocess

# Add project root to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.engines.video_engine import create_video
def main():
    import dashscope
    dashscope.api_key = "sk-0cebcb76fd6d45b293509bfd1790b6e1"
    
    gen_text = """今天收拾旧衣柜
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
    raw_audio_path = os.path.join(output_dir, "dashscope_cloned_voice.mp3")
    video_path = os.path.join(output_dir, "final_video_dashscope.mp4")
    
    bg_source = "/Users/mr.wu/.gemini/antigravity-ide/brain/93749d5b-89e8-47ff-bb27-6388cc95c916/media__1781532262382.jpg"
    bgm_path = "/Users/mr.wu/Project/wechat_video_engine/assets/bgms/guzheng_erhu.mp3"
    
    AUDIO_URL = "https://h.uguu.se/MCLEqDFC.wav"
    
    print("Creating custom voice via DashScope...")
    service = VoiceEnrollmentService()
    voice_id = service.create_voice(
        target_model="cosyvoice-v1",
        prefix="yueli3",
        url=AUDIO_URL
    )
    print(f"Generated Voice ID: {voice_id}")

    print("Synthesizing Speech...")
    synthesizer = SpeechSynthesizer(model="cosyvoice-v1", voice=voice_id)
    audio = synthesizer.call(gen_text)
    
    with open(raw_audio_path, "wb") as f:
        f.write(audio)
    
    print("Creating Video...")
    create_video(
        audio_path=raw_audio_path,
        text=gen_text,
        output_path=video_path,
        theme="life",
        bgm_path=bgm_path,
        bg_source=bg_source
    )
    print(f"Video created successfully at: {video_path}")

if __name__ == "__main__":
    main()
