import requests
import os

api_key = "sk_cc246df0daed85b4bb5690f867efa64c8635f762e0946bdc"
voice_id = "IKne3meq5aSn9XLyUdCD" # from their .env
url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"

headers = {
    "Accept": "audio/mpeg",
    "Content-Type": "application/json",
    "xi-api-key": api_key
}

data = {
    "text": "今天收拾旧衣柜，翻出一个老钱包。那是三十年前，他在厂里用的。",
    "model_id": "eleven_multilingual_v2",
    "voice_settings": {
        "stability": 0.5,
        "similarity_boost": 0.75
    }
}

response = requests.post(url, json=data, headers=headers)
if response.status_code == 200:
    with open("/Users/mac/project/silver_economy_pipeline/output/voice_samples/6_elevenlabs.mp3", "wb") as f:
        f.write(response.content)
    print("Generated 6_elevenlabs.mp3")
else:
    print("Error:", response.text)
