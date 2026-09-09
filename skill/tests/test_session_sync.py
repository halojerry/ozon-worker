"""session-sync 子命令回归（v0.70 批次 C4）：CDP 收割 seller 会话 → 上传 worker 代管。

核心契约：
- 收割结果无 sc_company_id → fail-fast exit 2（核心 cookie 不上传）
- 收割成功 → POST {worker}/api/v1/credentials/{id}/session，body={"cookies": {...}}
- 成功输出只打 cookie 名单与 harvested_at（脱敏摘要，绝不含 cookie 值）
- --status 只查状态不上传

mock 层：_fetch_seller_session_cookies / config_store 鉴权 / requests，无网络无 Chrome。
"""
import argparse
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.cli import cmd_session_sync  # noqa: E402


def _ns(**kw) -> argparse.Namespace:
    base = dict(credential_id="00000000-0000-0000-0000-00000000c1",
                worker_url="", status=False, cdp_url="")
    base.update(kw)
    return argparse.Namespace(**base)


@pytest.fixture()
def auth_ok(monkeypatch):
    import scripts.lib.config_store as cs
    monkeypatch.setattr(cs, "_require_auth", lambda: None)
    monkeypatch.setattr(cs, "get_mxou_token", lambda: "tok-test")


def test_sync_requires_sc_company_id(monkeypatch, capsys, auth_ok):
    monkeypatch.setattr("scripts.lib.ozon_seller_analytics._fetch_seller_session_cookies",
                        lambda cdp_url="": {"Abt": "x"})  # 无 sc_company_id
    with pytest.raises(SystemExit) as e:
        cmd_session_sync(_ns(credential_id="c1"))
    assert e.value.code == 2  # fail-fast：无核心 cookie 不上传
    out = capsys.readouterr().out
    assert "sc_company_id" in out  # 人话提示：告诉用户缺什么


def test_sync_uploads_cookies(monkeypatch, capsys, auth_ok):
    monkeypatch.setattr("scripts.lib.ozon_seller_analytics._fetch_seller_session_cookies",
                        lambda cdp_url="": {"Abt": "SECRETVAL", "sc_company_id": "5371047"})
    calls = {}

    class _Resp:
        status_code = 201

        def json(self):
            return {"ok": True, "status": "active",
                    "cookie_names": ["Abt", "sc_company_id"]}

    def _fake_post(url, json=None, headers=None, timeout=None):
        calls["url"] = url
        calls["json"] = json
        calls["headers"] = headers
        return _Resp()

    import requests as _requests
    monkeypatch.setattr(_requests, "post", _fake_post)
    # 上传成功后的状态回查（GET /session → harvested_at）
    monkeypatch.setattr(_requests, "get", lambda url, **kw: SimpleNamespace(
        status_code=200,
        json=lambda: {"status": "active", "harvested_at": "2026-09-08T00:00:00+00:00",
                      "cookie_names": ["Abt", "sc_company_id"]}))

    rc = cmd_session_sync(_ns())
    out = capsys.readouterr().out

    assert rc == 0
    assert calls["url"].endswith(
        "/api/v1/credentials/00000000-0000-0000-0000-00000000c1/session")
    assert calls["url"].count("/session") == 1
    assert calls["json"] == {"cookies": {"Abt": "SECRETVAL", "sc_company_id": "5371047"}}
    assert calls["headers"]["Authorization"] == "Bearer tok-test"
    # 脱敏摘要：名单 + harvested_at 在，cookie 值绝不在
    assert "sc_company_id" in out and "Abt" in out
    assert "2026-09-08T00:00:00+00:00" in out
    assert "SECRETVAL" not in out


def test_sync_status_flag_queries_without_upload(monkeypatch, capsys, auth_ok):
    uploaded = {"post": False}

    def _no_post(*a, **kw):
        uploaded["post"] = True
        raise AssertionError("--status 不应上传")

    import requests as _requests
    monkeypatch.setattr(_requests, "post", _no_post)
    monkeypatch.setattr(_requests, "get", lambda url, **kw: SimpleNamespace(
        status_code=200,
        json=lambda: {"status": "active", "harvested_at": "T1",
                      "cookie_names": ["sc_company_id"]}))

    rc = cmd_session_sync(_ns(status=True))
    out = capsys.readouterr().out
    assert rc == 0
    assert uploaded["post"] is False
    assert "active" in out and "sc_company_id" in out


def test_sync_worker_error_returns_1(monkeypatch, capsys, auth_ok):
    monkeypatch.setattr("scripts.lib.ozon_seller_analytics._fetch_seller_session_cookies",
                        lambda cdp_url="": {"sc_company_id": "5371047"})
    import requests as _requests

    class _Resp:
        status_code = 404

        def json(self):
            return {"detail": "店铺凭证不存在或不属于当前用户"}

    monkeypatch.setattr(_requests, "post", lambda url, **kw: _Resp())
    rc = cmd_session_sync(_ns())
    out = capsys.readouterr().out
    assert rc == 1
    assert "店铺凭证不存在" in out
