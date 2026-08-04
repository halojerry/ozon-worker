"""跟卖导入节点 v5 — import-by-sku 复制竞品卡片 → 统一定价 → AI 生图 → UPDATE 上传

跟卖管线 v5（Ozon 官方规范）:
  ① import-by-sku {sku, name, offer_id, currency_code, price, vat}
     → 复制竞品类目+属性到我们店铺
  ② 失败则 Fallback: 记录 "need_create"，后续 /v3/product/import CREATE
  ③ 成功 → 轮询获取 product_id → 统一定价(pricing_node) → AI 生图 → /v3/product/import UPDATE

✅ v4 (B2): 定价统一走 pricing_node, 此节点不再内联定价公式
✅ v4 (B3): 类目三层解析全部失败时传播错误(不静默降级为空类目)

关键设计:
  - offer_id = ozon_product_id（如 3852000144），保证一竞品一商品
  - import-by-sku 优先，不可复制时自动降级
"""
import logging
import time
from typing import Any

import requests as req

from graphs.state import GlobalState, FollowSellImportOutput

logger = logging.getLogger(__name__)

BRAND_DICT_ID = 126745801
CHINA_DICT_ID = 90296


def follow_sell_import_node(state: GlobalState) -> dict[str, Any]:
    """跟卖导入节点 v5 — v4: 返回 TypedDict 替代直接修改 GlobalState"""
    draft = state.envelope.get("draft", {}) if state.envelope else {}
    extensions = state.envelope.get("extensions", {}) if state.envelope else {}

    # ── 局部变量（替代 state.xxx 直接赋值）──
    product_id: str = ""
    comp_price: str = ""
    comp_name: str = ""
    dc_id: str = ""
    tp_id: str = ""
    orig_images: list = []
    variants: list = []
    item_id: str = ""
    final_attrs: list = []
    attrs_schema: list = []
    up_status: str = "pending"
    error_msg: str = ""
    failed_stg: str = ""
    category_missing: bool = False

    ozon_product_id = str(draft.get("ozon_product_id", ""))
    if not ozon_product_id:
        logger.error("❌ 跟卖: ozon_product_id 为空")
        return {"error_message": "跟卖需要竞品 ozon_product_id", "failed_stage": "follow_sell_import"}

    offer_id = ozon_product_id
    ozon_title = draft.get("title", "") or draft.get("ozon_title", "") or ""
    ozon_images = draft.get("images", []) or []
    ozon_cat = draft.get("ozon_category", {}) or {}

    # 占位价（import-by-sku 需要，实际售价由 pricing_node 计算）
    purchase_cost = float(draft.get("purchase_cost", 0) or 0)
    placeholder_price = max(10, int(purchase_cost * 2.0)) if purchase_cost > 0 else 100
    price_val = placeholder_price
    old_price_val = int(placeholder_price * 1.3)

    # 类目解析
    dc_raw = str(ozon_cat.get("description_category_id") or "")
    type_raw = str(ozon_cat.get("type_id") or "")
    dc_fallback, type_fallback = dc_raw, type_raw
    language = ozon_cat.get("language", "")
    if dc_raw and dc_raw.isdigit():
        category_hint = ozon_cat.get("category_path", "") or ozon_cat.get("category", "")
        last_segment = category_hint.split(" > ")[-1].strip() if category_hint else ""
        resolved_dc, resolved_type = _resolve_category_by_id(int(dc_raw), type_name_hint=last_segment, token=state.token)
        if resolved_dc and resolved_type:
            dc_raw, type_raw = resolved_dc, resolved_type
        else:
            logger.warning("数字 ID 直查+pg_trgm 均失败: dc=%s type=%s", dc_fallback, type_fallback)
            # ✅ v0.20 A: 解析失败绝不保留原始值（品牌页 ID 会被当有效类目上传 → Ozon 拒）
            dc_raw, type_raw = "", ""
    elif dc_raw:
        if not language:
            language = _detect_language(dc_raw)
        resolved_dc, resolved_type = _resolve_category(dc_raw, type_raw or dc_raw, language=language)
        if resolved_dc and resolved_type:
            dc_raw, type_raw = resolved_dc, resolved_type
        else:
            logger.warning("pg_trgm 类目搜索失败: dc=%s type=%s", dc_fallback, type_fallback)
            # ✅ v0.20 A: 同上，不保留无效原始类目
            dc_raw, type_raw = "", ""

    # 拉取属性 schema
    client_id = state.ozon_client_id
    api_key = state.ozon_api_key
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
                attrs_schema = _resp.json().get("result", [])
                logger.info("✅ 跟卖 schema 已拉取: %d 个属性", len(attrs_schema))
        except Exception as e:
            logger.warning("⚠️ 跟卖 schema 拉取失败（降级继续）: %s", e)

    headers = {"Client-Id": client_id, "Api-Key": api_key, "Content-Type": "application/json"}

    # ── ① import-by-sku ──
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
        logger.info("🔄 import-by-sku: sku=%s, offer_id=%s", ozon_product_id, offer_id)
        resp = req.post("https://api-seller.ozon.ru/v1/product/import-by-sku",
                        headers=headers, json=import_body, timeout=30)
        data = resp.json().get("result", {})
        unmatched = data.get("unmatched_sku_list", [])
        ibs_task_id = str(data.get("task_id", ""))
        if resp.status_code == 200 and not unmatched:
            logger.info("✅ import-by-sku 已提交: task_id=%s", ibs_task_id)
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
                                product_id = str(_pid)
                                logger.info("✅ import-by-sku 完成: product_id=%s，后续走 UPDATE", _pid)
                                break
                    if product_id:
                        break
                except Exception:
                    pass
            if not product_id:
                logger.info("⏳ import-by-sku 未在30s内完成，走 Fallback CREATE")
        else:
            logger.info("⚠️ import-by-sku 不可复制(unmatched=%s)，走 Fallback", unmatched)
    except Exception as e:
        logger.info("⚠️ import-by-sku 异常，走 Fallback: %s", e)

    # ── ② 组装返回值 ──
    # ⚠️ v0.14 P0-6: 优先读 draft.competitor_price（Skill 从 Ozon 页面提取的真实竞品售价）
    # 旧逻辑读 draft.get("price")/draft.get("ozon_price") —— draft.price 实为 1688 采购价(CNY)，
    # 被误当竞品价 → 竞品价保护分支(≥成本×1.3 保持竞品价)永不生效
    ozon_price = draft.get("competitor_price", "") or draft.get("ozon_price", "") or ""
    if ozon_price:
        comp_price = str(ozon_price).strip()
        logger.info(f"💰 竞品 Ozon 价格: {comp_price}")

    if dc_raw and dc_raw.isdigit() and int(dc_raw) > 0:
        dc_id = dc_raw
    if type_raw and type_raw.isdigit() and int(type_raw) > 0:
        tp_id = type_raw

    # ✅ v0.19.1 P0: 类目缺失不再一刀切失败
    # 1) import-by-sku 成功（product_id 已返回）→ Ozon 官方复制已带出类目/属性，
    #    不再强制要求 dc/tp（此前官方通道被前置校验掐死——南辕北辙）
    # 2) Fallback CREATE 才需要类目：缺失时用 1688 来源类目/标题 pg_trgm 兜底
    #    （复用 direct 管线现成引擎）
    import_by_sku_ok = bool(product_id)
    if not dc_id or not tp_id:
        if not import_by_sku_ok:
            src_path = ""
            _src = (state.envelope or {}).get("source", {}) if state.envelope else {}
            if isinstance(_src, dict):
                src_path = _src.get("source_category_path", "") or ""
            search_text = src_path.split(" > ")[-1].strip() if src_path else ""
            if not search_text:
                search_text = draft.get("source_category", "") or draft.get("title", "") or ""
            if search_text:
                try:
                    _r_dc, _r_tp = _resolve_category(search_text, search_text, language="ZH_HANS")
                    if _r_dc and _r_tp:
                        dc_id, tp_id = _r_dc, _r_tp
                        logger.info("✅ 1688 来源类目兜底成功: '%s' → %s/%s", search_text, dc_id, tp_id)
                except Exception as _e:
                    logger.warning("1688 类目兜底异常: %s", _e)
        if not dc_id or not tp_id:
            cat_path = ozon_cat.get("category_path", "") or ozon_cat.get("category", "")
            if import_by_sku_ok:
                logger.warning("⚠️ 跟卖无类目但 import-by-sku 已成功（%s），"
                               "继续走 UPDATE（类目由官方复制带出）", product_id)
                category_missing = True
            else:
                logger.error("❌ 跟卖类目解析全部失败（Fallback CREATE 需要类目）: "
                             "Widget ID=%s, breadcrumb=%s", dc_fallback, cat_path or "(empty)")
                return {
                    "error_message": f"类目解析失败: Widget ID={dc_fallback}, "
                                     f"breadcrumb={cat_path or '(empty)'}, 1688 兜底也无结果",
                    "failed_stage": "follow_sell_import",
                }

    if not ozon_title:
        ozon_title = draft.get("ozon_title", "") or draft.get("title", "") or "Товар"
    comp_name = ozon_title
    orig_images = ozon_images[:10]
    variants = []
    item_id = draft.get("item_id", ozon_product_id)
    final_attrs = [
        {"id": 85, "values": [{"dictionary_value_id": BRAND_DICT_ID, "value": "Нет бренда"}]},
        {"id": 5076, "values": [{"dictionary_value_id": BRAND_DICT_ID, "value": "Нет бренда"}]},
        {"id": 4389, "values": [{"dictionary_value_id": CHINA_DICT_ID, "value": "Китай"}]},
        {"id": 9048, "values": [{"dictionary_value_id": 0, "value": str(ozon_product_id)}]},
        {"id": 8962, "values": [{"dictionary_value_id": 0, "value": "1"}]},
    ]
    up_status = "pending"

    logger.info("✅ 跟卖 v5: product_id=%s, cat=%s/%s, imgs=%d",
                product_id, dc_id, tp_id, len(ozon_images))

    return {
        "progress_counter": 3,
        "product_id": product_id or None,
        "competitor_price": comp_price,
        "competitor_name": comp_name,
        "description_category_id": dc_id,
        "type_id": tp_id,
        "original_images": orig_images,
        "variants": variants,
        "item_id": item_id,
        "final_attributes": final_attrs,
        "attributes_schema": attrs_schema,
        "upload_status": up_status,
        "category_missing": category_missing,
        "error_message": error_msg,
        "failed_stage": failed_stg,
    }


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
