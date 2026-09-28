"""v0.83 批⑥ 回执真值化：实盘利润唯一计算入口（PLAN-v083-quality-campaign-v1 §3 批⑥）。

动因：worker 现有佣金链只把 `/v5/product/info/prices` 的 `sales_percent_*` 捡走，
其余实盘字段（`price.marketing_seller_price` 买家实付 / `acquiring` 收单费 /
FBS 物流费组）全部丢弃——「预估利润」与「实盘利润」从未对账，定价系统性偏差
无人报警。本模块把同一 `/v5` 响应里被丢弃的字段捡回来算实盘，**零新增 Ozon 调用**。

口径（唯一事实源，禁止内联复制）：
    实盘利润 = 买家实付(marketing_seller_price) 折算 CNY
             − 采购(purchase_cost_cny) − 国际运费(logistics_cost_cny)
             − Ozон 侧费用折算 CNY
    Ozon 侧费用 = 佣金基数×真实佣金率 + 佣金基数×acquiring + FBS 物流费组
    佣金基数 = marketing_seller_price（Ozon 官方 quirks：佣金 = marketing_seller_price × pct/100）

- 非 RUB 店（如 CNY 测试店）：marketing 已是 CNY，费用不再折算；`real_price_rub`
  按 CNY→RUB 汇率换算成 RUB 当量（最低价门槛口径，见 card_audit）。
- `unmodeled_fees`：响应里存在但本公式**未计入**的同通道费项（FBS min 档 /
  退货费 / 未识别键）——单列留痕，让「实盘利润的解释」可追溯（宁列不猜）。
- 跟卖卡同样适用（用户已拍板：跟卖卡价格也按我方公式定价）。
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

# 建模的 FBS 物流费组（金额，RUB；响应对 min/max 成对出现时取保守上界 max/单值）
_MODELED_FBS_FEE_KEYS: Tuple[str, ...] = (
    "fbs_first_mile_max_amount",
    "fbs_direct_flow_trans_max_amount",
    "fbs_deliv_to_customer_amount",
)
# 同通道但未建模的费项（出现即单列 unmodeled_fees，而非静默丢弃）
_UNMODELED_FBS_FEE_KEYS: Tuple[str, ...] = (
    "fbs_first_mile_min_amount",
    "fbs_direct_flow_trans_min_amount",
    "fbs_return_flow_amount",
)
# 已知佣金字段（sales 各模式 + FBS/FBO 物流费组）；其余 numeric 键一律视为未识别费项
_KNOWN_COMMISSION_KEYS = frozenset({
    "sales_percent_rfbs", "sales_percent_fbs", "sales_percent_fbo", "sales_percent_fbp",
    *_MODELED_FBS_FEE_KEYS, *_UNMODELED_FBS_FEE_KEYS,
})
# 履约模式解析顺序（默认 rfbs——worker rFBS 主履约通道，对齐 stores.json 默认）
_MODE_PRIORITY: Tuple[str, ...] = ("rfbs", "fbs", "fbo", "fbp")


def _num(v: Any) -> Optional[float]:
    """宽松数值化：None/畸形/NaN → None。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def _pct_to_rate(v: Any) -> Optional[float]:
    """Ozon 佣金/收单费值 → 比例。

    兼容两种返回形态：百分数（如 15.0 → 0.15）与分数（如 0.13 → 0.13）——
    >1 视为百分数 ÷100，≤1 视为已是分数。值缺失/非正 → None（0% 不是真实费率，
    对齐 commission_resolver 的「0 不采信」红线）。
    """
    f = _num(v)
    if f is None or f <= 0:
        return None
    return f / 100.0 if f > 1.0 else f


def resolve_commission_mode(extensions: Optional[dict]) -> str:
    """履约模式：`extensions.sales_mode`/`fulfillment_mode` 优先，默认 `rfbs`。

    信封未带模式（graph/follow 现状）→ rfbs（worker 主履约通道）。仅接受白名单
    值，未知/畸形一律回落 rfbs（不产生非法 `sales_percent_*` 键）。
    """
    ext = extensions if isinstance(extensions, dict) else {}
    for key in ("sales_mode", "fulfillment_mode"):
        v = str(ext.get(key) or "").strip().lower()
        if v in _MODE_PRIORITY:
            return v
    return "rfbs"


def _commission_for_mode(commissions: dict, mode: str) -> Tuple[str, Optional[float]]:
    """按模式取 `sales_percent_{mode}` 比例；缺失/非正 → 回退链 rfbs→fbs→fbo→fbp。

    Returns: (实际采用的 mode, 比例或 None)。
    """
    if not isinstance(commissions, dict):
        return mode, None
    order = [mode] + [m for m in _MODE_PRIORITY if m != mode]
    for m in order:
        rate = _pct_to_rate(commissions.get(f"sales_percent_{m}"))
        if rate is not None:
            return m, rate
    return mode, None


def compute_profit_reality(
    price_item: Optional[dict],
    *,
    purchase_cost_cny: Optional[float] = None,
    logistics_cost_cny: Optional[float] = None,
    predicted_profit_cny: Optional[float] = None,
    fx_rate: float = 1.0,
    currency_code: str = "RUB",
    commission_mode: str = "rfbs",
) -> Optional[Dict[str, Any]]:
    """从单条 `/v5/product/info/prices` item 算实盘利润（纯函数，零 I/O）。

    Args:
        price_item: `/v5` 响应 items[] 的单条（含 price{}/commissions{}/acquiring）。
        purchase_cost_cny: 采购成本（CNY，pricing_info.cost_cny）。
        logistics_cost_cny: 国际运费（CNY，pricing_info.logistics_cost_cny）。
        predicted_profit_cny: 预估利润（CNY，pricing_info.profit_estimation.profit_cny）；
            None → gap 字段为 None（无预估可比，如实留空不编造）。
        fx_rate: CNY→RUB 汇率（如 12.0）。RUB 店用于费用/售价折算 CNY；非 RUB 店
            仅用于 real_price_rub 的 RUB 当量换算。
        currency_code: 店铺挂牌币种（RUB/CNY/…）。
        commission_mode: 履约模式（resolve_commission_mode 产出）。

    Returns:
        dict（见模块 docstring）或 None（price_item 无有效售价 → 无从计算）。
    """
    if not isinstance(price_item, dict):
        return None
    p = price_item.get("price")
    if not isinstance(p, dict):
        p = {}
    seller_price = _num(p.get("price"))
    marketing = _num(p.get("marketing_seller_price"))
    if marketing is None or marketing <= 0:
        marketing = seller_price  # 无营销价（无活动）→ 卖家价即买家实付
    if marketing is None or marketing <= 0:
        return None  # 无有效售价（超价/隔离/未定价卡）→ 不产噪音

    commissions = price_item.get("commissions")
    if not isinstance(commissions, dict):
        commissions = {}
    comm_mode, comm_rate = _commission_for_mode(commissions, commission_mode)
    acq_rate = _pct_to_rate(price_item.get("acquiring"))

    # 佣金/收单基数 = 买家实付（Ozon 官方 quirks 口径）
    commission_base = marketing
    comm_fee = commission_base * (comm_rate or 0.0)
    acq_fee = commission_base * (acq_rate or 0.0)

    fbs_fees_rub = 0.0
    for key in _MODELED_FBS_FEE_KEYS:
        v = _num(commissions.get(key))
        if v:
            fbs_fees_rub += v

    unmodeled: List[Dict[str, Any]] = []
    for key in _UNMODELED_FBS_FEE_KEYS:
        v = _num(commissions.get(key))
        if v:
            unmodeled.append({"key": key, "value": round(v, 4)})
    for key, raw in commissions.items():
        if key in _KNOWN_COMMISSION_KEYS:
            continue
        v = _num(raw)
        if v:
            unmodeled.append({"key": str(key), "value": round(v, 4)})

    fees_native = comm_fee + acq_fee + fbs_fees_rub
    cur = str(currency_code or "RUB").strip().upper()
    _fx = fx_rate if (fx_rate and fx_rate > 0) else 1.0
    if cur == "RUB":
        revenue_cny = marketing / _fx
        fees_cny = fees_native / _fx
        real_price_rub = marketing
    elif cur == "CNY":
        revenue_cny = marketing
        fees_cny = fees_native
        real_price_rub = marketing * _fx  # RUB 当量（最低价门槛口径）
    else:
        # 其他币种：无对应该币种的汇率链，按原币直接入账（保守近似，如实标 currency_code）
        revenue_cny = marketing
        fees_cny = fees_native
        real_price_rub = marketing

    cost_cny = float(purchase_cost_cny or 0.0) + float(logistics_cost_cny or 0.0)
    real_profit_cny = revenue_cny - fees_cny - cost_cny

    if predicted_profit_cny is None:
        gap_cny: Optional[float] = None
        gap_pct: Optional[float] = None
    else:
        gap_cny = round(real_profit_cny - float(predicted_profit_cny), 4)
        denom = max(abs(float(predicted_profit_cny)), 1.0)  # 防除零（ABS 闸兜底）
        gap_pct = round(gap_cny / denom, 4)

    return {
        "real_price_rub": round(real_price_rub, 4),
        "seller_price": round(seller_price, 4) if seller_price is not None else None,
        "real_commission_pct": round(comm_rate, 4) if comm_rate is not None else None,
        "acquiring_pct": round(acq_rate, 4) if acq_rate is not None else None,
        "fbs_fees_rub": round(fbs_fees_rub, 4),
        "real_profit_cny": round(real_profit_cny, 4),
        "predicted_profit_cny": (
            round(float(predicted_profit_cny), 4) if predicted_profit_cny is not None else None
        ),
        "gap_cny": gap_cny,
        "gap_pct": gap_pct,
        "unmodeled_fees": unmodeled,
        "fx_rate": round(_fx, 6),
        "currency_code": cur,
        "commission_mode": comm_mode,
        # 明细（审计/对账用，非契约必需）
        "real_revenue_cny": round(revenue_cny, 4),
        "real_fees_cny": round(fees_cny, 4),
        "real_cost_cny": round(cost_cny, 4),
    }


def compute_profit_reality_multi(
    price_items: List[dict],
    *,
    primary_product_id: Optional[str] = None,
    **kwargs: Any,
) -> Optional[Dict[str, Any]]:
    """多 SKU：逐 product_id 对齐计算，返回主商品顶层 + `per_product` 映射。

    主商品 = primary_product_id 命中项，否则 items[0]。无任何可算项 → None。
    """
    if not price_items:
        return None
    by_pid: Dict[str, Dict[str, Any]] = {}
    ordered: List[Tuple[str, Dict[str, Any]]] = []
    for it in price_items:
        if not isinstance(it, dict):
            continue
        pid = str(it.get("product_id") or "").strip()
        computed = compute_profit_reality(it, **kwargs)
        if computed is None:
            continue
        if pid:
            by_pid[pid] = computed
        ordered.append((pid, computed))
    if not ordered:
        return None
    primary = None
    if primary_product_id:
        primary = by_pid.get(str(primary_product_id).strip())
    if primary is None:
        primary = ordered[0][1]
    out = dict(primary)
    out["per_product"] = by_pid
    return out
