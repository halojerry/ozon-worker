"""完成结果产品明细（v0.22）— 给 skill/agent 返回可读的经营数据。

每个产品一条：1688 采购链接、利润率、售价、采购价、运费预估、利润率和 Ozon 商品ID。
来源：graph_result（LangGraph 终态） + 原始信封 draft。
"""
from __future__ import annotations

from typing import Any


def build_product_summary(graph_result: dict, draft: dict) -> list[dict[str, Any]]:
    """从 LangGraph 终态 + draft 组装产品明细数组。"""
    pi = graph_result.get("pricing_info") or {}
    profit_est = pi.get("profit_estimation") or {}
    purchase_url = str(
        draft.get("purchase_url")
        or graph_result.get("purchase_url")
        or ""
    )
    purchase_cost = (
        draft.get("purchase_cost")
        if draft.get("purchase_cost") not in (None, "")
        else pi.get("cost_cny", 0)
    )
    base: dict[str, Any] = {
        "purchase_url": purchase_url,
        "purchase_cost": purchase_cost,
        "margin_rate": pi.get("margin_rate", 0),
        "price": pi.get("price", ""),
        "logistics_cost": pi.get("logistics_cost_cny", 0),
        "profit_rate": profit_est.get("profit_rate", 0),
        "product_id": graph_result.get("product_id"),
    }

    variants = pi.get("variant_prices") or []
    if variants:
        rows: list[dict[str, Any]] = []
        for var in variants:
            row = dict(base)
            row["sku_id"] = var.get("sku_id", "")
            row["price"] = var.get("price", "")
            row["old_price"] = var.get("old_price", "")
            rows.append(row)
        return rows
    return [base]
