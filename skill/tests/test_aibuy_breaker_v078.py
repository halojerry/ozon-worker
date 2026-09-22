#!/usr/bin/env python3
"""批A A4（fix/skill-silent-cdp-v1）：aibuy 批内熔断回归。

背景：aibuy mtop 通道整体故障时（风控/接口变更），search_by_image_aibuy 对每个
候选都完整走一遍 token→upload→search 失败链再降级 CDP，一批 N 候选白烧 N 次。
本文件锁定：模块级连续失败计数（空/异常 +1，成功归零）；≥3 次熔断打开 600s，
窗内调用直接返回 [] 且底层 mtop 零调用、只打一行日志；成功复位；600s 过期恢复
（半开放行）。既有 token 级 600s claim 闸不受影响（本批级补充）。

运行：
    cd skill && .venv314/bin/python -m pytest tests/test_aibuy_breaker_v078.py -q
"""
from __future__ import annotations

import logging
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.lib import ozon_image_search as ois  # noqa: E402


@pytest.fixture(autouse=True)
def _sealed(monkeypatch):
    """摘除 pytest 守卫启用熔断（生产语义）+ 隔离缓存 + 每用例复位计数。"""
    monkeypatch.setattr(ois, "_under_pytest", lambda: False)
    monkeypatch.setattr(ois, "cache_get", lambda ns, key: None)
    monkeypatch.setattr(ois, "cache_set", lambda *a, **k: None)
    ois._aibuy_breaker_reset()
    yield
    ois._aibuy_breaker_reset()


def _wire_pipeline(monkeypatch, search_results: list):
    """打通 aibuy 主路径：token 直供 + upload 空转 + search 可编程。"""
    monkeypatch.setattr(ois, "_read_aibuy_token",
                        lambda: {"_m_h5_tk": "tk_1", "_m_h5_tk_enc": "enc",
                                 "tfstk": "tf", "isg": "isg"})
    monkeypatch.setattr(ois, "_aibuy_image_upload", lambda url, tk: "")
    calls: list[int] = []

    def _search(img, tk, region="", page_size=20):
        calls.append(1)
        return search_results.pop(0) if search_results else []

    monkeypatch.setattr(ois, "_aibuy_image_search", _search)
    return calls


def test_breaker_opens_after_three_failures(monkeypatch, caplog):
    """3 连败 → 第 4 次快速返回 []，底层 mtop 零调用，只打一行熔断日志。"""
    calls = _wire_pipeline(monkeypatch, search_results=[])
    with caplog.at_level(logging.WARNING, logger="scripts.lib.ozon_image_search"):
        for _ in range(3):
            assert ois.search_by_image_aibuy("https://img/1.jpg") == []
        assert calls == [1, 1, 1]
        assert ois.search_by_image_aibuy("https://img/1.jpg") == []
    assert calls == [1, 1, 1], "熔断打开后底层 search 不得再被调用"
    breaker_logs = [r for r in caplog.records if "熔断" in r.message]
    assert len(breaker_logs) == 1, f"熔断只打一行日志，实际 {len(breaker_logs)} 行"
    assert "600s" in breaker_logs[0].getMessage()


def test_success_resets_counter(monkeypatch):
    """成功归零：2 败 + 1 成 + 2 败 → 计数 2 未达阈值，第 6 次仍走真实路径。"""
    calls = _wire_pipeline(
        monkeypatch,
        search_results=[[], [], [{"id": "1"}], [], []])
    for _ in range(2):
        ois.search_by_image_aibuy("https://img/1.jpg")
    assert ois._AIBUY_BREAKER_STATE["fails"] == 2
    assert ois.search_by_image_aibuy("https://img/1.jpg") != []
    assert ois._AIBUY_BREAKER_STATE["fails"] == 0, "成功必须复位计数"
    ois.search_by_image_aibuy("https://img/1.jpg")
    ois.search_by_image_aibuy("https://img/1.jpg")
    assert ois._AIBUY_BREAKER_STATE["fails"] == 2
    assert ois.search_by_image_aibuy("https://img/1.jpg") == []
    assert len(calls) == 6, "计数 2 < 阈值 3，不得熔断"


def test_breaker_expires_after_cooldown(monkeypatch):
    """熔断 600s 过期 → 半开恢复，放行真实调用（时间可用 monkeypatch 拨动）。"""
    calls = _wire_pipeline(monkeypatch, search_results=[])
    for _ in range(3):
        ois.search_by_image_aibuy("https://img/1.jpg")
    assert ois.search_by_image_aibuy("https://img/1.jpg") == []
    assert len(calls) == 3
    real_now = time.time()
    monkeypatch.setattr(ois, "_now", lambda: real_now + 601)
    assert ois.search_by_image_aibuy("https://img/1.jpg") == []
    assert len(calls) == 4, "冷却窗过期后应放行一次真实调用（半开）"
    assert ois._AIBUY_BREAKER_STATE["fails"] == 1, "半开失败重新计数"


def test_reset_hook_clears_state(monkeypatch):
    """_aibuy_breaker_reset 可测钩子：任意状态一键清零关断。"""
    _wire_pipeline(monkeypatch, search_results=[])
    for _ in range(3):
        ois.search_by_image_aibuy("https://img/1.jpg")
    assert ois._AIBUY_BREAKER_STATE["fails"] >= 3
    ois._aibuy_breaker_reset()
    assert ois._AIBUY_BREAKER_STATE == {"fails": 0, "opened_at": 0.0}
