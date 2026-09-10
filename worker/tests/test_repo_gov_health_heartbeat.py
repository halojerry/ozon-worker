"""BL-08 (repo-gov): /health 备份心跳透出回归（test_repo_gov_health_heartbeat）。

覆盖 /health（main.health_check）新增两字段：
- last_backup_at: float|None —— lazy import services.backup_heartbeat_service，
  服务缺失 / 返回 None / 抛异常时均为 None，绝不影响 200 语义；
- backup_stale: bool —— 只对「有过心跳但距今 >26h」置 True；从未备份（恒 None）
  不置 stale（交给运维判断，不制造狼来了）。

DB 层用 fake engine（本文件不需要真实 PG）。
"""
import asyncio
import sys
import time
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

_HB_MODULE = "services.backup_heartbeat_service"


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeConn:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, stmt, params=None):
        return _FakeResult([])


class _FakeEngine:
    def connect(self):
        return _FakeConn()


@pytest.fixture
def fake_db(monkeypatch):
    """health_check 内部 `from storage.database.db import get_engine` 走本 fake。"""
    import storage.database.db as db_mod
    monkeypatch.setattr(db_mod, "get_engine", lambda: _FakeEngine())


def _install_hb(monkeypatch, value):
    """注入 fake backup_heartbeat_service；value=None 表示 last_backup_at 返回 None。"""
    mod = types.ModuleType(_HB_MODULE)
    mod.last_backup_at = lambda: value
    monkeypatch.setitem(sys.modules, _HB_MODULE, mod)


def _call_health():
    import main

    return asyncio.run(main.health_check())


def test_fresh_heartbeat_not_stale(fake_db, monkeypatch):
    """有新鲜心跳（1h 前）→ 字段透出原值，backup_stale=False。"""
    ts = time.time() - 3600
    _install_hb(monkeypatch, ts)
    body = _call_health()
    assert body["status"] == "ok"
    assert body["db"] == "connected"
    assert body["last_backup_at"] == pytest.approx(ts)
    assert body["backup_stale"] is False


def test_stale_heartbeat_over_26h(fake_db, monkeypatch):
    """有过心跳但距今 >26h → backup_stale=True。"""
    ts = time.time() - 27 * 3600
    _install_hb(monkeypatch, ts)
    body = _call_health()
    assert body["last_backup_at"] == pytest.approx(ts)
    assert body["backup_stale"] is True


def test_heartbeat_26h_boundary_not_stale(fake_db, monkeypatch):
    """边界：恰好 26h（不大于）→ 不置 stale。"""
    ts = time.time() - 26 * 3600 + 30
    _install_hb(monkeypatch, ts)
    body = _call_health()
    assert body["backup_stale"] is False


def test_never_backed_up_is_none_not_stale(fake_db, monkeypatch):
    """表存在但从未备份（finished 恒 None）→ last_backup_at=None 且不误报 stale。"""
    _install_hb(monkeypatch, None)
    body = _call_health()
    assert body["status"] == "ok"
    assert body["last_backup_at"] is None
    assert body["backup_stale"] is False


def test_service_missing_still_ok(fake_db, monkeypatch):
    """心跳服务未部署（import 失败，sys.modules 置 None 强制 ImportError）
    → 字段为 None，端点仍 200 ok（compose healthcheck 语义不被新字段破坏）。"""
    monkeypatch.setitem(sys.modules, _HB_MODULE, None)
    body = _call_health()
    assert body["status"] == "ok"
    assert body["last_backup_at"] is None
    assert body["backup_stale"] is False


def test_service_raises_swallowed(fake_db, monkeypatch):
    """last_backup_at 抛异常 → 吞掉，字段 None，端点 ok。"""
    mod = types.ModuleType(_HB_MODULE)

    def _boom():
        raise RuntimeError("pg gone")

    mod.last_backup_at = _boom
    monkeypatch.setitem(sys.modules, _HB_MODULE, mod)
    body = _call_health()
    assert body["status"] == "ok"
    assert body["last_backup_at"] is None
    assert body["backup_stale"] is False


def test_http_layer_200_with_json_fields(fake_db, monkeypatch):
    """经 TestClient 走真实 HTTP 层：200 + JSON 可序列化（float/None/bool）。"""
    from fastapi.testclient import TestClient
    import main

    ts = time.time() - 30 * 3600
    _install_hb(monkeypatch, ts)
    client = TestClient(main.app)  # 不带 with → 不触发 lifespan（不连 PG / 不启动 worker）
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["last_backup_at"] == pytest.approx(ts)
    assert data["backup_stale"] is True
