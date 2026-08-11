#!/usr/bin/env python3
"""D5-A: probe_1688_page 预探测延迟可通过 settings.json 配置（TDD）。

`probe_interval_seconds`（默认 2.5）控制 service.py probe_1688_page 的
pre-probe 随机延迟：randint(interval*1000, interval*2500)。

① get_setting 返回 5.0 → randint(5000, 12500)；
② get_setting 返回 None → 默认 2.5 → randint(2500, 6250)；
③ get_setting 返回空串 → 回退 2.5 → randint(2500, 6250)。

运行：
    cd skill && .venv314/bin/python -m pytest tests/test_service_probe_interval.py -q
"""
from __future__ import annotations

import os
import random
import sys
from unittest import mock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class _BailAfterDelay(Exception):
    """mock time.sleep 抛出的哨兵：记录延迟后立即终止探测，避免真实浏览器启动。"""


def _run_probe(get_setting_value):
    """跑到 pre-probe 延迟处，返回 (randint_bounds, sleep_seconds)。"""
    from scripts.capabilities.browser_probe import service as svc

    url = "https://detail.1688.com/offer/980815374096.html"
    recorded = {}

    def _fake_sleep(seconds):
        recorded["sleep"] = seconds
        raise _BailAfterDelay()

    with mock.patch("scripts.lib.cache.cache_get", return_value=None), \
         mock.patch.object(svc, "_find_cached_probe", return_value=None), \
         mock.patch("scripts.lib.config_store.get_setting", return_value=get_setting_value), \
         mock.patch("random.randint", wraps=random.randint) as m_randint, \
         mock.patch("time.sleep", side_effect=_fake_sleep):
        try:
            svc.probe_1688_page(url)
        except _BailAfterDelay:
            pass
        else:
            pytest.fail("probe 未到达预探测延迟处（time.sleep 未被调用）")
    assert m_randint.call_count == 1, f"randint 应只调用一次，实际 {m_randint.call_count}"
    return m_randint.call_args.args, recorded["sleep"]


def test_probe_interval_from_setting_5s():
    """get_setting=5.0 → randint(5000, 12500)，sleep 在 5.0-12.5s。"""
    bounds, sleep_seconds = _run_probe(5.0)
    assert bounds == (5000, 12500), f"randint 上下界应从配置推导，实际 {bounds}"
    assert 5.0 <= sleep_seconds <= 12.5, f"sleep 应在 [5.0, 12.5]s，实际 {sleep_seconds}"


def test_probe_interval_default_when_setting_none():
    """get_setting=None → 默认 2.5 → randint(2500, 6250)。"""
    bounds, sleep_seconds = _run_probe(None)
    assert bounds == (2500, 6250), f"默认上下界应为 (2500, 6250)，实际 {bounds}"
    assert 2.5 <= sleep_seconds <= 6.25, f"sleep 应在 [2.5, 6.25]s，实际 {sleep_seconds}"


def test_probe_interval_fallback_when_setting_empty():
    """get_setting='' → 回退默认 2.5 → randint(2500, 6250)。"""
    bounds, sleep_seconds = _run_probe("")
    assert bounds == (2500, 6250), f"空配置应回退 (2500, 6250)，实际 {bounds}"
    assert 2.5 <= sleep_seconds <= 6.25, f"sleep 应在 [2.5, 6.25]s，实际 {sleep_seconds}"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
