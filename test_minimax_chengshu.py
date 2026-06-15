import requests
import json

api_key = "sk-api-Wycc8cvhG1NOf1xzk_4npN3xudP996hYwVI7Nt22JxYOSICp8EpewoTFpDu_htGmWZjOq-5AHcxn4miJUIRl7646CZXISyjGbKZRHNIeRjT-OYMwiIxBsZg"
url = "https://api.minimax.chat/v1/t2a_v2"
headers = {
    "Authorization": f"Bearer {api_key}",
    "Content-Type": "application/json"
}

text = "今天收拾旧衣柜，翻出一个老钱包。那是三十年前，他在厂里用的。里面夹着一张纸条，写着：老婆爱吃鱼，我以后中午不买肉了，省点钱周末给她炖。"

def test_voice(voice_id):
    data = {
        "model": "speech-01-hd",
        "text": text,
        "stream": False,
        "voice_setting": {
            "voice_id": voice_id,
            "speed": 0.8,
            "vol": 1.0,
            "pitch": -2
        },
        "audio_setting": {
            "sample_rate": 32000,
            "bitrate": 128000,
            "format": "mp3"
        }
    }
    resp = requests.post(url, headers=headers, json=data)
    if resp.status_code == 200:
        resp_json = resp.json()
        if "data" in resp_json and "audio" in resp_json["data"]:
            import bytes
            audio_hex = resp_json["data"]["audio"]
            with open(f"/Users/mac/project/silver_economy_pipeline/output/voice_samples/minimax_{voice_id}.mp3", "wb") as f:
                f.write(bytes.fromhex(audio_hex))
            print(f"Generated minimax_{voice_id}.mp3")
        else:
            print("Failed:", resp_json)
    else:
        print("HTTP Error:", resp.text)

test_voice("female-chengshu")
test_voice("female-yujie")
test_voice("Bingjiao") # Minimax classic
