"""PRD M5b(P2): 采集箱 CSV/JSON 批量导入测试(真实 PG)。

覆盖:JSON rows 导入(逐行建草稿)、失败行不阻断、text/csv 原始 CSV 导入、
缺失 title 的行报 error、导入草稿可被 list_drafts 看到。
"""
import os
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

DB_URL = os.environ.get(
    "PGDATABASE_URL",
    "postgresql://postgres:ozon123@localhost:5433/ozon",
)
os.environ["CREDENTIAL_MASTER_KEY"] = "0123456789abcdef0123456789abcdef"
os.environ["SKIP_ZOMBIE_RECOVERY"] = "1"
os.environ["SKIP_FAILED_REVIVE"] = "1"

import main as main_mod  # noqa: E402

TENANT = main_mod._key_user_id("tokCsv")


class FakeTokensTable:
    def __init__(self):
        self._rows = [{"user_id": "tenant-csv", "key": "tokCsv", "status": 1, "deleted_at": None}]
        self._eqs = []

    def select(self, *cols):
        return self

    def eq(self, col, val):
        self._eqs.append((col, val))
        return self

    def is_(self, col, val):
        self._eqs.append((col, val))
        return self

    def limit(self, n):
        return self

    def execute(self):
        eqs, self._eqs = self._eqs, []
        filtered = self._rows
        for col, val in eqs:
            filtered = [r for r in filtered if str(r.get(col)) == str(val)]
        return SimpleNamespace(data=filtered[:1])


class FakeSupabase:
    def table(self, name):
        if name == "tokens":
            return FakeTokensTable()
        raise AssertionError(f"unexpected table {name}")


@pytest.fixture(scope="module")
def client():
    eng = create_engine(DB_URL)
    try:
        with eng.connect():
            pass
    except Exception:
        pytest.skip("本地 PG 不可达")
    with eng.begin() as conn:
        conn.execute(text(
            "DELETE FROM product_drafts WHERE tenant_id=:t AND source='csv'"
        ), {"t": TENANT})
    with TestClient(main_mod.app) as c:
        yield c
    with eng.begin() as conn:
        conn.execute(text(
            "DELETE FROM product_drafts WHERE tenant_id=:t AND source='csv'"
        ), {"t": TENANT})


def test_import_json_rows(client):
    resp = client.post("/api/v1/drafts/import", json={
        "token": "tokCsv",
        "rows": [
            {
                "title": "CSV商品A",
                "item_id": "csv-a",
                "images": "https://img1.jpg|https://img2.jpg",
                "purchase_cost": 10.5,
                "purchase_url": "https://detail.1688.com/offer/111.html",
                "price": 499,
                "stock": 100,
                "weight": 300,
            },
            {
                "title": "CSV商品B",
                "item_id": "csv-b",
                "images": ["https://img3.jpg"],
                "purchase_cost": 3.2,
            },
            {
                "item_id": "csv-c",
                "purchase_cost": 1.0,
            },
        ],
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["created"] == 2
    assert data["failed"] == 1
    assert any("标题" in e["error"] for e in data["errors"])

    lst = client.get("/api/v1/drafts", headers={"Authorization": "Bearer tokCsv"})
    titles = [d["payload"]["draft"]["title"] for d in lst.json()]
    assert "CSV商品A" in titles and "CSV商品B" in titles


def test_import_raw_csv(client):
    csv_text = (
        "title,item_id,images,purchase_cost,purchase_url,price,stock,supplier,weight\n"
        "CSV原文商品,raw-1,https://img4.jpg,5.5,https://detail.1688.com/offer/222.html,299,50,测试供应商,400\n"
        "CSV原文商品2,raw-2,https://img5.jpg|https://img6.jpg,8.8,,399,,,500\n"
    )
    resp = client.post(
        "/api/v1/drafts/import",
        content=csv_text.encode("utf-8"),
        headers={"Content-Type": "text/csv; charset=utf-8",
                 "Authorization": "Bearer tokCsv"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["created"] == 2
    assert data["failed"] == 0
    lst = client.get("/api/v1/drafts", headers={"Authorization": "Bearer tokCsv"})
    rows = [d["payload"]["draft"] for d in lst.json()]
    raw1 = next(r for r in rows if r.get("item_id") == "raw-1")
    assert raw1["title"] == "CSV原文商品"
    assert raw1["images"] == ["https://img4.jpg"]
    assert raw1["weight"] == 400
    raw2 = next(r for r in rows if r.get("item_id") == "raw-2")
    assert raw2["images"] == ["https://img5.jpg", "https://img6.jpg"]


def test_import_empty_rejected(client):
    resp = client.post("/api/v1/drafts/import", json={
        "token": "tokCsv",
        "rows": [],
    })
    assert resp.status_code == 400


def test_export_csv(client):
    """采集箱导出 CSV:租户隔离、UTF-8 BOM、含导入行字段。"""
    resp = client.get(
        "/api/v1/drafts/export",
        headers={"Authorization": "Bearer tokCsv"},
    )
    assert resp.status_code == 200
    assert "text/csv" in resp.headers["content-type"]
    assert "attachment" in resp.headers["content-disposition"]
    text_body = resp.text
    assert text_body.startswith("\ufeff")
    assert "CSV商品A" in text_body
    assert "https://img1.jpg|https://img2.jpg" in text_body
    assert "title,item_id,images" in text_body
