import requests
import time
import jwt
import os

api_key = "6ccbd6a3e396427b994054f11085c40d.iVR5HXTtCJU76X33"

def generate_token(apikey: str, exp_seconds: int = 3600):
    try:
        id, secret = apikey.split(".")
    except Exception as e:
        raise Exception("invalid apikey", e)

    payload = {
        "api_key": id,
        "exp": int(round(time.time() * 1000)) + exp_seconds * 1000,
        "timestamp": int(round(time.time() * 1000)),
    }
    return jwt.encode(payload, secret, algorithm="HS256", headers={"alg": "HS256", "sign_type": "SIGN"})

token = generate_token(api_key)
url = "https://open.bigmodel.cn/api/paas/v4/audio/speech"
headers = {
    "Authorization": f"Bearer {token}",
    "Content-Type": "application/json"
}

text = "今天收拾旧衣柜，翻出一个老钱包。那是三十年前，他在厂里用的。里面夹着一张纸条，写着：老婆爱吃鱼，我以后中午不买肉了，省点钱周末给她炖。"

def test_zhipu(voice):
    data = {
        "model": "chatglm-audio",
        "input": text,
        "voice": voice
    }
    resp = requests.post(url, headers=headers, json=data)
    if resp.status_code == 200:
        with open(f"/Users/mac/project/silver_economy_pipeline/output/voice_samples/zhipu_{voice}.mp3", "wb") as f:
            f.write(resp.content)
        print(f"Generated zhipu_{voice}.mp3")
    else:
        print(f"Failed Zhipu {voice}:", resp.text)

test_zhipu("zhiren")
test_zhipu("zhiting")
