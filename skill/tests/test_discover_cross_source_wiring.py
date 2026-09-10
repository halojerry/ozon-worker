#!/usr/bin/env python3
"""discover 跨平台静默货源匹配 v1 批3 — discover 接线 + 快照 TDD。

接线矩阵（全 mock，零 Chrome/CDP/网络；test_match_selected_limits 同款 idioms）：
- 换源：胜者值真实写回 candidate.match_1688_url/title/price(+freight) 同槽位，
  快照在场（baseline 1688 原值留证 + platforms + decision + thresholds 回显）
- 不换源：槽位原封不动，快照仍在场（「为什么不换」要有证据）
- 无参照标题：extract_search_keyword=None → 两平台零调用 + skipped 快照
- NotLoggedIn run 级缓存：首候选探测后其余候选不再探测该平台（另一平台照常）
- 平台错误计数：per-run counter 递增 + 该候选该平台跳过（run 汇总行可见）
- 预算执行：慢平台吃满预算 → 次平台按剩余时间放行/省略键
- N 限制：6 个 profitable 候选 N=5 → 只比价 5 个
- 开关关闭：--compare-sources 0 → 零调用零快照零槽位变化（旧路径逐字保持）
- 快照键纪律：无 cookie 键、缺失平台省略键、decision 回显在场
- cloud_probe._assemble_discovery_meta 并入 candidate.discovery_meta
- CLI --compare-sources 默认 5 / 0=关

运行：
    cd skill && .venv314/bin/python -m pytest tests/test_discover_cross_source_wiring.py -q
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.lib import ozon_discovery as od  # noqa: E402
from scripts.lib.cross_source_search import (  # noqa: E402
    NotLoggedIn,
    SearchTimeoutError,
    SourceOffer,
)
from scripts.lib.ozon_discovery import ProductCandidate, match_selected  # noqa: E402

MATCH_1688 = {
    "url": "https://detail.1688.com/offer/1.html",
    "title": "保温杯500ml",
    "price": "30.0",
    "images": [],
    "confidence": 0.9,
    "badge_eff": 0.9,
    "category_name": "餐具 > 保温杯",  # 有值 → _backfill_1688_category 早退（零 AK 调用）
}
BASELINE_URL = MATCH_1688["url"]
BASELINE_PRICE = 30.0


def _cands(n: int) -> list[ProductCandidate]:
    out = []
    for i in range(n):
        c = ProductCandidate(ozon_product_id=str(100 + i), ozon_title="Термос",
                             ozon_price=1000.0, ozon_images=["http://img/x.jpg"])
        c.status = "ok"
        out.append(c)
    return out


def _offer(platform, title="保温杯500ml", price=10.0, freight=0.0, sold=100, url=""):
    return SourceOffer(
        platform=platform, title=title, price=price, freight=freight, sold=sold,
        url=url or f"https://x.example/{platform}/{price}")


class _Clock:
    """脚本化 monotonic 时钟（平台 fake 消耗预算用）。"""

    def __init__(self):
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _run(cands, *, workers=1, match=None, taobao=None, pdd=None,
         compare_sources=5, clock=None, **kw):
    """跑 match_selected：1688 图搜/两平台搜索/CDP 全 mock。

    taobao/pdd: None=零命中([])；list=静态 offers；Exception=恒抛；callable=定制。
    返回 (result, calls)；calls={"taobao": [...], "pdd": [...]}（每项含 keyword/timeout）。
    """
    if match is None:
        match = dict(MATCH_1688)
    calls = {"n": 0, "taobao": [], "pdd": []}

    def _fake_search(cdp_url, images, title, conn=None, mxou_token="", **_):
        idx = calls["n"]
        calls["n"] += 1
        return match(idx) if callable(match) else dict(match)

    def _fake_platform(name, spec):
        def _f(cdp_url, keyword, *, timeout=12.0, cdp=None):
            calls[name].append({"keyword": keyword, "timeout": round(timeout, 1)})
            if isinstance(spec, Exception):
                raise spec
            if callable(spec):
                return spec(keyword=keyword, timeout=timeout)
            return list(spec or [])
        return _f

    patches = [
        mock.patch.object(od, "_search_1688_source", _fake_search),
        mock.patch.object(od, "_discover_workers", return_value=workers),
        mock.patch("scripts.lib.cdp_client.CdpConnection"),
        mock.patch.object(od.time, "sleep", lambda s: None),
        mock.patch("scripts.lib.config_store.get_store_profile", return_value={}),
        mock.patch("scripts.lib.ozon_discovery._query_logistics_from_worker",
                   lambda *a, **k: None),
        mock.patch("scripts.lib.cross_source_search.search_taobao",
                   _fake_platform("taobao", taobao)),
        mock.patch("scripts.lib.cross_source_search.search_pdd",
                   _fake_platform("pdd", pdd)),
    ]
    if clock is not None:
        patches.append(mock.patch("time.monotonic", clock))
    with contextlib.ExitStack() as st:
        for p in patches:
            st.enter_context(p)
        result = match_selected(cands, "http://127.0.0.1:9222",
                                compare_sources=compare_sources, **kw)
    return result, calls


# ──────────────────── 换源 / 不换源 ────────────────────


class TestSwitchWriteBack:
    def test_winner_overwrites_same_slots_and_snapshot_present(self):
        """胜者写回同槽位（字段名不动），快照带 baseline 原值 + decision + thresholds。"""
        cands = _cands(1)
        tb = [_offer("taobao", price=10.0, freight=2.0, sold=77,
                     url="https://item.taobao.com/item.htm?id=99")]
        result, calls = _run(cands, taobao=tb, pdd=[])
        c = result[0]
        assert c.status == "profitable"
        # 槽位写回（字段名不变，URL 平台前缀自证）
        assert c.match_1688_url == "https://item.taobao.com/item.htm?id=99"
        assert c.match_1688_title == "保温杯500ml"
        assert c.match_1688_price == 10.0
        assert c.match_1688_freight_cny == 2.0
        # 快照
        snap = c.discovery_meta["source_comparison"]
        assert set(snap) == {"baseline", "platforms", "decision", "thresholds"}
        assert snap["baseline"]["url"] == BASELINE_URL
        assert snap["baseline"]["price"] == BASELINE_PRICE
        assert snap["baseline"]["freight"] is None  # 未知=None（诚实口径）
        assert snap["baseline"]["landed_cost"] == BASELINE_PRICE  # 运费缺=价 alone
        tb_entry = snap["platforms"]["taobao"]
        assert tb_entry["url"] == "https://item.taobao.com/item.htm?id=99"
        assert tb_entry["price"] == 10.0
        assert tb_entry["sold"] == 77
        assert tb_entry["matched"] is True
        assert tb_entry["switched"] is True
        assert tb_entry["freight_unknown"] is False
        assert 0.0 <= tb_entry["confirm"] <= 1.0
        assert snap["decision"]["switched"] is True
        assert "taobao" in snap["decision"]["reason"]
        assert snap["decision"]["winner"] == "https://item.taobao.com/item.htm?id=99"
        assert snap["thresholds"]["same_min"] > 0
        assert snap["thresholds"]["switch_ratio"] > 0

    def test_winner_freight_none_marks_freight_unknown_and_writes_none(self):
        """胜者运费未知 → freight 槽位写 None（新源运费未知，不冒充 1688 运费）。"""
        cands = _cands(1)
        tb = [_offer("taobao", price=10.0, freight=None,
                     url="https://item.taobao.com/item.htm?id=7")]
        result, _ = _run(cands, taobao=tb, pdd=[])
        c = result[0]
        assert c.match_1688_freight_cny is None
        snap = c.discovery_meta["source_comparison"]
        assert snap["platforms"]["taobao"]["freight_unknown"] is True

    def test_not_switched_slots_untouched_snapshot_present(self):
        """便宜但未达换源线 → 槽位原封不动，快照仍在场（switched=False）。"""
        cands = _cands(1)
        tb = [_offer("taobao", price=29.0, freight=0.0,
                     url="https://item.taobao.com/item.htm?id=5")]
        result, calls = _run(cands, taobao=tb, pdd=[])
        c = result[0]
        assert c.match_1688_url == BASELINE_URL
        assert c.match_1688_price == BASELINE_PRICE
        snap = c.discovery_meta["source_comparison"]
        assert snap["decision"]["switched"] is False
        assert snap["decision"]["winner"] is None
        assert "1688 保持" in snap["decision"]["reason"]
        assert snap["platforms"]["taobao"]["matched"] is True
        assert snap["platforms"]["taobao"]["switched"] is False

    def test_zero_result_platform_recorded_matched_false(self):
        """平台搜到零条（诚实空）→ matched=False 条目在场（不是 skipped）。"""
        cands = _cands(1)
        result, _ = _run(cands, taobao=[], pdd=[])
        entry = result[0].discovery_meta["source_comparison"]["platforms"]["taobao"]
        assert entry["matched"] is False
        assert entry["switched"] is False
        assert entry["price"] is None


# ──────────────────── 跳过 / 缓存 / 计数 ────────────────────


class TestSkipAndRunCache:
    def test_keyword_none_skips_both_platforms(self):
        """无参照标题（剥完全是修饰词）→ 两平台零调用 + skipped 快照。"""
        cands = _cands(1)
        nomatch = dict(MATCH_1688, title="新款 ins 网红 爆款")
        result, calls = _run(cands, match=nomatch, taobao=[], pdd=[])
        assert calls["taobao"] == [] and calls["pdd"] == []
        assert result[0].discovery_meta["source_comparison"] == {"skipped": "无参照标题"}

    def test_not_logged_in_cached_for_rest_of_run_other_platform_still_probed(self):
        """首候选 taobao 未登录 → 后续候选不再探测 taobao（pdd 照常两次）。"""
        cands = _cands(2)
        pd = [_offer("pdd", price=10.0, url="https://mobile.yangkeduo.com/goods.html?goods_id=1")]
        result, calls = _run(cands, taobao=NotLoggedIn("taobao", "无 _m_h5_tk"), pdd=pd)
        assert len(calls["taobao"]) == 1          # 只首候选探测一次
        assert len(calls["pdd"]) == 2             # 另一平台不受牵连
        for c in result:
            entry = c.discovery_meta["source_comparison"]["platforms"]["taobao"]
            assert entry == {"skipped": "not_logged_in"}
        # pdd 更便宜且过同款确认 → 照常换源
        assert result[0].match_1688_url.startswith("https://mobile.yangkeduo.com/")

    def test_platform_error_counter_increments_and_platform_skipped(self):
        """平台错误：per-run 计数递增，该候选该平台记 skipped+count；pdd 照常。"""
        cands = _cands(2)
        result, calls = _run(
            cands,
            taobao=SearchTimeoutError("taobao 解析超时"),
            pdd=[_offer("pdd", price=28.0)],  # 未达换源线 → 只记录不换
        )
        assert len(calls["taobao"]) == 2          # 错误不写负缓存：下候选仍重试一次
        assert len(calls["pdd"]) == 2
        for i, c in enumerate(result):
            entry = c.discovery_meta["source_comparison"]["platforms"]["taobao"]
            assert entry == {"skipped": "error", "count": i + 1}  # 1 → 2 递增

    def test_run_summary_line_mentions_broken_platform(self, caplog):
        """run 汇总行：比价/换源/未登录/平台错误计数可见（不静默消失）。"""
        cands = _cands(1)
        with caplog.at_level(logging.INFO, logger="scripts.lib.ozon_discovery"):
            _run(cands, taobao=NotLoggedIn("taobao", "x"), pdd=[])
        text = " ".join(r.getMessage() for r in caplog.records)
        assert "跨源比价" in text
        assert "taobao" in text


# ──────────────────── 预算 / N 限制 / 开关 ────────────────────


class TestBudgetAndLimits:
    def test_slow_platform_timeout_other_still_runs(self):
        """慢平台吃满单平台预算（12s 超时）→ 记 error；次平台按剩余预算照常。"""
        clock = _Clock()

        def _slow_taobao(**_):
            clock.now += 12.0  # 吃满单平台预算后超时
            raise SearchTimeoutError("taobao 预算耗尽")

        cands = _cands(1)
        result, calls = _run(cands, taobao=_slow_taobao,
                             pdd=[_offer("pdd", price=28.0)], clock=clock)
        assert len(calls["pdd"]) == 1
        assert calls["pdd"][0]["timeout"] == 12.0  # 剩余 13s → 上限 12s
        entry = result[0].discovery_meta["source_comparison"]["platforms"]["taobao"]
        assert entry == {"skipped": "error", "count": 1}

    def test_budget_exhausted_second_platform_omitted(self):
        """首平台吃穿整个候选预算（24.5s）→ 次平台不再发起、快照省略键。"""
        clock = _Clock()

        def _eat_budget(**_):
            clock.now += 24.5
            return [_offer("taobao", price=29.0)]

        cands = _cands(1)
        result, calls = _run(cands, taobao=_eat_budget, pdd=[], clock=clock)
        assert calls["pdd"] == []
        snap = result[0].discovery_meta["source_comparison"]
        assert "taobao" in snap["platforms"]
        assert "pdd" not in snap["platforms"]     # 缺失平台省略键

    def test_n_limit_only_top_n_candidates_compared(self):
        """6 个 profitable 候选 N=5 → 只比价 5 个（按利润率降序取头）。"""
        cands = _cands(6)
        _, calls = _run(cands, taobao=[], pdd=[], compare_sources=5)
        assert len(calls["taobao"]) == 5
        assert len(calls["pdd"]) == 5

    def test_compare_sources_zero_fully_off(self):
        """0=完全关闭：零调用、零快照、槽位零变化（旧路径逐字保持）。"""
        cands = _cands(2)
        result, calls = _run(cands, taobao=[_offer("taobao", price=1.0)],
                             pdd=[_offer("pdd", price=1.0)], compare_sources=0)
        assert calls["taobao"] == [] and calls["pdd"] == []
        for c in result:
            assert c.discovery_meta == {}
            assert c.match_1688_url == BASELINE_URL
            assert c.match_1688_price == BASELINE_PRICE


# ──────────────────── 阈值解析（CLI/env/默认）────────────────────


class TestResolveCompareSources:
    def test_default_is_5(self, monkeypatch):
        monkeypatch.delenv("DISCOVER_COMPARE_SOURCES", raising=False)
        assert od._resolve_compare_sources(None) == 5

    def test_explicit_zero_is_off(self):
        assert od._resolve_compare_sources(0) == 0

    def test_env_hot_override(self, monkeypatch):
        monkeypatch.setenv("DISCOVER_COMPARE_SOURCES", "3")
        assert od._resolve_compare_sources(None) == 3
        monkeypatch.setenv("DISCOVER_COMPARE_SOURCES", "0")
        assert od._resolve_compare_sources(None) == 0

    def test_invalid_env_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("DISCOVER_COMPARE_SOURCES", "not-a-number")
        assert od._resolve_compare_sources(None) == 5
        monkeypatch.setenv("DISCOVER_COMPARE_SOURCES", "-2")
        assert od._resolve_compare_sources(None) == 5

    def test_match_selected_env_off_via_env(self, monkeypatch):
        """match_selected(compare_sources=None) + env=0 → 零调用（热关闭）。"""
        monkeypatch.setenv("DISCOVER_COMPARE_SOURCES", "0")
        cands = _cands(1)
        calls = {"n": 0}

        def _fake_search(*a, **k):
            calls["n"] += 1
            return dict(MATCH_1688)

        with mock.patch.object(od, "_search_1688_source", _fake_search), \
             mock.patch.object(od, "_discover_workers", return_value=1), \
             mock.patch("scripts.lib.cdp_client.CdpConnection"), \
             mock.patch.object(od.time, "sleep", lambda s: None), \
             mock.patch("scripts.lib.config_store.get_store_profile", return_value={}), \
             mock.patch("scripts.lib.ozon_discovery._query_logistics_from_worker",
                        lambda *a, **k: None):
            result = match_selected(cands, "http://127.0.0.1:9222")
        assert calls["n"] == 1
        assert all(not c.discovery_meta for c in result)


# ──────────────────── 快照键纪律 ────────────────────


class TestSnapshotDiscipline:
    def test_snapshot_never_contains_cookies_and_matches_decision(self):
        cands = _cands(1)
        tb = [_offer("taobao", price=10.0, url="https://item.taobao.com/item.htm?id=9")]
        result, _ = _run(cands, taobao=tb, pdd=[])
        snap = result[0].discovery_meta["source_comparison"]
        raw = json.dumps(snap, ensure_ascii=False)
        assert "cookie" not in raw.lower()
        assert set(snap) == {"baseline", "platforms", "decision", "thresholds"}
        assert set(snap["decision"]) == {"winner", "reason", "switched"}

    def test_missing_platform_key_omitted_not_null(self):
        """预算耗尽场景：pdd 未探测 → 键整体缺席（不是 null/空壳）。"""
        clock = _Clock()

        def _eat(**_):
            clock.now += 24.5
            return []

        cands = _cands(1)
        result, _ = _run(cands, taobao=_eat, pdd=[], clock=clock)
        assert "pdd" not in result[0].discovery_meta["source_comparison"]["platforms"]


# ──────────────────── 信封/CLI 接线 ────────────────────


class TestEnvelopeAndCliWiring:
    def test_assemble_discovery_meta_merges_source_comparison(self):
        """cloud_probe._assemble_discovery_meta 整包并入 candidate.discovery_meta
        （采集箱可见可改的透传通道；空 dict 并入零增键）。"""
        from scripts.cloud_probe import _assemble_discovery_meta

        c = ProductCandidate(ozon_product_id="p1", ozon_title="t", ozon_price=100.0)
        assert "source_comparison" not in _assemble_discovery_meta(c)  # 空=零增键
        c.discovery_meta = {"source_comparison": {
            "baseline": {"url": BASELINE_URL, "price": BASELINE_PRICE},
            "platforms": {},
            "decision": {"winner": None, "reason": "r", "switched": False},
            "thresholds": {"same_min": 0.45, "switch_ratio": 0.9},
        }}
        meta = _assemble_discovery_meta(c)
        assert meta["source_comparison"]["decision"]["switched"] is False
        assert meta["source_comparison"]["baseline"]["url"] == BASELINE_URL

    def test_cli_discover_argument_default_and_off(self):
        from scripts.cli import build_arg_parser

        args = build_arg_parser().parse_args(["discover"])
        assert args.compare_sources == 5
        args0 = build_arg_parser().parse_args(["discover", "--compare-sources", "0"])
        assert args0.compare_sources == 0


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
