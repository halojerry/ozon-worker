"""跟卖导入节点 — 直接构建 Ozon 商品并上传（跳过 import-by-sku）

跟卖管线（简化版）：
  1. 从 envelope.draft 读取 Skill CDP 抓取的竞品数据
  2. 构建 Ozon payload（竞品类目 + 竞品图片 + 属性硬化）
  3. POST /v3/product/import 创建商品
  4. 路由到 ozon_status → END

不再使用 import-by-sku（其 sku 字段需要竞品真实 SKU，非 product_id）。
不再走完整管线（图片生成/定价/类目匹配均跳过）。
"""
import logging
import time
from typing import Any

import requests as req

from graphs.state import GlobalState

logger = logging.getLogger(__name__)

# Ozon 属性 ID 常量
BRAND_ATTR_IDS = [85, 5076]         # Бренд (品牌)
COUNTRY_ATTR_ID = 4389              # Страна-производитель (生产国)
NO_BRAND_DICT_ID = 126745801        # Нет бренда (无品牌)
CHINA_DICT_ID = 90296               # Китай (中国)


def follow_sell_import_node(state: GlobalState) -> GlobalState:
    """
    跟卖导入节点（v2 — 直接构建上传，不用 import-by-sku）:
      1. 从 draft.ozon_category 获取竞品类目 ID
      2. 从 draft.images 获取竞品图片（ir.ozone.ru，Ozon 审核通过）
      3. 从 draft 获取标题（优先俄语标题 keyword_ru）
      4. 构建 Ozon /v3/product/import payload
      5. 直接上传 → 轮询状态
      6. 属性硬化：品牌→Нет бренда, 国家→Китай
    """
    draft = state.envelope.get("draft", {}) if state.envelope else {}
    extensions = state.envelope.get("extensions", {}) if state.envelope else {}

    # ── Step 1: 从预抓取数据中提取关键字段 ──
    ozon_cat = draft.get("ozon_category", {}) or {}
    description_category_id = str(ozon_cat.get("description_category_id") or "")
    type_id = str(ozon_cat.get("type_id") or "")

    # 处理 None 和空值
    if description_category_id in ("", "None", "null"):
        description_category_id = ""
    if type_id in ("", "None", "null"):
        type_id = ""

    # ✅ 类目名称 → 数字 ID 转换（CDP 抓取到的是文本名称，不是 API 需要的数字 ID）
    if description_category_id and not description_category_id.isdigit():
        logger.info(f"🔍 类目名称 '{description_category_id}' 不是数字，pg_trgm 搜索...")
        resolved_dc, resolved_type = _resolve_category_ids(description_category_id, type_id)
        if resolved_dc:
            description_category_id = resolved_dc
            type_id = resolved_type or resolved_dc
            logger.info(f"✅ 类目解析: dc={description_category_id}, type={type_id}")
        else:
            logger.error(f"❌ 无法解析类目名称 '{description_category_id}' 到数字 ID")
            state.error_message = f"跟卖: 类目名称 '{description_category_id}' 无法解析为数字 ID"
            state.failed_stage = "follow_sell_import"
            return state

    # 图片：竞品 Ozon 原图（ir.ozone.ru），优先用从 Skill 传来的
    images = draft.get("images", []) or []
    if not images:
        # 兜底：从 state.original_images 获取
        images = getattr(state, "original_images", []) or []

    # 标题：优先用 keyword_ru（1688 AK 搜索结果中的俄语关键词）
    title = draft.get("keyword_ru", "") or draft.get("title", "")
    if not title:
        title = getattr(state, "competitor_name", "") or ""
    
    # ✅ 标题清洗：检测非俄语标题（中文/拉丁）→ 用类目名 + 通用描述替换
    if title and not _is_russian_title(title):
        logger.warning(f"⚠️ 标题含非俄语字符，替换为类目通用名: {title[:60]}...")
        # 从类目名派生俄语标题
        cat_name = ozon_cat.get("type_id", "") or ozon_cat.get("description_category_id", "")
        if cat_name and _is_russian_title(cat_name):
            title = f"{cat_name}, универсальный"
        else:
            title = "Товар, универсальный"  # 最终兜底

    # 采购信息
    purchase_cost = float(draft.get("purchase_cost", 0) or 0)
    purchase_url = draft.get("purchase_url", "")

    # 1688 SKU ID
    item_id = draft.get("item_id", "") or draft.get("sku_id", "") or ""
    sku_id = draft.get("sku_id", "") or item_id

    if not description_category_id or not type_id:
        logger.error("❌ 跟卖导入失败: draft.ozon_category 缺少 description_category_id 或 type_id")
        state.error_message = "跟卖导入: ozon_category 不完整，请确认 Skill CDP 抓取成功"
        state.failed_stage = "follow_sell_import"
        return state

    if not images:
        logger.warning("⚠️ 跟卖: 无竞品图片（draft.images 为空），Ozon 将报 image_absent")

    logger.info(
        f"🔄 跟卖导入 v2: cat={description_category_id}, type={type_id}, "
        f"images={len(images)}, title={title[:60] if title else 'N/A'}"
    )

    # ── Step 2: 构建 Ozon 上传 payload ──
    client_id = state.ozon_client_id
    api_key = state.ozon_api_key
    headers = {
        "Client-Id": client_id,
        "Api-Key": api_key,
        "Content-Type": "application/json",
    }

    # 价格：使用定价公式
    margin_rate = float((extensions or {}).get("margin_rate", 0.25))
    commission_rate = float((extensions or {}).get("commission_rate", 0.10))
    if purchase_cost > 0:
        price_val = max(10, int(purchase_cost * (1 + margin_rate) / (1 - commission_rate)))
        old_price_val = price_val + max(3, int(price_val * 0.2))
    else:
        price_val, old_price_val = 500, 600  # 无成本时兜底

    # 尺寸/重量兜底
    dims = draft.get("dimensions", {}) or {}
    depth = int(dims.get("length", 100) or 100)
    width = int(dims.get("width", 100) or 100)
    height = int(dims.get("height", 100) or 100)
    weight_g = int(draft.get("weight", 100) or 100)

    # offer_id: 用 1688 item_id + 时间戳
    offer_id = f"{item_id}_{int(time.time()) % 100000}" if item_id else f"follow_{int(time.time())}"

    # 构建 attributes（属性硬化）
    attributes: list[dict[str, Any]] = []

    # 品牌 → Нет бренда
    for brand_id in BRAND_ATTR_IDS:
        attributes.append({
            "id": brand_id,
            "values": [{"dictionary_value_id": NO_BRAND_DICT_ID, "value": "Нет бренда"}]
        })

    # 生产国 → Китай
    attributes.append({
        "id": COUNTRY_ATTR_ID,
        "values": [{"dictionary_value_id": CHINA_DICT_ID, "value": "Китай"}]
    })

    # 型号名称 (9048) = item_id
    if item_id:
        attributes.append({
            "id": 9048,
            "values": [{"dictionary_value_id": 0, "value": str(item_id)}]
        })

    # 件数 (8962) = 1
    attributes.append({
        "id": 8962,
        "values": [{"dictionary_value_id": 0, "value": "1"}]
    })

    ozon_payload: dict[str, Any] = {
        "items": [{
            "name": title,
            "offer_id": offer_id,
            "description_category_id": int(description_category_id) if description_category_id.isdigit() else 0,
            "type_id": int(type_id) if type_id.isdigit() else 0,
            "price": str(price_val),
            "old_price": str(old_price_val),
            "vat": "0",
            "currency_code": state.currency_code or "CNY",
            "weight": weight_g,
            "weight_unit": "g",
            "depth": depth,
            "width": width,
            "height": height,
            "dimension_unit": "mm",
            "images": images[:10],  # 最多 10 张
            "attributes": attributes,
            "complex_attributes": [],
            "barcode": "",
            "images360": [],
            "pdf_list": [],
        }]
    }

    # ── Step 3: 上传到 Ozon ──
    logger.info(f"📤 跟卖上传: offer_id={offer_id}, images={len(images)}, price={price_val}")
    try:
        upload_resp = req.post(
            "https://api-seller.ozon.ru/v3/product/import",
            headers=headers,
            json=ozon_payload,
            timeout=30,
        )
        if upload_resp.status_code != 200:
            err = upload_resp.text[:300]
            logger.error(f"❌ 跟卖上传失败: HTTP {upload_resp.status_code}, body={err}")
            state.error_message = f"跟卖上传失败: HTTP {upload_resp.status_code} - {err}"
            state.failed_stage = "follow_sell_import"
            return state

        upload_data = upload_resp.json()
        task_id = str(upload_data.get("result", {}).get("task_id", ""))
        logger.info(f"✅ 跟卖上传已提交, task_id={task_id}")
    except Exception as e:
        logger.error(f"❌ 跟卖上传异常: {e}")
        state.error_message = f"跟卖上传异常: {e}"
        state.failed_stage = "follow_sell_import"
        return state

    # ── Step 4: 轮询导入状态 ──
    max_wait = 120  # 跟卖简化：120 秒足够
    poll_interval = 3
    waited = 0

    while waited < max_wait:
        time.sleep(poll_interval)
        waited += poll_interval
        try:
            info_resp = req.post(
                "https://api-seller.ozon.ru/v1/product/import/info",
                headers=headers,
                json={"task_id": task_id},
                timeout=15,
            )
            if info_resp.status_code != 200:
                continue

            info_data = info_resp.json().get("result", {})
            st = info_data.get("status", "")

            if st == "success":
                items = info_data.get("items", [])
                if items:
                    state.product_id = str(items[0].get("product_id", ""))
                    state.upload_status = "success"
                    logger.info(f"✅ 跟卖导入完成, product_id={state.product_id}")
                break
            elif st == "failed":
                err_items = info_data.get("items", [])
                err_codes = []
                for ei in err_items:
                    for e in (ei.get("errors", []) or []):
                        err_codes.append(e.get("code", "?"))
                logger.error(f"❌ 跟卖导入失败: {err_codes}")
                state.error_message = f"跟卖导入失败: {err_codes}"
                state.failed_stage = "follow_sell_import"
                return state
        except Exception:
            continue
    else:
        logger.warning(f"⚠️ 跟卖导入轮询超时 ({max_wait}s)，继续后续流程")
        state.upload_status = "pending"

    # ── Step 5: 写入状态 ──
    state.description_category_id = description_category_id
    state.type_id = type_id
    state.competitor_name = title
    state.original_images = images

    logger.info(
        f"✅ 跟卖导入 v2 完成: product_id={state.product_id}, "
        f"cat={description_category_id}, images={len(images)}"
    )

    return state


def _resolve_category_ids(dc_name: str, type_name: str) -> tuple[str, str]:
    """将 CDP 抓取的俄语类目名称解析为 Ozon API 所需的数字 ID。
    
    使用 pg_trgm 搜索 category_tree_nodes 表（language=RU）。
    优先搜索 type 节点（node_type='type'），因为 type_id 必须是 type 节点。
    
    Returns:
        (description_category_id, type_id) — 如果找不到则返回 ("", "")
    """
    try:
        from storage.database.db import get_session
        from sqlalchemy import text
        
        session = get_session()
        try:
            # 搜索 type 节点（必须返回 type_id > 0）
            search_name = type_name or dc_name
            result = session.execute(
                text("""
                    SELECT description_category_id, type_id, node_name,
                           similarity(node_name, :name) AS sim
                    FROM category_tree_nodes
                    WHERE language = 'RU' 
                      AND node_type = 'type'
                      AND type_id IS NOT NULL
                      AND type_id > 0
                    ORDER BY sim DESC LIMIT 5
                """),
                {"name": search_name}
            )
            rows = result.fetchall()
            
            dc_id = ""
            type_id = ""
            if rows:
                # 优先精确匹配
                for row in rows:
                    if row.node_name.lower() == search_name.lower():
                        dc_id = str(row.description_category_id)
                        type_id = str(row.type_id)
                        break
                # 无精确匹配，取最相似的（sim > 0.3）
                if not dc_id and rows[0].sim > 0.3:
                    dc_id = str(rows[0].description_category_id)
                    type_id = str(rows[0].type_id)
                    logger.info(
                        "pg_trgm 模糊匹配: '%s' → '%s' (sim=%.3f, dc=%s, type=%s)",
                        search_name, rows[0].node_name, rows[0].sim, dc_id, type_id
                    )
            
            session.close()
            return dc_id, type_id
        finally:
            try:
                session.close()
            except Exception:
                pass
    except Exception as e:
        logger.warning("pg_trgm 类目解析异常: %s", e)
        return "", ""


def _is_russian_title(title: str) -> bool:
    """检测标题是否主要为俄语（西里尔字母）。
    
    如果标题中 Cyrillic 字符占比 > 50%，视为俄语标题。
    否则视为中文/拉丁/其他语言标题。
    """
    if not title:
        return False
    cyrillic = sum(1 for c in title if '\u0400' <= c <= '\u04FF')
    total = len(title.strip())
    return total > 0 and (cyrillic / total) > 0.5
