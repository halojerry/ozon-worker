# -*- coding: utf-8 -*-
"""
v0.32 Wave 2 — visual_vars_llm_node 视觉变量生成 LLM 节点。

对抗定案（TDD RED 先行）：
(a) LLM 返回合法 JSON → 解析出全部 19 个视觉变量（AC-1: 8 必填非空 + 11 可选默认化）
(b) LLM 返回坏 JSON / markdown 包裹 → 容错解析（镜像 scene_generation_llm 2 层范式）
(c) LLM 返回部分字段 → 缺失字段回退 extract/默认（不报错，8 必填仍非空）
(d) LLM 调用失败（异常/返回 None）→ 回退 extract_visual_vars_from_draft + 品类默认，不阻断
(e) 纯文本输入（F8 实证 deepseek-v4-flash 无视觉）：mock 调用无 images 参数/无图 URL

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

# ── 19 个视觉变量 key（PRD §2.2/§7.1）──
REQUIRED_KEYS = [
    "product", "color", "material", "appearance", "size",
    "lighting", "effects", "text_areas",
]
OPTIONAL_KEYS = [
    "model", "action", "scene", "background", "icons",
    "inset", "gift", "atmosphere", "packaging", "problem_scene", "comparison",
]
ALL_KEYS = REQUIRED_KEYS + OPTIONAL_KEYS

# 完整合法的 19 变量 JSON（PRD §5.2.1 示例，英文）
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


# ── (a) LLM 返回合法 JSON → 19 变量解析 ──
def test_valid_json_parses_all_19_vars():
    out, _calls = _run_node(draft=DRAFT, llm_content=json.dumps(VALID_JSON))
    vv = out.visual_vars
    assert set(vv.keys()) == set(ALL_KEYS), f"缺失 key: {set(ALL_KEYS) - set(vv.keys())}"
    for key in ALL_KEYS:
        assert vv[key] == VALID_JSON[key], f"{key} 未从 LLM 结果正确解析"


# ── (b) 坏 JSON / markdown 包裹 → 容错解析 ──
def test_markdown_wrapped_json_tolerated():
    content = "Here is the result:\n```json\n" + json.dumps(VALID_JSON) + "\n```"
    out, _calls = _run_node(draft=DRAFT, llm_content=content)
    vv = out.visual_vars
    assert set(vv.keys()) == set(ALL_KEYS)
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
    assert set(vv.keys()) == set(ALL_KEYS), "垃圾内容应回退默认并保持 19 key"
    for key in REQUIRED_KEYS:
        assert vv[key], f"必填 {key} 在回退后仍为空"


# ── (c) LLM 返回部分字段 → 缺失回退 extract/默认（不报错）──
def test_partial_fields_fallback_to_extract_and_defaults():
    partial = {"product": "IPL photo epilator", "color": "white + rose gold"}
    out, _calls = _run_node(draft=DRAFT, llm_content=json.dumps(partial))
    vv = out.visual_vars
    assert set(vv.keys()) == set(ALL_KEYS)
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
    assert set(vv.keys()) == set(ALL_KEYS)
    assert vv["material"] == "ABS塑料", "LLM 异常应回退 draft 材质"
    assert "150×50×30 mm" in vv["size"]
    for key in REQUIRED_KEYS:
        assert vv[key], f"必填 {key} 回退后为空"


def test_llm_returns_none_falls_back():
    out, _calls = _run_node(draft=DRAFT, llm_content=None)
    vv = out.visual_vars
    assert set(vv.keys()) == set(ALL_KEYS)
    # v0.32: color 已从生图变量移除（参考图承担颜色），fallback 用 neutral 默认
    assert vv["color"] == "neutral colors"
    for key in REQUIRED_KEYS:
        assert vv[key], f"必填 {key} 回退后为空"


def test_empty_draft_still_non_empty_required():
    # 极端：draft 为空 + LLM 失败 → 8 必填仍有通用英文默认（绝不阻断生图）
    out, _calls = _run_node(draft={}, llm_exc=ValueError("no token"))
    vv = out.visual_vars
    assert set(vv.keys()) == set(ALL_KEYS)
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


if __name__ == "__main__":
    import traceback

    tests = [
        test_valid_json_parses_all_19_vars,
        test_markdown_wrapped_json_tolerated,
        test_plain_text_lines_extracted_by_regex,
        test_truly_garbage_content_falls_back_not_crash,
        test_partial_fields_fallback_to_extract_and_defaults,
        test_llm_exception_falls_back_and_does_not_crash,
        test_llm_returns_none_falls_back,
        test_empty_draft_still_non_empty_required,
        test_no_images_passed_to_llm,
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
