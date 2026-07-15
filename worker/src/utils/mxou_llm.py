# mxou LLM Chat API 独立模块
# 用于避免平台模块缓存导致 call_mxou_chat_api 不可用
import logging
from typing import Dict, Any, Optional

import requests

logger = logging.getLogger(__name__)

MXOU_CHAT_API_URL = "https://api.mxou.cn/v1/chat/completions"


def call_mxou_chat_api(
    token: str,
    system_prompt: str,
    user_prompt: str,
    model: str = "deepseek-v4-flash",
    temperature: float = 0.0,
    max_tokens: int = 4096,
    timeout: int = 90
) -> Optional[str]:
    """
    调用 mxou LLM Chat API，返回响应文本。

    参数:
        token: mxou API 密钥（用户输入）
        system_prompt: 系统提示词
        user_prompt: 用户提示词
        model: 模型ID，默认 deepseek-v4-flash
        temperature: 温度，默认 0.0
        max_tokens: 最大输出 token 数
        timeout: 请求超时秒数

    返回:
        LLM 响应文本字符串；失败返回 None
    """
    if not token or not token.strip():
        logger.error("mxou chat API 调用失败: token 为空")
        return None

    headers: Dict[str, str] = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    payload: Dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": temperature,
        "max_tokens": max_tokens
    }

    try:
        response = requests.post(
            MXOU_CHAT_API_URL,
            headers=headers,
            json=payload,
            timeout=timeout
        )

        if response.status_code != 200:
            err_body: str = response.text[:500] if response.text else "no response body"
            logger.error(
                "mxou chat API 调用失败: HTTP %d, model=%s, body=%s",
                response.status_code, model, err_body
            )
            return None

        result: Any = response.json()
        if not isinstance(result, dict):
            logger.error("mxou chat API 响应非dict: %s", str(result)[:300])
            return None

        choices: list = result.get("choices", [])
        if not isinstance(choices, list) or len(choices) == 0:
            logger.error("mxou chat API 返回无choices: %s", str(result)[:300])
            return None

        first_choice: Any = choices[0]
        if not isinstance(first_choice, dict):
            logger.error("mxou chat API choices[0] 非dict: %s", str(first_choice)[:300])
            return None

        message: Any = first_choice.get("message", {})
        if not isinstance(message, dict):
            logger.error("mxou chat API message 非dict: %s", str(message)[:300])
            return None

        content: str = message.get("content", "")
        if not isinstance(content, str) or not content.strip():
            logger.warning("mxou chat API 返回空content (model=%s)", model)
            return ""

        logger.info("mxou chat API 成功: model=%s, content长度=%d", model, len(content))
        return content

    except requests.exceptions.Timeout:
        logger.error("mxou chat API 超时(timeout=%ds, model=%s)", timeout, model)
        return None
    except Exception as e:
        logger.error("mxou chat API 异常: %s (model=%s)", str(e), model)
        return None
