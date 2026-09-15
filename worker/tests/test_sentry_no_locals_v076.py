"""v0.76 T4(crypto-H3): Sentry init 必须 include_local_variables=False——防 token/api_key 随栈帧上报。

Sentry 默认捕获栈帧局部变量（include_local_variables=True）。worker 异常栈常穿透
GraphInput/payload 处理路径（token、ozon_api_key 明文在帧变量里），默认行为会把凭证
整包送第三方 Sentry。本测试锁定 init kwargs 必须显式关闭。

运行：
    cd worker && PGDATABASE_URL=... PYTHONPATH=src python -m pytest tests/test_sentry_no_locals_v076.py -q
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import utils.sentry_setup as ss


def test_init_disables_local_variables(monkeypatch):
    captured = {}
    monkeypatch.setattr("sentry_sdk.init", lambda **kw: captured.update(kw))
    # init_sentry 的两个早退分支一并绕过：重复初始化守卫 + 测试进程跳过
    # （pytest 下 PYTEST_CURRENT_TEST 恒在，_is_test_process 恒 True）
    monkeypatch.setattr(ss, "_SENTRY_INITIALIZED", False)
    monkeypatch.setattr(ss, "_is_test_process", lambda: False)
    monkeypatch.setenv("SENTRY_DSN", "https://examplePublicKey@o0.ingest.sentry.io/0")
    assert ss.init_sentry() is True
    assert captured.get("include_local_variables") is False
