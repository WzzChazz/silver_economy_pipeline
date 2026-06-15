import asyncio
import edge_tts
import os

text = "今天收拾旧衣柜，翻出一个老钱包。那是三十年前，他在厂里用的。"
out_dir = "/Users/mac/project/silver_economy_pipeline/output/voice_samples"
os.makedirs(out_dir, exist_ok=True)

async def generate(voice, rate, pitch, filename):
    communicate = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
    await communicate.save(os.path.join(out_dir, filename))
    print(f"Generated {filename}")

async def main():
    await generate("zh-CN-XiaoxiaoNeural", "+0%", "+0Hz", "1_xiaoxiao_normal.mp3")
    await generate("zh-CN-XiaoxiaoNeural", "-10%", "-10Hz", "2_xiaoxiao_low.mp3")
    await generate("zh-CN-shaanxi-XiaoniNeural", "+0%", "+0Hz", "3_shaanxi_normal.mp3")
    
if __name__ == "__main__":
    asyncio.run(main())
