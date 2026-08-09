"""视觉变量生成 LLM 节点 — 从 draft 文本推断 25 个视觉变量（英文 + 俄文内容）。

对抗定案（Wave 2 + v6 单阶段俄文生图模板）：
- 纯文本输入：deepseek-v4-flash 无视觉（F8 实证），绝不把 images 传给 LLM
- 2 层容错 JSON 解析（镜像 scene_generation_llm_node）：json.loads → 正则/文本提取
- 失败回退 extract_visual_vars_from_draft + 品类默认，绝不阻断生图
- AC-1 重写：9 必填非空 + 16 可选默认化（不是全部 25 必须非空）
- v6 扩展：REQUIRED + headline_style、OPTIONAL + 5 俄文变量（product_ru/cta_ru/
  selling_points_ru/effect_data_ru/target_ru）→ ALL_KEYS=25；brand_primary/accent 为
  确定性产出（来自 color_preset 的 get_preset_colors，不进 ALL_KEYS，LLM 不可覆盖）；
  输出 visual_vars 绝不含 color_preset key（防 assemble_prompt **_vv, color_preset=_cp 碰撞）
- SCENE 变量是全局兜底，scene_1/2/3 节点仍用各自 scene_context_N（不覆盖）
"""
import json
import os
import re
from typing import Dict, Any, Optional

from jinja2 import Template
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from runtime.context import Context

from graphs.state import VisualVarsInput, VisualVarsOutput
from utils.color_preset import get_preset_colors, resolve_color_preset
from utils.progress_logger import ProgressLogger
from utils.mxou_llm import call_mxou_chat_api
from utils.mxou_api import clean_title_for_image_prompt
from utils.prompt_assembler import _resolve_category_for_prompt, extract_visual_vars_from_draft

# 25 个视觉变量 key（PRD §2.2/§7.1 + v6 单阶段俄文生图模板）
# v6 扩展: REQUIRED + headline_style（9 个）; OPTIONAL + 5 俄文变量（16 个）; ALL = 25
REQUIRED_KEYS = [
    "product", "color", "material", "appearance", "size",
    "lighting", "effects", "text_areas",
    "headline_style",
]
OPTIONAL_KEYS = [
    "model", "action", "scene", "background", "icons",
    "inset", "gift", "atmosphere", "packaging", "problem_scene", "comparison",
    "product_ru", "cta_ru", "selling_points_ru", "effect_data_ru", "target_ru",
]
ALL_KEYS = REQUIRED_KEYS + OPTIONAL_KEYS

# 通用兜底：9 必填全非空，16 可选给默认值（AC-1）
_GENERIC_VARS = {
    "product": "product",
    "color": "neutral colors",
    "material": "high-quality material",
    "appearance": "sleek modern design",
    "size": "compact size",
    "lighting": "soft natural studio lighting",
    "effects": "subtle soft glow",
    "text_areas": "clean negative space for text overlay",
    "headline_style": "EXCLAIM",
    "model": "",
    "action": "",
    "scene": "clean modern environment",
    "background": "soft neutral background",
    "icons": "",
    "inset": "",
    "gift": "",
    "atmosphere": "premium and cozy",
    "packaging": "",
    "problem_scene": "",
    "comparison": "",
    # v6 俄文内容变量：LLM 失败回退恒空串（绝不用中文 title 顶替，中文会污染俄文模板）
    "product_ru": "",
    "cta_ru": "",
    "selling_points_ru": "",
    "effect_data_ru": "",
    "target_ru": "",
}

# 品类默认（category 关键词命中 → 覆盖通用兜底）
_CATEGORY_DEFAULTS = {
    "宠物": {"scene": "cozy home interior with a happy pet", "atmosphere": "warm and friendly", "lighting": "warm natural home lighting"},
    "美妆": {"scene": "modern vanity with soft bokeh", "lighting": "dramatic beauty lighting with soft shadows", "atmosphere": "premium and elegant"},
    "美容": {"scene": "modern vanity with soft bokeh", "lighting": "dramatic beauty lighting with soft shadows", "atmosphere": "premium and elegant"},
    "护肤": {"scene": "modern vanity with soft bokeh", "lighting": "soft bright beauty lighting", "atmosphere": "clean and premium"},
    "母婴": {"scene": "bright nursery room", "lighting": "soft bright daylight", "atmosphere": "gentle and safe"},
    "婴儿": {"scene": "bright nursery room", "lighting": "soft bright daylight", "atmosphere": "gentle and safe"},
    "儿童": {"scene": "bright playful room", "lighting": "soft bright daylight", "atmosphere": "fun and safe"},
    "家居": {"scene": "cozy modern living room", "lighting": "soft warm ambient lighting", "atmosphere": "cozy and premium"},
    "收纳": {"scene": "cozy modern living room", "lighting": "soft warm ambient lighting", "atmosphere": "cozy and premium"},
    "电子": {"scene": "futuristic tech studio", "lighting": "cool blue accent lighting", "atmosphere": "modern and high-tech"},
    "数码": {"scene": "futuristic tech studio", "lighting": "cool blue accent lighting", "atmosphere": "modern and high-tech"},
    "充电": {"scene": "futuristic tech studio", "lighting": "cool blue accent lighting", "atmosphere": "modern and high-tech"},
    "园艺": {"scene": "sunny garden with green plants", "lighting": "bright natural sunlight", "atmosphere": "fresh and natural"},
    "植物": {"scene": "sunny garden with green plants", "lighting": "bright natural sunlight", "atmosphere": "fresh and natural"},
    "清洁": {"scene": "clean minimal interior", "lighting": "bright even lighting", "atmosphere": "clean and efficient"},
    "户外": {"scene": "open outdoor nature setting", "lighting": "bright natural daylight", "atmosphere": "fresh and energetic"},
}
_CATEGORY_KEYWORDS = ("宠物", "美妆", "美容", "护肤", "母婴", "婴儿", "儿童", "家居", "收纳",
                      "电子", "数码", "充电", "园艺", "植物", "清洁", "户外")


def _category_defaults(category: str) -> Dict[str, str]:
    """按 category 关键词命中品类默认（未命中 → {}）。"""
    if not category:
        return {}
    for keyword in _CATEGORY_KEYWORDS:
        if keyword in category:
            return _CATEGORY_DEFAULTS[keyword]
    return {}


def _build_fallback_vars(draft: dict, scene_contexts: dict | None = None) -> Dict[str, str]:
    """确定性回退：extract_visual_vars_from_draft + 品类默认，25 key 全覆盖。

    - material/color ← draft.attributes（中文键直提）
    - size ← draft.dimensions(mm) + weight(g)
    - product ← draft.title
    - scene/lighting/atmosphere ← 品类默认
    - headline_style ← 默认 EXCLAIM；5 个 RU key ← ""（不用中文顶替）
    - brand_primary/accent ← color_preset 确定性路由（不进 ALL_KEYS，LLM 不可覆盖）
    - scene_1/2/3 ← scene_contexts 确定性透传（dict: scene_N → 中文场景文案，不翻译不重写；
      不进 ALL_KEYS，LLM 不可覆盖）；context 为空 → ""（模板 {% if %} 守卫处理）
    - 输出绝不含 color_preset key（防 assemble_prompt **_vv, color_preset=_cp 碰撞）
    - 其余 9 必填用通用英文默认（保证非空），16 可选默认化
    """
    draft = draft or {}
    extracted = extract_visual_vars_from_draft(draft)

    base = dict(_GENERIC_VARS)
    base.update(_category_defaults(extracted.get("category", "")))

    title = str(draft.get("title", "") or "").strip()
    if title:
        base["product"] = clean_title_for_image_prompt(title) or title
    color = extracted.get("color", "")
    if color:
        base["color"] = color
    material = extracted.get("material", "")
    if material:
        base["material"] = material

    size = extracted.get("size", "")
    weight = extracted.get("weight", "")
    if size and weight:
        base["size"] = f"{size}, {weight}"
    elif weight:
        base["size"] = weight
    elif size:
        base["size"] = size

    # ⚠️ v0.32 修复: 回退输出携带 category/weight（生图 prompt 组装消费，不再恒空）
    base["category"] = extracted.get("category", "")
    base["weight"] = extracted.get("weight", "")

    # ⚠️ v6: brand_primary/accent 为确定性产出（LLM 不可覆盖），来自 color_preset 预设路由
    _colors = get_preset_colors(resolve_color_preset(extracted.get("category", "")))
    base["brand_primary"] = _colors["primary"]
    base["accent"] = _colors["accent"]

    # ⚠️ v8: scene_1/2/3 为确定性透传（LLM 不可覆盖）——scene_contexts dict: scene_N → 中文场景文案
    scene_contexts = scene_contexts or {}
    for _n in (1, 2, 3):
        base[f"scene_{_n}"] = (scene_contexts.get(f"scene_{_n}") or "").strip()
    return base


def _try_loads(text: str) -> Optional[dict]:
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except (json.JSONDecodeError, ValueError):
        return None


def _dict_from_obj(obj: dict) -> Dict[str, str]:
    return {
        key: str(value).strip()
        for key, value in obj.items()
        if key in ALL_KEYS and value is not None and str(value).strip()
    }


def _extract_by_regex(text: str) -> Dict[str, str]:
    """Layer 2：逐 key 正则提取 `"key": "value"` 或 `key: "value"`。"""
    result = {}
    for key in ALL_KEYS:
        match = re.search(rf'["\']?{key}["\']?\s*[:：]\s*"([^"]*)"', text, re.IGNORECASE)
        if match:
            result[key] = match.group(1).strip()
    return result


def _parse_visual_vars(content: str) -> Dict[str, str]:
    """2 层容错解析（镜像 scene_generation_llm_node:69-88 范式）：
    json.loads → 失败时正则/文本提取；额外处理 markdown 代码围栏。"""
    text = (content or "").strip()
    if not text:
        return {}

    obj = _try_loads(text)
    if obj is not None:
        return _dict_from_obj(obj)

    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fenced:
        obj = _try_loads(fenced.group(1).strip())
        if obj is not None:
            return _dict_from_obj(obj)

    return _extract_by_regex(text)


def _merge_parsed(parsed: Dict[str, str], draft: dict, scene_contexts: dict | None = None) -> Dict[str, str]:
    """LLM 结果覆盖在确定性回退之上；空值不覆盖（保证 8 必填非空）。

    scene_N 不在 ALL_KEYS → 上方 ALL_KEYS 白名单过滤自动丢弃 LLM 返回的 scene_N，
    scene_1/2/3 由 scene_contexts 透传（LLM 不可覆盖）。
    """
    result = _build_fallback_vars(draft, scene_contexts)
    if isinstance(parsed, dict):
        for key, value in parsed.items():
            if key in ALL_KEYS and isinstance(value, str) and value.strip():
                result[key] = value.strip()
    return result


def _format_attributes(attributes: dict, limit: int = 30) -> str:
    """attributes dict → 纯文本行（限长，防超 token）。"""
    if not isinstance(attributes, dict):
        return ""
    lines = []
    for key, value in attributes.items():
        if isinstance(value, (list, tuple)) and value:
            value = value[0]
        lines.append(f"{key}: {value}")
    return "\n".join(lines[:limit])


_DEFAULT_SP = (
    "You are an expert e-commerce product photography art director. "
    "Given product text data, infer visual variables as a JSON object "
    "for Ozon marketplace AI image generation prompts. "
    "Source-language preservation: material, size, weight and category are provided "
    "verbatim in the source data (often Chinese) and are already accurate — copy them "
    "verbatim into the corresponding output values, NEVER rewrite, translate, or summarize them. "
    "Required keys (values MUST be non-empty): product, color, material, appearance, size, "
    "lighting, effects, text_areas, headline_style "
    "(material and size are copied verbatim from the source data; the other required values are English). "
    "Optional keys with sensible English defaults: model, action, scene, background, icons, "
    "inset, gift, atmosphere, packaging, problem_scene, comparison. "
    "headline_style is a 6-way choice for the on-image headline, pick exactly one per category: "
    "EXCLAIM (tech products), PROMISE (tools), NUMBER (spec-heavy products), "
    "CONTRAST (pain-point products), QUESTION (comparison products), "
    "TWO_LINE_TWO_COLOR (high-impact products). "
    "Generate 5 Russian content variables in Cyrillic (never Latin or CJK), derived from the product data: "
    "product_ru = short Russian product name ≤40 chars (on-image subtitle); "
    "cta_ru = short Russian call-to-action headline, 2-4 words in CAPS (e.g. НАДЁЖНАЯ защита); "
    "selling_points_ru = 3-4 Russian selling points from attributes/description, semicolon-separated, each ≤3 words; "
    "effect_data_ru = 1-3 numeric effect data points from numeric attributes (e.g. 45 минут; до 20 м²; 120 штук), semicolon-separated; "
    "target_ru = Russian target objects/categories, semicolon-separated. "
    "Output ONLY a JSON object with exactly these 25 keys "
    "(19 visual variables + headline_style + 5 Russian variables), no markdown, no extra text."
)
_DEFAULT_UP = (
    "Product title: {{title}}\nProduct description: {{description}}\n"
    "Product category: {{category}}\nProduct attributes:\n{{attributes}}\n"
    "Product size: {{size}}\nProduct weight: {{weight}}\n"
    "Scene context hint: {{scene_context}}\n\n"
    "Return the JSON object of the 25 variables (English visual + Russian content, no markdown)."
)


def visual_vars_llm_node(state: VisualVarsInput, config: RunnableConfig, runtime: Runtime[Context]) -> VisualVarsOutput:
    """
    title: 视觉变量生成LLM节点
    desc: 使用deepseek-V4-flash模型，从draft文本推断25个视觉变量（英文视觉 + 俄文内容），失败回退确定性提取
    integrations: api.mxou.cn LLM (deepseek-v4-flash)
    """
    ctx = runtime.context

    progress = ProgressLogger()
    progress.log_node_start("visual_vars_llm_node", "视觉变量生成LLM节点")

    cfg_file = os.path.join(os.getenv("APP_WORKSPACE_PATH"), config['metadata']['llm_cfg'])
    _cfg = {}
    try:
        with open(cfg_file, 'r') as fd:
            _cfg = json.load(fd)
    except Exception:
        progress.log_node_error("LLM配置读取失败，使用内置默认提示词", "检查 config/visual_vars_llm_cfg.json")

    llm_config = _cfg.get("config", {})
    sp = _cfg.get("sp", _DEFAULT_SP)
    up = _cfg.get("up", _DEFAULT_UP)

    draft = state.draft if isinstance(state.draft, dict) else {}
    title = clean_title_for_image_prompt(draft.get("title", "")) or state.title or ""
    description = draft.get("description", "") or state.description or ""
    # ⚠️ v0.32 修复: draft.category → state.category_name（assemble 回填的中文末级名）→ state.category(str) → ""
    category = _resolve_category_for_prompt(
        draft, getattr(state, "category_name", "")
    )
    if not category and isinstance(state.category, str) and state.category.strip():
        category = state.category.strip()
    attributes = draft.get("attributes") or state.attributes or {}

    # 纯文本输入（F8 实证：deepseek-v4-flash 无视觉，不传 images）
    extracted = extract_visual_vars_from_draft(draft)
    user_prompt = Template(up).render({
        "title": title,
        "description": description,
        "category": category,
        "attributes": _format_attributes(attributes),
        "size": extracted.get("size", ""),
        "weight": extracted.get("weight", ""),
        "scene_context": state.scene_context_1 or "",
    })

    parsed: Dict[str, str] = {}
    try:
        content = call_mxou_chat_api(
            token=state.token,
            system_prompt=sp,
            user_prompt=user_prompt,
            model=llm_config.get("model", "deepseek-v4-flash"),
            temperature=llm_config.get("temperature", 0.7),
            max_tokens=llm_config.get("max_tokens", 4096),
        )
        if content and content.strip():
            parsed = _parse_visual_vars(content)
    except Exception as e:
        progress.log_node_error(f"LLM 调用失败: {e}", "回退 extract_visual_vars_from_draft + 品类默认")

    # ⚠️ v8: scene_context_N → scene_N 确定性透传（LLM 不可覆盖，不翻译不重写）
    scene_contexts = {
        "scene_1": getattr(state, "scene_context_1", ""),
        "scene_2": getattr(state, "scene_context_2", ""),
        "scene_3": getattr(state, "scene_context_3", ""),
    }
    return VisualVarsOutput(visual_vars=_merge_parsed(parsed, draft, scene_contexts))
