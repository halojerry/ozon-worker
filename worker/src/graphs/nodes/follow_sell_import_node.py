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

    # ✅ v0.11: 完整定价 — 加物流+包装+汇率（之前漏算导致系统性地低价 $5-15）
    purchase_cost = float(draft.get("purchase_cost", 0) or 0)
    margin_rate = float((extensions or {}).get("margin_rate", 0.25))
    commission_rate = float((extensions or {}).get("commission_rate", 0.10))
    fx_buffer = float((extensions or {}).get("fx_buffer", 0.05))

    # 物流成本估算（简化版 — 完整版在 pricing_node 中）
    weight_g = int(draft.get("weight", 0) or 0)
    dims = draft.get("dimensions", {}) or {}
    weight_kg = max(weight_g / 1000.0, 0.05)
    # 兜底费率 ~0.05 CNY/g（与 pricing_node 一致）
    logistics_cost = max(weight_g * 0.05, 3.0)
    packaging_cost = 2.0

    # 汇率（CNY 店铺不加 fx_buffer）
    currency = (draft.get("currency") or "").upper()
    exchange_rate = 1.0
    if currency == "RUB":
        exchange_rate = 11.0  # 兜底汇率
        total_cost = purchase_cost + logistics_cost + packaging_cost
        base_price = total_cost * (1 + margin_rate) * (1 + fx_buffer) / max(1 - commission_rate, 0.9) * exchange_rate
    else:
        total_cost = purchase_cost + logistics_cost + packaging_cost
        base_price = total_cost * (1 + margin_rate) / max(1 - commission_rate, 0.9)

    price_val = max(10, int(base_price))
    old_price_val = price_val + max(3, int(price_val * 0.2))

    if purchase_cost > 0:
        logger.info("💰 跟卖定价: 成本=%.1f + 物流=%.1f + 包装=%.1f = 总成本=%.1f → 售价=%d (%.0f%%)",
                   purchase_cost, logistics_cost, packaging_cost, total_cost, price_val, margin_rate * 100)

    # 类目：优先数字 ID 直查 → 文本 pg_trgm（语言感知）
    # ✅ v0.10: 保存原始值作 fallback，resolution 失败时保留原值
    dc_raw = str(ozon_cat.get("description_category_id") or "")
    type_raw = str(ozon_cat.get("type_id") or "")
    dc_fallback, type_fallback = dc_raw, type_raw
    language = ozon_cat.get("language", "")
    if dc_raw and dc_raw.isdigit():
        # ✅ v0.9: 数字 ID → 直接查 category_tree_nodes
        # ✅ v0.11: 传入面包屑文本作 pg_trgm 兜底（Widget ID ≠ Seller ID）
        category_hint = ozon_cat.get("category_path", "") or ozon_cat.get("category", "")
        # 取面包屑路径末级作为 type 名匹配 hint
        last_segment = category_hint.split(" > ")[-1].strip() if category_hint else ""
        resolved_dc, resolved_type = _resolve_category_by_id(int(dc_raw), type_name_hint=last_segment, token=state.token)
        if resolved_dc and resolved_type:
            dc_raw, type_raw = resolved_dc, resolved_type
        else:
            logger.warning("数字 ID 直查+pg_trgm 均失败，使用原始 envelope 类目: dc=%s type=%s", dc_fallback, type_fallback)
            dc_raw, type_raw = dc_fallback, type_fallback
    elif dc_raw:
        # 文本 → pg_trgm（语言检测：RU/ZH_HANS）
        if not language:
            language = _detect_language(dc_raw)
        resolved_dc, resolved_type = _resolve_category(dc_raw, type_raw or dc_raw, language=language)
        if resolved_dc and resolved_type:
            dc_raw, type_raw = resolved_dc, resolved_type
        else:
            logger.warning("pg_trgm 类目搜索失败，使用原始 envelope 文本: dc=%s type=%s", dc_fallback, type_fallback)
            dc_raw, type_raw = dc_fallback, type_fallback

    # ✅ P3 修复：跟卖也拉取属性 schema（供 validate + retry 使用）
    client_id = state.ozon_client_id
    api_key = state.ozon_api_key
    attributes_schema: list = []
    if dc_raw and type_raw:
        try:
            import requests as _req
            _resp = _req.post(
                "https://api-seller.ozon.ru/v1/description-category/attribute",
                headers={"Client-Id": client_id, "Api-Key": api_key, "Content-Type": "application/json"},
                json={"description_category_id": int(dc_raw), "type_id": int(type_raw), "language": "ZH_HANS"},
                timeout=15,
            )
            if _resp.status_code == 200:
                attributes_schema = _resp.json().get("result", [])
                logger.info("✅ 跟卖 schema 已拉取: %d 个属性", len(attributes_schema))
        except Exception as e:
            logger.warning("⚠️ 跟卖 schema 拉取失败（降级继续）: %s", e)

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
            logger.info("✅ import-by-sku 已提交: task_id=%s", ibs_task_id)
            # ✅ v0.11: 短暂轮询（10×3s=30s），拿到 product_id 后走 UPDATE，避免重复产品卡
            for _ibs_attempt in range(10):
                time.sleep(3)
                try:
                    info_resp = req.post(
                        "https://api-seller.ozon.ru/v1/product/import/info",
                        headers=headers, json={"task_id": int(ibs_task_id)}, timeout=15,
                    )
                    if info_resp.status_code == 200:
                        info_items = info_resp.json().get("result", {}).get("items", [])
                        for _it in info_items:
                            _pid = _it.get("product_id")
                            _status = _it.get("status", "")
                            if _pid and _status == "imported":
                                state.product_id = str(_pid)
                                logger.info("✅ import-by-sku 完成: product_id=%s，后续走 UPDATE", _pid)
                                break
                    if state.product_id:
                        break
                except Exception:
                    pass
            if not state.product_id:
                logger.info("⏳ import-by-sku 未在30s内完成，走 Fallback CREATE")
        else:
            logger.info("⚠️ import-by-sku 不可复制(unmatched=%s)，走 Fallback", unmatched)
    except Exception as e:
        logger.info("⚠️ import-by-sku 异常，走 Fallback: %s", e)

    # ── ② 设置状态（走 Fallback 路径：pg_trgm + AI生图 + /v3/product/import）──
    # ✅ v0.11: 记录竞品 Ozon 价格（供 pricing_node 竞品价格覆盖逻辑使用）
    ozon_price = draft.get("price", "") or draft.get("ozon_price", "")
    if ozon_price:
        state.competitor_price = str(ozon_price).strip()
        logger.info(f"💰 竞品 Ozon 价格: {state.competitor_price}")

    # 类目：pg_trgm 解析俄语类目名 → 数字 ID
    if dc_raw:
        state.description_category_id = dc_raw
    if type_raw:
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

    # attributes_schema（跟卖现在拉取真实 schema）
    state.attributes_schema = attributes_schema

    # 标记
    state.upload_status = "pending"

    logger.info("✅ 跟卖 v5: product_id=%s, cat=%s/%s, imgs=%d",
                state.product_id, state.description_category_id, state.type_id,
                len(ozon_images))

    return state


def _detect_language(text: str) -> str:
    """检测文本语言 → pg_trgm 搜索语言"""
    if any('\u4e00' <= c <= '\u9fff' for c in text):
        return "ZH_HANS"
    return "RU"  # 默认俄语（Cyrillic）


def _translate_to_russian(text: str, token: str = "") -> str:
    """Translate Chinese category name to Russian via LLM (mxou)."""
    if not text or not token:
        return ""
    try:
        from utils.mxou_api import call_mxou_chat_api
        prompt = f"Переведи название категории товара на русский язык. Верни ТОЛЬКО перевод, без пояснений: {text}"
        result = call_mxou_chat_api(
            token=token,
            system_prompt="Ты переводчик. Переводи точно, без лишних слов.",
            user_prompt=prompt,
            model="deepseek-v4-flash",
            max_tokens=80,
        )
        if result and len(result.strip()) > 2:
            return result.strip()
    except Exception as e:
        logger.warning("LLM 翻译类目失败: %s", e)
    return ""


def _resolve_category_by_id(dc_id: int, type_name_hint: str = "", token: str = "") -> tuple[str, str]:
    """数字 description_category_id → 查 category_tree_nodes 获取 type_id
    
    Widget API 和 Seller API 使用不同的 ID 空间，数字直查经常失败。
    失败时用面包屑文本做 pg_trgm 搜索 Seller 类目树作为降级。
    """
    try:
        from utils.ozon_category_query import get_category_query
        query = get_category_query()
        node = query.get_node_by_description_category_id(dc_id)
        if node:
            dc_id_str = str(node["description_category_id"])
            type_id_str = str(node["type_id"])
            logger.info("✅ 数字 ID 直查: %d → dc=%s type=%s name=%s",
                       dc_id, dc_id_str, type_id_str, node.get("node_name", ""))
            return dc_id_str, type_id_str
    except Exception as e:
        logger.warning("数字 ID 直查失败: %s", e)
    
    # ✅ v0.11: Widget ID 不在 Seller 树中 → 用面包屑文本 pg_trgm 搜索
    if type_name_hint:
        # 检测面包屑文本语言，优先用对应语言搜索
        lang = _detect_language(type_name_hint)
        logger.info("🔍 数字 ID %d 直查失败，尝试 pg_trgm 文本搜索(lang=%s): '%s'", dc_id, lang, type_name_hint)
        dc_text, type_text = _resolve_category(type_name_hint, type_name_hint, language=lang)
        if dc_text and type_text:
            logger.info("✅ pg_trgm 兜底成功: '%s' → dc=%s type=%s", type_name_hint, dc_text, type_text)
            return dc_text, type_text
        # 尝试 RU 作为第二语言备选
        if lang != "RU":
            dc_text, type_text = _resolve_category(type_name_hint, type_name_hint, language="RU")
            if dc_text and type_text:
                logger.info("✅ pg_trgm 兜底(RU): '%s' → dc=%s type=%s", type_name_hint, dc_text, type_text)
                return dc_text, type_text
        
        # ✅ v0.11: 中文面包屑 → LLM 翻译俄语 → pg_trgm RU 搜索
        if lang != "RU":
            ru_hint = _translate_to_russian(type_name_hint, token)
            if ru_hint and ru_hint != type_name_hint:
                logger.info("🔍 LLM 翻译: '%s' → '%s', 尝试 pg_trgm RU 搜索", type_name_hint, ru_hint)
                dc_text, type_text = _resolve_category(ru_hint, ru_hint, language="RU")
                if dc_text and type_text:
                    logger.info("✅ LLM翻译+pg_trgm 成功: '%s' → dc=%s type=%s", ru_hint, dc_text, type_text)
                    return dc_text, type_text
    
    return "", ""


def _resolve_category(dc_name: str, type_name: str, language: str = "RU") -> tuple[str, str]:
    """pg_trgm 类目名 → 数字 ID（语言感知：RU/ZH_HANS）"""
    try:
        from utils.ozon_category_query import get_category_query
        query = get_category_query()
        search_name = type_name or dc_name
        language = language or _detect_language(search_name)
        candidates = query.search_nodes(search_name, top_k=3, node_type="type", language=language)
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
