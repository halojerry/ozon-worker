"""
图片URL预处理工具

S3已移除 — 图片直接从 MXOU API 返回 URL，无需重新上传。
所有图片URL直接返回，由MXOU API处理。
"""
import os
import logging
import requests
from typing import List, Optional
from collections import OrderedDict

logger = logging.getLogger(__name__)

# ✅ 内存优化：使用OrderedDict实现LRU缓存，最多100条，超过自动清理
_URL_CACHE_MAX_SIZE: int = 100
_url_cache: OrderedDict = OrderedDict()


def _is_1688_url(url: str) -> bool:
    """判断是否为1688/阿里CDN图片URL（需要Referer防盗链）"""
    if not isinstance(url, str):
        return False
    return any(domain in url for domain in [
        "alicdn.com",
        "1688.com",
        "cbu01.alicdn",
        "gw.alicdn",
    ])


# S3 存储已移除 — 图片直接从 MXOU API 返回 URL，无需重新上传


def _download_image(url: str, timeout: int = 30) -> Optional[bytes]:
    """下载图片，对1688 CDN自动添加Referer头"""
    try:
        headers = {}
        if _is_1688_url(url):
            headers["Referer"] = "https://detail.1688.com/"
            headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

        resp = requests.get(url, headers=headers, timeout=timeout)
        if resp.status_code == 200 and len(resp.content) > 0:
            return resp.content
        logger.warning(f"下载图片失败: url={url[:100]}, status={resp.status_code}")
        return None
    except Exception as e:
        logger.warning(f"下载图片异常: url={url[:100]}, error={e}")
        return None


def process_image_url(url: str) -> str:
    """
    预处理单个图片URL：
    - 如果是HTTP URL → 直接返回（MXOU可直接访问）
    - 如果是 data: URI → 直接返回

    注：S3已移除，1688图片直接传给MXOU处理。
    """
    if not isinstance(url, str) or not url:
        return url

    # data URI 直接返回
    if url.startswith("data:"):
        return url

    # 非HTTP URL 直接返回
    if not url.startswith("http://") and not url.startswith("https://"):
        return url

    # 所有URL直接返回（MXOU API可直接访问外部图片URL）
    # S3已移除，不再需要重新上传
    return url


def process_image_urls(urls: List[str]) -> List[str]:
    """
    批量预处理图片URL列表。
    返回处理后的URL列表（顺序与输入一致）。
    """
    if not isinstance(urls, list) or len(urls) == 0:
        return []

    result: List[str] = []
    for url in urls:
        processed = process_image_url(url)
        if processed:
            result.append(processed)
    return result


def clear_cache() -> None:
    """清空URL缓存（每个产品处理完成后调用）"""
    _url_cache.clear()
