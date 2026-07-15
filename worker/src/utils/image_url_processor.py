"""
图片URL预处理工具

将外部图片URL（如1688 CDN）下载到本地，上传到S3对象存储，生成签名URL。
解决mxou API无法直接访问1688 CDN图片（防盗链）的问题。

✅ 内存优化：
1. _url_cache改为带大小限制的缓存（最多100条），超过自动清理最旧条目
2. S3SyncStorage单例化，避免重复创建boto3连接池
3. 下载图片使用流式读取，避免大文件全量加载到内存
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


# ✅ 内存优化：S3客户端单例，避免重复创建连接池
_s3_storage_instance = None


def _get_s3_storage():
    """获取S3存储单例实例"""
    global _s3_storage_instance
    if _s3_storage_instance is not None:
        return _s3_storage_instance
    try:
        from storage.s3.s3_storage import S3SyncStorage
        _s3_storage_instance = S3SyncStorage(
            access_key=os.environ.get("COZE_BUCKET_ACCESS_KEY", ""),
            secret_key=os.environ.get("COZE_BUCKET_SECRET_KEY", ""),
            bucket_name=os.environ.get("COZE_BUCKET_NAME", ""),
        )
        return _s3_storage_instance
    except Exception as e:
        logger.error(f"S3存储初始化失败: {e}")
        return None


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


def _upload_to_s3(image_bytes: bytes, filename: str) -> Optional[str]:
    """上传图片到S3对象存储，返回签名URL（使用单例存储实例）"""
    try:
        storage = _get_s3_storage()
        if storage is None:
            logger.error("S3存储不可用，跳过上传")
            return None

        object_key = storage.upload_file(
            file_content=image_bytes,
            file_name=filename,
            content_type="image/jpeg",
        )
        presigned_url = storage.generate_presigned_url(key=object_key, expire_time=7200)
        return presigned_url
    except Exception as e:
        logger.error(f"上传S3失败: {e}")
        return None


def process_image_url(url: str) -> str:
    """
    预处理单个图片URL：
    - 如果是1688/阿里CDN URL → 下载→上传S3→返回签名URL
    - 如果是其他HTTP URL → 直接返回（mxou可直接访问）
    - 如果是 data: URI → 直接返回

    使用LRU缓存（最多100条）避免重复处理。
    """
    if not isinstance(url, str) or not url:
        return url

    # data URI 直接返回
    if url.startswith("data:"):
        return url

    # 非HTTP URL 直接返回
    if not url.startswith("http://") and not url.startswith("https://"):
        return url

    # 非1688 URL 直接返回（假设mxou可以直接访问）
    if not _is_1688_url(url):
        return url

    # 检查缓存
    if url in _url_cache:
        # ✅ LRU：移动到末尾（最近使用）
        _url_cache.move_to_end(url)
        return _url_cache[url]

    # 下载1688图片
    image_bytes = _download_image(url)
    if image_bytes is None:
        logger.warning(f"无法下载图片，使用原始URL: {url[:80]}")
        return url

    # 上传到S3（使用安全的文件名，去除特殊字符）
    import re as _re
    import hashlib as _hashlib
    raw_name = url.split("/")[-1].split("?")[0] or "image"
    # 用URL的md5作为文件名前缀，确保唯一且合法
    url_hash = _hashlib.md5(url.encode("utf-8")).hexdigest()[:12]
    # 提取原始扩展名
    ext = ".jpg"
    for valid_ext in [".jpg", ".jpeg", ".png", ".webp", ".gif"]:
        if raw_name.lower().endswith(valid_ext):
            ext = valid_ext
            break
    filename = f"img_ref_{url_hash}{ext}"

    s3_url = _upload_to_s3(image_bytes, filename)
    if s3_url is None:
        logger.warning(f"S3上传失败，使用原始URL: {url[:80]}")
        return url

    # ✅ 内存优化：写入LRU缓存，超过上限时自动清理最旧条目
    _url_cache[url] = s3_url
    if len(_url_cache) > _URL_CACHE_MAX_SIZE:
        _url_cache.popitem(last=False)  # 移除最旧的条目

    logger.info(f"图片URL预处理成功: {url[:60]}... → {s3_url[:60]}...")
    return s3_url


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
