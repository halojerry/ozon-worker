"""skill 采集数据上报 worker /api/v1/analytics/*（v0.34 C5, todo 8）。

what-to-sell SPA 三页采集结果（all-queries / ozon-bestsellers / market-bestsellers）
异步 POST 到 worker PG 沉淀（worker 端点 todo 6 已实现）：

    POST {WORKER_URL}/api/v1/analytics/queries            body: {token, queries: [...]}
    POST {WORKER_URL}/api/v1/analytics/ozon-bestsellers   body: {token, items: [...]}
    POST {WORKER_URL}/api/v1/analytics/market-bestsellers body: {token, items: [...]}

设计约定（plan sentry-attribute-fixes todo 8）：
- 失败不阻断：任何异常 → logger.warning + 返回 {"uploaded": 0, "error": ...}，绝不抛异常
- 无 token 跳过：返回 {"skipped": True, "reason": "no token"}，不发起请求
- worker 不可达降级：requests 超时/连接错误 → warning + error dict
- 不阻塞 CLI 主流程：upload_in_background() 用 daemon thread fire-and-forget
- 只上报指标数据，绝不上传 cookie / Ozon 凭证 / PII
- 不复制 maozi 的 gzip+AES 加密：worker 自家 PG 即权威源，HTTPS 即可
"""
from __future__ import annotations

import logging
import threading
from typing import Any

import requests

from scripts._const import CLOUD_API_BASE

logger = logging.getLogger(__name__)

# (connect, read) 秒 — 双 10s 内
UPLOAD_TIMEOUT = (10, 10)

# kind → body 列表字段名（worker _ANALYTICS_KINDS 的 list_key）
_LIST_KEYS = {
    "queries": "queries",
    "ozon-bestsellers": "items",
    "market-bestsellers": "items",
}


def _to_int(v: Any) -> int:
    try:
        return int(float(str(v).replace(" ", "").replace(",", "")))
    except (TypeError, ValueError):
        return 0


def _to_float(v: Any) -> float:
    try:
        return float(str(v).replace(" ", "").replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def _category_id(row: dict) -> int | None:
    """取类目 ID：Seller 空间 category2_id（description_category_id）优先，
    兼容 category1_id / category3_id / category_id。"""
    for k in ("category2_id", "category1_id", "category3_id", "category_id"):
        v = row.get(k)
        if v not in (None, "", 0, "0"):
            try:
                return int(v)
            except (TypeError, ValueError):
                continue
    return None


def _category_path(row: dict) -> str | None:
    """类目路径：category1 / category3 名称拼合；无名称返回 None。"""
    parts = [str(row[k]).strip() for k in ("category1", "category3") if row.get(k)]
    if not parts:
        return None
    return " / ".join(p for p in parts if p)


def _normalize_rows(kind: str, rows: list[dict]) -> list[dict]:
    """把 fetch 函数返回的原始 dict 过滤/重命名为 worker 端点期望字段。

    字段对齐 worker/api/schemas.py：
      queries:           query/count/ca/avg_ca_rub/uniq_queries_wca/uniq_sellers
      ozon-bestsellers:  sku_or_id/brand/category_id/category_path/ordering_amount/ordering_count/avg_price_rub
      market-bestsellers:product_name/brand/category_id/category_path/ordering_amount/daily_avg/other_platform_price
    未知字段丢弃；缺必填字段（query/sku/name）的行跳过；数字字段统一转类型。
    """
    if not isinstance(rows, list):
        return []
    out: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            if kind == "queries":
                q = str(row.get("query") or "").strip()
                if not q:
                    continue
                out.append({
                    "query": q,
                    "count": _to_int(row.get("count")),
                    "ca": _to_float(row.get("ca")),
                    "avg_ca_rub": _to_float(row.get("avg_ca_rub")),
                    # skill 端字段名是 uniq_queries_w_ca，worker 期望 uniq_queries_wca
                    "uniq_queries_wca": _to_int(
                        row.get("uniq_queries_w_ca", row.get("uniq_queries_wca"))),
                    "uniq_sellers": _to_float(row.get("uniq_sellers")),
                })
            elif kind == "ozon-bestsellers":
                sku = str(row.get("sku") or row.get("sku_or_id") or "").strip()
                if not sku:
                    continue
                item: dict[str, Any] = {
                    "sku_or_id": sku,
                    # 订购金额 = GMV（gmv_sum），订购数量 = soldCount
                    "ordering_amount": _to_float(row.get("gmv_sum", row.get("ordering_amount"))),
                    "ordering_count": _to_int(row.get("sold_count", row.get("ordering_count"))),
                    "avg_price_rub": _to_float(row.get("avg_price", row.get("avg_price_rub"))),
                }
                if row.get("brand"):
                    item["brand"] = str(row["brand"])
                cat = _category_id(row)
                if cat is not None:
                    item["category_id"] = cat
                path = _category_path(row)
                if path:
                    item["category_path"] = path
                out.append(item)
            elif kind == "market-bestsellers":
                name = str(
                    row.get("name") or row.get("product_name") or row.get("sku") or ""
                ).strip()
                if not name:
                    continue
                item = {
                    "product_name": name,
                    "ordering_amount": _to_float(row.get("gmv_sum", row.get("ordering_amount"))),
                }
                if row.get("daily_avg") not in (None, ""):
                    item["daily_avg"] = _to_float(row["daily_avg"])
                if row.get("brand"):
                    item["brand"] = str(row["brand"])
                cat = _category_id(row)
                if cat is not None:
                    item["category_id"] = cat
                path = _category_path(row)
                if path:
                    item["category_path"] = path
                # 其他平台价格：skill 侧暂未采集，预留（有则透传）
                if row.get("other_platform_price") not in (None, ""):
                    item["other_platform_price"] = _to_float(row["other_platform_price"])
                out.append(item)
        except Exception as exc:  # 单行归一化失败跳过，不阻断整批
            logger.debug("normalize %s row failed: %s", kind, exc)
    return out


def upload_analytics(kind: str, rows: list[dict], token: str | None = None) -> dict:
    """POST 采集数据到 worker /api/v1/analytics/{kind}。绝不抛异常。

    Returns:
        {"uploaded": N, "inserted": N, "upserted": N}  成功
        {"skipped": True, "reason": "no token"}         无 token / 空 rows
        {"uploaded": 0, "error": ...}                   失败 / 无有效行 / 未知 kind
    """
    if not token:
        return {"skipped": True, "reason": "no token"}
    if kind not in _LIST_KEYS:
        logger.warning("analytics upload: unknown kind %r, skip", kind)
        return {"uploaded": 0, "error": f"unknown kind {kind!r}"}
    if not rows:
        return {"skipped": True, "reason": "empty rows"}
    normalized = _normalize_rows(kind, rows)
    if not normalized:
        logger.warning("analytics upload %s: 0 valid rows after normalize, skip", kind)
        return {"uploaded": 0, "error": "no valid rows after normalize"}
    try:
        url = f"{CLOUD_API_BASE}/api/v1/analytics/{kind}"
        resp = requests.post(
            url,
            json={"token": token, _LIST_KEYS[kind]: normalized},
            timeout=UPLOAD_TIMEOUT,
        )
        if resp.status_code >= 400:
            logger.warning(
                "analytics upload %s failed: HTTP %s %s",
                kind, resp.status_code, resp.text[:300],
            )
            return {"uploaded": 0, "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
        try:
            data = resp.json()
        except Exception:
            logger.warning("analytics upload %s: non-JSON response HTTP %s", kind, resp.status_code)
            return {"uploaded": 0, "error": f"non-JSON response HTTP {resp.status_code}"}
        inserted = int(data.get("inserted") or 0)
        upserted = int(data.get("upserted") or 0)
        logger.info(
            "analytics upload %s ok: uploaded=%d inserted=%d upserted=%d",
            kind, len(normalized), inserted, upserted,
        )
        return {"uploaded": len(normalized), "inserted": inserted, "upserted": upserted}
    except Exception as exc:
        logger.warning("analytics upload %s failed（worker 不可达/网络错误）: %s", kind, exc)
        return {"uploaded": 0, "error": str(exc)}


def upload_in_background(kind: str, rows: list[dict], token: str | None = None) -> None:
    """fire-and-forget 上报：daemon thread 调 upload_analytics，绝不阻塞主流程。

    token 未传时从 config_store 读（settings.json 的 mxou_token）。无 token /
    空 rows → 直接返回（不发起请求）。仅记录日志，不 print（防污染 CLI 输出）。
    """
    if not rows:
        return
    if token is None:
        try:
            from scripts.lib import config_store
            token = config_store.get_mxou_token()
        except Exception as exc:
            logger.debug("analytics upload: read token failed: %s", exc)
    if not token:
        logger.info("analytics upload %s: no token, skipping upload", kind)
        return
    threading.Thread(
        target=_upload_worker,
        args=(kind, rows, token),
        daemon=True,
        name=f"analytics-upload-{kind}",
    ).start()


def _upload_worker(kind: str, rows: list[dict], token: str) -> None:
    """daemon thread 目标：upload_analytics 内部已全包异常，这里再兜底一层。"""
    try:
        upload_analytics(kind, rows, token=token)
    except Exception as exc:  # 双保险
        logger.warning("analytics upload thread %s crashed: %s", kind, exc)
