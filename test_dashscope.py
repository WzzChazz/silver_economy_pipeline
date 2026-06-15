import dashscope
from dashscope.audio.tts_v2 import SpeechSynthesizer
import os

dashscope.api_key = "sk-0cebcb76fd6d45b293509bfd1790b6e1"

text = "今天收拾旧衣柜，翻出一个老钱包。那是三十年前，他在厂里用的。"
out_dir = "/Users/mac/project/silver_economy_pipeline/output/voice_samples"
os.makedirs(out_dir, exist_ok=True)

def generate_voice(vid, filename, instruct=None):
    try:
        model = "cosyvoice-v1"
        if "v3.5-plus" in vid:
            model = "cosyvoice-v3.5-plus"
            
        kwargs = {
            "model": model,
            "voice": vid,
            "speech_rate": 0.85
        }
        if instruct and "v3.5-plus" in model:
            kwargs["instruction"] = instruct
            
        synthesizer = SpeechSynthesizer(**kwargs)
        audio_data = synthesizer.call(text)
        with open(os.path.join(out_dir, filename), "wb") as f:
            f.write(audio_data)
        print(f"Generated {filename}")
    except Exception as e:
        print(f"Failed to generate {filename}: {e}")

generate_voice("longxiaochun", "4_dashscope_longxiaochun.mp3")
generate_voice("cosyvoice-v3.5-plus-bailian-13d24217b6514e42a85c8ad031c97be5", "5_dashscope_terrified.mp3")
