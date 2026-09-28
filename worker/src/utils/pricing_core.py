"""定价纯计算核（v0.83 批①）——pricing_node / estimate_service / /estimate/batch 唯一同源实现。

背景（docs/PLAN-v083-quality-campaign-v1.md §2 实锤 B）：此前系统里有多条独立算价链
（pricing_node 上架主链、estimate_service 采集箱预估、skill 端若干内联公式），
差异远不止 reconcile —— 包装费常量、店铺 3PL 探测、fx 三级链、dc 来源、marks 各写
一份 → 用户看到的价与上架价漂移。本模块从 `graphs/nodes/pricing_node.py` 抽出**单 SKU
主链纯计算核**，所有消费方共调。

**带副作用的部分刻意留在 pricing_node**（不在本模块）：
Sentry 留痕、`price_sanity_guard` 价差守卫 block、Ozon API 兜底查汇率/币种、
`_check_balance_cached`、多 SKU 变体循环。本模块只做「取数 → 共享公式 → 组装 marks」。

输入：draft 形态 dict + extensions + currency_code/exchange_rate/dc_id/物流 provider+
service_level/店铺 margin 配置（可选请求级覆盖，estimate 用）。
输出：与 pricing_node `pricing_info` **同构** dict；审计明细经 `audit_out` 旁路回吐
（pricing_node 不传 → 输出逐字不变）。

唯一入口纪律（同 pricing_estimate）：禁止任何消费方内联定价公式。
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

# 与 pricing_node Step 3/Step 1 逐字一致（包装成本固定值 + 成本兜底）
PACKAGING_COST_CNY = 2.0
DEFAULT_COST_CNY = 10.0

# audit_out 未提供 vs 显式 None 的区分哨兵（debug 键用）
_UNSET = object()


def compute_pricing_core(
    draft: Dict[str, Any],
    extensions: Optional[Dict[str, Any]] = None,
    *,
    currency_code: str = "RUB",
    exchange_rate: float = 1.0,
    fx_source: str = "",
    description_category_id: Any = None,
    tpl_provider: str = "RETS",
    service_level: str = "Standard",
    logistics_source: str = "default_rets",
    apply_volume_floor: bool = False,
    margin_rate: Optional[float] = None,
    commission_rate: Optional[float] = None,
    fx_buffer: Optional[float] = None,
    margin_anchor: Optional[float] = None,
    margin_floor: Optional[float] = None,
    variable_cost_rate: Optional[float] = None,
    promo_variable_cost_rate: Optional[float] = None,
    query_logistics_cost_fn: Optional[Callable] = None,
    get_category_commission_fn: Optional[Callable] = None,
    audit_out: Optional[Dict[str, Any]] = None,
    debug_state_currency_code: Any = _UNSET,
) -> Dict[str, Any]:
    """单 SKU 定价主链（纯计算）→ pricing_info 同构 dict。

    Args:
        draft: 信封 draft 形态 dict（cost_cny/purchase_cost、weight、dimensions、
            attributes、ozon_category 等）。
        extensions: 信封 extensions（margin/commission/fx 配置 + commission_segments）。
        currency_code: 已解析的店铺币种（"RUB"/"CNY"；调用方负责兜底默认 RUB）。
        exchange_rate: CNY→RUB 汇率（CNY 路径不使用，调用方可传 1.0）。
        fx_source: 汇率来源（pg_cache/live_fetch/fallback_12/""）→ pricing_info
            exchange_rate_source marks。
        description_category_id: Ozon description_category_id（佣金缓存表键）。
        tpl_provider / service_level: 店铺 3PL 与服务等级（调用方探测后传入）。
        logistics_source: 物流配置来源标记（store/default_rets），进 audit_out。
        apply_volume_floor: 是否做体积重兜底（ensure_volume_weight_floor）。
            **v0.83.2 起三处（pricing_node / estimate / batch）统一传 True**——与
            prepare 上架链同序（normalize→reconcile→floor），卡面声明重量、物流计费
            重量与定价重量同口径（低密度件不再「卡 121g 计费 / 定价 100g」少收运费差）。
            守卫条件：prepare 上传链始终兜底，故定价链必须一致，否则系统性少收运费。
        margin_rate/commission_rate/fx_buffer/margin_anchor/margin_floor/
        variable_cost_rate/promo_variable_cost_rate: **请求级覆盖**（estimate 端点）。
            None → 回落 extensions / 默认（pricing_node 全不传，语义与改动前一致）。
        query_logistics_cost_fn / get_category_commission_fn: 依赖注入点（测试 mock
            与调用方模块属性 patch 兼容）；None → 惰性 import 真实实现。
        audit_out: 可选 dict；提供时被就地填充审计明细（commission_source/
            exchange_rate_source/logistics_source/weight_suspect/wd_marks 等），
            供 estimate/batch 回吐 marks。pricing_node 不传（输出零噪音）。
        debug_state_currency_code: pricing_node 的原始 state.currency_code（仅调试键）。

    Returns:
        pricing_info 同构 dict（键与 pricing_node 逐字一致；单档不产生 promo_price/
        margin_anchor/margin_floor/variable_cost_rate 键；非 stale 不产生
        commission_source 键——零行为噪音）。
    """
    extensions = extensions if isinstance(extensions, dict) else {}

    # ── Step 1: 采购成本（兼容 cost_cny / purchase_cost）──
    cost_cny: float = float(draft.get("cost_cny", 0) or draft.get("purchase_cost", 0) or 0)
    _cost_suspect: bool = False
    if cost_cny <= 0:
        cost_cny = DEFAULT_COST_CNY
        logger.warning("⚠️ cost_cny为0或空，使用默认值: %s CNY", DEFAULT_COST_CNY)
    elif cost_cny < 1.0:
        # v0.81 上架质量止血：采购价低值标疑（非阻断，只留痕）
        _cost_suspect = True
        logger.warning(
            "⚠️ cost_cny=%.2f 低于 1 CNY，疑似箱价/单价错位（purchase_cost_suspect），价格可能不可靠",
            cost_cny,
        )

    # ── Step 1b: 重量/尺寸归一化（公共模块，与 prepare 同源）──
    from utils.weight_dimension_normalizer import (
        normalize_weight_dimensions,
        reconcile_weight_with_attrs,
    )

    dims_obj = draft.get("dimensions", {})
    if not (isinstance(dims_obj, dict) and dims_obj):
        dims_obj = {
            "length": draft.get("depth", 0) or draft.get("length", 0),
            "width": draft.get("width", 0),
            "height": draft.get("height", 0),
        }
    weight, dims_mm, _wd_marks = normalize_weight_dimensions(
        draft.get("weight", 0), dims_obj, extensions
    )
    # v0.81 箱级毛重 reconcile（唯一入口，与 pricing_node/prepare 同源）
    weight, _reconcile_marks = reconcile_weight_with_attrs(
        weight, draft.get("attributes", {}) if isinstance(draft, dict) else {}
    )
    if _reconcile_marks:
        _wd_marks["reasons"].extend(_reconcile_marks)
    # v0.83.2：定价链体积重兜底（与 prepare 同序 normalize→reconcile→floor）——
    # pricing_node / estimate / batch 三处统一开启（见 compute_pricing_core 参数注释），
    # 低密度件按真实计费重量计价：卡面声明/物流计费/定价三者同口径（此前 pricing_node
    # 不兜底 → 卡与物流按 121g 计费、定价却按 100g 算，低密度单系统性少收运费差）。
    _volume_floor_mark: Optional[Dict[str, Any]] = None
    if apply_volume_floor:
        from utils.volume_weight_guard import (
            compute_density_g_cc,
            ensure_volume_weight_floor,
        )

        _w_before_floor = weight
        weight, _raised = ensure_volume_weight_floor(weight, dims_mm)
        if _raised:
            # 结构化审计键（风格对齐 prepare：from/to/density_before）
            _volume_floor_mark = {
                "from": int(_w_before_floor),
                "to": int(weight),
                "density_before": compute_density_g_cc(_w_before_floor, dims_mm),
            }
            _wd_marks["reasons"].append("weight_adjusted_for_volume")

    # mm → cm（物流费率表按 cm 匹配）
    depth: float = dims_mm["length"] / 10.0
    width: float = dims_mm["width"] / 10.0
    height: float = dims_mm["height"] / 10.0
    if depth <= 0:
        depth = 3.0
    if width <= 0:
        width = 2.0
    if height <= 0:
        height = 0.5

    # v0.21 P2: 重量/尺寸合理性打标
    from utils.draft_sanity import check_weight_suspect

    weight_suspect_reason: str = check_weight_suspect(weight, dims_obj)["reason"]
    if weight_suspect_reason:
        logger.warning("⚠️ 定价：重量/尺寸疑似异常（%s）——价格可能不可靠", weight_suspect_reason)
    if _wd_marks.get("reasons"):
        logger.warning(
            "定价：重量/尺寸标疑（%s）——价格基于标疑数据，供审计排查",
            "; ".join(_wd_marks["reasons"]),
        )

    # ── Step 2: margin 配置（请求级覆盖 > extensions > 默认；三档判定唯一口径）──
    from utils.pricing_estimate import is_dual_margin

    ext_margin_raw = extensions.get("margin_rate")
    dual_margin: bool = (margin_floor is not None) or is_dual_margin(
        margin_floor_present="margin_floor" in extensions,
        margin_anchor_present="margin_anchor" in extensions,
        has_margin_rate=(margin_rate is not None) or (ext_margin_raw is not None),
    )
    if margin_rate is not None:
        eff_margin: float = float(margin_rate)
    elif ext_margin_raw is not None:
        eff_margin = float(ext_margin_raw)
    elif dual_margin:
        eff_margin = 1.5  # v0.60 三档日常价默认
    else:
        eff_margin = 0.25

    def _pick(request_val, ext_val, default):
        if request_val is not None:
            return float(request_val)
        if ext_val is not None:
            return float(ext_val)
        return default

    if dual_margin:
        eff_anchor: Optional[float] = _pick(margin_anchor, extensions.get("margin_anchor"), 2.0)
        eff_floor: Optional[float] = _pick(margin_floor, extensions.get("margin_floor"), 0.6)
        eff_vcr: Optional[float] = _pick(variable_cost_rate, extensions.get("variable_cost_rate"), 0.155)
        eff_pvcr: Optional[float] = _pick(
            promo_variable_cost_rate, extensions.get("promo_variable_cost_rate"), 0.245
        )
    else:
        # 单档旧行为：三档参数全不透传（compute_price 缺省 → 旧公式逐字一致）
        eff_anchor = eff_floor = eff_vcr = eff_pvcr = None

    fx_buffer_val: float = float(
        fx_buffer if fx_buffer is not None else extensions.get("fx_buffer", 0.05)
    )

    # provisional 与最终价共用（三档分母一致，杜绝选段漂移）
    _dual_kwargs: Dict[str, Any] = (
        {
            "margin_anchor": eff_anchor,
            "margin_floor": eff_floor,
            "variable_cost_rate": eff_vcr,
            "promo_variable_cost_rate": eff_pvcr,
        }
        if dual_margin
        else {}
    )

    # ── Step 3: 物流费（同源 query_logistics_cost）──
    if query_logistics_cost_fn is not None:
        _qlc = query_logistics_cost_fn
    else:
        from utils.logistics_quote import query_logistics_cost
        _qlc = query_logistics_cost
    logistics_cost, logistics_channel, _logistics_detail = _qlc(
        weight, depth, width, height, tpl_provider, service_level
    )

    # ── Step 4: 定价公式（唯一入口 compute_price；懒 import 保 patch 语义）──
    from utils.pricing_estimate import compute_price

    total_cost_cny: float = cost_cny + logistics_cost + PACKAGING_COST_CNY

    explicit_raw = commission_rate if commission_rate is not None else extensions.get("commission_rate", 0.0)
    explicit_commission: float = float(explicit_raw or 0.0)

    _est_provisional = compute_price(
        total_cost_cny=total_cost_cny,
        margin_rate=eff_margin,
        commission_rate=0.10,
        fx_buffer=fx_buffer_val,
        currency_code=currency_code,
        exchange_rate=exchange_rate,
        **_dual_kwargs,
    )
    _provisional_price: float = float(_est_provisional["price"])
    if currency_code == "RUB":
        _price_rub: Optional[float] = _provisional_price
    elif exchange_rate and exchange_rate > 1:
        _price_rub = _provisional_price * exchange_rate
    else:
        _price_rub = None

    from utils.commission_resolver import pick_price_band, resolve_commission_rate_detail

    band: str = pick_price_band(_price_rub)

    dc_id_str = str(description_category_id) if description_category_id is not None else ""
    dc_id_int = int(dc_id_str) if dc_id_str.isdigit() else None

    if get_category_commission_fn is not None:
        _gcc = get_category_commission_fn
    else:
        from utils.commission_resolver import get_category_commission
        _gcc = get_category_commission

    _commission_detail = resolve_commission_rate_detail(
        description_category_id=dc_id_int,
        price_rub=_price_rub,
        explicit_commission=explicit_commission,
        extensions_commission_segments=extensions.get("commission_segments"),
        get_category_commission_fn=_gcc,
    )
    commission_rate_val: float = _commission_detail["rate"]
    commission_source: str = _commission_detail["source"]
    _commission_stale: bool = bool(_commission_detail.get("stale", False))
    logger.info(
        "佣金来源(source)=%s, 类目=%s, 档=%s, 佣金=%.1f%%%s",
        commission_source, dc_id_str or "N/A", band, commission_rate_val * 100.0,
        ", stale_fallback=缓存行超龄降级" if _commission_stale else "",
    )

    _est = compute_price(
        total_cost_cny=total_cost_cny,
        margin_rate=eff_margin,
        commission_rate=commission_rate_val,
        fx_buffer=fx_buffer_val,
        currency_code=currency_code,
        exchange_rate=exchange_rate,
        **_dual_kwargs,
    )
    price: int = _est["price"]
    old_price: int = _est["old_price"]
    promo_price: Optional[int] = _est.get("promo_price")
    currency_unit = "CNY" if currency_code == "CNY" else "RUB"
    profit_cny: float = _est["profit_cny"]
    profit_rate_actual: float = _est["profit_rate"]
    base_price_for_profit: float = _est["base_price"]

    # ── Step 5: 汇率源 marks ──
    if fx_source == "fallback_12":
        _fx_marks: Dict[str, Any] = {
            "exchange_rate_source": "fallback_12",
            "exchange_rate_fallback": True,
        }
    elif fx_source:
        _fx_marks = {"exchange_rate_source": fx_source}
    else:
        _fx_marks = {}

    # ── Step 6: 组装 pricing_info ──
    pricing_info: Dict[str, Any] = {
        "cost_cny": cost_cny,
        "logistics_cost_cny": logistics_cost,
        "logistics_channel": logistics_channel,
        "packaging_cost_cny": PACKAGING_COST_CNY,
        "total_cost_cny": total_cost_cny,
        "margin_rate": eff_margin,
        "commission_rate": commission_rate_val,
        # stale 时留痕（非 stale 不加键，零行为噪音）
        **({"commission_source": "stale_fallback"} if _commission_stale else {}),
        **({"purchase_cost_suspect": True} if _cost_suspect else {}),
        "fx_buffer": fx_buffer_val,
        "currency_code": currency_code,
        "exchange_rate": exchange_rate if currency_code == "RUB" else 1.0,
        **_fx_marks,
        "price": price,
        "old_price": old_price,
        **(
            {
                "promo_price": promo_price,
                "margin_anchor": eff_anchor,
                "margin_floor": eff_floor,
                "variable_cost_rate": eff_vcr,
            }
            if dual_margin
            else {}
        ),
        "currency_unit": currency_unit,
        "weight_suspect": weight_suspect_reason,
        "wd_audit": {
            "weight_source": _wd_marks.get("weight_source", "draft"),
            "weight_estimated": _wd_marks.get("weight_estimated", False),
            "dimensions_suspected": _wd_marks.get("dimensions_suspected", False),
            "reasons": _wd_marks.get("reasons", []),
            # v0.83.2：体积重兜底触发留痕（{from,to,density_before}；未触发省略键）
            **({"volume_floor_applied": _volume_floor_mark} if _volume_floor_mark else {}),
        },
        "price_formula": "total_cost × (1 + margin) / (1 - commission) [× (1 + fx_buffer) × exchange_rate if RUB]",
        "profit_estimation": {
            "profit_cny": round(profit_cny, 2),
            "profit_rate": round(profit_rate_actual, 4),
            "profit_formula": (
                "净利=售价×(1-佣金-变动成本率)-总成本；profit_rate=净利/售价（销售净利率）"
                if dual_margin
                else "净利=售价-总成本；profit_rate=净利/总成本（成本利润率）"
            ),
            "cost_breakdown": {
                "product_cost_cny": cost_cny,
                "logistics_cost_cny": logistics_cost,
                "packaging_cost_cny": PACKAGING_COST_CNY,
                "total_cost_cny": total_cost_cny,
            },
            "price_breakdown": {
                "base_price": round(base_price_for_profit, 2),
                "final_price": price,
                "old_price": old_price,
                "currency_unit": currency_unit,
            },
            "pricing_factors": {
                "margin_rate_target": eff_margin,
                "commission_rate": commission_rate_val,
                "fx_buffer": fx_buffer_val,
                "exchange_rate_applied": exchange_rate if currency_code == "RUB" else 1.0,
            },
        },
    }
    if debug_state_currency_code is not _UNSET:
        pricing_info["_debug_state_currency_code"] = debug_state_currency_code
    pricing_info["_debug_used_currency_code"] = currency_code

    # ── 审计旁路（estimate/batch 回吐 marks；pricing_node 不传）──
    if audit_out is not None:
        audit_out.update(
            {
                "cost_cny": cost_cny,
                "purchase_cost_suspect": _cost_suspect,
                "weight_g": weight,
                "dims_mm": dict(dims_mm),
                "weight_suspect": weight_suspect_reason,
                "wd_audit": dict(pricing_info["wd_audit"]),
                "volume_floor_applied": _volume_floor_mark,
                "weight_source": _wd_marks.get("weight_source", "draft"),
                "dual_margin": dual_margin,
                "margin_rate": eff_margin,
                "margin_anchor": eff_anchor,
                "margin_floor": eff_floor,
                "variable_cost_rate": eff_vcr,
                "promo_variable_cost_rate": eff_pvcr,
                "fx_buffer": fx_buffer_val,
                "logistics_cost_cny": logistics_cost,
                "logistics_channel": logistics_channel,
                "logistics_source": logistics_source,
                "total_cost_cny": total_cost_cny,
                "commission_rate": commission_rate_val,
                "commission_source": commission_source,
                "commission_stale": _commission_stale,
                "exchange_rate": exchange_rate,
                "exchange_rate_source": (fx_source or ""),
                "currency_code": currency_code,
                "price": price,
                "old_price": old_price,
                "promo_price": promo_price,
                "profit_cny": round(profit_cny, 2),
                "profit_rate": round(profit_rate_actual, 4),
                "base_price": round(base_price_for_profit, 2),
            }
        )
    return pricing_info
