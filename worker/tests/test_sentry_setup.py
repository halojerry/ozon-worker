"""Sentry 初始化/上报单测（v0.23）— DSN 为空 no-op；有 DSN 时初始化并带上下文上报。

运行：
    cd worker && PYTHONPATH=src python3 tests/test_sentry_setup.py
"""
from __future__ import annotations

import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import utils.sentry_setup as mod


def _reset() -> None:
    mod._SENTRY_INITIALIZED = False
    mod._SENTRY_ENABLED = False


def test_init_disabled_without_dsn():
    """无 SENTRY_DSN → init 返回 False，Sentry 不启用。"""
    _reset()
    with mock.patch.dict(os.environ, {}, clear=True):
        assert mod.init_sentry() is False
        assert mod._SENTRY_ENABLED is False


def test_init_enabled_with_dsn():
    """有 SENTRY_DSN → init 用 DSN/release 调用 SDK。"""
    _reset()
    fake_sdk = mock.MagicMock()
    with mock.patch.object(mod, "_is_test_process", return_value=False), mock.patch.dict(
        sys.modules, {"sentry_sdk": fake_sdk}
    ), mock.patch.dict(
        os.environ,
        {"SENTRY_DSN": "https://x@o1.ingest.us.sentry.io/2", "APP_VERSION": "0.23.0"},
        clear=True,
    ):
        assert mod.init_sentry() is True
        kwargs = fake_sdk.init.call_args.kwargs
        assert kwargs["dsn"].startswith("https://")
        assert kwargs["release"] == "0.23.0"
        assert kwargs["environment"] == "production"


def test_init_skipped_in_test_process():
    """测试进程（脚本名 test_*.py）即使有 DSN 也不启用，避免测试噪音。"""
    _reset()
    fake_sdk = mock.MagicMock()
    with mock.patch.object(mod, "_is_test_process", return_value=True), mock.patch.dict(
        sys.modules, {"sentry_sdk": fake_sdk}
    ), mock.patch.dict(os.environ, {"SENTRY_DSN": "https://x@o1.ingest.us.sentry.io/2"}, clear=True):
        assert mod.init_sentry() is False
        fake_sdk.init.assert_not_called()
        assert mod._SENTRY_ENABLED is False


def test_capture_task_error_noop_when_disabled():
    """未启用时 capture_task_error 不抛异常、不调用 SDK。"""
    _reset()
    fake_sdk = mock.MagicMock()
    with mock.patch.dict(sys.modules, {"sentry_sdk": fake_sdk}):
        mod.capture_task_error(ValueError("x"), task_id="t1", tenant_id="u1")
        fake_sdk.capture_exception.assert_not_called()


def test_capture_task_error_sets_context_and_flushes():
    """启用后上报异常并带 task_id/tenant_id 上下文，同步 flush。"""
    _reset()
    fake_sdk = mock.MagicMock()
    with mock.patch.object(mod, "_is_test_process", return_value=False), mock.patch.dict(
        sys.modules, {"sentry_sdk": fake_sdk}
    ), mock.patch.dict(os.environ, {"SENTRY_DSN": "https://x@o1.ingest.us.sentry.io/2"}, clear=True):
        assert mod.init_sentry() is True
        mod.capture_task_error(ValueError("boom"), task_id="t1", tenant_id="u1")
        fake_sdk.capture_exception.assert_called_once()
        fake_sdk.flush.assert_called_once_with(timeout=2)
        scope = fake_sdk.configure_scope.return_value.__enter__.return_value
        scope.set_tag.assert_any_call("task_id", "t1")
        scope.set_tag.assert_any_call("tenant_id", "u1")


if __name__ == "__main__":
    import traceback

    failed = 0
    total = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            total += 1
            try:
                fn()
                print(f"PASS {name}")
            except Exception:
                failed += 1
                print(f"FAIL {name}")
                traceback.print_exc()
    print(f"\n{total - failed}/{total} passed")
    sys.exit(1 if failed else 0)
