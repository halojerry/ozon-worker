# -*- coding: utf-8 -*-
"""
v0.32 Wave 1 — 生图流程优化基础部分：prompt_assembler 工具函数（纯新增）。

覆盖：
(a) 10 个 slot_key 渲染出非空 prompt（title 注入生效）
(b) slot_scene_context 覆盖 scene_context（scene_1/2/3 差异化）
(c) material/color/size/weight/category 注入（模板含占位符时渲染进 prompt）
(d) 非视觉 extra 变量（model/action）静默忽略（不报错、不注入）；
    visual vars 在增强后的真实模板中渲染进 prompt
(e) 缺失变量 → 无 {{ 残留、无 None/Undefined 字符串
(f) assemble_prompt 失败 → 回退 get_image_prompt 中文兜底
(g) extract_visual_vars_from_draft 单测：中文键命中 / mm 拼接 / 缺失→""
(h) Wave 1-C 真实模板增强：draft visual vars 渲染进真实模板 / 缺失无残留 / title 首句

运行：cd worker && PYTHONPATH=src python3 -m pytest tests/test_prompt_assembler.py -v
      cd worker && PYTHONPATH=src python3 tests/test_prompt_assembler.py
"""
import json
import os
import shutil
import sys
import tempfile
from contextlib import contextmanager

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from utils.prompt_assembler import (  # noqa: E402
    _resolve_category_for_prompt,
    assemble_prompt,
    extract_visual_vars_from_draft,
    merge_visual_vars,
)

# 10 个生图节点 slot_key（与 image_prompts.json / _DEFAULT_PROMPTS 对齐）
SLOT_KEYS = [
    "main", "white_bg", "multi_angle",
    "scene_1", "scene_2", "scene_3",
    "comparison", "detail", "social_proof", "variant_white_bg",
]

# 含全部 visual vars 占位符的自定义模板（模拟 Wave 1-C 模板增强后的形态）
# ⚠️ v0.32: color 占位符已移除（参考图承担颜色）
_CUSTOM_TEMPLATE = {
    "main": (
        "产品：{{title}}。材质：{{material}}。"
        "尺寸：{{size}}。重量：{{weight}}。类目：{{category}}。"
    ),
}


@contextmanager
def _real_workspace():
    """指向 worker/ 目录（读真实 config/image_prompts.json，不依赖调用方 cwd）"""
    old = os.environ.get("APP_WORKSPACE_PATH")
    try:
        os.environ["APP_WORKSPACE_PATH"] = os.path.join(os.path.dirname(__file__), "..")
        yield
    finally:
        if old is None:
            os.environ.pop("APP_WORKSPACE_PATH", None)
        else:
            os.environ["APP_WORKSPACE_PATH"] = old


@contextmanager
def _fake_workspace(prompts=None, corrupt=False, no_config=False):
    """临时 APP_WORKSPACE_PATH，可注入自定义提示词 JSON（prompts dict 或损坏/缺失）"""
    tmp = tempfile.mkdtemp(prefix="prompt_asm_test_")
    old_workspace = os.environ.get("APP_WORKSPACE_PATH")
    try:
        if not no_config:
            cfg_dir = os.path.join(tmp, "config")
            os.makedirs(cfg_dir, exist_ok=True)
            if corrupt:
                with open(os.path.join(cfg_dir, "image_prompts.json"), "w", encoding="utf-8") as fd:
                    fd.write("{ 这不是合法 JSON !!!")
            elif prompts is not None:
                with open(os.path.join(cfg_dir, "image_prompts.json"), "w", encoding="utf-8") as fd:
                    json.dump(prompts, fd, ensure_ascii=False)
        os.environ["APP_WORKSPACE_PATH"] = tmp
        yield tmp
    finally:
        if old_workspace is None:
            os.environ.pop("APP_WORKSPACE_PATH", None)
        else:
            os.environ["APP_WORKSPACE_PATH"] = old_workspace
        shutil.rmtree(tmp, ignore_errors=True)


# ── (a) 10 个 slot_key 渲染非空 + title 注入 ──
def test_all_10_slots_render_nonempty():
    for key in SLOT_KEYS:
        with _real_workspace():
            p = assemble_prompt(key, title="产品A")
            assert isinstance(p, str) and p, f"slot {key} 渲染结果为空"
            assert "产品A" in p, f"slot {key} 未注入 title"
            assert "{{" not in p, f"slot {key} 残留未渲染占位符"


# ── (b) slot_scene_context 覆盖 scene_context ──
def test_slot_scene_context_overrides_scene_context():
    with _real_workspace():
        p = assemble_prompt(
            "scene_1",
            title="产品A",
            scene_context="全局场景描述",
            slot_scene_context="局部特写描述",
        )
        assert "局部特写描述" in p
        assert "全局场景描述" not in p


def test_scene_context_used_when_no_slot_override():
    with _real_workspace():
        p = assemble_prompt("scene_2", title="产品A", scene_context="全局场景描述")
        assert "全局场景描述" in p


# ── (c) visual vars 注入（模板含占位符时）──
def test_visual_vars_rendered_when_placeholders_exist():
    with _fake_workspace(prompts=_CUSTOM_TEMPLATE):
        p = assemble_prompt(
            "main",
            title="保温杯",
            material="不锈钢",
            size="120×80×60 mm",
            weight="227 г",
            category="水具",
        )
        for token in ("保温杯", "不锈钢", "120×80×60 mm", "227 г", "水具"):
            assert token in p, f"变量 {token!r} 未渲染进 prompt"
        assert "{{" not in p


# ── (d) 非视觉 extra 变量静默忽略（visual vars 注入增强后的真实模板）──
def test_extra_vars_silently_ignored_without_placeholders():
    # Wave 1-C 后: visual vars 已含占位符 → 注入；model/action 无占位符 → 静默忽略
    with _real_workspace():
        p = assemble_prompt(
            "main",
            title="保温杯",
            material="ABS",
            size="100×60×60 mm",
            weight="200 г",
            category="水具",
            model="cinematic",
            action="产品特写",
        )
        assert "保温杯" in p
        assert "ABS" in p, "visual var（material）应注入增强后的模板"
        assert "cinematic" not in p, "extra 变量（model）不应注入"
        assert "产品特写" not in p, "extra 变量（action）不应注入"
        assert "{{" not in p


# ── (h) Wave 1-C 真实模板增强：draft visual vars 渲染进真实模板 ──
def test_real_template_renders_draft_visual_vars():
    """含 material 的 draft → 增强后的真实 main 模板渲染出「ABS塑料」（v0.32: color 已移除）"""
    draft = {
        "attributes": {"材质": "ABS塑料", "颜色": "白色"},
        "dimensions": {"length": 120, "width": 80, "height": 60},
        "weight": 227,
        "category": "水具",
    }
    with _real_workspace():
        p = assemble_prompt("main", title="保温杯", **extract_visual_vars_from_draft(draft))
        assert "ABS塑料" in p, "材质未渲染进 prompt"
        assert "120×80×60 mm" in p, "尺寸未渲染进 prompt"
        assert "227 г" in p, "重量未渲染进 prompt"
        assert "水具" in p, "类目未渲染进 prompt"
        assert "白色" not in p, "v0.32: color 不再注入（参考图承担颜色）"
        assert "{{" not in p


def test_draft_without_visual_vars_no_residue():
    """无 visual vars 的 draft → 无空占位符残留、无 {{ / None / Undefined"""
    draft = {"title": "保温杯"}
    with _real_workspace():
        p = assemble_prompt("main", title="保温杯", **extract_visual_vars_from_draft(draft))
        assert "{{" not in p, "存在未渲染占位符"
        assert "None" not in p
        assert "Undefined" not in p
        assert "保温杯" in p


def test_title_stays_first_sentence_with_visual_vars():
    """增强模板下 title 仍为首句（位于材质/尺寸描述之前）"""
    with _real_workspace():
        p = assemble_prompt("main", title="保温杯", material="ABS塑料", size="100×60×60 mm")
        assert p.startswith("产品：保温杯"), f"title 非首句: {p[:30]!r}"
        assert p.index("保温杯") < p.index("ABS塑料")


# ── (e) 缺失变量 → 无 {{ 残留、无 None/Undefined 字符串 ──
def test_missing_vars_no_residue_or_none():
    with _real_workspace():
        p = assemble_prompt("main")
        assert "{{" not in p
        assert "None" not in p
        assert "Undefined" not in p


def test_missing_vars_render_empty_not_none():
    # 模板含占位符但变量缺省 → 渲染为空串而非 "None"
    with _fake_workspace(prompts=_CUSTOM_TEMPLATE):
        p = assemble_prompt("main", title="保温杯")
        assert "材质：" in p
        assert "None" not in p
        assert "Undefined" not in p
        assert "{{" not in p


# ── (f) 失败 → 回退 get_image_prompt 中文兜底（绝不抛异常）──
def test_corrupt_config_falls_back_to_default():
    with _fake_workspace(corrupt=True):
        p = assemble_prompt("main", title="雨衣")
        assert "雨衣" in p
        assert "营销主图" in p  # 默认中文提示词内容


def test_render_failure_falls_back_to_get_image_prompt():
    # 非法 Jinja2 filter → assemble_prompt 渲染失败 → 回退 get_image_prompt 中文兜底（不抛异常）。
    # get_image_prompt 对同一坏模板自身也回退默认模板原文（不重渲染），故断言回退产物而非 title 注入。
    with _fake_workspace(prompts={"main": "产品：{{title | no_such_filter}} 主图"}):
        p = assemble_prompt("main", title="花洒")
        assert p, "回退产物为空"
        assert "no_such_filter" not in p, "仍含坏模板"
        assert "营销主图" in p, "回退产物应为默认中文提示词"


def test_missing_config_file_falls_back():
    with _fake_workspace(no_config=True):
        p = assemble_prompt("detail", title="手电筒")
        assert "手电筒" in p
        assert "微距细节特写图" in p


def test_unknown_slot_returns_empty_gracefully():
    with _fake_workspace(no_config=True):
        p = assemble_prompt("not_exist_key_xyz")
        assert p == ""


# ── (i) Wave 2: **extra 视觉变量透传 Jinja2 render ──

# 含 {{lighting}}/{{background}} 等 LLM 变量占位符的自定义模板（模拟 Wave 2 模板增强后的形态）
_LLM_TEMPLATE = {
    "main": (
        "产品：{{title}}。光线：{{lighting}}。背景：{{background}}。"
        "特效：{{effects}}。氛围：{{atmosphere}}。"
    ),
}


def test_extra_visual_vars_render_when_placeholders_exist():
    """extra={lighting:...} 且模板含 {{lighting}} → 渲染进 prompt"""
    with _fake_workspace(prompts=_LLM_TEMPLATE):
        p = assemble_prompt(
            "main", title="保温杯",
            lighting="soft studio light",
            background="cozy living room",
            effects="subtle soft glow",
            atmosphere="premium and cozy",
        )
        for token in ("保温杯", "soft studio light", "cozy living room", "subtle soft glow", "premium and cozy"):
            assert token in p, f"extra 变量 {token!r} 未渲染进 prompt"
        assert "{{" not in p


def test_extra_vars_silently_ignored_without_placeholders_wave2():
    """模板不含 {{lighting}} → extra lighting 被静默忽略（不报错、不注入）"""
    with _fake_workspace(prompts=_CUSTOM_TEMPLATE):  # _CUSTOM_TEMPLATE 无 lighting 占位符
        p = assemble_prompt("main", title="保温杯", lighting="studio light")
        assert "保温杯" in p
        assert "studio light" not in p, "extra 变量（lighting）不应注入无占位符模板"
        assert "{{" not in p


def test_real_template_renders_llm_visual_vars():
    """真实模板已加 {{lighting}}/{{background}}/{{effects}}/{{atmosphere}} → 传入即渲染"""
    with _real_workspace():
        p = assemble_prompt(
            "main", title="保温杯",
            lighting="warm golden hour light",
            background="cozy modern living room",
            atmosphere="premium and cozy",
        )
        for token in ("warm golden hour light", "cozy modern living room", "premium and cozy"):
            assert token in p, f"真实模板未渲染 extra 变量 {token!r}"
        assert "{{" not in p


def test_empty_extra_vars_no_residue():
    """LLM 值空串 → 模板有占位符但值为空 → 不产生 {{ 残留 / None（Jinja2 空串渲染）"""
    with _fake_workspace(prompts=_LLM_TEMPLATE):
        p = assemble_prompt("main", title="保温杯", lighting="", background="", effects="", atmosphere="")
        assert "{{" not in p
        assert "None" not in p
        assert "Undefined" not in p
        assert "保温杯" in p


# ── (g) extract_visual_vars_from_draft 单测 ──
def test_extract_material_color_size_weight_category():
    draft = {
        "attributes": {"材质": "ABS塑料", "颜色": "黑色"},
        "dimensions": {"length": 120, "width": 80, "height": 60},
        "weight": 227,
        "category": "宠物用品",
    }
    v = extract_visual_vars_from_draft(draft)
    # ⚠️ v0.32: color 已从生图变量移除（参考图承担颜色），不再提取
    assert v == {
        "material": "ABS塑料",
        "size": "120×80×60 mm",
        "weight": "227 г",
        "category": "宠物用品",
    }
    assert "color" not in v


def test_extract_first_hit_material_key():
    # 候选键顺序「材质/材料/material」——attributes 同时含两者时取首个命中
    draft = {"attributes": {"材料": "硅胶", "材质": "ABS"}}
    assert extract_visual_vars_from_draft(draft)["material"] == "ABS"


def test_extract_english_material_color():
    draft = {"attributes": {"Material": "Stainless Steel", "color": "Silver"}}
    v = extract_visual_vars_from_draft(draft)
    assert v["material"] == "Stainless Steel"
    assert "color" not in v


def test_extract_missing_dimension_skips_size():
    draft = {"dimensions": {"length": 120, "width": 80}}
    assert extract_visual_vars_from_draft(draft)["size"] == ""


def test_extract_empty_draft_all_empty():
    assert extract_visual_vars_from_draft({}) == {
        "material": "", "size": "", "weight": "", "category": "",
    }


def test_extract_attributes_missing_all_empty():
    draft = {"category": "宠物用品"}
    v = extract_visual_vars_from_draft(draft)
    assert v["material"] == ""
    assert "color" not in v
    assert v["category"] == "宠物用品"


# ── (i) v0.32 属性值清洗（多选逗号串/超长/空白）──
def test_extract_material_cleans_comma_multi_select():
    """1688 材质多选逗号串 → 取首个片段（防脏值污染 prompt）"""
    draft = {"attributes": {"材质": "ABS塑料,PP,PC"}}
    assert extract_visual_vars_from_draft(draft)["material"] == "ABS塑料"


def test_extract_material_cleans_chinese_comma_and_semicolon():
    """中文逗号/顿号/分号分隔 → 取首个片段"""
    for dirty in ("ABS塑料，PP", "ABS塑料、PP", "ABS塑料;PP"):
        draft = {"attributes": {"材质": dirty}}
        assert extract_visual_vars_from_draft(draft)["material"] == "ABS塑料", f"{dirty!r}"


def test_extract_material_truncates_very_long():
    """超长属性值（货号前缀等）→ 截断到 30 字符"""
    long_val = "X13桌面迷你风扇-黑色X13桌面迷你风扇-白色X13桌面迷你风扇-绿色超长值"
    draft = {"attributes": {"材质": long_val}}
    v = extract_visual_vars_from_draft(draft)["material"]
    assert len(v) <= 30, f"应截断到 30 字符，got {len(v)}"


def test_extract_material_strips_whitespace():
    """前后空白 → 清理"""
    draft = {"attributes": {"材质": "  ABS塑料  "}}
    assert extract_visual_vars_from_draft(draft)["material"] == "ABS塑料"


# ── (j) v0.32 修复: LLM 不得覆盖确定性提取的中文材质/尺寸/重量/类目 ──
def test_merge_llm_does_not_override_chinese_material():
    """draft 材质「ABS塑料」+ LLM material="Plastic" → merged 保持「ABS塑料」；
    创意变量 lighting 仍由 LLM 生效（LLM 只补创意，不覆盖确定性值）。"""
    draft = {"attributes": {"材质": "ABS塑料"}}
    merged = merge_visual_vars(draft, {"material": "Plastic", "lighting": "warm"})
    assert merged["material"] == "ABS塑料", "LLM 不应覆盖确定性提取的中文材质"
    assert merged["lighting"] == "warm", "创意变量（lighting）仍应由 LLM 生效"


def test_merge_carries_category_and_weight():
    """draft 含 category/weight → merged 原样保留（LLM 空值时）。"""
    merged = merge_visual_vars({"category": "文具", "weight": 227}, {})
    assert merged["category"] == "文具"
    assert merged["weight"] == "227 г"


def test_merge_state_category_name_fallback():
    """draft.category 空 + state_category_name 非空 → 兜底进 merged["category"]（防 {{category}} 恒空）。"""
    merged = merge_visual_vars({}, {}, state_category_name="文具收纳盒")
    assert merged["category"] == "文具收纳盒"


def test_merge_draft_category_prefers_over_state():
    """draft.category 非空 → state_category_name 不覆盖。"""
    merged = merge_visual_vars({"category": "文具"}, {}, state_category_name="儿童学习挂图")
    assert merged["category"] == "文具"


def test_merge_llm_does_not_override_deterministic_keys_at_all():
    """LLM 同时给 material/size/weight/category 英文值 → 全部被排除，保留确定性中文值。"""
    draft = {
        "attributes": {"材质": "ABS塑料"},
        "dimensions": {"length": 120, "width": 80, "height": 60},
        "weight": 227,
        "category": "水具",
    }
    merged = merge_visual_vars(
        draft,
        {"material": "Plastic", "size": "10x8x6 cm", "weight": "200 g", "category": "Drinkware"},
    )
    assert merged["material"] == "ABS塑料"
    assert merged["size"] == "120×80×60 mm"
    assert merged["weight"] == "227 г"
    assert merged["category"] == "水具"


# ── (k) v0.32 修复: 类目名解析（draft → state.category_name → ""）──
def test_resolve_category_prefers_draft_category():
    """draft.category 非空 → 直接返回，state_category_name 被忽略。"""
    assert _resolve_category_for_prompt({"category": "文具"}, state_category_name="文具收纳盒") == "文具"


def test_resolve_category_falls_back_to_state_name():
    """draft 无 category → 用 state.category_name（worker 类目匹配回填的末级名）。"""
    assert _resolve_category_for_prompt({}, state_category_name="文具收纳盒") == "文具收纳盒"


def test_resolve_category_empty_draft_category_falls_back_to_state_name():
    """draft.category 为空白字符串 → 仍回退 state.category_name。"""
    assert _resolve_category_for_prompt({"category": "   "}, state_category_name="文具收纳盒") == "文具收纳盒"


def test_resolve_category_all_missing_empty():
    """draft 与 state 均无类目 → ""。"""
    assert _resolve_category_for_prompt({}, state_category_name="") == ""


def test_resolve_category_ignores_non_string_draft_category():
    """旧信封 draft.category 可能是 dict（state.category 语义误入）→ 不当作类目名，回退 state。"""
    assert _resolve_category_for_prompt({"category": {"type_id": 1}}, state_category_name="收纳") == "收纳"


if __name__ == "__main__":
    import traceback

    tests = [
        test_all_10_slots_render_nonempty,
        test_slot_scene_context_overrides_scene_context,
        test_scene_context_used_when_no_slot_override,
        test_visual_vars_rendered_when_placeholders_exist,
        test_extra_vars_silently_ignored_without_placeholders,
        test_missing_vars_no_residue_or_none,
        test_missing_vars_render_empty_not_none,
        test_extra_vars_silently_ignored_without_placeholders,
        test_real_template_renders_draft_visual_vars,
        test_draft_without_visual_vars_no_residue,
        test_title_stays_first_sentence_with_visual_vars,
        test_corrupt_config_falls_back_to_default,
        test_render_failure_falls_back_to_get_image_prompt,
        test_missing_config_file_falls_back,
        test_unknown_slot_returns_empty_gracefully,
        test_extract_material_color_size_weight_category,
        test_extract_first_hit_material_key,
        test_extract_english_material_color,
        test_extract_missing_dimension_skips_size,
        test_extract_empty_draft_all_empty,
        test_extract_attributes_missing_all_empty,
        test_merge_llm_does_not_override_chinese_material,
        test_merge_carries_category_and_weight,
        test_merge_llm_does_not_override_deterministic_keys_at_all,
        test_resolve_category_prefers_draft_category,
        test_resolve_category_falls_back_to_state_name,
        test_resolve_category_empty_draft_category_falls_back_to_state_name,
        test_resolve_category_all_missing_empty,
        test_resolve_category_ignores_non_string_draft_category,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            print(f"  ✅ {t.__name__}")
            passed += 1
        except Exception:
            print(f"  ❌ {t.__name__}")
            traceback.print_exc()
    print(f"\n{passed}/{len(tests)} 通过")
    sys.exit(0 if passed == len(tests) else 1)
