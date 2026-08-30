"""PRD M3: 商品成本主数据 — product_costs / product_cost_history / order_line_costs 联动。

成本口径:到仓成本 = 采购价(信封契约已含 1688 国内运费);国际物流与汇率明细为阶段二,
real_profit 缺失成本/汇率时为 NULL(不编造)。
优先级:manual(手填)> envelope(自营上架)> discovery(选品)> 1688 刷新。
成本变更 → recalc_product 按 product_id 重算受影响订单 real_profit(幂等)。
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import text

from storage.database.db import get_engine

logger = logging.getLogger(__name__)


def _num(v) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def get_latest_fx_rate() -> Optional[float]:
    """最近一日 CNY→RUB(缺失 → None,real_profit 标 NULL 不编造)。"""
    with get_engine().connect() as conn:
        row = conn.execute(text(
            "SELECT cny_to_rub FROM fx_rates ORDER BY date DESC LIMIT 1"
        )).fetchone()
    return float(row[0]) if row else None


def upsert_fx_rate(date: str, cny_to_rub: float, source: str = "manual") -> None:
    with get_engine().begin() as conn:
        conn.execute(text(
            """
            INSERT INTO fx_rates (date, cny_to_rub, source)
            VALUES (:d, :r, :s)
            ON CONFLICT (date) DO UPDATE SET cny_to_rub=EXCLUDED.cny_to_rub, source=EXCLUDED.source
            """
        ), {"d": date, "r": cny_to_rub, "s": source})


def _cost_row(tenant_id: str, credential_id: str, product_id: str) -> Optional[dict]:
    with get_engine().connect() as conn:
        row = conn.execute(text(
            """
            SELECT product_id, offer_id, purchase_url, purchase_cost, freight_cny,
                   supplier, cost_source, updated_at
            FROM product_costs WHERE tenant_id=:t AND credential_id=:c AND product_id=:p
            """
        ), {"t": tenant_id, "c": str(credential_id), "p": product_id}).fetchone()
    if row is None:
        return None
    return {
        "product_id": row.product_id, "offer_id": row.offer_id,
        "purchase_url": row.purchase_url, "purchase_cost": row.purchase_cost,
        "freight_cny": row.freight_cny, "supplier": row.supplier,
        "cost_source": row.cost_source,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _upsert(
    tenant_id: str, credential_id: str, product_id: str, offer_id: str, *,
    purchase_url: str, purchase_cost: Optional[float], supplier: str,
    freight_cny: Optional[float], cost_source: str, changed_by: str,
) -> None:
    with get_engine().begin() as conn:
        prev = conn.execute(text(
            "SELECT purchase_cost, freight_cny FROM product_costs "
            "WHERE tenant_id=:t AND credential_id=:c AND product_id=:p"
        ), {"t": tenant_id, "c": str(credential_id), "p": product_id}).fetchone()
        if prev is not None and (prev[0], prev[1]) != (purchase_cost, freight_cny):
            conn.execute(text(
                """
                INSERT INTO product_cost_history
                    (tenant_id, credential_id, product_id, old_cost, new_cost, changed_by)
                VALUES (:t, :c, :p, :old, :new, :by)
                """
            ), {"t": tenant_id, "c": str(credential_id), "p": product_id,
                "old": prev[0], "new": purchase_cost, "by": changed_by})
        conn.execute(text(
            """
            INSERT INTO product_costs
                (tenant_id, credential_id, product_id, offer_id, purchase_url, purchase_cost,
                 freight_cny, supplier, currency, cost_source, updated_at)
            VALUES (:t, :c, :p, :o, :url, :cost, :fr, :sup, 'CNY', :src, NOW())
            ON CONFLICT (tenant_id, credential_id, product_id) DO UPDATE SET
                offer_id = EXCLUDED.offer_id,
                purchase_url = EXCLUDED.purchase_url,
                purchase_cost = EXCLUDED.purchase_cost,
                freight_cny = EXCLUDED.freight_cny,
                supplier = EXCLUDED.supplier,
                cost_source = EXCLUDED.cost_source,
                updated_at = NOW()
            """
        ), {
            "t": tenant_id, "c": str(credential_id), "p": product_id, "o": offer_id,
            "url": purchase_url, "cost": purchase_cost, "fr": freight_cny,
            "sup": supplier, "src": cost_source,
        })


def upsert_from_envelope(
    tenant_id: str, credential_id: str, product_id: str, offer_id: str, envelope: dict,
) -> None:
    """上架成功回填(envelope 源):manual 已存在则跳过(最高优先级)。"""
    draft = (envelope or {}).get("draft") or {}
    source = (envelope or {}).get("source") or {}
    purchase_cost = _num(draft.get("purchase_cost") or source.get("purchase_cost"))
    if purchase_cost is None:
        return
    existing = _cost_row(tenant_id, credential_id, product_id)
    if existing and existing["cost_source"] == "manual":
        return
    _upsert(
        tenant_id, credential_id, product_id, offer_id,
        purchase_url=str(source.get("purchase_url") or draft.get("purchase_url") or ""),
        purchase_cost=purchase_cost,
        supplier=str(draft.get("supplier") or ""),
        freight_cny=None, cost_source="envelope", changed_by="system:envelope",
    )


def upsert_manual(
    tenant_id: str, credential_id: str, product_id: str, offer_id: str, *,
    purchase_url: str = "", purchase_cost: Optional[float] = None,
    supplier: str = "", freight_cny: Optional[float] = None,
) -> dict:
    """手动维护成本(manual 最高优先级)+ 成本变更重算订单利润。"""
    if purchase_cost is None:
        raise ValueError("purchase_cost 不能为空")
    _upsert(
        tenant_id, credential_id, product_id, offer_id,
        purchase_url=purchase_url, purchase_cost=purchase_cost,
        supplier=supplier, freight_cny=freight_cny,
        cost_source="manual", changed_by="user:manual",
    )
    recalc_product(tenant_id, credential_id, product_id)
    return get_cost(tenant_id, credential_id, product_id)


def get_cost(tenant_id: str, credential_id: str, product_id: str) -> dict:
    row = _cost_row(tenant_id, credential_id, product_id) or {
        "product_id": product_id, "offer_id": "", "purchase_url": "",
        "purchase_cost": None, "freight_cny": None, "supplier": "",
        "cost_source": None, "updated_at": None,
    }
    with get_engine().connect() as conn:
        hist = conn.execute(text(
            "SELECT old_cost, new_cost, changed_by, changed_at FROM product_cost_history "
            "WHERE tenant_id=:t AND credential_id=:c AND product_id=:p ORDER BY changed_at DESC LIMIT 20"
        ), {"t": tenant_id, "c": str(credential_id), "p": product_id}).fetchall()
    row["history"] = [{
        "old_cost": h.old_cost, "new_cost": h.new_cost,
        "changed_by": h.changed_by,
        "changed_at": h.changed_at.isoformat() if h.changed_at else None,
    } for h in hist]
    return row


def recalc_product(tenant_id: str, credential_id: str, product_id: str) -> int:
    """成本变更 → 刷新该商品 order_line_costs 并重算受影响订单 real_profit(幂等)。"""
    fx = get_latest_fx_rate()
    with get_engine().begin() as conn:
        cost = conn.execute(text(
            "SELECT purchase_cost, freight_cny, purchase_url FROM product_costs "
            "WHERE tenant_id=:t AND credential_id=:c AND product_id=:p"
        ), {"t": tenant_id, "c": str(credential_id), "p": product_id}).fetchone()
        if cost is None:
            return 0
        conn.execute(text(
            "UPDATE order_line_costs SET source_cost=:cost, fx_rate=:fx, updated_at=NOW() "
            "WHERE tenant_id=:t AND credential_id=:c AND product_id=:p"
        ), {"cost": cost[0], "fx": fx, "t": tenant_id, "c": str(credential_id), "p": product_id})
        postings = [str(r[0]) for r in conn.execute(text(
            "SELECT DISTINCT posting_number FROM order_line_costs "
            "WHERE tenant_id=:t AND credential_id=:c AND product_id=:p"
        ), {"t": tenant_id, "c": str(credential_id), "p": product_id}).fetchall()]
    for pn in postings:
        _recalc_posting(tenant_id, credential_id, pn)
    return len(postings)


def _recalc_posting(tenant_id: str, credential_id: str, posting_number: str) -> None:
    """按 order_line_costs 汇总成本,回写 ozon_orders_cache.real_profit。"""
    with get_engine().begin() as conn:
        row = conn.execute(text(
            "SELECT total_amount, commission_amount FROM ozon_orders_cache "
            "WHERE tenant_id=:t AND credential_id=:c AND posting_number=:pn"
        ), {"t": tenant_id, "c": str(credential_id), "pn": posting_number}).fetchone()
        if row is None:
            return
        lines = conn.execute(text(
            "SELECT source_cost, fx_rate FROM order_line_costs "
            "WHERE tenant_id=:t AND credential_id=:c AND posting_number=:pn"
        ), {"t": tenant_id, "c": str(credential_id), "pn": posting_number}).fetchall()
        missing = any(l.source_cost is None or l.fx_rate is None for l in lines)
        real = None
        if not missing and row.total_amount is not None:
            total_cost = sum(float(l.source_cost) * float(l.fx_rate) for l in lines)
            real = round(float(row.total_amount) - float(row.commission_amount or 0) - total_cost, 2)
        conn.execute(text(
            "UPDATE ozon_orders_cache SET real_profit=:r "
            "WHERE tenant_id=:t AND credential_id=:c AND posting_number=:pn"
        ), {"r": real, "t": tenant_id, "c": str(credential_id), "pn": posting_number})
