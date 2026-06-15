import asyncio
import edge_tts
import os

text = "今天收拾旧衣柜，翻出一个老钱包。那是三十年前，他在厂里用的。里面夹着一张纸条，写着：老婆爱吃鱼，我以后中午不买肉了，省点钱周末给她炖。看完我愣了好久。他从来不说爱，但每一口省下的饭，都是最笨的深情。年轻时嫌他木讷，现在才懂，那个年代的男人，爱都在骨头里。老伴啊，这辈子有你在，我从不缺温暖。你身边也有这样的人吗？转发给那个默默对你好的人，别让爱藏在心里。"
out_dir = "/Users/mac/project/silver_economy_pipeline/output/voice_samples"

async def generate(voice, rate, pitch, filename):
    communicate = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
    await communicate.save(os.path.join(out_dir, filename))
    print(f"Generated {filename}")

async def main():
    # 选项A：超级慢速、深情、略微低沉的晓晓（讲故事最常用调音）
    await generate("zh-CN-XiaoxiaoNeural", "-25%", "-8Hz", "A_xiaoxiao_storyteller.mp3")
    # 选项B：非常温婉、自带岁月静好感的台湾晓辰（放慢语速消除部分口音感）
    await generate("zh-TW-HsiaoChenNeural", "-20%", "-5Hz", "B_hsiaochen_nostalgic.mp3")
    
if __name__ == "__main__":
    asyncio.run(main())
