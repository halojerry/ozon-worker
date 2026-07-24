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
    import_body: Dict[str, Any] = {
        "items": [{
            "sku": int(ozon_product_id),
            "offer_id": offer_id,
            "price": "10",
            "old_price": "12",
            "vat": "0",
        }]
    }

    # 如果有竞品图片，传入（提高卡片质量）
    competitor_images = draft.get("images", [])
    if competitor_images:
        import_body["items"][0]["images"] = competitor_images[:10]

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
    max_wait = 60  # 最多等 60 秒
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
        logger.warning(f"⚠️ import 轮询超时 ({max_wait}s)，继续尝试获取类目")

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
                logger.info(
                    f"✅ 跟卖类目: description_category_id={state.description_category_id}, "
                    f"type_id={state.type_id}"
                )
            else:
                logger.warning(f"⚠️ 未找到 offer_id={offer_id} 的产品信息")
        else:
            logger.warning(f"⚠️ /v3/product/info/list 返回 {info_list_resp.status_code}")
    except Exception as e:
        logger.warning(f"⚠️ 获取类目 ID 失败: {e}")

    # 确保 offer_id 传递到下游
    if not state.description_category_id:
        logger.warning("⚠️ 未能获取跟卖类目 ID，后续类目匹配节点将兜底处理")

    return state
