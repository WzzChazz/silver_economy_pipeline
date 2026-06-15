import requests
import json
import os

api_key = "sk-api-Wycc8cvhG1NOf1xzk_4npN3xudP996hYwVI7Nt22JxYOSICp8EpewoTFpDu_htGmWZjOq-5AHcxn4miJUIRl7646CZXISyjGbKZRHNIeRjT-OYMwiIxBsZg"
url = "https://api.minimax.chat/v1/t2a_v2"
headers = {
    "Authorization": f"Bearer {api_key}",
    "Content-Type": "application/json"
}

text = "今天收拾旧衣柜，翻出一个老钱包。写着老婆爱吃鱼，我以后中午不买肉了。"

def generate_minimax(voice_id):
    data = {
        "model": "speech-01-turbo",
        "text": text,
        "stream": False,
        "voice_setting": {
            "voice_id": voice_id,
            "speed": 1.0,
            "vol": 1.0,
            "pitch": 0
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
            print(f"SUCCESS: {voice_id}")
        else:
            print(f"FAILED (JSON): {voice_id} - {resp_json}")
    else:
        print(f"FAILED (HTTP): {voice_id} - {response.text}")

if __name__ == "__main__":
    generate_minimax("Chinese (Mandarin)_Wise_Women")
    generate_minimax("female-shudao")
    generate_minimax("female-yueli")
    generate_minimax("female-zhixing")
