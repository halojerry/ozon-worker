"""跟卖导入节点 — import-by-sku 复制竞品卡片 → AI 生图 → UPDATE 上传

跟卖管线 v5（Ozon 官方规范）:
  ① import-by-sku {sku, name, offer_id, currency_code, price, vat}
     → 复制竞品类目+属性到我们店铺
  ② 失败则 Fallback: 记录 "need_create"，后续 /v3/product/import CREATE
  ③ 成功 → 轮询获取 product_id → 走 AI 生图 + /v3/product/import UPDATE

关键设计:
  - offer_id = ozon_product_id（如 3852000144），保证一竞品一商品
  - import-by-sku 优先，不可复制时自动降级
"""
import logging
import time
from typing import Any

import requests as req

from graphs.state import GlobalState

logger = logging.getLogger(__name__)

BRAND_DICT_ID = 126745801
CHINA_DICT_ID = 90296


def follow_sell_import_node(state: GlobalState) -> GlobalState:
    """跟卖导入节点 v5"""
    draft = state.envelope.get("draft", {}) if state.envelope else {}
    extensions = state.envelope.get("extensions", {}) if state.envelope else {}

    ozon_product_id = str(draft.get("ozon_product_id", ""))
    if not ozon_product_id:
        logger.error("❌ 跟卖: ozon_product_id 为空")
        state.error_message = "跟卖需要竞品 ozon_product_id"
        state.failed_stage = "follow_sell_import"
        return state

    offer_id = ozon_product_id  # = 竞品 product_id，永远唯一
    ozon_title = draft.get("title", "") or draft.get("ozon_title", "") or ""
    ozon_images = draft.get("images", []) or []
    ozon_cat = draft.get("ozon_category", {}) or {}

    # 价格
    purchase_cost = float(draft.get("purchase_cost", 0) or 0)
    margin_rate = float((extensions or {}).get("margin_rate", 0.25))
    commission_rate = float((extensions or {}).get("commission_rate", 0.10))
    if purchase_cost > 0:
        price_val = max(10, int(purchase_cost * (1 + margin_rate) / (1 - commission_rate)))
    else:
        price_val = 500
    old_price_val = price_val + max(3, int(price_val * 0.2))

    # 类目：优先数字 ID，否则 pg_trgm
    dc_raw = str(ozon_cat.get("description_category_id") or "")
    type_raw = str(ozon_cat.get("type_id") or "")
    if dc_raw and not dc_raw.isdigit():
        dc_raw, type_raw = _resolve_category(dc_raw, type_raw or dc_raw)

    client_id = state.ozon_client_id
    api_key = state.ozon_api_key
    headers = {"Client-Id": client_id, "Api-Key": api_key, "Content-Type": "application/json"}

    # ── ① import-by-sku（fire-and-forget，不轮询等待）──
    # Ozon 处理 import-by-sku 需要 10+ 分钟，不适合同步等待
    # 改为提交后立即走 Fallback，两个路径用同一个 offer_id 不会冲突
    try:
        import_body = {
            "items": [{
                "sku": int(ozon_product_id),
                "name": ozon_title or f"Товар {ozon_product_id}",
                "offer_id": offer_id,
                "currency_code": state.currency_code or "CNY",
                "price": str(price_val),
                "old_price": str(old_price_val),
                "vat": "0",
            }]
        }
        logger.info("🔄 import-by-sku (fire-and-forget): sku=%s, offer_id=%s", ozon_product_id, offer_id)
        resp = req.post("https://api-seller.ozon.ru/v1/product/import-by-sku",
                        headers=headers, json=import_body, timeout=30)
        data = resp.json().get("result", {})
        unmatched = data.get("unmatched_sku_list", [])
        ibs_task_id = str(data.get("task_id", ""))
        if resp.status_code == 200 and not unmatched:
            logger.info("✅ import-by-sku 已提交(不等待): task_id=%s", ibs_task_id)
        else:
            logger.info("⚠️ import-by-sku 不可复制(unmatched=%s)，走 Fallback", unmatched)
    except Exception as e:
        logger.info("⚠️ import-by-sku 异常，走 Fallback: %s", e)

    # ── ② 设置状态（走 Fallback 路径：pg_trgm + AI生图 + /v3/product/import）──
    # 类目：pg_trgm 解析俄语类目名 → 数字 ID
    if dc_raw:
        state.description_category_id = dc_raw
        state.type_id = type_raw

    # 标题
    if not ozon_title:
        ozon_title = draft.get("ozon_title", "") or draft.get("title", "") or "Товар"
    state.competitor_name = ozon_title

    # 图片：竞品图作为 AI 生图参考
    state.original_images = ozon_images[:10]

    # 定价信息
    state.pricing_info = {
        "price": str(price_val),
        "old_price": str(old_price_val),
        "currency_code": state.currency_code or "CNY",
        "purchase_cost": purchase_cost,
    }

    # 属性硬化（供 prepare_ozon_upload 使用）
    state.final_attributes = [
        {"id": 85, "values": [{"dictionary_value_id": BRAND_DICT_ID, "value": "Нет бренда"}]},
        {"id": 5076, "values": [{"dictionary_value_id": BRAND_DICT_ID, "value": "Нет бренда"}]},
        {"id": 4389, "values": [{"dictionary_value_id": CHINA_DICT_ID, "value": "Китай"}]},
        {"id": 9048, "values": [{"dictionary_value_id": 0, "value": str(ozon_product_id)}]},
        {"id": 8962, "values": [{"dictionary_value_id": 0, "value": "1"}]},
    ]

    # attributes_schema 空（跟卖不需要 schema 校验）
    state.attributes_schema = []

    # 标记
    state.upload_status = "pending"

    logger.info("✅ 跟卖 v5: product_id=%s, cat=%s/%s, imgs=%d",
                state.product_id, state.description_category_id, state.type_id,
                len(ozon_images))

    return state


def _resolve_category(dc_name: str, type_name: str) -> tuple[str, str]:
    """pg_trgm 俄语类目名 → 数字 ID（复用 search_nodes，含 ILIKE fallback + 缓存同步）"""
    try:
        from utils.ozon_category_query import search_nodes
        search_name = type_name or dc_name
        candidates = search_nodes(search_name, top_k=3, node_type="type", language="RU")
        if candidates:
            best = candidates[0]
            sim = best.get("similarity", 0)
            if sim > 0.3:
                dc_id = str(best.get("description_category_id", ""))
                type_id = str(best.get("type_id", ""))
                logger.info("pg_trgm: '%s' → '%s' (sim=%.3f, dc=%s, type=%s)", 
                           search_name, best.get("node_name", ""), sim, dc_id, type_id)
                return dc_id, type_id
    except Exception as e:
        logger.warning("pg_trgm 异常: %s", e)
    return "", ""
