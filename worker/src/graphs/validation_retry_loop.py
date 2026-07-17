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
import requests
from utils.http_session import session
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
    "DESCRIPTION_DECLINE": "error_repair_llm",
    "MISSING_ATTRIBUTE": "error_repair_llm",
    "MISSING_REQUIRED_ATTRIBUTE": "error_repair_llm",
    "INVALID_CATEGORY": "error_repair_llm",
    "INVALID_PRICE": "repair_pricing",
    "WEIGHT_DIMENSION_ERROR": "repair_prepare",
    "INVALID_DIMENSION": "repair_prepare",
    # ✅ P1-2: 变体未合并 → 走 repair_prepare 重新构建payload（确保颜色属性/9048正确）
    "VARIANT_NOT_MERGED": "repair_prepare",
    # ✅ 9048冲突 → 走 repair_prepare 追加后缀重试
    "double_without_merger_offer": "repair_prepare",
}


# ============================================================
# 辅助函数
# ============================================================

def _sanitize_title(title: str) -> str:
    """
    标题后校验：确保标题符合Ozon规范
    1. 长度≤50字符（超长截断到最近的单词边界）
    2. 含标点符号（长标题无标点时自动添加）
    3. 无关键词堆砌（连续3+个名词无标点时，在适当位置插入逗号）
    """
    if not title or not title.strip():
        return title

    title = title.strip()

    # 1. 长度校验：≤50字符
    if len(title) > 50:
        # 截断到最近的空格边界（不截断单词）
        truncated: str = title[:50]
        last_space: int = truncated.rfind(' ')
        if last_space > 20:  # 确保截断后不会太短
            truncated = truncated[:last_space]
        title = truncated
        logger.warning(f"⚠️ 标题超长，截断为：{title}")

    # 2. 标点符号校验：如果标题>30字符且不含任何标点，添加标点
    punctuation_chars: set = {'.', ',', '-', '°', '(', ')', '/', ':', '–', '—'}
    has_punct: bool = any(ch in punctuation_chars for ch in title)
    if len(title) > 30 and not has_punct:
        # 在中间位置找空格，替换为逗号
        mid: int = len(title) // 2
        for i in range(mid, len(title)):
            if title[i] == ' ':
                title = title[:i] + ',' + title[i:]
                break
        if not any(ch in punctuation_chars for ch in title):
            # 兜底：末尾加句号
            title = title + '.'
        logger.warning(f"⚠️ 标题无标点，已补救：{title}")

    # 3. 关键词堆砌检测：连续4+个单词（每个≥4字符）无标点分隔
    words: list = title.split()
    if len(words) >= 5:
        consecutive_nouns: int = 0
        needs_fix: bool = False
        for w in words:
            if len(w) >= 4 and not any(ch in punctuation_chars for ch in w):
                consecutive_nouns += 1
                if consecutive_nouns >= 4:
                    needs_fix = True
                    break
            else:
                consecutive_nouns = 0

        if needs_fix:
            # 在第3个长词后插入逗号
            fixed_words: list = []
            long_count: int = 0
            for w in words:
                fixed_words.append(w)
                if len(w) >= 4 and not any(ch in punctuation_chars for ch in w):
                    long_count += 1
                    if long_count == 3:
                        fixed_words[-1] = w + ','
            title = ' '.join(fixed_words)
            logger.warning(f"⚠️ 检测到关键词堆砌，已插入标点：{title}")

    return title


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
    调用mxou LLM API
    使用Bearer {token}鉴权，与图片生成节点一致
    """
    cfg_file: str = os.path.join(os.getenv("APP_WORKSPACE_PATH", ""), config_path)
    with open(cfg_file, "r", encoding="utf-8") as fd:
        cfg: Dict[str, Any] = json.load(fd)

    llm_config: Dict[str, Any] = cfg.get("config", {})
    sp: str = cfg.get("sp", "")
    up: str = cfg.get("up", "")

    # 渲染用户提示词
    up_tpl: Template = Template(up)
    user_prompt: str = up_tpl.render(context_vars)

    headers: Dict[str, str] = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    payload: Dict[str, Any] = {
        "model": llm_config.get("model", "deepseek-v4-flash"),
        "messages": [
            {"role": "system", "content": sp},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": llm_config.get("temperature", 0.3),
        "max_tokens": llm_config.get("max_completion_tokens", 4096)
    }

    try:
        response = session.post(
            "https://api.mxou.cn/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=60
        )
        if response.status_code == 200:
            result: Dict[str, Any] = response.json()
            choices: list = result.get("choices", [])
            if choices and len(choices) > 0:
                content: str = choices[0].get("message", {}).get("content", "")
                logger.info(f"mxou LLM返回内容长度: {len(content)}")
                return content
        logger.error(f"mxou LLM返回 {response.status_code}: {response.text[:300]}")
        return ""
    except Exception as e:
        logger.error(f"mxou LLM调用异常: {e}")
        return ""


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

    first_error: Dict[str, Any] = errors[0] if isinstance(errors[0], dict) else {}
    error_code: str = first_error.get("code", "UNKNOWN")
    attribute_id: Any = first_error.get("attribute_id", 0)
    texts: Dict[str, Any] = first_error.get("texts", {})
    error_message: str = texts.get("message", "") if isinstance(texts, dict) else ""

    try:
        attr_id: int = int(attribute_id) if attribute_id else 0
    except (ValueError, TypeError):
        attr_id = 0

    # ✅ A1修复：从errors数组中移除已处理的错误，避免反复修同一个错误
    state.errors = errors[1:]
    logger.info(f"📋 移除已处理错误，剩余{len(state.errors)}个待处理")

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

    # 判断是否可修复
    unfixable_codes: set = {"PRODUCT_ALREADY_EXISTS"}
    if error_code in unfixable_codes:
        state.error_type = "unfixable"
        state.repair_node = "final_result"
        logger.warning(f"❌ 错误不可修复: {error_code}")
        return state

    # 查修复策略表
    repair_node: str = REPAIR_STRATEGY.get(error_code, "error_repair_llm")
    state.repair_node = repair_node
    state.error_type = "fixable"

    logger.info(f"✅ 错误可修复，选择修复节点: {repair_node}")
    return state


def repair_node_selector(state: ValidationRetryLoopState) -> str:
    """条件分支：根据repair_node选择修复节点"""
    if state.error_type == "unfixable":
        return "final_result"

    repair_node: str = state.repair_node
    if repair_node in ("error_repair_llm", "repair_pricing", "repair_prepare"):
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

    error_code: str = state.error_code
    attr_id: int = state.attribute_id
    token: str = state.token
    ozon_client_id: str = state.ozon_client_id
    ozon_api_key: str = state.ozon_api_key
    category_id: str = state.description_category_id
    type_id: str = state.type_id

    # ========== 特殊处理：DESCRIPTION_DECLINE + attr 8229（类型不匹配） ==========
    # 当 Ozon 审核拒绝产品因为"照片与类型不匹配"(attr 8229)时，
    # 根因通常是类目匹配错误，而非属性值错误。仅修改 8229 的值（仍限定在当前类目）
    # 无法解决问题，需要重新匹配类目。
    if error_code == "DESCRIPTION_DECLINE" and attr_id == 8229:
        logger.warning(
            "⚠️ 检测到 DESCRIPTION_DECLINE + attr 8229（类型不匹配）。"
            f"当前类目: category_id={category_id}, type_id={type_id}。"
            "这通常意味着产品被分配到了错误的 Ozon 类目，"
            "修改 8229 属性值无法解决此问题，需要手动重新匹配类目并重新上架。"
        )
        # 标记此产品需要重新分类（不做无意义的属性值修复）
        state.needs_recategorization = True
        logger.info("🔧 已标记 needs_recategorization=True，跳过属性值修复")
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
        if current_value:
            # 先用中文搜索
            search_result: List[Dict[str, Any]] = _search_dictionary_values(
                ozon_client_id, ozon_api_key, attr_id, category_id, type_id,
                current_value, "ZH_HANS"
            )

            # 中文搜不到 → 换英文
            if not search_result:
                logger.info(f"中文搜索无结果，换英文搜索: {current_value}")
                search_result = _search_dictionary_values(
                    ozon_client_id, ozon_api_key, attr_id, category_id, type_id,
                    current_value, "EN"
                )

            # API搜索命中 → 直接修复，不需要LLM
            if search_result and len(search_result) > 0:
                best_match: Dict[str, Any] = search_result[0]
                dict_value_id: int = best_match.get("id", 0)
                dict_value_text: str = best_match.get("value", "")
                logger.info(f"✅ API搜索命中: value={dict_value_text}, id={dict_value_id}")

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
            else:
                logger.warning(f"⚠️ API搜索未命中，转LLM兜底修复")

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

    # 产品信息
    product_name: str = state.draft.get("name", "") if state.draft else ""
    product_desc: str = state.draft.get("description", "") if state.draft else ""

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

                # ✅ 标题修复：如果LLM返回了corrected_title，同时修复name字段和属性4180
                if repaired_title:
                    repaired_title = _sanitize_title(repaired_title)
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

        # 确保所有值>0
        if weight <= 0:
            weight = 500  # 默认500克
        if depth <= 0:
            depth = 200  # 默认200毫米
        if width <= 0:
            width = 200
        if height <= 0:
            height = 200

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
            SKIP_ATTR_IDS: set = {9782, 23536}
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

    # ✅ A3修复：本地验证通过后，调用Ozon API预检
    if is_valid:
        try:
            ozon_validate_url: str = "https://api-seller.ozon.ru/v1/product/validate"
            validate_headers: Dict[str, str] = {
                "Client-Id": state.ozon_client_id,
                "Api-Key": state.ozon_api_key,
                "Content-Type": "application/json"
            }
            validate_payload: Dict[str, Any] = {"items": items}
            validate_resp = session.post(ozon_validate_url, headers=validate_headers, json=validate_payload, timeout=30)
            validate_data: Dict[str, Any] = validate_resp.json()
            validate_result: Dict[str, Any] = validate_data.get("result", {})
            validate_errors: list = validate_result.get("errors", [])

            if validate_errors:
                is_valid = False
                for ve in validate_errors:
                    ve_msg = ve.get("message", "") if isinstance(ve, dict) else str(ve)
                    validation_errors.append({"field": "ozon_validate", "message": ve_msg})
                    logger.warning(f"⚠️ Ozon预检错误: {ve_msg}")
                # ✅ A1关联：将Ozon预检错误转为errors数组格式，供parse_error处理
                state.errors = validate_errors
                state.error_message = "; ".join([e.get("message", "") for e in validation_errors])
                state.is_valid = False
                # retry_count 由 parse_error_node 递增，此处不重复计数
                logger.info(f"📋 Ozon预检发现{len(validate_errors)}个错误，转为errors数组处理")
            else:
                logger.info("✅ Ozon API预检通过")
        except Exception as e:
            logger.warning(f"⚠️ Ozon API预检异常（不阻塞）: {e}")

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
    """重新上传节点：调用Ozon API重新上传商品（同offer_id会更新而非重复创建）"""
    logger.info("🔄 开始重新上传Ozon...")

    # ✅ A4优化：如果有product_id，说明产品已存在，re-import会自动更新
    if state.product_id:
        logger.info(f"📝 产品已存在(product_id={state.product_id})，re-import将更新而非创建")

    ozon_url: str = "https://api-seller.ozon.ru/v3/product/import"
    headers: Dict[str, str] = {
        "Client-Id": state.ozon_client_id,
        "Api-Key": state.ozon_api_key,
        "Content-Type": "application/json"
    }

    items: list = state.ozon_payload.get("items", [])
    payload: Dict[str, Any] = {"items": items}

    try:
        response = session.post(ozon_url, headers=headers, json=payload, timeout=30)
        response_data: Dict[str, Any] = response.json()

        if response.status_code == 200:
            task_id: Any = response_data.get("result", {}).get("task_id", "")
            logger.info(f"✅ 重新上传成功，task_id={task_id}")
            state.task_id = str(task_id) if task_id else ""
            state.upload_status = "uploaded"
        else:
            error_msg: str = response_data.get("message", "Unknown error")
            logger.error(f"❌ 重新上传失败: {error_msg}")
            state.upload_status = "failed"
            state.error_message = f"重新上传失败: {error_msg}"
    except Exception as e:
        logger.error(f"❌ 重新上传异常: {e}")
        state.upload_status = "failed"
        state.error_message = f"重新上传异常: {str(e)}"

    return state


def recheck_status_node(state: ValidationRetryLoopState) -> ValidationRetryLoopState:
    """重新查询状态节点：轮询task_id状态，获取product_id和errors"""
    logger.info("🔍 开始查询重新上传状态...")

    task_id: str = state.task_id
    if not task_id:
        logger.error("❌ task_id为空，无法查询状态")
        state.upload_status = "failed"
        state.error_message = "task_id为空"
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
                logger.info(f"✅ 重新上传成功！product_id={product_id}")
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
            "final_result": "final_result",
        }
    )

    # 所有修复节点 → revalidate（重新验证）
    builder.add_edge("error_repair_llm", "revalidate")
    builder.add_edge("repair_prepare", "revalidate")
    builder.add_edge("repair_pricing", "revalidate")

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
