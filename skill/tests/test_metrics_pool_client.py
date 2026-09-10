# skill/tests/test_metrics_pool_client.py
"""数据池 skill 侧客户端：分批/失败静默/kill-switch。纯 mock requests。"""
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from scripts.lib import metrics_pool_client as mpc  # noqa: E402


def _cfg(monkeypatch):
    monkeypatch.setattr(mpc, "_worker_cfg", lambda: ("http://localhost:8080", "tok"))


def test_report_chunks_and_returns_accepted(monkeypatch):
    _cfg(monkeypatch)
    seen = []

    class R:
        status_code = 200
        def json(self):
            return {"accepted": 12, "skipped": 0}

    def fake_post(url, json=None, headers=None, timeout=None):
        seen.append(json["items"])
        return R()

    with mock.patch.object(mpc.requests, "post", side_effect=fake_post):
        n = mpc.report_seller_sync([{"sku": i} for i in range(25)])
    # adjudicated deviation：brief 测试字面 assert n == 12 与其参考实现（accepted +=）
    # 自相矛盾（3 批 × 恒 accepted 12 = 36）；reviewer 裁定累计语义 governs → 36。
    assert n == 36
    assert [len(c) for c in seen] == [12, 12, 1]


def test_report_never_raises(monkeypatch):
    _cfg(monkeypatch)
    with mock.patch.object(mpc.requests, "post", side_effect=OSError("down")):
        assert mpc.report_seller_sync([{"sku": 1}]) == 0


def test_kill_switch(monkeypatch):
    _cfg(monkeypatch)
    monkeypatch.setenv("METRICS_POOL_REPORT", "0")
    with mock.patch.object(mpc.requests, "post") as p:
        assert mpc.report_seller_sync([{"sku": 1}]) == 0
        p.assert_not_called()


def test_query_returns_map_or_none(monkeypatch):
    _cfg(monkeypatch)

    class R:
        status_code = 200
        def json(self):
            return {"metrics": [{"sku": 1, "sales_payload": {"monthsales": 9}}]}

    with mock.patch.object(mpc.requests, "get", return_value=R()):
        assert mpc.query_sku_metrics(["1"]) == {"1": {"sku": 1, "sales_payload": {"monthsales": 9}}}
    with mock.patch.object(mpc.requests, "get", side_effect=OSError("down")):
        assert mpc.query_sku_metrics(["1"]) is None
