# skill/tests/test_session_chips_harvest.py
"""CHIPS 分区 cookie 兜底：Network.getCookies 漏掉的分区 abt_data 由
Storage.getCookies 兜回（ozonAI cookieHandler 同款，同名取 value 更长者）。"""
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from scripts.lib import ozon_seller_analytics as osa  # noqa: E402


def test_partitioned_abt_data_merged(monkeypatch):
    network_cookies = {"result": {"cookies": [
        {"name": "sc_company_id", "value": "5381204"}]}}
    storage_cookies = {"result": {"cookies": [
        {"name": "sc_company_id", "value": "5381204"},
        {"name": "abt_data", "value": "short"},
        {"name": "abt_data", "value": "longer-partitioned-value"}]}}
    monkeypatch.setattr(osa, "_cdp_get_cookies_sequence",
                        lambda conn: [network_cookies, storage_cookies])
    out = osa._fetch_seller_session_cookies("http://127.0.0.1:9222")
    assert out["sc_company_id"] == "5381204"
    assert out["abt_data"] == "longer-partitioned-value"  # 同名取 value 更长者


def test_no_sc_company_id_still_fails_fast(monkeypatch):
    monkeypatch.setattr(osa, "_cdp_get_cookies_sequence",
                        lambda conn: [{"result": {"cookies": []}}, {"result": {"cookies": []}}])
    assert osa._fetch_seller_session_cookies("http://127.0.0.1:9222") == {}
