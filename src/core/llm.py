"""
llm.py  —  统一 LLM 调用层（OpenAI 兼容协议）

合并了原先散落在 story_engine / video_engine 的两份 `_call_llm`：
  1. 指数退避重试
  2. DeepSeek（评分，便宜）↔ Script LLM（改写，强）双供应商，主供应商耗尽自动兜底
  3. 统一 JSON 解析（剥离 ```json 围栏）
"""

import json
import logging
import os
import time
from typing import Optional

import httpx
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)


def _provider_config(name: str) -> dict:
    """按供应商名取配置；name ∈ {deepseek, script}"""
    if name == "deepseek":
        return {
            "api_key":  os.getenv("DEEPSEEK_API_KEY", ""),
            "base_url": os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
            "model":    os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
        }
    return {
        "api_key":  os.getenv("SCRIPT_API_KEY", ""),
        "base_url": os.getenv("SCRIPT_BASE_URL", "https://api.openai.com/v1"),
        "model":    os.getenv("SCRIPT_MODEL", "gpt-4o-mini"),
    }


def _is_configured(cfg: dict) -> bool:
    return bool(cfg["api_key"]) and "your-" not in cfg["api_key"]


def call_llm(
    prompt: str,
    system: str = "You output only valid JSON.",
    temperature: float = 0.3,
    prefer: str = "deepseek",
    max_retries: int = 3,
) -> Optional[dict]:
    """调用 LLM 并返回解析后的 JSON dict；失败返回 None。

    prefer 指定首选供应商，未配置或重试耗尽时自动切到另一家。
    """
    primary = prefer
    fallback = "script" if prefer == "deepseek" else "deepseek"

    # 首选未配置则直接用兜底
    if not _is_configured(_provider_config(primary)):
        logger.warning(f"{primary} 未配置 API Key，切换到 {fallback}")
        primary, fallback = fallback, None

    result = _try_provider(primary, prompt, system, temperature, max_retries)
    if result is not None:
        return result

    if fallback and _is_configured(_provider_config(fallback)):
        logger.warning(f"{primary} 调用失败，切换兜底供应商 {fallback}")
        return _try_provider(fallback, prompt, system, temperature, max_retries=2)
    return None


def _try_provider(
    name: str,
    prompt: str,
    system: str,
    temperature: float,
    max_retries: int,
) -> Optional[dict]:
    cfg = _provider_config(name)
    url = f"{cfg['base_url']}/chat/completions"
    payload = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",   "content": prompt},
        ],
        "temperature": temperature,
    }
    headers = {
        "Content-Type":  "application/json",
        "Authorization": f"Bearer {cfg['api_key']}",
    }

    for attempt in range(max_retries):
        try:
            with httpx.Client(timeout=60) as client:
                resp = client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            content = content.replace("```json", "").replace("```", "").strip()
            return json.loads(content)
        except json.JSONDecodeError as e:
            logger.error(f"[{name}] JSON 解析失败：{e}")
            return None
        except Exception as e:
            wait = 2 ** attempt
            logger.warning(f"[{name}] 调用失败（{attempt+1}/{max_retries}）：{e}，{wait}s 后重试")
            time.sleep(wait)
    return None
