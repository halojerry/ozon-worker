"""v0.81 SSTI 加固单测：utils/safe_template（SandboxedEnvironment）+ 6 调用点接线。

背景（Mimosa L3 扫描 7×high）：worker 全部 ``jinja2.Template(<字符串>).render(数据)``
的模板串来自 config/*.json（运维 bind-mount 热加载）或模块常量——数据作变量是
正确用法，但默认环境对「config 被注入恶意模板」无防护（``{{ ''.__class__.__mro__ }}``
类 payload 可沿属性链访问对象图拼进 LLM prompt）。本批统一切 SandboxedEnvironment：

- 良性模板渲染结果与 jinja2.Template 逐字一致（回归锁定，含真实生产模板）
- 恶意 payload 抛 jinja2.exceptions.SecurityError（或 UndefinedError），不泄漏
- 回退语义逐点对齐现状：image_prompts / prompt_assembler / assemble 既有 except
  接住沙箱异常走各自回退；scene / visual_vars / retry 直渲点与现状模板语法错误
  （TemplateSyntaxError）同为 fail-closed——恶意 config 宁可任务失败不外泄
"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from jinja2 import Template
from jinja2.exceptions import SecurityError, UndefinedError
from jinja2.sandbox import SandboxedEnvironment

sys.path.insert(0, "src")

from graphs.nodes import assemble_ozon_product_node
from graphs.nodes import scene_generation_llm_node
from graphs.nodes import visual_vars_llm_node
from graphs import validation_retry_loop
from utils import image_prompts, prompt_assembler
from utils.safe_template import (
    SANDBOX_ENV,
    render_safe,
    render_safe_mapping,
)

_SSTI_RAISES = (SecurityError, UndefinedError)


# ============================================================
# 恶意 payload（沙箱必须拦）
# ============================================================
# 这些 payload 链式访问非安全属性 → SecurityError / UndefinedError（渲染期硬拦）
MALICIOUS_PAYLOADS = [
    "{{ ''.__class__.__mro__ }}",
    "{{ ''.__class__.__base__.__subclasses__() }}",
    "{{ ''.__class__.__mro__[1].__subclasses__() }}",
    "{{ ().__class__.__bases__ }}",
    "{{ ''.__init__.__globals__ }}",
    "{{ self.__init__.__globals__ }}",  # self 顶层为 Undefined → UndefinedError，同样不泄漏
    "{{ ''.__class__.__mro__[1].__subclasses__()[0].__init__.__globals__ }}",
]

# 这些形态沙箱返回 Undefined 渲染为空串（jinja2 3.x Undefined.__str__=''）——
# 不抛异常但也零泄漏（拿不到 __class__/对象图任何信息）
MALICIOUS_EMPTY_OUTPUT_PAYLOADS = [
    "{{ ''|attr('__class__') }}",          # |attr 过滤器 → unsafe_undefined
    "{{ ''.__class__ }}",                  # 链尾即 __class__ → Undefined 渲染空
    "{{ c.description_category_id.__class__ }}",  # item 回退后链尾 __class__ 同上
]


def _assert_no_leak(payload: str, **vars) -> None:
    """恶意模板双保险断言：要么渲染期抛异常（硬拦），要么输出零对象图信息。"""
    forbidden = ("class '", "__mro__", "__subclasses__", "__globals__",
                 "__builtins__", "<class", "object at 0x")
    try:
        out = render_safe(payload, **vars)
    except Exception:
        return  # SecurityError / UndefinedError / 编译错等——均已拦截
    assert not any(t in out for t in forbidden), f"泄漏! {payload!r} -> {out!r}"


# ============================================================
# 良性模板回归：与 jinja2.Template 逐字一致
# ============================================================
BENIGN_CASES = [
    ("纯变量", "产品：{{title}}。场景：{{scene_context}}。",
     {"title": "沥水篮", "scene_context": "厨房"},),
    ("候选循环（category_match_v2 up 真实构造）",
     "候选：{% for c in candidates %}{{loop.index}}.{{c.description_category_id}}/{{c.type_id}}({{c.similarity}}) {% endfor %}共{{candidates|length}}",
     {"candidates": [
         {"description_category_id": 1, "type_id": 2, "similarity": 0.9},
         {"description_category_id": 3, "type_id": 4, "similarity": 0.8},
     ]},),
    ("条件+tojson（error_repair up 真实构造）",
     "{% if attributes %}属性:{{attributes_schema|tojson}}{% endif %}值:{{current_value}}",
     {"attributes": {"颜色": "白"}, "attributes_schema": [{"id": 8229}], "current_value": "白"},),
    ("dict 属性访问（draft.* 真实构造）",
     "{{draft.title}} 尺寸 {{draft.width}}x{{draft.height}}x{{draft.depth}}",
     {"draft": {"title": "收纳盒", "width": 40, "height": 25, "depth": 5}},),
]


@pytest.mark.parametrize("name,tpl,vars", BENIGN_CASES, ids=[c[0] for c in BENIGN_CASES])
def test_benign_matches_default_env(name, tpl, vars):
    """良性模板渲染结果与默认环境逐字一致——沙箱对正常业务零行为变更。"""
    expected = Template(tpl).render(**vars)
    assert render_safe(tpl, **vars) == expected
    assert render_safe_mapping(tpl, vars) == expected


def test_real_production_prompt_template_regression():
    """真实生产生图模板（image_prompts._DEFAULT_PROMPTS）沙箱渲染回归。"""
    tpl = image_prompts._DEFAULT_PROMPTS["main"]
    vars = {
        "title": "不锈钢沥水篮",
        "scene_1": "现代厨房",
        "atmosphere": "明亮",
        "product_ru": "Дуршлаг",
        "cta_ru": "Купите сейчас",
        "brand_primary": "белый",
        "accent": "серый",
    }
    assert render_safe(tpl, **vars) == Template(tpl).render(**vars)


def test_render_safe_empty_and_missing_vars():
    """缺变量渲染为空串（jinja2 Undefined 语义保持）；空模板返回空。"""
    assert render_safe("产品：{{title}}。") == "产品：。"
    assert render_safe("") == ""
    assert render_safe_mapping("{{a}}", {"a": None}) == "None"


# ============================================================
# 恶意模板：沙箱拦截，不泄漏
# ============================================================
@pytest.mark.parametrize("payload", MALICIOUS_PAYLOADS)
def test_malicious_payloads_blocked(payload):
    with pytest.raises(_SSTI_RAISES):
        render_safe(payload)


@pytest.mark.parametrize("payload", MALICIOUS_PAYLOADS[:4])
def test_malicious_payloads_via_mapping_blocked(payload):
    with pytest.raises(_SSTI_RAISES):
        render_safe_mapping(payload, {})


@pytest.mark.parametrize("payload", MALICIOUS_EMPTY_OUTPUT_PAYLOADS)
def test_malicious_attr_filter_variants_render_empty(payload):
    """|attr 过滤器 / 链尾 __class__ 形态：沙箱返回 Undefined 渲染空串——不抛但零泄漏。"""
    out = render_safe(payload, c={"description_category_id": 1})
    assert out == ""


@pytest.mark.parametrize("payload", MALICIOUS_PAYLOADS + MALICIOUS_EMPTY_OUTPUT_PAYLOADS)
def test_all_malicious_payloads_no_leak(payload):
    """全 payload 统一兜底断言：拦住或空串，绝不吐对象图信息。"""
    _assert_no_leak(payload, c={"description_category_id": 1})


def test_malicious_pivot_via_data_variable_blocked():
    """核心 SSTI 威胁形态：商品数据变量作跳板沿属性链逃逸 → 渲染期硬拦/零泄漏。"""
    with pytest.raises(_SSTI_RAISES):
        render_safe("产品：{{ title.__class__.__mro__ }}", title="沥水篮")
    _assert_no_leak("候选：{{ c.description_category_id.__class__ }}",
                    c={"description_category_id": 1})


def test_sandbox_env_is_sandboxed_and_no_autoescape():
    """环境守卫：必须 SandboxedEnvironment；autoescape 恒 False（prompt 是纯文本非 HTML）。"""
    assert isinstance(SANDBOX_ENV, SandboxedEnvironment)
    assert SANDBOX_ENV.autoescape is False
    raw = "<b>&'\"{{x}}"
    assert render_safe("{{v}}", v=raw) == raw


# ============================================================
# 调用点接线：回退语义逐点对齐现状
# ============================================================
def test_image_prompts_malicious_config_falls_back_to_default(monkeypatch):
    """image_prompts.get_image_prompt：恶意 config 模板 → SecurityError 被既有
    except 接住 → 回退默认模板原文（现状语义逐字保持，不炸）。"""
    monkeypatch.setattr(
        image_prompts, "_load_prompt_config",
        lambda: {"main": "产品：{{title}} {{ ''.__class__.__mro__ }}"},
    )
    out = image_prompts.get_image_prompt("main", title="沥水篮")
    assert out == image_prompts._DEFAULT_PROMPTS["main"]


def test_image_prompts_benign_config_renders(monkeypatch):
    """良性 config 模板正常渲染（优先级 config > 默认，现状语义）。"""
    monkeypatch.setattr(image_prompts, "_load_prompt_config", lambda: {"main": "hi {{title}}"})
    assert image_prompts.get_image_prompt("main", title="X") == "hi X"


def test_prompt_assembler_malicious_slot_falls_back_to_get_image_prompt(monkeypatch):
    """prompt_assembler.assemble_prompt：恶意槽位模板 → 既有 except 回退
    get_image_prompt（默认模板链），返回非空 prompt 不炸。"""
    monkeypatch.setattr(
        prompt_assembler, "_load_prompt_config",
        lambda: {"main": "{{ title }} {{ ''.__class__.__mro__ }}"},
    )
    # 兜底链 get_image_prompt 读 image_prompts 模块自己的 _load_prompt_config → 空 → 默认模板
    monkeypatch.setattr(image_prompts, "_load_prompt_config", dict)
    out = prompt_assembler.assemble_prompt("main", title="沥水篮")
    assert isinstance(out, str) and "沥水篮" in out


def _write_llm_cfg(tmp_path: Path, name: str, cfg: dict) -> Path:
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(exist_ok=True)
    cfg_file = cfg_dir / name
    cfg_file.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    return cfg_file


def test_assemble_llm_match_malicious_cfg_returns_none(monkeypatch, tmp_path):
    """assemble._llm_match_category：恶意 category_match_v2_cfg.json →
    SecurityError 被既有 except Exception 接住 → 返回 None（回落下一匹配层，不炸任务）。"""
    _write_llm_cfg(tmp_path, "category_match_v2_cfg.json",
                   {"config": {"model": "m"}, "sp": "sp", "up": "{{ title }} {{ ''.__class__.__mro__ }}"})
    monkeypatch.setenv("APP_WORKSPACE_PATH", str(tmp_path))
    assert assemble_ozon_product_node._llm_match_category("标题", "描述", {}, [], "tok") is None


def test_assemble_llm_match_benign_cfg_renders_and_parses(monkeypatch, tmp_path):
    """assemble._llm_match_category：良性模板渲染进 LLM 调用并解析回 dict（回归）。"""
    _write_llm_cfg(tmp_path, "category_match_v2_cfg.json",
                   {"config": {"model": "m"}, "sp": "系统提示",
                    "up": "标题:{{title}} 候选数:{{candidates|length}}"})
    monkeypatch.setenv("APP_WORKSPACE_PATH", str(tmp_path))
    captured = {}

    def fake_llm(**kwargs):
        captured.update(kwargs)
        return '{"description_category_id": 123, "type_id": 456, "category_path": "收纳", "confidence": 0.9}'

    monkeypatch.setattr(assemble_ozon_product_node, "call_mxou_chat_api", fake_llm)
    result = assemble_ozon_product_node._llm_match_category(
        "标题", "描述", {"颜色": "白"}, [{"description_category_id": 1}], "tok")
    assert result["description_category_id"] == 123
    assert captured["system_prompt"] == "系统提示"
    assert captured["user_prompt"] == "标题:标题 候选数:1"


def test_retry_llm_malicious_cfg_fails_closed(monkeypatch, tmp_path):
    """validation_retry_loop._call_mxou_llm：恶意 config → SecurityError 上抛
    （fail-closed，与现状 TemplateSyntaxError 传播同级；上游调用点各有 except/raise）。"""
    _write_llm_cfg(tmp_path, "translate_russian_cfg.json",
                   {"config": {}, "sp": "", "up": "{{ text }} {{ ''.__class__.__mro__ }}"})
    monkeypatch.setenv("APP_WORKSPACE_PATH", str(tmp_path))
    with pytest.raises(SecurityError):
        validation_retry_loop._call_mxou_llm("tok", "config/translate_russian_cfg.json", {"text": "红色"})


def test_retry_llm_benign_cfg_renders(monkeypatch, tmp_path):
    """validation_retry_loop._call_mxou_llm：良性模板渲染 + LLM mock（回归）。"""
    _write_llm_cfg(tmp_path, "translate_russian_cfg.json",
                   {"config": {}, "sp": "", "up": "翻译:{{text}}"})
    monkeypatch.setenv("APP_WORKSPACE_PATH", str(tmp_path))
    from utils import mxou_api
    monkeypatch.setattr(mxou_api, "call_mxou_chat_api",
                        lambda **kw: f"OK:{kw['user_prompt']}")
    out = validation_retry_loop._call_mxou_llm("tok", "config/translate_russian_cfg.json", {"text": "红色"})
    assert out == "OK:翻译:红色"


def _fake_runtime() -> SimpleNamespace:
    return SimpleNamespace(context=SimpleNamespace())


def test_scene_node_benign_cfg_renders(monkeypatch, tmp_path):
    """scene_generation_llm_node：良性 config 渲染 + LLM mock → 3 场景解析（回归）。"""
    _write_llm_cfg(tmp_path, "scene_llm_cfg.json",
                   {"config": {}, "sp": "s", "up": "场景:{{title}}/{{category}}"})
    monkeypatch.setenv("APP_WORKSPACE_PATH", str(tmp_path))
    monkeypatch.setattr(
        scene_generation_llm_node, "call_mxou_chat_api",
        lambda **kw: '{"scene_1":"厨房","scene_2":"办公室","scene_3":"客厅"}')
    state = scene_generation_llm_node.SceneGenerationInput(
        draft={"title": "沥水篮", "category": "家居"})
    out = scene_generation_llm_node.scene_generation_llm_node(
        state, {"metadata": {"llm_cfg": "config/scene_llm_cfg.json"}}, _fake_runtime())
    assert (out.scene_context_1, out.scene_context_2, out.scene_context_3) == ("厨房", "办公室", "客厅")


def test_scene_node_malicious_cfg_fails_closed(monkeypatch, tmp_path):
    """scene_generation_llm_node：恶意 config → SecurityError 上抛
    （直渲点无回退保护，fail-closed 与现状模板语法错误同级——恶意模板绝不送 LLM）。"""
    _write_llm_cfg(tmp_path, "scene_llm_cfg.json",
                   {"config": {}, "sp": "s", "up": "{{ ''.__class__.__mro__ }}"})
    monkeypatch.setenv("APP_WORKSPACE_PATH", str(tmp_path))
    state = scene_generation_llm_node.SceneGenerationInput(draft={"title": "沥水篮"})
    with pytest.raises(_SSTI_RAISES):
        scene_generation_llm_node.scene_generation_llm_node(
            state, {"metadata": {"llm_cfg": "config/scene_llm_cfg.json"}}, _fake_runtime())


def test_visual_vars_node_benign_cfg_llm_garbage_falls_back(monkeypatch, tmp_path):
    """visual_vars_llm_node：良性 config + LLM 返回垃圾 → 既有容错回退确定性提取
    （回归：沙箱不改变节点自身回退语义，不炸任务）。"""
    _write_llm_cfg(tmp_path, "visual_vars_llm_cfg.json",
                   {"config": {}, "sp": "s", "up": "视觉变量:{{title}} 属性:{{attributes}}"})
    monkeypatch.setenv("APP_WORKSPACE_PATH", str(tmp_path))
    monkeypatch.setattr(visual_vars_llm_node, "call_mxou_chat_api", lambda **kw: "这不是JSON")
    state = visual_vars_llm_node.VisualVarsInput(
        draft={"title": "沥水篮", "attributes": {"颜色": "白色"}})
    out = visual_vars_llm_node.visual_vars_llm_node(
        state, {"metadata": {"llm_cfg": "config/visual_vars_llm_cfg.json"}}, _fake_runtime())
    assert isinstance(out.visual_vars, dict) and out.visual_vars


def test_visual_vars_node_malicious_cfg_fails_closed(monkeypatch, tmp_path):
    """visual_vars_llm_node：恶意 config → SecurityError 上抛（同 scene，fail-closed）。"""
    _write_llm_cfg(tmp_path, "visual_vars_llm_cfg.json",
                   {"config": {}, "sp": "s", "up": "{{ ''.__class__.__mro__ }}"})
    monkeypatch.setenv("APP_WORKSPACE_PATH", str(tmp_path))
    state = visual_vars_llm_node.VisualVarsInput(draft={"title": "沥水篮"})
    with pytest.raises(_SSTI_RAISES):
        visual_vars_llm_node.visual_vars_llm_node(
            state, {"metadata": {"llm_cfg": "config/visual_vars_llm_cfg.json"}}, _fake_runtime())


# ============================================================
# 接线纪律守卫：jinja2 直引不得回潮
# ============================================================
def test_no_direct_jinja2_import_outside_safe_template():
    """「新增模板渲染必须走 utils/safe_template」机器锁定——worker src 除
    safe_template.py 外禁止任何 jinja2 import（防直接 Template() 回潮）。"""
    src_root = Path(__file__).resolve().parents[1] / "src"
    offenders = []
    for p in sorted(src_root.rglob("*.py")):
        if p.name == "safe_template.py":
            continue
        text = p.read_text(encoding="utf-8")
        if "from jinja2 import" in text or "import jinja2" in text:
            offenders.append(str(p))
    assert offenders == []
