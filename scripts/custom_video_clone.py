import os
import sys
import torch
import torchaudio
import torchaudio.functional as F

os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
from f5_tts.api import F5TTS

def main():
    ref_audio = "/Users/mr.wu/Downloads/uts_TASK_1781517629798_11f810af.mp3"
    ref_text = "哎呀,这件老物件多少年没见了,那时候我们还住在胡同里呢,每天早上都听见卖豆腐脑的腰和声,日子苦是苦,可心里头热乎着,大它就没有乎着。"
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
    audio_path = os.path.join(output_dir, "cloned_voice_pitched.wav")
    
    print("Initializing F5-TTS...")
    f5tts = F5TTS()
    
    print("Starting Inference...")
    wav, sr, spect = f5tts.infer(
        ref_file=ref_audio,
        ref_text=ref_text,
        gen_text=gen_text,
    )
    
    print("Adjusting Pitch...")
    waveform = torch.tensor(wav).unsqueeze(0)
    waveform_shifted = F.pitch_shift(waveform, sr, n_steps=2.5)
    
    torchaudio.save(audio_path, waveform_shifted, sr)
    print(f"Cloned and pitch-shifted audio saved to {audio_path}")

if __name__ == "__main__":
    main()
