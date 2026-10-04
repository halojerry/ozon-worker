#!/usr/bin/env python3
"""cli.py Sentry 错误上报单测（mock-only，无需真实安装 sentry-sdk）。

覆盖 _init_sentry（DSN 未设 / DSN 设置 / ImportError / 测试进程守卫）与
_capture_exception（非敏感 tags，绝不含凭证键）。

运行：
    cd skill && python3.12 tests/test_sentry_skill.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import cli  # noqa: E402

_DSN = "https://deadbeefdeadbeefdeadbeef@o000000.ingest.us.sentry.io/0000000"


def _install_mock_sentry() -> mock.MagicMock:
    """注入 mock sentry_sdk 到 sys.modules（cli.py lazy import 命中 sys.modules 缓存）。"""
    m = mock.MagicMock()
    sys.modules["sentry_sdk"] = m
    return m


def _teardown_mock_sentry() -> None:
    sys.modules.pop("sentry_sdk", None)


def _skill_version() -> str:
    v = (Path(__file__).resolve().parent.parent / "VERSION").read_text(encoding="utf-8").strip()
    return v or "0.0.0"


def test_init_sentry_no_dsn_uses_builtin_default():
    """v0.37: DSN 未配置（env/settings 均空）→ 用内置默认 DSN，init 被调用（用户零配置）。"""
    from scripts.lib.config_store import DEFAULT_SENTRY_DSN
    m = _install_mock_sentry()
    try:
        with mock.patch.dict(os.environ, {"SENTRY_DSN": ""}, clear=False), \
             mock.patch("scripts.cli._is_sentry_test_process", return_value=False):
            result = cli._init_sentry()
        assert result is True
        m.init.assert_called_once()
        kwargs = m.init.call_args.kwargs
        assert kwargs["dsn"] == DEFAULT_SENTRY_DSN
        assert kwargs["environment"] == "skill"
        assert kwargs["traces_sample_rate"] == 0.0
    finally:
        _teardown_mock_sentry()


def test_init_sentry_with_dsn_inits_env_skill_and_release():
    """DSN 设置 + mock sentry_sdk → init 被调用且 environment=="skill"、release==VERSION。"""
    m = _install_mock_sentry()
    try:
        with mock.patch.dict(os.environ, {"SENTRY_DSN": _DSN}, clear=False), \
             mock.patch("scripts.cli._is_sentry_test_process", return_value=False):
            result = cli._init_sentry()
        assert result is True
        m.init.assert_called_once()
        kwargs = m.init.call_args.kwargs
        assert kwargs["dsn"] == _DSN
        assert kwargs["environment"] == "skill"
        assert kwargs["release"] == _skill_version()
        assert kwargs["traces_sample_rate"] == 0.0
    finally:
        _teardown_mock_sentry()


def test_init_sentry_import_error_returns_false_without_crash():
    """import sentry_sdk 抛 ImportError → _init_sentry() False 不崩。"""
    sys.modules.pop("sentry_sdk", None)  # 确保走真实 import 路径
    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

    def fake_import(name, *args, **kwargs):
        if name == "sentry_sdk":
            raise ImportError("No module named 'sentry_sdk'")
        return real_import(name, *args, **kwargs)

    try:
        with mock.patch.dict(os.environ, {"SENTRY_DSN": _DSN}, clear=False), \
             mock.patch("scripts.cli._is_sentry_test_process", return_value=False), \
             mock.patch("builtins.__import__", side_effect=fake_import):
            result = cli._init_sentry()
        assert result is False
    finally:
        _teardown_mock_sentry()


def test_capture_exception_tags_contain_no_credentials():
    """_capture_exception 调 capture_exception，set_tag 键绝不含凭证键。"""
    m = _install_mock_sentry()
    try:
        exc = RuntimeError("boom")
        cli._capture_exception(exc, "graph")
        m.capture_exception.assert_called_once_with(exc)
        tag_keys = [c.args[0] for c in m.set_tag.call_args_list]
        assert tag_keys == ["command", "skill_version", "os", "platform"]
        for key in tag_keys:
            assert not any(cred in key.lower() for cred in ("token", "ak", "api_key", "client_id"))
        m.flush.assert_called_once()
    finally:
        _teardown_mock_sentry()


def test_is_sentry_test_process_pytest_main_module_v0832():
    """✅ v0.83.2 补洞：`python -m pytest` 形态（argv[0]=pytest/__main__.py，
    PYTEST_CURRENT_TEST 未设）也必须判 True——CI Skill Tests job 用此形态，
    旧判定在 collection 阶段全不中 → 测试假错误直灌生产 Sentry。"""
    # 形态①：CI Docker 实测 argv[0]（Sentry 事件 sys.argv 留证）
    argv0 = "/usr/local/lib/python3.12/site-packages/pytest/__main__.py"
    with mock.patch.object(sys, "argv", [argv0, "tests/", "-q"]), \
         mock.patch.dict(os.environ, {}, clear=False):
        os.environ.pop("PYTEST_CURRENT_TEST", None)
        assert cli._is_sentry_test_process() is True
    # 形态②：裸 pytest
    with mock.patch.object(sys, "argv", ["pytest"]), \
         mock.patch.dict(os.environ, {}, clear=False):
        os.environ.pop("PYTEST_CURRENT_TEST", None)
        assert cli._is_sentry_test_process() is True
    # 形态③：正常用户命令（graph 等）——pytest 未 import 时不误伤
    import importlib.util as _ilu
    _had = "pytest" in sys.modules
    if _had:
        # 测试进程内 pytest 恒在 sys.modules——只验「basename 干净 + 无环境变量」
        # 的路径分支，pytest-in-sys.modules 分支由形态①②与真实验证覆盖。
        pass
    else:
        with mock.patch.object(sys, "argv", ["/opt/skill/scripts/cli.py", "graph"]), \
             mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PYTEST_CURRENT_TEST", None)
            assert cli._is_sentry_test_process() is False
    # 形态④：PYTEST_CURRENT_TEST 执行期兜底
    with mock.patch.dict(os.environ, {"PYTEST_CURRENT_TEST": "x::y"}, clear=False):
        assert cli._is_sentry_test_process() is True


def test_init_sentry_skips_in_test_process():
    """测试进程（sys.argv[0] 含 test_）→ 跳过 init 返回 False。"""
    m = _install_mock_sentry()
    try:
        with mock.patch.dict(os.environ, {"SENTRY_DSN": _DSN}, clear=False), \
             mock.patch.object(sys, "argv", ["/some/path/tests/test_runner.py"]):
            result = cli._init_sentry()
        assert result is False
        m.init.assert_not_called()
    finally:
        _teardown_mock_sentry()


def _main() -> int:
    fns = [(k, v) for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    failed = 0
    for name, fn in fns:
        try:
            fn()
            print(f"✅ {name}")
        except Exception as exc:
            failed += 1
            print(f"❌ {name}: {type(exc).__name__}: {exc}")
    total = len(fns)
    print(f"\n{total - failed}/{total} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
