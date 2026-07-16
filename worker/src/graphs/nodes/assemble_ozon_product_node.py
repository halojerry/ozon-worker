"""
统一商品组装节点 — 替代 4 节点管线

将 category_lookup + attributes_fetch + attributes_llm + attributes_learning
合并为单一 Python 函数，消除跨节点状态传递 bug。

流程:
  1. PG 缓存查询 → pg_trgm 搜索 top-15 候选类目
  2. LLM 类目匹配 → 从候选中选出 description_category_id + type_id
  3. PG 缓存查询 → 获取属性 schema + 字典值
  4. LLM 完整组装 → 输出完整 /v3/product/import items JSON
  5. 解析校验 → 写入 state 兼容下游节点

替代节点:
  - category_lookup_node
  - attributes_fetch_node
  - attributes_llm_node
  - attributes_learning_node
"""

import os
import json
import time
import logging
import re
import requests
from typing import Any, Optional
from jinja2 import Template
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context

from graphs.state import GlobalState
from utils.mxou_llm import call_mxou_chat_api
from utils.progress_logger import ProgressLogger
from utils.ozon_category_query import get_category_query, OzonCategoryQuery
from utils.http_session import session

logger = logging.getLogger(__name__)

# ==================== 常量 ====================

# 品牌属性ID列表（按优先级）
BRAND_ATTRIBUTE_IDS = [85, 5076, 23171]

# "无品牌" 字典值
NO_BRAND_DICT_ID = 126745801
NO_BRAND_VALUE = "Нет бренда"

# 原产国（中国）
COUNTRY_ATTR_ID = 4389
CHINA_DICT_ID = 90296
CHINA_VALUE = "Китай"

# Ozon 强制属性
FORCE_ATTR_9048 = 9048   # 变体绑定名
FORCE_ATTR_8229 = 8229   # 类型名称
FORCE_ATTR_4191 = 4191   # 完整描述
FORCE_ATTR_4180 = 4180   # 短描述/关键字
FORCE_ATTR_4958 = 4958   # 适用对象（部分类目）
FORCE_ATTR_8962 = 8962   # 件数（部分类目）
FORCE_ATTR_23171 = 23171 # hashtag 标签（部分类目）

# 分类名属性（8229 的替代）
TYPE_NAME_ATTR_IDS = [8229]

# 集合属性（values 数组可包含多个元素）
COLLECTION_ATTR_IDS = {9048, 23171}


def assemble_ozon_product_node(
    state: GlobalState,
    config: RunnableConfig,
    runtime: Runtime[Context],
) -> dict[str, Any]:
    """
    统一商品组装节点。

    输入: GlobalState（含 draft, token, ozon_client_id, ozon_api_key, pricing_info）
    输出: dict（被 LangGraph 合并到 GlobalState）
    """
    progress = ProgressLogger()
    progress.log_node_start("assemble_ozon_product", "统一商品组装")
    progress.log_node_action("Step 1: 类目匹配...")

    draft: dict[str, Any] = state.draft or {}
    token: str = state.token or ""
    ozon_client_id: str = str(state.ozon_client_id or "")
    ozon_api_key: str = state.ozon_api_key or ""
    currency_code: str = state.currency_code or "RUB"

    title: str = draft.get("title", "")
    description: str = draft.get("description", "")
    images: list[str] = draft.get("images", []) or []
    weight_grams: int = draft.get("weight", 100)
    dimensions: dict[str, int] = draft.get("dimensions", {}) or {}
    purchase_cost: float = float(draft.get("purchase_cost", 0) or 0)
    sku_id: str = draft.get("sku_id", "")
    attributes_1688: dict[str, Any] = draft.get("attributes", {}) or {}
    variants: list[dict[str, Any]] = draft.get("variants", []) or []

    # 定价信息（来自 pricing_node）
    pricing_info: dict[str, Any] = state.pricing_info if hasattr(state, 'pricing_info') else {}
    price_rub: str = str(pricing_info.get("price", "1000"))
    old_price_rub: str = str(pricing_info.get("old_price", "1500"))

    if not title:
        logger.error("产品标题为空，无法进行类目匹配")
        return {"error_message": "产品标题为空，无法进行类目匹配"}

    # 初始化查询助手
    query = get_category_query()

    # =====================================================
    # Step 1: 类目匹配
    # =====================================================
    logger.info(f"🔍 Step 1: 类目匹配 — 产品: {title[:60]}")

    # 1a. 提取搜索关键词
    keywords = _extract_keywords(title, description, attributes_1688)
    logger.info(f"   关键词: {keywords}")

    # 1b. PG 缓存搜索 top-15 候选
    candidates = query.search_nodes(keywords, top_k=15, node_type="type")
    if not candidates:
        # 回退：不过滤 node_type
        candidates = query.search_nodes(keywords, top_k=15, node_type=None)
        logger.warning("   叶子类型搜索为空，回退到全部节点搜索")

    if not candidates:
        logger.error("❌ 类目搜索无结果")
        return {"error_message": "类目匹配失败：无候选类目"}

    logger.info(f"   pg_trgm 返回 {len(candidates)} 个候选")

    # 1c. LLM 从候选中选最佳类目
    category_result = _llm_match_category(title, description, attributes_1688, candidates, token)

    if not category_result:
        # 回退：取 similarity 最高的候选
        best = candidates[0]
        category_result = {
            "description_category_id": best["description_category_id"],
            "type_id": best["type_id"],
            "category_path": best["full_path"],
            "confidence": "low",
            "reason": f"LLM 失败，回退到最高相似度候选: {best['node_name']}",
        }
        logger.warning(f"   LLM 类目匹配失败，回退到: {best['full_path']}")

    description_category_id: int = int(category_result["description_category_id"])
    type_id: int = int(category_result["type_id"])
    category_path: str = category_result.get("category_path", "")
    logger.info(f"   ✅ 类目匹配: [{description_category_id}/{type_id}] {category_path}")

    # =====================================================
    # Step 2: 获取属性 Schema（PG 缓存优先，Ozon API 回退）
    # =====================================================
    progress.log_node_action(f"Step 2: 获取属性 Schema — category={description_category_id}, type={type_id}")

    attr_schema = query.get_attribute_schema(description_category_id, type_id)
    if attr_schema and isinstance(attr_schema, dict) and attr_schema.get("result"):
        attr_list: list[dict[str, Any]] = attr_schema["result"]
        logger.info(f"   ✅ PG 缓存命中: {len(attr_list)} 个属性")
    else:
        # Ozon API 回退
        logger.info("   PG 缓存未命中，调用 Ozon API...")
        attr_list = _fetch_attribute_schema_from_ozon(
            ozon_client_id, ozon_api_key,
            description_category_id, type_id
        )
        if not attr_list:
            logger.error("❌ 属性 Schema 获取失败")
            return {"error_message": f"属性 Schema 获取失败: category={description_category_id}, type={type_id}"}
        logger.info(f"   ✅ Ozon API 返回: {len(attr_list)} 个属性")

    # 标记必填属性
    required_attrs = [a for a in attr_list if a.get("is_required", False)]
    logger.info(f"   其中 {len(required_attrs)} 个必填属性")

    # =====================================================
    # Step 3: 预加载字典值（dict attributes only）
    # =====================================================
    logger.info("📖 Step 3: 预加载字典值")

    dict_lookup: dict[int, list[dict[str, Any]]] = {}
    for attr in attr_list:
        dict_id = attr.get("dictionary_id", 0)
        if dict_id and dict_id > 0:
            attr_id = int(attr.get("id", 0))
            values = query.get_dictionary_values(attr_id, description_category_id, type_id)
            if values and isinstance(values, list) and len(values) > 0:
                dict_lookup[attr_id] = values
            elif isinstance(values, dict) and values.get("result"):
                dict_lookup[attr_id] = values["result"]

    dict_attr_count = sum(1 for a in attr_list if a.get("dictionary_id", 0) > 0)
    cached_dict_count = len(dict_lookup)
    logger.info(f"   字典属性: {dict_attr_count} 个, PG 缓存命中: {cached_dict_count} 个")

    # 精简字典值（每属性最多 30 个值传给 LLM）
    summarized_dict: dict[str, Any] = {}
    for attr_id, values in dict_lookup.items():
        if len(values) <= 30:
            summarized_dict[str(attr_id)] = [
                {"id": v.get("id"), "value": v.get("value"), "info": v.get("info", "")}
                for v in values if isinstance(v, dict)
            ]
        else:
            summarized_dict[str(attr_id)] = {
                "total_count": len(values),
                "sample_values": [
                    {"id": v.get("id"), "value": v.get("value"), "info": v.get("info", "")}
                    for v in values[:30] if isinstance(v, dict)
                ],
                "note": f"共{len(values)}个值，仅显示前30个，如需匹配未显示的值请在sample_values中查找"
            }

    # =====================================================
    # Step 4: LLM 完整组装
    # =====================================================
    logger.info("🤖 Step 4: LLM 组装完整 /v3/product/import JSON")

    items_json = _llm_assemble_product(
        draft=draft,
        description_category_id=description_category_id,
        type_id=type_id,
        category_path=category_path,
        attr_list=attr_list,
        dict_lookup=summarized_dict,
        token=token,
        currency_code=currency_code,
        price_rub=price_rub,
        old_price_rub=old_price_rub,
    )

    if not items_json or not items_json.get("items"):
        logger.error("❌ LLM 组装失败，返回空 items")
        return {"error_message": "LLM 组装失败：未生成有效的 items JSON"}

    items = items_json.get("items", [])
    logger.info(f"   ✅ LLM 生成 {len(items)} 个 item(s)")

    # =====================================================
    # Step 5: 解析 + 校验 + 补充
    # =====================================================
    logger.info("🔍 Step 5: 解析校验 LLM 输出")

    items = _validate_and_enrich_items(
        items=items,
        attr_list=attr_list,
        dict_lookup=dict_lookup,
        images=images,
        ozon_client_id=ozon_client_id,
        ozon_api_key=ozon_api_key,
        description_category_id=description_category_id,
        type_id=type_id,
        weight_grams=weight_grams,
        dimensions=dimensions,
    )

    # =====================================================
    # Step 6: 提取 final_attributes（兼容下游节点）
    # =====================================================
    # 提取第一个 item 的属性作为 final_attributes（兼容 prepare_ozon_upload）
    final_attributes: list[dict[str, Any]] = []
    if items and items[0].get("attributes"):
        for attr in items[0]["attributes"]:
            for v in (attr.get("values") or []):
                final_attributes.append({
                    "attribute_id": attr["id"],
                    "value": v.get("value", ""),
                    "dictionary_value_id": v.get("dictionary_value_id", 0),
                    "source": "llm",
                })

    # 为兼容 learning_record_node，同时设置 llm_attributes
    llm_attributes = final_attributes

    # =====================================================
    # Step 7: 返回结果 dict（LangGraph 自动合并到 GlobalState）
    # =====================================================
    progress.log_node_success(f"类目={category_path}, 属性={len(final_attributes)}个, items={len(items)}个")

    logger.info(f"✅ 统一组装完成: 类目=[{description_category_id}/{type_id}], 属性={len(final_attributes)}个, items={len(items)}个")

    return {
        "description_category_id": str(description_category_id),
        "type_id": str(type_id),
        "attributes_schema": attr_list,
        "dictionary_values": dict_lookup,
        "final_attributes": final_attributes,
        "llm_attributes": llm_attributes,
        "learned_attributes": {},
        "ozon_payloads": [{"items": items}],
    }


# ==================== 辅助函数 ====================


def _extract_keywords(title: str, description: str, attributes: dict[str, Any]) -> str:
    """从产品数据中提取搜索关键词"""
    parts: list[str] = []

    # 取标题前 30 个字符
    if title:
        parts.append(title[:60])

    # 取描述的关键片段
    if description:
        # 尝试取中文部分
        desc_clean = description[:200]
        parts.append(desc_clean)

    # 属性中的值
    if attributes:
        attr_vals = []
        for k, v in list(attributes.items())[:5]:
            if isinstance(v, str) and len(v) < 50:
                attr_vals.append(f"{k}:{v}")
        if attr_vals:
            parts.append("; ".join(attr_vals))

    return " ".join(parts)[:500]


def _llm_match_category(
    title: str,
    description: str,
    attributes: dict[str, Any],
    candidates: list[dict[str, Any]],
    token: str,
) -> Optional[dict[str, Any]]:
    """LLM 从候选类目列表中选出最佳匹配"""
    try:
        workspace = os.getenv("COZE_WORKSPACE_PATH", "/workspace/projects")
        cfg_path = os.path.join(workspace, "config/category_match_v2_cfg.json")

        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)

        llm_cfg = cfg.get("config", {})
        model_id = llm_cfg.get("model", "deepseek-v4-flash")
        sp_template = cfg.get("sp", "")
        up_template = cfg.get("up", "")

        sp_tpl = Template(sp_template)
        up_tpl = Template(up_template)

        system_prompt = sp_tpl.render({})

        # 准备模板变量
        attr_flat = {}
        if attributes:
            for k, v in attributes.items():
                if isinstance(v, (str, int, float)):
                    attr_flat[k] = str(v)

        user_prompt = up_tpl.render({
            "title": title,
            "description": description[:500] if description else "",
            "attributes": attr_flat,
            "candidates": candidates,
        })

        resp = call_mxou_chat_api(
            token=token,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model=model_id,
            temperature=0.0,
            max_tokens=1024,
        ) or ""

        if not resp.strip():
            logger.error("LLM 类目匹配返回空")
            return None

        # 清理 JSON
        resp = resp.replace("```json", "").replace("```", "").strip()
        # 尝试提取 JSON 对象
        match = re.search(r'\{[^{}]*"description_category_id"[^{}]*\}', resp, re.DOTALL)
        if match:
            resp = match.group(0)

        result = json.loads(resp)
        logger.info(f"   LLM 类目匹配: {result.get('category_path', '')} (confidence={result.get('confidence', '?')})")
        return result

    except json.JSONDecodeError as e:
        logger.error(f"LLM 类目匹配 JSON 解析失败: {e}, raw={resp[:200]}")
        return None
    except Exception as e:
        logger.error(f"LLM 类目匹配异常: {e}")
        return None


def _fetch_attribute_schema_from_ozon(
    ozon_client_id: str,
    ozon_api_key: str,
    description_category_id: int,
    type_id: int,
) -> list[dict[str, Any]]:
    """从 Ozon API 获取属性 Schema（回退路径）"""
    try:
        url = "https://api-seller.ozon.ru/v1/description-category/attribute"
        headers = {
            "Client-Id": ozon_client_id,
            "Api-Key": ozon_api_key,
            "Content-Type": "application/json",
        }
        payload = {
            "description_category_id": description_category_id,
            "type_id": type_id,
            "language": "ZH_HANS",
        }
        resp = session.post(url, json=payload, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        result = data.get("result", [])
        logger.info(f"   Ozon API 返回 {len(result)} 个属性")
        return result
    except Exception as e:
        logger.error(f"Ozon 属性 API 调用失败: {e}")
        return []


def _llm_assemble_product(
    draft: dict[str, Any],
    description_category_id: int,
    type_id: int,
    category_path: str,
    attr_list: list[dict[str, Any]],
    dict_lookup: dict[str, Any],
    token: str,
    currency_code: str,
    price_rub: str,
    old_price_rub: str,
) -> Optional[dict[str, Any]]:
    """LLM 组装完整 /v3/product/import items JSON"""
    try:
        workspace = os.getenv("COZE_WORKSPACE_PATH", "/workspace/projects")
        cfg_path = os.path.join(workspace, "config/product_assembly_cfg.json")

        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)

        llm_cfg = cfg.get("config", {})
        model_id = llm_cfg.get("model", "deepseek-v4-flash")
        sp_template = cfg.get("sp", "")
        up_template = cfg.get("up", "")

        sp_tpl = Template(sp_template)
        up_tpl = Template(up_template)

        system_prompt = sp_tpl.render({})

        # 准备模板变量
        title = draft.get("title", "")
        desc = draft.get("description", "")
        images = draft.get("images", []) or []
        weight = draft.get("weight", 100)
        dims = draft.get("dimensions", {}) or {}
        cost = draft.get("purchase_cost", 0)
        sku_id = draft.get("sku_id", "")
        variants = draft.get("variants", []) or []
        supplier = draft.get("supplier", "")
        stock = draft.get("stock", "")

        user_prompt = up_tpl.render({
            "sku_id": sku_id,
            "title": title,
            "description": desc[:1000] if desc else "",
            "purchase_cost": cost,
            "weight": weight,
            "depth": dims.get("length", 100),
            "width": dims.get("width", 100),
            "height": dims.get("height", 50),
            "supplier": supplier,
            "stock": stock,
            "images": images[:15],
            "description_category_id": description_category_id,
            "type_id": type_id,
            "category_path": category_path,
            "attributes_schema": attr_list,
            "dict_lookup": dict_lookup,
            "currency_code": currency_code,
            "price": price_rub,
            "old_price": old_price_rub,
            "variants": variants,
        })

        resp = call_mxou_chat_api(
            token=token,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model=model_id,
            temperature=0.0,
            max_tokens=8192,
        ) or ""

        if not resp.strip():
            logger.error("LLM 组装返回空")
            return None

        # 清理 JSON
        resp = resp.strip()
        # 移除 Markdown 代码块
        resp = re.sub(r'^```(?:json)?\s*\n?', '', resp)
        resp = re.sub(r'\n?```\s*$', '', resp)

        # 尝试提取最外层 JSON 对象
        match = re.search(r'\{[^{}]*"items"\s*:\s*\[.*\]\s*\}', resp, re.DOTALL)
        if match:
            resp = match.group(0)

        result = json.loads(resp)
        if not isinstance(result, dict) or "items" not in result:
            logger.error(f"LLM 输出格式错误: {list(result.keys()) if isinstance(result, dict) else type(result)}")
            return None

        return result

    except json.JSONDecodeError as e:
        # 截断日志避免输出过大
        snippet = resp[:500] if 'resp' in dir() else ""
        logger.error(f"LLM 组装 JSON 解析失败: {e}, snippet={snippet}")
        return None
    except Exception as e:
        logger.error(f"LLM 组装异常: {e}")
        return None


def _validate_and_enrich_items(
    items: list[dict[str, Any]],
    attr_list: list[dict[str, Any]],
    dict_lookup: dict[int, list[dict[str, Any]]],
    images: list[str],
    ozon_client_id: str,
    ozon_api_key: str,
    description_category_id: int,
    type_id: int,
    weight_grams: int,
    dimensions: dict[str, int],
) -> list[dict[str, Any]]:
    """校验 LLM 输出的 items 并补充缺失字段"""

    # 构建属性索引
    attr_by_id: dict[int, dict[str, Any]] = {
        int(a["id"]): a for a in attr_list if "id" in a
    }
    required_attr_ids = {
        int(a["id"]) for a in attr_list
        if a.get("is_required", False) and "id" in a
    }

    validated_items: list[dict[str, Any]] = []

    for idx, item in enumerate(items):
        # === 基本字段补全 ===
        if not item.get("description_category_id"):
            item["description_category_id"] = description_category_id
        if not item.get("type_id"):
            item["type_id"] = type_id
        if not item.get("currency_code"):
            item["currency_code"] = "RUB"
        if not item.get("vat"):
            item["vat"] = "0"
        if not item.get("dimension_unit"):
            item["dimension_unit"] = "mm"
        if not item.get("weight_unit"):
            item["weight_unit"] = "g"
        if not item.get("depth") or item.get("depth") == 0:
            item["depth"] = dimensions.get("length", 100)
        if not item.get("width") or item.get("width") == 0:
            item["width"] = dimensions.get("width", 100)
        if not item.get("height") or item.get("height") == 0:
            item["height"] = dimensions.get("height", 50)
        if not item.get("weight") or item.get("weight") == 0:
            item["weight"] = weight_grams

        # 图片
        if not item.get("images"):
            item["images"] = images[:15]
        if not item.get("primary_image") and images:
            item["primary_image"] = images[0] if images else ""

        # 数组字段
        item.setdefault("complex_attributes", [])
        item.setdefault("images360", [])
        item.setdefault("pdf_list", [])
        item.setdefault("barcode", item.get("barcode", ""))

        # === 属性校验 ===
        attrs = item.get("attributes", [])
        seen_ids: set[int] = set()

        validated_attrs: list[dict[str, Any]] = []
        for attr in attrs:
            if not isinstance(attr, dict):
                continue

            attr_id = int(attr.get("id", 0))
            if attr_id == 0:
                continue
            if attr_id in seen_ids:
                logger.warning(f"   重复 attribute_id={attr_id}，跳过")
                continue
            seen_ids.add(attr_id)

            # 确保有 complex_id
            if "complex_id" not in attr:
                attr["complex_id"] = 0

            # 校验 values
            values = attr.get("values", [])
            if not isinstance(values, list):
                values = [values]
            if not values:
                values = [{"dictionary_value_id": 0, "value": ""}]

            validated_values = []
            for v in values:
                if not isinstance(v, dict):
                    continue
                dict_val_id = v.get("dictionary_value_id", 0)
                value = v.get("value", "")

                # 字典属性校验 dictionary_value_id
                schema_attr = attr_by_id.get(attr_id, {})
                dict_id = schema_attr.get("dictionary_id", 0)

                if dict_id and dict_id > 0 and dict_val_id == 0:
                    # 尝试从 dict_lookup 中查找匹配
                    dict_vals = dict_lookup.get(attr_id, [])
                    if isinstance(dict_vals, list):
                        for dv in dict_vals:
                            if isinstance(dv, dict) and dv.get("value", "").lower() == str(value).lower():
                                dict_val_id = dv.get("id", 0)
                                logger.info(f"   ✅ 修正 dictionary_value_id: attr={attr_id}, value='{value}' → id={dict_val_id}")
                                break

                validated_values.append({
                    "dictionary_value_id": int(dict_val_id) if dict_val_id else 0,
                    "value": str(value),
                })

            attr["values"] = validated_values
            validated_attrs.append(attr)

        # === 补充缺失的必填属性 ===
        present_ids = {int(a["id"]) for a in validated_attrs if "id" in a}
        missing_required = required_attr_ids - present_ids

        for missing_id in sorted(missing_required):
            schema_attr = attr_by_id.get(missing_id, {})
            if not schema_attr:
                continue

            dict_id = schema_attr.get("dictionary_id", 0)
            new_attr: dict[str, Any] = {
                "complex_id": 0,
                "id": missing_id,
                "values": [],
            }

            if dict_id and dict_id > 0:
                # 字典属性 → 取第一个可用值
                dict_vals = dict_lookup.get(missing_id, [])
                if isinstance(dict_vals, list) and dict_vals:
                    first = dict_vals[0]
                    if isinstance(first, dict):
                        new_attr["values"] = [{
                            "dictionary_value_id": first.get("id", 0),
                            "value": str(first.get("value", "")),
                        }]
                elif isinstance(dict_vals, dict) and dict_vals.get("result"):
                    first = dict_vals["result"][0] if dict_vals["result"] else {}
                    if first:
                        new_attr["values"] = [{
                            "dictionary_value_id": first.get("id", 0),
                            "value": str(first.get("value", "")),
                        }]
                else:
                    new_attr["values"] = [{"dictionary_value_id": 0, "value": ""}]
            else:
                # 自由文本属性
                new_attr["values"] = [{"dictionary_value_id": 0, "value": ""}]

            validated_attrs.append(new_attr)
            logger.warning(f"   ⚠️ 补充缺失必填属性: id={missing_id} ({schema_attr.get('name', '?')})")

        # === 特殊属性修正 ===
        # 品牌（85, 5076）
        for brand_id in BRAND_ATTRIBUTE_IDS:
            brand_attr = next((a for a in validated_attrs if int(a.get("id", 0)) == brand_id), None)
            if brand_attr:
                values = brand_attr.get("values", [])
                for v in values:
                    if v.get("dictionary_value_id", 0) == 0:
                        v["dictionary_value_id"] = NO_BRAND_DICT_ID
                        v["value"] = NO_BRAND_VALUE
                        logger.info(f"   ✅ 品牌 attribute_id={brand_id} 修正为 'Нет бренда'")

        # 原产国（4389）
        country_attr = next((a for a in validated_attrs if int(a.get("id", 0)) == COUNTRY_ATTR_ID), None)
        if country_attr:
            values = country_attr.get("values", [])
            for v in values:
                if v.get("dictionary_value_id", 0) == 0:
                    v["dictionary_value_id"] = CHINA_DICT_ID
                    v["value"] = CHINA_VALUE
        else:
            # 4389 是很多类目的必填属性，如果缺失则补充
            validated_attrs.append({
                "complex_id": 0,
                "id": COUNTRY_ATTR_ID,
                "values": [{"dictionary_value_id": CHINA_DICT_ID, "value": CHINA_VALUE}],
            })

        # 9048（变体绑定名）
        if FORCE_ATTR_9048 not in present_ids and FORCE_ATTR_9048 not in {int(a["id"]) for a in validated_attrs}:
            offer_id = item.get("offer_id", "unknown")
            validated_attrs.append({
                "complex_id": 0,
                "id": FORCE_ATTR_9048,
                "values": [{"dictionary_value_id": 0, "value": f"{offer_id}_variant"}],
            })

        item["attributes"] = validated_attrs
        validated_items.append(item)

    return validated_items
