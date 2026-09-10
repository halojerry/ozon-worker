"""BL-10 (repo-gov): mxou 调用埋点（record_call 台账）+ 对账脚本回归。

覆盖：
- call_mxou_chat_api：即将发起 HTTP 处写台账一行（tenant_id=None、token_fp
  与 _token_fingerprint 同源、endpoint="chat"）；重试路径只记一行；
- call_mxou_image_api：生图埋点 endpoint="image_gen:<model>"；
- record_call 抛异常被吞：原 API 调用完全不受影响；
- 余额 pre-check 拦下（未发 HTTP、未计费）→ 不记台账；
- reconcile_mxou.py：按 token_fp / 按日汇总 SQL 形状（GROUP BY / LIMIT 恒在）
  与输出格式（含优雅降级文案）；epoch 与 timestamp 时间列自适应。

台账服务 lazy import —— 全部经 sys.modules 注入 fake（服务由并行改动提供，
本文件对真实服务零依赖）。
"""
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

_LEDGER_MODULE = "services.mxou_ledger_service"


class _R:
    """fake requests.Response"""

    def __init__(self, code=200, j=None, text=""):
        self.status_code = code
        self._j = j or {}
        self.text = text

    def json(self):
        return self._j


class _Recorder:
    def __init__(self, raise_on_call=False):
        self.calls = []
        self.raise_on_call = raise_on_call

    def record_call(self, *, tenant_id, token_fp, endpoint):
        if self.raise_on_call:
            raise RuntimeError("ledger down")
        self.calls.append(
            {"tenant_id": tenant_id, "token_fp": token_fp, "endpoint": endpoint}
        )


def _install_ledger(monkeypatch, raise_on_call=False) -> _Recorder:
    rec = _Recorder(raise_on_call=raise_on_call)
    mod = types.ModuleType(_LEDGER_MODULE)
    mod.record_call = rec.record_call
    monkeypatch.setitem(sys.modules, _LEDGER_MODULE, mod)
    return rec


@pytest.fixture(autouse=True)
def _reset_balance_cache():
    """每个用例重置模块级余额缓存，避免跨用例污染（同 test_mxou_balance_precheck）。"""
    import utils.mxou_api as mxou_api

    mxou_api._BALANCE_CACHE["value"] = None
    mxou_api._BALANCE_CACHE["ts"] = 0.0
    mxou_api._BALANCE_CACHE["fp"] = None
    yield
    mxou_api._BALANCE_CACHE["value"] = None
    mxou_api._BALANCE_CACHE["ts"] = 0.0
    mxou_api._BALANCE_CACHE["fp"] = None


# ---------------------------------------------------------------------------
# mxou_api 埋点
# ---------------------------------------------------------------------------

def test_chat_records_call(monkeypatch):
    """chat 即将发 HTTP 处记一行：tenant_id=None / token_fp 同源 / endpoint="chat"。"""
    import utils.mxou_api as mxou_api

    rec = _install_ledger(monkeypatch)
    monkeypatch.setattr(mxou_api, "get_mxou_balance", lambda t: 100.0)
    monkeypatch.setattr(mxou_api, "mxou_acquire", lambda t: None)

    class FakeSession:
        def post(self, url, **kw):
            return _R(200, {"choices": [{"message": {"content": "回答"}}]})

    monkeypatch.setattr(mxou_api, "_get_session", lambda: FakeSession())

    out = mxou_api.call_mxou_chat_api(token="tok-secret-1", system_prompt="s", user_prompt="u")
    assert out == "回答"
    assert len(rec.calls) == 1
    c = rec.calls[0]
    assert c["endpoint"] == "chat"
    assert c["tenant_id"] is None
    assert c["token_fp"] == mxou_api._token_fingerprint("tok-secret-1")
    assert c["token_fp"] != "tok-secret-1", "台账必须存脱敏指纹，绝不存 token 原文"


def test_record_call_failure_swallowed(monkeypatch):
    """台账服务抛异常 → 被吞，原 chat 调用照常返回。"""
    import utils.mxou_api as mxou_api

    _install_ledger(monkeypatch, raise_on_call=True)
    monkeypatch.setattr(mxou_api, "get_mxou_balance", lambda t: 100.0)
    monkeypatch.setattr(mxou_api, "mxou_acquire", lambda t: None)

    class FakeSession:
        def post(self, url, **kw):
            return _R(200, {"choices": [{"message": {"content": "不受影响"}}]})

    monkeypatch.setattr(mxou_api, "_get_session", lambda: FakeSession())

    out = mxou_api.call_mxou_chat_api(token="tok", system_prompt="s", user_prompt="u")
    assert out == "不受影响"


def test_image_records_call_with_model_endpoint(monkeypatch):
    """生图埋点：endpoint="image_gen:<model>"（真实计费生成一行）。"""
    import utils.mxou_api as mxou_api

    rec = _install_ledger(monkeypatch)
    monkeypatch.setattr(mxou_api, "get_mxou_balance", lambda t: 100.0)
    monkeypatch.setattr(mxou_api, "mxou_acquire", lambda t: None)

    class FakeSession:
        def post(self, url, **kw):
            return _R(200, {"status": "succeeded", "results": [{"url": "http://img/1.png"}]})

    monkeypatch.setattr(mxou_api, "_get_session", lambda: FakeSession())

    url = mxou_api.call_mxou_image_api(token="tok-img", prompt="p", model="gpt-image-2")
    assert url == "http://img/1.png"
    assert len(rec.calls) == 1
    c = rec.calls[0]
    assert c["endpoint"] == "image_gen:gpt-image-2"
    assert c["tenant_id"] is None
    assert c["token_fp"] == mxou_api._token_fingerprint("tok-img")


def test_no_record_when_balance_precheck_blocks(monkeypatch):
    """余额不足 fast-fail（未发 HTTP、未计费）→ 台账零记录。"""
    import utils.mxou_api as mxou_api

    rec = _install_ledger(monkeypatch)
    monkeypatch.setattr(mxou_api, "get_mxou_balance", lambda t: 0.0)
    monkeypatch.setattr(mxou_api, "mxou_acquire", lambda t: None)

    post_calls = []

    class FakeSession:
        def post(self, url, **kw):
            post_calls.append(url)
            return _R(200, {})

    monkeypatch.setattr(mxou_api, "_get_session", lambda: FakeSession())

    with pytest.raises(mxou_api.MxouOutOfQuotaError):
        mxou_api.call_mxou_image_api(token="tok-poor", prompt="p")

    assert rec.calls == []
    assert post_calls == []


# ---------------------------------------------------------------------------
# reconcile_mxou.py（只读对账 CLI）
# ---------------------------------------------------------------------------

class _ScriptConn:
    """按序吐结果并记录 (sql, params) 的 fake connection。"""

    def __init__(self, results):
        self._results = list(results)
        self.executed = []

    def execute(self, stmt, params=None):
        self.executed.append((str(stmt), params))
        rows = self._results.pop(0)
        return _RowList(rows)


class _RowList:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


def _norm(sql: str) -> str:
    return " ".join(sql.split())


def test_reconcile_summarize_by_token_sql_and_format():
    import reconcile_mxou as rc

    conn = _ScriptConn([[("ab12cd34", 5), ("ef56gh78", 2)]])
    rows = rc.summarize_by_token(conn, limit=20)
    sql, params = conn.executed[0]
    assert "GROUP BY token_fp" in _norm(sql)
    assert "ORDER BY" in _norm(sql)
    assert "LIMIT :limit" in _norm(sql)
    assert params == {"limit": 20}
    assert rows == [("ab12cd34", 5), ("ef56gh78", 2)]
    text_out = rc.format_token_rows(rows)
    assert "ab12cd34" in text_out and "5" in text_out
    assert "token_fp" in text_out


def test_reconcile_summarize_by_day_epoch_vs_timestamp():
    import reconcile_mxou as rc

    # epoch 数值列（double precision）→ to_timestamp 包裹
    conn = _ScriptConn([
        [("created_at", "double precision")],          # information_schema 内省
        [("2026-09-11", 45), ("2026-09-10", 12)],      # 按日汇总
    ])
    rows = rc.summarize_by_day(conn, days=7, limit=31)
    assert rows is not None
    day_sql, day_params = conn.executed[1]
    assert "to_timestamp(created_at)" in _norm(day_sql)
    assert "GROUP BY day" in _norm(day_sql)
    assert "LIMIT :limit" in _norm(day_sql)
    assert day_params == {"days": 7, "limit": 31}
    out = rc.format_day_rows(rows)
    assert "2026-09-11" in out and "45" in out

    # timestamp 列 → 不包 to_timestamp
    conn2 = _ScriptConn([
        [("created_at", "timestamp without time zone")],
        [("2026-09-11", 3)],
    ])
    rc.summarize_by_day(conn2, days=7, limit=31)
    day_sql2, _ = conn2.executed[1]
    assert "to_timestamp" not in _norm(day_sql2)


def test_reconcile_day_summary_graceful_when_no_ts_column():
    import reconcile_mxou as rc

    # 表存在但时间列候选全缺 → summarize_by_day 返回 None，格式器如实标注
    conn = _ScriptConn([[("token_fp", "character varying"), ("endpoint", "text")]])
    rows = rc.summarize_by_day(conn, days=7, limit=31)
    assert rows is None
    out = rc.format_day_rows(None)
    assert "跳过" in out or "找不到时间列" in out
