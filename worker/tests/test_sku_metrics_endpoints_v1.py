"""seller-sync / sku-metrics 端点：纯 mock（monkeypatch 服务函数），无 PG 依赖。
运行：PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_sku_metrics_endpoints_v1.py -q
"""
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from fastapi.testclient import TestClient  # noqa: E402


def _client() -> TestClient:
    import main
    return TestClient(main.app)


def _no_db():
    """get_session 模块属性补丁：路由内建 session 不触真引擎（纯 mock，无 PG 依赖）。"""
    return mock.patch("routes.analytics_routes.get_session", return_value=mock.MagicMock())


def test_seller_sync_requires_bearer():
    resp = _client().post("/api/v1/analytics/seller-sync", json={"items": []})
    assert resp.status_code == 401


def test_seller_sync_accepts_batch():
    with mock.patch("routes.analytics_routes.upsert_seller_sync_items",
                    return_value={"accepted": 2, "skipped": 0}) as m, _no_db():
        resp = _client().post(
            "/api/v1/analytics/seller-sync",
            headers={"Authorization": "Bearer testtok"},
            json={"items": [{"sku": 1, "sales_payload": {}}], "source_company_id": "5381204"})
    assert resp.status_code == 200
    assert resp.json() == {"accepted": 2, "skipped": 0}
    assert m.call_args.kwargs["source_company_id"] == "5381204"


def test_seller_sync_invalid_items_422():
    with mock.patch("routes.analytics_routes.upsert_seller_sync_items",
                    side_effect=ValueError("invalid literal for int()")), _no_db():
        resp = _client().post(
            "/api/v1/analytics/seller-sync",
            headers={"Authorization": "Bearer testtok"},
            json={"items": [{"sku": 1, "category_dc": "not-a-number"}]})
    assert resp.status_code == 422


def test_sku_metrics_query():
    with mock.patch("routes.analytics_routes.query_sku_metrics",
                    return_value=[{"sku": 1, "needs_sales_sync": False}]), _no_db():
        resp = _client().get(
            "/api/v1/analytics/sku-metrics?skus=1,2",
            headers={"Authorization": "Bearer testtok"})
    assert resp.status_code == 200
    assert resp.json()["metrics"][0]["sku"] == 1
