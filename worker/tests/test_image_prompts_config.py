# -*- coding: utf-8 -*-
"""
v0.15 专项验证：生图提示词外置配置（config/image_prompts.json 热加载 + 默认兜底）

运行：cd worker && PYTHONPATH=src python3 tests/test_image_prompts_config.py
"""
import json
import os
import shutil
import sys
import tempfile
from contextlib import contextmanager

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from utils.image_prompts import get_image_prompt, _DEFAULT_PROMPTS  # noqa: E402


@contextmanager
def _fake_workspace(prompts=None, corrupt=False, no_config=False):
    """临时 APP_WORKSPACE_PATH，可注入自定义提示词 JSON（prompts dict 或原始文本）"""
    tmp = tempfile.mkdtemp(prefix="imgprompt_test_")
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


# ── 正常渲染（真实配置文件）──
def test_render_title():
    """{{title}} 占位符被产品标题替换"""
    p = get_image_prompt("main", title="测试 洒水壶")
    assert "测试 洒水壶" in p
    assert "{{title}}" not in p
    assert "电商营销主图" in p  # v8 中文模板内容


def test_render_scene_context():
    """v8: {{scene_1}} 占位符被场景描述替换（scene 槽位独立变量，含中文特殊字符）"""
    p = get_image_prompt("scene_1", scene_1="家庭生活场景·阳台")
    assert "家庭生活场景·阳台" in p
    assert "{{scene_1}}" not in p
    assert "{{scene_context}}" not in p  # v8 模板已无 scene_context 占位符


def test_no_var_prompt():
    """无变量提示词（comparison）原样返回"""
    p = get_image_prompt("comparison")
    assert "生成产品对比电商展示图" in p  # v8 中文模板内容
    assert "{{" not in p


def test_variant_white_bg():
    """变体白底图提示词（v8 禁文字 3 图之一，中文文案）"""
    p = get_image_prompt("variant_white_bg")
    assert "纯白底图" in p


def test_all_default_keys_rendered():
    """所有默认提示词均可渲染（含不传占位符变量的场景）"""
    for key in _DEFAULT_PROMPTS:
        p = get_image_prompt(key)
        assert isinstance(p, str) and p, f"key {key} 渲染结果为空"


def test_config_file_matches_defaults():
    """config/image_prompts.json 与代码默认提示词逐字一致（防配置漂移）"""
    cfg_path = os.path.join(os.path.dirname(__file__), "..", "config", "image_prompts.json")
    with open(cfg_path, "r", encoding="utf-8") as fd:
        data = json.load(fd)
    for key in _DEFAULT_PROMPTS:
        assert key in data, f"配置文件缺少 key: {key}"
        assert data[key] == _DEFAULT_PROMPTS[key], f"key {key} 与默认值不一致"


# ── 兜底路径 ──
def test_missing_key_falls_back_to_default():
    """配置文件缺失 key → 回退默认提示词（不抛异常）"""
    with _fake_workspace(prompts={"main": "自定义主图提示词"}):
        p = get_image_prompt("white_bg", title="花盆")
        assert "花盆" in p
        assert "纯白底" in p  # 默认提示词内容（v8 中文）


def test_corrupt_json_falls_back():
    """配置文件 JSON 损坏 → 回退默认提示词（不抛异常）"""
    with _fake_workspace(corrupt=True):
        p = get_image_prompt("main", title="雨衣")
        assert "雨衣" in p
        assert "电商营销主图" in p


def test_missing_file_falls_back():
    """配置文件不存在 → 回退默认提示词（不抛异常）"""
    with _fake_workspace(no_config=True):
        p = get_image_prompt("detail")
        assert "生成产品电商详情展示图" in p  # v8 detail 中文内容（微距句在 material 守卫内，不依赖）


def test_unknown_key_returns_empty():
    """未知 key 且无默认值 → 返回空串（不抛异常）"""
    with _fake_workspace(no_config=True):
        p = get_image_prompt("not_exist_key_xyz")
        assert p == ""


def test_render_failure_falls_back():
    """模板渲染失败（非法 filter）→ 回退默认模板（不抛异常）"""
    with _fake_workspace(prompts={"main": "产品：{{title | no_such_filter}} 主图"}):
        p = get_image_prompt("main", title="花洒")
        assert "电商营销主图" in p  # 回退默认（v8 中文）


# ── 热加载语义 ──
def test_hot_reload():
    """改配置文件后下一次调用立即生效（无缓存，热加载）"""
    with _fake_workspace(prompts={"main": "V1 版本提示词"}) as tmp:
        p1 = get_image_prompt("main", title="x")
        assert p1 == "V1 版本提示词"

        # 修改文件 → 下一次调用直接读到新值（无需重启/重建）
        with open(os.path.join(tmp, "config", "image_prompts.json"), "w", encoding="utf-8") as fd:
            json.dump({"main": "V2 更新后的提示词"}, fd, ensure_ascii=False)
        p2 = get_image_prompt("main", title="x")
        assert p2 == "V2 更新后的提示词"


# ── v6 单阶段俄文生图模板期望（已适配：v6 英文断言 → v8 中文语义，保留原意图）──
def test_main_template_has_product_prefix():
    """main 模板首句 = "产品：{{title}}。" 中文前缀（保留 title 接地，v6 的 "Product: " 已改中文）"""
    p = get_image_prompt("main", title="保温杯")
    assert "产品：保温杯" in p


def test_main_renders_russian_placeholders():
    """v6: main 模板含俄文/风格占位符 → 传入即渲染（{{product_ru}}/{{cta_ru}}/{{headline_style}}）"""
    p = get_image_prompt(
        "main",
        title="x",
        product_ru="ПАЛОЧКИ",
        cta_ru="ЗАЩИТА",
        headline_style="EXCLAIM",
    )
    assert "ПАЛОЧКИ" in p, "{{product_ru}} 未渲染"
    assert "ЗАЩИТА" in p, "{{cta_ru}} 未渲染"


def test_white_bg_has_no_text_rule():
    """white_bg（后缀 B 禁文字图）含中文硬规则「（俄语/中文/英文均禁止）」（v6 的 'no text of any kind' 已改中文）"""
    p = get_image_prompt("white_bg")
    assert "（俄语/中文/英文均禁止）" in p


def test_white_bg_has_no_russian_placeholder():
    """v6: white_bg 不含任何 RU/文字占位符（{{product_ru}} 等不得出现）"""
    p = get_image_prompt("white_bg")
    assert "product_ru" not in p
    assert "{{product_ru}}" not in p


def test_main_negative_embedded():
    """main（后缀 A 允许俄文图）内嵌中文负面规则「严禁出现水印」（v6 的 "no Chinese text" 已改中文）"""
    p = get_image_prompt("main")
    assert "严禁出现水印" in p


def test_empty_ru_no_residue():
    """v6: 无 RU 值时渲染不残留 {{product_ru 占位符"""
    p = get_image_prompt("main", title="x")
    assert "{{product_ru" not in p


# ── v8 中文模板期望（RED 任务：锁定未来 v8 中文模板特征，当前 v6 英文模板下应失败）──
def test_main_template_chinese_prefix():
    """v8: main 模板首句 = "产品：{{title}}。" 中文前缀（v8 中文文案）"""
    p = get_image_prompt("main", title="保温杯")
    assert "产品：保温杯" in p, "v8 main 缺中文前缀「产品：」"


def test_main_renders_v8_russian_placeholders():
    """v8: main 含俄文/场景独立变量占位符 → 传入即渲染（{{product_ru}}/{{cta_ru}}/{{scene_1}}）"""
    p = get_image_prompt(
        "main",
        title="x",
        product_ru="ПАЛОЧКИ",
        cta_ru="ЗАЩИТА",
        selling_points_ru="a;b",
        effect_data_ru="45 мин",
        target_ru="комары",
        scene_1="夏日森林",
    )
    assert "ПАЛОЧКИ" in p, "{{product_ru}} 未渲染"
    assert "ЗАЩИТА" in p, "{{cta_ru}} 未渲染"
    assert "夏日森林" in p, "{{scene_1}} 未渲染（v8 场景槽位用 scene_N 独立变量）"


def test_white_bg_chinese_no_text():
    """v8: white_bg（禁文字 3 图之一）含中文负面规则「禁止任何文字」，且不含 RU 占位符"""
    p = get_image_prompt("white_bg")
    assert ("禁止任何文字" in p) or ("俄语/中文/英文均禁止" in p), "v8 white_bg 缺中文禁文字规则"
    assert "product_ru" not in p
    assert "{{product_ru}}" not in p


def test_multi_angle_no_text():
    """v8: multi_angle 禁一切文字（用户纠正 v8 HTML——即使 HTML 标俄文角标，multi_angle 也不允许任何文字）"""
    p = get_image_prompt("multi_angle")
    assert "{{product_ru}}" not in p
    assert "{{cta_ru}}" not in p
    assert "ВИД" not in p, "multi_angle 不得含俄文角标（ВИД 等）"
    assert "product_ru" not in p


def test_scene_slots_use_scene_vars():
    """v8: scene_N 槽位用独立变量 {{scene_1}} 渲染（不再依赖 {{scene_context}}）"""
    p = get_image_prompt("scene_1", scene_1="夏日森林")
    assert "夏日森林" in p, "{{scene_1}} 未渲染"
    assert "{{scene_1" not in p


if __name__ == "__main__":
    import traceback

    tests = [
        test_render_title,
        test_render_scene_context,
        test_no_var_prompt,
        test_variant_white_bg,
        test_all_default_keys_rendered,
        test_config_file_matches_defaults,
        test_missing_key_falls_back_to_default,
        test_corrupt_json_falls_back,
        test_missing_file_falls_back,
        test_unknown_key_returns_empty,
        test_render_failure_falls_back,
        test_hot_reload,
        test_main_template_has_product_prefix,
        test_main_renders_russian_placeholders,
        test_white_bg_has_no_text_rule,
        test_white_bg_has_no_russian_placeholder,
        test_main_negative_embedded,
        test_empty_ru_no_residue,
        test_main_template_chinese_prefix,
        test_main_renders_v8_russian_placeholders,
        test_white_bg_chinese_no_text,
        test_multi_angle_no_text,
        test_scene_slots_use_scene_vars,
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
