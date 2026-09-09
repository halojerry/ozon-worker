"""v0.71 类目属性交互端懒加载测试（「选类目必出属性表单」红线修订）。

v0.70 只读缓存 → 7992 类目对 vs attribute_cache 12 行，表单 99.8% 显示未预热。
修订后：缓存优先 → 未命中用租户凭证按需拉一次 Ozon → 回写 30d → 失败降级
found=False+reason；?attr_id= 单属性字典值按需（翻页≤3 页）。
mock 层：get_category_query / credential_service / ozon_post 全 mock，鉴权链
真实 fail-open（本地无 Supabase）；PG 仅用于应用启动建表，不可达则 skip。
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

HDR = {"Authorization": "Bearer tokCatLazy071"}


class _FakeQuery:
    def __init__(self, schema=None, dict_values=None):
        self._schema = schema
        self._dict_values = dict_values

    def search_nodes(self, query_text, top_k=15, node_type=None, language="ZH_HANS"):
        return []

    def get_attribute_schema(self, dc, tp, language="ZH_HANS"):
        return self._schema

    def get_dictionary_values(self, attr_id, dc, tp, language="ZH_HANS"):
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


def test_lazy_fetch_on_cache_miss(client):
    """缓存未命中 + 租户有凭证 → 按需拉 Ozon + 回写 + found=True/fetched=True。"""
    fq = _FakeQuery(schema=None)
    ozon_raw = {"result": [
        {"id": 8229, "name": "Тип", "dictionary_id": 30, "is_required": True,
         "is_collection": True, "max_value_count": 1, "type": "Dictionary"},
        {"id": 4180, "name": "Цвет", "dictionary_id": 0, "is_required": False,
         "is_collection": False, "max_value_count": 0, "type": "String"},
    ]}
    with mock.patch.object(ocq_mod, "get_category_query", return_value=fq), \
         mock.patch("services.credential_service.get_default_credential",
                    return_value={"id": "c1", "ozon_client_id": "cid", "api_key": "k"}), \
         mock.patch("utils.ozon_client.ozon_post", return_value=ozon_raw) as op, \
         mock.patch("utils.local_db_manager.LocalDBManager.set_attribute_cache") as wb:
        r = client.get("/api/v1/categories/attributes?dc=1&tp=2", headers=HDR)
    assert r.status_code == 200
    body = r.json()
    assert body["found"] is True and body["cached"] is False and body["fetched"] is True
    attrs = body["attributes"]
    # ⚠️ v0.71 修复：必填读原始 is_required（此前读 required 恒 False）
    assert attrs[0]["required"] is True
    assert attrs[0]["is_collection"] is True and attrs[0]["max_value_count"] == 1
    assert attrs[1]["required"] is False
    assert op.call_count == 1
    assert wb.call_count == 1  # 回写 30d 缓存


def test_lazy_fetch_degrades_without_credential(client):
    """无凭证 → found=False + reason=no_credential，绝不触发 Ozon 调用。"""
    fq = _FakeQuery(schema=None)
    with mock.patch.object(ocq_mod, "get_category_query", return_value=fq), \
         mock.patch("services.credential_service.get_default_credential", return_value=None), \
         mock.patch("services.credential_service.list_credentials", return_value=[]), \
         mock.patch("utils.ozon_client.ozon_post") as op:
        r = client.get("/api/v1/categories/attributes?dc=1&tp=2", headers=HDR)
    assert r.status_code == 200
    body = r.json()
    assert body["found"] is False and body["reason"] == "no_credential"
    assert op.call_count == 0


def test_lazy_fetch_fetch_failure_degrades(client):
    """Ozon 拉取异常 → found=False + reason=fetch_failed（不 5xx 不破坏页面）。"""
    fq = _FakeQuery(schema=None)
    with mock.patch.object(ocq_mod, "get_category_query", return_value=fq), \
         mock.patch("services.credential_service.get_default_credential",
                    return_value={"id": "c1", "ozon_client_id": "cid", "api_key": "k"}), \
         mock.patch("utils.ozon_client.ozon_post", side_effect=RuntimeError("boom")):
        r = client.get("/api/v1/categories/attributes?dc=1&tp=2", headers=HDR)
    assert r.status_code == 200
    body = r.json()
    assert body["found"] is False
    assert body["reason"].startswith("fetch_failed")


def test_attr_id_values_lazy_fetch_with_pagination(client):
    """?attr_id= 字典值按需：翻页（has_next→last_value_id）聚合后回写。"""
    fq = _FakeQuery(schema=None)

    def _ozon_post(client_id, api_key, endpoint, body, timeout=30):
        assert endpoint == "/v1/description-category/attribute/values"
        if "last_value_id" not in body:
            return {"result": [{"id": 1, "value": "a"}, {"id": 2, "value": "b"}],
                    "has_next": True}
        assert body["last_value_id"] == 2
        return {"result": [{"id": 3, "value": "c"}], "has_next": False}

    with mock.patch.object(ocq_mod, "get_category_query", return_value=fq), \
         mock.patch("services.credential_service.get_default_credential",
                    return_value={"id": "c1", "ozon_client_id": "cid", "api_key": "k"}), \
         mock.patch("utils.ozon_client.ozon_post", side_effect=_ozon_post) as op, \
         mock.patch("utils.local_db_manager.LocalDBManager.set_dictionary_value_cache") as wb:
        r = client.get("/api/v1/categories/attributes?dc=1&tp=2&attr_id=8229", headers=HDR)
    assert r.status_code == 200
    body = r.json()
    assert body["found"] is True and body["fetched"] is True
    assert [v["id"] for v in body["values"]] == [1, 2, 3]
    assert op.call_count == 2  # 两页
    assert wb.call_count == 1


def test_attr_id_values_cache_hit_no_ozon(client):
    """字典值缓存命中 → 直接返回，零 Ozon 调用。"""
    fq = _FakeQuery(dict_values=[{"id": 9, "value": "Красный"}])
    with mock.patch.object(ocq_mod, "get_category_query", return_value=fq), \
         mock.patch("services.credential_service.get_default_credential",
                    return_value={"id": "c1", "ozon_client_id": "cid", "api_key": "k"}), \
         mock.patch("utils.ozon_client.ozon_post") as op:
        r = client.get("/api/v1/categories/attributes?dc=1&tp=2&attr_id=4180", headers=HDR)
    assert r.status_code == 200
    body = r.json()
    assert body["cached"] is True and body["fetched"] is False
    assert body["values"] == [{"id": 9, "value": "Красный"}]
    assert op.call_count == 0
