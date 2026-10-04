"""价格计算节点 - 价格计算 + 物流费率匹配（基于Ozon API 3PL + 服务等级 + 评分组）

v0.83 批①：单 SKU 主链纯计算核已抽出到 ``utils/pricing_core.compute_pricing_core``
（pricing_node / estimate_service / /estimate/batch 三处同源）。本节点保留**带副作用**
的部分：Ozon API 兜底查币种/汇率、Sentry 留痕、``price_sanity_guard`` 价差守卫 block、
多 SKU 变体循环 —— 定价行为与抽取前逐字一致。
"""
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
from utils.price_sanity_guard import check_price_sanity  # ✅ v0.73: 价差守卫（Issue5b，锚价在场时校验终价倍数）
# 佣金缓存查询注入点（core 经注入点调用，既保 patch 语义又保持 core 纯净）
from utils.commission_resolver import get_category_commission
# ✅ fix/dedupe-batch1 C7: 管线本地码唯一事实源（禁裸字符串，见 utils/pipeline_error_codes.py）
from utils.pipeline_error_codes import LOCAL_PRICE_GAP_BLOCKED, LOCAL_PRICING_FAILED
import time as _time


logger = get_logger(__name__)


def _commission_fn():
    """取当前模块命名空间的 get_category_commission。

    既有测试族按 ``pn.get_category_commission`` 模块属性 patch（``globals()`` 动态取
    保 patch 生效，不在顶层固化函数对象）。
    """
    return globals().get("get_category_commission")


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
    
    # 🔍 币种解析（v0.83 gate B2 信任序：信封 → 本地凭证行 → Ozon API）
    # 背景：此前信封/凭证都没有时回源 Ozon，查询超时/失败**静默回落默认 RUB** →
    # CNY 合约店发出 RUB 价 → Ozon 拒 currency_differs_from_contract（gate #4 实锤）。
    # 现在按信任序逐级降级，全败 → 显式 failed（CURRENCY_UNRESOLVED），绝不静默 RUB。
    from utils.currency_resolver import normalize_currency, resolve_credential_currency

    currency_code = ""
    currency_source = ""

    # ① 信封显式币种（extensions.currency_code；skill 侧可选注入，最权威）
    _envelope_cc = normalize_currency((extensions or {}).get("currency_code"))
    if _envelope_cc:
        currency_code, currency_source = _envelope_cc, "envelope"

    # ② 本地凭证行币种（纯本地读，不触网不超时；tenant 缺省则跳过——防 mock 场景误查）
    if not currency_code:
        _tenant = str(getattr(state, "user_id", "") or "")
        if _tenant:
            _cred_cc = resolve_credential_currency(_tenant, ozon_client_id)
            if _cred_cc:
                currency_code, currency_source = _cred_cc, "credential"

    # ③ auth_node 透传的 Ozon API 查询结果（非空即权威）
    if not currency_code:
        _state_cc = normalize_currency(state.currency_code)
        if _state_cc:
            currency_code, currency_source = _state_cc, "ozon_api"

    # ③b 节点内 Ozon API 兜底查询（auth_node 查询失败/为空时再试一次）
    if not currency_code and ozon_client_id and ozon_api_key:
        try:
            logger.info("币种仍未解析，fallback调用Ozon API查询店铺货币")
            # F-F01（2026-09-09 审计）：收敛 ozon_post（全局限流 + 429/5xx 重试）
            ozon_data = ozon_post(ozon_client_id, ozon_api_key, "/v1/seller/info", {}, timeout=60)
            company = ozon_data.get('company', {})
            if isinstance(company, dict):
                _api_cc = normalize_currency(company.get('currency', ''))
                if _api_cc:
                    currency_code, currency_source = _api_cc, "ozon_api"
                    logger.info(f"Ozon API查询成功，currency: '{currency_code}'")
        except Exception as e:
            logger.warning(f"Ozon API查询失败: {str(e)}")

    # ④ 全败 → 显式失败（绝不静默 RUB；非永久错误——重试/重采集可恢复）
    if not currency_code:
        logger.error("币种解析失败（信封/本地凭证/Ozon API 均未返回有效币种）→ CURRENCY_UNRESOLVED 阻断")
        return PricingOutput(
            pricing_info={"currency_source": "unresolved"},
            price="",
            old_price="",
            error_message=(
                "[PRICING_FAILED] 店铺币种无法确定（信封/本地凭证/Ozon API 均未返回有效币种），"
                "拒绝按默认币种报价（CNY 合约店错发 RUB 价会被 Ozon 拒 currency_differs_from_contract）"
            ),
            error_code="CURRENCY_UNRESOLVED",
            failed_stage="pricing",
        )

    logger.info(
        "pricing_node最终使用currency_code: '%s' (source=%s)", currency_code, currency_source,
    )
    
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
        # ✅ BL-01（2026-09-11）: 汇率值仍经 _get_exchange_rate（float 契约，既有测试族
        # 按此 mock），来源（pg_cache/live_fetch/fallback_12）经线程键通道取走即清，
        # fallback_12 时在 pricing_info 打双 marks 供审计「价格离谱是否源于兜底汇率」。
        exchange_rate: float = _get_exchange_rate(supabase_url, supabase_key, currency_code)
        _fx_source: str = _consume_fx_source()

        # 店铺 3PL/服务等级探测（Ozon API IO，留节点侧；core 只接最终参数）
        from utils.logistics_quote import get_store_logistics_config, query_logistics_cost
        tpl_provider, service_level = get_store_logistics_config(ozon_client_id, ozon_api_key)
        logger.info("🔍 DEBUG 物流查询参数: tpl=%s, svc=%s, currency=%s",
                    tpl_provider, service_level, currency_code)

        # ── 单 SKU 主链纯计算核（与 estimate/batch 同源）──
        from utils.pricing_core import compute_pricing_core

        _audit: Dict[str, Any] = {}
        pricing_info: Dict[str, Any] = compute_pricing_core(
            draft,
            extensions,
            currency_code=currency_code,
            exchange_rate=exchange_rate,
            fx_source=_fx_source,
            description_category_id=getattr(state, "description_category_id", "") or "",
            tpl_provider=tpl_provider,
            service_level=service_level,
            # v0.83.2：定价链与 prepare 上架链同序启用体积重兜底（normalize→reconcile→
            # floor）——prepare 对卡面/物流按兜底后重量（如 100g→121g）计费，定价若仍按
            # 兜底前重量算则系统性少收运费差；三处同口径后卡面声明/物流计费/定价一致。
            apply_volume_floor=True,
            query_logistics_cost_fn=query_logistics_cost,
            get_category_commission_fn=_commission_fn(),
            audit_out=_audit,
            debug_state_currency_code=state.currency_code,
        )

        # ✅ v0.83 gate B2: 币种来源审计键（envelope/credential/ozon_api）——
        # 供排查「价格为何是该币种」（此前静默 RUB 无法回溯币种从哪来）。
        pricing_info["currency_source"] = currency_source

        price: int = pricing_info["price"]
        old_price: int = pricing_info["old_price"]
        currency_unit: str = pricing_info["currency_unit"]
        logistics_cost: float = pricing_info["logistics_cost_cny"]
        packaging_cost: float = pricing_info["packaging_cost_cny"]
        margin_rate: float = pricing_info["margin_rate"]
        commission_rate: float = pricing_info["commission_rate"]
        _dual_margin: bool = bool(_audit.get("dual_margin"))

        # ✅ v0.85 follow_clone 锚价覆盖（PLAN-follow-clone-v1 §3，用户拍板
        # 2026-10-03）：上架价卡「跟卖列表前 20 报价均值」×factor（缺省 1.0，
        # env FOLLOW_CLONE_PRICE_FACTOR 可调——「比竞品增幅可调/出单后自改」）。
        # - 锚价 = draft.competitor_price（skill 选品时**物化**均值，恒 RUB——
        #   price_sanity 红线：绝不引用化/延迟解析）；
        # - 利润闸：锚价换算后低于 core 底线价（promo_price 档）→ 如实拒绝
        #   （宁缺毋滥，同 commission_fallback_not_profitable 语义）；
        # - 价差守卫自动覆盖：均值同时物化进 discovery_meta.ozon_price（同锚槽）。
        _fc_ext = extensions if isinstance(extensions, dict) else {}
        if _fc_ext.get("follow_clone"):
            try:
                _fc_anchor_rub = float(draft.get("competitor_price") or 0)
            except (TypeError, ValueError):
                _fc_anchor_rub = 0.0
            if _fc_anchor_rub > 0:
                import os as _os
                try:
                    _fc_factor = float(_os.getenv("FOLLOW_CLONE_PRICE_FACTOR", "1.0") or 1.0)
                except ValueError:
                    _fc_factor = 1.0
                if _fc_factor <= 0:
                    logger.warning("FOLLOW_CLONE_PRICE_FACTOR 非法（%.4g），回落 1.0", _fc_factor)
                    _fc_factor = 1.0
                _fc_target_rub = _fc_anchor_rub * _fc_factor
                # CNY 跨境店：锚价 RUB → CNY（exchange_rate = 1 CNY 兑多少 RUB）
                _fc_price = (round(_fc_target_rub / exchange_rate)
                             if currency_code == "CNY" and exchange_rate > 0
                             else round(_fc_target_rub))
                _fc_price = max(1, _fc_price)
                _fc_floor = int(pricing_info.get("promo_price") or pricing_info.get("price") or 0)
                if _fc_floor > 0 and _fc_price < _fc_floor:
                    logger.error(
                        "⛔ follow_clone 利润闸拒绝：锚价 %s RUB×%.4g → %s %s 低于底线价 %s"
                        "（成本链 compute_pricing_core 权威），宁缺毋滥",
                        _fc_anchor_rub, _fc_factor, _fc_price, currency_unit, _fc_floor)
                    return PricingOutput(
                        pricing_info={"follow_clone_anchor_rub": _fc_anchor_rub,
                                      "floor_price": _fc_floor},
                        price="",
                        old_price="",
                        error_message=(
                            f"[PRICING_FAILED] follow_clone 锚价 {round(_fc_anchor_rub)} RUB "
                            f"×{_fc_factor} = {_fc_price} {currency_unit} 低于底线价 "
                            f"{_fc_floor}（利润闸拒绝，换货源或调 factor）"
                        ),
                        error_code=LOCAL_PRICING_FAILED,
                        failed_stage="pricing",
                    )
                from utils.pricing_estimate import enforce_old_price_rule
                _fc_old = enforce_old_price_rule(_fc_price, round(_fc_price * 1.3))
                pricing_info["price_source"] = "follow_clone_anchor"
                pricing_info["anchor_price_rub"] = round(_fc_anchor_rub)
                pricing_info["anchor_factor"] = _fc_factor
                price = _fc_price
                old_price = _fc_old
                pricing_info["price"] = price
                pricing_info["old_price"] = old_price
                logger.info(
                    "🧬 follow_clone 锚价覆盖：anchor=%s RUB ×%.4g → price=%s %s "
                    "(old=%s, floor=%s)",
                    round(_fc_anchor_rub), _fc_factor, price, currency_unit, old_price, _fc_floor)

        # ✅ v0.37 A2/B2: 重量/尺寸标疑放行但上报 Sentry（留痕，不阻断定价）
        _wd_audit = _audit.get("wd_audit") or {}
        if _wd_audit.get("reasons"):
            try:
                from utils.sentry_setup import capture_task_error
                _dims_mm = _audit.get("dims_mm") or {}
                capture_task_error(
                    message=(
                        f"[WEIGHT_DIM_SUSPECT] weight={_audit.get('weight_g')}g dims="
                        f"{_dims_mm.get('length')}×{_dims_mm.get('width')}×{_dims_mm.get('height')}mm "
                        f"source={_audit.get('weight_source')} "
                        f"reasons={'; '.join(_wd_audit['reasons'])}"
                    ),
                    task_id=str(getattr(state, "task_id", "") or ""),
                    tenant_id=str(getattr(state, "tenant_id", "") or ""),
                    token=str(getattr(state, "token", "") or ""),
                )
            except Exception:
                pass


        _price_log = (
            f"价格计算成功(三档): price(日常)={price} {currency_unit}, "
            f"old_price(划线)={old_price} {currency_unit}, "
            f"promo_price(促销)={pricing_info.get('promo_price')} {currency_unit}"
            if _dual_margin
            else f"价格计算成功: price={price} {currency_unit}, old_price={old_price} {currency_unit}"
        )
        logger.info(f"{_price_log}, currency_code={currency_code}")

        # ✅ 多SKU变体定价：为每个variant计算独立价格（副作用：变体循环留节点侧）
        # ⚠️ v0.60: 优先用 state.variants（与 prepare 同源，ingest 提取），
        # 避免 draft.variants 与 state.variants 漂移导致错价（draft 兜底）
        variants_list: list = state.variants if getattr(state, "variants", None) else (
            draft.get("variants", []) if isinstance(draft, dict) else []
        )
        # 三档 kwargs（档位判定唯一口径在 core，此处按 core 回吐的 audit 重建）
        _dual_kwargs: Dict[str, Any] = (
            {
                "margin_anchor": _audit.get("margin_anchor"),
                "margin_floor": _audit.get("margin_floor"),
                "variable_cost_rate": _audit.get("variable_cost_rate"),
                "promo_variable_cost_rate": _audit.get("promo_variable_cost_rate"),
            }
            if _dual_margin
            else {}
        )
        variant_prices: list = []
        if variants_list and isinstance(variants_list, list) and len(variants_list) > 0:
            logger.info(f"🔄 多SKU变体定价：共{len(variants_list)}个变体")
            # 懒导入（模块属性可 patch——test_error_code_wiring_v0772 等按此锁异常出口）
            from utils.pricing_estimate import compute_price
            _fx_buffer_val = float(_audit.get("fx_buffer", 0.05))
            for var in variants_list:
                if not isinstance(var, dict):
                    continue
                # 使用variant的price作为采购成本（1688售价即为我们的采购成本）
                var_cost_cny: float = float(var.get("price", 0) or _audit.get("cost_cny") or 0)
                var_total_cost: float = var_cost_cny + logistics_cost + packaging_cost

                # ⚠️ v0.60: 变体定价统一走 compute_price（与单 SKU 同源，
                # 消除旧内联公式 ×1.15 vs 单 SKU ×1.2 的 old_price 漂移）
                var_est = compute_price(
                    total_cost_cny=var_total_cost,
                    margin_rate=margin_rate,
                    commission_rate=commission_rate,
                    fx_buffer=_fx_buffer_val,
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
        # ✅ v0.77.3：传终价币种——锚价恒 RUB，非 RUB 店（如 CNY 测试店）跨币种
        # 直接比倍数是假阳性（gate 实测 608₽÷30¥=20× 冤杀六卡），守卫内部跳比。
        # ✅ v0.78 批C（fix/guard-precision-v1 Q8）：skip 增强为「换算真比」——
        # ⚠️ 不能直接透传 core 用的 exchange_rate：CNY 店 _get_exchange_rate 恒返
        # 1.0（CNY 定价路径不使用汇率），1.0 不是换算汇率，透传会把 608₽÷1.0
        # 当 608¥ 又比出 20× 假阳性（gate 冤杀形态复活）。非 RUB 店且有锚时，
        # 在此按 RUB 方向另取真实 CNY→RUB 汇率（fx 三级链 pg_cache → live →
        # fallback 12）传给守卫；无锚不白查（守卫恒 ok），取汇率失败按无汇率
        # 处理（守卫内部维持 skip，绝不 raise）。RUB 店同币种不换算，传值无影响。
        _guard_fx_rate: float = float(exchange_rate or 0.0)
        if (str(currency_code or "RUB").strip().upper() != "RUB"
                and _guard_fx_rate <= 1
                and isinstance(_meta, dict) and _meta):
            try:
                _guard_fx_rate = float(_get_exchange_rate(supabase_url, supabase_key, "RUB"))
            except Exception as _fx_e:
                logger.warning("价差守卫取换算汇率失败（按无汇率 skip 处理）: %s", _fx_e)
                _guard_fx_rate = 0.0
        _verdict, _gap = check_price_sanity(
            float(price), _meta if isinstance(_meta, dict) else None,
            final_currency=str(currency_code or "RUB"),
            exchange_rate=_guard_fx_rate,
        )
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
                error_code=LOCAL_PRICE_GAP_BLOCKED,
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
            # ✅ v0.77.2: 错误码透出（留存表 error_code 列）
            error_code=LOCAL_PRICING_FAILED,
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
