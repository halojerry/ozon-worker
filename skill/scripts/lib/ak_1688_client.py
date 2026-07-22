#!/usr/bin/env python3
"""Lightweight 1688 AK client — search + product detail fetch."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import time
import uuid
from functools import wraps
from typing import Any, Optional
from urllib.parse import parse_qs, quote, urlparse

import requests

from scripts.lib.config_store import load_config
from scripts._const import SKILL_ROOT

logger = logging.getLogger(__name__)

BASE_URL = "https://skills-gateway.1688.com"
AINEXT_BASE_URL = "https://ainext.1688.com"
FIND_PRODUCT_API = "/api/find_product/1.0.0"
WORKFLOW_API = "/1688claw/skill/workflow"
DEFAULT_TIMEOUT_SECONDS = 30
MAX_RETRIES = 3
RETRY_DELAY_BASE = 1
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


class ConfigError(Exception):
    """AK 未配置或格式无效"""
    pass


class ApiError(Exception):
    """API 请求失败"""
    pass


class AuthError(ApiError):
    """AK 无效 / 签名失败 / 未配置 (401)"""
    pass


class ParamError(ApiError):
    """请求参数不合法 (400)"""
    pass


class RateLimitError(ApiError):
    """请求被限流 (429)"""
    pass


class ServiceError(ApiError):
    """服务端异常 / 网络异常 (500)"""
    pass


class _RetriableHTTPError(Exception):
    """可重试的 HTTP 网关错误（500/502/503/504）"""
    def __init__(self, status_code: int):
        self.status_code = status_code
        super().__init__(f"HTTP {status_code}")


# 可重试的 HTTP 状态码
RETRIABLE_STATUS_CODES = {500, 502, 503, 504}


def _with_retry(max_retries: int = MAX_RETRIES):
    """装饰器：重试 ConnectionError / Timeout / 网关瞬态错误(500/502/503/504)"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except (requests.exceptions.ConnectionError,
                        requests.exceptions.Timeout) as e:
                    last_exc = e
                    delay = min(RETRY_DELAY_BASE * (2 ** attempt), 10)
                    logger.warning("网络异常(尝试%d/%d): %s, %ds后重试",
                                   attempt + 1, max_retries, e, delay)
                    if attempt < max_retries - 1:
                        time.sleep(delay)
                except _RetriableHTTPError as e:
                    last_exc = e
                    delay = min(RETRY_DELAY_BASE * (2 ** attempt), 10)
                    logger.warning("网关超时(尝试%d/%d): HTTP %d, %ds后重试",
                                   attempt + 1, max_retries, e.status_code, delay)
                    if attempt < max_retries - 1:
                        time.sleep(delay)
            raise ServiceError(f"网络异常，已重试{max_retries}次: {last_exc}")
        return wrapper
    return decorator


# ── Auth helpers ──


def _extract_ak_keys(raw_ak: str) -> tuple[str, str]:
    raw_ak = raw_ak.strip()
    if ":" in raw_ak:
        parts = raw_ak.split(":", 1)
        return parts[0].strip(), parts[1].strip()
    # Base64 encoded: decode, first 32 chars = secret, rest = access key id
    padded = raw_ak + "=" * (-len(raw_ak) % 4)
    try:
        decoded = base64.urlsafe_b64decode(padded).decode("utf-8")
        return decoded[32:], decoded[:32]
    except Exception as e:
        logger.debug('Base64 AK decode failed — falling back to plain format: %s', e)
    if len(raw_ak) > 32:
        return raw_ak[32:], raw_ak[:32]
    raise ConfigError("1688 AK 格式无效（需要 AK:Secret 或 64位密钥）")


def _content_md5(body: str) -> str:
    if not body:
        return ""
    return base64.b64encode(hashlib.md5(body.encode("utf-8")).digest()).decode("utf-8")


def _canonicalized_resource(uri: str) -> str:
    parsed = urlparse(uri)
    path = parsed.path or "/"
    if not parsed.query:
        return path
    params = parse_qs(parsed.query, keep_blank_values=True)
    parts: list[str] = []
    for key in sorted(params.keys()):
        for value in sorted(params[key]):
            parts.append(f"{quote(key, safe='')}={quote(value, safe='')}")
    return f"{path}?{'&'.join(parts)}"


def get_ak_from_file() -> Optional[str]:
    """从本地文件读取 AK（支持官方 SDK 格式）"""
    from pathlib import Path
    
    # 尝试从多个位置读取 AK
    ak_paths = [
        # 当前目录下的 .1688-AK
        Path(".1688-AK/.ak_store.json"),
        # workspace 目录
        Path("workspace/.1688-AK/.ak_store.json"),
        # 用户主目录
        Path.home() / ".openclaw" / "workspace" / ".1688-AK" / ".ak_store.json",
        # 1688-sourcing-inquiry 目录
        SKILL_ROOT.parent / "1688-sourcing-inquiry-0.1.0/workspace/.1688-AK/.ak_store.json",
    ]
    
    for ak_path in ak_paths:
        if ak_path.exists():
            try:
                with open(ak_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    ak = data.get("ak")
                    if ak:
                        return ak
            except Exception:
                continue
    
    return None


def _signature_headers(method: str, path: str, body: str) -> dict[str, str]:
    # 优先从本地文件读取 AK（官方 SDK 格式）
    ak = get_ak_from_file()
    
    # 如果文件没有，尝试从环境变量读取
    if not ak:
        cfg = load_config()
        ak = cfg.get("ALI_1688_AK", os.environ.get("ALI_1688_AK", ""))
    
    if not ak:
        raise ConfigError("缺少 1688 AK")
    access_key_id, access_key_secret = _extract_ak_keys(ak)
    content_type = "application/json"
    timestamp = str(int(time.time()))
    nonce = uuid.uuid4().hex[:8]
    content_md5_val = _content_md5(body)
    sign_headers = {
        "x-csk-ak": access_key_id,
        "x-csk-time": timestamp,
        "x-csk-nonce": nonce,
        "x-csk-content-md5": content_md5_val,
        "x-csk-version": "1.0.0",
    }
    canonicalized_headers = "".join(
        f"{key.lower()}:{sign_headers[key].strip()}\n"
        for key in sorted(sign_headers.keys())
    )
    string_to_sign = (
        method.upper()
        + "\n" + content_md5_val
        + "\n" + content_type
        + "\n" + timestamp
        + "\n" + canonicalized_headers
        + _canonicalized_resource(path)
    )
    signature = base64.b64encode(
        hmac.new(
            access_key_secret.encode("utf-8"),
            string_to_sign.encode("utf-8"),
            hashlib.sha256,
        ).digest()
    ).decode("utf-8")
    return {
        "Content-Type": content_type,
        "x-csk-sign": signature,
        **sign_headers,
    }


@_with_retry()
def _post_1688(path: str, body: dict[str, Any], *, base_url: str = BASE_URL) -> dict[str, Any]:
    """
    POST 请求 1688 API（自动签名 + 重试 + 错误映射）
    
    Raises:
        AuthError / ParamError / RateLimitError / ServiceError / ApiError
    """
    url = f"{base_url}{path}"
    body_str = json.dumps(body, ensure_ascii=False)
    headers = {
        **DEFAULT_HEADERS,
        **_signature_headers("POST", path, body_str),
    }
    
    try:
        resp = requests.post(url, headers=headers, data=body_str.encode("utf-8"), timeout=DEFAULT_TIMEOUT_SECONDS)
        resp.raise_for_status()
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else None
        if status in RETRIABLE_STATUS_CODES:
            raise _RetriableHTTPError(status)
        if status == 401:
            raise AuthError("签名无效或已过期（401）")
        if status == 429:
            raise RateLimitError("请求被限流（429），请稍后重试")
        if status == 400:
            raise ParamError("请求参数不合法（400）")
        raise ServiceError(f"HTTP 错误 {status}") if status is not None else ServiceError("HTTP 错误 (unknown)")
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
        raise  # Let @_with_retry decorator handle these
    
    result = resp.json()
    
    # 检查业务错误
    if result.get("success") is False:
        msg_code = str(result.get("msgCode") or "")
        msg_info = result.get("msgInfo", "")
        
        # 标准 HTTP 状态码映射
        if "401" in msg_code:
            raise AuthError("签名无效（401）")
        if "429" in msg_code:
            raise RateLimitError("请求被限流（429）")
        if "400" in msg_code:
            raise ParamError("请求参数不合法（400）")
        if "500" in msg_code:
            raise ServiceError("服务异常（500），请稍后重试")
        
        detail = msg_info or msg_code or "未知业务错误"
        raise ApiError(f"1688 API 业务错误: {detail}")
    
    return result



# ── all_info parser ──


def _extract_markdown_section(all_info: str, heading: str) -> str:
    import re
    pattern = rf"#\s*{re.escape(heading)}\n(.*?)(?=\n#\s|\Z)"
    match = re.search(pattern, str(all_info or ""), flags=re.S)
    return match.group(1).strip() if match else ""


def _parse_markdown_kv_table(section_text: str) -> dict[str, list[str]]:
    parsed: dict[str, list[str]] = {}
    for raw_line in str(section_text or "").splitlines():
        line = raw_line.strip()
        if not line.startswith("|") or line.startswith("|--"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 2:
            continue
        key, value = cells[0], cells[1]
        if key in {"属性名", "类目级别"} or not key or not value:
            continue
        parsed.setdefault(key, []).append(value)
    return parsed


def _parse_category_table(section_text: str) -> list[dict[str, str]]:
    categories: list[dict[str, str]] = []
    for raw_line in str(section_text or "").splitlines():
        line = raw_line.strip()
        if not line.startswith("|") or line.startswith("|--"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 2:
            continue
        level, name = cells[0], cells[1]
        if level == "类目级别" or not level or not name:
            continue
        categories.append({"level": level, "name": name})
    return categories


def parse_offer_detail_info(all_info: str) -> dict[str, Any]:
    """Parse ainext offer_detail all_info text into structured data."""
    import re
    title_section = _extract_markdown_section(all_info, "商品标题")
    price_section = _extract_markdown_section(all_info, "商品价格")
    category_section = _extract_markdown_section(all_info, "商品类目")
    sku_section = _extract_markdown_section(all_info, "商品SKU属性")
    title = next((line.strip() for line in title_section.splitlines() if line.strip()), "")
    price_match = re.search(r"([0-9]+(?:\.[0-9]+)?)", price_section)
    return {
        "title": title,
        "price": float(price_match.group(1)) if price_match else None,
        "categories": _parse_category_table(category_section),
        "sku_attributes": _parse_markdown_kv_table(sku_section),
        "all_info": str(all_info or ""),
    }



# ── Public API ──


def _parse_product_item(item: dict[str, Any]) -> dict[str, Any]:
    """将 API 返回的单个商品条目映射为统一商品结构"""
    product_id = str(item.get("itemId", ""))
    detail_url = item.get("detailUrl") or (
        f"https://detail.1688.com/offer/{product_id}.html" if product_id else ""
    )
    return {
        "product_id": product_id,
        "title": item.get("title", ""),
        "image_url": item.get("imageUrl", ""),
        "detail_url": detail_url,
        "similarity_score": float(item.get("score", 0)),
        "price": item.get("currentPrice"),
        "sku_id": item.get("skuId", ""),
        "sku_title": item.get("skuTitle", ""),
        "yx_index": item.get("yxIndex"),
        "quantity_begin": item.get("quantityBegin"),
        "unit": item.get("unit", ""),
        "supplier": item.get("company", ""),
        "sold_count": item.get("soldOut", 0),
        "stock_amount": item.get("storeAmount", 0),
        "user_id": str(item.get("userId", "")),
        "member_id": item.get("memberId", ""),
        "category_id": item.get("cateId"),
        "promotion_tags": item.get("promotionTags", []),
        "service_infos": item.get("serviceInfos", []),
        "selling_points": item.get("sellingPoints", []),
    }


def search_products(
    query: str,
    *,
    page: int = 1,
    page_size: int = 20,
    sort_type: str = "",
    score_level: Optional[str] = None,
    purchase_amount: int = 1,
    tags: str = "",
    ic_tags: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Search 1688 products by keyword.

    Returns list of product dicts with unified structure.
    
    Args:
        query: 搜索关键词
        page: 页码
        page_size: 每页数量
        sort_type: 排序类型 (price_asc/price_desc/sold_desc/yx_desc)
        score_level: 相关性档位 (high/medium/low)
        purchase_amount: 采购件数
        tags: TC标（品池标签）
        ic_tags: IC标（品池标签）
    
    Raises:
        AuthError: AK 无效或未配置
        RateLimitError: 请求被限流
        ServiceError: 服务异常
    """
    body: dict[str, Any] = {
        "query": query,
        "pageNum": page,
        "pageSize": page_size,
        "purchaseAmount": purchase_amount,
    }
    if score_level:
        body["scoreLevel"] = score_level
    if tags:
        body["tags"] = tags
    if sort_type:
        body["sortType"] = sort_type
    if ic_tags:
        body["icTags"] = ic_tags
    
    result = _post_1688(FIND_PRODUCT_API, body)
    
    # 处理不同的响应结构
    data = result.get("data")
    if isinstance(data, dict):
        # 嵌套结构: {"data": {"data": [...]}}
        data = data.get("data", [])
    elif data is None:
        # 尝试 model 字段
        model = result.get("model") or {}
        data = model.get("data", [])
    
    if not isinstance(data, list):
        return []
    return [_parse_product_item(item) for item in data]


def search_by_image(
    image_path: str = "",
    image_url: str = "",
    *,
    page_size: int = 10,
    sort_type: str = "",
    score_level: str = "high",
    purchase_amount: int = 1,
    tags: str = "4306497",
) -> list[dict[str, Any]]:
    """1688 以图搜款 — 上传图片搜索同款/相似商品。

    Args:
        image_path: 本地图片路径
        image_url: 图片 URL（与 image_path 二选一）
        page_size: 返回数量
        sort_type: 排序 (price_asc/price_desc/sold_desc/yx_desc)
        score_level: 相关性 (high/medium/low)
        purchase_amount: 采购件数
        tags: 品池标签

    Returns:
        匹配商品列表，同 search_products() 的数据结构
    """
    import os as _os
    from scripts.lib.image_preprocessor import preprocess_image, image_to_base64

    img_base64 = ""
    img_url = ""
    converted_path = None
    download_tmp = None

    if image_url:
        # URL 图片先下载到本地，再 base64 上传（1688 API 无法直接访问外部 CDN）
        try:
            import requests as _req
            import tempfile as _tmp
            resp = _req.get(image_url, timeout=30, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            })
            resp.raise_for_status()
            download_tmp = _tmp.NamedTemporaryFile(suffix=".jpg", delete=False)
            download_tmp.write(resp.content)
            download_tmp.close()
            image_path = download_tmp.name
        except Exception as e:
            raise ValueError(f"下载图片失败: {e}")

    if image_path:
        img_info = preprocess_image(image_path)
        if img_info.get("type") == "local":
            img_base64 = image_to_base64(img_info["path"])
            if img_info.get("converted"):
                converted_path = img_info["path"]
        else:
            img_url = img_info.get("url", "")
    elif not image_url:
        raise ValueError("image_path 或 image_url 至少需要一个")

    body: dict[str, Any] = {
        "pageSize": page_size,
        "purchaseAmount": purchase_amount,
    }
    if img_base64:
        body["imgBase64"] = img_base64
    if img_url:
        body["imageUrl"] = img_url
    if score_level:
        body["scoreLevel"] = score_level
    if tags:
        body["tags"] = tags
    if sort_type:
        body["sortType"] = sort_type

    try:
        result = _post_1688(FIND_PRODUCT_API, body)
    finally:
        if converted_path:
            try:
                _os.unlink(converted_path)
            except OSError:
                pass
        if download_tmp:
            try:
                _os.unlink(download_tmp.name)
            except OSError:
                pass

    # 解析响应（同 search_products）
    data = result.get("data")
    if isinstance(data, dict):
        data = data.get("data", [])
    elif data is None:
        model = result.get("model") or {}
        data = model.get("data", [])

    if not isinstance(data, list):
        return []
    return [_parse_product_item(item) for item in data]


def _extract_images_from_raw(raw: dict[str, Any]) -> list[str]:
    """从 ainext API 原始响应中提取图片 URL"""
    images = []
    
    # 尝试从 different 字段提取图片
    for key in ["images", "imageList", "mainImages", "imageUrl"]:
        val = raw.get(key)
        if isinstance(val, list):
            for img in val:
                if isinstance(img, str) and img.startswith("http"):
                    images.append(img)
                elif isinstance(img, dict) and img.get("url"):
                    images.append(img["url"])
        elif isinstance(val, str) and val.startswith("http"):
            images.append(val)
    
    # 从 all_info 中提取图片 URL
    all_info = str(raw.get("all_info") or "")
    img_pattern = r'https?://[^\s\)\"\']+\.(?:jpg|jpeg|png|webp)'
    found_imgs = re.findall(img_pattern, all_info)
    for img in found_imgs:
        if img not in images:
            images.append(img)
    
    return images[:20]  # 最多返回 20 张图片


def _extract_weight_dimensions(raw: dict[str, Any]) -> dict[str, Any]:
    """从 ainext API 原始响应中提取重量和尺寸"""
    result = {"weight_grams": None, "dimensions_mm": None}
    
    # 尝试从 different 字段提取
    for key in ["weight", "weightGrams", "weight_grams"]:
        val = raw.get(key)
        if val:
            try:
                result["weight_grams"] = int(float(str(val).replace("g", "").replace("克", "").strip()))
            except (ValueError, TypeError) as e:
                logger.debug('weight parse failed for key=%s val=%s: %s', key, val, e)
    
    for key in ["dimensions", "size", "dimensionsMm"]:
        val = raw.get(key)
        if isinstance(val, str) and "x" in val.lower():
            parts = val.lower().replace("mm", "").split("x")
            if len(parts) == 3:
                try:
                    result["dimensions_mm"] = {
                        "length": int(float(parts[0].strip())),
                        "width": int(float(parts[1].strip())),
                        "height": int(float(parts[2].strip())),
                    }
                except (ValueError, TypeError) as e:
                    logger.debug('dimensions parse failed for val=%s: %s', val, e)
    
    return result


def get_product_details(item_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Fetch product details via ainext.1688.com offer_detail API.

    Returns dict mapping item_id -> {
        item_id, title, price, categories, sku_attributes, 
        all_info, raw, images, weight_grams, dimensions_mm
    }
    
    Raises:
        AuthError: AK 无效或未配置
        RateLimitError: 请求被限流
        ServiceError: 服务异常
    """
    if not item_ids:
        return {}
    
    result = _post_1688(
        WORKFLOW_API,
        {"code": "offer_detail", "bizParams": {"item_id": [str(i).strip() for i in item_ids]}},
        base_url=AINEXT_BASE_URL,
    )
    model = result.get("model") or {}
    biz_data = model.get("bizData") or {}
    if not isinstance(biz_data, dict):
        return {}
    details: dict[str, dict[str, Any]] = {}
    for item_id, item in biz_data.items():
        if not isinstance(item, dict):
            continue
        nid = str(item_id).strip()
        if not nid:
            continue
        all_info = str(item.get("all_info") or "")
        parsed = parse_offer_detail_info(all_info)
        
        # 从原始 API 响应中提取图片
        images = _extract_images_from_raw(item)
        
        # 如果 API 没有返回图片，尝试从搜索 API 获取
        if not images:
            try:
                search_results = search_products(parsed["title"][:30], page=1, page_size=5)
                for sp in search_results:
                    if sp.get("image_url") and str(sp.get("product_id", "")) == nid:
                        images = [sp["image_url"]]
                        break
                if not images and search_results:
                    images = [search_results[0].get("image_url", "")]
            except Exception as e:
                logger.debug("Image fallback search failed: %s", e)
        
        # 从原始 API 响应中提取重量和尺寸
        packaging = _extract_weight_dimensions(item)
        
        details[nid] = {
            "item_id": nid,
            "title": parsed["title"],
            "price": parsed["price"],
            "categories": parsed["categories"],
            "sku_attributes": parsed["sku_attributes"],
            "all_info": all_info,
            "raw": item,
            "images": [img for img in images if img],  # 过滤空值
            "weight_grams": packaging["weight_grams"],
            "dimensions_mm": packaging["dimensions_mm"],
        }
    return details


def parse_product_url(url: str) -> Optional[dict[str, str]]:
    """Parse 1688 product URL to extract offer ID."""
    value = str(url or "").strip()
    if not value:
        return None
    # Pure numeric ID
    if re.fullmatch(r"\d{6,18}", value):
        return {
            "platform": "1688",
            "product_id": value,
            "canonical_url": f"https://detail.1688.com/offer/{value}.html",
        }
    # URL parsing
    parsed = urlparse(value)
    path = parsed.path or ""
    m = re.search(r"/offer/(\d+)", path)
    if m:
        pid = m.group(1)
        return {
            "platform": "1688",
            "product_id": pid,
            "canonical_url": f"https://detail.1688.com/offer/{pid}.html",
        }
    # Query param
    for key in ("id", "offerId", "offer_id"):
        m = re.search(rf"{key}=(\d+)", parsed.query or "")
        if m:
            pid = m.group(1)
            return {"platform": "1688", "product_id": pid, "canonical_url": value}
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# Product enrichment — API + CDP merge (one call, all edge cases handled)
# ═══════════════════════════════════════════════════════════════════════════════


def enrich_product_with_cdp(
    detail_url: str,
    *,
    api_data: Optional[dict[str, Any]] = None,
    timeout_seconds: int = 30,
) -> dict[str, Any]:
    """Enrich a 1688 product with CDP browser data.  Single entry point.

    Call this after ``get_product_details()`` — it handles everything:
    Chrome check, login check, CDP probe, API+CDP merge, and graceful
    degradation.  **Never raises** — always returns a structured result.

    Returns::

        {
            'ok': bool,               # CDP probe completed successfully
            'degraded': bool,         # true when CDP was unavailable / partial
            'degraded_reason': str,   # human-readable explanation
            'user_action': Optional[str], # what the user needs to do (if anything)
            'data': {
                'title': str,
                'price': str,
                'brand': str,
                'seller': str,
                'images': list[str],
                'weight_grams': Optional[int],
                'packaging_rows': list[dict],
                'sku_details': list[dict],
                'attributes': list[dict],
                'option_groups': list[dict],
            },
            'source': 'api+cdp' | 'api_only' | 'cdp_degraded',
        }

    Agent usage (Worker A Step 2b)::

        enriched = enrich_product_with_cdp(
            detail_url=item['detailUrl'],
            api_data=d,
        )
        # No branching needed — enriched['data'] is always populated.
        # When degraded, images/packaging_rows/etc. are empty lists.
    """
    from scripts.capabilities.browser_probe.service import (
        check_cdp_prerequisites,
        probe_1688_page_safe,
    )

    api = dict(api_data or {})

    # ── Base data from API (always available) ──
    result: dict[str, Any] = {
        'ok': False,
        'degraded': True,
        'degraded_reason': '',
        'user_action': None,
        'data': {
            'title': api.get('title', ''),
            'price': api.get('price', ''),
            'brand': '',
            'seller': '',
            'images': list(api.get('images') or []),
            'weight_grams': None,
            'packaging_rows': [],
            'shipping': {},
            'description': '',
            'sku_details': [],
            'attributes': [],
            'option_groups': [],
            'category_id': api.get('category_id', ''),
        },
        'source': 'api_only',
    }

    # ── Check CDP prerequisites ──
    prereqs = check_cdp_prerequisites()
    if not prereqs['browser_available']:
        issues = prereqs.get('issues', [])
        suggestions = prereqs.get('suggestions', [])
        result['degraded_reason'] = (
            '未找到 Chromium 内核浏览器（Chrome/Edge/Brave/360等）。'
        )
        result['user_action'] = (
            '请安装任一 Chromium 内核浏览器后重试。\n'
            + '\n'.join(f'  • {s}' for s in suggestions) +
            '\n  已安装？运行: pounding-ozon install-browser 强制检测'
        )
        result['source'] = 'api_only'
        return result

    # ── Handle missing session or login: auto-launch + wait ──
    from scripts.capabilities.browser_probe.service import (
        _resolve_browser_session,
        _cdp_available,
        _wait_for_login_session,
        find_browser_executable,
    )

    profile_name = 'default'
    session = _resolve_browser_session(profile_name)
    cdp_url = str(session.get('cdp_url') or '').strip()
    session_alive = bool(cdp_url and _cdp_available(cdp_url))
    session_logged_in = bool(session.get('login_detected'))

    if not session_alive:
        if not session_logged_in:
            # No live Chrome AND never logged in — need full login flow
            url = str(detail_url or api.get('detail_url') or '').strip()
            if not url:
                url = 'https://detail.1688.com/'

            resolved_browser = find_browser_executable(None)
            new_session = _wait_for_login_session(
                url,
                profile_name=profile_name,
                browser_path=resolved_browser or '',
                timeout_seconds=max(timeout_seconds, 60),
            )
            if new_session and new_session.get('login_detected'):
                session = new_session
            elif new_session and new_session.get('cdp_url'):
                session = new_session
            else:
                result['degraded_reason'] = (
                    '等待 1688 登录超时。请在自动打开的浏览器窗口中扫码登录。'
                )
                result['user_action'] = (
                    '请在浏览器中打开 https://login.1688.com/member/signin.htm '
                    '扫码登录 1688 后重试。支持 Chrome/Edge/Brave 等 Chromium 内核浏览器。'
                )
                return result
        # else: logged in but Chrome isn't running — _resolve_browser_session()
        # already auto-launched a new Chrome with the persistent profile.
        # Cookies are preserved, so just use it directly.

    # ── CDP probe ──
    url = str(detail_url or api.get('detail_url') or '').strip()
    if not url:
        result['degraded_reason'] = '缺少 1688 商品链接，无法启动浏览器探测。'
        return result

    probe_result = probe_1688_page_safe(url, timeout_seconds=timeout_seconds)
    probe_data = probe_result.get('data', {})

    if probe_result['ok']:
        result['ok'] = True
        result['degraded'] = False
        result['source'] = 'api+cdp'
        # Update login_detected flag — user may have logged in manually
        if prereqs.get('login_required'):
            session['login_detected'] = True
            try:
                from scripts.capabilities.browser_probe.service import _write_browser_session
                _write_browser_session(profile_name, session)
            except Exception as e:
                logger.debug('_write_browser_session failed for %s: %s', profile_name, e)
    elif probe_result['degraded'] and probe_data.get('images'):
        # Partial success — got some data even though probe wasn't 100%
        result['degraded_reason'] = '部分商品数据未能提取，已获取已有数据。'
        result['source'] = 'cdp_degraded'
    else:
        result['degraded_reason'] = (
            f"浏览器探测失败: {probe_result.get('error', '未知错误')}"
        )
        result['source'] = 'api_only'
        return result

    # ── Merge CDP data over API data ──
    result['data'].update({
        # API title is the full product title (e.g. "东南亚爆款638手持便携式高速电风扇...")
        # CDP may extract short snippets ("638 usb", "X05").  Prefer the longer title.
        'title': (
            result['data']['title']
            if len(result['data']['title'] or '') >= len(probe_data.get('title') or '')
            else probe_data.get('title')
        ),
        'price': probe_data.get('price') or result['data']['price'],
        'brand': probe_data.get('brand') or result['data']['brand'],
        'seller': probe_data.get('seller') or result['data']['seller'],
        'images': probe_data.get('images') or result['data']['images'],
        'weight_grams': probe_data.get('weight_grams') or result['data']['weight_grams'],
        'packaging_rows': probe_data.get('packaging_rows') or [],
        'shipping': probe_data.get('shipping') or {},
        'description': probe_data.get('description') or '',
        'sku_details': probe_data.get('sku_details') or [],
        'attributes': probe_data.get('attributes') or [],
        'option_groups': probe_data.get('option_groups') or [],
    })

    return result
