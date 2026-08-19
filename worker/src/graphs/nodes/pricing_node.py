"""价格计算节点 - 价格计算 + 物流费率匹配（基于Ozon API 3PL + 服务等级 + 评分组）"""
import os
import json
import logging
import math
import requests
from utils.http_session import session
from typing import Any, Dict, Optional, Tuple
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from runtime.context import Context
from graphs.state import PricingInput, PricingOutput
from utils.logger import get_logger, set_trace_context, log_ozon_api_call
from utils.draft_sanity import check_weight_suspect  # v0.21 P2 定价防线
from utils.commission_resolver import (  # 任务 1.3: 佣金唯一解析入口（explicit>缓存表>segments>0.10）
    get_category_commission,
    pick_price_band,
    resolve_commission_rate,
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
            ozon_url = "https://api-seller.ozon.ru/v1/seller/info"
            ozon_headers = {
                'Client-Id': ozon_client_id,
                'Api-Key': ozon_api_key,
                'Content-Type': 'application/json'
            }
            ozon_response = session.post(ozon_url, headers=ozon_headers, json={}, timeout=60)
            ozon_data = ozon_response.json()
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
            error_message="Draft data is None"
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
        margin_rate: float = float(extensions.get("margin_rate", 0.25))  # 利润率 25%
        
        fx_buffer: float = float(extensions.get("fx_buffer", 0.05))  # 汇率缓冲 5%
        
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
        exchange_rate: float = _get_exchange_rate(supabase_url, supabase_key, currency_code)
        
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
        
        commission_rate, commission_source = resolve_commission_rate(
            description_category_id=dc_id_int,
            price_rub=_price_rub,
            explicit_commission=explicit_commission,
            extensions_commission_segments=extensions.get("commission_segments"),
            get_category_commission_fn=get_category_commission,
        )
        logger.info(
            f"佣金来源(source)={commission_source}, 类目={dc_id or 'N/A'}, "
            f"档={band}, 佣金={commission_rate*100:.1f}%"
        )
        
        # 除零守卫保留给下方变体定价循环使用（变体 old_price 规则与单 SKU 不同，独立计算）
        commission_divisor: float = (1.0 - commission_rate)
        if commission_divisor <= 0:
            commission_divisor = 0.9  # 防止除零

        _est = compute_price(
            total_cost_cny=total_cost_cny,
            margin_rate=margin_rate,
            commission_rate=commission_rate,
            fx_buffer=fx_buffer,
            currency_code=currency_code,
            exchange_rate=exchange_rate,  # CNY 时 _get_exchange_rate 返回 1.0（CNY 路径不使用）
        )
        price: int = _est["price"]
        old_price: int = _est["old_price"]
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
            "fx_buffer": fx_buffer,
            "currency_code": currency_code,
            "exchange_rate": exchange_rate if currency_code == "RUB" else 1.0,
            "price": price,
            "old_price": old_price,
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
                "profit_formula": "final_price - total_cost",
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
        
        logger.info(f"价格计算成功: price={price} {currency_unit}, old_price={old_price} {currency_unit}, currency_code={currency_code}")
        
        # ✅ 多SKU变体定价：为每个variant计算独立价格
        variants_list: list = draft.get("variants", []) if isinstance(draft, dict) else []
        variant_prices: list = []
        if variants_list and isinstance(variants_list, list) and len(variants_list) > 0:
            logger.info(f"🔄 多SKU变体定价：共{len(variants_list)}个变体")
            for var in variants_list:
                if not isinstance(var, dict):
                    continue
                # 使用variant的price作为采购成本（1688售价即为我们的采购成本）
                var_cost_cny: float = float(var.get("price", 0) or cost_cny)
                var_total_cost: float = var_cost_cny + logistics_cost + packaging_cost
                
                if currency_code == "CNY":
                    var_base_price: float = var_total_cost * (1 + margin_rate) / commission_divisor
                    var_price: int = math.ceil(var_base_price)
                    var_old_price: int = var_price + 3 if var_price <= 25 else math.ceil(var_price * 1.15)
                else:
                    var_base_rub: float = var_total_cost * (1 + margin_rate) * (1 + fx_buffer) / commission_divisor * exchange_rate
                    var_price = math.ceil(var_base_rub)
                    var_old_price = var_price + 3 if var_price <= 25 else math.ceil(var_price * 1.15)
                
                var_sku_id: str = str(var.get("sku_id", ""))
                var_color: str = str(var.get("color", ""))
                variant_prices.append({
                    "sku_id": var_sku_id,
                    "color": var_color,
                    "price": var_price,
                    "old_price": var_old_price,
                    "currency_code": currency_code
                })
                logger.info(f"  变体 {var_sku_id}: color={var_color}, cost={var_cost_cny}CNY → price={var_price} {currency_unit}, old_price={var_old_price}")
            
            pricing_info["variant_prices"] = variant_prices
            logger.info(f"✅ 多SKU变体定价完成：{len(variant_prices)}个变体价格已计算")
        
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
            error_message=f"[PRICING_FAILED] Pricing calculation failed: {str(e)}"
        )



def _get_exchange_rate(supabase_url: str, supabase_key: str, currency_code: str = "RUB") -> float:
    """查询汇率（根据currency_code决定汇率方向，优先 PG 缓存）"""
    if currency_code == "CNY":
        return 1.0
    
    # ✅ 从 PG 缓存查询（替代旧 Supabase REST API）
    from utils.local_db_manager import LocalDBManager
    local_db = LocalDBManager()
    rate = local_db.get_exchange_rate("CNY", "RUB")
    if rate:
        logger.info(f"PG 汇率查询成功: CNY→RUB = {rate}")
        return rate
    
    # 缓存未命中，使用默认值
    logger.warning("PG 汇率缓存未命中，使用默认汇率 12.0")
    return 12.0
