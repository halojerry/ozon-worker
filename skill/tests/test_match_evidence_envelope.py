#!/usr/bin/env python3
"""图搜匹配证据 extensions.match_evidence 透传回归（v0.66.1）。

背景：worker 类目学习闭环（L0 authoritative）需要图搜置信信号做门槛，但
discover/follow 信封此前不带任何匹配置信元数据（confidence/badge_eff/
search_method/trusted 全部终止在 skill 本地）。本批把它们透传为信封
extensions.match_evidence = {method, confidence, badge_eff, trusted}。

字段来源（以实际落盘字段为准）：
- discover：ozon_discovery match_selected._process_match 只把
  confidence/badge_eff 落 candidate（match_confidence/match_badge_eff，
  ozon_discovery.py:139-142/783-786）；search_method/trusted/idx 未下传到
  候选 → discover 信封的 match_evidence 无 method 键（字段缺失省略），
  trusted 取 badge_eff>=1.0（matchBadgeFull 直通放行语义）。
- follow：best dict 带 _pick_best_match/_attach_match_meta 的 confidence/
  badge_eff，method 取 follow_sell_cloud 本地 search_method（aibuy/cdp/
  image/text）；best 未下传原图搜位置 idx → trusted 按 method=="aibuy"
  近似（官方引擎已按图相似度排好序）。

运行：
    cd skill && .venv314/bin/python -m pytest tests/test_match_evidence_envelope.py -q
"""
from __future__ import annotations

import json
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.lib.ozon_discovery import ProductCandidate  # noqa: E402
from scripts import cloud_probe  # noqa: E402

URL = "https://www.ozon.ru/product/avtopoilka-4767514314/"
CDP_DATA = {"success": True, "images": ["http://img/ozon/1.jpg"], "title": "Автопоилка"}
AIBUY_RESULTS = [{
    "id": "980815374096", "title": "宠物饮水器", "price": "5.5",
    "image": "http://img/1688/1.jpg", "badge": "",
}]
BEST = {
    "id": "980815374096", "title": "宠物饮水器", "badge_score": 0,
    "confidence": 0.82, "badge_eff": 0.6, "score": 80.0,
}


# ─────────────────────────── discover 路径 ───────────────────────────

def _mk_candidate(**overrides) -> ProductCandidate:
    """构造 ProductCandidate（match 决策元数据可覆盖）。"""
    c = ProductCandidate(
        ozon_product_id="4767514314",
        ozon_title="Автопоилка для кошек 2л",
        ozon_price=1290.0,
    )
    c.match_1688_url = "https://detail.1688.com/offer/980815374096.html"
    c.competing_sellers = 4
    c.weight_g = 0
    c.dimensions_mm = {}
    c.ozon_category = {}
    # 默认 = 无匹配元数据（dataclass 默认 0.0/""），由 overrides 带入真实匹配值
    c.match_confidence = 0.0
    c.match_badge_eff = 0.0
    for k, v in overrides.items():
        setattr(c, k, v)
    return c


_RETRY_DEFAULT = object()  # 哨兵：默认走完整信封；传 None 强制走降级裸信封路径


def _discover_envelope(cand: ProductCandidate, *, retry_returns=_RETRY_DEFAULT) -> dict | None:
    """mock build_graph_envelope_with_retry / 凭证跑 build_envelope_from_discovery。"""
    env = {
        "token": "sk-test",
        "ozon_client_id": "123",
        "ozon_api_key": "key",
        "envelope": {
            "draft": {"item_id": "980815374096", "title": "x", "images": [],
                      "weight": 0, "dimensions": {"length": 0, "width": 0, "height": 0}},
            "source": {"purchase_url": "u", "purchase_cost": 1.0},
            "extensions": {},
        },
    }
    ret = env if retry_returns is _RETRY_DEFAULT else retry_returns
    with mock.patch.object(cloud_probe, "build_graph_envelope_with_retry",
                           return_value=ret), \
         mock.patch("scripts.lib.config_store.get_mxou_token", return_value="sk-test"):
        return cloud_probe.build_envelope_from_discovery(
            cand, {"client_id": "123", "api_key": "key"})


def test_discover_envelope_carries_match_evidence():
    """candidate 带 match_confidence/match_badge_eff → extensions.match_evidence 注入。

    discover 候选未下传 search_method（ozon_discovery 仅落 confidence/badge_eff），
    method 键按「字段缺失省略」不注入；trusted = badge_eff>=1.0。
    """
    cand = _mk_candidate(match_confidence=0.87, match_badge_eff=0.93)
    result = _discover_envelope(cand)
    assert result is not None
    mev = result["envelope"]["extensions"]["match_evidence"]
    assert mev["confidence"] == 0.87
    assert mev["badge_eff"] == 0.93
    assert mev["trusted"] is False          # badge_eff < 1.0 非 matchBadgeFull
    assert "method" not in mev              # discover 候选未下传 channel → 省略
    json.dumps(result, ensure_ascii=False)  # JSON 可序列化（提交契约）


def test_discover_full_badge_marks_trusted():
    """badge_eff==1.0（matchBadgeFull 直通放行）→ trusted=True。"""
    cand = _mk_candidate(match_confidence=0.5, match_badge_eff=1.0)
    result = _discover_envelope(cand)
    assert result is not None
    mev = result["envelope"]["extensions"]["match_evidence"]
    assert mev["confidence"] == 0.5
    assert mev["badge_eff"] == 1.0
    assert mev["trusted"] is True


def test_discover_no_match_meta_no_key():
    """无匹配元数据（candidate 默认 0.0）→ extensions 无 match_evidence 键。"""
    cand = _mk_candidate()  # match_confidence=0 / match_badge_eff=0
    result = _discover_envelope(cand)
    assert result is not None
    assert "match_evidence" not in result["envelope"]["extensions"]


def test_discover_bare_envelope_fallback_no_key():
    """裸信封降级路径（AK+CDP 全失败 → build_graph_envelope_with_retry 返 None）
    → 即使候选带匹配元数据也不注入 match_evidence（降级段不组装证据）。"""
    cand = _mk_candidate(match_confidence=0.87, match_badge_eff=0.93)
    result = _discover_envelope(cand, retry_returns=None)
    assert result is not None
    assert "match_evidence" not in result["envelope"]["extensions"]


# ─────────────────────────── follow 路径 ───────────────────────────

def _run_follow() -> dict:
    """mock 完整外部依赖跑 follow_sell_cloud（dry-run），走 aibuy 图搜通道。"""
    with mock.patch("scripts.lib.cache.cache_get", return_value=None), \
         mock.patch("scripts.lib.cache.cache_set"), \
         mock.patch("scripts.lib.config_store._require_auth"), \
         mock.patch.object(cloud_probe, "_get_ozon_credentials",
                           return_value={"client_id": "1", "api_key": "k"}), \
         mock.patch("scripts.lib.config_store.get_mxou_token", return_value="sk"), \
         mock.patch.object(cloud_probe, "_cached_ozon_scrape", return_value=CDP_DATA), \
         mock.patch("scripts.lib.chrome_launcher.ensure_chrome_cdp", return_value=(True, "ok")), \
         mock.patch("scripts.cli._chrome_profile_dir", return_value="/tmp/profile"), \
         mock.patch("scripts.lib.cdp_client.CdpConnection"), \
         mock.patch("scripts.lib.ozon_seller_analytics.fetch_sales_analytics", return_value={}), \
         mock.patch("scripts.lib.ozon_image_search.search_by_image_aibuy", return_value=AIBUY_RESULTS), \
         mock.patch("scripts.lib.ozon_image_search.search_by_image_cdp", return_value=[]), \
         mock.patch("scripts.lib.ozon_discovery._pick_best_match", return_value=BEST), \
         mock.patch("scripts.lib.source_candidates.spawn_source_report"), \
         mock.patch.object(cloud_probe, "build_graph_envelope_with_retry",
                           return_value={
                               "token": "sk", "ozon_client_id": "1", "ozon_api_key": "k",
                               "envelope": {
                                   "draft": {"item_id": "980815374096", "title": "x",
                                             "images": [], "weight": 0,
                                             "dimensions": {"length": 0, "width": 0,
                                                            "height": 0}},
                                   "source": {"purchase_url": "u", "purchase_cost": 1.0},
                                   "extensions": {},
                               },
                           }), \
         mock.patch("scripts.lib.config_store.get_store_profile", return_value={}):
        return cloud_probe.follow_sell_cloud(URL, auto_submit=False, store_id="s1")


def test_follow_envelope_carries_match_evidence():
    """follow best 带 confidence/badge_eff（method=aibuy）→ match_evidence 注入。"""
    r = _run_follow()
    assert r.get("success") is True, r
    assert r.get("envelope_built") is True
    mev = r["envelope"]["envelope"]["extensions"]["match_evidence"]
    assert mev["method"] == "aibuy"
    assert mev["confidence"] == 0.82
    assert mev["badge_eff"] == 0.6
    # aibuy 官方引擎已按图相似度排好序（trusted_source 判据），best 未下传 idx
    # → 按 method=="aibuy" 近似 trusted=True（_assemble_match_evidence 注释）
    assert mev["trusted"] is True
    json.dumps(r["envelope"], ensure_ascii=False)


def test_assemble_match_evidence_empty_when_no_signals():
    """组装函数无有效信号 → 返回 {}（调用方不注入，防空壳）。"""
    assert cloud_probe._assemble_match_evidence() == {}
    assert cloud_probe._assemble_match_evidence(method="aibuy") == {}
    assert cloud_probe._assemble_match_evidence(confidence=0, badge_eff=0) == {}


if __name__ == "__main__":
    import traceback

    failed = total = 0
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
