#!/usr/bin/env python3
"""批B6: 预估打印共享入口 _estimate_and_print + graph/follow --min-margin 拦截（纯 mock）。

- _estimate_and_print：与 graph 腿原内联公式同源（售价=总成本×(1+margin)/(1-commission)），
  graph/follow 两腿共用；免责一行（预估非终价，worker 实算为准）
- --min-margin（float 默认 0.0=不拦截零变化）：预估利润率 < 阈值 → print 拦截原因 + exit 3
  （对齐 --min-density 语义）；discover 腿不加（已有 profitable 筛选）
- follow 腿在 follow_sell_cloud 内部提交前拦截（提交后再打印就晚了）

运行：
    cd skill && .venv314/bin/python -m pytest tests/test_estimate_min_margin_v078.py -q
"""
from __future__ import annotations

import io
import os
import sys
from contextlib import ExitStack
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import scripts.cli as cli  # noqa: E402
from scripts import cloud_probe  # noqa: E402


def _quote(cost: float):
    return SimpleNamespace(cost=cost, fallback_chain="worker")


_DRAFT = {
    "item_id": "123", "title": "x",
    "purchase_cost": 10.0, "weight": 1000,
    "dimensions": {"length": 100, "width": 100, "height": 50},
}


def test_estimate_and_print_formula_and_lines():
    """①公式与 worker 同源 + 💰 行 + 免责行；返回四键。"""
    with mock.patch("scripts.lib.ozon_discovery._query_logistics_from_worker",
                    return_value=_quote(5.0)), \
            mock.patch("scripts.lib.config_store.get_ozon_credentials",
                       return_value={"margin_rate": 0.5, "commission_rate": 0.2}), \
            mock.patch("sys.stdout", io.StringIO()) as out:
        est = cli._estimate_and_print(dict(_DRAFT), store="")
    assert est is not None
    # total = 10 + 5 = 15；price = 15×1.5/0.8 = 28.12；profit = 13.12；rate = 46.7%
    assert est["estimated_retail_price_cny"] == 28.12
    assert est["estimated_logistics_cny"] == 5.0
    assert est["estimated_profit_cny"] == 13.12
    assert est["estimated_profit_rate"] == 46.7
    text = out.getvalue()
    assert "💰 预估:" in text
    assert "28.12" in text
    assert "预估非终价" in text and "Worker 实算为准" in text


def test_estimate_and_print_insufficient_data_returns_none():
    """②数据不足（无采购价）→ None、不打印、不查询物流。"""
    with mock.patch("scripts.lib.ozon_discovery._query_logistics_from_worker") as q, \
            mock.patch("sys.stdout", io.StringIO()) as out:
        est = cli._estimate_and_print({"weight": 0, "purchase_cost": 0}, store="")
    assert est is None
    assert "💰" not in out.getvalue()
    q.assert_not_called()


def test_estimate_and_print_logistics_fallback_flat():
    """③物流查询失败 → 1kg×15 元/kg 兜底（原 graph 腿同款兜底）。"""
    with mock.patch("scripts.lib.ozon_discovery._query_logistics_from_worker",
                    return_value=None), \
            mock.patch("scripts.lib.config_store.get_ozon_credentials",
                       return_value=None), \
            mock.patch("sys.stdout", io.StringIO()) as out:
        est = cli._estimate_and_print(dict(_DRAFT), store="")
    assert est is not None
    assert est["estimated_logistics_cny"] == 15.0  # 1000g → 1kg × 15
    assert "💰 预估:" in out.getvalue()


# ── graph 腿 --min-margin ───────────────────────────────────────────────────

_GRAPH_ENV = {
    "token": "t", "ozon_client_id": "c", "ozon_api_key": "k",
    "envelope": {"draft": dict(_DRAFT), "source": {}, "extensions": {}},
}


def _run_graph(extra_args: list):
    """跑 cmd_graph（预估走 mock 物流），返回 (rc, stdout, submit 调用数)。"""
    submitted = []

    def _fake_submit(graph):
        submitted.append(graph)
        return {"ok": True, "task_id": "T1"}

    with ExitStack() as stack:
        stack.enter_context(mock.patch(
            "scripts.lib.config_store.preflight_check", return_value=[]))
        stack.enter_context(mock.patch(
            "scripts.cloud_probe.build_graph_envelope_with_retry",
            return_value=_GRAPH_ENV))
        stack.enter_context(mock.patch(
            "scripts.cloud_probe._source_preflight", return_value=(True, "")))
        stack.enter_context(mock.patch(
            "scripts.cloud_probe._check_min_density", return_value=(True, "")))
        stack.enter_context(mock.patch(
            "scripts.cloud_probe.submit_envelope", side_effect=_fake_submit))
        stack.enter_context(mock.patch(
            "scripts.lib.ozon_discovery._query_logistics_from_worker",
            return_value=_quote(5.0)))
        stack.enter_context(mock.patch(
            "scripts.lib.config_store.get_ozon_credentials",
            return_value={"margin_rate": 0.5, "commission_rate": 0.2}))
        out = io.StringIO()
        stack.enter_context(mock.patch("sys.stdout", out))
        args = cli.build_arg_parser().parse_args(
            ["graph", "--item-id", "123", *extra_args])
        rc = cli.cmd_graph(args)
    return rc, out.getvalue(), len(submitted)


def test_graph_min_margin_zero_does_not_block():
    """④--min-margin 缺省 0.0：不拦截（现状零变化），提交照常。"""
    rc, out, n_submit = _run_graph([])
    assert rc == 0
    assert n_submit == 1
    assert "💰 预估:" in out, "graph 腿预估打印走共享入口"


def test_graph_min_margin_blocks_with_exit3():
    """⑤预估利润率 46.7% < --min-margin 50 → 打印拦截原因 + exit 3 + 零提交。"""
    rc, out, n_submit = _run_graph(["--min-margin", "50"])
    assert rc == 3
    assert n_submit == 0, "拦截后不提交"
    assert "预估利润率 46.7%" in out and "--min-margin 50" in out


def test_graph_min_margin_above_rate_passes():
    """⑥--min-margin 40 < 46.7% → 不拦截。"""
    rc, out, n_submit = _run_graph(["--min-margin", "40"])
    assert rc == 0
    assert n_submit == 1


# ── follow 腿 ───────────────────────────────────────────────────────────────

URL = "https://www.ozon.ru/product/avtopoilka-4767514314/"
CDP_DATA = {"success": True, "images": ["http://img/ozon/1.jpg"], "title": "Автопоилка"}
CDP_RESULTS = [{"id": "980815374096", "title": "宠物饮水器", "price": "5.5",
                "image": "http://img/1688/1.jpg", "badge": "符合 3/3 个条件"}]
BEST = {"id": "980815374096", "badge_score": 3, "title": "宠物饮水器"}
FOLLOW_ENV = {
    "token": "sk", "ozon_client_id": "1", "ozon_api_key": "k",
    "envelope": {"draft": dict(_DRAFT), "extensions": {}},
}


def _run_follow_cloud(min_margin: float, submit_res: dict | None = None):
    """跑真 follow_sell_cloud（全 mock）+ 物流 mock，返回 (result, submit 调用数)。"""
    submit_res = submit_res or {"ok": True, "task_id": "T-F1"}
    submits = []

    def _fake_submit(envelope):
        submits.append(envelope)
        return dict(submit_res)

    with mock.patch("scripts.lib.cache.cache_get", return_value=None), \
            mock.patch("scripts.lib.cache.cache_set"), \
            mock.patch("scripts.lib.config_store._require_auth"), \
            mock.patch.object(cloud_probe, "_get_ozon_credentials",
                              return_value={"client_id": "1", "api_key": "k"}), \
            mock.patch("scripts.lib.config_store.get_mxou_token", return_value="sk"), \
            mock.patch.object(cloud_probe, "_cached_ozon_scrape", return_value=CDP_DATA), \
            mock.patch("scripts.lib.chrome_launcher.ensure_chrome_cdp",
                       return_value=(True, "ok")), \
            mock.patch("scripts.cli._chrome_profile_dir", return_value="/tmp/profile"), \
            mock.patch("scripts.lib.cdp_client.CdpConnection"), \
            mock.patch("scripts.lib.ozon_seller_analytics.fetch_sales_analytics",
                       return_value={}), \
            mock.patch("scripts.lib.ozon_image_search.search_by_image_cdp",
                       return_value=CDP_RESULTS), \
            mock.patch("scripts.lib.ozon_discovery._pick_best_match", return_value=BEST), \
            mock.patch.object(cloud_probe, "build_graph_envelope_with_retry",
                              return_value=FOLLOW_ENV), \
            mock.patch.object(cloud_probe, "submit_envelope", side_effect=_fake_submit), \
            mock.patch("scripts.lib.config_store.get_store_profile", return_value={}), \
            mock.patch("scripts.lib.ozon_discovery._query_logistics_from_worker",
                       return_value=_quote(5.0)), \
            mock.patch("scripts.lib.config_store.get_ozon_credentials",
                       return_value={"margin_rate": 0.5, "commission_rate": 0.2}):
        r = cloud_probe.follow_sell_cloud(URL, auto_submit=True, store_id="s1",
                                          min_margin=min_margin)
    return r, len(submits)


def test_follow_submits_and_prints_estimate_when_min_margin_zero():
    """⑦follow --min-margin 缺省 0：提交前预估打印（B6 补齐 follow 无预估的缺口），照常提交。"""
    r, n = _run_follow_cloud(0.0)
    assert r.get("success") is True
    assert n == 1
    assert isinstance(r.get("estimate"), dict), "预估结果挂 result 供 _out 透出"


def test_follow_min_margin_blocks_before_submit():
    """⑧follow --min-margin 50：提交前拦截（不提交），blocked_reason=low_margin。"""
    r, n = _run_follow_cloud(50.0)
    assert n == 0, "low_margin 必须拦在提交前"
    assert r.get("blocked_reason") == "low_margin"
    assert r.get("success") is False
    assert r.get("submit_result") is None


def test_cmd_follow_passes_min_margin_and_returns_3_on_block():
    """⑨cmd_follow 透传 --min-margin；blocked_reason=low_margin → exit 3。"""
    captured = {}

    def _fake_follow(ozon_url, **kw):
        captured.update(kw)
        return {"success": False, "blocked_reason": "low_margin"}

    with mock.patch("scripts.lib.config_store.preflight_check", return_value=[]), \
            mock.patch("scripts.cloud_probe.follow_sell_cloud",
                       side_effect=_fake_follow), \
            mock.patch("sys.stdout", io.StringIO()):
        args = cli.build_arg_parser().parse_args(
            ["follow", "--ozon-url", URL, "--auto-submit", "--min-margin", "50"])
        rc = cli.cmd_follow(args)
    assert rc == 3
    assert captured.get("min_margin") == 50.0, "cmd_follow 必须把 --min-margin 传进管线"


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
    sys.exit(1 if failed else 0)
