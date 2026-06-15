import os
import torch
import torchaudio

# Set HF mirror for faster download in China
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"

from f5_tts.api import F5TTS

print("Initializing F5-TTS...")
f5tts = F5TTS()

ref_audio = "/Users/mac/Downloads/uts_TASK_1781517629798_11f810af.mp3"
ref_text = "哎呀，这件老物件多少年没见了，那时候我们还住在胡同里呢，每天早上都听见卖豆腐脑的吆喝声，日子苦是苦，可心里头热乎着。"
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

print("Starting Inference...")
wav, sr, spect = f5tts.infer(
    ref_file=ref_audio,
    ref_text=ref_text,
    gen_text=gen_text,
)

out_path = "/Users/mac/project/silver_economy_pipeline/output/custom_perfect_video/cloned_voice.wav"
torchaudio.save(out_path, torch.tensor(wav).unsqueeze(0), sr)
print(f"Cloned audio saved to {out_path}")
