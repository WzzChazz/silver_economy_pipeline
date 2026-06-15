import os
import httpx
from dotenv import load_dotenv

load_dotenv()
api_key = os.getenv("SCRIPT_API_KEY")
url = "https://api.moonshot.cn/v1/chat/completions"
headers = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {api_key}"
}
payload = {
    "model": "moonshot-v1-8k",
    "messages": [{"role": "user", "content": "1"}],
    "max_tokens": 10
}

try:
    resp = httpx.post(url, json=payload, headers=headers, timeout=30.0)
    print("Status Code:", resp.status_code)
    print("Response:", resp.text)
except Exception as e:
    print("Error:", e)
