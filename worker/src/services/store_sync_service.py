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
import os
import threading
import uuid
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
# 单店同步最大订单拉取页数（防超大店一次同步打爆配额；v4 单页上限 100）
_ORDER_PAGE = 100
_MAX_ORDER_PAGES = 25
# 低库存阈值：库存 < 此值判定为低库存（快照 low_stock_count）
_LOW_STOCK_THRESHOLD = 10
# 各域同步间隔(分钟;PRD §3 频率)
_RETURNS_INTERVAL_MIN = int(os.getenv("STORE_SYNC_RETURNS_INTERVAL_MIN", "30"))
_ACTIONS_INTERVAL_MIN = int(os.getenv("STORE_SYNC_ACTIONS_INTERVAL_MIN", "60"))
_WAREHOUSE_INTERVAL_MIN = int(os.getenv("STORE_SYNC_WAREHOUSE_INTERVAL_MIN", "1440"))
_ANALYTICS_INTERVAL_MIN = int(os.getenv("STORE_SYNC_ANALYTICS_INTERVAL_MIN", "1440"))
_RATING_INTERVAL_MIN = int(os.getenv("STORE_SYNC_RATING_INTERVAL_MIN", "1440"))
_ANALYTICS_METRICS = ["hits_view_search", "hits_view_pdp", "orders_count", "revenue"]
# 同步重入守卫（thread-local，防零订单店 get_store_stats 懒同步递归）
# sync_store → _append_metrics_snapshot → get_store_stats → 懒同步 sync_store → ...
_sync_in_progress = threading.local()


def _sync_guard_active() -> bool:
    return getattr(_sync_in_progress, "active", False)


def _jobs_enabled() -> bool:
    """任务化同步特性开关(PRD M1):1=只读缓存(空不触发同步);0=旧版懒同步。"""
    return os.getenv("STORE_SYNC_JOBS_ENABLED", "1") == "1"


# ──────────────────────────────────────────────
# 同步
# ──────────────────────────────────────────────


def sync_store(tenant_id: str, credential_id: str, force_domains: bool = False) -> dict:
    """同步单个店铺(订单+商品+退货+促销+仓库+分析+评分),逐项容错(一项失败不阻断另一项)。

    credential 归属校验：get_decrypted 跨租户/已吊销 → 404。
    force_domains=True(initial/manual)强制全域;否则各域按自身水位节流。
    末尾追加一条 store_metrics_history 快照（失败静默降级,active_discount_count 取促销域真值）。
    """
    _sync_in_progress.active = True
    try:
        client_id, api_key = credential_service.get_decrypted(tenant_id, credential_id)
        result: dict[str, Any] = {"credential_id": credential_id, "ozon_client_id": client_id}
        result["orders"] = _sync_orders(tenant_id, credential_id, client_id, api_key)
        result["products"] = _sync_products(tenant_id, credential_id, client_id, api_key)
        result["returns"] = _sync_returns(tenant_id, credential_id, client_id, api_key, force_domains)
        result["actions"] = _sync_actions(tenant_id, credential_id, client_id, api_key, force_domains)
        result["warehouse"] = _sync_warehouse(tenant_id, credential_id, client_id, api_key, force_domains)
        result["analytics"] = _sync_analytics(tenant_id, credential_id, client_id, api_key, force_domains)
        result["rating"] = _sync_rating(tenant_id, credential_id, client_id, api_key, force_domains)
        _append_metrics_snapshot(tenant_id, credential_id)
        return result
    finally:
        _sync_in_progress.active = False


# ──────────────────────────────────────────────
# 各域水位(domain_state JSONB)与退货/促销/仓库/分析/评分同步
# ──────────────────────────────────────────────


def _domain_state_row(tenant_id: str, credential_id: str) -> dict:
    with get_engine().connect() as conn:
        row = conn.execute(text(
            "SELECT domain_state FROM credential_sync_state "
            "WHERE tenant_id=:t AND credential_id=:c"
        ), {"t": tenant_id, "c": str(credential_id)}).fetchone()
    if row is None or not row.domain_state:
        return {}
    return row.domain_state if isinstance(row.domain_state, dict) else {}


def _domain_state_update(tenant_id: str, credential_id: str, domain: str, **fields) -> None:
    """read-modify-write domain_state[domain] 字段(单事务)。"""
    state = _domain_state_row(tenant_id, credential_id)
    state.setdefault(domain, {}).update(fields)
    with get_engine().begin() as conn:
        conn.execute(text(
            """
            INSERT INTO credential_sync_state
                (tenant_id, credential_id, domain_state, orders_error, products_error, updated_at)
            VALUES (:t, :c, CAST(:ds AS jsonb), '', '', NOW())
            ON CONFLICT (tenant_id, credential_id) DO UPDATE SET
                domain_state = EXCLUDED.domain_state,
                updated_at = NOW()
            """
        ), {"t": tenant_id, "c": str(credential_id), "ds": json.dumps(state, ensure_ascii=False)})


def _domain_due(tenant_id: str, credential_id: str, domain: str,
                interval_min: int, force: bool) -> bool:
    if force:
        return True
    state = _domain_state_row(tenant_id, credential_id).get(domain) or {}
    last = state.get("last_synced_at")
    if not last:
        return True
    try:
        last_dt = datetime.datetime.fromisoformat(str(last))
    except (ValueError, TypeError):
        return True
    return datetime.datetime.now(datetime.timezone.utc) - last_dt >= datetime.timedelta(minutes=interval_min)


def _sync_returns(tenant_id: str, credential_id: str, client_id: str, api_key: str,
                  force: bool = False) -> dict:
    """退货同步(/v1/returns/list,has_next 分页,30min 节流)。"""
    from utils.ozon_client import ozon_post
    if not _domain_due(tenant_id, credential_id, "returns", _RETURNS_INTERVAL_MIN, force):
        return {"synced": 0, "error": "", "skipped": True}
    state = _domain_state_row(tenant_id, credential_id).get("returns") or {}
    last = state.get("last_synced_at")
    try:
        since_dt = datetime.datetime.fromisoformat(str(last)) if last else \
            datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=30)
    except (ValueError, TypeError):
        since_dt = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=30)
    to_dt = datetime.datetime.now(datetime.timezone.utc)
    since = since_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    to = to_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    synced = 0
    offset = 0
    try:
        for _page in range(10):
            resp = ozon_post(client_id, api_key, "/v1/returns/list",
                             {"filter": {"since": since, "to": to}, "limit": 100, "offset": offset},
                             timeout=30, language="RU")
            items = resp.get("returns") or (resp.get("result") or {}).get("returns") or []
            if not items:
                break
            _upsert_returns(tenant_id, credential_id, items)
            synced += len(items)
            if not resp.get("has_next") and not (resp.get("result") or {}).get("has_next"):
                break
            offset += len(items)
    except Exception as exc:
        logger.warning("退货同步失败 tenant=%s store=%s: %s",
                       tenant_id, credential_id, str(exc)[:200])
        _domain_state_update(tenant_id, credential_id, "returns", error=str(exc)[:200])
        return {"synced": synced, "error": str(exc)[:120]}
    _domain_state_update(tenant_id, credential_id, "returns",
                         last_synced_at=to_dt.isoformat(), error="")
    return {"synced": synced, "error": ""}


def _upsert_returns(tenant_id: str, credential_id: str, items: list) -> None:
    rows = []
    for it in items:
        if not isinstance(it, dict) or not it.get("id"):
            continue
        rows.append({
            "tenant_id": tenant_id,
            "credential_id": str(credential_id),
            "return_id": int(it["id"]),
            "posting_number": str(it.get("posting_number") or ""),
            "order_id": str(it.get("order_id") or it.get("order_number") or ""),
            "return_type": str(it.get("type") or ""),
            "schema": str(it.get("schema") or ""),
            "reason": str(it.get("return_reason_name") or ""),
            "compensation_status": str(it.get("compensation_status") or ""),
            "product": json.dumps(it.get("product"), ensure_ascii=False) if it.get("product") else None,
            "status": str(it.get("status") or ""),
            "raw": json.dumps(it, ensure_ascii=False),
        })
    if not rows:
        return
    with get_engine().begin() as conn:
        conn.execute(text(
            """
            INSERT INTO ozon_returns_cache
                (tenant_id, credential_id, return_id, posting_number, order_id, return_type,
                 schema, reason, compensation_status, product, status, raw, synced_at)
            VALUES
                (:tenant_id, :credential_id, :return_id, :posting_number, :order_id, :return_type,
                 :schema, :reason, :compensation_status, CAST(:product AS jsonb), :status,
                 CAST(:raw AS jsonb), NOW())
            ON CONFLICT (tenant_id, credential_id, return_id) DO UPDATE SET
                posting_number = EXCLUDED.posting_number,
                order_id = EXCLUDED.order_id,
                return_type = EXCLUDED.return_type,
                schema = EXCLUDED.schema,
                reason = EXCLUDED.reason,
                compensation_status = EXCLUDED.compensation_status,
                product = EXCLUDED.product,
                status = EXCLUDED.status,
                raw = EXCLUDED.raw,
                synced_at = NOW()
            """
        ), rows)


def _sync_actions(tenant_id: str, credential_id: str, client_id: str, api_key: str,
                  force: bool = False) -> dict:
    """促销/活动计数(/v1/actions,60min 节流)→ domain_state.actions.count(快照真值)。"""
    from utils.ozon_client import ozon_post
    if not _domain_due(tenant_id, credential_id, "actions", _ACTIONS_INTERVAL_MIN, force):
        return {"count": None, "error": "", "skipped": True}
    try:
        resp = ozon_post(client_id, api_key, "/v1/actions", {"limit": 100, "offset": 0},
                         timeout=30, language="RU")
        result = resp.get("result") or resp
        actions = result.get("actions") or result.get("items") or []
        count = len(actions)
    except Exception as exc:
        logger.warning("促销同步失败 tenant=%s store=%s: %s",
                       tenant_id, credential_id, str(exc)[:200])
        _domain_state_update(tenant_id, credential_id, "actions", error=str(exc)[:200])
        return {"count": None, "error": str(exc)[:120]}
    _domain_state_update(tenant_id, credential_id, "actions",
                         count=count, last_synced_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                         error="")
    return {"count": count, "error": ""}


def _sync_warehouse(tenant_id: str, credential_id: str, client_id: str, api_key: str,
                    force: bool = False) -> dict:
    """仓库字典(/v2/warehouse/list,24h 节流)。"""
    from utils.ozon_client import ozon_post
    if not _domain_due(tenant_id, credential_id, "warehouse", _WAREHOUSE_INTERVAL_MIN, force):
        return {"synced": 0, "error": "", "skipped": True}
    try:
        resp = ozon_post(client_id, api_key, "/v2/warehouse/list", {}, timeout=30, language="RU")
        items = resp.get("warehouses") or []
        _upsert_warehouses(tenant_id, credential_id, items)
    except Exception as exc:
        logger.warning("仓库同步失败 tenant=%s store=%s: %s",
                       tenant_id, credential_id, str(exc)[:200])
        _domain_state_update(tenant_id, credential_id, "warehouse", error=str(exc)[:200])
        return {"synced": 0, "error": str(exc)[:120]}
    _domain_state_update(tenant_id, credential_id, "warehouse",
                         last_synced_at=datetime.datetime.now(datetime.timezone.utc).isoformat(), error="")
    return {"synced": len(items), "error": ""}


def _upsert_warehouses(tenant_id: str, credential_id: str, items: list) -> None:
    rows = []
    for it in items:
        if not isinstance(it, dict) or not it.get("warehouse_id"):
            continue
        rows.append({
            "tenant_id": tenant_id,
            "credential_id": str(credential_id),
            "warehouse_id": int(it["warehouse_id"]),
            "name": str(it.get("name") or ""),
            "is_rfbs": bool(it.get("is_rfbs", False)),
            "raw": json.dumps(it, ensure_ascii=False),
        })
    if not rows:
        return
    with get_engine().begin() as conn:
        conn.execute(text(
            """
            INSERT INTO warehouse_cache
                (tenant_id, credential_id, warehouse_id, name, is_rfbs, raw, synced_at)
            VALUES (:tenant_id, :credential_id, :warehouse_id, :name, :is_rfbs,
                    CAST(:raw AS jsonb), NOW())
            ON CONFLICT (tenant_id, credential_id, warehouse_id) DO UPDATE SET
                name = EXCLUDED.name,
                is_rfbs = EXCLUDED.is_rfbs,
                raw = EXCLUDED.raw,
                synced_at = NOW()
            """
        ), rows)


def _sync_analytics(tenant_id: str, credential_id: str, client_id: str, api_key: str,
                    force: bool = False) -> dict:
    """店铺分析日表(/v1/analytics/data,日级节流,metrics 与请求顺序对齐)。"""
    from utils.ozon_client import ozon_post
    if not _domain_due(tenant_id, credential_id, "analytics", _ANALYTICS_INTERVAL_MIN, force):
        return {"synced": 0, "error": "", "skipped": True}
    state = _domain_state_row(tenant_id, credential_id).get("analytics") or {}
    last = state.get("last_synced_at")
    try:
        since_dt = datetime.datetime.fromisoformat(str(last)) if last else \
            datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=30)
    except (ValueError, TypeError):
        since_dt = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=30)
    to_dt = datetime.datetime.now(datetime.timezone.utc)
    try:
        resp = ozon_post(client_id, api_key, "/v1/analytics/data", {
            "date_from": since_dt.strftime("%Y-%m-%d"),
            "date_to": to_dt.strftime("%Y-%m-%d"),
            "metrics": _ANALYTICS_METRICS,
            "dimension": ["day"],
            "limit": 200, "offset": 0,
        }, timeout=30, language="RU")
        result = resp.get("result") or {}
        rows = result.get("data") or []
        synced = _upsert_analytics_rows(tenant_id, credential_id, rows)
    except Exception as exc:
        logger.warning("分析同步失败 tenant=%s store=%s: %s",
                       tenant_id, credential_id, str(exc)[:200])
        _domain_state_update(tenant_id, credential_id, "analytics", error=str(exc)[:200])
        return {"synced": 0, "error": str(exc)[:120]}
    _domain_state_update(tenant_id, credential_id, "analytics",
                         last_synced_at=to_dt.isoformat(), error="")
    return {"synced": synced, "error": ""}


def _upsert_analytics_rows(tenant_id: str, credential_id: str, rows: list) -> int:
    synced = 0
    for row in rows:
        dims = row.get("dimensions") or []
        metrics = row.get("metrics") or []
        if not dims or not metrics:
            continue
        stat_date = str(dims[0].get("id") or "")[:10] if isinstance(dims[0], dict) else ""
        if not stat_date:
            continue
        for metric, value in zip(_ANALYTICS_METRICS, metrics):
            if value is None:
                continue
            with get_engine().begin() as conn:
                conn.execute(text(
                    """
                    INSERT INTO ozon_store_analytics_daily
                        (tenant_id, credential_id, stat_date, metric, value)
                    VALUES (:t, :c, :d, :m, :v)
                    ON CONFLICT (tenant_id, credential_id, stat_date, metric) DO UPDATE SET
                        value = EXCLUDED.value
                    """
                ), {"t": tenant_id, "c": str(credential_id), "d": stat_date,
                    "m": metric, "v": float(value)})
            synced += 1
    return synced


def _sync_rating(tenant_id: str, credential_id: str, client_id: str, api_key: str,
                 force: bool = False) -> dict:
    """评分(/v1/rating/summary,日级节流)→ credentials.rating_*。"""
    from utils.ozon_client import ozon_post
    if not _domain_due(tenant_id, credential_id, "rating", _RATING_INTERVAL_MIN, force):
        return {"rating": None, "error": "", "skipped": True}
    try:
        resp = ozon_post(client_id, api_key, "/v1/rating/summary", {}, timeout=30, language="RU")
        groups = resp.get("groups") or []
        localization_index = resp.get("localization_index")
        rating_total = None
        for g in groups:
            for it in (g.get("items") or []):
                try:
                    v = float(it.get("current_value"))
                    if v > 0:
                        rating_total = v
                        break
                except (TypeError, ValueError):
                    continue
            if rating_total is not None:
                break
        with get_engine().begin() as conn:
            conn.execute(text(
                """
                UPDATE credentials SET
                    rating_total = :r,
                    rating_localization_index = :li,
                    rating_items = CAST(:ri AS jsonb),
                    rating_updated_at = NOW(),
                    updated_at = NOW()
                WHERE tenant_id=:t AND id::text=:c
                """
            ), {"r": rating_total, "li": localization_index,
                "ri": json.dumps(groups, ensure_ascii=False),
                "t": tenant_id, "c": str(credential_id)})
    except Exception as exc:
        logger.warning("评分同步失败 tenant=%s store=%s: %s",
                       tenant_id, credential_id, str(exc)[:200])
        _domain_state_update(tenant_id, credential_id, "rating", error=str(exc)[:200])
        return {"rating": None, "error": str(exc)[:120]}
    _domain_state_update(tenant_id, credential_id, "rating",
                         rating=rating_total,
                         last_synced_at=datetime.datetime.now(datetime.timezone.utc).isoformat(), error="")
    return {"rating": rating_total, "error": ""}


def _append_metrics_snapshot(tenant_id: str, credential_id: str) -> None:
    """同步末尾追加一条 store_metrics_history 快照（append-only，失败静默降级）。

    profit_amount/profit_rate 写 NULL（OzonProductCache 无成本字段，不编造）。
    get_store_stats 的懒同步经重构后尊重重入守卫，零订单店不会递归。
    """
    try:
        stats = get_store_stats(tenant_id, credential_id)
        products = _product_counts(tenant_id, credential_id)
        active_discount_count = int(
            (_domain_state_row(tenant_id, credential_id).get("actions") or {}).get("count") or 0)
        snapshot_at = datetime.datetime.now(datetime.timezone.utc)
        raw = json.dumps({"stats": stats, "products": products}, ensure_ascii=False)
        with get_engine().begin() as conn:
            conn.execute(text(
                """
                INSERT INTO store_metrics_history
                    (tenant_id, credential_id, store_id, snapshot_at, order_count,
                     sales_amount, commission_amount, profit_amount, product_count,
                     low_stock_count, active_discount_count, profit_rate, raw)
                VALUES
                    (:tenant_id, :credential_id, :store_id, :snapshot_at, :order_count,
                     :sales_amount, :commission_amount, NULL, :product_count,
                     :low_stock_count, :active_discount_count, NULL, CAST(:raw AS jsonb))
                """
            ), {
                "tenant_id": tenant_id,
                "credential_id": uuid.UUID(str(credential_id)),
                "store_id": str(credential_id),
                "snapshot_at": snapshot_at,
                "order_count": stats.get("today_orders") or 0,
                "sales_amount": stats.get("today_sales_amount"),
                "commission_amount": stats.get("today_commission"),
                "product_count": products["product_count"],
                "low_stock_count": products["low_stock_count"],
                "active_discount_count": active_discount_count,
                "raw": raw,
            })
        logger.info("店铺指标快照落历史 store=%s orders=%s products=%s",
                    credential_id, stats.get("today_orders"), products["product_count"])
    except Exception as exc:
        logger.warning("店铺指标快照失败（静默降级）store=%s: %s",
                       credential_id, str(exc)[:200])


def _product_counts(tenant_id: str, credential_id: str) -> dict:
    """商品数/低库存数（未归档，stock < _LOW_STOCK_THRESHOLD 计低库存）。"""
    with get_engine().connect() as conn:
        row = conn.execute(text(
            "SELECT COUNT(*), "
            "COUNT(*) FILTER (WHERE stock IS NOT NULL AND stock < :low_stock) "
            "FROM ozon_products_cache "
            "WHERE tenant_id=:t AND credential_id=:c AND archived=FALSE"
        ), {"t": tenant_id, "c": str(credential_id), "low_stock": _LOW_STOCK_THRESHOLD}).fetchone()
    return {"product_count": int(row[0] or 0), "low_stock_count": int(row[1] or 0)}


def _sync_orders(tenant_id: str, credential_id: str, client_id: str, api_key: str) -> dict:
    """订单增量同步(PRD M1 续传)：游标分页拉全 → upsert。

    v4 兼容（I-11）：/v4/posting/fbs/list 用 cursor/has_next 分页（无 offset/total），
    limit 上限 100；T4.3：同步时按 product_id 批量拉主图缓存进 products JSONB。
    续传语义(PRD §5.3)：每 job 预算 _MAX_ORDER_PAGES 页;预算耗尽且 has_next →
    持久化当前 since/to 窗口 + cursor + orders_sync_incomplete=true;
    续传用同一窗口+cursor 继续,完成才清标志并把 orders_last_synced_at 推进到窗口 to。
    中途失败不推进水位(保留 incomplete,下次续传)。
    """
    from utils.ozon_client import ozon_post

    state = _sync_state_row(tenant_id, credential_id)
    incomplete = bool(state and state.orders_sync_incomplete)
    if incomplete and state and state.orders_window_since and state.orders_window_to:
        since_dt = state.orders_window_since
        to_dt = state.orders_window_to
        cursor = state.orders_sync_cursor or ""
    else:
        since_dt = _orders_since(tenant_id, credential_id)
        to_dt = datetime.datetime.now(datetime.timezone.utc)
        cursor = ""
    since = since_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    to = to_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    synced = 0
    truncated = False
    for _page in range(_MAX_ORDER_PAGES):
        try:
            resp = ozon_post(
                client_id, api_key, "/v4/posting/fbs/list",
                {
                    "sort_dir": "ASC",
                    "filter": {"since": since, "to": to},
                    "limit": _ORDER_PAGE,
                    "cursor": cursor,
                    "with": {"analytics_data": True, "financial_data": True},
                },
                timeout=30, language="RU",
            )
        except Exception as exc:
            logger.warning("订单同步拉取失败 tenant=%s store=%s: %s",
                           tenant_id, credential_id, str(exc)[:200])
            if cursor:
                _persist_orders_continuation(tenant_id, credential_id, since_dt, to_dt, cursor)
            _set_orders_error_no_watermark(tenant_id, credential_id,
                                           f"拉取失败: {str(exc)[:120]}")
            return {"synced": synced, "error": str(exc)[:120], "incomplete": True}

        # v4 响应扁平（cursor/has_next/postings）；v3 包 result——兼容两者
        result = resp.get("result") or resp
        postings = result.get("postings") or []
        if not postings:
            break
        _upsert_orders(tenant_id, credential_id, postings, client_id, api_key)
        synced += len(postings)
        if not result.get("has_next"):
            break
        cursor = result.get("cursor") or ""
        if not cursor:
            break
        if _page >= _MAX_ORDER_PAGES - 1:
            # 预算耗尽且 has_next → 持久化续传点(不推进水位)
            _persist_orders_continuation(tenant_id, credential_id, since_dt, to_dt, cursor)
            truncated = True
            break

    if not truncated:
        _complete_orders_window(tenant_id, credential_id, to_dt)
    return {"synced": synced, "error": "", "incomplete": truncated}


def _persist_orders_continuation(
    tenant_id: str, credential_id: str,
    since_dt: datetime.datetime, to_dt: datetime.datetime, cursor: str,
) -> None:
    """持久化订单续传点(窗口+cursor+incomplete=true),不推进 orders_last_synced_at。"""
    with get_engine().begin() as conn:
        conn.execute(text(
            """
            INSERT INTO credential_sync_state
                (tenant_id, credential_id, orders_window_since, orders_window_to,
                 orders_sync_cursor, orders_sync_incomplete, orders_error, products_error, updated_at)
            VALUES (:t, :c, :s, :to, :cur, TRUE, '', '', NOW())
            ON CONFLICT (tenant_id, credential_id) DO UPDATE SET
                orders_window_since = EXCLUDED.orders_window_since,
                orders_window_to = EXCLUDED.orders_window_to,
                orders_sync_cursor = EXCLUDED.orders_sync_cursor,
                orders_sync_incomplete = TRUE,
                orders_error = '',
                updated_at = NOW()
            """
        ), {"t": tenant_id, "c": credential_id, "s": since_dt, "to": to_dt, "cur": cursor})


def _complete_orders_window(
    tenant_id: str, credential_id: str, window_to: datetime.datetime,
) -> None:
    """窗口完成:清续传标志,orders_last_synced_at 推进到窗口 to,清错误。"""
    with get_engine().begin() as conn:
        conn.execute(text(
            """
            INSERT INTO credential_sync_state
                (tenant_id, credential_id, orders_last_synced_at, orders_sync_incomplete,
                 orders_sync_cursor, orders_window_since, orders_window_to,
                 orders_error, products_error, updated_at)
            VALUES (:t, :c, :wto, FALSE, NULL, NULL, NULL, '', '', NOW())
            ON CONFLICT (tenant_id, credential_id) DO UPDATE SET
                orders_last_synced_at = EXCLUDED.orders_last_synced_at,
                orders_sync_incomplete = FALSE,
                orders_sync_cursor = NULL,
                orders_window_since = NULL,
                orders_window_to = NULL,
                orders_error = '',
                updated_at = NOW()
            """
        ), {"t": tenant_id, "c": credential_id, "wto": window_to})


def _set_orders_error_no_watermark(tenant_id: str, credential_id: str, error: str) -> None:
    """订单错误回写,但**不推进** orders_last_synced_at(失败窗口可续传)。"""
    with get_engine().begin() as conn:
        conn.execute(text(
            """
            INSERT INTO credential_sync_state
                (tenant_id, credential_id, orders_error, products_error, updated_at)
            VALUES (:t, :c, :e, '', NOW())
            ON CONFLICT (tenant_id, credential_id) DO UPDATE SET
                orders_error = :e,
                updated_at = NOW()
            """
        ), {"t": tenant_id, "c": credential_id, "e": error})


def _orders_since(tenant_id: str, credential_id: str) -> datetime.datetime:
    """增量起点：上次同步 − 1h 重叠；从未同步 → 90 天前。"""
    now = datetime.datetime.now(datetime.timezone.utc)
    row = _sync_state_row(tenant_id, credential_id)
    last = (row.orders_last_synced_at if row else None)
    if last is not None:
        return last - datetime.timedelta(hours=_SYNC_OVERLAP_HOURS)
    return now - datetime.timedelta(days=SYNC_ORDERS_WINDOW_DAYS)


def _upsert_orders(tenant_id: str, credential_id: str, postings: list,
                   client_id: Optional[str] = None, api_key: Optional[str] = None) -> None:
    """订单 upsert（ON CONFLICT tenant+store+posting_number DO UPDATE 覆盖状态）。

    T4.3：传入 client_id/api_key 时按 product_id 批量拉主图，把 image 缓存进 products JSONB
    （幂等 fetch，列表读取秒开不再透传 Ozon）。
    """
    images: dict = {}
    if client_id and api_key:
        images = order_service.fetch_order_images(client_id, api_key, postings)
    # PRD M3: 行级成本(手动 order_notes 覆盖 > product_costs)+ fx → real_profit
    from services.product_cost_service import get_latest_fx_rate
    norms = []
    for p in postings:
        if isinstance(p, dict) and p.get("posting_number"):
            norms.append((p, order_service._normalize_posting(p)))
    costs_map = _load_line_costs(tenant_id, credential_id, norms)
    notes_map = _load_order_note_overrides(tenant_id, credential_id, postings)
    fx = get_latest_fx_rate()
    line_rows: list[dict] = []
    rows = []
    for p, norm in norms:
        order_service.apply_product_images(norm.get("products") or [], images)
        created = None
        if norm.get("created_at"):
            try:
                created = datetime.datetime.fromisoformat(norm["created_at"])
            except (ValueError, TypeError):
                created = None
        pn = norm["posting_number"]
        total_cost = 0.0
        all_have = True
        for line in norm.get("products") or []:
            pid = str(line.get("product_id") or "")
            if not pid:
                continue
            cost = notes_map.get((pn, pid))
            if cost is None and pid in costs_map:
                cost = costs_map[pid].get("purchase_cost")
            revenue = _line_revenue(line)
            if cost is None or fx is None:
                line_rows.append({
                    "tenant_id": tenant_id, "credential_id": credential_id,
                    "posting_number": pn, "product_id": pid,
                    "sku": str(line.get("sku") or ""),
                    "source_url": costs_map.get(pid, {}).get("purchase_url", ""),
                    "source_cost": None, "fx_rate": fx, "revenue_rub": revenue,
                })
                all_have = False
            else:
                cost_v = float(cost)
                total_cost += cost_v * float(fx)
                line_rows.append({
                    "tenant_id": tenant_id, "credential_id": credential_id,
                    "posting_number": pn, "product_id": pid,
                    "sku": str(line.get("sku") or ""),
                    "source_url": costs_map.get(pid, {}).get("purchase_url", ""),
                    "source_cost": cost_v, "fx_rate": fx, "revenue_rub": revenue,
                })
        real_profit = None
        if all_have and norm.get("total_amount") is not None:
            real_profit = round(
                float(norm["total_amount"]) - float(norm.get("commission_amount") or 0) - total_cost, 2)
        rows.append({
            "tenant_id": tenant_id,
            "credential_id": credential_id,
            "posting_number": pn,
            "status": norm["status"],
            "raw_status": norm["raw_status"],
            # executemany 下 CAST(:x AS jsonb) 无法适配 dict——预序列化 JSON 字符串
            "products": json.dumps(norm["products"], ensure_ascii=False),
            "product_count": norm["product_count"],
            "total_amount": norm["total_amount"],
            "commission_amount": norm["commission_amount"],
            "profit": norm["profit"],
            "real_profit": real_profit,
            "warehouse": norm["warehouse"],
            "delivery_method": norm["delivery_method"],
            "cancel_reason": norm["cancel_reason"],
            "cancellation": norm["cancellation"],
            "order_created_at": created,
        })
    if not rows:
        return
    with get_engine().begin() as conn:
        if line_rows:
            conn.execute(text(
                """
                INSERT INTO order_line_costs
                    (posting_number, tenant_id, credential_id, product_id, sku, source_url,
                     source_cost, fx_rate, revenue_rub, cost_version, updated_at)
                VALUES
                    (:posting_number, :tenant_id, :credential_id, :product_id, :sku, :source_url,
                     :source_cost, :fx_rate, :revenue_rub, 1, NOW())
                ON CONFLICT (posting_number, product_id) DO UPDATE SET
                    source_url = EXCLUDED.source_url,
                    source_cost = EXCLUDED.source_cost,
                    fx_rate = EXCLUDED.fx_rate,
                    revenue_rub = EXCLUDED.revenue_rub,
                    cost_version = order_line_costs.cost_version + 1,
                    updated_at = NOW()
                """
            ), line_rows)
        conn.execute(text(
            """
            INSERT INTO ozon_orders_cache
                (tenant_id, credential_id, posting_number, status, raw_status,
                 products, product_count, total_amount, commission_amount, profit, real_profit,
                 warehouse, delivery_method, cancel_reason, cancellation, order_created_at, synced_at)
            VALUES
                (:tenant_id, :credential_id, :posting_number, :status, :raw_status,
                 CAST(:products AS jsonb), :product_count, :total_amount, :commission_amount, :profit, :real_profit,
                 :warehouse, :delivery_method, :cancel_reason, :cancellation, :order_created_at, NOW())
            ON CONFLICT (tenant_id, credential_id, posting_number) DO UPDATE SET
                status = EXCLUDED.status,
                raw_status = EXCLUDED.raw_status,
                products = EXCLUDED.products,
                product_count = EXCLUDED.product_count,
                total_amount = EXCLUDED.total_amount,
                commission_amount = EXCLUDED.commission_amount,
                profit = EXCLUDED.profit,
                real_profit = EXCLUDED.real_profit,
                warehouse = EXCLUDED.warehouse,
                delivery_method = EXCLUDED.delivery_method,
                cancel_reason = EXCLUDED.cancel_reason,
                cancellation = EXCLUDED.cancellation,
                order_created_at = EXCLUDED.order_created_at,
                synced_at = NOW()
            """
        ), rows)


def _load_line_costs(tenant_id: str, credential_id: str, norms: list) -> dict:
    """批量加载商品成本(product_costs)→ {product_id: {purchase_cost, purchase_url}}。"""
    ids = {str(line.get("product_id")) for _, norm in norms
           for line in (norm.get("products") or []) if line.get("product_id")}
    if not ids:
        return {}
    with get_engine().connect() as conn:
        rows = conn.execute(text(
            "SELECT product_id, purchase_cost, purchase_url FROM product_costs "
            "WHERE tenant_id=:t AND credential_id=:c AND product_id = ANY(:ids)"
        ), {"t": tenant_id, "c": str(credential_id), "ids": list(ids)}).fetchall()
    return {r.product_id: {"purchase_cost": r.purchase_cost, "purchase_url": r.purchase_url}
            for r in rows}


def _load_order_note_overrides(tenant_id: str, credential_id: str, postings: list) -> dict:
    """行级手动成本(order_notes.source_cost)→ {(posting, product_id): cost}。"""
    pns = [str(p.get("posting_number")) for p in postings if p.get("posting_number")]
    if not pns:
        return {}
    with get_engine().connect() as conn:
        rows = conn.execute(text(
            "SELECT posting_number, product_id, source_cost FROM order_notes "
            "WHERE tenant_id=:t AND posting_number = ANY(:pns) "
            "AND product_id <> '' AND source_cost IS NOT NULL"
        ), {"t": tenant_id, "pns": pns}).fetchall()
    return {(r.posting_number, r.product_id): r.source_cost for r in rows}


def _line_revenue(line: dict) -> Optional[float]:
    price = line.get("price")
    qty = line.get("quantity")
    if price is None or qty is None:
        return None
    try:
        return round(float(price) * int(qty), 2)
    except (TypeError, ValueError):
        return None


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
    ids = [int(it["product_id"]) for it in items
           if isinstance(it, dict) and str(it.get("product_id") or "").isdigit()]
    return _fetch_info_map_by_ids(client_id, api_key, ids)


def _fetch_info_map_by_ids(client_id: str, api_key: str, ids: list) -> dict:
    """按 int product_id 批量拉 /v3/product/info/list → {str(product_id): info}。

    与 _fetch_info_map 同源（限流退避 3 次 1s/2s）；供订单商品图按 product_id 复用（T4.3）。
    PRD M0 实测:响应是顶层 items[]、商品项用 id 字段(M1 修正;兼容旧 result.items + product_id)。
    """
    from utils.ozon_client import ozon_post

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
        info_items = (info_resp.get("items")
                      or (info_resp.get("result") or {}).get("items") or [])
        if info_items:
            out: dict = {}
            for it in info_items:
                if not isinstance(it, dict):
                    continue
                pid = it.get("id") or it.get("product_id")
                if pid is not None:
                    out[str(pid)] = it
            return out
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
        # PRD M0 实测:price/old_price/min_price 可能是字符串或对象,min_price 可能空串
        price_el, old_price_el, min_price_el = _extract_tier_prices(info)
        archived = bool(info.get("is_archived") or info.get("is_autoarchived"))
        errors = info.get("errors") or []
        status = "archived" if archived else ("error" if errors else "visible")
        rows.append({
            "tenant_id": tenant_id,
            "credential_id": credential_id,
            "product_id": pid,
            "offer_id": str(it.get("offer_id") or ""),
            "name": str(info.get("name") or it.get("offer_id") or ""),
            "image": (info.get("images") or [None])[0] if isinstance(info.get("images"), list) and info.get("images") else None,
            "price": price_el,
            "old_price": old_price_el,
            "min_price": min_price_el,
            "stock": (info.get("stocks") or {}).get("present") if isinstance(info.get("stocks"), dict) else None,
            "currency": "",
            "status": status,
            "error": json.dumps(errors, ensure_ascii=False) if errors else None,
            "archived": archived,
            "archived_at": datetime.datetime.now(datetime.timezone.utc).isoformat() if archived else None,
        })
    if not rows:
        return
    with get_engine().begin() as conn:
        conn.execute(text(
            """
            INSERT INTO ozon_products_cache
                (tenant_id, credential_id, product_id, offer_id, name, image,
                 price, old_price, min_price, stock, currency, status, error,
                 archived, archived_at, synced_at)
            VALUES
                (:tenant_id, :credential_id, :product_id, :offer_id, :name, :image,
                 :price, :old_price, :min_price, :stock, :currency, :status,
                 CAST(:error AS jsonb), :archived, :archived_at, NOW())
            ON CONFLICT (tenant_id, credential_id, product_id) DO UPDATE SET
                offer_id = EXCLUDED.offer_id,
                name = EXCLUDED.name,
                image = EXCLUDED.image,
                price = EXCLUDED.price,
                old_price = EXCLUDED.old_price,
                min_price = EXCLUDED.min_price,
                stock = EXCLUDED.stock,
                currency = EXCLUDED.currency,
                status = EXCLUDED.status,
                error = EXCLUDED.error,
                archived = EXCLUDED.archived,
                archived_at = EXCLUDED.archived_at,
                synced_at = NOW()
            """
        ), rows)


def _extract_tier_prices(info: dict) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """三档价归一化:price/old_price/min_price 兼容字符串/空串/对象三种形态。"""
    def _to_num(v) -> Optional[float]:
        if v is None or v == "":
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    price_raw = info.get("price")
    if isinstance(price_raw, dict):
        price = _to_num(price_raw.get("price") or price_raw.get("marketing_price"))
        old_price = _to_num(price_raw.get("old_price"))
        min_price = _to_num(price_raw.get("min_price"))
        if old_price is None:
            old_price = _to_num(info.get("old_price"))
        if min_price is None:
            min_price = _to_num(info.get("min_price"))
        return price, old_price, min_price
    return _to_num(price_raw), _to_num(info.get("old_price")), _to_num(info.get("min_price"))


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
            "orders_error, products_error, orders_sync_incomplete, "
            "orders_window_since, orders_window_to, orders_sync_cursor "
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
    """店铺同步状态(webui 展示最后同步时间/错误/配置/在途 job/stale)。"""
    row = _sync_state_row(tenant_id, credential_id)
    if row is None:
        base: dict = {"credential_id": credential_id,
                      "orders_last_synced_at": None, "products_last_synced_at": None,
                      "orders_error": "", "products_error": "",
                      "last_success_at": None, "consecutive_failures": 0}
    else:
        base = {
            "credential_id": credential_id,
            "orders_last_synced_at": row.orders_last_synced_at.isoformat() if row.orders_last_synced_at else None,
            "products_last_synced_at": row.products_last_synced_at.isoformat() if row.products_last_synced_at else None,
            "orders_error": row.orders_error or "",
            "products_error": row.products_error or "",
            "last_success_at": row.last_success_at.isoformat() if getattr(row, "last_success_at", None) else None,
            "consecutive_failures": int(getattr(row, "consecutive_failures", 0) or 0),
        }
    if not _jobs_enabled():
        return base
    from services import store_sync_jobs
    current = store_sync_jobs.current_job(tenant_id, str(credential_id))
    cfg = _credential_sync_cfg(tenant_id, str(credential_id))
    failures = int(base["consecutive_failures"] or 0)
    last_success = base.get("last_success_at")
    stale = failures >= 3
    if last_success:
        try:
            last_dt = datetime.datetime.fromisoformat(last_success)
            if datetime.datetime.now(datetime.timezone.utc) - last_dt > datetime.timedelta(hours=2):
                stale = True
        except (ValueError, TypeError):
            stale = True
    base.update({
        "sync_enabled": cfg["sync_enabled"],
        "sync_interval_minutes": cfg["sync_interval_minutes"],
        "sync_products_interval_minutes": cfg["sync_products_interval_minutes"],
        "current_job": current,
        "is_stale": stale,
    })
    return base


def _credential_sync_cfg(tenant_id: str, credential_id: str) -> dict:
    """凭证同步配置(PRD M1:sync_enabled + 分节间隔)。"""
    with get_engine().connect() as conn:
        row = conn.execute(text(
            "SELECT sync_enabled, sync_interval_minutes, sync_products_interval_minutes "
            "FROM credentials WHERE tenant_id=:t AND id::text=:c"
        ), {"t": tenant_id, "c": str(credential_id)}).fetchone()
    if row is None:
        return {"sync_enabled": True, "sync_interval_minutes": 15,
                "sync_products_interval_minutes": 30}
    return {
        "sync_enabled": bool(row.sync_enabled),
        "sync_interval_minutes": int(row.sync_interval_minutes or 15),
        "sync_products_interval_minutes": int(row.sync_products_interval_minutes or 30),
    }


def update_sync_config(
    tenant_id: str, credential_id: str, *,
    sync_enabled: Optional[bool] = None,
    sync_interval_minutes: Optional[int] = None,
    sync_products_interval_minutes: Optional[int] = None,
) -> dict:
    """更新店铺同步配置(PRD §4 PATCH /stores/{id}/sync-config;免 api_key)。"""
    with get_engine().begin() as conn:
        row = conn.execute(text(
            """
            UPDATE credentials SET
                sync_enabled = COALESCE(:e, sync_enabled),
                sync_interval_minutes = COALESCE(:o, sync_interval_minutes),
                sync_products_interval_minutes = COALESCE(:p, sync_products_interval_minutes),
                updated_at = NOW()
            WHERE tenant_id=:t AND id::text=:c AND status='active'
            RETURNING sync_enabled, sync_interval_minutes, sync_products_interval_minutes
            """
        ), {
            "t": tenant_id, "c": str(credential_id), "e": sync_enabled,
            "o": sync_interval_minutes, "p": sync_products_interval_minutes,
        }).fetchone()
    if row is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="凭证不存在或已吊销")
    return {
        "sync_enabled": bool(row.sync_enabled),
        "sync_interval_minutes": int(row.sync_interval_minutes or 15),
        "sync_products_interval_minutes": int(row.sync_products_interval_minutes or 30),
    }


def _sync_status_str(tenant_id: str, credential_id: str, last_synced_at) -> str:
    """读取端数据新鲜度(PRD §8):never/syncing/ok/stale。"""
    if not _jobs_enabled():
        return "ok" if last_synced_at else "never"
    from services import store_sync_jobs
    if store_sync_jobs.current_job(tenant_id, str(credential_id)):
        return "syncing"
    if not last_synced_at:
        return "never"
    status = get_sync_status(tenant_id, str(credential_id))
    return "stale" if status.get("is_stale") else "ok"


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
    T4.3：旧缓存商品行缺图 → 按 product_id/sku 批量补一次并落库（幂等）。
    """
    client_id, api_key = credential_service.get_decrypted(tenant_id, str(credential_id))
    if lazy_sync and _needs_orders_sync(tenant_id, str(credential_id)) and not _jobs_enabled():
        sync_store(tenant_id, str(credential_id))

    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    since = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=min(max(int(since_days), 1), 90))
    sql = ("SELECT posting_number, status, raw_status, products, product_count, "
           "total_amount, commission_amount, profit, real_profit, warehouse, delivery_method, "
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
            "real_profit": r.real_profit,
            "warehouse": r.warehouse or "",
            "delivery_method": r.delivery_method or "",
            "cancel_reason": r.cancel_reason or "",
            "cancellation": r.cancellation or "",
        })
    # T4.3：旧缓存行（v4 前同步）products 无 image → 批量补一次并落库（懒，失败不阻断）
    _backfill_order_images(tenant_id, str(credential_id), client_id, api_key, items)
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
        "sync_status": _sync_status_str(tenant_id, str(credential_id), status_info["orders_last_synced_at"]),
    }


def list_cached_products(
    tenant_id: str,
    credential_id: str,
    limit: int = 50,
    offset: int = 0,
    lazy_sync: bool = True,
    status: str = "",
    source: str = "",
) -> dict:
    """在线商品读取：PG 缓存（未同步 → 懒同步；?refresh=1 强制）。

    source: ''=全部; matched=已有成本/货源(product_costs 行);
    unmatched=未匹配货源(工作台「未匹配」筛选)。
    """
    credential_service.get_decrypted(tenant_id, str(credential_id))
    if lazy_sync and _needs_products_sync(tenant_id, str(credential_id)) and not _jobs_enabled():
        sync_store(tenant_id, str(credential_id))

    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    where = ["tenant_id=:t", "credential_id=:c"]
    params: dict = {"t": tenant_id, "c": str(credential_id)}
    if status and status != "all":
        if status == "archived":
            where.append("archived=TRUE")
        elif status == "error":
            where.append("status='error'")
        elif status == "visible":
            where.append("archived=FALSE AND status <> 'error'")
        else:
            where.append("archived=FALSE")
    else:
        where.append("archived=FALSE")
    if source == "matched":
        where.append("EXISTS (SELECT 1 FROM product_costs pc WHERE "
                     "pc.tenant_id=ozon_products_cache.tenant_id "
                     "AND pc.credential_id=ozon_products_cache.credential_id "
                     "AND pc.product_id=ozon_products_cache.product_id)")
    elif source == "unmatched":
        where.append("NOT EXISTS (SELECT 1 FROM product_costs pc WHERE "
                     "pc.tenant_id=ozon_products_cache.tenant_id "
                     "AND pc.credential_id=ozon_products_cache.credential_id "
                     "AND pc.product_id=ozon_products_cache.product_id)")
    where_sql = " AND ".join(where)
    with get_engine().connect() as conn:
        total = int(conn.execute(text(
            f"SELECT COUNT(*) FROM ozon_products_cache WHERE {where_sql}"
        ), params).scalar() or 0)
        rows = conn.execute(text(
            "SELECT product_id, offer_id, name, image, price, old_price, min_price, "
            "stock, currency, status, error, archived "
            f"FROM ozon_products_cache WHERE {where_sql} "
            "ORDER BY product_id LIMIT :lim OFFSET :off"
        ), {**params, "lim": limit, "off": offset}).fetchall()

    items = [{
        "product_id": r.product_id,
        "offer_id": r.offer_id,
        "name": r.name,
        "image": r.image,
        "price": r.price,
        "old_price": r.old_price,
        "min_price": r.min_price,
        "stock": r.stock,
        "currency": r.currency or "",
        "status": r.status or "",
        "error": r.error or [],
        "archived": bool(r.archived),
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
        "sync_status": _sync_status_str(tenant_id, str(credential_id), status_info["products_last_synced_at"]),
    }


def _backfill_order_images(
    tenant_id: str, credential_id: str, client_id: str, api_key: str, items: list
) -> None:
    """旧缓存订单行无商品图 → 按 product_id/sku 批量补主图并落库（幂等，失败不阻断）。"""
    missing = [it for it in items
               if any(isinstance(p, dict) and not p.get("image") for p in (it.get("products") or []))]
    if not missing:
        return
    images = order_service.fetch_order_images(
        client_id, api_key, [{"products": it.get("products", [])} for it in missing])
    if not images:
        return
    for it in missing:
        order_service.apply_product_images(it.get("products") or [], images)
    with get_engine().begin() as conn:
        conn.execute(text(
            "UPDATE ozon_orders_cache SET products=CAST(:products AS jsonb) "
            "WHERE tenant_id=:t AND credential_id=:c AND posting_number=:pn"
        ), [{
            "products": json.dumps(it["products"], ensure_ascii=False),
            "t": tenant_id, "c": credential_id, "pn": it["posting_number"],
        } for it in missing])


# ──────────────────────────────────────────────
# 店铺卡统计（T4.6）：store_sync 缓存聚合（今日订单/销售额/利润）
# ──────────────────────────────────────────────


def get_store_stats(tenant_id: str, credential_id: str) -> dict:
    """店铺卡统计：ozon_orders_cache 今日（UTC 自然日）订单数/销售额/佣金/利润/件数。

    租户隔离：get_decrypted 归属校验（跨租户 404）；聚合 SQL 带 tenant_id + credential_id。
    ⚠️ 无评分字段——缓存无 rating 数据，店铺卡不显示评分。
    """
    client_id, _api_key = credential_service.get_decrypted(tenant_id, str(credential_id))
    if _needs_orders_sync(tenant_id, str(credential_id)) and not _sync_guard_active() and not _jobs_enabled():
        sync_store(tenant_id, str(credential_id))
    today_start = datetime.datetime.now(datetime.timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0)
    with get_engine().connect() as conn:
        row = conn.execute(text(
            "SELECT COUNT(*), COALESCE(SUM(total_amount), 0), COALESCE(SUM(commission_amount), 0), "
            "COALESCE(SUM(profit), 0), COALESCE(SUM(product_count), 0) "
            "FROM ozon_orders_cache "
            "WHERE tenant_id=:t AND credential_id=:c "
            "AND (order_created_at IS NULL OR order_created_at >= :since)"
        ), {"t": tenant_id, "c": str(credential_id), "since": today_start}).fetchone()
    status_info = get_sync_status(tenant_id, str(credential_id))
    return {
        "credential_id": str(credential_id),
        "ozon_client_id": client_id,
        "stats_date": today_start.date().isoformat(),
        "today_orders": int(row[0] or 0),
        "today_sales_amount": round(float(row[1] or 0), 2),
        "today_commission": round(float(row[2] or 0), 2),
        "today_profit": round(float(row[3] or 0), 2),
        "today_product_count": int(row[4] or 0),
        "data_freshness": {
            "synced_at": status_info["orders_last_synced_at"],
            "is_stale": bool(status_info.get("is_stale", False)),
        },
    }


def sync_health() -> dict:
    """admin sync-health:全部 active 店同步健康总览(PRD M3)。"""
    from services import store_sync_jobs
    with get_engine().connect() as conn:
        rows = conn.execute(text(
            """
            SELECT c.id::text AS credential_id, c.tenant_id, c.ozon_client_id, c.shop_name,
                   c.sync_enabled, s.last_success_at, s.consecutive_failures,
                   s.orders_last_synced_at, s.products_last_synced_at,
                   s.orders_error, s.products_error
            FROM credentials c
            LEFT JOIN credential_sync_state s
                ON s.credential_id = c.id AND s.tenant_id = c.tenant_id
            WHERE c.status='active'
            ORDER BY c.created_at DESC
            """
        )).fetchall()
    now = datetime.datetime.now(datetime.timezone.utc)
    items = []
    for r in rows:
        failures = int(r.consecutive_failures or 0)
        stale = failures >= 3
        last = r.last_success_at
        if last is not None and now - last > datetime.timedelta(hours=2):
            stale = True
        current = None
        try:
            current = store_sync_jobs.current_job(str(r.tenant_id), str(r.credential_id))
        except Exception:
            current = None
        items.append({
            "credential_id": str(r.credential_id),
            "ozon_client_id": str(r.ozon_client_id),
            "shop_name": r.shop_name or "",
            "sync_enabled": bool(r.sync_enabled),
            "last_success_at": r.last_success_at.isoformat() if r.last_success_at else None,
            "consecutive_failures": failures,
            "is_stale": stale,
            "current_job": current,
            "orders_error": r.orders_error or "",
            "products_error": r.products_error or "",
        })
    summary = {
        "total": len(items),
        "syncing": sum(1 for i in items if i["current_job"]),
        "stale": sum(1 for i in items if i["is_stale"]),
    }
    return {"summary": summary, "items": items}
