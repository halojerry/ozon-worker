"""v0.56: 店铺数据缓存同步服务 — Ozon 订单/在线商品落 PG，读取秒开。

设计（对比旧版每次实时透传 Ozon API）：
- 同步：订单增量（since=最后同步点−1h 重叠，上限 90 天）+ 商品全量分页
- 写入：upsert 覆盖（订单状态会变）；商品本次未出现 → archived（不硬删）
- 读取：list_cached_orders / list_cached_products 走 PG；从未同步过 →
  sync_store 先同步再返回（懒同步，用户无感首屏稍慢）
- ⚠️ 租户隔离硬约束：所有 SQL 按 tenant_id + credential_id 过滤；
  credential 归属一律经 credential_service.get_decrypted 校验（跨租户 404）
"""
from __future__ import annotations

import datetime
import json
import logging
from typing import Any, Optional

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from services import credential_service, order_service, shelf_service
from storage.database.db import get_engine

logger = logging.getLogger(__name__)

# 订单增量窗口（与 order_service._build_filter 上限一致）
SYNC_ORDERS_WINDOW_DAYS = 90
# 增量重叠窗口：最后同步点往前多拉 1 小时，防边界漏单
_SYNC_OVERLAP_HOURS = 1
# 商品全量分页大小（Ozon /v3/product/list 单页上限 1000，保守取 500）
_PRODUCT_PAGE = 500
# 单店同步最大订单拉取页数（防超大店一次同步打爆配额；每页 200）
_ORDER_PAGE = 200
_MAX_ORDER_PAGES = 25


# ──────────────────────────────────────────────
# 同步
# ──────────────────────────────────────────────


def sync_store(tenant_id: str, credential_id: str) -> dict:
    """同步单个店铺（订单增量 + 商品全量），逐项容错（一项失败不阻断另一项）。

    credential 归属校验：get_decrypted 跨租户/已吊销 → 404。
    """
    client_id, api_key = credential_service.get_decrypted(tenant_id, credential_id)
    result: dict[str, Any] = {"credential_id": credential_id, "ozon_client_id": client_id}
    result["orders"] = _sync_orders(tenant_id, credential_id, client_id, api_key)
    result["products"] = _sync_products(tenant_id, credential_id, client_id, api_key)
    return result


def _sync_orders(tenant_id: str, credential_id: str, client_id: str, api_key: str) -> dict:
    """订单增量同步：since = max(上次同步 − 1h 重叠, 90 天前) → 分页拉全 → upsert。"""
    from utils.ozon_client import ozon_post

    since_dt = _orders_since(tenant_id, credential_id)
    since = since_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    to = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    synced = 0
    offset = 0
    for _page in range(_MAX_ORDER_PAGES):
        try:
            resp = ozon_post(
                client_id, api_key, "/v3/posting/fbs/list",
                {
                    "dir": "ASC",
                    "filter": {"since": since, "to": to},
                    "limit": _ORDER_PAGE,
                    "offset": offset,
                    "with": {"analytics_data": True, "financial_data": True},
                },
                timeout=30, language="RU",
            )
        except Exception as exc:
            logger.warning("订单同步拉取失败 tenant=%s store=%s: %s",
                           tenant_id, credential_id, str(exc)[:200])
            _set_sync_error(tenant_id, credential_id, "orders",
                            f"拉取失败: {str(exc)[:120]}")
            return {"synced": synced, "error": str(exc)[:120]}

        result = resp.get("result") or {}
        postings = result.get("postings") or []
        if not postings:
            break
        _upsert_orders(tenant_id, credential_id, postings)
        synced += len(postings)
        offset += len(postings)
        if offset >= int(result.get("total") or offset):
            break

    _set_sync_error(tenant_id, credential_id, "orders", "")
    return {"synced": synced, "error": ""}


def _orders_since(tenant_id: str, credential_id: str) -> datetime.datetime:
    """增量起点：上次同步 − 1h 重叠；从未同步 → 90 天前。"""
    now = datetime.datetime.now(datetime.timezone.utc)
    row = _sync_state_row(tenant_id, credential_id)
    last = (row.orders_last_synced_at if row else None)
    if last is not None:
        return last - datetime.timedelta(hours=_SYNC_OVERLAP_HOURS)
    return now - datetime.timedelta(days=SYNC_ORDERS_WINDOW_DAYS)


def _upsert_orders(tenant_id: str, credential_id: str, postings: list) -> None:
    """订单 upsert（ON CONFLICT tenant+store+posting_number DO UPDATE 覆盖状态）。"""
    rows = []
    for p in postings:
        if not isinstance(p, dict) or not p.get("posting_number"):
            continue
        norm = order_service._normalize_posting(p)
        created = None
        if norm.get("created_at"):
            try:
                created = datetime.datetime.fromisoformat(norm["created_at"])
            except (ValueError, TypeError):
                created = None
        rows.append({
            "tenant_id": tenant_id,
            "credential_id": credential_id,
            "posting_number": norm["posting_number"],
            "status": norm["status"],
            "raw_status": norm["raw_status"],
            # executemany 下 CAST(:x AS jsonb) 无法适配 dict——预序列化 JSON 字符串
            "products": json.dumps(norm["products"], ensure_ascii=False),
            "product_count": norm["product_count"],
            "total_amount": norm["total_amount"],
            "commission_amount": norm["commission_amount"],
            "profit": norm["profit"],
            "warehouse": norm["warehouse"],
            "delivery_method": norm["delivery_method"],
            "cancel_reason": norm["cancel_reason"],
            "cancellation": norm["cancellation"],
            "order_created_at": created,
        })
    if not rows:
        return
    with get_engine().begin() as conn:
        conn.execute(text(
            """
            INSERT INTO ozon_orders_cache
                (tenant_id, credential_id, posting_number, status, raw_status,
                 products, product_count, total_amount, commission_amount, profit,
                 warehouse, delivery_method, cancel_reason, cancellation, order_created_at, synced_at)
            VALUES
                (:tenant_id, :credential_id, :posting_number, :status, :raw_status,
                 CAST(:products AS jsonb), :product_count, :total_amount, :commission_amount, :profit,
                 :warehouse, :delivery_method, :cancel_reason, :cancellation, :order_created_at, NOW())
            ON CONFLICT (tenant_id, credential_id, posting_number) DO UPDATE SET
                status = EXCLUDED.status,
                raw_status = EXCLUDED.raw_status,
                products = EXCLUDED.products,
                product_count = EXCLUDED.product_count,
                total_amount = EXCLUDED.total_amount,
                commission_amount = EXCLUDED.commission_amount,
                profit = EXCLUDED.profit,
                warehouse = EXCLUDED.warehouse,
                delivery_method = EXCLUDED.delivery_method,
                cancel_reason = EXCLUDED.cancel_reason,
                cancellation = EXCLUDED.cancellation,
                order_created_at = EXCLUDED.order_created_at,
                synced_at = NOW()
            """
        ), rows)


def _sync_products(tenant_id: str, credential_id: str, client_id: str, api_key: str) -> dict:
    """商品全量同步：分页拉 /v3/product/list → 批量 /v3/product/info/list 补详情 → upsert + archived。"""
    from utils.ozon_client import ozon_post

    seen_ids: set[str] = set()
    offset = 0
    total = None
    error = ""
    while total is None or offset < total:
        try:
            list_resp = ozon_post(
                client_id, api_key, "/v3/product/list",
                {"filter": {"visibility": "ALL"}, "limit": _PRODUCT_PAGE, "offset": offset, "sort_dir": "ASC"},
                timeout=30, language="RU",
            )
        except Exception as exc:
            logger.warning("商品同步拉取失败 tenant=%s store=%s: %s",
                           tenant_id, credential_id, str(exc)[:200])
            error = f"拉取失败: {str(exc)[:120]}"
            break

        result = list_resp.get("result") or {}
        items = result.get("items") or []
        total = int(result.get("total") or (offset + len(items)))
        if not items:
            break

        info_map = _fetch_info_map(client_id, api_key, items)
        _upsert_products(tenant_id, credential_id, items, info_map)
        for it in items:
            if isinstance(it, dict) and it.get("product_id"):
                seen_ids.add(str(it["product_id"]))

        offset += len(items)
        if len(items) < _PRODUCT_PAGE:
            break

    _archive_missing(tenant_id, credential_id, seen_ids)
    if not error:
        _set_sync_error(tenant_id, credential_id, "products", "")
    return {"synced": len(seen_ids), "error": error}


def _fetch_info_map(client_id: str, api_key: str, items: list) -> dict:
    """批量补商品详情（复用 shelf_service 的限流退避逻辑，3 次 1s/2s）。"""
    from utils.ozon_client import ozon_post

    ids = [int(it["product_id"]) for it in items
           if isinstance(it, dict) and str(it.get("product_id") or "").isdigit()]
    if not ids:
        return {}
    import time as _time
    for attempt in range(3):
        try:
            info_resp = ozon_post(
                client_id, api_key, "/v3/product/info/list",
                {"product_id": ids}, timeout=30, language="RU",
            )
        except Exception:
            if attempt < 2:
                _time.sleep(1 + attempt)
            continue
        info_items = (info_resp.get("result") or {}).get("items") or []
        if info_items:
            return {str(it.get("product_id")): it for it in info_items if isinstance(it, dict)}
        if attempt < 2:
            _time.sleep(1 + attempt)
    logger.warning("Ozon info/list 同步限流（3 次空）ids=%s", ids[:5])
    return {}


def _upsert_products(tenant_id: str, credential_id: str, items: list, info_map: dict) -> None:
    rows = []
    for it in items:
        if not isinstance(it, dict) or not it.get("product_id"):
            continue
        pid = str(it["product_id"])
        info = info_map.get(pid, {}) or {}
        price = info.get("price")
        price_el = None
        if isinstance(price, dict):
            price_el = price.get("price") or price.get("marketing_price")
        rows.append({
            "tenant_id": tenant_id,
            "credential_id": credential_id,
            "product_id": pid,
            "offer_id": str(it.get("offer_id") or ""),
            "name": str(info.get("name") or it.get("offer_id") or ""),
            "image": (info.get("images") or [None])[0] if isinstance(info.get("images"), list) and info.get("images") else None,
            "price": price_el if price_el is not None else None,
            "stock": (info.get("stocks") or {}).get("present") if isinstance(info.get("stocks"), dict) else None,
            "currency": "",
        })
    if not rows:
        return
    with get_engine().begin() as conn:
        conn.execute(text(
            """
            INSERT INTO ozon_products_cache
                (tenant_id, credential_id, product_id, offer_id, name, image,
                 price, stock, currency, archived, synced_at)
            VALUES
                (:tenant_id, :credential_id, :product_id, :offer_id, :name, :image,
                 :price, :stock, :currency, FALSE, NOW())
            ON CONFLICT (tenant_id, credential_id, product_id) DO UPDATE SET
                offer_id = EXCLUDED.offer_id,
                name = EXCLUDED.name,
                image = EXCLUDED.image,
                price = EXCLUDED.price,
                stock = EXCLUDED.stock,
                currency = EXCLUDED.currency,
                archived = FALSE,
                synced_at = NOW()
            """
        ), rows)


def _archive_missing(tenant_id: str, credential_id: str, seen_ids: set) -> None:
    """本次同步未出现的商品 → archived=True（软删，保历史展示）。"""
    with get_engine().begin() as conn:
        conn.execute(text(
            "UPDATE ozon_products_cache SET archived=TRUE "
            "WHERE tenant_id=:t AND credential_id=:c AND NOT (product_id = ANY(:ids))",
        ), {"t": tenant_id, "c": credential_id, "ids": list(seen_ids)})


def _sync_state_row(tenant_id: str, credential_id: str):
    with get_engine().connect() as conn:
        return conn.execute(text(
            "SELECT orders_last_synced_at, products_last_synced_at, "
            "orders_error, products_error "
            "FROM credential_sync_state WHERE tenant_id=:t AND credential_id=:c"
        ), {"t": tenant_id, "c": credential_id}).fetchone()


def _set_sync_error(tenant_id: str, credential_id: str, kind: str, error: str) -> None:
    """同步状态 upsert（orders/products 各自的最后时间 + 错误）。

    两列错误字段都必须写入（另一列置空），否则 NOT NULL 约束报错。
    """
    col_ts = "orders_last_synced_at" if kind == "orders" else "products_last_synced_at"
    col_err = "orders_error" if kind == "orders" else "products_error"
    other_err = "products_error" if kind == "orders" else "orders_error"
    with get_engine().begin() as conn:
        conn.execute(text(
            f"""
            INSERT INTO credential_sync_state (tenant_id, credential_id, {col_ts}, {col_err}, {other_err}, updated_at)
            VALUES (:t, :c, NOW(), :err, '', NOW())
            ON CONFLICT (tenant_id, credential_id) DO UPDATE SET
                {col_ts} = NOW(),
                {col_err} = :err,
                {other_err} = '',
                updated_at = NOW()
            """
        ), {"t": tenant_id, "c": credential_id, "err": error})


# ──────────────────────────────────────────────
# 读取（PG 缓存）
# ──────────────────────────────────────────────


def get_sync_status(tenant_id: str, credential_id: str) -> dict:
    """店铺同步状态（webui 展示最后同步时间/错误）。"""
    row = _sync_state_row(tenant_id, credential_id)
    if row is None:
        return {"credential_id": credential_id,
                "orders_last_synced_at": None, "products_last_synced_at": None,
                "orders_error": "", "products_error": ""}
    return {
        "credential_id": credential_id,
        "orders_last_synced_at": row.orders_last_synced_at.isoformat() if row.orders_last_synced_at else None,
        "products_last_synced_at": row.products_last_synced_at.isoformat() if row.products_last_synced_at else None,
        "orders_error": row.orders_error or "",
        "products_error": row.products_error or "",
    }


def _needs_orders_sync(tenant_id: str, credential_id: str) -> bool:
    return not _has_rows(tenant_id, credential_id, "ozon_orders_cache")


def _needs_products_sync(tenant_id: str, credential_id: str) -> bool:
    return not _has_rows(tenant_id, credential_id, "ozon_products_cache")


def _has_rows(tenant_id: str, credential_id: str, table: str) -> bool:
    with get_engine().connect() as conn:
        row = conn.execute(text(
            f"SELECT 1 FROM {table} WHERE tenant_id=:t AND credential_id=:c LIMIT 1"
        ), {"t": tenant_id, "c": credential_id}).fetchone()
    return row is not None


def list_cached_orders(
    tenant_id: str,
    credential_id: str,
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    since_days: int = 30,
    lazy_sync: bool = True,
) -> dict:
    """订单读取：PG 缓存。从未同步过 → 懒同步（用户无感首屏稍慢）；?refresh=1 强制同步。

    租户隔离：credential 归属先经 get_decrypted 校验（跨租户 404），
    随后查询一律带 tenant_id + credential_id。
    """
    credential_service.get_decrypted(tenant_id, str(credential_id))  # 归属校验（跨租户 404）
    if lazy_sync and _needs_orders_sync(tenant_id, str(credential_id)):
        sync_store(tenant_id, str(credential_id))

    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    since = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=min(max(int(since_days), 1), 90))
    sql = ("SELECT posting_number, status, raw_status, products, product_count, "
           "total_amount, commission_amount, profit, warehouse, delivery_method, "
           "cancel_reason, cancellation, order_created_at "
           "FROM ozon_orders_cache WHERE tenant_id=:t AND credential_id=:c")
    params: dict = {"t": tenant_id, "c": str(credential_id)}
    if status and status != "all":
        sql += " AND status=:st"
        params["st"] = status
    sql += " AND (order_created_at IS NULL OR order_created_at >= :since)"
    params["since"] = since

    count_sql = f"SELECT COUNT(*) FROM ({sql}) _sub"
    with get_engine().connect() as conn:
        total = int(conn.execute(text(count_sql), params).scalar() or 0)
        rows = conn.execute(
            text(sql + " ORDER BY order_created_at DESC NULLS LAST LIMIT :lim OFFSET :off"),
            {**params, "lim": limit, "off": offset},
        ).fetchall()

    items = []
    for r in rows:
        items.append({
            "posting_number": r.posting_number,
            "status": r.status,
            "raw_status": r.raw_status,
            "created_at": r.order_created_at.isoformat() if r.order_created_at else None,
            "products": r.products or [],
            "product_count": r.product_count,
            "total_amount": r.total_amount,
            "commission_amount": r.commission_amount,
            "profit": r.profit,
            "warehouse": r.warehouse or "",
            "delivery_method": r.delivery_method or "",
            "cancel_reason": r.cancel_reason or "",
            "cancellation": r.cancellation or "",
        })
    status_info = get_sync_status(tenant_id, str(credential_id))
    store = {"id": str(credential_id),
             "ozon_client_id": credential_service.get_decrypted(tenant_id, str(credential_id))[0]}
    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
        "store": store,
        "last_synced_at": status_info["orders_last_synced_at"],
        "sync_error": status_info["orders_error"],
    }


def list_cached_products(
    tenant_id: str,
    credential_id: str,
    limit: int = 50,
    offset: int = 0,
    lazy_sync: bool = True,
) -> dict:
    """在线商品读取：PG 缓存（未同步 → 懒同步；?refresh=1 强制）。"""
    credential_service.get_decrypted(tenant_id, str(credential_id))
    if lazy_sync and _needs_products_sync(tenant_id, str(credential_id)):
        sync_store(tenant_id, str(credential_id))

    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    with get_engine().connect() as conn:
        total = int(conn.execute(text(
            "SELECT COUNT(*) FROM ozon_products_cache "
            "WHERE tenant_id=:t AND credential_id=:c AND archived=FALSE"
        ), {"t": tenant_id, "c": str(credential_id)}).scalar() or 0)
        rows = conn.execute(text(
            "SELECT product_id, offer_id, name, image, price, stock, currency "
            "FROM ozon_products_cache "
            "WHERE tenant_id=:t AND credential_id=:c AND archived=FALSE "
            "ORDER BY product_id LIMIT :lim OFFSET :off"
        ), {"t": tenant_id, "c": str(credential_id), "lim": limit, "off": offset}).fetchall()

    items = [{
        "product_id": r.product_id,
        "offer_id": r.offer_id,
        "name": r.name,
        "image": r.image,
        "price": r.price,
        "stock": r.stock,
        "currency": r.currency or "",
    } for r in rows]
    status_info = get_sync_status(tenant_id, str(credential_id))
    store = {"id": str(credential_id),
             "ozon_client_id": credential_service.get_decrypted(tenant_id, str(credential_id))[0]}
    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
        "store": store,
        "last_synced_at": status_info["products_last_synced_at"],
        "sync_error": status_info["products_error"],
    }
