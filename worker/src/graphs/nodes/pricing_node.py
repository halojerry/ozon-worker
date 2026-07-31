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
from utils.ozon_client import ozon_post
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
        weight_raw = draft.get("weight", 0)
        try:
            weight: float = float(weight_raw) if weight_raw else 0.0
        except (ValueError, TypeError):
            weight = 0.0
            logger.warning(f"定价节点：weight 无法解析为数字（{weight_raw}），使用默认 0")
        # ✅ 单位转换：kg → g 判定
        if isinstance(weight_raw, str) and '.' in str(weight_raw) and 0 < weight < 10000:
            weight = weight * 1000  # kg → g
            logger.info(f"定价节点重量转换：{weight_raw}kg → {weight}g")
        
        # 提前提取尺寸对象（用于小重量检测和后续定价计算）
        dims_obj = draft.get("dimensions", {})
        def _safe_float(val) -> float:
            try:
                return float(val) if val else 0.0
            except (ValueError, TypeError):
                return 0.0
        
        # ✅ v0.11: 小重量+大尺寸 → 疑似 kg 当 g 传（与 prepare_ozon_upload 一致）
        if 0 < weight < 10:
            l = _safe_float(dims_obj.get("length", 0))
            w = _safe_float(dims_obj.get("width", 0))
            h = _safe_float(dims_obj.get("height", 0))
            if max(l, w, h) > 50:
                weight = weight * 1000
                logger.warning(f"定价节点：weight={weight_raw}g 但 max_dim={max(l,w,h)}mm，疑似 kg→g 修正为 {weight}g")
        
        # ✅ 关键修复：从嵌套的 dimensions 对象中提取尺寸（mm→cm）
        if isinstance(dims_obj, dict):
            depth_mm = _safe_float(dims_obj.get("length") or dims_obj.get("depth"))
            width_mm = _safe_float(dims_obj.get("width"))
            height_mm = _safe_float(dims_obj.get("height"))
        else:
            depth_mm = _safe_float(draft.get("depth"))
            width_mm = _safe_float(draft.get("width"))
            height_mm = _safe_float(draft.get("height"))
        # mm → cm
        depth: float = depth_mm / 10.0
        width: float = width_mm / 10.0
        height: float = height_mm / 10.0
        # 逐维度补默认值（与 prepare_ozon_upload_node 一致：depth→3cm, width→2cm, height→0.5cm）
        if depth <= 0:
            depth = 3.0
        if width <= 0:
            width = 2.0
        if height <= 0:
            height = 0.5
        
        # ✅ 重量为0时使用默认值（与prepare_ozon_upload_node一致）
        if weight <= 0:
            weight = 300.0  # 默认300克
            logger.warning("⚠️ weight为0或空，使用默认值: 300克")
        
        # cost_cny为0时使用默认值
        if cost_cny <= 0:
            cost_cny = 10.0
            logger.warning("⚠️ cost_cny为0或空，使用默认值: 10 CNY")
        
        # 获取扩展配置
        margin_rate: float = float(extensions.get("margin_rate", 0.25))  # 利润率 25%
        
        # ✅ 尝试从 Ozon API 获取真实佣金率
        commission_rate: float = float(extensions.get("commission_rate", 0.0))
        if commission_rate <= 0:
            try:
                # ✅ v0.11: 用 description_category_id 查佣金（offer_id 不存在）
                # 查询 /v4/product/info/limit 获取类目级别的佣金信息
                dc_id = getattr(state, 'description_category_id', '') or ''
                if dc_id:
                    price_resp = ozon_post(ozon_client_id, ozon_api_key,
                        "/v5/product/info/prices",
                        {"filter": {"offer_id": []}, "limit": 1},
                        timeout=10)
                    # 尝试从 store-level commission 获取
                    comms = price_resp.get("result", {}).get("commissions", {})
                    commission_rate = comms.get("sales_percent_rfbs", 0) / 100.0
                if commission_rate > 0:
                    logger.info(f"✅ 店铺佣金率 rFBS={commission_rate*100:.1f}%")
            except Exception:
                pass
        if commission_rate <= 0:
            commission_rate = 0.10  # fallback
            
        fx_buffer: float = float(extensions.get("fx_buffer", 0.05))  # 汇率缓冲 5%
        
        # Step 2: 查询物流费率（SQLite logistics_rates + Ozon API获取3PL/服务等级）
        tpl_provider, service_level = _get_store_logistics_config(ozon_client_id, ozon_api_key)
        logger.info(f"🔍 DEBUG 物流查询参数: weight={weight}g, dims={depth}x{width}x{height}cm, tpl={tpl_provider}, svc={service_level}, cost_cny={cost_cny}")
        logistics_cost, logistics_channel = _query_logistics_from_sqlite(
            weight, depth, width, height, tpl_provider, service_level
        )
        
        # Step 3: 查询包装成本（固定值）
        packaging_cost: float = 2.0  # CNY
        
        # Step 4: 查询汇率（根据currency_code决定汇率方向）
        exchange_rate: float = _get_exchange_rate(supabase_url, supabase_key, currency_code)
        
        # Step 5: 价格计算公式
        # 总成本 = 产品成本 + 物流成本 + 包装成本
        total_cost_cny: float = cost_cny + logistics_cost + packaging_cost
        
        # Ozon佣金是售价的百分比，所以正确公式：售价 = 总成本 / (1 - 佣金率 - 利润率)
        # 简化：售价 = 总成本 * (1 + margin_rate) / (1 - commission_rate)
        commission_divisor: float = (1.0 - commission_rate)
        if commission_divisor <= 0:
            commission_divisor = 0.9  # 防止除零
        
        # 根据currency_code决定价格计算方式
        if currency_code == "CNY":
            # 店铺是CNY，直接计算人民币价格（CNY店铺无汇率风险，不使用fx_buffer）
            base_price_cny: float = total_cost_cny * (1 + margin_rate) / commission_divisor
            price: int = math.ceil(base_price_cny)
            # Ozon规则：折扣至少 20%（price≤25 时 old_price-price≥5；否则 20% 加价）
            if price <= 25:
                old_price: int = max(price + 5, math.ceil(price * 1.2))
            else:
                old_price = math.ceil(price * 1.2)
            currency_unit = "CNY"
        else:
            # 店铺是RUB，计算俄罗斯卢布价格
            base_price_rub: float = total_cost_cny * (1 + margin_rate) * (1 + fx_buffer) / commission_divisor * exchange_rate
            price: int = math.ceil(base_price_rub)
            # Ozon规则：折扣至少 20%
            if price <= 25:
                old_price = max(price + 5, math.ceil(price * 1.2))
            else:
                old_price = math.ceil(price * 1.2)
            currency_unit = "RUB"
        
        # ✅ 跟卖模式：如果竞品价格已有利润（≥ 成本*1.3），保持竞品价格以增加竞争力
        competitor_price_str = getattr(state, 'competitor_price', '') or ''
        # ✅ P2 修复：用 extensions.follow_sell 判断，而非 product_id（1688 管线也会设置）
        extensions = getattr(state, 'extensions', {}) or {}
        is_follow_sell = bool(extensions.get('follow_sell', False))
        if is_follow_sell and competitor_price_str:
            try:
                comp_price = float(competitor_price_str)
                min_viable = total_cost_cny * 1.3  # 最低可接受售价（30% margin）
                if comp_price >= min_viable:
                    logger.info(f"💰 跟卖定价: 竞品价 {comp_price} ≥ 最低 {min_viable:.0f}，保持竞品价格")
                    price = int(math.ceil(comp_price))
                    old_price = max(price + 5, math.ceil(price * 1.2)) if price <= 25 else math.ceil(price * 1.2)
                else:
                    logger.info(f"💰 跟卖定价: 竞品价 {comp_price} < 最低 {min_viable:.0f}，使用公式重算 {price}")
            except (ValueError, TypeError):
                pass
        
        # Step 6: 计算利润预估
        # 利润 = 最终价格 - 总成本
        if currency_code == "CNY":
            profit_cny: float = price - total_cost_cny
            base_price_for_profit: float = base_price_cny
        else:
            # RUB店铺：利润需要转换回CNY计算
            profit_cny: float = (price / exchange_rate) - total_cost_cny
            base_price_for_profit: float = base_price_rub / exchange_rate
        
        profit_rate_actual: float = profit_cny / total_cost_cny if total_cost_cny > 0 else 0.0
        
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
            error_message=f"Pricing calculation failed: {str(e)}"
        )


def _get_store_logistics_config(ozon_client_id: str, ozon_api_key: str) -> tuple:
    """查询Ozon API获取店铺第三方物流(3PL)和服务等级"""
    try:
        headers = {
            "Client-Id": ozon_client_id,
            "Api-Key": ozon_api_key,
            "Content-Type": "application/json"
        }
        resp = session.post(
            "https://api-seller.ozon.ru/v2/delivery-method/list",
            headers=headers, json={"limit": 100}, timeout=30
        )
        if resp.status_code != 200:
            logger.warning(f"Ozon配送方式查询失败: {resp.status_code}")
            return ("RETS", "Standard")

        data: Any = resp.json()
        methods = data.get("delivery_methods", [])
        if not methods:
            return ("RETS", "Standard")

        # 从配送方式名称中提取3PL和服务等级
        # 名称格式如: "RETS Standard Longyan rFBS Courier" 或 "RETS Economy..."
        first_method_name = methods[0].get("name", "")
        tpl_provider = "RETS"
        service_level = "Standard"

        # 已知的3PL列表
        known_tpls = ["RETS", "ATC", "ZTO", "Ural", "GUOO", "CEL", "GBS", "OYX", "ABT", "Xingyuan", "Tanais"]
        for tpl in known_tpls:
            if tpl.lower() in first_method_name.lower():
                tpl_provider = tpl
                break

        # 服务等级匹配
        name_upper = first_method_name.upper()
        if "ECONOMY" in name_upper:
            service_level = "Economy"
        elif "EXPRESS" in name_upper:
            service_level = "Express"
        elif "STANDARD" in name_upper:
            service_level = "Standard"

        logger.info(f"店铺物流配置: 3PL={tpl_provider}, 服务等级={service_level}, 配送方式名={first_method_name}")
        return (tpl_provider, service_level)

    except Exception as e:
        logger.error(f"查询店铺物流配置失败: {str(e)}")
        return ("RETS", "Standard")


def _query_logistics_from_sqlite(weight: float, depth_cm: float, width_cm: float, height_cm: float, tpl_provider: str, service_level: str) -> tuple:
    """从 PG 物流费率表查询，按 3PL+服务等级+评分组精确匹配"""
    from storage.database.db import get_session
    from storage.database.shared.model import LogisticsRate
    from sqlalchemy import and_, select

    session = get_session()
    try:
        dims = sorted([depth_cm, width_cm, height_cm], reverse=True)
        longest_cm = dims[0] if dims else 0
        sum_cm = sum(dims)

        # 查询1: 3PL + 服务等级 + 重量 + 尺寸全匹配
        logger.info(f"🔍 DEBUG Q1: tpl={tpl_provider}, svc={service_level}, w={int(weight)}, sum={int(sum_cm)}, longest={int(longest_cm)}")
        rows = session.execute(
            select(LogisticsRate).where(
                and_(
                    LogisticsRate.tpl_provider == tpl_provider,
                    LogisticsRate.service_level == service_level,
                    LogisticsRate.weight_min <= int(weight),
                    LogisticsRate.weight_max >= int(weight),
                    LogisticsRate.sum_limit_cm >= int(sum_cm),
                    LogisticsRate.longest_limit_cm >= int(longest_cm),
                )
            ).order_by(LogisticsRate.base_cost.asc()).limit(1)
        ).scalars().all()
        logger.info(f"🔍 DEBUG Q1 result: {len(rows)} rows")

        # 查询2: 尺寸不满足，仅按重量 + 3PL匹配
        if not rows:
            logger.info(f"🔍 DEBUG Q2 fallback: weight-only filter")
            rows = session.execute(
                select(LogisticsRate).where(
                    and_(
                        LogisticsRate.tpl_provider == tpl_provider,
                        LogisticsRate.service_level == service_level,
                        LogisticsRate.weight_min <= int(weight),
                        LogisticsRate.weight_max >= int(weight),
                    )
                ).order_by(LogisticsRate.base_cost.asc()).limit(1)
                ).scalars().all()
            logger.info(f"🔍 DEBUG Q2 result: {len(rows)} rows")

        # 查询3: 该3PL无匹配，同服务等级其他3PL
        if not rows:
            logger.info(f"🔍 DEBUG Q3 fallback: cross-3PL, same service_level")
            rows = session.execute(
                select(LogisticsRate).where(
                    and_(
                        LogisticsRate.service_level == service_level,
                        LogisticsRate.weight_min <= int(weight),
                        LogisticsRate.weight_max >= int(weight),
                        LogisticsRate.sum_limit_cm >= int(sum_cm),
                        LogisticsRate.longest_limit_cm >= int(longest_cm),
                    )
                ).order_by(LogisticsRate.base_cost.asc()).limit(1)
            ).scalars().all()
            logger.info(f"🔍 DEBUG Q3 result: {len(rows)} rows")

        if rows:
            row = rows[0]
            base_cost = float(row.base_cost)
            per_gram_rate = float(row.per_gram_rate)
            vol_divisor = int(row.vol_weight_divisor)

            billable_weight = weight
            if vol_divisor > 1:
                vol_weight = (depth_cm * width_cm * height_cm) / vol_divisor
                billable_weight = max(weight, vol_weight)

            logistics_cost = base_cost + per_gram_rate * billable_weight
            channel_name = f"{row.tpl_provider}_{row.service_level}_{row.scoring_group}"

            logger.info(
                f"PG 物流费率匹配: 3PL={row.tpl_provider}, 等级={row.service_level}, 评分组={row.scoring_group}, "
                f"weight={weight}g, billable={billable_weight:.1f}g, "
                f"base={base_cost}, rate={per_gram_rate}/g, cost={logistics_cost:.2f} CNY"
            )
            return (logistics_cost, channel_name)

        # 最终fallback：使用RETS Standard
        fb_row = session.execute(
            select(LogisticsRate).where(
                and_(
                    LogisticsRate.tpl_provider == "RETS",
                    LogisticsRate.service_level == "Standard",
                    LogisticsRate.weight_min <= int(weight),
                    LogisticsRate.weight_max >= int(weight),
                )
            ).order_by(LogisticsRate.weight_min.asc()).limit(1)
        ).scalar_one_or_none()

        if fb_row:
            base_cost = float(fb_row.base_cost)
            per_gram_rate = float(fb_row.per_gram_rate)
            logistics_cost = base_cost + per_gram_rate * weight
            logger.warning(f"物流费率最终fallback到RETS Standard: cost={logistics_cost:.2f}")
            return (logistics_cost, "RETS_Standard_fallback")

        # 绝对最后fallback
        logger.warning(f"PG 物流费率表无数据，使用默认费率")
        return (max(5.0, weight * 0.05), "default_fallback")
    
    except Exception as e:
        logger.error(f"PG 物流费率查询失败: {str(e)}")
        return (max(5.0, weight * 0.05), "error_fallback")
    finally:
        session.close()


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