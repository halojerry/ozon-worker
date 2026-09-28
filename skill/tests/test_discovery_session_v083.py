"""v0.83 批⑤ discover Session canonical 落盘（skill 侧）。

覆盖（方案 docs/PLAN-v083-quality-campaign-v1.md §3⑤ 验收闸）：
1. run_id 形态 ``disc_<yymmdd_hhmmss>_<6hex>`` + 校验（拒裸 id / 路径注入）。
2. 候选分组结构 ⇄ 扁平 dict roundtrip 不丢字段；raw 证据 cap（图 ≤10/卖家 ≤20/文本 ≤500）。
3. 自包含 session 文档落盘 sessions/{run_id}.json + index.jsonl 一行一 run（同 run 幂等）。
4. 尾 JSON ``_emit_discover_session_out``（run_id/candidates_count/summary/session_path）。
5. ``--sync-sessions`` 幂等（.reported sidecar；无 token 不炸）。
6. 旧 ``load_latest_discovery`` 兼容读 discovery_*.json（session 缺席时）。
7. gitleaks 红线：session_json 无 cookie/token/api_key 敏感键。
8. canonical 上报载荷带 session_run_id/schema_version/session_json + 投影 candidates。
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.lib import discovery_session as ds  # noqa: E402
from scripts.lib import ozon_discovery as od  # noqa: E402
from scripts.lib.ozon_discovery import ProductCandidate  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_session(tmp_path):
    """每用例独立 sessions 目录 + 清空进程内 session 上下文。"""
    sess_dir = tmp_path / "sessions"
    with mock.patch.object(ds, "SESSIONS_DIR", sess_dir), \
         mock.patch.object(ds, "INDEX_PATH", sess_dir / "index.jsonl"), \
         mock.patch.object(ds, "_INDEX_LOCK", sess_dir / ".index.lock"):
        ds.end_session()
        yield sess_dir
        ds.end_session()


def _mk(pid="p1", status="profitable", **kw):
    c = ProductCandidate(ozon_product_id=pid, ozon_title=f"Title {pid}",
                         ozon_price=1500.0, **kw)
    c.status = status
    return c


# ── 1. run_id ────────────────────────────────────────────────────────

def test_run_id_format_and_validation():
    rid = ds.new_run_id()
    assert re.fullmatch(r"disc_\d{6}_\d{6}_[0-9a-f]{6}", rid), rid
    assert ds.is_valid_run_id(rid)
    # 裸 id / 路径注入 / 短随机段全拒
    for bad in ("", "disc_123", "disc_260928_101530", "../evil",
                "disc_260928_101530_zzzzzz", "disc_260928_101530_a1b2c3d"):
        assert not ds.is_valid_run_id(bad), bad


# ── 2. 分组 ⇄ 扁平 roundtrip + cap ───────────────────────────────────

def test_group_flatten_roundtrip_no_field_loss():
    c = _mk("p9")
    c.match_1688_url = "https://detail.1688.com/offer/1.html"
    c.ozon_url = "https://www.ozon.ru/product/p9"
    c.min_competing_price = 1800.0
    c.monthly_sales = 120
    c.session_count = 0
    c.profit_margin = 22.5
    c.discovery_meta = {"source_comparison": {"x": 1}}
    c.session_run_id = "disc_260928_101530_a1b2c3"
    from dataclasses import asdict
    flat = asdict(c)
    grouped = ds.group_candidate(flat)
    back = ds.flatten_candidate(grouped)
    # 关键字段逐字回读
    assert back["ozon_product_id"] == "p9"
    assert back["match_1688_url"] == c.match_1688_url
    assert back["min_competing_price"] == 1800.0
    assert back["monthly_sales"] == 120
    assert back["session_count"] == 0          # 真实 0 不丢
    assert back["profit_margin"] == 22.5
    assert back["discovery_meta"] == {"source_comparison": {"x": 1}}
    assert back["session_run_id"] == "disc_260928_101530_a1b2c3"
    assert back["status"] == "profitable"


def test_raw_evidence_caps():
    c = _mk("p1")
    c.ozon_images = [f"https://img.ozon.ru/{i}.jpg" for i in range(25)]
    c.match_1688_images = [f"https://img.1688.com/{i}.jpg" for i in range(25)]
    c.competing_seller_list = [{"s": i} for i in range(50)]
    c.source_chain = [{"name": "x" * 800} for i in range(30)]
    c.page_category_path = "Очень/Длинный/Путь/" + ("ш" * 800)
    from dataclasses import asdict
    g = ds.group_candidate(asdict(c))
    prov = g["provenance"]
    assert len(prov["ozon_images"]) == 10
    assert len(prov["match_1688_images"]) == 10
    assert len(prov["competing_seller_list"]) == 20
    assert len(prov["source_chain"]) == 20
    assert len(prov["page_truth"]["category_path"]) == 500  # 文本截 500


# ── 3. 落盘 + index ─────────────────────────────────────────────────

def test_save_session_and_index_single_line(tmp_path):
    rid = ds.begin_session(kind="discover", keyword="поилка",
                           params={"min_margin": 15}, env={"fx_rate": 0.075})
    c = _mk("p1")
    c.session_run_id = rid
    from dataclasses import asdict
    doc = ds.build_session_document([asdict(c)])
    p1 = ds.save_session(doc)
    assert p1 is not None and p1.name == f"{rid}.json"
    # 同 run 再落（预匹配/后匹配）→ index 仍只有一行
    p2 = ds.save_session(doc)
    assert p2 == p1
    lines = [ln for ln in ds.INDEX_PATH.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["session_run_id"] == rid
    assert row["summary"]["profitable"] == 1
    # 自包含文档可独立读回
    loaded = ds.load_session(rid)
    assert loaded["schema_version"] == ds.SCHEMA_VERSION
    assert loaded["session_run_id"] == rid
    assert loaded["entry"]["keyword"] == "поилка"
    assert loaded["env"]["fx_rate"] == 0.075


def test_invalid_run_id_not_saved(tmp_path):
    assert ds.save_session({"session_run_id": "bogus", "candidates": []}) is None


# ── 4. 尾 JSON ──────────────────────────────────────────────────────

def test_emit_discover_session_out(capsys):
    from scripts import cli
    rid = ds.begin_session(kind="discover", keyword="kw")
    ds.set_session_path("/tmp/whatever.json")
    cli._emit_discover_session_out([_mk("p1"), _mk("p2", status="matched")])
    payload = json.loads(capsys.readouterr().out)
    assert payload["run_id"] == rid
    assert payload["candidates_count"] == 2
    assert payload["summary"]["total"] == 2
    assert payload["session_path"] == "/tmp/whatever.json"


def test_emit_noop_without_session(capsys):
    from scripts import cli
    ds.end_session()
    cli._emit_discover_session_out([_mk("p1")])
    assert capsys.readouterr().out == ""


# ── 5. sync-sessions ────────────────────────────────────────────────

def test_sync_sessions_idempotent(monkeypatch):
    rid = ds.begin_session(kind="discover", keyword="kw")
    c = _mk("p1")
    c.session_run_id = rid
    from dataclasses import asdict
    doc = ds.build_session_document([asdict(c)])
    ds.save_session(doc)
    assert ds.pending_sessions() == [rid]

    posted = []

    def _fake_post(session_doc, token, report_fields, **kw):
        posted.append(session_doc["session_run_id"])
        ds.mark_reported(session_doc["session_run_id"])
        return True

    monkeypatch.setattr(ds, "post_session_report", _fake_post)
    res = ds.sync_sessions(od.REPORT_FIELDS, lambda: "sk-x", limit=10)
    assert res["synced"] == 1 and res["failed"] == 0
    assert posted == [rid]
    assert ds.pending_sessions() == []          # .reported sidecar 落盘
    # 二次跑：无待传 → 不重复 POST（幂等安全）
    res2 = ds.sync_sessions(od.REPORT_FIELDS, lambda: "sk-x", limit=10)
    assert res2["scanned"] == 0 and res2["synced"] == 0


def test_sync_sessions_no_token(monkeypatch):
    rid = ds.begin_session(kind="discover", keyword="kw")
    c = _mk("p1")
    c.session_run_id = rid
    from dataclasses import asdict
    ds.save_session(ds.build_session_document([asdict(c)]))
    monkeypatch.setattr(ds, "post_session_report",
                        lambda *a, **k: pytest.fail("无 token 不应 POST"))
    res = ds.sync_sessions(od.REPORT_FIELDS, lambda: "", limit=10)
    assert res["reason"] == "no_token" and res["synced"] == 0


# ── 6. 旧缓存兼容 ───────────────────────────────────────────────────

def test_load_latest_discovery_reads_session_then_legacy(tmp_path, monkeypatch):
    # session 路径：canonical session 落盘后 load_latest_discovery 还原扁平候选
    rid = ds.begin_session(kind="discover", keyword="kw")
    c = _mk("p1")
    c.match_1688_url = "https://detail.1688.com/offer/1.html"
    c.session_run_id = rid
    from dataclasses import asdict
    ds.save_session(ds.build_session_document([asdict(c)]))
    cache_dir = tmp_path / "discovery"
    cache_dir.mkdir()
    monkeypatch.setattr(od, "DISCOVERY_CACHE_DIR", cache_dir)
    rows = od.load_latest_discovery()
    assert len(rows) == 1 and rows[0]["ozon_product_id"] == "p1"
    assert rows[0]["match_1688_url"].endswith("/1.html")


def test_load_latest_discovery_legacy_fallback(tmp_path, monkeypatch):
    # 无 session → 旧 discovery_*.json 兼容读
    cache_dir = tmp_path / "discovery"
    cache_dir.mkdir()
    (cache_dir / "discovery_20260101_000000.json").write_text(
        json.dumps([{"ozon_product_id": "legacy1", "status": "profitable"}]),
        encoding="utf-8")
    monkeypatch.setattr(od, "DISCOVERY_CACHE_DIR", cache_dir)
    with mock.patch.object(ds, "SESSIONS_DIR", tmp_path / "nonexistent"):
        rows = od.load_latest_discovery()
    assert rows and rows[0]["ozon_product_id"] == "legacy1"


# ── 7. 敏感键红线 ───────────────────────────────────────────────────

def test_session_json_has_no_secret_keys():
    rid = ds.begin_session(kind="discover", keyword="kw", env={"fx_rate": 0.075})
    c = _mk("p1")
    c.session_run_id = rid
    from dataclasses import asdict
    doc = ds.build_session_document([asdict(c)])
    blob = json.dumps(doc, ensure_ascii=False).lower()
    for needle in ("cookie", "authorization", "bearer", "api_key", "apikey",
                   "password", "secret", "token"):
        assert needle not in blob, f"session 文档不得含敏感键: {needle}"


# ── 8. canonical 上报载荷 ───────────────────────────────────────────

def test_build_report_payload_canonical():
    rid = ds.begin_session(kind="discover", keyword="поилка",
                           params={"min_margin": 15})
    c = _mk("p1")
    c.session_run_id = rid
    c.match_category_divergent = False
    from dataclasses import asdict
    doc = ds.build_session_document([asdict(c)])
    payload = ds.build_report_payload(doc, "sk-x", od.REPORT_FIELDS)
    assert payload["token"] == "sk-x"
    assert payload["session_run_id"] == rid
    assert payload["schema_version"] == ds.SCHEMA_VERSION
    assert payload["session_json"]["session_run_id"] == rid
    assert payload["keyword"] == "поилка"
    # 投影 candidates 向后兼容（白名单 + 派生 ozon_image）
    assert payload["candidates"][0]["ozon_product_id"] == "p1"
    assert len(payload["candidates"]) == 1


def test_report_discovery_run_includes_session(monkeypatch):
    """_report_discovery_run 带 session → payload 含 canonical 三键（成功打 .reported）。"""
    rid = ds.begin_session(kind="discover", keyword="kw")
    c = _mk("p1")
    c.session_run_id = rid
    from dataclasses import asdict
    doc = ds.build_session_document([asdict(c)])
    captured = {}

    class _Resp:
        status_code = 200

    def _post(url, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return _Resp()

    with mock.patch("scripts.lib.config_store.get_mxou_token", return_value="sk-test"), \
         mock.patch("requests.post", _post):
        od._report_discovery_run("kw", {"min_margin": 15}, [c], session=doc)
    assert captured["json"]["session_run_id"] == rid
    assert captured["json"]["session_json"]["session_run_id"] == rid
    assert ds.is_reported(rid)


def test_report_discovery_run_without_session_legacy(monkeypatch):
    """不带 session（旧调用）→ payload 无 canonical 键（向后兼容旧 worker）。"""
    c = _mk("p1")
    captured = {}

    class _Resp:
        status_code = 200

    def _post(url, json=None, timeout=None):
        captured["json"] = json
        return _Resp()

    with mock.patch("scripts.lib.config_store.get_mxou_token", return_value="sk-test"), \
         mock.patch("requests.post", _post):
        od._report_discovery_run("kw", {}, [c])
    assert "session_run_id" not in captured["json"]
    assert "session_json" not in captured["json"]


# ── 9. canonical 落盘走 session（_save_discovery_log 分流）────────────
def test_save_discovery_log_canonical_when_session(tmp_path, monkeypatch):
    rid = ds.begin_session(kind="discover", keyword="kw")
    monkeypatch.setattr(od, "_spawn_discovery_report", lambda *a, **k: None)
    p = od._save_discovery_log([_mk("p1")], keyword="kw")
    assert p is not None and p.name == f"{rid}.json"
    doc = json.loads(p.read_text(encoding="utf-8"))
    assert doc["session_run_id"] == rid
    assert doc["candidates"][0]["id"]["ozon_product_id"] == "p1"


def test_save_discovery_log_legacy_without_session(tmp_path, monkeypatch):
    cache_dir = tmp_path / "discovery"
    monkeypatch.setattr(od, "DISCOVERY_CACHE_DIR", cache_dir)
    monkeypatch.setattr(od, "_spawn_discovery_report", lambda *a, **k: None)
    ds.end_session()
    p = od._save_discovery_log([_mk("p1")])
    assert p is not None and p.name.startswith("discovery_")
    assert isinstance(json.loads(p.read_text(encoding="utf-8")), list)


# ── 10. sync-sessions CLI 出口（NEXT 纪律 + 出口码）──────────────────

def test_cmd_sync_sessions_cli_ok(monkeypatch, capsys):
    import argparse
    from scripts import cli

    monkeypatch.setattr(ds, "sync_sessions",
                        lambda *a, **k: {"scanned": 0, "synced": 0, "failed": 0,
                                         "reason": ""})
    monkeypatch.setattr(ds, "pending_sessions", lambda: [])
    assert cli.cmd_sync_sessions(argparse.Namespace(limit=5)) == 0
    out = capsys.readouterr().out
    assert "👉 NEXT:" in out
    assert '"pending_after": 0' in out


def test_cmd_sync_sessions_cli_no_token(monkeypatch, capsys):
    import argparse
    from scripts import cli

    monkeypatch.setattr(ds, "sync_sessions",
                        lambda *a, **k: {"scanned": 1, "synced": 0, "failed": 0,
                                         "reason": "no_token"})
    monkeypatch.setattr(ds, "pending_sessions", lambda: ["disc_260928_101530_a1b2c3"])
    assert cli.cmd_sync_sessions(argparse.Namespace(limit=5)) == 1
    out = capsys.readouterr().out
    assert "👉 NEXT:" in out and "set_token" in out
