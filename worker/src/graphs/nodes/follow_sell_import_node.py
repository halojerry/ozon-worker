"""跟卖导入节点 — import-by-sku 复制竞品卡片 + 获取类目结构

跟卖管线的第一步：将竞品 Ozon 商品卡片复制到我们的店铺，
获取正确的 description_category_id 和 type_id，供后续节点使用。
"""
import logging
import time
from typing import Any, Dict

import requests as req

from graphs.state import GlobalState

logger = logging.getLogger(__name__)


def follow_sell_import_node(state: GlobalState) -> GlobalState:
    """
    跟卖导入节点:
      1. 从 envelope.draft 获取 ozon_product_id（竞品 ID）
      2. POST /v1/product/import-by-sku 复制卡片
      3. 轮询 import task 状态
      4. GET /v3/product/info/list 获取类目 ID
      5. 写入 state.description_category_id + state.type_id
    """
    draft = state.envelope.get("draft", {})
    ozon_product_id = str(draft.get("ozon_product_id", ""))
    offer_id = f"follow_{ozon_product_id}"

    if not ozon_product_id:
        logger.error("❌ 跟卖导入失败: envelope.draft.ozon_product_id 为空")
        state.error_message = "跟卖导入需要 ozon_product_id"
        state.failed_stage = "follow_sell_import"
        return state

    logger.info(f"🔄 跟卖导入: product_id={ozon_product_id}, offer_id={offer_id}")

    client_id = state.ozon_client_id
    api_key = state.ozon_api_key
    headers = {
        "Client-Id": client_id,
        "Api-Key": api_key,
        "Content-Type": "application/json",
    }

    # Step 1: import-by-sku
    # ✅ 使用 envelope 中的 purchase_cost 计算合理价格，而非硬编码 10/12
    purchase_cost = float(draft.get("purchase_cost", 0) or 0)
    margin_rate = float((envelope.get("extensions", {}) or {}).get("margin_rate", 0.25))
    if purchase_cost > 0:
        # CNY 店铺定价公式: 售价 = 成本 * (1+利润率) / (1-佣金率)
        commission_rate = float((envelope.get("extensions", {}) or {}).get("commission_rate", 0.10))
        price_val = max(10, int(purchase_cost * (1 + margin_rate) / (1 - commission_rate)))
        old_price_val = price_val + max(3, int(price_val * 0.2))
    else:
        price_val, old_price_val = 100, 120  # 无成本信息时的兜底
    
    import_body: Dict[str, Any] = {
        "items": [{
            "sku": int(ozon_product_id),
            "offer_id": offer_id,
            "price": str(price_val),
            "old_price": str(old_price_val),
            "vat": "0",
        }]
    }

    # ❌ 不传 1688 原图到 import-by-sku：1688 图片可能含物流/退货文字被 Ozon 拒绝
    # import-by-sku 会自动复制竞品商品卡上的 Ozon 审核通过的干净图片
    # competitor_images = draft.get("images", [])  # REMOVED — 用竞品原图

    try:
        import_resp = req.post(
            "https://api-seller.ozon.ru/v1/product/import-by-sku",
            headers=headers,
            json=import_body,
            timeout=30,
        )
        if import_resp.status_code != 200:
            logger.error(f"❌ import-by-sku 失败: HTTP {import_resp.status_code}, body={import_resp.text[:300]}")
            state.error_message = f"import-by-sku 失败: HTTP {import_resp.status_code}"
            state.failed_stage = "follow_sell_import"
            return state

        task_id = str(import_resp.json().get("result", {}).get("task_id", ""))
        logger.info(f"✅ import-by-sku 已提交, task_id={task_id}")
    except Exception as e:
        logger.error(f"❌ import-by-sku 异常: {e}")
        state.error_message = f"import-by-sku 异常: {e}"
        state.failed_stage = "follow_sell_import"
        return state

    # Step 2: 轮询 import task
    max_wait = 180  # 最多等 180 秒（Ozon import-by-sku 有时很慢）
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
            status = info_data.get("status", "")

            if status == "success":
                items = info_data.get("items", [])
                if items:
                    product_id = str(items[0].get("product_id", ""))
                    state.product_id = product_id
                    logger.info(f"✅ import 完成, product_id={product_id}")
                break
            elif status == "failed":
                logger.error(f"❌ import 失败: {info_data}")
                state.error_message = f"import-by-sku 任务失败: {info_data}"
                state.failed_stage = "follow_sell_import"
                return state
            # else: pending, continue polling
        except Exception:
            continue
    else:
        logger.warning(f"⚠️ import 轮询超时 ({max_wait}s)，尝试获取类目")
        # 🆕 超时后最后尝试一次：可能已经完成了
        try:
            info_resp = req.post(
                "https://api-seller.ozon.ru/v1/product/import/info",
                headers=headers,
                json={"task_id": task_id},
                timeout=15,
            )
            if info_resp.status_code == 200:
                info_data = info_resp.json().get("result", {})
                if info_data.get("status") == "success":
                    items = info_data.get("items", [])
                    if items:
                        state.product_id = str(items[0].get("product_id", ""))
                        logger.info(f"✅ import 超时后完成, product_id={state.product_id}")
        except Exception:
            pass

    # Step 3: 获取类目 ID
    try:
        # 用 offer_id 查询产品信息
        info_list_resp = req.post(
            "https://api-seller.ozon.ru/v3/product/info/list",
            headers=headers,
            json={"offer_id": [offer_id]},
            timeout=15,
        )
        if info_list_resp.status_code == 200:
            items = info_list_resp.json().get("result", [])
            if items:
                item = items[0]
                state.description_category_id = str(item.get("description_category_id", ""))
                state.type_id = str(item.get("type_id", ""))
                competitor_name = item.get("name", "")
                if competitor_name:
                    state.competitor_name = competitor_name  # 竞品俄语标题，供下游 SEO 使用
                logger.info(
                    f"✅ 跟卖类目: description_category_id={state.description_category_id}, "
                    f"type_id={state.type_id}, 竞品标题={competitor_name[:60] if competitor_name else 'N/A'}"
                )
            else:
                logger.warning(f"⚠️ 未找到 offer_id={offer_id} 的产品信息")
        else:
            logger.warning(f"⚠️ /v3/product/info/list 返回 {info_list_resp.status_code}")
    except Exception as e:
        logger.warning(f"⚠️ 获取类目 ID 失败: {e}")

    # 🆕 Fallback: offer_id 查询失败时，用 product_id 直接查询 Ozon 产品信息
    # product_id 在 Step 2 import 轮询成功时已写入 state，比 offer_id 更可靠
    if not state.description_category_id and state.product_id:
        try:
            logger.info(f"🔄 回退查询: 用 product_id={state.product_id} 获取 Ozon 类目...")
            fallback_resp = req.post(
                "https://api-seller.ozon.ru/v3/product/info/list",
                headers=headers,
                json={"product_id": [int(state.product_id)]},
                timeout=15,
            )
            if fallback_resp.status_code == 200:
                fallback_items = fallback_resp.json().get("result", [])
                if fallback_items:
                    item = fallback_items[0]
                    state.description_category_id = str(item.get("description_category_id", ""))
                    state.type_id = str(item.get("type_id", ""))
                    competitor_name = item.get("name", "")
                    if competitor_name:
                        state.competitor_name = competitor_name
                    logger.info(
                        f"✅ 回退类目(by product_id): description_category_id={state.description_category_id}, "
                        f"type_id={state.type_id}, 竞品标题={competitor_name[:60] if competitor_name else 'N/A'}"
                    )
                else:
                    logger.warning(f"⚠️ product_id={state.product_id} 查询无结果")
            else:
                logger.warning(f"⚠️ 回退查询返回 HTTP {fallback_resp.status_code}")
        except Exception as e:
            logger.warning(f"⚠️ 回退查询异常: {e}")

    # ✅ Step 4: 属性硬化 — 强制品牌=Нет бренда, 国家=Китай
    # import-by-sku 复制竞品卡时可能带入真实品牌，需要覆盖为无品牌避免侵权
    if state.product_id and state.description_category_id:
        try:
            logger.info(f"🔧 跟卖属性硬化: product_id={state.product_id}")
            attr_resp = req.post(
                "https://api-seller.ozon.ru/v1/product/attributes/update",
                headers=headers,
                json={"items": [{
                    "offer_id": offer_id,
                    "product_id": int(state.product_id),
                    "attributes": [
                        {"id": 85, "values": [{"dictionary_value_id": 126745801, "value": "Нет бренда"}]},
                        {"id": 5076, "values": [{"dictionary_value_id": 126745801, "value": "Нет бренда"}]},
                        {"id": 4389, "values": [{"dictionary_value_id": 90296, "value": "Китай"}]},
                    ]
                }]},
                timeout=15,
            )
            if attr_resp.status_code == 200:
                logger.info(f"✅ 跟卖属性硬化成功 (brand→Нет бренда, country→Китай)")
            else:
                logger.warning(f"⚠️ 属性硬化返回 {attr_resp.status_code}: {attr_resp.text[:100]}")
        except Exception as _ae:
            logger.warning(f"⚠️ 属性硬化异常: {_ae}")

    # 确保 offer_id 传递到下游
    if not state.description_category_id:
        logger.warning("⚠️ 未能获取跟卖类目 ID，后续类目匹配节点将兜底处理")

    return state
