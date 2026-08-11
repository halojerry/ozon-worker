"""P1-4(skill): query --watch 主动状态通知 + graph/follow/discover --notify（TDD RED→GREEN）。

- query --watch：首次 check_task_status 非终态 → 每 10s 轮询直到终态（mock 证明多次调用）
  + 中间态打印进度百分比（progress.percent）
- cmd_graph --notify → submit_envelope 收到的 GraphInput 顶层含 notify=True
- cmd_discover --notify（auto_submit ThreadPoolExecutor 路径）→ submit_envelope 信封含 notify=True
- cmd_follow --notify → follow_sell_cloud 收到 notify=True
- follow_sell_cloud(notify=True, auto_submit=True) → submit_envelope 收到的信封含 notify=True

运行：
    cd skill && .venv314/bin/python -m pytest tests/test_query_watch.py -q
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


# ── query --watch ──────────────────────────────────────────────────────────

def test_query_watch_polls_until_terminal():
    """--watch：running(35%) → completed；check_task_status 被轮询 ≥2 次，进度与终态都打印。"""
    from scripts import cli

    poll_count = {"n": 0}

    def _status(task_id):
        poll_count["n"] += 1
        if poll_count["n"] == 1:
            return {
                "task_id": task_id, "status": "running", "ok": False, "terminal": False,
                "progress": {"stage": "pricing", "percent": 35},
            }
        return {
            "task_id": task_id, "status": "completed", "ok": True, "terminal": True,
            "result_json": {"product_summary": [{"product_id": "123", "price": "999"}]},
        }

    args = argparse.Namespace(task_id="T1", watch=True, timeout=900)
    with mock.patch("scripts.cloud_probe.check_task_status", side_effect=_status) as m_status, \
         mock.patch("scripts.cloud_probe.time.sleep"), \
         mock.patch("sys.stdout", new_callable=io.StringIO) as out:
        rc = cli.cmd_query(args)
    assert rc == 0
    assert poll_count["n"] >= 2, f"非终态后应继续轮询, got {poll_count}"
    assert m_status.call_count >= 2
    assert "35%" in out.getvalue(), f"应打印进度百分比, got:\n{out.getvalue()}"
    assert "completed" in out.getvalue(), f"应打印最终终态, got:\n{out.getvalue()}"


def test_query_watch_single_call_when_terminal():
    """--watch：首次即终态 → 只轮询一次（不再空转）。"""
    from scripts import cli

    args = argparse.Namespace(task_id="T1", watch=True, timeout=900)
    with mock.patch("scripts.cloud_probe.check_task_status",
                    return_value={"task_id": "T1", "status": "failed",
                                  "ok": False, "terminal": True,
                                  "error_message": "boom"}) as m_status, \
         mock.patch("scripts.cloud_probe.time.sleep"), \
         mock.patch("sys.stdout", new_callable=io.StringIO) as out:
        rc = cli.cmd_query(args)
    assert rc == 0
    assert m_status.call_count == 1, "终态不应继续轮询"
    assert "failed" in out.getvalue()
    assert "boom" in out.getvalue()


# ── cmd_graph --notify ─────────────────────────────────────────────────────

def test_cmd_graph_notify_sets_graph_key():
    """cmd_graph --notify → submit_envelope 收到的 GraphInput 顶层含 notify=True。"""
    from scripts import cli

    graph = {
        "token": "sk", "ozon_client_id": "1", "ozon_api_key": "k",
        "envelope": {"draft": {"item_id": "1001", "title": "宠物饮水器"}},
    }
    submitted: list[dict] = []

    def _submit(g):
        submitted.append(g)
        return {"ok": True, "task_id": "T-1001"}

    args = argparse.Namespace(item_id="1001", url="", category_query="", retries=3,
                              store="", no_submit=False, ozon_ref_url="", notify=True)
    with mock.patch("scripts.lib.config_store.preflight_check", return_value=[]), \
         mock.patch("scripts.lib.chrome_launcher.ensure_chrome_cdp",
                    return_value=(True, "ok")), \
         mock.patch("scripts.cli._chrome_profile_dir", return_value="/tmp/p"), \
         mock.patch("scripts.cloud_probe.build_graph_envelope_with_retry",
                    return_value=graph), \
         mock.patch("scripts.cloud_probe.submit_envelope", side_effect=_submit) as m_sub, \
         mock.patch("sys.stdout", new_callable=io.StringIO):
        rc = cli.cmd_graph(args)
    assert rc == 0
    assert submitted and submitted[0].get("notify") is True, \
        f"提交载荷应含 notify=True, got {submitted}"
    assert m_sub.call_args.args[0].get("notify") is True, m_sub.call_args


# ── cmd_discover --notify（auto_submit 路径）───────────────────────────────

def _profitable(pid="p1", title="Автопоилка для кошек",
                url="https://detail.1688.com/offer/1001.html"):
    c = ProductCandidate(ozon_product_id=pid, ozon_title=title, ozon_price=1500.0)
    c.status = "profitable"
    c.match_1688_url = url
    c.match_1688_title = "宠物自动饮水器"
    c.match_confidence = 0.9
    c.match_badge_eff = 1.0
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
        china=False, local=False, review=False, notify=True,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _run_discover(args, candidates, selected, extra_patches=(), confirm="y"):
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
        mock.patch("builtins.input", return_value=confirm),
    ] + list(extra_patches)
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            rc = cli.cmd_discover(args)
    return rc, out.getvalue()


def test_cmd_discover_notify_sets_envelope_flag():
    """cmd_discover --notify + auto_submit → submit_envelope 信封含 notify=True。"""
    c1 = _profitable("p1", "Товар один")
    submitted: list[dict] = []

    def _build(c, store_config, store_id=""):
        return {"draft": {"title": c.ozon_title, "item_id": c.ozon_product_id}}

    def _submit(envelope):
        submitted.append(envelope)
        return {"ok": True, "task_id": "T-p1"}

    args = _discover_args(auto_submit=True, notify=True)
    rc, out = _run_discover(
        args, [c1], [c1],
        extra_patches=[
            mock.patch("scripts.cloud_probe.build_envelope_from_discovery",
                       side_effect=_build),
            mock.patch("scripts.cloud_probe.submit_envelope", side_effect=_submit),
        ],
    )
    assert rc == 0
    assert submitted and submitted[0].get("notify") is True, \
        f"提交信封应含 notify=True, got {submitted}"
    assert "→ task_id=T-p1" in out


# ── cmd_follow --notify + follow_sell_cloud 信封透传 ───────────────────────

def test_cmd_follow_passes_notify_flag():
    """cmd_follow --notify → follow_sell_cloud 收到 notify=True。"""
    from scripts import cli
    args = argparse.Namespace(ozon_url="https://www.ozon.ru/product/x-1/",
                              auto_submit=True, store="", review=False, notify=True)
    with mock.patch("scripts.lib.config_store.preflight_check", return_value=[]), \
         mock.patch("scripts.lib.chrome_launcher.ensure_chrome_cdp",
                    return_value=(True, "ok")), \
         mock.patch("scripts.cli._chrome_profile_dir", return_value="/tmp/p"), \
         mock.patch("scripts.cloud_probe.follow_sell_cloud",
                    return_value={"success": True}) as m_fsc, \
         mock.patch("sys.stdout", new_callable=io.StringIO):
        rc = cli.cmd_follow(args)
    assert rc == 0
    assert m_fsc.call_args.kwargs.get("notify") is True, m_fsc.call_args


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


def test_follow_cloud_notify_sets_envelope_key():
    """follow_sell_cloud(notify=True, auto_submit=True) → submit_envelope 信封含 notify=True。"""
    cp, url, patches = _follow_ctx()
    submitted: list[dict] = []

    def _submit(envelope):
        submitted.append(envelope)
        return {"ok": True, "task_id": "T1"}

    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        stack.enter_context(mock.patch.object(cp, "submit_envelope", side_effect=_submit))
        r = cp.follow_sell_cloud(url, auto_submit=True, store_id="s1", notify=True)

    assert r.get("success") is True, f"notify 不应影响提交成功: {r.get('error')}"
    assert submitted and submitted[0].get("notify") is True, \
        f"提交信封应含 notify=True, got {submitted}"


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
