"""批次 C(v0.70): Ozon 卖家会话直调客户端 — 只拼请求与判废，零业务逻辑。

消费 ozon_session_service 解出的 Cookie 头 + sc_company_id，直调
seller.ozon.ru 内部端点（what_to_sell v3，契约与 skill
ozon_seller_analytics.fetch_sales_analytics_direct 实证直调同源：
POST + Cookie + x-o3-company-id + x-o3-language: zh-Hans）。

判废出口（C6 失效联动）：401/403/登录 302 → (None, "session_expired")，
由调用方 mark_status("expired")。429/5xx/网络异常 ≠ 会话失效，不误标。

安全红线：cookie 头只进请求头，绝不写日志（本模块唯一日志是 HTTP 状态码）。
"""
from __future__ import annotations

import logging

import requests

logger = logging.getLogger(__name__)

SELLER_ORIGIN = "https://seller.ozon.ru"
WHAT_TO_SELL_URL = f"{SELLER_ORIGIN}/api/site/seller-analytics/what_to_sell/data/v3"

_SESSION_EXPIRED = "session_expired"

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


def build_what_to_sell_payload(sku: str | int, limit: int = 50, offset: int = 0) -> dict:
    """what_to_sell v3 请求体（skill 实证契约，勿改键名/取值形态）。

    sku 是 int64 无 _0 后缀：Ozon product_id 变体后缀（``123456_0``）在此剥离，
    只留数字段。limit/offset 沿用实测可用的字符串形态（与 skill 直调逐字一致）。
    """
    sku_clean = str(sku or "").split("_", 1)[0].strip()
    return {
        "limit": str(limit),
        "offset": str(offset),
        "filter": {"stock": "any_stock", "period": "monthly",
                   "categories": [], "sku": sku_clean},
        "sort": {"key": "sum_gmv_desc"},
    }


def what_to_sell(cookie_header: str, sc_company_id: str, payload: dict,
                 *, timeout: int = 20) -> tuple[dict | None, str | None]:
    """POST what_to_sell v3 → (data, None) | (None, error_code)。

    error_code: session_expired（401/403/终态 3xx/落到登录页，调用方标 expired）、
    ozon_http_{status}（其余非 200）、network_error、non_json_response。

    ⚠️ 实机契约（2026-09-09）：seller nginx 首请求会 307→同路径?__rr=1 并
    Set-Cookie 下发 nonce（__Secure-ETC），重试必须携带该新 cookie 才放行——
    因此必须走 Session cookie jar + allow_redirects=True（307 语义保持
    POST+body），手动改 header 重放会无限 307。判废只看终态。
    """
    headers = {
        "Content-Type": "application/json",
        "x-o3-company-id": str(sc_company_id),
        "x-o3-language": "zh-Hans",
        "User-Agent": _UA,
        "Referer": f"{SELLER_ORIGIN}/",
        "Accept": "application/json, text/plain, */*",
    }
    session = requests.Session()
    for part in cookie_header.split(";"):
        if "=" in part:
            name, _, value = part.partition("=")
            session.cookies.set(name.strip(), value.strip())
    try:
        resp = session.post(WHAT_TO_SELL_URL, json=payload, headers=headers,
                            timeout=timeout, allow_redirects=True)
    except requests.RequestException as exc:
        logger.warning("what_to_sell 直调网络异常: %s", str(exc)[:150])
        return None, "network_error"
    finally:
        session.close()
    final_url = str(getattr(resp, "url", "") or "")
    if resp.status_code in (401, 403) or "login" in final_url.lower():
        # 会话/风控判废；重定向跟到登录页同判（C6 联动标 expired）
        logger.info("what_to_sell 直调判废 HTTP %s final=%s",
                    resp.status_code, final_url[:80])
        return None, _SESSION_EXPIRED
    if 300 <= resp.status_code < 400:
        # 重定向链未到 200 终态（罕见）——不误标会话失效
        return None, f"ozon_http_{resp.status_code}"
    if resp.status_code != 200:
        return None, f"ozon_http_{resp.status_code}"
    try:
        return resp.json(), None
    except ValueError:
        logger.warning("what_to_sell 直调非 JSON 响应（body 头 80 字节已丢弃）")
        return None, "non_json_response"
