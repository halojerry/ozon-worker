"""T14b: 草稿单字段 AI 重新生成服务（只读，不写回草稿）。

复用 `utils.mxou_api.call_mxou_chat_api`（LLM 调用）+ 现有翻译路径配方
（prepare_ozon_upload_node 标题「核心词+属性+场景」公式 / 描述净化规则、
utils.title_sanitizer 的西里尔/拉丁/中文正则）——**不新建模型客户端**。

- field ∈ {title, description, attributes, tags}；**不含 brand**（品牌强制
  Нет бренда 约定，见 AGENTS.md）。
- 失败契约：返回 None（调用方转 422），**绝不返回含中文/拉丁残留的值**。
"""

import json
import logging
import re
from typing import Optional

from utils.mxou_api import call_mxou_chat_api
from utils.title_formula import build_title_formula_prompt, parse_title_formula_keywords  # v0.59 标题公式唯一入口

logger = logging.getLogger(__name__)

# 支持的字段（不含 brand——品牌强制 Нет бренда 约定，不提供 AI 生成）
AI_FIELDS = frozenset({"title", "description", "attributes", "tags"})

# ── 校验正则（与 utils/title_sanitizer.py 同源）──
_CYRILLIC_RE = re.compile(r"[а-яА-ЯёЁ]")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")
_LATIN_RE = re.compile(r"[a-zA-Z]{2,}")  # 连续 2+ 拉丁 = 残留


def extract_current_value(field: str, payload: dict) -> Optional[str]:
    """从 draft 信封 payload 提取字段当前值；缺失/空 → None。

    - attributes: dict {中文属性名: 值} → 行格式（每行 "名: 值"）
    - tags: draft.tags 优先；无则从 attributes 中键名含「标签」的值提取
    """
    draft = payload.get("draft") or {}
    if field == "title":
        return str(draft.get("title") or "").strip() or None
    if field == "description":
        return str(draft.get("description") or "").strip() or None
    if field == "tags":
        tags = str(draft.get("tags") or "").strip()
        if tags:
            return tags
        attrs = draft.get("attributes") or {}
        for key, value in attrs.items():
            if "标签" in str(key):
                text = str(value or "").strip()
                if text:
                    return text
        return None
    if field == "attributes":
        attrs = draft.get("attributes") or {}
        lines = [
            f"{key}: {value}" for key, value in attrs.items()
            if str(value or "").strip()
        ]
        return "\n".join(lines) if lines else None
    return None


def _build_prompt(field: str, current_value: str, traffic_keywords: Optional[list] = None) -> tuple[str, str]:
    """按字段构建 (system_prompt, user_prompt)——复用现有翻译路径配方。"""
    if field == "title":
        # 与 prepare_ozon_upload_node 标题分支同配方：共享模块 utils/title_formula
        # （核心词+属性+场景公式 + 流量词建议行；traffic_keywords 已 parse 过滤）
        sys_prompt = build_title_formula_prompt(
            "zh", parse_title_formula_keywords(traffic_keywords or []),
        )
    elif field == "description":
        # 与 prepare_ozon_upload_node 描述翻译同配方（净化规则）
        sys_prompt = (
            "你是一个专业翻译，专门翻译Ozon俄罗斯电商平台的产品描述。将给定的中文描述翻译成俄语。\n"
            "严格规则：\n"
            "1. 100%西里尔字母，移除所有拉丁字母和中文字符\n"
            "2. 移除营销词汇：爆款、热销、新品、促销、跨境、亚马逊、best、hot、sale、new、premium、top、free\n"
            "3. 移除联系方式：网址、电话、邮箱\n"
            "4. 移除品牌名称引用\n"
            "5. 只返回俄语描述文本，不要添加任何解释或前缀"
        )
    elif field == "attributes":
        sys_prompt = (
            "你是Ozon俄罗斯电商平台的产品属性专家。将给定的中文产品属性（每行「属性名: 值」）"
            "翻译为俄语并输出为 JSON 对象。\n"
            "严格规则：\n"
            "1. 只输出一个 JSON 对象，键为俄语属性名，值为俄语属性值（不要 Markdown 代码块、不要多余文本）\n"
            "2. 100%西里尔字母，禁止拉丁字母和中文（品牌值填 \"Нет бренда\"）\n"
            "3. 保留属性数量，不要合并或删除\n"
            "示例：\n"
            "- 输入：\"颜色: 白色\\n材质: 塑料\"\n"
            "- 输出：{\"Цвет\": \"Белый\", \"Материал\": \"Пластик\"}"
        )
    elif field == "tags":
        sys_prompt = (
            "你是Ozon俄罗斯电商平台的主题标签专家。根据给定的中文产品信息生成俄语主题标签。\n"
            "严格规则：\n"
            "1. 生成 3-8 个俄语主题标签，逗号分隔，不要 # 号\n"
            "2. 100%西里尔字母，禁止拉丁字母和中文\n"
            "3. 只返回标签文本，不要解释"
        )
    else:  # pragma: no cover — AI_FIELDS 门已挡
        raise ValueError(f"unknown field: {field}")
    return sys_prompt, f"Translate to Russian: {current_value}"


def _is_clean_russian(text: str) -> bool:
    """非空 + 含西里尔 + 无中文 + 无拉丁残留。"""
    return bool(_CYRILLIC_RE.search(text)) and not _CJK_RE.search(text) and not _LATIN_RE.search(text)


def _validate_attributes_json(text: str) -> Optional[str]:
    """attributes 结果必须是合法 JSON 对象，且所有键/值为干净俄语。"""
    cleaned = text.strip()
    # 剥离可能的 Markdown 代码块包裹（```json ... ```）
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    try:
        obj = json.loads(cleaned)
    except (json.JSONDecodeError, TypeError):
        logger.warning("attributes LLM 输出非 JSON: %r", text[:120])
        return None
    if not isinstance(obj, dict) or not obj:
        logger.warning("attributes LLM 输出非对象: %r", text[:120])
        return None
    for key, value in obj.items():
        if not _is_clean_russian(str(key)) or not _is_clean_russian(str(value)):
            logger.warning("attributes 含中文/拉丁残留: %r", text[:120])
            return None
    return json.dumps(obj, ensure_ascii=False)


def regenerate_field(field: str, current_value: str, token: str, traffic_keywords: Optional[list] = None) -> Optional[str]:
    """单字段 AI 重新生成：LLM → 校验（非空 RU 无中文/拉丁残留）→ 返回；失败 None。

    token: mxou API 密钥（复用 call_mxou_chat_api，不新建客户端）。
    traffic_keywords: 标题公式流量词（俄语，可选；title 分支注入 system_prompt）。
    """
    if field not in AI_FIELDS:
        raise ValueError(f"unknown field: {field}")
    text = str(current_value or "").strip()
    if not text:
        return None

    sys_prompt, user_prompt = _build_prompt(field, text, traffic_keywords)
    result = call_mxou_chat_api(
        token=token,
        system_prompt=sys_prompt,
        user_prompt=user_prompt,
        temperature=0.0,
        max_tokens=1500,  # deepseek-v4-flash reasoning 需要充足配额
    )
    if not result:
        logger.warning("draft_ai field=%s LLM 调用失败/空响应", field)
        return None

    result = result.strip()
    if field == "attributes":
        return _validate_attributes_json(result)
    if _is_clean_russian(result):
        return result
    logger.warning("draft_ai field=%s 结果含中文/拉丁残留或非俄语: %r", field, result[:120])
    return None
