#!/usr/bin/env python3
"""D3 L3: --review 人工评审暂停（TDD RED→GREEN）。

- discover --review：弱匹配候选逐个确认——y → review_decision=approved 提交；
  n → agent_reject + write_review_record，且 auto-submit 排除被拒候选
- discover 默认（无 --review）：不触发评审输入（零回归）
- follow_sell_cloud(review=True)：展示候选 + 接受/改选/拒绝；拒绝 →
  no_relevant_match=True 不组装信封不提交；review=False 无任何 input
- cmd_follow 把 --review 透传给 follow_sell_cloud

运行：
    cd skill && .venv314/bin/python -m pytest tests/test_cli_review.py -q
"""
from __future__ import annotations

import argparse
import io
import os
import sys
from contextlib import ExitStack
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.lib.ozon_discovery import ProductCandidate  # noqa: E402


def _profitable(pid="p1", title="Автопоилка для кошек",
                url="https://detail.1688.com/offer/1001.html",
                conf=0.9, badge_eff=1.0):
    c = ProductCandidate(ozon_product_id=pid, ozon_title=title, ozon_price=1500.0)
    c.status = "profitable"
    c.match_1688_url = url
    c.match_1688_title = "宠物自动饮水器"
    c.match_confidence = conf
    c.match_badge_eff = badge_eff
    c.has_analytics = False
    c.competing_sellers = 7
    c.rating = 4.5
    return c


def _discover_args(**overrides):
    defaults = dict(
        url="", keyword="поилка", max_products=50, min_margin=15.0, fx_rate=0.075,
        store="", no_analytics=False, min_price=0, max_price=0, brand_filter="nobrand",
        rules="monthly_sales>=1", export="", output="", auto_submit=True,
        fission=False, max_depth=2, allow_depth_3=False, max_total_products=300,
        time_budget=600.0, max_sellers_per_product=20, max_products_per_seller=15,
        non_interactive=False, blue_ocean_source="", blue_ocean_csv="",
        china=False, local=False, review=False,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _run_discover(args, candidates, selected, input_side_effect=("y", "y"),
                  extra_patches=()):
    """跑完整 cmd_discover（采集/匹配 mock），返回 (rc, stdout)。"""
    from scripts import cli
    patches = [
        mock.patch("scripts.lib.chrome_launcher.ensure_chrome_cdp",
                   return_value=(True, "ok")),
        mock.patch("scripts.lib.ozon_discovery.collect_and_analyze",
                   return_value=candidates),
        mock.patch("scripts.lib.ozon_discovery.apply_selection_rules",
                   return_value=selected),
        mock.patch("scripts.lib.ozon_discovery.match_selected"),
        mock.patch("scripts.lib.config_store.get_mxou_token", return_value=""),
        mock.patch("scripts.lib.config_store.get_store_profile", return_value={}),
        mock.patch("scripts.lib.config_store.get_store", return_value={}),
        mock.patch("scripts.lib.config_store.get_setting", return_value=False),
        mock.patch("scripts.lib.review_log.write_review_record"),
        mock.patch("builtins.input", side_effect=input_side_effect),
    ] + list(extra_patches)
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            rc = cli.cmd_discover(args)
    return rc, out.getvalue()


# ── discover --review：y 接受 / n 拒绝 ────────────────────────────────────

def test_review_weak_accept_submits_and_records_approved():
    """--review + 弱匹配候选 y → review_decision=approved、提交、记录 approved。"""
    strong = _profitable("p1", "Товар один")
    weak = _profitable("p2", "Товар два", conf=0.2, badge_eff=0.0)
    submitted: list[str] = []

    def _build(c, store_config, store_id=""):
        return {"draft": {"title": c.ozon_title, "item_id": c.ozon_product_id}}

    def _submit(envelope):
        submitted.append(envelope["draft"]["item_id"])
        return {"ok": True, "task_id": f"T-{envelope['draft']['item_id']}"}

    args = _discover_args(auto_submit=True, review=True)
    rc, out = _run_discover(
        args, [strong, weak], [strong, weak],
        input_side_effect=("y", "y"),  # 评审 y → 确认 y
        extra_patches=[
            mock.patch("scripts.cloud_probe.build_envelope_from_discovery",
                       side_effect=_build),
            mock.patch("scripts.cloud_probe.submit_envelope", side_effect=_submit),
        ],
    )
    assert rc == 0
    assert weak.review_decision == "approved", "y 应标记 approved"
    assert sorted(submitted) == ["p1", "p2"], f"approved 候选应提交, got {submitted}"
    assert "人工评审" in out, "评审界面应打印"


def test_review_weak_reject_records_and_blocks_submit():
    """--review + 弱匹配候选 n → agent_reject 写 review_log，且该候选不提交。"""
    weak = _profitable("p1", "Товар один", conf=0.1, badge_eff=0.0)
    submitted: list[str] = []

    def _build(c, store_config, store_id=""):
        return {"draft": {"title": c.ozon_title, "item_id": c.ozon_product_id}}

    def _submit(envelope):
        submitted.append(envelope["draft"]["item_id"])
        return {"ok": True, "task_id": "T-1"}

    args = _discover_args(auto_submit=True, review=True)
    rc, out = _run_discover(
        args, [weak], [weak],
        input_side_effect=("n",),  # 评审拒绝 → to_submit 空 → 不再询问确认
        extra_patches=[
            mock.patch("scripts.cloud_probe.build_envelope_from_discovery",
                       side_effect=_build),
            mock.patch("scripts.cloud_probe.submit_envelope", side_effect=_submit),
        ],
    )
    assert rc == 0
    assert weak.review_decision == "agent_reject", "n 应标记 agent_reject"
    assert submitted == [], f"agent_reject 候选不得提交, got {submitted}"
    assert "没有符合条件的" in out, "应提示无候选可提交"


def test_review_agent_reject_excluded_from_mixed_batch():
    """强弱混合：n 只拒绝弱候选，强候选照常提交。"""
    strong = _profitable("p1", "Товар один")
    weak = _profitable("p2", "Товар два", conf=0.2, badge_eff=0.0)
    submitted: list[str] = []

    def _build(c, store_config, store_id=""):
        return {"draft": {"title": c.ozon_title, "item_id": c.ozon_product_id}}

    def _submit(envelope):
        submitted.append(envelope["draft"]["item_id"])
        return {"ok": True, "task_id": f"T-{envelope['draft']['item_id']}"}

    args = _discover_args(auto_submit=True, review=True)
    rc, out = _run_discover(
        args, [strong, weak], [strong, weak],
        input_side_effect=("n", "y"),  # 弱候选拒绝 → 确认 y
        extra_patches=[
            mock.patch("scripts.cloud_probe.build_envelope_from_discovery",
                       side_effect=_build),
            mock.patch("scripts.cloud_probe.submit_envelope", side_effect=_submit),
        ],
    )
    assert rc == 0
    assert weak.review_decision == "agent_reject"
    assert submitted == ["p1"], f"只有强候选应提交, got {submitted}"


# ── discover 默认（无 --review）零回归 ────────────────────────────────────

def test_default_no_review_never_calls_input():
    """无 --review 且不 auto_submit → 全程零 input() 调用（零回归）。"""
    c1 = _profitable("p1", "Товар один")

    def _no_input(*a, **k):
        raise AssertionError("默认模式不应调用 input()（评审未启用）")

    args = _discover_args(auto_submit=False, review=False)
    rc, out = _run_discover(args, [c1], [c1], input_side_effect=_no_input)
    assert rc == 0
    assert "人工评审" not in out, "默认模式不打印评审界面"


def test_default_no_review_auto_submit_only_confirm_gate():
    """无 --review 但 auto_submit → input 仅出现在确认门（一次），提交照常。"""
    c1 = _profitable("p1", "Товар один")
    submitted: list[str] = []
    calls: list[str] = []

    def _fake_input(prompt=""):
        calls.append(prompt)
        return "y"

    def _build(c, store_config, store_id=""):
        return {"draft": {"title": c.ozon_title, "item_id": c.ozon_product_id}}

    def _submit(envelope):
        submitted.append(envelope["draft"]["item_id"])
        return {"ok": True, "task_id": "T-p1"}

    args = _discover_args(auto_submit=True, review=False)
    rc, out = _run_discover(
        args, [c1], [c1], input_side_effect=_fake_input,
        extra_patches=[
            mock.patch("scripts.cloud_probe.build_envelope_from_discovery",
                       side_effect=_build),
            mock.patch("scripts.cloud_probe.submit_envelope", side_effect=_submit),
        ],
    )
    assert rc == 0
    assert submitted == ["p1"], "默认 auto-submit 行为不变"
    assert len(calls) == 1, f"默认模式应只有确认门一次 input, got {len(calls)} 次: {calls}"
    assert "确认提交" in calls[0], f"唯一 input 应为确认门, got {calls[0]!r}"


# ── follow_sell_cloud review 暂停 ─────────────────────────────────────────

def _follow_ctx(best=None, cdp_results=None):
    """构造 follow_sell_cloud 的完整 mock 上下文（对齐 test_follow_cache）。"""
    from scripts import cloud_probe as cp

    url = "https://www.ozon.ru/product/avtopoilka-4767514314/"
    cdp_data = {
        "success": True, "images": ["http://img/ozon/1.jpg"], "title": "Автопоилка",
        "price": "1290", "attributes": {}, "characteristics": [], "aspects": [],
    }
    if best is None:
        best = {"id": "980815374096", "badge_score": 3, "title": "宠物饮水器",
                "price": "5.5", "image": "http://img/1688/1.jpg",
                "badge": "全部符合", "confidence": 1.0, "badge_eff": 1.0,
                "score": 100.0, "reject_reason": ""}
    if cdp_results is None:
        cdp_results = [{"id": "980815374096", "title": "宠物饮水器", "price": "5.5",
                        "image": "http://img/1688/1.jpg", "badge": "全部符合"}]
    envelope = {
        "token": "sk", "ozon_client_id": "1", "ozon_api_key": "k",
        "envelope": {"draft": {"item_id": "980815374096"}, "extensions": {}},
    }
    patches = [
        mock.patch("scripts.lib.cache.cache_get", return_value=None),
        mock.patch("scripts.lib.cache.cache_set"),
        mock.patch("scripts.lib.config_store._require_auth"),
        mock.patch.object(cp, "_get_ozon_credentials",
                          return_value={"client_id": "1", "api_key": "k"}),
        mock.patch("scripts.lib.config_store.get_mxou_token", return_value="sk"),
        mock.patch.object(cp, "_cached_ozon_scrape", return_value=cdp_data),
        mock.patch("scripts.lib.chrome_launcher.ensure_chrome_cdp",
                   return_value=(True, "ok")),
        mock.patch("scripts.cli._chrome_profile_dir", return_value="/tmp/profile"),
        mock.patch("scripts.lib.cdp_client.CdpConnection"),
        mock.patch("scripts.lib.ozon_seller_analytics.fetch_sales_analytics",
                   return_value={}),
        mock.patch("scripts.lib.ozon_image_search.search_by_image_cdp",
                   return_value=cdp_results),
        mock.patch("scripts.lib.ozon_discovery._pick_best_match", return_value=best),
        mock.patch.object(cp, "build_graph_envelope_with_retry", return_value=envelope),
        mock.patch("scripts.lib.config_store.get_store_profile", return_value={}),
        mock.patch("scripts.lib.review_log.write_review_record"),
    ]
    return cp, url, patches


def test_follow_review_false_no_prompt():
    """review=False → 评审不启用：input 抛异常也不影响（零 input 调用）。"""
    from scripts import cloud_probe as cp
    cp, url, patches = _follow_ctx()

    def _no_input(*a, **k):
        raise AssertionError("review=False 不应调用 input()")

    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        stack.enter_context(mock.patch("builtins.input", side_effect=_no_input))
        r = cp.follow_sell_cloud(url, auto_submit=False, store_id="s1")
    assert r.get("success") is True, f"review=False 应正常组装信封: {r.get('error')}"
    assert r.get("best_match"), r


def test_follow_review_true_accept_best_submits():
    """review=True + 接受最佳（回车）→ best_match 保留，正常组装/提交。"""
    from scripts import cloud_probe as cp
    cp, url, patches = _follow_ctx()
    submitted = []

    def _submit(envelope):
        submitted.append(envelope)
        return {"ok": True, "task_id": "T1"}

    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        stack.enter_context(mock.patch("builtins.input", return_value=""))
        stack.enter_context(mock.patch.object(cp, "submit_envelope", side_effect=_submit))
        r = cp.follow_sell_cloud(url, auto_submit=True, store_id="s1", review=True)

    assert r.get("success") is True, f"接受最佳应正常提交: {r.get('error')}"
    assert r.get("task_id") == "T1"
    assert r.get("best_match"), "best_match 应保留"


def test_follow_review_true_reject_all_blocks_submit():
    """review=True + n（拒绝全部）→ no_relevant_match、不组装信封、记录 agent_reject。"""
    from scripts import cloud_probe as cp
    cp, url, patches = _follow_ctx()
    calls: list[dict] = []

    def _log(record):
        calls.append(record)

    def _build(*a, **k):
        raise AssertionError("拒绝全部后不应组装信封")

    def _submit(*a, **k):
        raise AssertionError("拒绝全部后不应提交")

    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        stack.enter_context(mock.patch("builtins.input", return_value="n"))
        stack.enter_context(mock.patch("scripts.lib.review_log.write_review_record",
                                       side_effect=_log))
        stack.enter_context(mock.patch.object(cp, "build_graph_envelope_with_retry",
                                              side_effect=_build))
        stack.enter_context(mock.patch.object(cp, "submit_envelope",
                                              side_effect=_submit))
        r = cp.follow_sell_cloud(url, auto_submit=True, store_id="s1", review=True)

    assert r.get("no_relevant_match") is True, "n 应标记 no_relevant_match"
    assert r.get("envelope_built") is False
    assert not r.get("best_match"), "拒绝后不应保留 best_match"
    assert not r.get("success"), "拒绝后不应成功"
    assert any(rec["decision"] == "agent_reject" for rec in calls), \
        f"应写 agent_reject 记录, got {calls}"


def test_cmd_follow_passes_review_flag():
    """cmd_follow 把 --review 透传给 follow_sell_cloud。"""
    from scripts import cli
    args = argparse.Namespace(ozon_url="https://www.ozon.ru/product/x-1/",
                              auto_submit=False, store="", review=True)
    with mock.patch("scripts.lib.config_store.preflight_check", return_value=[]), \
         mock.patch("scripts.lib.chrome_launcher.ensure_chrome_cdp",
                    return_value=(True, "ok")), \
         mock.patch("scripts.cli._chrome_profile_dir", return_value="/tmp/p"), \
         mock.patch("scripts.cloud_probe.follow_sell_cloud",
                    return_value={"success": True}) as m_fsc, \
         mock.patch("sys.stdout", new_callable=io.StringIO):
        rc = cli.cmd_follow(args)
    assert rc == 0
    assert m_fsc.call_args.kwargs.get("review") is True, m_fsc.call_args


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
