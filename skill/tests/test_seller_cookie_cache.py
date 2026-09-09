#!/usr/bin/env python3
"""P0-3 seller 会话 cookie 磁盘缓存单测（2026-09-09 审计靶点一③）。

用户实机症状：Ozon seller cookie 无任何磁盘兜底——活 Chrome 现读失败即
wait_for_seller_login 开 seller 页，每次 discover/queries/跟卖富化都可能重演。

修复契约（get_seller_session_cookies，ozon_seller_analytics.py）：
  活读成功 → 写穿 30min 磁盘缓存；活读失败 → 回落含 sc_company_id 的缓存
  快照；双缺 → {}（调用方既有 CDP/降级链兜底）。

运行：
    cd skill && .venv314/bin/python -m pytest tests/test_seller_cookie_cache.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.lib import cache, ozon_seller_analytics as osa


@pytest.fixture()
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path / "cache")
    return tmp_path / "cache"


def _live(cookies):
    return lambda cdp_url=None: cookies


def test_live_success_writes_through_cache(isolated_cache, monkeypatch):
    monkeypatch.setattr(
        osa, "_fetch_seller_session_cookies",
        _live({"sc_company_id": "123", "sess": "abc"}))
    got = osa.get_seller_session_cookies()
    assert got["sc_company_id"] == "123"
    cached = cache.cache_get(osa.SELLER_COOKIE_CACHE_NS, osa.SELLER_COOKIE_CACHE_KEY)
    assert cached is not None and cached["sc_company_id"] == "123"


def test_live_fail_falls_back_to_cached_snapshot(isolated_cache, monkeypatch):
    cache.cache_set(osa.SELLER_COOKIE_CACHE_NS, osa.SELLER_COOKIE_CACHE_KEY,
                    {"sc_company_id": "456", "old": "1"}, ttl=1800)
    monkeypatch.setattr(osa, "_fetch_seller_session_cookies", _live({}))
    got = osa.get_seller_session_cookies()
    assert got["sc_company_id"] == "456"


def test_cached_without_company_id_ignored(isolated_cache, monkeypatch):
    """快照缺 sc_company_id（不完整抓取）不可用 → {}，不放行残缺 cookie。"""
    cache.cache_set(osa.SELLER_COOKIE_CACHE_NS, osa.SELLER_COOKIE_CACHE_KEY,
                    {"sess": "x"}, ttl=1800)
    monkeypatch.setattr(osa, "_fetch_seller_session_cookies", _live({}))
    assert osa.get_seller_session_cookies() == {}


def test_both_miss_returns_empty(isolated_cache, monkeypatch):
    monkeypatch.setattr(osa, "_fetch_seller_session_cookies", _live({}))
    assert osa.get_seller_session_cookies() == {}
    assert cache.cache_get(osa.SELLER_COOKIE_CACHE_NS, osa.SELLER_COOKIE_CACHE_KEY) is None
