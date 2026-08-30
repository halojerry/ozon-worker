"""skill 端货源匹配上报(PRD M5b):图搜/跟卖匹配结果 → worker source_candidates。

POST {CLOUD_API_BASE}/api/v1/source-candidates
body: {token, client_id?, candidates: [{product_id, source_offer_id, source_url,
       price_cny, match_score, match_method, status}]}

设计:
- fail-open:任何异常只 logger.warning,绝不阻塞主流程(daemon 线程触发);
- 无 token / 无 product_id → 直接跳过;
- match_score 优先级:badge_score > confidence > normalization_score > 排名归一化;
- price 兼容 "¥12.34" / "12.34-15.00" / 纯数字(取区间下限,保守成本)。
"""
from __future__ import annotations

import logging
import re
import threading
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _parse_price_cny(raw: Any) -> Optional[float]:
    """1688 价格字符串 → 最低价 CNY(取区间下限);失败 → None。"""
    if raw is None:
        return None
    text = str(raw).replace("¥", "").replace("￥", "").replace(",", "").strip()
    if not text or not re.search(r"\d", text):
        return None
    nums = re.findall(r"\d+(?:\.\d+)?", text)
    if not nums:
        return None
    try:
        return min(float(n) for n in nums)
    except (TypeError, ValueError):
        return None


def _detail_url(item: dict) -> str:
    url = str(item.get("detail_url") or item.get("url") or "").strip()
    if url:
        return url
    offer_id = str(item.get("id") or item.get("product_id") or item.get("itemId") or "").strip()
    if offer_id and offer_id.isdigit():
        return f"https://detail.1688.com/offer/{offer_id}.html"
    return ""


def _match_score(item: dict, rank: int) -> float:
    """评分归一化:显式评分优先,否则按排名 1.0→0.6 递减(避免全 0 无法排序)。"""
    for key in ("badge_score", "confidence", "normalization_score", "score"):
        try:
            val = float(item.get(key) or 0)
        except (TypeError, ValueError):
            continue
        if val > 0:
            return round(min(val, 1.0), 2)
    return round(max(0.6, 1.0 - rank * 0.1), 2)


def _offer_id(item: dict) -> str:
    return str(
        item.get("source_offer_id")
        or item.get("offer_id")
        or item.get("id")
        or item.get("product_id")
        or item.get("itemId")
        or ""
    ).strip()


def report_source_candidates(
    product_id: str,
    matches: list[dict],
    method: str,
    *,
    client_id: str = "",
    token: Optional[str] = None,
    max_n: int = 5,
) -> None:
    """同步上报匹配候选(调用方应放到 daemon 线程)。缺 token/product → 跳过。"""
    product_id = str(product_id or "").strip()
    if not product_id or not matches:
        return
    try:
        from scripts._const import CLOUD_API_BASE
        from scripts.lib.config_store import get_mxou_token
        import requests as _req

        mxou_token = token or get_mxou_token()
        if not mxou_token:
            logger.warning("source_candidates 上报跳过: 无 token(set_token 配置)")
            return

        rows = []
        for rank, item in enumerate(matches[:max_n]):
            offer_id = _offer_id(item)
            url = _detail_url(item)
            if not url:
                continue
            rows.append({
                "product_id": product_id,
                "source_offer_id": offer_id,
                "source_url": url,
                "price_cny": _parse_price_cny(item.get("price") or item.get("match_1688_price")),
                "match_score": _match_score(item, rank),
                "match_method": str(method or "image").lower(),
                "status": "valid",
            })
        if not rows:
            return
        payload = {"token": mxou_token, "candidates": rows}
        if client_id:
            payload["client_id"] = str(client_id)
        resp = _req.post(
            f"{CLOUD_API_BASE}/api/v1/source-candidates",
            json=payload,
            timeout=8,
        )
        if resp.status_code >= 300:
            logger.warning("source_candidates 上报失败: HTTP %s", resp.status_code)
    except Exception as exc:
        logger.warning("source_candidates 上报失败(本地不影响): %s", exc)


def spawn_source_report(
    product_id: str,
    matches: list[dict],
    method: str,
    *,
    client_id: str = "",
    token: Optional[str] = None,
) -> None:
    """非阻塞上报(daemon 线程,fail-open)。"""
    if not product_id or not matches:
        return
    try:
        threading.Thread(
            target=report_source_candidates,
            args=(product_id, matches, method),
            kwargs={"client_id": client_id, "token": token},
            daemon=True,
        ).start()
    except Exception as exc:
        logger.warning("source_candidates 上报线程启动失败: %s", exc)
