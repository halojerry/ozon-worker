"""
图片URL预处理工具

S3已移除 — 图片直接从 MXOU API 返回 URL，无需重新上传。
所有图片URL直接返回，由MXOU API处理。
"""
from typing import List, Optional
from collections import OrderedDict

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


# 跨平台货源扩展 v1（feat/cross-platform-sourcing 批1）：Referer 按图床域分派。
# pdd 图床有热链校验（无 Referer 可能 403），淘宝系 taobaocdn 同理；天猫/淘宝
# 主图床实为 img.alicdn.com——保持现状派 detail.1688.com（alicdn 不校验 Referer
# 指向，1688 行为逐字节不变、不重派）。无规则命中 → None（不加头，行为同今日）。
_REFERER_TAOBAO = "https://item.taobao.com/"
_REFERER_PDD = "https://mobile.yangkeduo.com/"


def _referer_for_url(url) -> Optional[str]:
    """按图床域分派防盗链 Referer；无规则命中 → None。"""
    if not isinstance(url, str):
        return None
    if _is_1688_url(url):
        return "https://detail.1688.com/"
    if "taobaocdn.com" in url:
        return _REFERER_TAOBAO
    if any(domain in url for domain in ["pddpic.com", "yangkeduo.com", "pinduoduo.com"]):
        return _REFERER_PDD
    return None


# S3 存储已移除 — 图片直接从 MXOU API 返回 URL，无需重新上传

# v0.76 T16(controller)：`_download_image` 已删除——全仓零生产调用方的死代码
# （T15 评审 N2），且是裸 requests.get（用户可控 URL 一旦误接即盲 SSRF）。
# Referer 分派唯一事实源 `_referer_for_url` 保留，活链消费方：
# utils/cos_uploader.salvage_original_images（E1 转存）与
# services/draft_image_mirror._mirror_one（草稿镜像）——两条链 v0.76 T14/T15
# 起均走 utils.secure_fetch.safe_fetch。


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
