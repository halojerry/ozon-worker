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


def test_init_wires_before_send():
    """init_sentry 注册 before_send（语言噪音聚合回调）。"""
    _reset()
    fake_sdk = mock.MagicMock()
    with mock.patch.object(mod, "_is_test_process", return_value=False), mock.patch.dict(
        sys.modules, {"sentry_sdk": fake_sdk}
    ), mock.patch.dict(os.environ, {"SENTRY_DSN": "https://x@o1.ingest.us.sentry.io/2"}, clear=True):
        assert mod.init_sentry() is True
        kwargs = fake_sdk.init.call_args.kwargs
        assert callable(kwargs.get("before_send"))


# ============================================================
# v0.32 before_send — 语言噪音指纹聚合（T5）
# 62 个语言检查噪音 issue 走 sentry_sdk 默认 LoggingIntegration
# （节点内 logger.error 自动上报，extra 无 task_id），必须在 before_send
# 按「消息特征 + logger 名」拦截：fingerprint 聚合 + level 降 warning
# + trace_id 注入；非噪音原样通过；_SENTRY_ENABLED=False 短路零开销。
# ============================================================

def _noise_event() -> dict:
    """伪造一条语言检查噪音事件（模拟 ozon_validate_node 的 logger.error 上报）。"""
    return {
        "message": "❌ item[0]名称含中文字符: 太阳能草坪灯",
        "logger": "graphs.nodes.ozon_validate_node",
        "level": "error",
        "extra": {},
    }


def test_before_send_aggregates_lang_noise():
    """语言噪音事件 → 单一 fingerprint 聚合 + level 降 warning + trace_id 注入。"""
    _reset()
    mod._SENTRY_ENABLED = True
    event = _noise_event()
    out = mod._before_send(event, hint={})
    assert out is event  # 原地修改
    assert out["fingerprint"] == [mod._LANG_NOISE_FINGERPRINT]
    assert out["level"] == "warning"
    assert out["extra"]["noise_group"] == "language_validation"
    assert out["extra"]["trace_id"].startswith("lang-noise-")


def test_before_send_trace_id_deterministic():
    """trace_id 注入确定性：同一消息两次调用结果一致。"""
    _reset()
    mod._SENTRY_ENABLED = True
    e1 = mod._before_send(_noise_event())["extra"]["trace_id"]
    e2 = mod._before_send(_noise_event())["extra"]["trace_id"]
    assert e1 == e2


def test_before_send_handles_logentry_format():
    """sentry LoggingIntegration 事件走 logentry.message 字段也能识别。"""
    _reset()
    mod._SENTRY_ENABLED = True
    event = {
        "logentry": {"message": "❌ 属性8229含中文字符: 杀虫剂", "params": []},
        "logger": "graphs.nodes.ozon_validate_node",
        "level": "error",
    }
    out = mod._before_send(event)
    assert out["fingerprint"] == [mod._LANG_NOISE_FINGERPRINT]
    assert out["level"] == "warning"


def test_before_send_passthrough_non_noise():
    """非噪音错误 → 原样通过（不修改任何字段）。"""
    _reset()
    mod._SENTRY_ENABLED = True
    event = {
        "message": "Ozon API 500: rate limited",
        "logger": "utils.ozon_client",
        "level": "error",
    }
    out = mod._before_send(event)
    assert out is event
    assert "fingerprint" not in out
    assert "noise_group" not in out.get("extra", {})
    assert out["level"] == "error"


def test_before_send_wrong_logger_passthrough():
    """消息含噪音关键词但 logger 名不是已知噪音源 → 原样通过。"""
    _reset()
    mod._SENTRY_ENABLED = True
    event = {
        "message": "❌ 描述含中文字符",
        "logger": "some.other.module",
        "level": "error",
    }
    out = mod._before_send(event)
    assert out is event
    assert "fingerprint" not in out
    assert out["level"] == "error"


def test_before_send_short_circuit_when_disabled():
    """_SENTRY_ENABLED=False → 短路，事件原样返回零修改。"""
    _reset()
    mod._SENTRY_ENABLED = False
    event = _noise_event()
    out = mod._before_send(event)
    assert out is event
    assert "fingerprint" not in out
    assert out["level"] == "error"


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
