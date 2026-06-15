import asyncio
import os
import shutil
import logging
import torch
import torchaudio
import huggingface_hub
from src.engines.video_engine import create_video

logging.basicConfig(level=logging.INFO)

# --- F5-TTS Offline Monkey Patch ---
LOCAL_MODEL_DIR = "/Users/mac/Downloads/f5_models"

def mock_hf_download(repo_id, filename, **kwargs):
    base_name = os.path.basename(filename)
    if "model_1250000.safetensors" in base_name:
        base_name = "model_1200000.safetensors"
        
    local_path = os.path.join(LOCAL_MODEL_DIR, base_name)
    if not os.path.exists(local_path):
        raise FileNotFoundError(f"Missing {local_path} from {repo_id}")
    return local_path

huggingface_hub.hf_hub_download = mock_hf_download

from f5_tts.api import F5TTS
# ------------------------------------

async def main():
    out_dir = "/Users/mac/project/silver_economy_pipeline/output/custom_perfect_video"
    os.makedirs(out_dir, exist_ok=True)
    
    # 1. 复制背景图
    source_image = "/Users/mac/Downloads/ChatGPT Image 2026年6月15日 16_41_36.png"
    bg_image = os.path.join(out_dir, "perfect_bg.png")
    if not os.path.exists(bg_image) and os.path.exists(source_image):
        shutil.copy(source_image, bg_image)
    
    # 2. 原始音频参考文件（提取音色用） - Using the cached copy because original might be moved
    ref_audio = "/Users/mac/project/silver_economy_pipeline/output/custom_perfect_video/voice.mp3"
    ref_text = "哎呀，这件老物件多少年没见了，那时候我们还住在胡同里呢，每天早上都听见卖豆腐脑的吆喝声，日子苦是苦，可心里头热乎着。"
    
    # 3. 目标生成剧本
    gen_text = """今天收拾屋子
翻出了老伴十年前的旧大衣
口袋里还装着
当年给我买药的发票
人到了这个年纪啊
才彻底看透
儿女再孝顺
也代替不了那份朝夕相伴
老伴在
家就在
老伴在
心就安
认同的朋友
点个红心
祝天下老夫老妻
都能互相陪伴到老"""

    # 4. 生成克隆语音
    cloned_wav_path = os.path.join(out_dir, "cloned_voice.wav")
    
    print("Loading local F5-TTS...")
    f5tts = F5TTS()
    
    print("Cloning voice from reference...")
    wav, sr, spect = f5tts.infer(
        ref_file=ref_audio,
        ref_text=ref_text,
        gen_text=gen_text,
    )
    torchaudio.save(cloned_wav_path, torch.tensor(wav).unsqueeze(0), sr)
    print(f"Cloned audio saved to {cloned_wav_path}")
    
    # 5. 视频输出路径
    video_path = os.path.join(out_dir, "final_video_cloned_perfect.mp4")
    
    print("Aligning subtitles and Compiling final video...")
    await create_video(
        audio_path=cloned_wav_path,
        text=gen_text,
        bg_source=bg_image,
        output_path=video_path,
        bgm_path="/Users/mac/project/silver_economy_pipeline/assets/bgms/erhu.mp3"
    )
    print(f"Video saved to: {video_path}")

if __name__ == "__main__":
    asyncio.run(main())
