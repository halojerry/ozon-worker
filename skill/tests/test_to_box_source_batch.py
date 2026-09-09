#!/usr/bin/env python3
"""to_box 批次契约回归（P3）：drafts 请求体顶层 source_batch（≤64 字符）。

契约（与 worker 侧钉死）：写入端新增**可选**字符串字段 source_batch（≤64 字符），
读取端 ?batch=<value> 查询。本文件只锁写入端：

① submit_draft(source_batch=...) → 只进请求体顶层，绝不进 envelope/extensions；
② 不传/空 → 请求体**不含**该字段（向后兼容老 Worker），且不改动调用方原 dict；
③ 超 64 字符截断到 64；
④ discover-task --to-box：source_batch = 本次运行 task_id（任务状态文件可对账）。

运行：
    cd skill && .venv314/bin/python -m pytest tests/test_to_box_source_batch.py -q
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import scripts.cli as cli  # noqa: E402
from scripts.lib.ozon_discovery import ProductCandidate  # noqa: E402


# ── submit_draft 单元（请求体形状）────────────────────────────────────────

def _http_spy(monkeypatch) -> dict:
    """拦截 requests.post，返回 {url, body} 捕获器；模拟 200 → {"id": "d-1"}。"""
    captured = {}

    def fake_post(url, json=None, **kw):
        captured.update(url=url, body=json)

        class R:
            status_code = 200

            def json(self):
                return {"id": "d-1"}

        return R()

    monkeypatch.setattr("requests.post", fake_post)
    # 最小适配：_require_auth 的缓存校验直接放行（否则 verify_with_worker 打真实网络）
    monkeypatch.setattr("scripts.lib.config_store.get_mxou_token", lambda: "sk-t")
    monkeypatch.setattr("scripts.lib.config_store.is_auth_valid", lambda: True)
    return captured


def _envelope() -> dict:
    return {"token": "sk-t", "ozon_client_id": "1", "ozon_api_key": "k",
            "envelope": {"draft": {}, "source": {}, "extensions": {}}}


def test_submit_draft_source_batch_in_body_top_level(monkeypatch):
    """P3: source_batch 只进请求体顶层，绝不进 envelope/extensions（对齐 A6 notes 红线）。"""
    from scripts import cloud_probe
    cap = _http_spy(monkeypatch)
    env = _envelope()
    resp = cloud_probe.submit_draft(env, source_batch="20260909_180107")
    assert resp["ok"] is True and resp["draft_id"] == "d-1"
    assert cap["body"]["source_batch"] == "20260909_180107"
    assert "source_batch" not in cap["body"]["envelope"]["extensions"]
    assert cap["url"].endswith("/api/v1/drafts")


def test_submit_draft_without_source_batch_omits_key(monkeypatch):
    """无 task_id 场景：不带该字段（老 Worker 向后兼容），原 dict 也不被污染。"""
    from scripts import cloud_probe
    cap = _http_spy(monkeypatch)
    env = _envelope()
    cloud_probe.submit_draft(env)
    assert "source_batch" not in cap["body"], "缺失场景必须整键缺席，不许空串占位"
    assert "source_batch" not in env, "调用方原 dict 不被写入"

    cap2 = _http_spy(monkeypatch)
    cloud_probe.submit_draft(_envelope(), source_batch="")
    assert "source_batch" not in cap2["body"], "空串同样视为缺失"


def test_submit_draft_source_batch_truncated_to_64(monkeypatch):
    """契约 ≤64 字符：超长截断（防御性，调用方正常传 ≤64 的 task_id）。"""
    from scripts import cloud_probe
    cap = _http_spy(monkeypatch)
    cloud_probe.submit_draft(_envelope(), source_batch="b" * 100)
    assert cap["body"]["source_batch"] == "b" * 64


def test_submit_draft_note_and_source_batch_coexist(monkeypatch):
    """--note 与 source_batch 同给：两键都在顶层，互不进信封。"""
    from scripts import cloud_probe
    cap = _http_spy(monkeypatch)
    cloud_probe.submit_draft(_envelope(), note="竞品月销高", source_batch="20260909_180107")
    assert cap["body"]["notes"] == "竞品月销高"
    assert cap["body"]["source_batch"] == "20260909_180107"
    assert "notes" not in cap["body"]["envelope"]["extensions"]
    assert "source_batch" not in cap["body"]["envelope"]["extensions"]


# ── discover-task --to-box 接线：source_batch = 本次运行 task_id ─────────

def _args(**kw) -> argparse.Namespace:
    base = dict(url="https://www.ozon.ru/highlight/tovary-iz-kitaya-935133/",
                keyword="", target_count=10, max_scan=300, filter_profile=None,
                base_filter="", min_margin=15.0, fx_rate=0.075, match_limit=None,
                match_concurrency=1, no_match_streak_stop=5, store="",
                to_box=False, auto_submit=False, dry_run=True, resume=False,
                no_analytics=False, export="")
    base.update(kw)
    return argparse.Namespace(**base)


def _cand(pid: str, status: str) -> ProductCandidate:
    c = ProductCandidate(ozon_product_id=pid, ozon_title=f"Товар {pid}",
                         ozon_price=1000.0, ozon_images=["http://img/x.jpg"])
    c.status = status
    c.profit_margin = 25.0
    c.match_1688_url = f"https://detail.1688.com/offer/{pid}.html" if status == "profitable" else ""
    return c


def test_discover_task_to_box_source_batch_is_run_task_id(tmp_path):
    """--to-box：每条 drafts 请求带 source_batch=运行 task_id（与任务状态文件一致）。"""
    from scripts.lib import chrome_launcher, config_store
    from scripts.lib import ozon_discovery as od
    captured: list = []

    def _fake_submit(envelope, source_batch=None):
        captured.append(source_batch)
        return {"draft_id": f"draft-{len(captured)}"}

    cands = [_cand("201", "profitable"), _cand("202", "profitable")]
    with mock.patch.object(od, "DISCOVERY_CACHE_DIR", tmp_path), \
         mock.patch.object(chrome_launcher, "ensure_chrome_cdp", return_value=(True, "ok")), \
         mock.patch.object(od, "collect_and_analyze", return_value=cands), \
         mock.patch.object(od, "match_selected"), \
         mock.patch.object(config_store, "get_store_profile", return_value={}), \
         mock.patch.object(config_store, "get_setting", return_value=None), \
         mock.patch.object(config_store, "get_store", return_value={"client_id": "1", "api_key": "k"}), \
         mock.patch("scripts.cloud_probe.submit_draft", side_effect=_fake_submit), \
         mock.patch("scripts.cloud_probe.build_envelope_from_discovery",
                    side_effect=lambda c, sc, store_id="": {"token": "t", "envelope": {}}):
        rc = cli.cmd_discover_task(_args(to_box=True, dry_run=False))
    assert rc == 0
    assert captured, "--to-box 应触发 submit_draft"
    state = json.loads(
        next((tmp_path / "tasks").glob("task_*.json")).read_text(encoding="utf-8"))
    assert captured == [state["task_id"]] * len(captured), "source_batch=本次运行 task_id"
    assert len(state["task_id"]) <= 64, "task_id 满足 ≤64 契约"


def test_discover_task_dry_run_does_not_submit(tmp_path):
    """干跑：不提交 → source_batch 无从产生（无 drafts 请求）。"""
    from scripts.lib import chrome_launcher, config_store
    from scripts.lib import ozon_discovery as od
    m = mock.Mock(return_value={"draft_id": "d-x"})
    cands = [_cand("301", "ok")]
    with mock.patch.object(od, "DISCOVERY_CACHE_DIR", tmp_path), \
         mock.patch.object(chrome_launcher, "ensure_chrome_cdp", return_value=(True, "ok")), \
         mock.patch.object(od, "collect_and_analyze", return_value=cands), \
         mock.patch.object(od, "match_selected"), \
         mock.patch.object(config_store, "get_store_profile", return_value={}), \
         mock.patch.object(config_store, "get_setting", return_value=None), \
         mock.patch.object(config_store, "get_store", return_value={"client_id": "1", "api_key": "k"}), \
         mock.patch("scripts.cloud_probe.submit_draft", m), \
         mock.patch("scripts.cloud_probe.build_envelope_from_discovery",
                    side_effect=lambda c, sc, store_id="": {"token": "t", "envelope": {}}):
        rc = cli.cmd_discover_task(_args(dry_run=True))
    assert rc == 0
    m.assert_not_called()

