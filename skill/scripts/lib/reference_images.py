#!/usr/bin/env python3
from __future__ import annotations

import re
from typing import Iterable
from urllib.parse import urlparse


def _normalized_url(value: str) -> str:
    return str(value or '').strip()


def is_likely_product_image(url: str) -> bool:
    """检查是否是产品图片（更严格的过滤）"""
    lowered = str(url or '').strip().lower()
    if not lowered:
        return False
    parsed = urlparse(lowered)
    path = parsed.path or ''
    hostname = parsed.hostname or ''
    if not path:
        return False
    image_exts = ('.jpg', '.jpeg', '.png', '.webp', '.avif')
    if not path.endswith(image_exts):
        return False

    # 域名白名单：只允许已知产品图 CDN
    ALLOWED_DOMAINS = (
        'cbu01.alicdn.com',
        'img.alicdn.com',
        'gju3.alicdn.com',
        'gtms04.alicdn.com',  # 部分产品主图在这个域名
    )
    if not any(domain in hostname for domain in ALLOWED_DOMAINS):
        # 不是已知产品图 CDN → 拒绝（排除 cbu-pkgorigin-fx.1688.com 等）
        return False

    # 排除明显的非产品图片
    bad_tokens = (
        'logo', 'icon', 'sprite', 'avatar', 'banner', 'badge', 'svg',
        'placeholder', 'loading', 'spinner', 'arrow', 'button',
        'star', 'heart', 'thumb-up', 'thumb-down',
        'tps-', 'gw.alicdn.com/imgextra', 'img.alicdn.com/imgextra', 'gg_dtc',
        'rate.jpg', 'overseas_pic', '/gw/',
        '/tfs/', '200-200', '100-100', '80-80',
        'watermark', 'sample', 'desc/',
        # 评价区头像（已知的污染 URL）
        '22185873824_536529798',
        # 跨产品污染（readResourceImages 缓存）
        '6000000003538-0-cib',
        '671120191-0-cib',
        # 非主图 CDN
        'cbu-pkgorigin-fx.1688.com',
    )
    if any(token in lowered for token in bad_tokens):
        return False

    # 检查图片尺寸（URL 中可能有尺寸信息）
    # 格式1: _WxH.ext
    size_match = re.search(r'_(\d+)x(\d+)\.', lowered)
    if size_match:
        w, h = int(size_match.group(1)), int(size_match.group(2))
        if w < 200 or h < 200:
            return False
    # 格式2: WxH.ext or W-H.ext in path
    size_match2 = re.search(r'/(\d+)[x\-](\d+)\.', lowered)
    if size_match2:
        w, h = int(size_match2.group(1)), int(size_match2.group(2))
        if w < 200 or h < 200:
            return False

    return True


def dedupe_reference_images(urls: Iterable[str]) -> list[str]:
    """去重图片 URL"""
    seen: set[str] = set()
    ordered: list[str] = []
    for raw in urls:
        url = _normalized_url(raw)
        if not url:
            continue
        # 标准化 URL 用于去重
        normalized = url.split('?')[0].split('#')[0]
        if normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(url)
    return ordered


def reference_priority(url: str) -> tuple[int, int, str]:
    """图片优先级排序"""
    lowered = url.lower()
    # 白底图优先
    white_background_hint = 0 if any(token in lowered for token in ('white', '白底', 'cutout', 'isolated')) else 1
    # 1688 图片优先
    preferred_host = 0 if any(host in lowered for host in ('alicdn.com', '1688.com', 'taobaocdn.com')) else 1
    # 较长的 URL 通常质量更高
    length_score = -len(url)
    return (white_background_hint, preferred_host, length_score)


def select_reference_images(urls: Iterable[str], limit: int = 10) -> list[str]:
    """选择最佳参考图片"""
    deduped = dedupe_reference_images(urls)
    filtered = [url for url in deduped if is_likely_product_image(url)]
    # 如果过滤后没有图片，使用原始列表
    if not filtered:
        filtered = deduped
    ordered = sorted(filtered, key=reference_priority)
    return ordered[:limit]


def merge_followup_reference_images(white_background_url: str, source_urls: Iterable[str], limit: int = 4) -> list[str]:
    """合并白底图和源图片"""
    merged = [white_background_url] + list(source_urls)
    deduped = dedupe_reference_images(merged)
    if not deduped:
        return []
    primary = deduped[0]
    rest = select_reference_images(deduped[1:], limit=max(0, limit - 1))
    return [primary] + rest


def get_best_product_images(images: list[str], limit: int = 10) -> list[str]:
    """获取最佳产品图片（用于 n8n 管线）"""
    # 0. 清理 URL（修复 .jpg_.jpg 等问题）
    cleaned = []
    for img in images:
        if not img:
            continue
        # 修复 .jpg_.jpg 后缀
        if img.endswith('.jpg_.jpg'):
            img = img[:-5]  # 移除 _.jpg (5 chars)
        elif img.endswith('.jpg.jpg'):
            img = img[:-4]  # 移除 .jpg (4 chars)
        # 确保有 .jpg 后缀
        if not any(img.lower().endswith(ext) for ext in ('.jpg', '.jpeg', '.png', '.webp', '.avif')):
            img = img + '.jpg'
        cleaned.append(img)
    # 1. 去重
    deduped = dedupe_reference_images(cleaned)
    # 2. 过滤
    filtered = [img for img in deduped if is_likely_product_image(img)]
    # 3. 排序
    ordered = sorted(filtered, key=reference_priority)
    # 4. 返回指定数量
    return ordered[:limit]
