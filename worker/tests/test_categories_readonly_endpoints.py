"""v0.70 采集箱手工改配 — 类目搜索/属性缓存只读端点测试。

GET /api/v1/categories/search（树搜索，node_type=type）与
GET /api/v1/categories/attributes（缓存只读不回源）。
query 层 mock（get_category_query），鉴权链走真实 fail-open（本地无 Supabase）；
PG 仅用于应用启动建表，不可达则 skip。
"""
import os
import sys
from pathlib import Path
from unittest import mock

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

os.environ["CREDENTIAL_MASTER_KEY"] = "0123456789abcdef0123456789abcdef"
os.environ["SKIP_ZOMBIE_RECOVERY"] = "1"
os.environ["SKIP_FAILED_REVIVE"] = "1"
os.environ["SKIP_STORE_SYNC"] = "1"

import main as main_mod  # noqa: E402
import utils.ozon_category_query as ocq_mod  # noqa: E402

HDR = {"Authorization": "Bearer tokCatReadonly"}


class _FakeQuery:
    def __init__(self, schema=None, dict_values=None):
        self._schema = schema
        self._dict_values = dict_values
        self.search_calls: list[tuple] = []
        self.dict_calls: list[tuple] = []

    def search_nodes(self, query_text, top_k=15, node_type=None, language="ZH_HANS"):
        self.search_calls.append((query_text, top_k, node_type, language))
        return [{
            "description_category_id": 98765432, "type_id": 12345678,
            "node_name": "收纳盒", "full_path": "Дом и сад > Хранение > 收纳盒",
            "similarity": 0.83,
        }]

    def get_attribute_schema(self, dc, tp, language="ZH_HANS"):
        return self._schema

    def get_dictionary_values(self, attr_id, dc, tp, language="ZH_HANS"):
        self.dict_calls.append((attr_id, dc, tp))
        return self._dict_values


@pytest.fixture(scope="module")
def client():
    try:
        import sqlalchemy
        url = os.environ.get("PGDATABASE_URL",
                             "postgresql://postgres:localdev123@localhost:5433/ozon")
        eng = sqlalchemy.create_engine(url)
        with eng.connect():
            pass
    except Exception:
        pytest.skip("本地 PG 不可达")
    with TestClient(main_mod.app) as c:
        yield c


def test_search_requires_bearer(client):
    assert client.get("/api/v1/categories/search?q=帽").status_code == 401


def test_search_empty_q_returns_empty(client):
    r = client.get("/api/v1/categories/search?q=", headers=HDR)
    assert r.status_code == 200
    assert r.json() == {"items": []}


def test_search_returns_items_and_forces_type_zh(client):
    fq = _FakeQuery()
    with mock.patch.object(ocq_mod, "get_category_query", return_value=fq):
        r = client.get("/api/v1/categories/search?q=收纳", headers=HDR)
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1
    it = items[0]
    assert it["description_category_id"] == "98765432"
    assert it["type_id"] == "12345678"
    assert it["node_name"] == "收纳盒"
    assert it["similarity"] == pytest.approx(0.83)
    assert fq.search_calls and fq.search_calls[0][2] == "type"  # node_type 强制 type
    assert fq.search_calls[0][3] == "ZH_HANS"


def test_attributes_requires_numeric_dc_tp(client):
    r = client.get("/api/v1/categories/attributes?dc=abc&tp=1", headers=HDR)
    assert r.status_code == 422


def test_attributes_cache_miss_no_credential_degrades(client):
    """缓存未命中且租户无凭证 → found=False + reason（v0.71 语义：
    有凭证才按需拉取，无凭证降级纯缓存，绝不让页面 5xx）。"""
    fq = _FakeQuery(schema=None)
    with mock.patch.object(ocq_mod, "get_category_query", return_value=fq):
        r = client.get("/api/v1/categories/attributes?dc=1&tp=2", headers=HDR)
    assert r.status_code == 200
    body = r.json()
    assert body["found"] is False
    assert body["attributes"] == []
    assert body["reason"] == "no_credential"


def test_attributes_found_with_dict_values(client):
    schema = {"result": [
        {"id": 4180, "name": "Тип", "type": "String", "required": True,
         "dictionary_id": 0},
        {"id": 8229, "name": "类型", "type": "Dictionary", "required": True,
         "dictionary_id": 30},
        {"id": 0, "name": "坏数据", "dictionary_id": 5},  # attr_id=0 不查字典
    ]}
    fq = _FakeQuery(schema=schema, dict_values=[
        {"id": 9710, "value": "收纳盒"}, {"id": 9711, "value": "整理箱"}])
    with mock.patch.object(ocq_mod, "get_category_query", return_value=fq):
        r = client.get("/api/v1/categories/attributes?dc=98765432&tp=12345678",
                       headers=HDR)
    assert r.status_code == 200
    body = r.json()
    assert body["found"] is True and body["cached"] is True
    attrs = body["attributes"]
    assert len(attrs) == 3
    assert "values" not in attrs[0], "非字典属性不带 values 键"
    assert attrs[1]["values"] == [{"id": 9710, "value": "收纳盒"},
                                  {"id": 9711, "value": "整理箱"}]
    # 字典查询只对有效 attr_id 发起
    assert fq.dict_calls == [(8229, 98765432, 12345678)]


def test_attributes_list_shape_schema(client):
    """schema 为裸 list（新缓存格式）同样解析。"""
    fq = _FakeQuery(schema=[{"description_attribute_id": 4180, "name": "Тип",
                             "dictionary_id": 0}])
    with mock.patch.object(ocq_mod, "get_category_query", return_value=fq):
        r = client.get("/api/v1/categories/attributes?dc=1&tp=1", headers=HDR)
    body = r.json()
    assert body["found"] is True
    assert body["attributes"][0]["id"] == 4180  # description_attribute_id 兜底
