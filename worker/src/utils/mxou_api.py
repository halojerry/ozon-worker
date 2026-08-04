# mxou API 统一调用工具 v2
# 功能: 图片生成(异步+轮询) + LLM Chat(文本生成)
# 响应格式: {"id":"...", "status":"succeeded", "results":[{"url":"..."}], "progress":100}
import os
import time
import logging
import requests
from typing import List, Optional, Dict, Any

from utils.mxou_rate_limiter import mxou_acquire, handle_mxou_429

logger = logging.getLogger(__name__)

MXOU_IMAGE_API_URL = "https://api.mxou.cn/v1/images/generations"
MXOU_CHAT_API_URL = "https://api.mxou.cn/v1/chat/completions"

# grsai 进度查询 API
GRSAI_API_URL = "https://grsai.dakka.com.cn/v1/api/result"
GRSAI_API_KEY = os.getenv("GRSAI_API_KEY", "")

# 默认生图模型和降级模型
PRIMARY_IMAGE_MODEL = "gpt-image-2"
FALLBACK_IMAGE_MODEL = "nano-banana-fast"

# 内存优化：复用requests.Session，避免每次请求创建新TCP连接
_session: Optional[requests.Session] = None


def _get_session() -> requests.Session:
    """获取全局requests.Session单例"""
    global _session
    if _session is None:
        _session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=5,
            pool_maxsize=10,
            max_retries=0
        )
        _session.mount("https://", adapter)
        _session.mount("http://", adapter)
    return _session


# ============================================================
# LLM Chat API
# ============================================================

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
        "max_tokens": max_tokens,
        "thinking": {"type": "disabled"}  # 禁用 DeepSeek 推理，防止 reasoning tokens 吃掉 max_tokens
    }

    session = _get_session()

    # ⚠️ v0.14 B3: 全局限流器 — 按 token 滑动窗口控制 MXOU RPM，防并发打爆
    mxou_acquire(token)

    # ⚠️ v0.14 B2: 重试退避（旧代码 0 重试，API 故障时逐条调用级联浪费）
    # 规则: 4xx（除429）不重试；429 走指数退避；5xx/timeout/异常 退避重试 2 次
    max_attempts = 3  # 首次 + 2 次重试
    last_err = ""
    for attempt in range(max_attempts):
        try:
            response = session.post(
                MXOU_CHAT_API_URL,
                headers=headers,
                json=payload,
                timeout=timeout
            )

            status_code = response.status_code
            if status_code == 200:
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
                # ✅ reasoning_content fallback: DeepSeek 推理模型可能把输出放在 reasoning_content 而非 content
                if (not isinstance(content, str) or not content.strip()) and isinstance(message.get("reasoning_content"), str):
                    content = message["reasoning_content"].strip()
                    if content:
                        logger.info("mxou chat API 从 reasoning_content 回退成功 (model=%s), 长度=%d", model, len(content))
                if not isinstance(content, str) or not content.strip():
                    logger.warning("mxou chat API 返回空content (model=%s)", model)
                    return ""

                logger.info("mxou chat API 成功: model=%s, content长度=%d", model, len(content))
                return content

            err_body = response.text[:500] if response.text else "no response body"
            # 4xx 除 429 外不重试（请求/鉴权/参数错误，重试无意义）
            if 400 <= status_code < 500 and status_code != 429:
                logger.error(
                    "mxou chat API 调用失败: HTTP %d, model=%s, body=%s",
                    status_code, model, err_body
                )
                return None
            # 429 限流 → 指数退避
            if status_code == 429:
                logger.warning("mxou chat API 429 限流 (第%d次, model=%s)", attempt + 1, model)
                if handle_mxou_429(token, attempt, max_retries=2):
                    continue
                logger.error("mxou chat API 429 重试耗尽 (model=%s)", model)
                return None
            # 5xx → 退避重试
            last_err = f"HTTP {status_code}: {err_body}"
            if attempt < max_attempts - 1:
                wait = 2 ** attempt
                logger.warning(
                    "mxou chat API 5xx(第%d次, model=%s): %s, %.0fs 后重试...",
                    attempt + 1, model, last_err, wait
                )
                time.sleep(wait)
                continue
            logger.error("mxou chat API 调用失败: %s (model=%s)", last_err, model)
            return None

        except requests.exceptions.Timeout:
            last_err = f"timeout({timeout}s)"
            if attempt < max_attempts - 1:
                wait = 2 ** attempt
                logger.warning("mxou chat API 超时(第%d次, model=%s)，%.0fs 后重试...", attempt + 1, model, wait)
                time.sleep(wait)
                continue
            logger.error("mxou chat API 超时(timeout=%ds, model=%s)", timeout, model)
            return None
        except Exception as e:
            last_err = str(e)
            if attempt < max_attempts - 1:
                wait = 2 ** attempt
                logger.warning("mxou chat API 异常(第%d次, model=%s): %s，%.0fs 后重试...", attempt + 1, model, str(e), wait)
                time.sleep(wait)
                continue
            logger.error("mxou chat API 异常: %s (model=%s)", str(e), model)
            return None

    return None


# ============================================================
# 图片生成 API
# ============================================================

def call_mxou_image_api(
    token: str,
    prompt: str,
    ref_images: Optional[List[str]] = None,
    aspect_ratio: str = "3:4",
    timeout: int = 180,
    max_retries: int = 2,
    model: str = PRIMARY_IMAGE_MODEL
) -> Optional[str]:
    """
    调用mxou图片生成API，返回生成的图片URL。
    主模型(gpt-image-2)失败后自动降级到nano-banana-fast。

    参数:
        token: mxou API密钥（用户输入）
        prompt: 生图提示词
        ref_images: 参考图URL列表（可选，最多2张）
        aspect_ratio: 图片比例，默认"3:4"（对应1090x1443）
        timeout: 单次请求/轮询最大等待（秒），默认180（v0.19：主模型异步生图常超90s，
            90s 曾导致频繁误降级 nano-banana-fast）
        max_retries: 最大重试次数，默认2（共 3 次尝试；v0.19 由 3 收紧，避免超时级联重试）
        model: 生图模型，默认gpt-image-2

    返回:
        生成的图片URL字符串；失败返回None
    """
    t0 = time.time()
    # Step 1: 用主模型尝试
    result_url: Optional[str] = _call_image_with_model(
        token=token,
        prompt=prompt,
        ref_images=ref_images,
        aspect_ratio=aspect_ratio,
        timeout=timeout,
        max_retries=max_retries,
        model=model
    )

    if result_url:
        logger.info("生图成功 model=%s 耗时=%.1fs", model, time.time() - t0)
        return result_url

    # Step 2: 主模型失败，降级到 fallback 模型
    if model != FALLBACK_IMAGE_MODEL:
        logger.warning(
            "主模型 %s 生图失败（已重试，总耗时=%.1fs），降级到 %s 重试...",
            model, time.time() - t0, FALLBACK_IMAGE_MODEL
        )
        fallback_url: Optional[str] = _call_image_with_model(
            token=token,
            prompt=prompt,
            ref_images=ref_images,
            aspect_ratio=aspect_ratio,
            timeout=timeout,
            max_retries=1,  # 降级只重试1次
            model=FALLBACK_IMAGE_MODEL
        )
        if fallback_url:
            logger.warning("降级模型 %s 生图成功（总耗时=%.1fs）",
                           FALLBACK_IMAGE_MODEL, time.time() - t0)
            return fallback_url

        logger.error("主模型 %s 和降级模型 %s 均失败（总耗时=%.1fs）",
                     model, FALLBACK_IMAGE_MODEL, time.time() - t0)
    else:
        logger.error("模型 %s 生图失败（无降级）", model)

    return None


def _call_image_with_model(
    token: str,
    prompt: str,
    ref_images: Optional[List[str]],
    aspect_ratio: str,
    timeout: int,
    max_retries: int,
    model: str
) -> Optional[str]:
    """
    使用指定模型调用图片生成API（含重试和轮询）。
    """
    if not token or not token.strip():
        logger.error("mxou image API 调用失败: token 为空")
        return None

    headers: Dict[str, str] = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    safe_images: List[str] = ref_images if ref_images else []
    payload: Dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "images": safe_images,
        "aspectRatio": aspect_ratio,
        "replyType": "async"
    }

    session = _get_session()

    # ⚠️ v0.14 B3: 全局限流器 — 生图（慢操作+高成本）更需限流防并发打爆
    mxou_acquire(token)

    for attempt in range(max_retries + 1):
        try:
            response = session.post(
                MXOU_IMAGE_API_URL,
                headers=headers,
                json=payload,
                timeout=timeout
            )

            if response.status_code != 200:
                err_body = response.text[:300] if response.text else "no response"
                if attempt < max_retries:
                    logger.warning(
                        "mxou image API调用失败(第%d次, model=%s): HTTP %d, body=%s, 1秒后重试...",
                        attempt + 1, model, response.status_code, err_body
                    )
                    time.sleep(1)
                    continue
                logger.error(
                    "mxou image API调用失败(model=%s): HTTP %d, body=%s",
                    model, response.status_code, err_body
                )
                return None

            result: Any = response.json()
            if not isinstance(result, dict):
                logger.error("mxou image API响应非dict: %s", str(result)[:300])
                return None

            status: str = result.get("status", "")
            task_id: str = result.get("id", "")

            # 优先：status=succeeded 时直接从 results 提取 URL（同步返回）
            if status == "succeeded":
                url = _extract_url_from_results(result)
                if url:
                    logger.info("mxou同步返回图片(model=%s): %s", model, url[:80])
                    return url

            # 有 task_id 但未同步返回 → 轮询
            if isinstance(task_id, str) and task_id:
                logger.info("mxou任务(model=%s, status=%s)，开始轮询... task_id=%s", model, status, task_id)
                return _poll_grsai_task(task_id, max_wait=timeout, token=token)

            # 失败/违规
            error_msg: str = result.get("error", "unknown error")
            logger.error("mxou image API返回status=%s(model=%s), error=%s", status, model, error_msg)
            return None

        except requests.exceptions.Timeout:
            if attempt < max_retries:
                logger.warning("mxou image API超时(第%d次, model=%s)，1秒后重试...", attempt + 1, model)
                time.sleep(1)
                continue
            logger.error("mxou image API超时(timeout=%ds, model=%s)", timeout, model)
            return None
        except Exception as e:
            if attempt < max_retries:
                logger.warning("mxou image API异常(第%d次, model=%s): %s，1秒后重试...", attempt + 1, model, str(e))
                time.sleep(1)
                continue
            logger.error("mxou image API异常(model=%s): %s", model, str(e))
            return None

    return None


def _extract_url_from_results(result: dict) -> Optional[str]:
    """从API响应中提取图片URL"""
    results = result.get("results", [])
    if isinstance(results, list) and len(results) > 0:
        first_result = results[0]
        if isinstance(first_result, dict):
            url = first_result.get("url", "")
            if isinstance(url, str) and url:
                return url
    logger.error("mxou API返回succeeded但无有效results: %s", str(result)[:300])
    return None


# ============================================================
# grsai 进度查询（替换旧的 mxou 轮询）
# ============================================================

def _poll_grsai_task(task_id: str, max_wait: int = 90, token: str = "") -> Optional[str]:
    """
    通过 grsai API 轮询生图任务进度。

    参数:
        task_id: mxou 返回的任务ID
        max_wait: 最大等待秒数，默认 90

    返回:
        生成的图片 URL 字符串；失败/超时返回 None
    """
    headers: Dict[str, str] = {
        "Authorization": f"Bearer {GRSAI_API_KEY}",
        "Content-Type": "application/json"
    }

    # ✅ v0.25: 生图通常 >60s，前 30s 不轮询（减无效请求），之后每 5s 一次
    initial_delay: int = 30
    poll_interval: int = 5
    max_polls: int = max((max_wait - initial_delay) // poll_interval, 1)

    session = _get_session()

    time.sleep(initial_delay)
    for i in range(max_polls):
        time.sleep(poll_interval)
        try:
            response = session.get(
                GRSAI_API_URL,
                headers=headers,
                params={"id": task_id},
                timeout=30
            )

            if response.status_code != 200:
                logger.warning("grsai查询task=%s失败: HTTP %d", task_id, response.status_code)
                # grsai 失败时 fallback 到 mxou 轮询
                logger.info("grsai查询失败，fallback到mxou轮询...")
                return _poll_mxou_task_fallback(
                    task_id, max_wait=max_wait - initial_delay - (i + 1) * poll_interval, token=token
                )

            result: Any = response.json()
            if not isinstance(result, dict):
                logger.warning("grsai响应非dict: %s", str(result)[:300])
                continue

            status: str = result.get("status", "")
            progress: Any = result.get("progress", 0)
            logger.info("grsai轮询 %d/%d: task_id=%s, status=%s, progress=%s%%", i + 1, max_polls, task_id, status, progress)

            if status == "succeeded":
                url: Optional[str] = _extract_url_from_results(result)
                if url:
                    return url
                # grsai 返回 succeeded 但无 URL，尝试从 results 提取
                results: Any = result.get("results", [])
                if isinstance(results, list) and len(results) > 0:
                    first: Any = results[0]
                    if isinstance(first, dict):
                        url_val: str = first.get("url", "")
                        if isinstance(url_val, str) and url_val:
                            return url_val
                    elif isinstance(first, str):
                        return first
                logger.error("grsai返回succeeded但无URL: %s", str(result)[:300])
                return None

            if status in ("failed", "violation"):
                error_msg: str = result.get("error", "unknown error")
                logger.error("grsai任务%s: %s", status, error_msg)
                return None

        except Exception as e:
            logger.warning("grsai轮询异常: %s", str(e))

    logger.error("grsai轮询超时: task_id=%s (max_wait=%ds)", task_id, max_wait)
    return None


def _poll_mxou_task_fallback(task_id: str, max_wait: int = 90, token: str = "") -> Optional[str]:
    """
    grsai 不可用时的 fallback：直接轮询 mxou API。
    """
    if max_wait <= 0:
        max_wait = 30

    poll_interval: int = 3
    max_polls: int = max(max_wait // poll_interval, 1)

    session = _get_session()
    headers: Dict[str, str] = {"Authorization": f"Bearer {token}"} if token else {}

    for i in range(max_polls):
        time.sleep(poll_interval)
        try:
            response = session.get(
                f"{MXOU_IMAGE_API_URL}/{task_id}",
                headers=headers,
                timeout=30
            )
            if response.status_code != 200:
                logger.warning("mxou fallback轮询task=%s失败: HTTP %d", task_id, response.status_code)
                continue

            result: Any = response.json()
            if not isinstance(result, dict):
                continue

            status: str = result.get("status", "")
            progress: Any = result.get("progress", 0)
            logger.info("mxou fallback轮询 %d/%d: status=%s, progress=%s%%", i + 1, max_polls, status, progress)

            if status == "succeeded":
                return _extract_url_from_results(result)

            if status in ("failed", "violation"):
                error_msg: str = result.get("error", "unknown error")
                logger.error("mxou fallback任务%s: %s", status, error_msg)
                return None

        except Exception as e:
            logger.warning("mxou fallback轮询异常: %s", str(e))

    logger.error("mxou fallback轮询超时: task_id=%s (max_wait=%ds)", task_id, max_wait)
    return None


# ============================================================
# 标题清洗：生图 prompt 去平台/营销污染
# ============================================================

# 平台名/营销垃圾词（中英文），出现在生图标题中会污染 AI 生成结果
_IMAGE_PROMPT_JUNK_WORDS = [
    # ═══ A: 平台/市场名（直接污染视觉风格）═══
    "1688", "alibaba", "阿里巴巴",
    "aliexpress", "ali express", "速卖通",
    "taobao", "淘宝",
    "tmall", "天猫",
    "amazon", "亚马逊",
    "shopee", "lazada",
    "tiktok", "抖音",
    "temu", "shein", "wish", "ebay", "etsy",
    "jd", "jingdong", "京东",
    "拼多多", "pinduoduo",
    "walmart",
    "ozon", "озон", "wildberries",
    # ═══ B: 跨境/代发黑话 ═══
    "跨境", "跨境电商", "跨境爆款", "跨境现货", "跨境货源",
    "一件代发", "代发", "货源",
    "批发", "厂家直销", "直销", "工厂直供", "源头厂家",
    "dropshipping", "dropship",
    "cross border", "cross-border",
    "现货", "现货批发", "现货供应",
    # ═══ C: 营销吹嘘词 ═══
    "爆款", "热卖", "热销", "畅销",
    "新款", "新品", "同款",
    "促销", "限量", "秒杀", "清仓", "特价", "包邮",
    "好评", "五星", "推荐", "首选", "必备",
    "hot sale", "bestseller", "best seller", "new arrival",
    "trending", "popular", "top rated",
    "premium", "exclusive", "limited",
    "free shipping", "fast delivery", "in stock",
    "high quality", "factory price", "cheap",
    # ═══ D: 中文电商套话 ═══
    "厂家", "供应商", "生产厂家",
    "实力商家", "认证商家", "品牌授权",
    "支持定制", "来样定制",
    "OEM", "ODM",
    "免费拿样", "免费样品",
    "品质保证", "高质量", "优质", "高品质",
    "创意", "实用", "多功能",
    # ═══ E: 通用填充词 ═══
    "产品", "商品", "物品",
    "supply", "manufacturer", "factory", "direct",
    "wholesale", "agent", "distributor",
]

import re as _re
_IMG_JUNK_PATTERN = _re.compile(
    '|'.join(_re.escape(w) for w in _IMAGE_PROMPT_JUNK_WORDS),
    _re.IGNORECASE
)


def clean_title_for_image_prompt(title: str) -> str:
    """清洗产品标题，去除平台名/营销词，只保留产品描述。
    
    示例:
    "跨境爆款 现货 抖音同款1688亚马逊 Frog Plant Stand" 
    → "Frog Plant Stand"
    
    "Hot Sale 2024 New OEM Factory Price Garden Tools"
    → "Garden Tools"
    """
    if not title:
        return title
    cleaned = _IMG_JUNK_PATTERN.sub('', title)
    # 清理多余空格
    cleaned = _re.sub(r'\s+', ' ', cleaned).strip()
    # 去掉首尾标点
    cleaned = cleaned.strip(' ,.-;:!?，。、；：！？')
    return cleaned if cleaned else title  # 如果全部清空了，保留原标题
