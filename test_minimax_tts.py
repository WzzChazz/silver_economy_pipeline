import requests
import os
import json

api_key = "sk-api-Wycc8cvhG1NOf1xzk_4npN3xudP996hYwVI7Nt22JxYOSICp8EpewoTFpDu_htGmWZjOq-5AHcxn4miJUIRl7646CZXISyjGbKZRHNIeRjT-OYMwiIxBsZg"
url = "https://api.minimax.chat/v1/t2a_v2"
headers = {
    "Authorization": f"Bearer {api_key}",
    "Content-Type": "application/json"
}

text = "今天收拾旧衣柜，翻出一个老钱包。那是三十年前，他在厂里用的。里面夹着一张纸条，写着：老婆爱吃鱼，我以后中午不买肉了，省点钱周末给她炖。"

def generate_minimax(voice_id, filename):
    data = {
        "model": "speech-01-turbo",
        "text": text,
        "stream": False,
        "voice_setting": {
            "voice_id": voice_id,
            "speed": 0.85,
            "vol": 1.0,
            "pitch": -1
        },
        "audio_setting": {
            "sample_rate": 32000,
            "bitrate": 128000,
            "format": "mp3"
        }
    }
    
    response = requests.post(url, headers=headers, json=data)
    if response.status_code == 200:
        resp_json = response.json()
        if "data" in resp_json and "audio" in resp_json["data"]:
            import bytes
            # response is json containing hex string in data.audio
            audio_hex = resp_json["data"]["audio"]
            with open(filename, "wb") as f:
                f.write(bytes.fromhex(audio_hex))
            print(f"Generated {filename}")
        else:
            print("Failed:", resp_json)
    else:
        print("HTTP Error:", response.text)

# Let's try some female voices. "female-zhixing" (intellectual), "female-shudao" (storyteller)
# Since I'm not sure if "speech-01-turbo" is the correct model name or the format, let me print the result
generate_minimax("female-shudao", "minimax_shudao.mp3")
generate_minimax("female-zhixing", "minimax_zhixing.mp3")
