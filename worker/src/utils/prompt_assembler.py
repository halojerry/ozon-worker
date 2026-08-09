"""生图 prompt 组装工具（Wave 1 基础部分，纯新增，不修改 image_prompts.py / 模板）

职责：
- `extract_visual_vars_from_draft`: 确定性从 draft 提取视觉变量（无 LLM，零成本）
- `assemble_prompt`: 读 config/image_prompts.json 模板（热加载）渲染；
  任何失败回退 `image_prompts.get_image_prompt`（现有中文模板兜底，绝不抛异常）

模板占位符现状: {{title}} / {{scene_context}}（v0.16）。
material/color/size/weight/category 占位符由 Wave 1-C 模板增强加入；
extra 视觉变量（lighting/background/effects/atmosphere 等 LLM 值）由 Wave 2
透传到 Jinja2 render——模板含对应占位符则渲染，无则静默忽略（预期行为，不报错）。
"""
import logging

from jinja2 import Template

from utils.image_prompts import _DEFAULT_PROMPTS, _load_prompt_config, get_image_prompt

logger = logging.getLogger(__name__)

# draft.attributes 键候选（按优先级首个命中）
_MATERIAL_KEYS = ("材质", "材料", "material")
_COLOR_KEYS = ("颜色", "color")

# ⚠️ v0.32: 1688 属性值清洗上限（防脏值污染 prompt）
# 实测「X13桌面迷你风扇-黑色,X13桌面迷你风扇-白色,...」多选逗号串原样进 prompt
_ATTR_VALUE_MAX_LEN = 30
_ATTR_SPLIT_SEP = ("，", ",", "、", ";", "；")


def _clean_attr_value(raw: str) -> str:
    """清洗 1688 属性值：去空白、多选串取首项、长度截断。空 → "". """
    if not raw:
        return ""
    value = str(raw).strip()
    if not value:
        return ""
    # 多选串（逗号/顿号/分号分隔）→ 取首个片段（prompt 只描述主值，避免脏串）
    for sep in _ATTR_SPLIT_SEP:
        if sep in value:
            value = value.split(sep)[0].strip()
            break
    # 截断超长值（防货号前缀等噪声）
    if len(value) > _ATTR_VALUE_MAX_LEN:
        value = value[:_ATTR_VALUE_MAX_LEN].rstrip()
    return value


def _first_attr_value(attributes: dict, keys: tuple[str, ...]) -> str:
    """按候选键顺序取 attributes 首个命中的值（大小写不敏感）。缺失/空 → "".
    返回前经 _clean_attr_value 清洗（多选串/超长/空白）。
    """
    if not isinstance(attributes, dict):
        return ""
    lowered = {str(k).lower(): v for k, v in attributes.items()}
    for key in keys:
        value = lowered.get(key.lower())
        if value is None:
            continue
        if isinstance(value, (list, tuple)) and value:
            value = value[0]
        return _clean_attr_value(value)
    return ""


def extract_visual_vars_from_draft(draft: dict) -> dict:
    """确定性从 draft 提取视觉变量（无 LLM）。返回 {material, size, weight, category}。

    ⚠️ v0.32: color 已移除——参考图已含产品真实颜色，prompt 注入颜色（尤其
    1688 多选逗号串脏值）反而误导生图。颜色由参考图 + 模板承担。

    - material ← draft.attributes 键「材质/材料/material」（首个命中）
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
        "size": size,
        "weight": weight,
        "category": draft.get("category", ""),
    }


def merge_visual_vars(draft: dict, llm_vars: dict) -> dict:
    """合并视觉变量 3 源（Wave 2 接线）: 确定性 extract（低优先） + LLM 19 变量（高优先）。

    - LLM 值非空字符串时覆盖确定性值；空串/空白/None/非 dict 一律忽略
      （不产生空占位符残留）
    - 返回 {material, size, weight, category, ...LLM 扩展变量}
    """
    merged = extract_visual_vars_from_draft(draft)
    if isinstance(llm_vars, dict):
        merged.update(
            {k: v for k, v in llm_vars.items() if isinstance(v, str) and v.strip()}
        )
    return merged


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
    **extra,  # Wave 2 变量（lighting/background/effects/atmosphere/color_preset 等），透传模板渲染
) -> str:
    """组装生图 prompt。

    - 场景优先级: slot_scene_context > scene_context > 模板默认
    - 渲染: 读 config/image_prompts.json 模板（热加载，同 image_prompts.py 模式），
      把 title/scene_context/5 固定视觉变量 + extra 全部注入 Jinja2
      （模板含对应占位符才渲染，多余 kwargs 静默忽略）
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
        # Wave 2: extra 变量透传 Jinja2 render —— 模板含占位符则渲染，无则静默忽略
        render_kwargs.update({k: v for k, v in extra.items() if isinstance(v, str)})
        return Template(template).render(**render_kwargs)
    except Exception as e:
        logger.warning("prompt_assembler 渲染失败(key=%s): %s，回退 get_image_prompt", slot_key, e)
        return get_image_prompt(slot_key, title=title, scene_context=effective_scene)
