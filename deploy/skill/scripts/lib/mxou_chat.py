"""最小 mxou Chat API 客户端（skill 侧，v0.25 S3）。仅依赖 requests。"""
from __future__ import annotations

import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)
MXOU_CHAT_API_URL = "https://api.mxou.cn/v1/chat/completions"


def call_chat(token: str, system_prompt: str, user_prompt: str,
              model: str = "deepseek-v4-flash", max_tokens: int = 4096) -> Optional[str]:
    if not token or not token.strip():
        logger.error("mxou chat 调用失败: token 为空")
        return None
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system_prompt},
                     {"role": "user", "content": user_prompt}],
        "temperature": 0.0,
        "max_tokens": max_tokens,
        "thinking": {"type": "disabled"},
    }
    try:
        resp = requests.post(
            MXOU_CHAT_API_URL,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=payload, timeout=90,
        )
        if resp.status_code == 200:
            data = resp.json()
            content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
            return content if isinstance(content, str) and content.strip() else None
        logger.error("mxou chat API 失败: HTTP %d, %s", resp.status_code, resp.text[:200])
    except Exception as e:
        logger.error("mxou chat 请求异常: %s", e)
    return None
