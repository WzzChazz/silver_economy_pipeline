import os
from zhipuai import ZhipuAI

api_key = "6ccbd6a3e396427b994054f11085c40d.iVR5HXTtCJU76X33"
client = ZhipuAI(api_key=api_key)

text = "今天收拾旧衣柜，翻出一个老钱包。那是三十年前，他在厂里用的。"
out_dir = "/Users/mac/project/silver_economy_pipeline/output/voice_samples"

def generate(voice):
    try:
        response = client.audio.speech.create(
            model="chatglm-audio",
            input=text,
            voice=voice
        )
        with open(os.path.join(out_dir, f"9_zhipu_{voice}.mp3"), "wb") as f:
            f.write(response.content)
        print(f"Generated 9_zhipu_{voice}.mp3")
    except Exception as e:
        print(f"Failed Zhipu {voice}: {e}")

generate("zhiting")
generate("zhiren")
