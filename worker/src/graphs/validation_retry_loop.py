# ============================================================
# 验证循环修复子图（Validation Retry Loop Subgraph）
#
# 修复策略（基于Ozon真实错误数据）：
#   - warning_attribute_values_out_of_range → error_repair_llm（API搜索字典值 + LLM兜底）
#   - INVALID_ATTRIBUTE_VALUE → error_repair_llm（API搜索 + LLM兜底）
#   - BR_hashtag_validation → error_repair_llm（LLM生成合规标签）
#   - DESCRIPTION_DECLINE → error_repair_llm（LLM重写描述）
#   - MISSING_ATTRIBUTE → error_repair_llm（LLM生成缺失属性）
#   - INVALID_PRICE → repair_pricing（规则计算）
#   - WEIGHT_DIMENSION_ERROR → repair_prepare（规则修正）
#   - INVALID_CATEGORY → error_repair_llm（查类目树 + LLM匹配）
#   - 未知错误码 → error_repair_llm（LLM智能分析）
# ============================================================

import os
import re
import json
import time
import logging
from utils.http_session import session
from utils.title_sanitizer import sanitize_title
from typing import Dict, List, Any, Optional
from jinja2 import Template
from pydantic import BaseModel, Field
from langgraph.graph import StateGraph, END


# ============================================================
# 日志配置
# ============================================================
logger = logging.getLogger("validation_retry_loop")


# ============================================================
# 子图状态定义
# ============================================================
class ValidationRetryLoopState(BaseModel):
    """验证循环修复子图全局状态"""
    # 输入数据
    ozon_payload: Dict[str, Any] = Field(default_factory=dict, description="Ozon上传payload")
    validation_errors: list = Field(default_factory=list, description="验证错误列表")
    errors: list = Field(default_factory=list, description="Ozon官方错误数组")
    error_message: str = Field(default="", description="错误信息")

    draft: Dict[str, Any] = Field(default_factory=dict, description="产品数据")
    token: str = Field(default="", description="mxou API Token")
    ozon_client_id: str = Field(default="", description="Ozon Client ID")
    ozon_api_key: str = Field(default="", description="Ozon API Key")
    description_category_id: str = Field(default="", description="Ozon类目ID")
    type_id: str = Field(default="", description="Ozon类型ID")
    task_id: str = Field(default="", description="任务ID")

    purchase_url: str = Field(default="", description="采购链接")
    purchase_cost: str = Field(default="", description="采购成本")
    sku_id: str = Field(default="", description="SKU ID")
    profit_estimation: Dict[str, Any] = Field(default_factory=dict, description="利润预估")
    final_attributes: list = Field(default_factory=list, description="最终属性")
    attributes_schema: list = Field(default_factory=list, description="属性Schema")
    dictionary_values: Dict[str, List[Dict[str, Any]]] = Field(default_factory=dict, description="字典值数据")
    learned_attributes: Dict[str, Any] = Field(default_factory=dict, description="已学习的属性映射")
    pricing_info: Dict[str, Any] = Field(default_factory=dict, description="价格信息")

    # 循环状态
    retry_count: int = Field(default=0, description="当前重试次数")
    max_retries: int = Field(default=3, description="最大重试次数")

    # 解析结果
    error_code: str = Field(default="", description="当前错误代码")
    attribute_id: int = Field(default=0, description="当前错误属性ID")
    error_type: str = Field(default="", description="错误类型分类")
    repair_node: str = Field(default="", description="选中的修复节点名")

    # 修复结果
    is_valid: bool = Field(default=False, description="验证结果")
    upload_status: str = Field(default="", description="上传状态")
    product_id: str = Field(default="", description="商品ID")
    status: str = Field(default="", description="Ozon处理状态")
    
    # 类目重匹配标志（DESCRIPTION_DECLINE + attr 8229 时设置）
    needs_recategorization: bool = Field(default=False, description="是否需要重新匹配类目")
    
    # 产品名（error_repair_llm_node 用产品名搜索字典值）
    product_name: str = Field(default="", description="产品名称（用于字典值搜索）")
    
    # 修复元数据（classify_error_node 传递给 repair 节点的指令）
    repair_metadata: Dict[str, Any] = Field(default_factory=dict, description="修复元数据（如 remove_attrs）")


class ValidationRetryLoopInput(BaseModel):
    """验证循环修复子图输入"""
    ozon_payload: Dict[str, Any] = Field(..., description="初始Ozon payload")
    validation_errors: list = Field(default=[], description="初始验证错误")
    errors: list = Field(default=[], description="Ozon官方错误数组")
    error_message: str = Field(default="", description="初始错误信息")

    draft: Dict[str, Any] = Field(..., description="产品数据")
    token: str = Field(..., description="API Token")
    ozon_client_id: str = Field(..., description="Ozon Client ID")
    ozon_api_key: str = Field(..., description="Ozon API Key")
    description_category_id: str = Field(..., description="Ozon类目ID")
    type_id: str = Field(default="", description="Ozon类型ID")
    task_id: str = Field(default="", description="任务ID")
    product_id: str = Field(default="", description="商品ID（ozon_status 已分配）")  # ← 关键！用于靶向修复

    purchase_url: str = Field(default="", description="采购链接")
    purchase_cost: str = Field(default="", description="采购成本")
    sku_id: str = Field(default="", description="SKU ID")
    profit_estimation: Dict[str, Any] = Field(default_factory=dict, description="利润预估")
    final_attributes: list = Field(default_factory=list, description="最终属性")
    attributes_schema: list = Field(default_factory=list, description="属性Schema")
    dictionary_values: Dict[str, List[Dict[str, Any]]] = Field(default_factory=dict, description="字典值数据")
    learned_attributes: Dict[str, Any] = Field(default_factory=dict, description="已学习的属性映射")
    pricing_info: Dict[str, Any] = Field(default_factory=dict, description="价格信息")


class ValidationRetryLoopOutput(BaseModel):
    """验证循环修复子图输出"""
    ozon_payload: Dict[str, Any] = Field(default_factory=dict, description="修复后的Ozon payload")
    validation_errors: list = Field(default_factory=list, description="最终验证错误")
    is_valid: bool = Field(default=False, description="最终验证结果")
    retry_count: int = Field(default=0, description="实际重试次数")
    error_type: str = Field(default="", description="最终错误类型")
    error_message: str = Field(default="", description="最终错误信息")
    product_id: Optional[str] = Field(default=None, description="商品ID")
    upload_status: str = Field(default="", description="上传状态")


# ============================================================
# 错误修复策略映射表（基于Ozon真实错误数据）
# ============================================================
REPAIR_STRATEGY: Dict[str, str] = {
    "warning_attribute_values_out_of_range": "error_repair_llm",
    "INVALID_ATTRIBUTE_VALUE": "error_repair_llm",
    "BR_hashtag_validation": "error_repair_llm",
    "BR_hashtag_brand": "error_repair_llm",
    "BR_CRITICAL_OIL_BRAND": "error_repair_llm",  # 品牌不在该类目字典中，移除强制品牌
    "DESCRIPTION_DECLINE": "error_repair_llm",
    "MISSING_ATTRIBUTE": "error_repair_llm",
    "MISSING_REQUIRED_ATTRIBUTE": "error_repair_llm",
    "INVALID_CATEGORY": "error_repair_llm",
    "INVALID_PRICE": "repair_pricing",
    "WEIGHT_DIMENSION_ERROR": "repair_prepare",
    "INVALID_DIMENSION": "repair_prepare",
    # ✅ 变体未合并 → 走 repair_prepare 重新构建payload
    "VARIANT_NOT_MERGED": "repair_prepare",
    # ✅ 9048冲突 → 走 repair_prepare 追加后缀重试
    "double_without_merger_offer": "repair_prepare",
    # ✅ 中文字符在属性中 → 走 error_repair_llm 批量翻译
    "BR_chinese_hieroglyphs_in_attribute": "error_repair_llm",
    # ✅ 体积重量ML判断错误 → 走 repair_dimensions 重新计算
    "ML_INCORRECT_VOLUME_WEIGHT": "repair_dimensions",
    # ✅ 尺寸错误 → 走 repair_prepare（修复weight/dimensions）
    "INCORRECT_DIMENSION": "repair_prepare",
    # ✅ 产地错误 → 走 error_repair_llm 修复
    "BR_warning_wrong_country": "error_repair_llm",
    # ✅ 必填属性值为空 → 走 error_repair_llm 补全字典值
    "error_attribute_values_empty": "error_repair_llm",
    # ✅ 图片相关 → WARNING级别，不触发retry（标记unfixable）
    "pics_http_error": "unfixable",
    "pics_cant_decode": "unfixable",
    "primary_image_load_failed": "unfixable",
    "some_image_failed": "unfixable",
    "warning_all_image_failed": "unfixable",
    # ✅ 火险品/管制品 → 不可修复（需要认证文件）
    "BR_hazard_class1": "unfixable",
    "FB_fire_hazardous_goods": "unfixable",
    "FB_LIGHTER": "unfixable",
    # ✅ 密度错误 → 走 repair_prepare 修复尺寸
    "INCORRECT_DENSITY": "repair_prepare",
}


# ============================================================
# 靶向修复类型分类（reupload_node 路由器用）
# 当 product_id 存在时，根据错误类型选择最优的 Ozon API 端点
# ============================================================

# 属性类错误 → POST /v1/product/attributes/update（增量，无需重新审核）
FIX_TYPE_ATTRIBUTES: set = {
    "error_attribute_values_empty", "BR_chinese_hieroglyphs_in_attribute",
    "BR_warning_wrong_country", "warning_attribute_values_out_of_range",
    "MISSING_ATTRIBUTE", "MISSING_REQUIRED_ATTRIBUTE", "INVALID_ATTRIBUTE_VALUE",
    "BR_hashtag_validation", "BR_hashtag_brand", "BR_CRITICAL_OIL_BRAND",
    "marking_auto_corrected",
}

# 价格类错误 → POST /v1/product/prices/update（增量，无需重新审核）
FIX_TYPE_PRICES: set = {
    "INVALID_PRICE", "discount_for_low_price",
}

# 类目/尺寸/描述/图片错误 → POST /v3/product/import（UPDATE 模式，需 product_id）
# 这些错误需要更新产品的扁平字段（category_id, dimensions, description, images）
FIX_TYPE_PRODUCT_IMPORT: set = {
    "INVALID_CATEGORY", "DESCRIPTION_DECLINE",
    "INCORRECT_DIMENSION", "INCORRECT_DENSITY",
    "ML_INCORRECT_VOLUME_WEIGHT", "WEIGHT_DIMENSION_ERROR",
    "VARIANT_NOT_MERGED", "double_without_merger_offer",
}

# 不可修复错误 → 标记 warning，直接 success（不浪费重试次数）
FIX_TYPE_UNFIXABLE: set = {
    "pics_http_error", "pics_cant_decode", "primary_image_load_failed",
    "some_image_failed", "warning_all_image_failed",
    "BR_hazard_class1", "FB_fire_hazardous_goods", "FB_LIGHTER",
    "PRODUCT_ALREADY_EXISTS",
}


def classify_fix_type(error_code: str) -> str:
    """根据 error_code 返回靶向修复类型：attributes / prices / product_import / unfixable"""
    if error_code in FIX_TYPE_ATTRIBUTES:
        return "attributes"
    if error_code in FIX_TYPE_PRICES:
        return "prices"
    if error_code in FIX_TYPE_PRODUCT_IMPORT:
        return "product_import"
    if error_code in FIX_TYPE_UNFIXABLE:
        return "unfixable"
    # 未知错误：保守回退到全量 product_import（至少有 product_id 时为 UPDATE 模式）
    return "product_import"


# ============================================================
# 辅助函数
# ============================================================

# ── 标题净化（v4: 提取到 utils/title_sanitizer.py，共享于 prepare + retry）──
# sanitize_title() 已从 utils.title_sanitizer 导入


def _call_ozon_api(ozon_client_id: str, ozon_api_key: str, endpoint: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """调用Ozon API的通用方法"""
    url: str = f"https://api-seller.ozon.ru{endpoint}"
    headers: Dict[str, str] = {
        "Client-Id": ozon_client_id,
        "Api-Key": ozon_api_key,
        "Content-Type": "application/json"
    }
    try:
        response = session.post(url, headers=headers, json=payload, timeout=30)
        if response.status_code == 200:
            return response.json()
        logger.warning(f"Ozon API {endpoint} 返回 {response.status_code}: {response.text[:200]}")
        return {}
    except Exception as e:
        logger.warning(f"Ozon API {endpoint} 异常: {e}")
        return {}


def _search_dictionary_values(ozon_client_id: str, ozon_api_key: str,
                               attribute_id: int, category_id: str, type_id: str,
                               search_value: str, language: str) -> List[Dict[str, Any]]:
    """
    调用Ozon API按关键词搜索字典值
    先用中文(ZH_HANS)搜索，搜不到换英文(EN)
    """
    result: Dict[str, Any] = _call_ozon_api(
        ozon_client_id, ozon_api_key,
        "/v1/description-category/attribute/values/search",
        {
            "attribute_id": attribute_id,
            "description_category_id": int(category_id) if category_id else 0,
            "type_id": int(type_id) if type_id else 0,
            "value": search_value,
            "language": language,
            "limit": 50
        }
    )
    values: list = result.get("result", [])
    return values if values else []


def _find_alternative_type_id(ozon_client_id: str, ozon_api_key: str,
                               description_category_id: int, current_type_id: int) -> int:
    """查找同一类目下的替代 type_id（排除当前失败的 type_id）。

    调用 Ozon API /v1/description-category/tree 获取该 category 下的所有 type。
    """
    try:
        result = _call_ozon_api(
            ozon_client_id, ozon_api_key,
            "/v1/description-category/tree",
            {"description_category_id": description_category_id, "language": "RU"}
        )
        children = result.get("result", [])
        if isinstance(children, list):
            for child in children:
                if isinstance(child, dict):
                    tid = child.get("type_id") or child.get("id")
                    if tid and int(tid) != current_type_id:
                        return int(tid)
    except Exception as e:
        logger.debug(f"查找替代type_id失败: {e}")
    return 0


def _get_attribute_schema(ozon_client_id: str, ozon_api_key: str,
                           category_id: str, type_id: str, language: str) -> List[Dict[str, Any]]:
    """查询类目属性schema"""
    result: Dict[str, Any] = _call_ozon_api(
        ozon_client_id, ozon_api_key,
        "/v1/description-category/attribute",
        {
            "description_category_id": int(category_id) if category_id else 0,
            "type_id": int(type_id) if type_id else 0,
            "language": language
        }
    )
    return result.get("result", [])


def _call_mxou_llm(token: str, config_path: str, context_vars: Dict[str, Any]) -> str:
    """
    调用mxou LLM API — 委托给统一的 call_mxou_chat_api（含 thinking 禁用 + reasoning_content 回退）
    """
    from utils.mxou_api import call_mxou_chat_api

    cfg_file: str = os.path.join(os.getenv("APP_WORKSPACE_PATH", ""), config_path)
    with open(cfg_file, "r", encoding="utf-8") as fd:
        cfg: Dict[str, Any] = json.load(fd)

    llm_config: Dict[str, Any] = cfg.get("config", {})
    sp: str = cfg.get("sp", "")
    up: str = cfg.get("up", "")

    up_tpl: Template = Template(up)
    user_prompt: str = up_tpl.render(context_vars)

    model = llm_config.get("model", "deepseek-v4-flash")
    temperature = llm_config.get("temperature", 0.3)
    max_tokens = llm_config.get("max_completion_tokens", 4096)

    result = call_mxou_chat_api(
        token=token,
        system_prompt=sp,
        user_prompt=user_prompt,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    if result:
        logger.info(f"mxou LLM返回内容长度: {len(result)}")
    return result or ""


# ============================================================
# 子图节点函数
# ============================================================
def parse_error_node(state: ValidationRetryLoopState) -> ValidationRetryLoopState:
    """解析错误节点：从Ozon官方错误结构中提取错误代码和属性ID"""
    logger.info("📋 开始解析Ozon错误...")

    errors: list = state.errors or []
    if not errors:
        logger.warning("⚠️ 无errors数据，尝试从error_message提取")
        state.error_code = "UNKNOWN"
        state.attribute_id = 0
        state.error_type = "unknown"
        state.repair_node = "error_repair_llm"
        state.retry_count += 1
        return state

    # ✅ v0.11: 批量处理 — 按 fix_type 分组，同类型错误一次修完
    # 例：3个属性错误 → 一次 attributes/update 调用全修
    from collections import defaultdict
    grouped: dict = defaultdict(list)
    for err in errors:
        if isinstance(err, dict):
            ft = classify_fix_type(err.get("code", "UNKNOWN"))
            grouped[ft].append(err)

    # 取数量最多的类型优先处理
    fix_type = max(grouped, key=lambda k: len(grouped[k]))
    batch = grouped[fix_type]
    
    first_error = batch[0]
    error_code: str = first_error.get("code", "UNKNOWN")
    attribute_id: Any = first_error.get("attribute_id", 0)
    texts: Dict[str, Any] = first_error.get("texts", {})
    error_message: str = texts.get("message", "") if isinstance(texts, dict) else ""

    try:
        attr_id: int = int(attribute_id) if attribute_id else 0
    except (ValueError, TypeError):
        attr_id = 0

    # 移除已处理的同类型错误，保留其他类型的
    batch_codes = {e.get("code") for e in batch if isinstance(e, dict)}
    state.errors = [e for e in errors if isinstance(e, dict) and e.get("code") not in batch_codes]
    logger.info(f"📋 批量处理: {len(batch)}个 '{fix_type}' 错误，剩余{len(state.errors)}个其他类型")

    state.error_code = error_code
    state.attribute_id = attr_id
    state.error_message = error_message

    logger.info(f"📋 错误解析结果: code={error_code}, attr_id={attr_id}, msg={error_message[:100]}")

    state.retry_count += 1
    return state


def classify_error_node(state: ValidationRetryLoopState) -> ValidationRetryLoopState:
    """分类错误节点：判断错误是否可修复，选择修复策略"""
    logger.info(f"🔍 分类错误: code={state.error_code}, attr_id={state.attribute_id}")

    error_code: str = state.error_code
    attr_id: int = state.attribute_id

    # 判断是否可修复
    unfixable_codes: set = {"PRODUCT_ALREADY_EXISTS"}
    if error_code in unfixable_codes:
        state.error_type = "unfixable"
        state.repair_node = "final_result"
        logger.warning(f"❌ 错误不可修复: {error_code}")
        return state

    # ✅ 图片错误（attr=4195 附加图片, attr=4194 主图）无法通过属性修复解决
    # 标记为 warning 而非阻塞错误，让产品继续上架
    if error_code == "DESCRIPTION_DECLINE" and attr_id in (4194, 4195):
        logger.warning(
            f"⚠️ 图片问题(attr={attr_id})，无法通过retry修复，标记为warning继续上架"
        )
        state.error_type = "unfixable"
        state.repair_node = "final_result"
        # ✅ 图片问题不阻断产品上架，标记为有效（带warning）
        state.is_valid = True
        state.upload_status = "success_with_warning"
        return state

    # ✅ 品牌错误：该类目不接受"Нет бренда"，移除强制品牌属性
    if error_code == "BR_CRITICAL_OIL_BRAND":
        logger.warning("⚠️ 品牌错误(BR_CRITICAL_OIL_BRAND)，移除强制品牌属性 85/5076")
        state.error_type = "retry"
        state.repair_node = "error_repair_llm"
        state.repair_metadata = {"remove_attrs": [85, 5076]}
        return state

    # 查修复策略表
    repair_node: str = REPAIR_STRATEGY.get(error_code, "error_repair_llm")
    
    # ✅ 不可修复的错误 → 不浪费retry循环
    if repair_node == "unfixable":
        logger.warning(f"❌ 错误不可修复（图片/管制品）: {error_code}")
        state.error_type = "unfixable"
        state.repair_node = "final_result"
        state.is_valid = True  # 不阻断产品
        return state
    
    state.repair_node = repair_node
    state.error_type = "fixable"

    logger.info(f"✅ 错误可修复，选择修复节点: {repair_node}")
    return state


def repair_node_selector(state: ValidationRetryLoopState) -> str:
    """条件分支：根据repair_node选择修复节点"""
    if state.error_type == "unfixable":
        return "final_result"

    repair_node: str = state.repair_node
    if repair_node in ("error_repair_llm", "repair_pricing", "repair_prepare", "repair_dimensions"):
        return repair_node

    # 默认走LLM修复
    return "error_repair_llm"


def error_repair_llm_node(state: ValidationRetryLoopState) -> ValidationRetryLoopState:
    """
    错误修复LLM节点：先查Ozon API搜索字典值，搜不到再调mxou LLM

    处理场景：
    - warning_attribute_values_out_of_range: API搜索字典值(ZH_HANS→EN) → LLM兜底
    - BR_hashtag_validation: LLM生成合规标签
    - DESCRIPTION_DECLINE: LLM重写描述 / 8229类型不匹配 → 触发类目重匹配
    - MISSING_ATTRIBUTE: LLM生成缺失属性
    - INVALID_CATEGORY: 查类目树 + LLM匹配
    - 未知错误码: LLM智能分析
    """
    logger.info(f"🔧 开始LLM修复: code={state.error_code}, attr_id={state.attribute_id}")

    # ✅ 品牌错误快速修复：直接从 payload 移除品牌属性（不调 LLM）
    remove_attrs = (state.repair_metadata or {}).get("remove_attrs", [])
    if remove_attrs:
        items = state.ozon_payload.get("items", [])
        for item in items:
            attrs = item.get("attributes", [])
            item["attributes"] = [a for a in attrs if a.get("id") not in remove_attrs]
        logger.info(f"✅ 已移除属性 {remove_attrs}，剩余属性: {sum(len(i.get('attributes',[])) for i in items)}")
        state.error_type = "retry"
        state.repair_node = "revalidate"
        return state

    error_code: str = state.error_code
    attr_id: int = state.attribute_id

    # ✅ v0.11: BR_hashtag_brand 专用修复 — 从产品名重新生成合规 hashtag
    if error_code == "BR_hashtag_brand":
        logger.info("🔧 BR_hashtag_brand: 重新生成不含品牌名的 hashtag")
        items = state.ozon_payload.get("items", [])
        for item in items:
            product_name = item.get("name", "") or state.product_name or ""
            from graphs.nodes.assemble_ozon_product_node import _generate_hashtags as _gen_tags
            new_tags = _gen_tags(product_name)
            attrs = item.get("attributes", [])
            for a in attrs:
                if a.get("id") == 23171:
                    a["value"] = new_tags
                    a["dictionary_value_id"] = 0
                    logger.info(f"   ✅ hashtag #23171 已修正: {new_tags}")
                    break
            else:
                # 23171 不存在，补充
                attrs.append({"id": 23171, "value": new_tags, "dictionary_value_id": 0})
                logger.info(f"   ✅ hashtag #23171 已补充: {new_tags}")
            item["attributes"] = attrs
        state.error_type = "retry"
        state.repair_node = "revalidate"
        return state

    token: str = state.token
    ozon_client_id: str = state.ozon_client_id
    ozon_api_key: str = state.ozon_api_key
    category_id: str = state.description_category_id
    type_id: str = state.type_id

    # ========== 特殊处理：DESCRIPTION_DECLINE + attr 8229（类型不匹配） ==========
    # 使用 pg_trgm 搜索 category_tree_nodes 找到最匹配的 type_id，
    # 而非盲选同一 category 下的替代 type（原 _find_alternative_type_id 太粗糙）
    if error_code == "DESCRIPTION_DECLINE" and attr_id == 8229:
        logger.warning(
            "⚠️ 检测到 DESCRIPTION_DECLINE + attr 8229（类型不匹配）。"
            f"当前类目: category_id={category_id}, type_id={type_id}。"
        )

        # 从 payload 中提取产品名（俄语）用于 pg_trgm 搜索
        product_name = ""
        items = state.ozon_payload.get("items", [])
        if items and isinstance(items[0], dict):
            product_name = items[0].get("name", "")
        if not product_name:
            product_name = state.product_name or ""

        best_type_id = None
        best_category_id = None
        best_type_name = ""

        # ✅ P2 修复：Ozon API 优先（权威、实时），pg_trgm 降级为 fallback
        # 原因：PG 缓存可能过时，自修复需要最新数据
        logger.info("🔍 优先使用 Ozon API 查找替代 type_id（实时数据）")
        try:
            alt_type_id = _find_alternative_type_id(
                ozon_client_id, ozon_api_key,
                int(category_id) if category_id else 0,
                int(type_id) if type_id else 0
            )
            if alt_type_id and str(alt_type_id) != str(type_id):
                logger.info(f"✅ Ozon API 替代 type_id: {type_id} → {alt_type_id}")
                state.type_id = str(alt_type_id)
                for item in state.ozon_payload.get("items", []):
                    if isinstance(item, dict):
                        item["type_id"] = int(alt_type_id)
                return state
        except Exception as _e:
            logger.warning(f"⚠️ Ozon API 查找替代 type_id 失败: {_e}")

        # Ozon API 无结果 → pg_trgm 降级（使用缓存数据）
        if product_name:
            try:
                from utils.ozon_category_query import get_category_query
                query = get_category_query()
                candidates = query.search_nodes(
                    product_name[:100], top_k=5, node_type="type", language="RU"
                )
                if candidates:
                    best = candidates[0]
                    best_type_id = best.get("type_id")
                    best_category_id = best.get("description_category_id")
                    best_type_name = best.get("node_name", "")
                    logger.info(
                        f"🔍 pg_trgm 降级搜索: '{product_name[:60]}' → "
                        f"type_id={best_type_id}, type_name='{best_type_name}'"
                    )

                    # 关键词重叠验证
                    _prod_words = set(product_name.lower().replace(',', ' ').split())
                    _type_words = set(best_type_name.lower().replace(',', ' ').split())
                    _overlap = _prod_words & _type_words
                    if not _overlap and len(candidates) > 1:
                        for cand in candidates[1:]:
                            _tw = set(cand.get("node_name", "").lower().replace(',', ' ').split())
                            if _prod_words & _tw:
                                best = cand
                                best_type_id = best.get("type_id")
                                best_category_id = best.get("description_category_id")
                                best_type_name = best.get("node_name", "")
                                break

                    if best_type_id and str(best_type_id) != str(type_id):
                        logger.info(f"🔧 pg_trgm type 修复: {type_id} → {best_type_id}")
                        state.type_id = str(best_type_id)
                        for item in state.ozon_payload.get("items", []):
                            if isinstance(item, dict):
                                item["type_id"] = int(best_type_id)
                        if best_category_id and str(best_category_id) != str(category_id):
                            state.description_category_id = str(best_category_id)
                            for item in state.ozon_payload.get("items", []):
                                if isinstance(item, dict):
                                    item["description_category_id"] = int(best_category_id)
                        return state
            except Exception as _pg_e:
                logger.warning(f"⚠️ pg_trgm 降级搜索失败: {_pg_e}")

        # 完全找不到替代 → 标记
        state.needs_recategorization = True
        logger.info("🔧 已标记 needs_recategorization=True（找不到合适的替代 type）")
        return state

    # ========== 特殊处理：BR_chinese_hieroglyphs_in_attribute（中文字符） ==========
    # Ozon 拒绝产品因为属性值含中文/日文字符。需要批量扫描并翻译所有含中文的属性。
    if error_code == "BR_chinese_hieroglyphs_in_attribute":
        logger.warning("⚠️ 检测到 BR_chinese_hieroglyphs_in_attribute，批量翻译含中文的属性值")
        _chinese_re = re.compile(r'[\u4e00-\u9fff]')
        _cyrillic_re = re.compile(r'[а-яА-ЯёЁ]')
        _english_allowed = {9024}  # SKU编码允许英文

        # 使用 translate_russian_cfg.json 配置调用 LLM
        cfg_path = os.path.join(
            os.getenv("APP_WORKSPACE_PATH", "/app"),
            "config/translate_russian_cfg.json"
        )
        try:
            with open(cfg_path, "r", encoding="utf-8") as _f:
                _t_cfg = json.load(_f)
            _t_sp = _t_cfg.get("sp", "将给定文本翻译为俄语，只返回俄语翻译。")
            _t_model = _t_cfg.get("config", {}).get("model", "deepseek-v4-flash")
        except Exception:
            _t_sp = "将给定文本翻译为俄语，只返回俄语翻译。"
            _t_model = "deepseek-v4-flash"

        from utils.mxou_api import call_mxou_chat_api

        translated_count = 0
        product_name = state.product_name or ""
        for attr in state.final_attributes:
            if not isinstance(attr, dict):
                continue
            attr_id_val = attr.get("id") or attr.get("attribute_id")
            if attr_id_val is None:
                continue
            try:
                aid = int(attr_id_val)
            except (ValueError, TypeError):
                continue
            if aid in _english_allowed:
                continue

            val = str(attr.get("value", ""))
            dict_val_id = attr.get("dictionary_value_id", 0)

            # ✅ 处理空值字典属性：用产品名搜索字典值
            if not val and dict_val_id == 0 and product_name:
                logger.info(f"  属性{aid}值为空，尝试用产品名搜索: {product_name[:30]}")
                try:
                    search_result = _search_dictionary_values(
                        ozon_client_id, ozon_api_key, aid,
                        category_id, type_id, product_name[:30], "RU"
                    )
                    if search_result:
                        best = search_result[0]
                        attr["value"] = best.get("value", "")
                        attr["dictionary_value_id"] = best.get("id", 0)
                        translated_count += 1
                        logger.info(f"  ✅ 属性{aid}搜索到值: {attr['value'][:30]}")
                        continue
                except Exception as _search_e:
                    logger.debug(f"  属性{aid}搜索失败: {_search_e}")

            if val and _chinese_re.search(val):
                logger.info(f"  翻译属性{aid}: {val[:60]}...")
                translated = call_mxou_chat_api(
                    token=token,
                    system_prompt=_t_sp,
                    user_prompt=f"翻译为俄语：{val}",
                    model=_t_model,
                    temperature=0.0,
                    max_tokens=200,
                ) or ""
                translated = translated.strip()
                if translated and _cyrillic_re.search(translated):
                    attr["value"] = translated
                    translated_count += 1
                    logger.info(f"  ✅ 属性{aid}翻译成功: {translated[:60]}")
                else:
                    # 翻译失败，清空值避免Ozon拒绝
                    attr["value"] = ""
                    logger.warning(f"  ⚠️ 属性{aid}翻译失败，已清空")

        # 同步到 ozon_payload
        items = state.ozon_payload.get("items", [])
        for item in items:
            if not isinstance(item, dict):
                continue
            item["attributes"] = state.final_attributes

        logger.info(f"✅ 中文字符批量翻译完成: {translated_count}个属性已翻译")
        return state

    # ========== Step 1: 查属性schema（中文） ==========
    attr_schema: List[Dict[str, Any]] = _get_attribute_schema(
        ozon_client_id, ozon_api_key, category_id, type_id, "ZH_HANS"
    )

    # 找到当前出错的属性定义
    current_attr_def: Dict[str, Any] = {}
    for schema_attr in attr_schema:
        if not isinstance(schema_attr, dict):
            continue
        if schema_attr.get("id") == attr_id:
            current_attr_def = schema_attr
            break

    attr_name: str = current_attr_def.get("name", f"attr_{attr_id}")
    dictionary_id: int = current_attr_def.get("dictionary_id", 0)
    logger.info(f"📋 属性定义: name={attr_name}, dictionary_id={dictionary_id}")

    # 从final_attributes中获取当前错误值（确保current_value始终初始化）
    current_value: str = ""
    for attr in state.final_attributes:
        if not isinstance(attr, dict):
            continue
        if attr.get("id") == attr_id or attr.get("attribute_id") == attr_id:
            current_value = str(attr.get("value", ""))
            break

    # ========== Step 2: 如果是字典属性，用values/search搜索 ==========
    if attr_id > 0 and dictionary_id > 0:

        # ✅ 字典值按需刷新：warning_attribute_values_out_of_range 强制刷新缓存
        if error_code == "warning_attribute_values_out_of_range":
            logger.info(f"🔄 warning_attribute_values_out_of_range: 强制刷新属性{attr_id}的字典值缓存")
            try:
                _fresh_result = _call_ozon_api(
                    ozon_client_id, ozon_api_key,
                    "/v1/description-category/attribute/values",
                    {
                        "attribute_id": attr_id,
                        "description_category_id": int(category_id) if category_id else 0,
                        "type_id": int(type_id) if type_id else 0,
                        "language": "RU",
                        "limit": 5000,
                        "last_value_id": 0,
                    }
                )
                _fresh_values = _fresh_result.get("result", [])
                if _fresh_values:
                    # 写入 PG 缓存
                    try:
                        from utils.local_db_manager import LocalDBManager
                        local_db = LocalDBManager()
                        local_db.set_dictionary_value_cache(
                            attribute_id=attr_id,
                            description_category_id=int(category_id) if category_id else 0,
                            type_id=int(type_id) if type_id else 0,
                            values_data=_fresh_values,
                            language="RU",  # fetch 用 RU → cache 用 RU
                            expires_in=86400,
                        )
                        logger.info(f"  ✅ 字典值缓存已刷新: attr={attr_id}, {len(_fresh_values)}条")
                    except Exception as _cache_e:
                        logger.debug(f"  字典缓存写入跳过: {_cache_e}")
            except Exception as _fresh_e:
                logger.warning(f"  ⚠️ 字典值刷新失败: {_fresh_e}")

        # 确定搜索关键词：优先用当前值，其次用产品名或属性名
        search_terms: List[str] = []
        if current_value:
            search_terms.append(current_value)
        else:
            # current_value 为空（error_attribute_values_empty）→ 用产品名或属性名搜索
            product_name = state.product_name or ""
            if product_name:
                search_terms.append(product_name[:50])
            if attr_name:
                search_terms.append(attr_name[:30])

        for search_term in search_terms:
            if not search_term:
                continue
            # 先用中文搜索
            search_result: List[Dict[str, Any]] = _search_dictionary_values(
                ozon_client_id, ozon_api_key, attr_id, category_id, type_id,
                search_term, "ZH_HANS"
            )

            # 中文搜不到 → 换英文
            if not search_result:
                logger.info(f"中文搜索无结果，换英文搜索: {search_term}")
                search_result = _search_dictionary_values(
                    ozon_client_id, ozon_api_key, attr_id, category_id, type_id,
                    search_term, "EN"
                )

            # API搜索命中 → 直接修复，不需要LLM
            if search_result and len(search_result) > 0:
                best_match: Dict[str, Any] = search_result[0]
                dict_value_id: int = best_match.get("id", 0)
                dict_value_text: str = best_match.get("value", "")
                logger.info(f"✅ API搜索命中: value={dict_value_text}, id={dict_value_id} (search_term={search_term})")

                # 更新final_attributes
                updated_attrs: list = []
                for attr in state.final_attributes:
                    if not isinstance(attr, dict):
                        updated_attrs.append(attr)
                        continue
                    if attr.get("id") == attr_id or attr.get("attribute_id") == attr_id:
                        attr["value"] = dict_value_text
                        attr["dictionary_value_id"] = dict_value_id
                        logger.info(f"✅ 属性{attr_id}已修复为: {dict_value_text}")
                    updated_attrs.append(attr)
                state.final_attributes = updated_attrs
                return state

        logger.warning(f"⚠️ API搜索未命中（所有关键词），转LLM兜底修复")

    # ========== Step 2.5: 自由文本/字典属性 → 已知默认值兜底 ==========
    # 对齐 assemble_ozon_product_node._validate_and_enrich_items 的 KNOWN_DEFAULTS
    # 和 prepare_ozon_upload_node 的 _FALLBACK_FREE_TEXT_ATTRS
    # 避免对常见空属性浪费 LLM 调用
    _KNOWN_DEFAULTS_RETRY: dict[int, str] = {
        # 必填属性默认值（来自 assemble_ozon_product_node）
        8205: "730",               # Срок годности в днях — 2年
        9163: "Универсальный",     # Пол — 通用
        8962: "1",                 # Количество предметов
        4958: "Универсальный",     # Назначение
        8292: "0",                 # Объединить на одной карточке — 不合并
        # 非必填但常报错的默认值（来自 prepare_ozon_upload_node + Ozon 实际）
        7578: "365",               # Срок годности (дни)
        10350: "40",               # Макс. температура хранения
        10351: "0",                # Мин. температура хранения
        8787: "сухое место",       # Условия хранения
        8050: "полимерные материалы",  # Материал
        9782: "не опасный",        # Класс опасности
        9048: "",                  # Название модели — 不设默认值，由 revalidate 用 offer_id 补
        23487: "",                 # Производитель — 不设默认值，用 supplier 填充
    }

    if attr_id > 0 and attr_id in _KNOWN_DEFAULTS_RETRY:
        default_val = _KNOWN_DEFAULTS_RETRY[attr_id]
        if default_val:  # 非空默认值
            logger.info(f"📋 使用已知默认值: attr={attr_id} name={attr_name} → '{default_val}'")
            updated_attrs = []
            found = False
            for attr in state.final_attributes:
                if not isinstance(attr, dict):
                    updated_attrs.append(attr)
                    continue
                if attr.get("id") == attr_id or attr.get("attribute_id") == attr_id:
                    attr["value"] = default_val
                    attr["dictionary_value_id"] = 0  # 默认值为自由文本格式
                    found = True
                    logger.info(f"✅ 属性{attr_id}已用默认值修复: '{default_val}'")
                updated_attrs.append(attr)
            if not found:
                # 属性不存在 → 添加新属性
                updated_attrs.append({
                    "attribute_id": attr_id, "id": attr_id,
                    "value": default_val, "dictionary_value_id": 0,
                    "source": "retry_default"
                })
                logger.info(f"✅ 已添加缺失属性{attr_id}，默认值: '{default_val}'")
            state.final_attributes = updated_attrs
            return state

    # ========== Step 3: API搜不到 → 调用mxou LLM ==========
    # 准备LLM上下文
    error_info: str = json.dumps({
        "error_code": error_code,
        "attribute_id": attr_id,
        "attribute_name": attr_name,
        "error_message": state.error_message,
    }, ensure_ascii=False, indent=2)

    # 获取所有字典值列表（如果有字典约束）
    all_dict_values: str = ""
    if dictionary_id > 0:
        dict_result: Dict[str, Any] = _call_ozon_api(
            ozon_client_id, ozon_api_key,
            "/v1/description-category/attribute/values",
            {
                "attribute_id": attr_id,
                "description_category_id": int(category_id) if category_id else 0,
                "type_id": int(type_id) if type_id else 0,
                "language": "ZH_HANS",
                "limit": 50,
                "last_value_id": 0
            }
        )
        all_values_list: list = dict_result.get("result", [])
        if all_values_list:
            all_dict_values = json.dumps(
                [{"id": v.get("id", 0), "value": v.get("value", "")} for v in all_values_list[:30]],
                ensure_ascii=False
            )

    # 产品信息（优先从 draft 取，回退到 ozon_payload）
    product_name: str = ""
    product_desc: str = ""
    if state.draft:
        product_name = state.draft.get("name", "") or state.draft.get("title", "")
        product_desc = state.draft.get("description", "")
    if not product_name and hasattr(state, "ozon_payload") and state.ozon_payload:
        items = state.ozon_payload.get("items", [])
        if items and isinstance(items, list):
            product_name = items[0].get("name", "")
    if not product_name and hasattr(state, "final_attributes") and state.final_attributes:
        # 尝试从 final_attributes 中提取名称
        for attr in state.final_attributes:
            if attr.get("attribute_id") in (4180, 4191):  # attribute_id for name
                product_name = attr.get("value", "")
                break

    # 调用mxou LLM
    llm_response: str = _call_mxou_llm(
        token,
        "config/error_repair_llm_cfg.json",
        {
            "error_detail": json.dumps(error_info, ensure_ascii=False) if error_info else "{}",
            "attribute_name": attr_name,
            "current_value": current_value,
            "dictionary_values": json.dumps(all_dict_values, ensure_ascii=False) if all_dict_values else "无字典值约束",
            "product_name": product_name,
            "product_description": product_desc[:500] if product_desc else "",
            "error_code": error_code,
            "attribute_id": attr_id
        }
    )

    # 解析LLM返回
    if llm_response:
        try:
            # 尝试提取JSON
            json_match = re.search(r'\{.*\}', llm_response, re.DOTALL)
            if json_match:
                llm_result: Dict[str, Any] = json.loads(json_match.group())

                # ✅ 修复：LLM返回格式为 corrected_attributes 数组，需从中提取匹配attr_id的值
                corrected_attrs: list = llm_result.get("corrected_attributes", [])
                repaired_value: str = ""
                repaired_dict_id: int = 0
                if isinstance(corrected_attrs, list):
                    for ca in corrected_attrs:
                        if isinstance(ca, dict) and ca.get("attribute_id") == attr_id:
                            repaired_value = str(ca.get("value", ""))
                            try:
                                repaired_dict_id = int(ca.get("dictionary_value_id", 0))
                            except (ValueError, TypeError):
                                repaired_dict_id = 0
                            break

                # 兼容旧格式（直接返回repaired_value）
                if not repaired_value:
                    repaired_value = str(llm_result.get("repaired_value", ""))
                    try:
                        repaired_dict_id = int(llm_result.get("dictionary_value_id", 0))
                    except (ValueError, TypeError):
                        repaired_dict_id = 0

                repaired_desc: str = llm_result.get("corrected_description", "") or llm_result.get("repaired_description", "")
                repaired_tags: str = llm_result.get("corrected_tags", "") or llm_result.get("repaired_tags", "")
                repaired_title: str = llm_result.get("corrected_title", "") or llm_result.get("repaired_title", "")
                repair_explanation: str = llm_result.get("repair_explanation", "") or llm_result.get("explanation", "")

                # ✅ v0.8.0 标题修复增强：确保修复后的标题为俄语
                if repaired_title:
                    repaired_title = sanitize_title(repaired_title)
                    # 检查修复后的标题是否仍含拉丁字母或无西里尔
                    _latin_check = re.compile(r'[a-zA-Z]')
                    _cyrillic_check = re.compile(r'[а-яА-ЯёЁ]')
                    if not repaired_title or (_latin_check.search(repaired_title) and not _cyrillic_check.search(repaired_title)):
                        logger.warning(f"⚠️ LLM生成的标题仍含拉丁/无西里尔: '{repaired_title[:60]}'，强制翻译为俄语")
                        # 用 call_mxou_chat_api 强制生成俄语标题
                        try:
                            from utils.mxou_api import call_mxou_chat_api
                            _orig_name = (items[0].get("name", "") if (items := state.ozon_payload.get("items", [])) and len(items) > 0 else "") or state.product_name or ""
                            _rus_title = call_mxou_chat_api(
                                token=token,
                                system_prompt="你是Ozon俄罗斯电商平台产品命名专家。将以下产品标题翻译为俄语（西里尔字母）。要求：≤80字符，必须含西里尔字母，无拉丁/中文。只返回俄语标题。",
                                user_prompt=f"产品标题：{_orig_name or repaired_title}",
                                model="deepseek-v4-flash",
                                temperature=0.0,
                                max_tokens=200
                            ) or ""
                            _rus_title = _rus_title.strip()
                            if _rus_title and _cyrillic_check.search(_rus_title) and not _latin_check.search(_rus_title):
                                repaired_title = sanitize_title(_rus_title) or _rus_title
                                logger.info(f"✅ 强制俄语翻译成功: {repaired_title[:80]}")
                            else:
                                logger.warning(f"⚠️ 强制俄语翻译仍不合格: '{_rus_title[:60]}'")
                                repaired_title = ""
                        except Exception as _trans_e:
                            logger.warning(f"⚠️ 强制俄语翻译失败: {_trans_e}")
                            repaired_title = ""
                    
                    if repaired_title:
                        ozon_payload_title: Dict[str, Any] = state.ozon_payload
                        items_title: list = ozon_payload_title.get("items", [])
                    if items_title and len(items_title) > 0:
                        # 修复所有变体的name字段
                        for it in items_title:
                            if isinstance(it, dict):
                                it["name"] = repaired_title
                        logger.info(f"✅ 标题已修复（所有变体）：{repaired_title[:80]}")
                    # 同步修复final_attributes中的4180
                    updated_title_attrs: list = []
                    for attr in state.final_attributes:
                        if not isinstance(attr, dict):
                            updated_title_attrs.append(attr)
                            continue
                        aid_val: Any = attr.get("id") or attr.get("attribute_id")
                        if aid_val is not None:
                            try:
                                if int(aid_val) == 4180:
                                    attr["value"] = repaired_title
                                    logger.info(f"✅ 属性4180同步修复为：{repaired_title[:80]}")
                            except (ValueError, TypeError):
                                pass
                        updated_title_attrs.append(attr)
                    state.final_attributes = updated_title_attrs

                # 自动判断修复类型
                if repaired_desc:
                    repair_type: str = "description"
                elif repaired_tags:
                    repair_type = "tags"
                elif repaired_title:
                    repair_type = "title"
                elif repaired_value:
                    repair_type = "attribute"
                else:
                    repair_type = "attribute"

                # ✅ v0.8.0: LLM未生成标题但错误涉及名称缺失 → 强制生成俄语标题
                if not repaired_title and (
                    error_code == "UNKNOWN"
                    or (state.error_message and any(kw in str(state.error_message).lower() for kw in ["名称", "name", "название", "标题", "title"]))
                    or attr_id == 0
                ):
                    logger.warning(f"⚠️ LLM未生成标题但错误涉及名称缺失，强制生成俄语标题")
                    try:
                        from utils.mxou_api import call_mxou_chat_api
                        _items_force = state.ozon_payload.get("items", [])
                        _orig_title = (_items_force[0].get("name", "") if _items_force and len(_items_force) > 0 else "") or state.product_name or ""
                        _rus_title_force = call_mxou_chat_api(
                            token=token,
                            system_prompt=(
                                "你是Ozon俄罗斯电商平台产品命名专家。\n"
                                "根据以下产品信息生成俄语标题（西里尔字母）。\n"
                                "要求：≤80字符，必须含西里尔字母，无拉丁/中文。只返回标题。"
                            ),
                            user_prompt=f"产品信息：{_orig_title or product_name}",
                            model="deepseek-v4-flash",
                            temperature=0.0,
                            max_tokens=200
                        ) or ""
                        _rus_title_force = _rus_title_force.strip()
                        _latin_re2 = re.compile(r'[a-zA-Z]')
                        _cyrillic_re2 = re.compile(r'[а-яА-ЯёЁ]')
                        if _rus_title_force and _cyrillic_re2.search(_rus_title_force) and not _latin_re2.search(_rus_title_force):
                            repaired_title = sanitize_title(_rus_title_force) or _rus_title_force
                            repair_type = "title"
                            logger.info(f"✅ 强制俄语标题生成成功: {repaired_title[:80]}")
                        else:
                            logger.warning(f"⚠️ 强制俄语标题不合格: '{_rus_title_force[:60]}'")
                    except Exception as _force_e:
                        logger.warning(f"⚠️ 强制俄语标题生成失败: {_force_e}")
                
                # 应用强制生成的标题
                if repaired_title and repair_type == "title":
                    ozon_payload_t: Dict[str, Any] = state.ozon_payload
                    items_t: list = ozon_payload_t.get("items", [])
                    if items_t and len(items_t) > 0:
                        for it in items_t:
                            if isinstance(it, dict):
                                it["name"] = repaired_title
                        logger.info(f"✅ 标题已强制修复（所有变体）：{repaired_title[:80]}")
                    # 同步 final_attributes 中的 4180
                    for attr in state.final_attributes:
                        if isinstance(attr, dict) and int(attr.get("id") or attr.get("attribute_id") or 0) == 4180:
                            attr["value"] = repaired_title

                logger.info(f"✅ LLM修复结果: type={repair_type}, value={repaired_value[:50] if repaired_value else '(empty)'}")

                # 根据修复类型应用修复
                if repair_type == "description" and repaired_desc:
                    # 修复描述
                    ozon_payload: Dict[str, Any] = state.ozon_payload
                    items: list = ozon_payload.get("items", [])
                    if items and len(items) > 0:
                        items[0]["description"] = repaired_desc
                        logger.info(f"✅ 描述已修复（长度: {len(repaired_desc)}）")

                elif repair_type == "tags" and repaired_tags:
                    # 修复标签
                    updated_attrs2: list = []
                    for attr in state.final_attributes:
                        if not isinstance(attr, dict):
                            updated_attrs2.append(attr)
                            continue
                        if attr.get("id") == attr_id or attr.get("attribute_id") == attr_id:
                            attr["value"] = repaired_tags
                        updated_attrs2.append(attr)
                    state.final_attributes = updated_attrs2
                    logger.info(f"✅ 标签已修复: {repaired_tags[:50]}")

                elif repair_type == "attribute" and repaired_value:
                    # 修复属性值
                    updated_attrs3: list = []
                    found: bool = False
                    for attr in state.final_attributes:
                        if not isinstance(attr, dict):
                            updated_attrs3.append(attr)
                            continue
                        if attr.get("id") == attr_id or attr.get("attribute_id") == attr_id:
                            attr["value"] = repaired_value
                            if repaired_dict_id > 0:
                                attr["dictionary_value_id"] = repaired_dict_id
                            found = True
                        updated_attrs3.append(attr)

                    # 如果属性不存在（MISSING_ATTRIBUTE），添加新属性（内部格式）
                    if not found and attr_id > 0:
                        new_attr: Dict[str, Any] = {
                            "attribute_id": attr_id,
                            "value": repaired_value,
                            "dictionary_value_id": repaired_dict_id if repaired_dict_id else 0,
                            "source": "repair"
                        }
                        updated_attrs3.append(new_attr)
                        logger.info(f"✅ 已添加缺失属性: attr_id={attr_id}, value={repaired_value[:50]}")
                    state.final_attributes = updated_attrs3

                logger.info(f"修复说明: {repair_explanation}")
            else:
                logger.warning("⚠️ LLM返回中未找到JSON，无法解析")
        except json.JSONDecodeError as e:
            logger.error(f"❌ LLM返回JSON解析失败: {e}")
    else:
        logger.warning("⚠️ LLM返回为空")

    logger.info(f"✅ error_repair_llm修复完成，retry_count={state.retry_count}")
    return state


def repair_prepare_node(state: ValidationRetryLoopState) -> ValidationRetryLoopState:
    """修复尺寸重量节点：修复weight/depth/width/height等扁平字段，以及9048冲突"""
    logger.info("🔧 开始修复Ozon payload（尺寸重量/9048修复）")

    ozon_payload: Dict[str, Any] = state.ozon_payload
    items: list = ozon_payload.get("items", [])

    # ✅ double_without_merger_offer修复：给所有变体的9048追加版本后缀
    if state.error_code == "double_without_merger_offer":
        retry_suffix = f"_v{state.retry_count}"
        for item in items:
            for attr in item.get("attributes", []):
                if isinstance(attr, dict) and attr.get("id") == 9048:
                    old_val = attr["values"][0].get("value", "") if attr.get("values") else ""
                    if not old_val.endswith(retry_suffix):
                        new_val = (old_val + retry_suffix)[:50]
                        attr["values"] = [{"dictionary_value_id": 0, "value": new_val}]
                        logger.info(f"✅ 9048冲突修复: {old_val} → {new_val}")
        return state

    if items and len(items) > 0:
        first_item: Dict[str, Any] = items[0]

        # Ozon API使用扁平字段（weight/depth/width/height），不是dimensions对象
        weight_val: Any = first_item.get("weight", 0)
        depth_val: Any = first_item.get("depth", 0)
        width_val: Any = first_item.get("width", 0)
        height_val: Any = first_item.get("height", 0)

        try:
            weight: int = int(weight_val) if weight_val else 0
        except (ValueError, TypeError):
            weight = 0
        try:
            depth: int = int(depth_val) if depth_val else 0
        except (ValueError, TypeError):
            depth = 0
        try:
            width: int = int(width_val) if width_val else 0
        except (ValueError, TypeError):
            width = 0
        try:
            height: int = int(height_val) if height_val else 0
        except (ValueError, TypeError):
            height = 0

        # ✅ 自适应默认值：根据重量用密度0.8推算合理尺寸
        if weight <= 0:
            weight = 100  # 默认100克（多数小商品）
        if depth <= 0 or width <= 0 or height <= 0:
            # 用密度0.8 g/cm³ 从重量推算尺寸
            volume_cm3 = weight / 0.8
            side_cm = max(1.0, volume_cm3 ** (1.0 / 3.0))
            side_mm = max(20, min(int(side_cm * 10), 500))
            if depth <= 0:
                depth = side_mm
            if width <= 0:
                width = max(20, int(side_mm * 0.8))
            if height <= 0:
                height = max(15, int(side_mm * 0.6))

        # ✅ INCORRECT_DENSITY修复：如果密度极低(< 1.0)，说明尺寸被错误放大(cm→mm)，缩小10倍
        if weight > 0 and depth > 0 and width > 0 and height > 0:
            vol_m3 = (depth * width * height) / 1e9
            dens = (weight / 1000.0) / vol_m3 if vol_m3 > 0 else 0
            if dens < 1.0:
                old_d = depth; old_w = width; old_h = height
                depth = max(10, int(depth / 10))
                width = max(10, int(width / 10))
                height = max(10, int(height / 10))
                logger.warning(f"⚠️ 密度{dens:.2f}过低，尺寸缩小10倍: {old_d}x{old_w}x{old_h} → {depth}x{width}x{height}mm")

        first_item["weight"] = weight
        first_item["depth"] = depth
        first_item["width"] = width
        first_item["height"] = height
        logger.info(f"✅ 尺寸重量已修正（{weight}g, {depth}x{width}x{height}mm）")

    logger.info(f"✅ prepare修复完成，retry_count={state.retry_count}")
    return state


def repair_pricing_node(state: ValidationRetryLoopState) -> ValidationRetryLoopState:
    """修复价格节点：确保价格在合理范围内"""
    logger.info("🔧 开始修复价格（pricing修复）")

    ozon_payload: Dict[str, Any] = state.ozon_payload
    items: list = ozon_payload.get("items", [])

    if items and len(items) > 0:
        first_item: Dict[str, Any] = items[0]

        price_str: str = str(first_item.get("price", "0"))
        try:
            price: float = float(price_str) if price_str else 0
        except (ValueError, TypeError):
            price = 0

        if price <= 0:
            # 从pricing_info中获取价格
            pricing_info: Dict[str, Any] = state.pricing_info or {}
            suggested_price: float = pricing_info.get("final_price", 0)
            if suggested_price <= 0:
                suggested_price = pricing_info.get("selling_price", 999)

            first_item["price"] = str(int(suggested_price))
            first_item["old_price"] = str(int(suggested_price * 1.2))
            first_item["min_price"] = str(int(suggested_price * 0.9))
            logger.info(f"✅ 价格已修复：{price} → {suggested_price}")
        else:
            logger.info(f"✅ 价格正常：{price}")

    logger.info(f"✅ pricing修复完成，retry_count={state.retry_count}")
    return state


def repair_dimensions_node(state: ValidationRetryLoopState) -> ValidationRetryLoopState:
    """修复体积/重量节点：基于密度重新计算合理的长宽高。

    Ozon ML 系统会对比同类商品的体积重量，差异过大会返回
    ML_INCORRECT_VOLUME_WEIGHT 或 INCORRECT_DIMENSION。
    此节点用自适应密度重新计算立方体尺寸：
    - 小物品（<500g）：0.8 g/cm³（塑料/金属）
    - 中等物品（500-5000g）：0.3 g/cm³（家居用品）
    - 大物品（>5000g）：0.1 g/cm³（大件轻质物品）
    """
    logger.info("🔧 开始修复体积/重量（dimensions修复）")

    ozon_payload: Dict[str, Any] = state.ozon_payload
    items: list = ozon_payload.get("items", [])

    for i, item in enumerate(items):
        if not isinstance(item, dict):
            continue

        # 读取当前重量（克）
        weight_str: str = str(item.get("weight", "0"))
        try:
            weight_g: float = float(weight_str) if weight_str else 0
        except (ValueError, TypeError):
            weight_g = 0

        # 如果重量为0，使用默认值
        if weight_g <= 0:
            weight_g = 100
            item["weight"] = str(int(weight_g))
            logger.info(f"  item[{i}].weight 为0，设为默认 100g")

        # Ozon 重量范围限制: 40g - 120000g
        OZON_WEIGHT_MIN = 40
        OZON_WEIGHT_MAX = 120000
        if weight_g < OZON_WEIGHT_MIN:
            weight_g = OZON_WEIGHT_MIN
            item["weight"] = str(int(weight_g))
            logger.warning(f"  item[{i}].weight {weight_g}g < 最小值{OZON_WEIGHT_MIN}g，已修正")
        elif weight_g > OZON_WEIGHT_MAX:
            weight_g = OZON_WEIGHT_MAX
            item["weight"] = str(int(weight_g))
            logger.warning(f"  item[{i}].weight {weight_g}g > 最大值{OZON_WEIGHT_MAX}g，已修正")

        # 读取当前尺寸（mm）
        depth = int(float(str(item.get("depth", "0")) or "0"))
        width = int(float(str(item.get("width", "0")) or "0"))
        height = int(float(str(item.get("height", "0")) or "0"))

        # ✅ 自适应密度：根据重量范围选择合适的密度
        # 小物品（<500g）：密度较高（塑料/金属）
        # 中等物品（500-5000g）：密度中等
        # 大物品（>5000g）：密度较低（大件轻质物品）
        if weight_g < 500:
            density = 0.8  # g/cm³（小物品：塑料、金属、化妆品等）
        elif weight_g < 5000:
            density = 0.3  # g/cm³（中等物品：家居用品、工具等）
        else:
            density = 0.1  # g/cm³（大物品：家具、推车、大型玩具等）
        volume_cm3 = weight_g / density
        # 立方体边长（cm）→ 转 mm
        side_cm = max(1.0, volume_cm3 ** (1.0 / 3.0))
        side_mm = max(30, min(int(side_cm * 10), 500))

        # 分配三边：略做差异化（不是完美立方体）
        new_depth = side_mm
        new_width = max(30, int(side_mm * 0.8))
        new_height = max(20, int(side_mm * 0.6))

        item["depth"] = str(new_depth)
        item["width"] = str(new_width)
        item["height"] = str(new_height)
        item["dimension_unit"] = "mm"
        item["weight_unit"] = "g"

        # ✅ 一致性校验：体积重量 vs 实际重量
        recalc_vw = (new_depth * new_width * new_height) / 5000.0
        if recalc_vw > 0:
            ratio = weight_g / recalc_vw
            if ratio > 3.0 or ratio < 0.33:
                # 重量与体积严重不匹配，调整重量为体积重量
                old_weight = int(weight_g)
                weight_g = max(40, min(int(recalc_vw), 120000))
                item["weight"] = str(int(weight_g))
                logger.warning(
                    f"  item[{i}] 重量与体积不匹配(比值={ratio:.1f}x): "
                    f"重量 {old_weight}g → {int(weight_g)}g (体积重量={int(recalc_vw)}g)"
                )

        logger.info(
            f"  item[{i}] 尺寸已修复: weight={int(weight_g)}g, "
            f"dimensions={new_depth}×{new_width}×{new_height}mm "
            f"(密度≈{density} g/cm³, 体积重量≈{int(recalc_vw)}g)"
        )

    logger.info(f"✅ dimensions修复完成，retry_count={state.retry_count}")
    return state


# ============================================================
# 靶向修复函数（reupload_node 路由器调用）
# ============================================================

def _fix_via_attributes_update(state: ValidationRetryLoopState) -> bool:
    """调用 POST /v1/product/attributes/update 增量更新属性。

    当 product_id 存在且错误为属性相关时使用。此 API 只更新属性值，
    不触发 Ozon 全量重新审核，响应快速（~3s）。

    Returns:
        True 表示 API 调用成功（200），False 表示失败需回退。
    """
    items = state.ozon_payload.get("items", [])
    first_item = items[0] if items else {}
    offer_id = first_item.get("offer_id", "")

    if not offer_id:
        logger.warning("⚠️ attributes/update: offer_id 缺失")
        return False

    # 从 final_attributes 构造 Ozon 格式的属性列表
    attributes = state.final_attributes or first_item.get("attributes", [])
    ozon_attrs = []
    for attr in attributes:
        if not isinstance(attr, dict):
            continue
        aid = attr.get("id") or attr.get("attribute_id")
        vals = attr.get("values", [])
        if not vals and attr.get("value"):
            vals = [{"value": str(attr["value"]), "dictionary_value_id": attr.get("dictionary_value_id", 0)}]
        if aid and vals:
            ozon_attrs.append({"id": int(aid), "values": vals})

    if not ozon_attrs:
        logger.warning("⚠️ attributes/update: 无有效属性")
        return False

    update_body = {
        "items": [{
            "offer_id": str(offer_id),
            "product_id": int(state.product_id),
            "attributes": ozon_attrs,
        }]
    }

    try:
        resp = session.post(
            "https://api-seller.ozon.ru/v1/product/attributes/update",
            headers={
                "Client-Id": state.ozon_client_id,
                "Api-Key": state.ozon_api_key,
                "Content-Type": "application/json",
            },
            json=update_body, timeout=30,
        )
        if resp.status_code == 200:
            task_id = resp.json().get("result", {}).get("task_id", "") or resp.json().get("task_id", "")
            logger.info(f"✅ 属性增量更新成功 (task_id={task_id}, {len(ozon_attrs)} attrs)")
            return True
        else:
            logger.warning(f"⚠️ attributes/update 返回 {resp.status_code}: {resp.text[:200]}")
            return False
    except Exception as e:
        logger.warning(f"⚠️ attributes/update 异常: {e}")
        return False


def _fix_via_prices_update(state: ValidationRetryLoopState) -> bool:
    """调用 POST /v1/product/import/prices 增量更新价格。

    当 product_id 存在且错误为价格相关时使用。此 API 只更新价格字段，
    不触发 Ozon 重新审核，响应快速（~3s）。

    Returns:
        True 表示 API 调用成功（200），False 表示失败需回退。
    """
    items = state.ozon_payload.get("items", [])
    first_item = items[0] if items else {}
    offer_id = first_item.get("offer_id", "")
    product_id = state.product_id

    if not offer_id or not product_id:
        logger.warning("⚠️ prices/update: offer_id 或 product_id 缺失")
        return False

    # 获取价格：优先从 ozon_payload，其次从 pricing_info
    price = first_item.get("price", "")
    old_price = first_item.get("old_price", "")
    min_price = first_item.get("min_price", "")

    if not price:
        pricing_info = state.pricing_info or {}
        suggested_price = pricing_info.get("final_price", 0) or pricing_info.get("selling_price", 0)
        if suggested_price > 0:
            price = str(int(suggested_price))
            old_price = str(int(suggested_price * 1.2))
            min_price = str(int(suggested_price * 0.9))

    if not price:
        logger.warning("⚠️ prices/update: 无法确定价格")
        return False

    update_body = {
        "prices": [{
            "offer_id": str(offer_id),
            "product_id": int(product_id),
            "price": str(price),
            "old_price": str(old_price) if old_price else str(int(float(price) * 1.2)),
            "min_price": str(min_price) if min_price else str(int(float(price) * 0.9)),
        }]
    }

    try:
        resp = session.post(
            "https://api-seller.ozon.ru/v1/product/import/prices",
            headers={
                "Client-Id": state.ozon_client_id,
                "Api-Key": state.ozon_api_key,
                "Content-Type": "application/json",
            },
            json=update_body, timeout=30,
        )
        if resp.status_code == 200:
            logger.info(f"✅ 价格增量更新成功: price={price}, old_price={update_body['prices'][0]['old_price']}")
            # 同步更新 ozon_payload 中的价格
            first_item["price"] = str(price)
            first_item["old_price"] = str(update_body["prices"][0]["old_price"])
            first_item["min_price"] = str(update_body["prices"][0]["min_price"])
            return True
        else:
            logger.warning(f"⚠️ prices/update 返回 {resp.status_code}: {resp.text[:200]}")
            return False
    except Exception as e:
        logger.warning(f"⚠️ prices/update 异常: {e}")
        return False


def _fix_via_product_import_update(state: ValidationRetryLoopState) -> Optional[str]:
    """调用 POST /v3/product/import（UPDATE 模式）更新产品。

    当 product_id 存在且错误需要更新扁平字段（类目/尺寸/描述/图片）时使用。
    在 payload 的每个 item 中添加 product_id 字段，Ozon 将其视为更新而非创建。

    Returns:
        Ozon task_id (str) 如果成功，None 如果失败。
    """
    items = state.ozon_payload.get("items", [])
    if not items:
        logger.warning("⚠️ product/import(update): items 为空")
        return None

    product_id = state.product_id
    try:
        pid_int = int(product_id)
    except (ValueError, TypeError):
        logger.warning(f"⚠️ product/import(update): product_id 无效: {product_id}")
        return None

    # 关键：为每个 item 添加 product_id，Ozon 将其视为 UPDATE
    for item in items:
        if isinstance(item, dict):
            item["product_id"] = pid_int

    try:
        resp = session.post(
            "https://api-seller.ozon.ru/v3/product/import",
            headers={
                "Client-Id": state.ozon_client_id,
                "Api-Key": state.ozon_api_key,
                "Content-Type": "application/json",
            },
            json={"items": items}, timeout=60,
        )
        if resp.status_code == 200:
            task_id = resp.json().get("result", {}).get("task_id", "")
            logger.info(f"✅ product/import(UPDATE) 成功: task_id={task_id}, product_id={pid_int}")
            return str(task_id) if task_id else None
        else:
            logger.warning(f"⚠️ product/import(UPDATE) 返回 {resp.status_code}: {resp.text[:200]}")
            return None
    except Exception as e:
        logger.warning(f"⚠️ product/import(UPDATE) 异常: {e}")
        return None


def revalidate_node(state: ValidationRetryLoopState) -> ValidationRetryLoopState:
    """重新验证节点：验证修复后的payload结构"""
    logger.info(f"🔍 开始重新验证（retry_count={state.retry_count}）")

    ozon_payload: Dict[str, Any] = state.ozon_payload
    validation_errors: list = []
    is_valid: bool = True

    if ozon_payload and isinstance(ozon_payload, dict):
        items: list = ozon_payload.get("items", [])
        if items and len(items) > 0:
            first_item: Dict[str, Any] = items[0]

            # 检查必填字段
            if not first_item.get("name"):
                validation_errors.append({"field": "name", "message": "产品名称缺失"})
                is_valid = False
            # ✅ v0.8.0: 检查名称是否为纯拉丁字母（Ozon 禁止）
            _item_name = str(first_item.get("name", ""))
            if _item_name:
                _latin_re_v = re.compile(r'[a-zA-Z]')
                _cyrillic_re_v = re.compile(r'[а-яА-ЯёЁ]')
                if _latin_re_v.search(_item_name) and not _cyrillic_re_v.search(_item_name):
                    validation_errors.append({"field": "name", "message": "产品名称含拉丁字母，需改为俄语"})
                    is_valid = False
            if not first_item.get("offer_id"):
                validation_errors.append({"field": "offer_id", "message": "SKU ID缺失"})
                is_valid = False
            if not first_item.get("price"):
                validation_errors.append({"field": "price", "message": "价格缺失"})
                is_valid = False

            # 更新attributes（从final_attributes转换为Ozon格式）
            # ✅ A5修复：同步更新category_id和type_id（error_repair_llm可能修改了这些）
            if state.description_category_id:
                first_item["description_category_id"] = state.description_category_id
            if state.type_id:
                first_item["type_id"] = state.type_id

            # 跳过Ozon禁止编辑的属性
            SKIP_ATTR_IDS: set = {23536}  # 23536: код маркировки, Ozon自动设置
            # 需要翻译为俄语的属性ID（含23171标签，Ozon要求标签为俄语）
            TRANSLATE_ATTR_IDS: set = {4191, 4180, 4384, 4389, 23171}
            # ⚠️ 9048不放入TRANSLATE_ATTR_IDS！prepare_ozon_upload_node已经翻译过了，
            # 这里从payload中提取已有的9048值，避免重新翻译导致值不一致（变体无法合并）

            # 从现有payload中提取已有的9048值（prepare节点已翻译）
            existing_9048_val: str = ""
            existing_9048_dict_id: int = 0
            for ex_attr in first_item.get("attributes", []):
                if not isinstance(ex_attr, dict):
                    continue
                if int(ex_attr.get("id", 0)) == 9048:
                    ex_vals: list = ex_attr.get("values", [])
                    if ex_vals and isinstance(ex_vals[0], dict):
                        existing_9048_val = str(ex_vals[0].get("value", ""))
                        existing_9048_dict_id = int(ex_vals[0].get("dictionary_value_id", 0))
                    break

            ozon_attrs: list = []
            skipped_attrs: list = []
            for attr in state.final_attributes:
                if not isinstance(attr, dict):
                    continue
                attr_id_val: Any = attr.get("id") or attr.get("attribute_id")
                if attr_id_val is None:
                    continue

                attr_id_int: int = int(attr_id_val) if attr_id_val else 0

                # 跳过禁止编辑的属性
                if attr_id_int in SKIP_ATTR_IDS:
                    skipped_attrs.append(attr_id_int)
                    continue

                # 属性4389(原产国)硬编码为Китай
                if attr_id_int == 4389:
                    ozon_attrs.append({
                        "complex_id": 0,
                        "id": 4389,
                        "values": [{
                            "dictionary_value_id": 90296,
                            "value": "Китай"
                        }]
                    })
                    continue

                attr_value: Any = attr.get("value", "")
                dict_value_id: Any = attr.get("dictionary_value_id", 0)

                # 9048特殊处理：优先使用payload中已有的值（prepare已翻译），避免重新翻译导致不一致
                if attr_id_int == 9048 and existing_9048_val:
                    attr_value = existing_9048_val
                    dict_value_id = existing_9048_dict_id
                    logger.info(f"✅ 9048使用payload已有值（避免重新翻译）: {str(attr_value)[:60]}")

                # 翻译需要俄语的属性（如果值是拉丁字母/中文）— 不再翻译9048
                if attr_id_int in TRANSLATE_ATTR_IDS and attr_value:
                    val_str: str = str(attr_value)
                    has_cyr: bool = any('\u0400' <= ch <= '\u04FF' for ch in val_str)
                    if not has_cyr:
                        try:
                            translated_val: str = _call_mxou_llm(
                                state.token,
                                "config/translate_russian_cfg.json",
                                {"text": val_str}
                            ).strip()
                            if translated_val and any('\u0400' <= ch <= '\u04FF' for ch in translated_val):
                                attr_value = translated_val
                                logger.info(f"✅ revalidate翻译属性{attr_id_int}: '{val_str[:40]}' → '{translated_val[:40]}'")
                        except Exception as trans_err:
                            logger.warning(f"属性{attr_id_int}翻译失败: {trans_err}")

                ozon_attr: Dict[str, Any] = {
                    "complex_id": 0,
                    "id": attr_id_int,
                    "values": [{
                        "dictionary_value_id": int(dict_value_id) if dict_value_id else 0,
                        "value": str(attr_value) if attr_value else ""
                    }]
                }
                ozon_attrs.append(ozon_attr)

            if skipped_attrs:
                logger.info(f"✅ revalidate跳过禁止编辑的属性: {skipped_attrs}")

            # ✅ 确保属性9048（型号名称）存在 - 这是Ozon必填属性
            # 与prepare_ozon_upload_node使用相同的逻辑：优先用8229(产品类型名)，其次用draft中的title
            has_9048: bool = any(
                isinstance(a, dict) and int(a.get("id", 0)) == 9048
                for a in ozon_attrs
            )
            if not has_9048:
                model_name_val: str = ""
                # 从final_attributes中提取8229(产品类型名)作为型号名
                for fa in state.final_attributes:
                    if not isinstance(fa, dict):
                        continue
                    try:
                        if int(fa.get("attribute_id", 0)) == 8229:
                            model_name_val = str(fa.get("value", "")).strip()
                            break
                    except (ValueError, TypeError):
                        continue
                # 如果8229没有西里尔字符，用draft中的name
                if not model_name_val or not any('\u0400' <= ch <= '\u04FF' for ch in model_name_val):
                    draft_name: str = ""
                    if state.draft and isinstance(state.draft, dict):
                        draft_name = str(state.draft.get("name", "")).strip()
                    if draft_name:
                        model_name_val = draft_name[:50]
                    elif state.sku_id:
                        model_name_val = state.sku_id
                if model_name_val:
                    model_name_val = model_name_val[:50]
                    ozon_attrs.append({
                        "complex_id": 0,
                        "id": 9048,
                        "values": [{"dictionary_value_id": 0, "value": model_name_val}]
                    })
                    logger.info(f"✅ revalidate补充属性9048（型号名称），值: {model_name_val[:80]}")

            first_item["attributes"] = ozon_attrs
            logger.info(f"✅ 已更新{len(ozon_attrs)}个属性到items[0]（跳过{len(skipped_attrs)}个）")

            # ✅ 关键修复：同步共享属性到所有变体items（不只是items[0]）
            # 变体之间只能有颜色、尺寸(4295)、价格、主图不同
            # 其他所有属性（包括9048绑定属性）必须完全一致，否则Ozon不会合并变体
            # ✅ 动态检测颜色属性ID（不同类目可能使用10096或10097等）
            detected_color_attr_id: int = 10096
            if isinstance(ozon_attrs, list):
                _color_ids_set: set = {10096, 10097, 10098, 10099}
                for _ba in ozon_attrs:
                    if not isinstance(_ba, dict):
                        continue
                    _ba_id: int = int(_ba.get("id", 0))
                    if _ba_id in _color_ids_set:
                        detected_color_attr_id = _ba_id
                        break
            variant_attr_ids: set = {detected_color_attr_id, 4295}
            if len(items) > 1:
                # 从items[0]的属性中提取共享属性（排除颜色和尺码）
                shared_attrs_for_variants: list = [
                    attr for attr in ozon_attrs
                    if isinstance(attr, dict) and int(attr.get("id", 0)) not in variant_attr_ids
                ]
                synced_count: int = 0
                for idx in range(1, len(items)):
                    other_item: Dict[str, Any] = items[idx]
                    if not isinstance(other_item, dict):
                        continue
                    # 保留该变体原有的颜色和尺码属性
                    variant_specific_attrs: list = []
                    old_attrs: list = other_item.get("attributes", [])
                    if isinstance(old_attrs, list):
                        for oa in old_attrs:
                            if not isinstance(oa, dict):
                                continue
                            oa_id: Any = oa.get("id", 0)
                            try:
                                oa_id_int: int = int(oa_id) if oa_id else 0
                            except (ValueError, TypeError):
                                oa_id_int = 0
                            if oa_id_int in variant_attr_ids:
                                variant_specific_attrs.append(oa)
                    # 合并：共享属性 + 变体特有属性
                    other_item["attributes"] = list(shared_attrs_for_variants) + variant_specific_attrs
                    # 同步description_category_id和type_id
                    if state.description_category_id:
                        other_item["description_category_id"] = state.description_category_id
                    if state.type_id:
                        other_item["type_id"] = state.type_id
                    synced_count += 1
                if synced_count > 0:
                    logger.info(f"✅ 已同步共享属性（含9048）到{synced_count}个变体items，保留各变体的颜色/尺码属性")

            # 检查尺寸重量（扁平字段）
            weight_val: Any = first_item.get("weight", 0)
            try:
                weight_check: int = int(weight_val) if weight_val else 0
            except (ValueError, TypeError):
                weight_check = 0
            if weight_check <= 0:
                validation_errors.append({"field": "weight", "message": "重量缺失或为0"})
                is_valid = False

            # ✅ 属性级别验证：检查必填属性和字典值有效性
            ozon_attrs_list: list = first_item.get("attributes", [])
            attr_id_set: set = set()
            for oa in ozon_attrs_list:
                if not isinstance(oa, dict):
                    continue
                oa_id: Any = oa.get("id")
                if oa_id is not None:
                    attr_id_set.add(int(oa_id))
                # 检查字典属性是否有dictionary_value_id
                oa_values: list = oa.get("values", [])
                for v in oa_values:
                    if not isinstance(v, dict):
                        continue
                    v_val: Any = v.get("value", "")
                    v_dict_id: Any = v.get("dictionary_value_id", 0)
                    # 如果值看起来像字典值但dictionary_value_id为0，可能有问题
                    # （但不能强制要求，因为有些属性值是自由文本）

            # 检查schema中必填属性是否都已设置
            # 收集本次revalidate发现的缺失属性，稍后同步到state.errors
            newly_missing_errors: list = []
            for schema_attr in state.attributes_schema:
                if not isinstance(schema_attr, dict):
                    continue
                is_required: Any = schema_attr.get("is_required", False)
                if is_required:
                    schema_attr_id: Any = schema_attr.get("id")
                    if schema_attr_id is not None and int(schema_attr_id) not in attr_id_set:
                        attr_name: str = schema_attr.get("name", f"id={schema_attr_id}")
                        missing_err: Dict[str, Any] = {
                            "field": f"attribute[{attr_name}]",
                            "message": f"必填属性缺失: {attr_name} (id={schema_attr_id})"
                        }
                        validation_errors.append(missing_err)
                        # ✅ 同步写入state.errors，格式与parse_error_node期望一致
                        newly_missing_errors.append({
                            "code": "MISSING_REQUIRED_ATTRIBUTE",
                            "attribute_id": int(schema_attr_id),
                            "texts": {"message": f"必填属性缺失: {attr_name} (id={schema_attr_id})"}
                        })
                        is_valid = False
                        logger.warning(f"⚠️ 必填属性缺失: {attr_name} (id={schema_attr_id})")

            # ✅ 将revalidate发现的缺失属性错误合并到state.errors，供下一轮parse_error_node处理
            if newly_missing_errors:
                existing_errs: list = state.errors or []
                # 提取已有的attribute_id集合，避免重复添加
                existing_attr_ids: set = set()
                for ee in existing_errs:
                    if isinstance(ee, dict):
                        ea_id: Any = ee.get("attribute_id", 0)
                        if ea_id:
                            existing_attr_ids.add(int(ea_id))
                for nme in newly_missing_errors:
                    nme_attr_id: int = nme.get("attribute_id", 0)
                    if nme_attr_id not in existing_attr_ids:
                        existing_errs.append(nme)
                        existing_attr_ids.add(nme_attr_id)
                state.errors = existing_errs
                logger.info(f"📋 已将{len(newly_missing_errors)}个缺失属性错误同步到state.errors")
        else:
            validation_errors.append({"field": "items", "message": "产品列表为空"})
            is_valid = False
    else:
        validation_errors.append({"field": "payload", "message": "Payload结构无效"})
        is_valid = False

    state.validation_errors = validation_errors
    state.error_message = "; ".join([e.get("message", "") for e in validation_errors]) if validation_errors else ""
    state.is_valid = is_valid

    # ✅ 本地验证通过。注：Ozon /v1/product/validate API 不存在（返回404），
    # 不调用外部API预检，仅依赖本地验证结果。

    logger.info(f"✅ 重新验证完成：is_valid={is_valid}, errors={len(validation_errors)}")
    return state


def should_continue(state: ValidationRetryLoopState) -> str:
    """条件分支：判断验证结果，决定继续修复还是退出"""
    # ✅ 修复：先检查is_valid，再检查retry_count（与should_reupload保持一致）
    if state.is_valid:
        logger.info("✅ 验证通过，准备重新上传")
        return "success"

    if state.retry_count >= state.max_retries:
        logger.warning(f"❌ 重试次数耗尽（{state.retry_count}/{state.max_retries}），退出循环")
        return "exit"

    logger.info("❌ 验证失败，继续修复")
    return "parse_error"


def reupload_node(state: ValidationRetryLoopState) -> ValidationRetryLoopState:
    """重新上传节点：靶向路由器。

    根据 product_id 和 error_code 选择最优的 Ozon API 端点：
    - 有 product_id + 属性错误 → attributes/update（增量，~3s，无需审核）
    - 有 product_id + 价格错误 → prices/update（增量，~3s，无需审核）
    - 有 product_id + 类目/尺寸/描述错误 → product/import UPDATE 模式（需审核轮询）
    - 无 product_id → 全量 product/import CREATE 模式（首次上传失败场景）
    - 不可修复错误 → 直接标记 success（不浪费重试）
    """
    logger.info("🔄 开始重新上传Ozon...")

    error_code = state.error_code
    has_product_id = bool(state.product_id)

    if has_product_id:
        logger.info(f"📝 产品已存在(product_id={state.product_id})，使用靶向修复")

    # ── 无 product_id：全量 CREATE（首次上传失败场景）──
    if not has_product_id:
        logger.info("📦 无 product_id，使用全量 product/import (CREATE 模式)")
        return _full_import_create(state)

    # ── 有 product_id：靶向路由 ──
    fix_type = classify_fix_type(error_code)
    logger.info(f"🎯 靶向修复类型: {fix_type} (error_code={error_code})")

    # 类型 1: 属性错误 → attributes/update（增量，无需审核轮询）
    if fix_type == "attributes":
        if _fix_via_attributes_update(state):
            state.upload_status = "success"
            state.is_valid = True
            logger.info("✅ 属性增量更新成功，跳过审核轮询")
            return state
        else:
            logger.warning("⚠️ attributes/update 失败，回退到 product/import UPDATE")
            fix_type = "product_import"  # 回退

    # 类型 2: 价格错误 → prices/update（增量，无需审核轮询）
    if fix_type == "prices":
        if _fix_via_prices_update(state):
            state.upload_status = "success"
            state.is_valid = True
            logger.info("✅ 价格增量更新成功，跳过审核轮询")
            return state
        else:
            logger.warning("⚠️ prices/update 失败，回退到 product/import UPDATE")
            fix_type = "product_import"  # 回退

    # 类型 3: 类目/尺寸/描述/图片 → product/import UPDATE 模式
    if fix_type == "product_import":
        task_id = _fix_via_product_import_update(state)
        if task_id:
            state.task_id = str(task_id)
            state.upload_status = "uploaded"
            logger.info(f"✅ product/import(UPDATE) 已提交，task_id={task_id}，等待审核轮询")
            return state
        else:
            logger.error("❌ product/import(UPDATE) 失败")
            state.upload_status = "failed"
            state.error_message = f"product/import(UPDATE) 失败: error_code={error_code}"
            return state

    # 类型 4: 不可修复 → 直接标记成功
    if fix_type == "unfixable":
        state.upload_status = "success"
        state.is_valid = True
        logger.info(f"⚠️ 不可修复错误({error_code})，标记为 success（不阻断产品上架）")
        return state

    # 回退：未知类型 → 全量 CREATE
    logger.warning(f"⚠️ 未知 fix_type={fix_type}，回退到全量 product/import (CREATE)")
    return _full_import_create(state)


def _full_import_create(state: ValidationRetryLoopState) -> ValidationRetryLoopState:
    """全量 product/import（CREATE 模式）— 用于无 product_id 的首次上传或回退场景。"""
    items: list = state.ozon_payload.get("items", [])
    payload: Dict[str, Any] = {"items": items}

    try:
        response = session.post(
            "https://api-seller.ozon.ru/v3/product/import",
            headers={
                "Client-Id": state.ozon_client_id,
                "Api-Key": state.ozon_api_key,
                "Content-Type": "application/json",
            },
            json=payload, timeout=30,
        )
        response_data: Dict[str, Any] = response.json()

        if response.status_code == 200:
            task_id: Any = response_data.get("result", {}).get("task_id", "")
            logger.info(f"✅ 全量 import(CREATE) 成功，task_id={task_id}")
            state.task_id = str(task_id) if task_id else ""
            state.upload_status = "uploaded"
        else:
            error_msg: str = response_data.get("message", "Unknown error")
            logger.error(f"❌ 全量 import(CREATE) 失败: {error_msg}")
            state.upload_status = "failed"
            state.error_message = f"重新上传失败: {error_msg}"
    except Exception as e:
        logger.error(f"❌ 全量 import(CREATE) 异常: {e}")
        state.upload_status = "failed"
        state.error_message = f"重新上传异常: {str(e)}"

    return state


def recheck_status_node(state: ValidationRetryLoopState) -> ValidationRetryLoopState:
    """重新查询状态节点：轮询task_id状态，获取product_id和errors。
    如果增量API已成功（upload_status=success），跳过轮询直接返回。"""
    logger.info("🔍 开始查询重新上传状态...")

    # ✅ 增量 API 已成功（attributes/update 或 prices/update），无需轮询
    if state.upload_status == "success":
        logger.info("✅ 增量 API 已成功，跳过审核轮询")
        return state

    task_id: str = state.task_id
    if not task_id:
        logger.error("❌ task_id为空，无法查询状态")
        state.upload_status = "failed"
        state.error_message = "task_id为空"
        return state

    # ✅ 防御：检测 UUID 格式（ingest_node 生成的系统 task_id）
    # reupload_node 失败时 state.task_id 未被覆盖，仍为 UUID
    if len(task_id) == 36 and task_id.count('-') == 4:
        logger.error(f"❌ task_id 仍为系统 UUID（上传失败未覆盖）: {task_id}")
        state.upload_status = "failed"
        state.error_message = "Ozon 上传失败，未获取到 Ozon task_id"
        return state

    try:
        task_id_int: int = int(float(task_id))
    except (ValueError, TypeError):
        logger.error(f"❌ task_id转换失败：{task_id}")
        state.upload_status = "failed"
        state.error_message = f"task_id格式错误: {task_id}"
        return state

    ozon_url: str = "https://api-seller.ozon.ru/v1/product/import/info"
    headers: Dict[str, str] = {
        "Client-Id": state.ozon_client_id,
        "Api-Key": state.ozon_api_key,
        "Content-Type": "application/json"
    }

    payload: Dict[str, Any] = {"task_id": task_id_int}

    # ✅ 轮询查询（10次×3秒=30秒，平衡速度和可靠性）
    max_polls: int = 10
    poll_interval: int = 3

    for attempt in range(1, max_polls + 1):
        time.sleep(poll_interval)

        try:
            response = session.post(ozon_url, headers=headers, json=payload, timeout=30)
            response_data: Dict[str, Any] = response.json()

            if response.status_code != 200:
                error_msg: str = response_data.get("message", "Unknown error")
                logger.error(f"❌ 查询状态失败(attempt {attempt}/{max_polls}): {error_msg}")
                if attempt == max_polls:
                    state.upload_status = "failed"
                    state.error_message = f"查询状态失败: {error_msg}"
                continue

            result_items: list = response_data.get("result", {}).get("items", [])

            if not result_items:
                logger.error(f"❌ 响应中无items数据(attempt {attempt}/{max_polls})")
                if attempt == max_polls:
                    state.upload_status = "failed"
                continue

            first_item: Dict[str, Any] = result_items[0]
            item_status: str = first_item.get("status", "")
            product_id: Any = first_item.get("product_id", "")
            item_errors: list = first_item.get("errors", [])

            logger.info(f"📊 轮询[{attempt}/{max_polls}] 状态={item_status}, product_id={product_id}, errors={len(item_errors)}")

            state.product_id = str(product_id) if product_id else ""
            state.status = item_status
            state.errors = item_errors

            if item_status == "imported" and product_id:
                state.upload_status = "success"
                state.is_valid = True
                state.product_id = str(product_id) if product_id else ""
                logger.info(f"✅ 重新上传成功！product_id={product_id}")
                
                # ✅ Bug 4 修复：等待 Ozon 审核通过（moderate_status）
                # 导入成功不代表审核通过，需要额外轮询 /v3/product/info/list
                logger.info(f"⏳ 等待 Ozon 审核（最多300秒）...")
                info_url: str = "https://api-seller.ozon.ru/v3/product/info/list"
                for mod_attempt in range(1, 61):  # 60 × 5s = 300s
                    time.sleep(5)
                    try:
                        mod_resp = session.post(info_url, headers=headers, 
                            json={"product_id": [str(product_id)]}, timeout=20)
                        mod_data = mod_resp.json()
                        mod_items = mod_data.get("items", [])
                        if mod_items:
                            mod_status = mod_items[0].get("statuses", {}).get("moderate_status", "")
                            mod_errors = mod_items[0].get("errors", [])
                            logger.info(f"📊 审核轮询[{mod_attempt}/60] moderate={mod_status} errors={len(mod_errors)}")
                            if mod_status == "approved":
                                logger.info(f"✅ 审核通过！product_id={product_id}")
                                state.is_valid = True
                                break
                            elif mod_status == "declined":
                                logger.error(f"❌ 审核被拒：{[e['code'] for e in mod_errors]}")
                                state.upload_status = "failed"
                                state.is_valid = False
                                state.error_message = f"审核被拒: {mod_errors}"
                                break
                    except Exception as e:
                        logger.warning(f"⚠️ 审核轮询异常: {e}")
                break
            elif item_errors and len(item_errors) > 0:
                state.upload_status = "failed"
                state.is_valid = False
                logger.error(f"❌ 重新上传失败，发现{len(item_errors)}个错误")
                break
            elif attempt < max_polls:
                logger.info(f"⏳ 状态={item_status}，继续轮询...")
                continue
            else:
                # 最后一次仍为pending
                state.upload_status = "pending"
                logger.warning(f"⚠️ 轮询{max_polls}次后仍为pending，退出")
        except Exception as e:
            logger.error(f"❌ 查询状态异常(attempt {attempt}/{max_polls}): {e}")
            if attempt == max_polls:
                state.upload_status = "failed"
                state.error_message = f"查询状态异常: {str(e)}"

    return state


def should_reupload(state: ValidationRetryLoopState) -> str:
    """
    判断重新上传结果

    返回：
    - "success": 上传成功，退出循环
    - "parse_error": 上传失败有新错误，回到parse_error重新解析
    - "exit": 达到最大重试次数，退出循环
    """
    logger.info("🔍 判断重新上传结果...")

    # ✅ A7修复：先检查上传是否成功，再检查重试次数
    if state.upload_status == "success":
        logger.info("✅ 重新上传成功，退出循环")
        return "success"

    if state.retry_count >= state.max_retries:
        logger.warning(f"⚠️ 达到最大重试次数（{state.retry_count}/{state.max_retries}），退出循环")
        return "exit"

    # pending状态（轮询超时但import可能仍在处理中）→ 不再循环，视为部分成功
    if state.upload_status == "pending":
        if state.product_id:
            logger.info(f"⚠️ 轮询超时但已有product_id={state.product_id}，视为成功")
            return "success"
        logger.warning("⚠️ 轮询超时且无product_id，退出循环")
        return "exit"

    if state.errors and len(state.errors) > 0:
        logger.info(f"❌ 发现{len(state.errors)}个新错误，回到parse_error重新解析")
        return "parse_error"

    logger.warning("⚠️ 重新上传状态未知，退出循环")
    return "exit"


def final_result(state: ValidationRetryLoopState) -> ValidationRetryLoopOutput:
    """最终结果节点：返回修复结果"""
    # ✅ 如果重新上传成功，清除之前的错误消息
    final_error_message = state.error_message
    if state.upload_status == "success":
        final_error_message = ""
        logger.info("✅ 重新上传成功，清除之前的错误消息")
    return ValidationRetryLoopOutput(
        ozon_payload=state.ozon_payload,
        validation_errors=state.validation_errors,
        is_valid=state.is_valid,
        retry_count=state.retry_count,
        error_type=state.error_type,
        error_message=final_error_message,
        product_id=state.product_id if state.product_id else None,
        upload_status=state.upload_status
    )


# ============================================================
# 创建子图
# ============================================================
def create_validation_retry_loop():
    """
    创建验证循环修复子图

    拓扑逻辑（修复→检查→修复循环）：
    parse_error → classify_error → repair_node_selector（条件分支）
      → error_repair_llm → revalidate → should_continue
        → 成功 → reupload → recheck_status → should_reupload
          → 成功 → final_result → END
          → 有错误 → parse_error（循环回到解析）
          → 达到最大次数 → final_result → END
        → 失败 → parse_error（循环回到解析）
        → 达到最大次数 → final_result → END
      → repair_pricing → revalidate → (同上)
      → repair_dimensions → revalidate → (同上)
      → repair_prepare → revalidate → (同上)
    """
    builder = StateGraph(
        ValidationRetryLoopState,
        input_schema=ValidationRetryLoopInput,
        output_schema=ValidationRetryLoopOutput
    )

    # 添加节点
    builder.add_node("parse_error", parse_error_node)
    builder.add_node("classify_error", classify_error_node)
    builder.add_node("error_repair_llm", error_repair_llm_node)
    builder.add_node("repair_prepare", repair_prepare_node)
    builder.add_node("repair_pricing", repair_pricing_node)
    builder.add_node("repair_dimensions", repair_dimensions_node)
    builder.add_node("revalidate", revalidate_node)
    builder.add_node("reupload", reupload_node)
    builder.add_node("recheck_status", recheck_status_node)
    builder.add_node("final_result", final_result)

    # 设置入口点
    builder.set_entry_point("parse_error")

    # parse_error → classify_error
    builder.add_edge("parse_error", "classify_error")

    # 条件分支1：根据repair_node选择修复节点
    builder.add_conditional_edges(
        source="classify_error",
        path=repair_node_selector,
        path_map={
            "error_repair_llm": "error_repair_llm",
            "repair_prepare": "repair_prepare",
            "repair_pricing": "repair_pricing",
            "repair_dimensions": "repair_dimensions",
            "final_result": "final_result",
        }
    )

    # 所有修复节点 → revalidate（重新验证）
    builder.add_edge("error_repair_llm", "revalidate")
    builder.add_edge("repair_prepare", "revalidate")
    builder.add_edge("repair_pricing", "revalidate")
    builder.add_edge("repair_dimensions", "revalidate")

    # 条件分支2：重新验证后判断结果
    builder.add_conditional_edges(
        source="revalidate",
        path=should_continue,
        path_map={
            "success": "reupload",
            "parse_error": "parse_error",
            "exit": "final_result"
        }
    )

    # 重新上传 → 重新查询状态
    builder.add_edge("reupload", "recheck_status")

    # 条件分支3：重新查询状态后判断结果
    builder.add_conditional_edges(
        source="recheck_status",
        path=should_reupload,
        path_map={
            "success": "final_result",
            "parse_error": "parse_error",
            "exit": "final_result"
        }
    )

    # 结束
    builder.add_edge("final_result", END)

    return builder.compile()


# 编译子图
validation_retry_loop = create_validation_retry_loop()
