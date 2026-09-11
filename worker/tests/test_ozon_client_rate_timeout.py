# -*- coding: utf-8 -*-
"""BL-03: ozon_client 限流器 acquire 超时返 False 的 fail-open 告警测试。

背景：_rate_limiter.acquire(endpoint) 阻塞式超时返 False，此前返回值被忽略
（fail-open 静默）——限流等待失败无任何痕迹。改后：False → warning 告警，
请求仍放行（不 raise、不改重试结构——per-credential 桶隔离留 B2-β 设计）。

说明：ozon_client 走 utils/http_session 共享连接池 session（v0.14 D2），
非裸 requests.post——测试替换 http_session.session 桩即等价 mock 请求层。

运行：cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_ozon_client_rate_timeout.py -q
"""
from __future__ import annotations

import logging
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from utils import http_session, ozon_client


class _FakeResp:
    """requests.Response 最小桩（2xx 快乐路径）。

    payload 的 result 须为 dict——/v3/product/import 的 _summarize_response
    会取 result.task_id（str 会 AttributeError）。
    """

    ok = True
    status_code = 200
    text = ""

    def json(self):
        return {"result": {"task_id": "t-1"}}

    def raise_for_status(self):
        return None


def _patch_transport(monkeypatch, posts: list) -> None:
    """替换共享 session 为桩 session，记录发出的 URL。"""

    def _fake_post(url, json=None, headers=None, timeout=None):
        posts.append(url)
        return _FakeResp()

    monkeypatch.setattr(http_session, "session", SimpleNamespace(post=_fake_post))


def test_acquire_false_warns_and_request_still_sent(monkeypatch, caplog):
    """acquire 返 False（等待超时）→ warning 告警 + 请求仍发出（fail-open）。"""
    posts: list = []
    _patch_transport(monkeypatch, posts)
    monkeypatch.setattr(
        ozon_client._rate_limiter, "acquire", lambda endpoint, timeout=30.0: False,
    )
    with caplog.at_level(logging.WARNING, logger="ozon.api"):
        out = ozon_client.ozon_post("cid", "key", "/v3/product/import", {"k": 1})
    assert posts == ["https://api-seller.ozon.ru/v3/product/import"], "fail-open：请求必须仍发出"
    assert out == {"result": {"task_id": "t-1"}}, "调用方照常拿到响应"
    warnings = [
        r for r in caplog.records
        if r.levelno == logging.WARNING and "限流器" in r.getMessage()
    ]
    assert warnings, "acquire False 必须留 warning 告警"


def test_acquire_true_no_warning(monkeypatch, caplog):
    """acquire 正常获取令牌 → 不告警，请求照常发出。"""
    posts: list = []
    _patch_transport(monkeypatch, posts)
    monkeypatch.setattr(
        ozon_client._rate_limiter, "acquire", lambda endpoint, timeout=30.0: True,
    )
    with caplog.at_level(logging.WARNING, logger="ozon.api"):
        ozon_client.ozon_post("cid", "key", "/v3/product/import", {})
    assert posts, "请求正常发出"
    assert not [r for r in caplog.records if "限流器" in r.getMessage()], \
        "成功获取令牌不得告警"
