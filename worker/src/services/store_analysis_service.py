"""店铺分析服务 — 利润率/库存/候选清单（harness-store-analysis 计划 todo 6）。

数据源缺口：
- `OzonProductCache`（model.py:898）只存 price/old_price/stock/currency，**无采购成本/物流成本/佣金**。
- 逐品精确利润率需成本 → 成本从 `product_task_index → ozon_product_tasks.payload.envelope`
  恢复（本系统上架商品才有）。
- 有成本的商品经 `estimate_service.estimate_from_envelope`（compute_price +
  commission_resolver provisional band pass + 物流费率唯一入口）算 profit_rate；
  无成本商品只给「当前价 + 库存」，**不填 profit_rate**（不给无成本商品编造利润）。
- 全店综合趋势用 `store_metrics_history` 沉淀的 profit_rate/sales_amount 聚合。

Must NOT do：
- 不内联定价公式（一律走 estimate_service / compute_price / commission_resolver 唯一入口）。
- 不给无成本商品编造利润（该类商品不填 profit_rate 字段）。
- 不跨租户（credential 归属经 credential_service.get_decrypted 校验，跨租户 404）。
"""
from __future__ import annotations

import datetime
import logging
from typing import Any, Optional

from sqlalchemy import text

from services import credential_service, estimate_service
from storage.database.db import get_engine

logger = logging.getLogger(__name__)

# 低利润阈值（profit_rate < 此值 → 低利润商品）
LOW_MARGIN_THRESHOLD = 0.15
# 库存阈值（stock 低于此值且 >0 → 低库存；<=0 → 缺货）
LOW_STOCK_THRESHOLD = 10
# 可改价候选最低利润率（达标 + 有成本 → promo_ready）
PROMO_READY_PROFIT_RATE = 0.25


def analyze_store(tenant_id: str, credential_id: str) -> dict:
    """整店分析：summary + profit_trend + low_margin/out_of_stock/promo_ready 清单。

    Args:
        tenant_id: 租户（user_id）。
        credential_id: 店铺凭证 ID（字符串）。

    Returns:
        {
          "summary": {"product_count","low_stock_count","active_discount_count","avg_profit_rate"},
          "profit_trend": [{"snapshot_at","profit_rate","sales_amount"}],
          "low_margin_products": [{"product_id","name","price_rub","profit_rate","suggestion"}],
          "out_of_stock_products": [{"product_id","name","stock"}],
          "promo_ready_products": [{"product_id","name","profit_rate","candidate_action"}],
        }
        无成本商品不填 profit_rate 字段。
    """
    # 归属校验（跨租户 404）
    credential_service.get_decrypted(tenant_id, str(credential_id))

    products = _list_products(tenant_id, str(credential_id))
    cost_payloads = _load_cost_payloads(tenant_id, str(credential_id))

    profit_by_pid: dict[str, float] = {}
    for pid, payload in cost_payloads.items():
        try:
            est = estimate_service.estimate_from_envelope(payload)
        except Exception as exc:
            logger.warning("分析单品利润失败 product_id=%s: %s", pid, exc)
            continue
        pr = est.get("profit_rate")
        if pr is None:
            continue
        profit_by_pid[pid] = float(pr)

    enriched: list[dict[str, Any]] = []
    for p in products:
        entry = dict(p)
        pid = p["product_id"]
        if pid in profit_by_pid:
            entry["profit_rate"] = profit_by_pid[pid]
            entry["has_cost"] = True
        else:
            entry["has_cost"] = False
        enriched.append(entry)

    low_margin, out_of_stock, promo_ready = _classify(enriched)

    summary = {
        "product_count": len(products),
        "low_stock_count": sum(
            1 for p in enriched
            if p["stock"] is not None and 0 < p["stock"] < LOW_STOCK_THRESHOLD
        ),
        "active_discount_count": sum(
            1 for p in enriched
            if p.get("old_price") is not None and p.get("price") is not None
            and p["old_price"] > p["price"]
        ),
        "avg_profit_rate": _avg_profit_rate(profit_by_pid),
    }

    return {
        "summary": summary,
        "profit_trend": _load_profit_trend(tenant_id, str(credential_id)),
        "low_margin_products": low_margin,
        "out_of_stock_products": out_of_stock,
        "promo_ready_products": promo_ready,
    }


# ──────────────────────────────────────────────
# 读取
# ──────────────────────────────────────────────


def _list_products(tenant_id: str, credential_id: str) -> list[dict]:
    """读 ozon_products_cache 未归档商品（当前价 + 库存，无成本字段）。"""
    with get_engine().connect() as conn:
        rows = conn.execute(text(
            "SELECT product_id, name, price, old_price, stock, currency "
            "FROM ozon_products_cache "
            "WHERE tenant_id=:t AND credential_id=:c AND archived=FALSE "
            "ORDER BY product_id"
        ), {"t": tenant_id, "c": str(credential_id)}).fetchall()
    return [{
        "product_id": str(r.product_id),
        "name": str(r.name or ""),
        "price": r.price,
        "old_price": r.old_price,
        "stock": r.stock,
        "currency": str(r.currency or ""),
    } for r in rows]


def _load_cost_payloads(tenant_id: str, credential_id: str) -> dict[str, dict]:
    """从 product_task_index → ozon_product_tasks.payload.envelope 恢复本店商品成本信封。

    只回 payload.envelope（有 purchase_cost → 有成本；无 → 该商品无成本）。
    信封缺失/异常 → 该商品跳过（视为无成本，不编造）。
    """
    rows = []
    try:
        with get_engine().connect() as conn:
            rows = conn.execute(text(
                "SELECT i.product_id, t.payload "
                "FROM product_task_index i "
                "JOIN ozon_product_tasks t ON t.id = i.task_id "
                "WHERE i.tenant_id=:t AND i.credential_id=:c"
            ), {"t": tenant_id, "c": str(credential_id)}).fetchall()
    except Exception as exc:
        logger.warning("恢复商品成本信封失败 tenant=%s store=%s: %s",
                       tenant_id, credential_id, exc)
        return {}

    result: dict[str, dict] = {}
    for product_id, payload in rows:
        if not isinstance(payload, dict):
            continue
        envelope = payload.get("envelope") or {}
        draft = envelope.get("draft") or {}
        # 有 purchase_cost 才视为有成本（无成本商品不填 profit_rate）。
        # key 用 Ozon product_id（来自 product_task_index，与 ozon_products_cache 对齐），
        # **不是** draft.item_id（1688 ID，两者在生产数据中不相等——曾致全部落 has_cost=False）。
        cost = draft.get("purchase_cost") or draft.get("cost_cny")
        if not cost or float(cost) <= 0:
            continue
        result[str(product_id)] = envelope
    return result


def _load_profit_trend(tenant_id: str, credential_id: str) -> list[dict]:
    """读 store_metrics_history 沉淀的趋势（snapshot_at 升序）。"""
    with get_engine().connect() as conn:
        rows = conn.execute(text(
            "SELECT snapshot_at, profit_rate, sales_amount "
            "FROM store_metrics_history "
            "WHERE tenant_id=:t AND credential_id=:c "
            "ORDER BY snapshot_at ASC"
        ), {"t": tenant_id, "c": str(credential_id)}).fetchall()
    trend = []
    for r in rows:
        snap = r.snapshot_at
        snapshot_iso = None
        if isinstance(snap, datetime.datetime):
            snap = snap.replace(tzinfo=None) if snap.tzinfo is None else snap
            snapshot_iso = snap.isoformat()
        elif isinstance(snap, str):
            snapshot_iso = snap
        trend.append({
            "snapshot_at": snapshot_iso,
            "profit_rate": r.profit_rate,
            "sales_amount": r.sales_amount,
        })
    return trend


# ──────────────────────────────────────────────
# 分类
# ──────────────────────────────────────────────


def _classify(enriched: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    """按利润率/库存/可改价候选分类（无成本商品不进 low_margin/promo_ready）。"""
    low_margin: list[dict] = []
    out_of_stock: list[dict] = []
    promo_ready: list[dict] = []

    for p in enriched:
        pid = p["product_id"]
        name = p["name"]
        price_rub = p.get("price")
        stock = p.get("stock")

        # 缺货：stock <= 0
        if stock is not None and stock <= 0:
            out_of_stock.append({"product_id": pid, "name": name, "stock": stock})

        # 有成本才有利润判定
        if p.get("has_cost") and p.get("profit_rate") is not None:
            rate = p["profit_rate"]
            if rate < LOW_MARGIN_THRESHOLD:
                low_margin.append({
                    "product_id": pid,
                    "name": name,
                    "price_rub": price_rub,
                    "profit_rate": rate,
                    "suggestion": "利润偏低，建议提价或降低采购成本",
                })
            if rate >= PROMO_READY_PROFIT_RATE:
                candidate = {
                    "product_id": pid,
                    "name": name,
                    "profit_rate": rate,
                    "candidate_action": "可参与促销/改价（利润充足）",
                }
                promo_ready.append(candidate)

    return low_margin, out_of_stock, promo_ready


def _avg_profit_rate(profit_by_pid: dict[str, float]) -> Optional[float]:
    """有成本商品的平均利润率；无成本商品 → None。"""
    if not profit_by_pid:
        return None
    return round(sum(profit_by_pid.values()) / len(profit_by_pid), 4)
