"""生图 prompt 组装工具（Wave 1 基础部分，纯新增，不修改 image_prompts.py / 模板）

职责：
- `extract_visual_vars_from_draft`: 确定性从 draft 提取视觉变量（无 LLM，零成本）
- `assemble_prompt`: 读 config/image_prompts.json 模板（热加载）渲染；
  任何失败回退 `image_prompts.get_image_prompt`（现有中文模板兜底，绝不抛异常）

模板占位符现状: {{title}} / {{scene_context}}（v0.16）。
material/color/size/weight/category 占位符由 Wave 1-C 模板增强加入——
模板不含时 Jinja2 静默忽略多余 kwargs（预期行为，不报错）。
extra 变量（model/action/lighting 等）为 Wave 2 预留，当前不透传到模板。
"""
import logging

from jinja2 import Template

from utils.image_prompts import _DEFAULT_PROMPTS, _load_prompt_config, get_image_prompt

logger = logging.getLogger(__name__)

# draft.attributes 键候选（按优先级首个命中）
_MATERIAL_KEYS = ("材质", "材料", "material")
_COLOR_KEYS = ("颜色", "color")


def _first_attr_value(attributes: dict, keys: tuple[str, ...]) -> str:
    """按候选键顺序取 attributes 首个命中的值（大小写不敏感）。缺失/空 → "". """
    if not isinstance(attributes, dict):
        return ""
    lowered = {str(k).lower(): v for k, v in attributes.items()}
    for key in keys:
        value = lowered.get(key.lower())
        if value is None:
            continue
        if isinstance(value, (list, tuple)) and value:
            value = value[0]
        return "" if value is None else str(value)
    return ""


def extract_visual_vars_from_draft(draft: dict) -> dict:
    """确定性从 draft 提取视觉变量（无 LLM）。返回 {material, color, size, weight, category}。

    - material ← draft.attributes 键「材质/材料/material」（首个命中）
    - color ← draft.attributes 键「颜色/color」（首个命中）
    - size ← draft.dimensions {length,width,height} mm → "120×80×60 mm"（缺任一维度→""）
    - weight ← draft.weight（int 克）→ "227 г"
    - category ← draft.category
    - 全部缺失 → ""（模板内置描述兜底，绝不产空产物）
    """
    draft = draft or {}
    attributes = draft.get("attributes") or {}
    dims = draft.get("dimensions") or {}

    size = ""
    if all(dims.get(k) is not None for k in ("length", "width", "height")):
        size = f"{dims['length']}×{dims['width']}×{dims['height']} mm"

    weight = ""
    if draft.get("weight") is not None:
        weight = f"{draft['weight']} г"

    return {
        "material": _first_attr_value(attributes, _MATERIAL_KEYS),
        "color": _first_attr_value(attributes, _COLOR_KEYS),
        "size": size,
        "weight": weight,
        "category": draft.get("category", ""),
    }


def assemble_prompt(
    slot_key: str,
    *,
    title: str = "",
    scene_context: str = "",
    slot_scene_context: str = "",
    material: str = "",
    color: str = "",
    size: str = "",
    weight: str = "",
    category: str = "",
    **extra,  # Wave 2 变量（model/action/lighting 等），当前不透传到模板
) -> str:
    """组装生图 prompt。

    - 场景优先级: slot_scene_context > scene_context > 模板默认
    - 渲染: 读 config/image_prompts.json 模板（热加载，同 image_prompts.py 模式），
      把 title/scene_context/material/color/size/weight/category 注入 Jinja2
    - 兜底: 任何失败 → 调用 image_prompts.get_image_prompt(slot_key, title=title,
      scene_context=有效场景)（现有中文模板兜底）
    - slot_scene_context 非空 → 作为 {{scene_context}} 渲染（scene_1/2/3 差异化）
    """
    effective_scene = slot_scene_context or scene_context
    try:
        template = _load_prompt_config().get(slot_key) or _DEFAULT_PROMPTS.get(slot_key, "")
        if not template:
            # 模板与默认值均缺失 → 交还 get_image_prompt 处理（告警 + 空串）
            return get_image_prompt(slot_key, title=title, scene_context=effective_scene)
        render_kwargs = {
            "title": title,
            "scene_context": effective_scene,
            "material": material,
            "color": color,
            "size": size,
            "weight": weight,
            "category": category,
        }
        return Template(template).render(**render_kwargs)
    except Exception as e:
        logger.warning("prompt_assembler 渲染失败(key=%s): %s，回退 get_image_prompt", slot_key, e)
        return get_image_prompt(slot_key, title=title, scene_context=effective_scene)
