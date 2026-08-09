# -*- coding: utf-8 -*-
"""
v0.32 Wave 2 — visual_vars_llm_node 视觉变量生成 LLM 节点。

对抗定案（TDD RED 先行）：
(a) LLM 返回合法 JSON → 解析出全部 25 个视觉变量（AC-1: 9 必填非空 + 16 可选默认化）
(b) LLM 返回坏 JSON / markdown 包裹 → 容错解析（镜像 scene_generation_llm 2 层范式）
(c) LLM 返回部分字段 → 缺失字段回退 extract/默认（不报错，9 必填仍非空）
(d) LLM 调用失败（异常/返回 None）→ 回退 extract_visual_vars_from_draft + 品类默认，不阻断
(e) 纯文本输入（F8 实证 deepseek-v4-flash 无视觉）：mock 调用无 images 参数/无图 URL
(f) v6 单阶段俄文生图模板: REQUIRED +headline_style、OPTIONAL +5 俄文变量（product_ru/
    cta_ru/selling_points_ru/effect_data_ru/target_ru）→ ALL_KEYS=25；brand_primary/accent
    为确定性产出（来自 color_preset 的 get_preset_colors，不进 ALL_KEYS，LLM 不可覆盖）；
    输出 visual_vars 绝不含 color_preset key（防 assemble_prompt **_vv, color_preset=_cp 碰撞）

运行：cd worker && PYTHONPATH=src python3 -m pytest tests/test_visual_vars_llm_node.py -v
      cd worker && PYTHONPATH=src python3 tests/test_visual_vars_llm_node.py
"""
import json
import os
import sys
from contextlib import contextmanager
from unittest.mock import patch

os.environ.setdefault("GRSAI_API_KEY", "test-key")
os.environ.setdefault("LOG_FORMAT", "text")
os.environ.setdefault("LOG_LEVEL", "WARNING")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from graphs.nodes.visual_vars_llm_node import visual_vars_llm_node  # noqa: E402
from graphs.state import VisualVarsInput  # noqa: E402

import graphs.nodes.visual_vars_llm_node as _node_mod  # noqa: E402
from graphs.nodes.visual_vars_llm_node import (  # noqa: E402
    _DEFAULT_SP,
    _build_fallback_vars,
)
from utils.color_preset import get_preset_colors, resolve_color_preset  # noqa: E402

# ── 25 个视觉变量 key（PRD §2.2/§7.1 + v6 单阶段俄文生图模板）──
# v6 扩展: REQUIRED + headline_style（9 个）; OPTIONAL + 5 俄文变量（16 个）; ALL = 25
# 确定性产出（不进 ALL_KEYS，LLM 不可覆盖）: brand_primary/accent（HEX，来自 color_preset）
REQUIRED_KEYS = [
    "product", "color", "material", "appearance", "size",
    "lighting", "effects", "text_areas",
    "headline_style",
]
RU_KEYS = ["product_ru", "cta_ru", "selling_points_ru", "effect_data_ru", "target_ru"]
OPTIONAL_KEYS = [
    "model", "action", "scene", "background", "icons",
    "inset", "gift", "atmosphere", "packaging", "problem_scene", "comparison",
] + RU_KEYS
ALL_KEYS = REQUIRED_KEYS + OPTIONAL_KEYS

# 完整合法的 25 变量 JSON（PRD §5.2.1 示例英文 + v6 驱蚊香示例俄文）
VALID_JSON = {
    "product": "premium blue and rose-gold IPL photo epilator, sleek ergonomic body",
    "color": "navy blue + rose gold",
    "material": "smooth ABS plastic",
    "appearance": "compact handle with LED flash window",
    "size": "15x5x3 cm",
    "model": "young blonde woman with glowing skin",
    "action": "gliding the epilator on her forearm with bright flash of light",
    "scene": "modern bathroom vanity with soft bokeh",
    "background": "deep navy blue with subtle bokeh highlights",
    "lighting": "dramatic beauty lighting with cool blue rim light",
    "effects": "bright white flash, subtle lens flare, soft glow",
    "text_areas": "top-left headline band + upper right circular badges",
    "icons": "three circular icons: cooling mode, unlimited flashes, wireless",
    "inset": "small inset showing razor and sunglasses gift accessories",
    "gift": "bonus accessories: safety razor and protective glasses",
    "atmosphere": "premium / cozy",
    "packaging": "black-blue box with gold logo",
    "problem_scene": "left side: irritated skin with visible stubble",
    "comparison": "right side: smooth skin after IPL treatment",
    # ── v6 单阶段俄文生图模板新增（参照 v6 驱蚊香示例）──
    "headline_style": "EXCLAIM",
    "product_ru": "ПАЛОЧКИ от комаров",
    "cta_ru": "НАДЁЖНАЯ защита",
    "selling_points_ru": "длительная защита; быстрый результат",
    "effect_data_ru": "45 минут; 120 штук",
    "target_ru": "комары; мухи",
}

# 最小 draft（含可确定性提取的 material/color/size/weight/category）
DRAFT = {
    "title": "冰点光子嫩肤脱毛仪",
    "description": "家用 IPL 脱毛仪，冰感嫩肤，无痛脱毛。",
    "category": "美妆护肤",
    "attributes": {"材质": "ABS塑料", "颜色": "白色", "电源方式": "充电式"},
    "dimensions": {"length": 150, "width": 50, "height": 30},
    "weight": 227,
    "images": ["https://example.com/product_1.jpg", "https://example.com/product_2.jpg"],
}

_CFG = {"metadata": {"execute_id": "test-run", "llm_cfg": "config/visual_vars_llm_cfg.json"}}
_RUNTIME = type("FakeRuntime", (), {"context": None})()


@contextmanager
def _workspace():
    """指向 worker/（读真实 config/visual_vars_llm_cfg.json，不依赖调用方 cwd）"""
    old = os.environ.get("APP_WORKSPACE_PATH")
    try:
        os.environ["APP_WORKSPACE_PATH"] = os.path.join(os.path.dirname(__file__), "..")
        yield
    finally:
        if old is None:
            os.environ.pop("APP_WORKSPACE_PATH", None)
        else:
            os.environ["APP_WORKSPACE_PATH"] = old


def _run_node(draft=None, llm_content=None, llm_exc=None, scene_context_1=""):
    """执行节点，mock 模块内绑定的 call_mxou_chat_api。返回 (output, captured_kwargs)。"""
    captured = {}

    def _fake(token, system_prompt=None, user_prompt=None, **kwargs):
        captured["token"] = token
        captured["system_prompt"] = system_prompt
        captured["user_prompt"] = user_prompt
        captured.update(kwargs)
        if llm_exc is not None:
            raise llm_exc
        return llm_content

    state = VisualVarsInput(draft=draft or {}, token="sk-test", scene_context_1=scene_context_1)
    with _workspace(), patch.object(_node_mod, "call_mxou_chat_api", side_effect=_fake):
        out = visual_vars_llm_node(state, _CFG, _RUNTIME)
    return out, captured


# ── (a) LLM 返回合法 JSON → 25 变量解析 ──
def test_valid_json_parses_all_25_vars():
    out, _calls = _run_node(draft=DRAFT, llm_content=json.dumps(VALID_JSON))
    vv = out.visual_vars
    assert set(ALL_KEYS) <= set(vv.keys()), f"缺失 key: {set(ALL_KEYS) - set(vv.keys())}"
    for key in ALL_KEYS:
        assert vv[key] == VALID_JSON[key], f"{key} 未从 LLM 结果正确解析"


# ── (b) 坏 JSON / markdown 包裹 → 容错解析 ──
def test_markdown_wrapped_json_tolerated():
    content = "Here is the result:\n```json\n" + json.dumps(VALID_JSON) + "\n```"
    out, _calls = _run_node(draft=DRAFT, llm_content=content)
    vv = out.visual_vars
    assert set(ALL_KEYS) <= set(vv.keys())
    assert vv["product"] == VALID_JSON["product"]
    assert vv["lighting"] == VALID_JSON["lighting"]


def test_plain_text_lines_extracted_by_regex():
    # 非 JSON 文本 → Layer 2 逐 key 正则/文本提取
    lines = "\n".join(f'{key}: "{value}"' for key, value in VALID_JSON.items())
    out, _calls = _run_node(draft=DRAFT, llm_content="Variables:\n" + lines)
    vv = out.visual_vars
    for key in ALL_KEYS:
        assert vv.get(key) == VALID_JSON[key], f"正则提取 {key} 失败: {vv.get(key)!r}"


def test_truly_garbage_content_falls_back_not_crash():
    out, _calls = _run_node(draft=DRAFT, llm_content="完全没有 JSON 的废话文本 %%%")
    vv = out.visual_vars
    assert set(ALL_KEYS) <= set(vv.keys()), "垃圾内容应回退默认并覆盖全部 19 key"
    for key in REQUIRED_KEYS:
        assert vv[key], f"必填 {key} 在回退后仍为空"


# ── (c) LLM 返回部分字段 → 缺失回退 extract/默认（不报错）──
def test_partial_fields_fallback_to_extract_and_defaults():
    partial = {"product": "IPL photo epilator", "color": "white + rose gold"}
    out, _calls = _run_node(draft=DRAFT, llm_content=json.dumps(partial))
    vv = out.visual_vars
    assert set(ALL_KEYS) <= set(vv.keys())
    # LLM 提供的值保留
    assert vv["product"] == "IPL photo epilator"
    assert vv["color"] == "white + rose gold"
    # 缺失的必填 material/size 从 draft 确定性提取（extract_visual_vars_from_draft）
    assert vv["material"] == "ABS塑料", "material 应回退 draft.attributes[材质]"
    assert "150×50×30 mm" in vv["size"], "size 应回退 draft.dimensions"
    # 其余必填（appearance/lighting/effects/text_areas）回退默认，非空
    for key in ("appearance", "lighting", "effects", "text_areas"):
        assert vv[key], f"必填 {key} 回退后为空"
    # 可选缺失 → 默认化（不报错）
    for key in OPTIONAL_KEYS:
        assert key in vv


# ── (d) LLM 调用失败（异常 / None）→ 回退，不阻断 ──
def test_llm_exception_falls_back_and_does_not_crash():
    out, _calls = _run_node(draft=DRAFT, llm_exc=RuntimeError("api down"))
    vv = out.visual_vars
    assert set(ALL_KEYS) <= set(vv.keys())
    assert vv["material"] == "ABS塑料", "LLM 异常应回退 draft 材质"
    assert "150×50×30 mm" in vv["size"]
    for key in REQUIRED_KEYS:
        assert vv[key], f"必填 {key} 回退后为空"


def test_llm_returns_none_falls_back():
    out, _calls = _run_node(draft=DRAFT, llm_content=None)
    vv = out.visual_vars
    assert set(ALL_KEYS) <= set(vv.keys())
    # v0.32: color 已从生图变量移除（参考图承担颜色），fallback 用 neutral 默认
    assert vv["color"] == "neutral colors"
    for key in REQUIRED_KEYS:
        assert vv[key], f"必填 {key} 回退后为空"


def test_empty_draft_still_non_empty_required():
    # 极端：draft 为空 + LLM 失败 → 8 必填仍有通用英文默认（绝不阻断生图）
    out, _calls = _run_node(draft={}, llm_exc=ValueError("no token"))
    vv = out.visual_vars
    assert set(ALL_KEYS) <= set(vv.keys())
    for key in REQUIRED_KEYS:
        assert vv[key], f"空 draft 下必填 {key} 为空"


# ── (e) 纯文本输入：不传 images 给 LLM（F8 实证）──
def test_no_images_passed_to_llm():
    out, calls = _run_node(draft=DRAFT, llm_content=json.dumps(VALID_JSON))
    # mock 调用无 images/image/ref_images 参数
    for kw in ("images", "image", "ref_images"):
        assert kw not in calls, f"LLM 调用不应包含 {kw} 参数"
    # user_prompt 纯文本，不含图片 URL（F8: deepseek-v4-flash 无视觉）
    assert calls["user_prompt"] and isinstance(calls["user_prompt"], str)
    assert "https://example.com/product_1.jpg" not in calls["user_prompt"], \
        "图片 URL 不应出现在 LLM 文本 prompt 中"
    # 但 draft 数据（title/attributes/尺寸）应传入
    assert "冰点光子嫩肤脱毛仪" in calls["user_prompt"]
    assert "ABS塑料" in calls["user_prompt"]


# ── (f) v0.32 修复: 回退变量携带 category/weight + SP 不再强制必填变量全英文 ──
def test_fallback_vars_include_category_and_weight():
    """确定性回退输出必须携带 category/weight（生图 prompt 组装消费）。"""
    vv = _build_fallback_vars(DRAFT)
    assert vv["category"] == "美妆护肤", "回退输出应含 draft.category"
    assert vv["weight"] == "227 г", "回退输出应含 draft.weight（克 → г）"


def test_fallback_vars_empty_category_weight_when_draft_missing():
    """draft 无 category/weight → 回退输出中这两 key 为 ""（不缺失、不残留）。"""
    vv = _build_fallback_vars({})
    assert vv["category"] == ""
    assert vv["weight"] == ""


def test_sp_accepts_non_english_material():
    """_DEFAULT_SP 不再要求 material 等确定性值必须英文（source 中文原样保留）。"""
    assert "in English" not in _DEFAULT_SP, "SP 不应再要求所有必填变量必须为英文"
    low = _DEFAULT_SP.lower()
    assert "material" in low, "SP 仍应提及 material"
    assert "verbatim" in low or "not rewrite" in low or "never rewrite" in low, \
        "SP 应要求 material/size/weight/category 原样保留、不得重写"


# ── (g) v6 单阶段俄文生图模板：确定性配色 + 风格变量 + 俄文变量透传 ──
def test_fallback_vars_include_brand_colors():
    """确定性回退必须携带 brand_primary/accent（HEX，来自 color_preset 预设路由）。"""
    vv = _build_fallback_vars(DRAFT)
    _preset = resolve_color_preset(DRAFT["category"])  # 美妆护肤 → BEAUTY_PINK
    _colors = get_preset_colors(_preset)
    assert vv["brand_primary"] == _colors["primary"], \
        f"brand_primary 应为 {_colors['primary']}（BEAUTY_PINK 预设）"
    assert vv["accent"] == _colors["accent"], \
        f"accent 应为 {_colors['accent']}（BEAUTY_PINK 预设）"
    assert isinstance(vv["brand_primary"], str) and vv["brand_primary"].startswith("#")
    assert isinstance(vv["accent"], str) and vv["accent"].startswith("#")


def test_fallback_headline_style_defaults_to_exclaim():
    """确定性回退的 headline_style 必须是默认 EXCLAIM（LLM 失败时仍有风格指令）。"""
    vv = _build_fallback_vars(DRAFT)
    assert vv["headline_style"] == "EXCLAIM", "回退默认 headline_style 应为 EXCLAIM"


def test_fallback_ru_vars_empty_when_llm_fails():
    """LLM 失败回退时 5 个俄文变量为 ""——绝不用中文 title 顶替（中文会污染俄文模板）。"""
    vv = _build_fallback_vars(DRAFT)
    for key in RU_KEYS:
        assert vv[key] == "", f"回退时 {key} 应为空串（不得用中文顶替）"


def test_visual_vars_never_contains_color_preset():
    """输出 visual_vars 绝不含 color_preset key（防 assemble_prompt **_vv, color_preset=_cp kwargs 碰撞）。"""
    fb = _build_fallback_vars(DRAFT)
    assert "color_preset" not in fb, "确定性回退不得携带 color_preset key"
    # LLM 即使恶意返回 color_preset → 最终输出也绝不含（ALL_KEYS 白名单过滤）
    evil = {**VALID_JSON, "color_preset": "BEAUTY_PINK"}
    out, _calls = _run_node(draft=DRAFT, llm_content=json.dumps(evil))
    assert "color_preset" not in out.visual_vars, \
        "visual_vars 不得含 color_preset（否则 assemble_prompt **_vv, color_preset=_cp 会 TypeError）"


def test_sp_mentions_cyrillic_ru_keys_and_25_vars():
    """_DEFAULT_SP 必须声明俄文变量指令（Cyrillic + product_ru）与 25 变量/headline_style。"""
    assert "Cyrillic" in _DEFAULT_SP, "SP 应要求俄文内容（Cyrillic）"
    assert "product_ru" in _DEFAULT_SP, "SP 应声明俄文变量 product_ru"
    assert ("25" in _DEFAULT_SP) or ("headline_style" in _DEFAULT_SP), \
        "SP 应提及 25 个变量或 headline_style"


def test_llm_ru_values_pass_through():
    """LLM 提供的俄文变量值应透传到 visual_vars（不丢弃、不被回退覆盖）。"""
    out, _calls = _run_node(draft=DRAFT, llm_content=json.dumps(VALID_JSON))
    vv = out.visual_vars
    assert vv["product_ru"] == "ПАЛОЧКИ от комаров", "product_ru 应透传 LLM 值"
    assert vv["cta_ru"] == "НАДЁЖНАЯ защита", "cta_ru 应透传 LLM 值"
    assert vv["selling_points_ru"] == "длительная защита; быстрый результат", \
        "selling_points_ru 应透传 LLM 值"
    assert vv["effect_data_ru"] == "45 минут; 120 штук", "effect_data_ru 应透传 LLM 值"
    assert vv["target_ru"] == "комары; мухи", "target_ru 应透传 LLM 值"


if __name__ == "__main__":
    import traceback

    tests = [
        test_valid_json_parses_all_25_vars,
        test_markdown_wrapped_json_tolerated,
        test_plain_text_lines_extracted_by_regex,
        test_truly_garbage_content_falls_back_not_crash,
        test_partial_fields_fallback_to_extract_and_defaults,
        test_llm_exception_falls_back_and_does_not_crash,
        test_llm_returns_none_falls_back,
        test_empty_draft_still_non_empty_required,
        test_no_images_passed_to_llm,
        test_fallback_vars_include_category_and_weight,
        test_fallback_vars_empty_category_weight_when_draft_missing,
        test_sp_accepts_non_english_material,
        test_fallback_vars_include_brand_colors,
        test_fallback_headline_style_defaults_to_exclaim,
        test_fallback_ru_vars_empty_when_llm_fails,
        test_visual_vars_never_contains_color_preset,
        test_sp_mentions_cyrillic_ru_keys_and_25_vars,
        test_llm_ru_values_pass_through,
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
