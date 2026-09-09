# worker/tests/test_ozon_session_client_headers.py
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from utils import ozon_session_client as osc  # noqa: E402


def test_headers_carry_app_name():
    captured = {}

    class R:
        status_code = 200
        url = "https://seller.ozon.ru/api/site/seller-analytics/what_to_sell/data/v3"
        def json(self):
            return {"result": {"items": []}}

    def fake_post(self, url, json=None, headers=None, timeout=None, allow_redirects=None):
        captured.update(headers or {})
        return R()

    with mock.patch.object(osc.requests.Session, "post", new=fake_post):
        osc.what_to_sell("a=1", "5381204", osc.build_what_to_sell_payload("123"))
    assert captured["x-o3-app-name"] == "seller-ui"
    assert captured["x-o3-company-id"] == "5381204"
    assert captured["x-o3-language"] == "zh-Hans"
