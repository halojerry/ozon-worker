"""D13: discover --auto-submit --to-box 入采集箱 — _submit_one 分支单测（纯 mock）。

- to_box=True  → 走 submit_draft（webui 采集箱 product_drafts source=skill），不调 submit_envelope
- to_box=False → 维持 submit_envelope 直接上架，不调 submit_draft
- 采集箱路径 stdout 展示 draft_id；直接上架路径展示 task_id

运行:
    cd skill && .venv314/bin/python -m pytest tests/test_discover_to_box.py -q
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


def _profitable(pid="p1", title="Автопоилка для кошек", url="https://detail.1688.com/offer/1001.html"):
    c = ProductCandidate(ozon_product_id=pid, ozon_title=title, ozon_price=1500.0)
    c.status = "profitable"
    c.match_1688_url = url
    c.has_analytics = False
    c.competing_sellers = 7
    c.rating = 4.5
    return c


def _discover_args(**overrides):
    defaults = dict(
        url="", keyword="поилка", max_products=50, min_margin=15.0, fx_rate=0.075,
        store="", no_analytics=False, min_price=0, max_price=0, brand_filter="nobrand",
        rules="monthly_sales>=1", export="", output="", auto_submit=True,
        to_box=False, notify=False, review=False,
        fission=False, max_depth=2, allow_depth_3=False, max_total_products=300,
        time_budget=600.0, max_sellers_per_product=20, max_products_per_seller=15,
        non_interactive=False, blue_ocean_source="", blue_ocean_csv="",
        china=False, local=False,
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
        mock.patch("builtins.input", return_value=confirm),
    ] + list(extra_patches)
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            rc = cli.cmd_discover(args)
    return rc, out.getvalue()


def _build_envelope(c, store_config, store_id=""):
    return {"draft": {"title": c.ozon_title, "item_id": c.ozon_product_id}}


def test_discover_to_box_uses_submit_draft_not_submit_envelope():
    """Given --to-box，When auto-submit，Then 走 submit_draft 入采集箱，submit_envelope 不被调用。"""
    c1 = _profitable("p1", "Товар один")
    submitted_draft_ids: list[str] = []

    def _submit_draft(envelope):
        submitted_draft_ids.append(envelope["draft"]["item_id"])
        return {"ok": True, "draft_id": f"D-{envelope['draft']['item_id']}"}

    args = _discover_args(auto_submit=True, to_box=True)
    m_env = mock.Mock(return_value={"ok": True, "task_id": "T-p1"})
    rc, out = _run_discover(
        args, [c1], [c1],
        extra_patches=[
            mock.patch("scripts.cloud_probe.build_envelope_from_discovery",
                       side_effect=_build_envelope),
            mock.patch("scripts.cloud_probe.submit_draft", side_effect=_submit_draft),
            mock.patch("scripts.cloud_probe.submit_envelope", m_env),
        ],
    )
    assert rc == 0
    assert submitted_draft_ids == ["p1"], f"to_box 应走 submit_draft, got {submitted_draft_ids}"
    m_env.assert_not_called()
    assert "📥 已入采集箱: Товар один → draft_id=D-p1" in out
    assert "task_id=" not in out, "to_box 不应打印 task_id"


def test_discover_without_to_box_uses_submit_envelope():
    """Given 无 --to-box，When auto-submit，Then 维持 submit_envelope 直接上架，submit_draft 不被调用。"""
    c1 = _profitable("p1", "Товар один")
    submitted_tasks: list[str] = []

    def _submit_env(envelope):
        submitted_tasks.append(envelope["draft"]["item_id"])
        return {"ok": True, "task_id": f"T-{envelope['draft']['item_id']}"}

    args = _discover_args(auto_submit=True, to_box=False)
    m_draft = mock.Mock(return_value={"ok": True, "draft_id": "D-p1"})
    rc, out = _run_discover(
        args, [c1], [c1],
        extra_patches=[
            mock.patch("scripts.cloud_probe.build_envelope_from_discovery",
                       side_effect=_build_envelope),
            mock.patch("scripts.cloud_probe.submit_draft", m_draft),
            mock.patch("scripts.cloud_probe.submit_envelope", side_effect=_submit_env),
        ],
    )
    assert rc == 0
    assert submitted_tasks == ["p1"], f"无 to_box 应走 submit_envelope, got {submitted_tasks}"
    m_draft.assert_not_called()
    assert "✓ 已提交: Товар один → task_id=T-p1" in out
    assert "draft_id=" not in out, "非 to_box 不应打印 draft_id"


def test_discover_to_box_parallel_all_in_box():
    """2 候选 to_box：全部入采集箱，stdout 展示各自 draft_id。"""
    c1 = _profitable("p1", "Товар один")
    c2 = _profitable("p2", "Товар два")
    submitted: list[str] = []

    def _submit_draft(envelope):
        submitted.append(envelope["draft"]["item_id"])
        return {"ok": True, "draft_id": f"D-{envelope['draft']['item_id']}"}

    args = _discover_args(auto_submit=True, to_box=True)
    m_env = mock.Mock(return_value={"ok": True, "task_id": "T-x"})
    rc, out = _run_discover(
        args, [c1, c2], [c1, c2],
        extra_patches=[
            mock.patch("scripts.cloud_probe.build_envelope_from_discovery",
                       side_effect=_build_envelope),
            mock.patch("scripts.cloud_probe.submit_draft", side_effect=_submit_draft),
            mock.patch("scripts.cloud_probe.submit_envelope", m_env),
        ],
    )
    assert rc == 0
    assert sorted(submitted) == ["p1", "p2"]
    m_env.assert_not_called()
    assert "→ draft_id=D-p1" in out and "→ draft_id=D-p2" in out
