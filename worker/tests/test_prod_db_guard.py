"""prod_db_guard 单测（v0.75 部署加固）：marker 三态语义。

不依赖真实 PG——sqlalchemy create_engine 打桩（连接链路全 mock）。
语义锁定（scripts/prod_db_guard.py 探测约定）：
- marker 表存在且有行 → is_production_db=True / enforce → SystemExit(2)
- 表不存在 / 表为空 / 连接失败 → False（放行，后续用例自然报错）
"""
from unittest import mock

import pytest

from scripts import prod_db_guard


def _make_engine(scalars):
    """构造 mock engine：conn.execute(...).scalar() 依次返回 scalars。"""
    conn = mock.MagicMock()
    results = [mock.MagicMock(scalar=mock.MagicMock(return_value=v)) for v in scalars]
    conn.execute.side_effect = results
    ctx = mock.MagicMock()
    ctx.__enter__.return_value = conn
    ctx.__exit__.return_value = False
    eng = mock.MagicMock()
    eng.connect.return_value = ctx
    return eng


def _patch_engine_factory(monkeypatch, scalars):
    """create_engine 打桩为工厂：每次调用（is_production_db 可能被多次调）
    返回全新 engine，避免 side_effect 序列被上一次调用耗尽。"""
    monkeypatch.setattr(
        "sqlalchemy.create_engine",
        lambda url, connect_args=None: _make_engine(scalars),
    )


def test_marker_present_blocks(monkeypatch, capsys):
    _patch_engine_factory(monkeypatch, [True, 1])  # to_regclass 有表 + COUNT=1
    assert prod_db_guard.is_production_db("postgresql://x") is True
    with pytest.raises(SystemExit) as ei:
        prod_db_guard.enforce_not_production("postgresql://x")
    assert ei.value.code == 2
    assert "prod_marker" in capsys.readouterr().err


def test_table_absent_passes(monkeypatch):
    _patch_engine_factory(monkeypatch, [None])  # to_regclass → None（无表）
    assert prod_db_guard.is_production_db("postgresql://x") is False


def test_marker_table_empty_passes(monkeypatch):
    _patch_engine_factory(monkeypatch, [True, 0])  # 有表但 0 行（哨兵被清）
    assert prod_db_guard.is_production_db("postgresql://x") is False


def test_connection_error_passes(monkeypatch):
    eng = mock.MagicMock()
    eng.connect.side_effect = RuntimeError("connection refused")
    monkeypatch.setattr(
        "sqlalchemy.create_engine", lambda url, connect_args=None: eng
    )
    assert prod_db_guard.is_production_db("postgresql://x") is False


def test_connect_timeout_used(monkeypatch):
    """连接超时 3s 必须下发（防不可达主机上闸探测挂死整个会话）。"""
    seen = {}

    def _fake_create_engine(url, connect_args=None):
        seen["connect_args"] = connect_args
        raise RuntimeError("boom")

    monkeypatch.setattr("sqlalchemy.create_engine", _fake_create_engine)
    prod_db_guard.is_production_db("postgresql://unreachable")
    assert seen["connect_args"] == {"connect_timeout": 3}
