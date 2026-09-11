"""价格计算节点 - 价格计算 + 物流费率匹配（基于Ozon API 3PL + 服务等级 + 评分组）"""
import os
import json
import logging
import threading
from typing import Any, Dict, Optional, Tuple
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from runtime.context import Context
from graphs.state import PricingInput, PricingOutput
from utils.logger import get_logger, set_trace_context, log_ozon_api_call
from utils.ozon_client import ozon_post  # F-F01: Ozon 直连统一入口
from utils.draft_sanity import check_weight_suspect  # v0.21 P2 定价防线
from utils.price_sanity_guard import check_price_sanity  # ✅ v0.73: 价差守卫（Issue5b，锚价在场时校验终价倍数）
from utils.commission_resolver import (  # 任务 1.3: 佣金唯一解析入口（explicit>缓存表>segments>0.10）
    get_category_commission,
    pick_price_band,
    resolve_commission_rate_detail,  # BL-24 一期: 180d 新鲜度闸（detail 版带 stale 标志）
)
import time as _time


logger = get_logger(__name__)


def pricing_node(state: PricingInput, config: RunnableConfig, runtime: Runtime[Context]) -> PricingOutput:
    """
    title: 价格计算节点
    desc: 查询物流价格表（standard渠道）+ 计算最优惠价格 + 根据currency_code决定货币类型
    integrations: Supabase
    """
    ctx = runtime.context
    
    # 空值判断
    draft = state.draft
    extensions = state.extensions or {}
    supabase_url = state.supabase_url
    supabase_key = state.supabase_key
    ozon_client_id = state.ozon_client_id
    ozon_api_key = state.ozon_api_key
    
    # 🔍 获取currency_code（关键：从GlobalState获取，如果为空则fallback查询Ozon API）
    currency_code = state.currency_code
    logger.info(f"pricing_node收到currency_code: '{state.currency_code}' (原始值)")
    
    # Fallback：如果currency_code为空，直接调用Ozon API查询
    if not currency_code and ozon_client_id and ozon_api_key:
        try:
            logger.info("currency_code为空，fallback调用Ozon API查询店铺货币")
            # F-F01（2026-09-09 审计）：收敛 ozon_post（全局限流 + 429/5xx 重试）
            ozon_data = ozon_post(ozon_client_id, ozon_api_key, "/v1/seller/info", {}, timeout=60)
            company = ozon_data.get('company', {})
            if isinstance(company, dict):
                currency_code = company.get('currency', '')
                logger.info(f"Ozon API查询成功，currency: '{currency_code}'")
        except Exception as e:
            logger.warning(f"Ozon API查询失败: {str(e)}")
    
    # 如果仍然为空，使用默认值"RUB"
    if not currency_code:
        currency_code = "RUB"
        logger.warning("currency_code仍然为空，使用默认值RUB")
    
    logger.info(f"pricing_node最终使用currency_code: '{currency_code}'")
    
    # 空值判断：draft为None或完全空字典时报错
    if draft is None:
        logger.error("Draft data is None")
        return PricingOutput(
            pricing_info={},
            price="",
            old_price="",
            error_message="Draft data is None",
            # ✅ v0.73 Task8: Output 默认值已归零（operator.add 粘连根治），失败出口
            # 必须显式带 failed_stage——否则 _graph_result_is_failed 的
            # (error_message 且 failed_stage) 通道失守 → 假 completed。
            failed_stage="pricing",
        )
    
    # 如果draft是空字典，使用默认值继续处理
    if not draft or draft == {}:
        logger.warning("Draft data is empty, using default values")
        draft = {
            "cost_cny": 50.0,  # 默认成本50元
            "weight": 500,  # 默认重量500克
            "depth": 20,  # 默认尺寸
            "width": 15,
            "height": 10
        }
    
    try:
        # Step 1: 获取基础数据
        # ✅ 关键修复：兼容purchase_cost字段（扁平信封使用purchase_cost而非cost_cny）
        cost_cny: float = float(draft.get("cost_cny", 0) or draft.get("purchase_cost", 0) or 0)
        # ⚠️ v0.37 A2/B2 修复: 重量/尺寸归一化统一走公共模块（与 prepare 同源）。
        # 旧逻辑在此独立实现 <10g×1000 轻物误伤（真实 3g→3000g → 物流费爆炸）。
        # 公共模块只对缺失兜底、对已有值仅标记，绝不改写。
        from utils.weight_dimension_normalizer import normalize_weight_dimensions

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
        # mm → cm（物流费率表按 cm 匹配）
        depth: float = dims_mm["length"] / 10.0
        width: float = dims_mm["width"] / 10.0
        height: float = dims_mm["height"] / 10.0
        # 逐维度补默认值（depth→3cm, width→2cm, height→0.5cm，仅当兜底后仍 0）
        if depth <= 0:
            depth = 3.0
        if width <= 0:
            width = 2.0
        if height <= 0:
            height = 0.5

        # v0.21 P2: 重量/尺寸合理性打标（第二道防线；main.py 已拦超限，这里兜底标记）
        weight_suspect_reason: str = check_weight_suspect(weight, dims_obj)["reason"]
        if weight_suspect_reason:
            logger.warning("⚠️ 定价节点：重量/尺寸疑似异常（%s）——价格可能不可靠", weight_suspect_reason)
        if _wd_marks.get("reasons"):
            logger.warning(
                "定价节点：重量/尺寸标疑（%s）——价格基于标疑数据，供审计排查",
                "; ".join(_wd_marks["reasons"]),
            )
            # ✅ v0.37 A2/B2: 标疑放行但上报 Sentry（留痕，不阻断定价）
            try:
                from utils.sentry_setup import capture_task_error
                capture_task_error(
                    message=(
                        f"[WEIGHT_DIM_SUSPECT] weight={weight}g dims="
                        f"{dims_mm['length']}×{dims_mm['width']}×{dims_mm['height']}mm "
                        f"source={_wd_marks.get('weight_source')} "
                        f"reasons={'; '.join(_wd_marks['reasons'])}"
                    ),
                    task_id=str(getattr(state, "task_id", "") or ""),
                    tenant_id=str(getattr(state, "tenant_id", "") or ""),
                    token=str(getattr(state, "token", "") or ""),
                )
            except Exception:
                pass
        
        # cost_cny为0时使用默认值
        if cost_cny <= 0:
            cost_cny = 10.0
            logger.warning("⚠️ cost_cny为0或空，使用默认值: 10 CNY")
        
        # 获取扩展配置
        fx_buffer: float = float(extensions.get("fx_buffer", 0.05))  # 汇率缓冲 5%

        # ✅ v0.65 三档默认激活：与 estimate_service 同规则——floor/anchor 在场，或 margin 键全缺 → 三档。
        # 用户决策（2026-08-21 + v0.65 拍板）：未配置店铺自动三档（margin 默认 1.5 / anchor 2.0 /
        # floor 0.6 / vcr 0.155 / pvcr 0.245）；显式配了 margin_rate 且无 floor/anchor 的店保持旧单档
        # （存量显式配置向后兼容——单档缺省 margin 0.25，不透传三档参数，compute_price 旧公式逐字一致）。
        ext_margin_raw = extensions.get("margin_rate")
        dual_margin: bool = (
            "margin_floor" in extensions or "margin_anchor" in extensions
            or ext_margin_raw is None
        )
        if dual_margin:
            margin_rate: float = float(ext_margin_raw) if ext_margin_raw is not None else 1.5  # 三档日常价（默认 1.5）
            margin_anchor: Optional[float] = float(extensions.get("margin_anchor", 2.0))  # 划线原价（用户拍板默认 2.0）
            margin_floor: Optional[float] = float(extensions.get("margin_floor", 0.6))    # 促销底线（用户拍板默认 0.6）
            variable_cost_rate: Optional[float] = float(extensions.get("variable_cost_rate", 0.155))  # 日常变动成本
            promo_variable_cost_rate: Optional[float] = float(extensions.get("promo_variable_cost_rate", 0.245))  # 促销变动成本
        else:
            margin_rate = float(ext_margin_raw or 0.25)  # 旧单档利润率（显式 margin_rate 向后兼容）
            margin_anchor = None
            margin_floor = None
            variable_cost_rate = None
            promo_variable_cost_rate = None
        
        # Step 2: 查询物流费率（PG logistics_rates + Ozon API获取3PL/服务等级）
        # ⚠️ v0.29.x: 改用公共模块 logistics_quote(与 /api/v1/logistics/quote 端点同源)
        from utils.logistics_quote import get_store_logistics_config, query_logistics_cost
        tpl_provider, service_level = get_store_logistics_config(ozon_client_id, ozon_api_key)
        logger.info(f"🔍 DEBUG 物流查询参数: weight={weight}g, dims={depth}x{width}x{height}cm, tpl={tpl_provider}, svc={service_level}, cost_cny={cost_cny}")
        logistics_cost, logistics_channel, _detail = query_logistics_cost(
            weight, depth, width, height, tpl_provider, service_level
        )
        
        # Step 3: 查询包装成本（固定值）
        packaging_cost: float = 2.0  # CNY
        
        # Step 4: 查询汇率（根据currency_code决定汇率方向）
        # ✅ BL-01（2026-09-11）: 汇率值仍经 _get_exchange_rate（float 契约，既有测试族
        # 按此 mock），来源（pg_cache/live_fetch/fallback_12）经线程键通道取走即清，
        # fallback_12 时在 pricing_info 打双 marks 供审计「价格离谱是否源于兜底汇率」。
        exchange_rate: float = _get_exchange_rate(supabase_url, supabase_key, currency_code)
        _fx_source: str = _consume_fx_source()
        _fx_marks: Dict[str, Any] = (
            {"exchange_rate_source": "fallback_12", "exchange_rate_fallback": True}
            if _fx_source == "fallback_12"
            else ({"exchange_rate_source": _fx_source} if _fx_source else {})
        )
        
        # Step 5+6: 价格计算公式（M1.2 共享公式：utils/pricing_estimate.compute_price 唯一定义处，
        # 与 estimate 端点同源。公式 = 总成本 × (1+margin)/(1-commission) [× (1+fx_buffer)×汇率 if RUB]）
        # 总成本 = 产品成本 + 物流成本 + 包装成本
        total_cost_cny: float = cost_cny + logistics_cost + packaging_cost
        
        from utils.pricing_estimate import compute_price
        
        # ✅ 任务 1.3: 佣金三重 bug 修复（provisional-price band pass）
        # 旧逻辑: 调 Ozon prices 接口用 offer_id 空数组 filter → 查不到数据 → 恒 fallback 0.10。
        # 新逻辑: 佣金档位依赖售价、售价依赖佣金（鸡生蛋）——先用 0.10 算临时价（仅选档，
        #         非最终价依据）→ 得 RUB 临时售价 → 选价格档 → resolve_commission_rate
        #         （explicit > 缓存表 band 选段 > extensions segments > 0.10）→ 用真实佣金重算最终价。
        explicit_commission: float = float(extensions.get("commission_rate", 0.0))
        
        _est_provisional = compute_price(
            total_cost_cny=total_cost_cny,
            margin_rate=margin_rate,
            commission_rate=0.10,
            fx_buffer=fx_buffer,
            currency_code=currency_code,
            exchange_rate=exchange_rate,
        )
        _provisional_price: float = float(_est_provisional["price"])
        # 临时售价 → RUB 档位判定价：
        # - RUB 店铺：price 即 RUB，直接用
        # - CNY 店铺：有真实汇率(>1)时换算成 RUB 等价价选档
        # - CNY 且无有效汇率 / 货币不明：中性档 leq_5000（避免低估佣金亏钱）
        if currency_code == "RUB":
            _price_rub = _provisional_price
        elif exchange_rate and exchange_rate > 1:
            _price_rub = _provisional_price * exchange_rate
        else:
            _price_rub = None
        band: str = pick_price_band(_price_rub) if _price_rub is not None else "leq_5000"
        
        dc_id: str = getattr(state, "description_category_id", "") or ""
        dc_id_int = int(dc_id) if str(dc_id).isdigit() else None
        
        # ✅ BL-24 一期: 缓存行 180d 新鲜度闸——超龄行不再直接采信，降级
        # segments/fallback（resolve_commission_rate_detail 内部处理），
        # stale=True 时 pricing_info marks 留 commission_source="stale_fallback" 供审计。
        _commission_detail = resolve_commission_rate_detail(
            description_category_id=dc_id_int,
            price_rub=_price_rub,
            explicit_commission=explicit_commission,
            extensions_commission_segments=extensions.get("commission_segments"),
            get_category_commission_fn=get_category_commission,
        )
        commission_rate: float = _commission_detail["rate"]
        commission_source: str = _commission_detail["source"]
        _commission_stale: bool = bool(_commission_detail.get("stale", False))
        logger.info(
            f"佣金来源(source)={commission_source}, 类目={dc_id or 'N/A'}, "
            f"档={band}, 佣金={commission_rate*100:.1f}%"
            + (", stale_fallback=缓存行超龄降级" if _commission_stale else "")
        )
        
        # 三档时透传新参数给 compute_price（唯一定价公式入口）；单档时全不传 → compute_price 保持旧行为
        _dual_kwargs: Dict[str, Any] = (
            {
                "margin_anchor": margin_anchor,
                "margin_floor": margin_floor,
                "variable_cost_rate": variable_cost_rate,
                "promo_variable_cost_rate": promo_variable_cost_rate,
            }
            if dual_margin
            else {}
        )

        _est = compute_price(
            total_cost_cny=total_cost_cny,
            margin_rate=margin_rate,
            commission_rate=commission_rate,
            fx_buffer=fx_buffer,
            currency_code=currency_code,
            exchange_rate=exchange_rate,  # CNY 时 _get_exchange_rate 返回 1.0（CNY 路径不使用）
            **_dual_kwargs,
        )
        price: int = _est["price"]
        old_price: int = _est["old_price"]
        promo_price: Optional[int] = _est.get("promo_price")
        currency_unit = "CNY" if currency_code == "CNY" else "RUB"
        profit_cny: float = _est["profit_cny"]
        profit_rate_actual: float = _est["profit_rate"]
        base_price_for_profit: float = _est["base_price"]
        
        # ⚠️ v0.26 决策：跟卖不再用竞品价定价（已删除原「竞品价 ≥ 成本×1.3 保持竞品价」分支）。
        # 原因（用户拍板，2026-08-05）：竞品价（RUB）与成本（CNY）直接比较是单位 bug，
        # 一旦触发会把竞品 RUB 数当 CNY 定价 → 暴利 10 倍 / 亏损；且跟卖默认 follow_type=hand
        # 是重做类目/属性/生图的产品卡，非 1:1 复制，价格必须按我方成本公式算，防亏钱。
        # 竞品价仅作审计参考（state.competitor_price 保留），不参与定价。
        # （原分支同时存在 schema 缺陷：PricingInput 缺 competitor_price 字段，条件边转换
        #  会剥掉该字段 → 分支本就永不触发；现显式删除，避免未来补字段后误激活。）
        extensions = getattr(state, 'extensions', {}) or {}
        
        # Step 7: 组装价格信息（包含profit_estimation）
        pricing_info: Dict[str, Any] = {
            "cost_cny": cost_cny,
            "logistics_cost_cny": logistics_cost,
            "logistics_channel": logistics_channel,
            "packaging_cost_cny": packaging_cost,
            "total_cost_cny": total_cost_cny,
            "margin_rate": margin_rate,
            "commission_rate": commission_rate,
            # ✅ BL-24 一期: 缓存行超龄降级（stale）时留痕——审计可回答「这个价
            # 的佣金哪来的」；非 stale 不加键（零行为噪音，与现状逐字一致）。
            **({"commission_source": "stale_fallback"} if _commission_stale else {}),
            "fx_buffer": fx_buffer,
            "currency_code": currency_code,
            "exchange_rate": exchange_rate if currency_code == "RUB" else 1.0,
            # BL-01: 汇率源留痕（fallback_12 额外标 fallback=True；pg_cache/live_fetch 只带 source）
            **_fx_marks,
            "price": price,
            "old_price": old_price,
            **({"promo_price": promo_price,
                "margin_anchor": margin_anchor,
                "margin_floor": margin_floor,
                "variable_cost_rate": variable_cost_rate} if dual_margin else {}),
            "currency_unit": currency_unit,
            "weight_suspect": weight_suspect_reason,
            # ✅ v0.37 A2/B2: 重量/尺寸归一化标疑明细（weight_source/reasons），
            # 供审计排查「价格离谱是否源于重量误伤」
            "wd_audit": {
                "weight_source": _wd_marks.get("weight_source", "draft"),
                "weight_estimated": _wd_marks.get("weight_estimated", False),
                "dimensions_suspected": _wd_marks.get("dimensions_suspected", False),
                "reasons": _wd_marks.get("reasons", []),
            },
            "price_formula": "total_cost × (1 + margin) / (1 - commission) [× (1 + fx_buffer) × exchange_rate if RUB]",
            # ✅ 新增：利润预估明细
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
                    "packaging_cost_cny": packaging_cost,
                    "total_cost_cny": total_cost_cny
                },
                "price_breakdown": {
                    "base_price": round(base_price_for_profit, 2),
                    "final_price": price,
                    "old_price": old_price,
                    "currency_unit": currency_unit
                },
                "pricing_factors": {
                    "margin_rate_target": margin_rate,
                    "commission_rate": commission_rate,
                    "fx_buffer": fx_buffer,
                    "exchange_rate_applied": exchange_rate if currency_code == "RUB" else 1.0
                }
            },
            # 🔍 调试信息
            "_debug_state_currency_code": state.currency_code,
            "_debug_used_currency_code": currency_code
        }
        
        _price_log = (
            f"价格计算成功(三档): price(日常)={price} {currency_unit}, "
            f"old_price(划线)={old_price} {currency_unit}, promo_price(促销)={promo_price} {currency_unit}"
            if dual_margin
            else f"价格计算成功: price={price} {currency_unit}, old_price={old_price} {currency_unit}"
        )
        logger.info(f"{_price_log}, currency_code={currency_code}")
        
        # ✅ 多SKU变体定价：为每个variant计算独立价格
        # ⚠️ v0.60: 优先用 state.variants（与 prepare 同源，ingest 提取），
        # 避免 draft.variants 与 state.variants 漂移导致错价（draft 兜底）
        variants_list: list = state.variants if getattr(state, "variants", None) else (
            draft.get("variants", []) if isinstance(draft, dict) else []
        )
        variant_prices: list = []
        if variants_list and isinstance(variants_list, list) and len(variants_list) > 0:
            logger.info(f"🔄 多SKU变体定价：共{len(variants_list)}个变体")
            for var in variants_list:
                if not isinstance(var, dict):
                    continue
                # 使用variant的price作为采购成本（1688售价即为我们的采购成本）
                var_cost_cny: float = float(var.get("price", 0) or cost_cny)
                var_total_cost: float = var_cost_cny + logistics_cost + packaging_cost

                # ⚠️ v0.60: 变体定价统一走 compute_price（与单 SKU 同源，
                # 消除旧内联公式 ×1.15 vs 单 SKU ×1.2 的 old_price 漂移）
                var_est = compute_price(
                    total_cost_cny=var_total_cost,
                    margin_rate=margin_rate,
                    commission_rate=commission_rate,
                    fx_buffer=fx_buffer,
                    currency_code=currency_code,
                    exchange_rate=exchange_rate,
                    **_dual_kwargs,
                )
                var_price: int = var_est["price"]
                var_old_price: int = var_est["old_price"]
                var_promo_price: Optional[int] = var_est.get("promo_price")
                
                var_sku_id: str = str(var.get("sku_id", ""))
                var_color: str = str(var.get("color", ""))
                var_entry: Dict[str, Any] = {
                    "sku_id": var_sku_id,
                    "color": var_color,
                    "price": var_price,
                    "old_price": var_old_price,
                    "currency_code": currency_code
                }
                if var_promo_price is not None:
                    var_entry["promo_price"] = var_promo_price
                variant_prices.append(var_entry)
                _var_log = f"price={var_price} {currency_unit}, old_price={var_old_price}"
                if var_promo_price is not None:
                    _var_log += f", promo_price={var_promo_price} {currency_unit}"
                logger.info(f"  变体 {var_sku_id}: color={var_color}, cost={var_cost_cny}CNY → {_var_log}")
            
            pricing_info["variant_prices"] = variant_prices
            logger.info(f"✅ 多SKU变体定价完成：{len(variant_prices)}个变体价格已计算")

        # ✅ v0.73 价差守卫（Issue5b worker 侧防线，utils/price_sanity_guard 唯一入口）：
        # 锚价（信封 extensions.discovery_meta.ozon_price，退 min_competing_price）在场时
        # 校验终价倍数——block → failed 出清 + 入采集箱（非致命）；warn → 放行 +
        # pricing_info.price_gap_warn 留痕；无锚（graph 流 discovery_meta 空，含生产转盘
        # 本例）恒 ok 零误杀。final_price<=0 由 compute_price 除零兜底管，本守卫不越界。
        envelope_raw = getattr(state, "envelope", None)
        _meta = {}
        if isinstance(envelope_raw, dict):
            _ext = envelope_raw.get("extensions")
            if isinstance(_ext, dict):
                _meta = _ext.get("discovery_meta") or {}
        _verdict, _gap = check_price_sanity(float(price), _meta if isinstance(_meta, dict) else None)
        if _verdict == "block":
            _reason = (
                f"价差守卫：终价 {price}{currency_unit} 与选品锚价 {_gap['anchor_price']}"
                f"（{_gap['anchor_source']}）差距超 {_gap['ratio']} 倍，疑似货源错配"
            )
            # [PRICING_FAILED] 前缀复用 v0.14 P1-4 既有路由通道（route_after_pricing
            # 按该标记阻断）；✅ v0.73 Task8 起 failed_stage 默认值归零——失败出口显式
            # 带 "pricing"、成功恒空，不再「恒 pricing 无法区分成败」。
            _error = f"[PRICING_FAILED] {_reason}"
            _box_notice = ""
            try:
                # 入采集箱（幂等、非致命）——模仿 assemble._maybe_create_blocked_draft 的
                # try/except 模式；缺 title 时服务层 400 → None，同样静默。
                from utils.blocked_draft_box import create_blocked_draft, format_box_notice
                _tenant = str(getattr(state, "user_id", "") or "").strip()
                if _tenant:
                    _env = envelope_raw if (isinstance(envelope_raw, dict) and envelope_raw.get("draft")) else {"draft": draft or {}}
                    _box_notice = format_box_notice(create_blocked_draft(_tenant, _env, [], _reason))
            except Exception as _box_e:
                logger.warning("价差守卫阻断入箱失败（非致命）: %s", _box_e)
            # ✅ v0.73 终审 I2: notice 前缀带原因（task_processor error_message 优先取
            # notice——此前纯入箱文案，任务行看不到「价差 N 倍」根因）。对齐 Task2
            # _final_result_blocked_to_box 的「拦截：原因，入箱结果」拼接模式。
            _notice = (
                f"价差守卫拦截：终价 {price}{currency_unit} 与选品锚价 {_gap['anchor_price']}"
                f"（{_gap['anchor_source']}）差距超 {_gap['ratio']} 倍（疑似货源错配）"
            )
            if _box_notice:
                _notice = f"{_notice}，{_box_notice}"
            logger.error("⛔ 价差守卫阻断（不上架错配货源）: %s %s", _reason, _notice)
            return PricingOutput(
                pricing_info={"price_gap_block": _gap},
                price="",
                old_price="",
                notice=_notice,
                error_message=_error,
                failed_stage="pricing",
            )
        if _verdict == "warn":
            pricing_info["price_gap_warn"] = _gap
            logger.warning(
                "⚠️ 价差守卫告警（放行）：终价 %s%s vs 选品锚价 %s（%s）差 %.1f 倍，供审计排查",
                price, currency_unit, _gap["anchor_price"], _gap["anchor_source"], _gap["ratio"],
            )

        return PricingOutput(
            pricing_info=pricing_info,
            price=str(price),
            old_price=str(old_price),
            error_message=""
        )
        
    except Exception as e:
        logger.error(f"价格计算失败: {str(e)}")
        return PricingOutput(
            pricing_info={},
            price="",
            old_price="",
            # ⚠️ v0.14 P1-4: [PRICING_FAILED] 标记，graph 检测后阻断管线，不再用 ¥1000 兜底上架
            error_message=f"[PRICING_FAILED] Pricing calculation failed: {str(e)}",
            # ✅ v0.73 Task8: 失败出口显式带 failed_stage（默认值已归零，见上）
            failed_stage="pricing",
        )



# ── BL-01: 汇率来源线程键通道 ──
# _get_exchange_rate 的返回值契约是 float（5 个既有测试族按该契约 mock 此函数），
# 汇率来源（pg_cache/live_fetch/fallback_12）经线程键 dict 传给调用点打 marks：
# pricing_node 同步消费（写后即取即清），50 并发任务池线程间不串线。
_FX_SOURCE_BY_THREAD: Dict[int, str] = {}


def _record_fx_source(source: str) -> None:
    """记录本次汇率来源（线程键隔离，供调用点消费）。"""
    _FX_SOURCE_BY_THREAD[threading.get_ident()] = source


def _consume_fx_source() -> str:
    """取走并清空当前线程的汇率来源。

    无记录返回空串——mock 掉 _get_exchange_rate 的既有测试/历史路径拿不到来源，
    调用点不打 marks，行为与改动前一致（消费语义同时保证无残留串扰）。
    """
    return _FX_SOURCE_BY_THREAD.pop(threading.get_ident(), "")


def _get_exchange_rate(supabase_url: str, supabase_key: str, currency_code: str = "RUB") -> float:
    """查询汇率（根据currency_code决定汇率方向）

    ✅ BL-01（2026-09-11）: CNY→RUB 路径改走 utils/fx_rate_service.resolve_cny_rub_rate
    三级源链（PG 缓存 → er-api live 拉取并回写死缓存 → 12.0 兜底）——根治
    set_exchange_rate 全仓零调用导致的「缓存过期后恒兜底 12.0 无告警」死缓存事故。
    返回值契约保持 float（既有测试按此 mock 本函数）；来源经 _record_fx_source
    传给调用点进 pricing_info.marks。
    """
    if currency_code == "CNY":
        _record_fx_source("")  # CNY 路径无汇率源，顺手清线程残留
        return 1.0

    from utils.fx_rate_service import resolve_cny_rub_rate
    rate, source = resolve_cny_rub_rate()
    _record_fx_source(source)
    if source == "fallback_12":
        logger.warning("PG 汇率缓存未命中，使用默认汇率 12.0")
    else:
        logger.info("汇率查询成功: CNY→RUB = %s (source=%s)", rate, source)
    return rate
