"""完成结果产品明细（v0.22）— 给 skill/agent 返回可读的经营数据。

每个产品一条：1688 采购链接、利润率、售价、采购价、运费预估、利润率和 Ozon 商品ID。
来源：graph_result（LangGraph 终态） + 原始信封 draft。
"""
from __future__ import annotations

from typing import Any


def _ozon_status(graph_result: dict) -> str:
    """v0.27: 把图终态的审核状态映射为 agent 可读的 approved/pending/declined。

    - moderation_status == "approved" → approved
    - moderation_status == "pending" → pending
    - moderation_status == "error" 或 upload failed / 有错误标记 → declined(含原因)
    - 其他(未走到审核) → 空
    """
    mod = str(graph_result.get("moderation_status") or "")
    if mod == "approved":
        return "approved"
    if mod == "pending":
        return "pending"
    if mod == "error":
        return "declined"
    up = str(graph_result.get("upload_status") or "")
    err = str(graph_result.get("error_message") or "")
    if up == "failed" or err or graph_result.get("_harness_status") == "failed":
        return "declined"
    return ""


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
        # ✅ v0.27: 上架审核状态透出 — agent 看完任务不再只知道"成功/失败",
        # 而是知道 approved/pending/declined + 拒绝原因(ENVELOPE-STANDARD 🅳)
        "ozon_status": _ozon_status(graph_result),
        "ozon_error": str(
            graph_result.get("error_message")
            or graph_result.get("_harness_error")
            or graph_result.get("validation_errors")
            or ""
        ),
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
