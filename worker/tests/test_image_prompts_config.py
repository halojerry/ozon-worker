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
    assert "营销主图" in p


def test_render_scene_context():
    """{{scene_context}} 占位符被场景描述替换（含中文特殊字符）"""
    p = get_image_prompt("scene_1", scene_context="家庭生活场景·阳台")
    assert "家庭生活场景·阳台" in p
    assert "{{scene_context}}" not in p


def test_no_var_prompt():
    """无占位符提示词（comparison）原样返回"""
    p = get_image_prompt("comparison")
    assert "对比电商展示图" in p
    assert "{{" not in p


def test_variant_white_bg():
    """变体白底图提示词"""
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
        assert "纯白底产品图" in p  # 默认提示词内容


def test_corrupt_json_falls_back():
    """配置文件 JSON 损坏 → 回退默认提示词（不抛异常）"""
    with _fake_workspace(corrupt=True):
        p = get_image_prompt("main", title="雨衣")
        assert "雨衣" in p
        assert "营销主图" in p


def test_missing_file_falls_back():
    """配置文件不存在 → 回退默认提示词（不抛异常）"""
    with _fake_workspace(no_config=True):
        p = get_image_prompt("detail")
        assert "详情展示图" in p


def test_unknown_key_returns_empty():
    """未知 key 且无默认值 → 返回空串（不抛异常）"""
    with _fake_workspace(no_config=True):
        p = get_image_prompt("not_exist_key_xyz")
        assert p == ""


def test_render_failure_falls_back():
    """模板渲染失败（非法 filter）→ 回退默认模板（不抛异常）"""
    with _fake_workspace(prompts={"main": "产品：{{title | no_such_filter}} 主图"}):
        p = get_image_prompt("main", title="花洒")
        assert "营销主图" in p  # 回退默认


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
