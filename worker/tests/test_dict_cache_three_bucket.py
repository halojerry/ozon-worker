"""v0.72 字典值缓存三桶策略单测（撑爆 40G 盘事故根治）。

三桶（utils/dict_value_cache.py）：global=cat_dep_false（哨兵键 attr,0,0）/
scoped=cat_dep_true（attr,dc,tp 现状）/ ephemeral=首页即 has_next（>2000 值
不物化）。全部 mock 层单测，不触网；PG 用例需本地 5433。
"""
import os
import sys
from pathlib import Path
from unittest import mock

os.environ.setdefault("GRSAI_API_KEY", "test-key")
os.environ.setdefault("LOG_FORMAT", "text")
os.environ.setdefault("LOG_LEVEL", "WARNING")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from utils.dict_value_cache import (  # noqa: E402
    BUCKET_EPHEMERAL,
    BUCKET_GLOBAL,
    BUCKET_SCOPED,
    classify_bucket,
    is_category_dependent,
    routed_get,
    routed_set,
)


# ═══════════ 分类 ═══════════

def test_classify_by_category_dependent():
    assert classify_bucket({"category_dependent": False}) == BUCKET_GLOBAL
    assert classify_bucket({"category_dependent": True}) == BUCKET_SCOPED
    # 字段缺失/非 dict → 保守 scoped（旧缓存行/旧夹具行为不变）
    assert classify_bucket({}) == BUCKET_SCOPED
    assert classify_bucket(None) == BUCKET_SCOPED
    assert is_category_dependent("junk") is True


def test_classify_ephemeral_by_size():
    """首页即 has_next（truncated）或值数超上限 → ephemeral 不物化。"""
    assert classify_bucket({"category_dependent": False}, truncated=True) == BUCKET_EPHEMERAL
    assert classify_bucket({"category_dependent": True}, value_count=2001) == BUCKET_EPHEMERAL
    assert classify_bucket({"category_dependent": True}, value_count=2000) == BUCKET_SCOPED


# ═══════════ routed_set 落桶 ═══════════

def _patch_ldb():
    """patch LocalDBManager.set_dictionary_value_cache，记录调用参数。"""
    calls = {}

    def _rec(self, attribute_id, description_category_id, type_id, values_data,
             language="ZH_HANS", expires_in=30 * 86400):
        calls["args"] = (attribute_id, description_category_id, type_id, language, len(values_data))

    return mock.patch("utils.local_db_manager.LocalDBManager.set_dictionary_value_cache", _rec), calls


def test_routed_set_global_for_cat_dep_false():
    """cat_dep=false → 全局桶哨兵键 (attr,0,0)。"""
    p, calls = _patch_ldb()
    with p:
        bucket = routed_set(4389, [{"id": 1, "value": "Китай"}], 85282223, 970988646,
                            attr_row={"category_dependent": False})
    assert bucket == BUCKET_GLOBAL
    aid, dc, tp, lang, n = calls["args"]
    assert (dc, tp) == (0, 0)  # 哨兵键
    assert n == 1


def test_routed_set_scoped_default_and_explicit():
    p, calls = _patch_ldb()
    with p:
        bucket = routed_set(8229, [{"id": 9, "value": "Ларь"}], 85282223, 970988646,
                            attr_row={"category_dependent": True})
    assert bucket == BUCKET_SCOPED
    assert calls["args"][1:3] == (85282223, 970988646)

    p, calls = _patch_ldb()
    with p:
        # attr_row 缺省 + 显式 cat_dep=False → global
        bucket = routed_set(4389, [{"id": 1, "value": "Китай"}], 1, 2,
                            category_dependent=False)
    assert bucket == BUCKET_GLOBAL
    assert calls["args"][1:3] == (0, 0)


def test_routed_set_ephemeral_skips_db():
    """巨型字典（truncated / 超上限）→ 不写库。"""
    p, calls = _patch_ldb()
    with p:
        bucket = routed_set(85, [{"id": i, "value": f"b{i}"} for i in range(50)],
                            85282223, 970988646, truncated=True)
    assert bucket == BUCKET_EPHEMERAL
    assert "args" not in calls  # 零 DB 写


# ═══════════ 读回退（scoped → global） ═══════════

def test_get_dictionary_values_falls_back_to_global():
    """scoped 未命中 → 全局桶 (attr,0,0) 回退（真实 PG）。"""
    import sqlalchemy
    url = os.environ.get("PGDATABASE_URL",
                         "postgresql://postgres:localdev123@localhost:5433/ozon")
    try:
        eng = sqlalchemy.create_engine(url)
        with eng.connect():
            pass
    except Exception:
        import pytest
        pytest.skip("本地 PG 不可达")

    from utils.ozon_category_query import get_category_query
    import time as _t
    from utils.local_db_manager import LocalDBManager
    ldb = LocalDBManager()
    aid = 999000111  # 测试专用 id，避免污染真实属性
    try:
        # 只写全局桶
        ldb.set_dictionary_value_cache(aid, 0, 0, [{"id": 7, "value": "GLOBAL"}],
                                       expires_in=3600)
        # scoped (dc,tp) 查询应回退命中全局桶
        vals = get_category_query().get_dictionary_values(aid, 85282223, 970988646)
        assert vals == [{"id": 7, "value": "GLOBAL"}]
        # scoped 命中优先于 global
        ldb.set_dictionary_value_cache(aid, 85282223, 970988646,
                                       [{"id": 8, "value": "SCOPED"}], expires_in=3600)
        vals = get_category_query().get_dictionary_values(aid, 85282223, 970988646)
        assert vals == [{"id": 8, "value": "SCOPED"}]
        # routed_get 透传
        assert routed_get(aid, 85282223, 970988646) == [{"id": 8, "value": "SCOPED"}]
    finally:
        try:
            import time as _now
            from storage.database.db import get_session
            from sqlalchemy import text
            s = get_session()
            s.execute(text("DELETE FROM dictionary_value_cache WHERE attribute_id=:a"),
                      {"a": aid})
            s.commit()
            s.close()
        except Exception:
            pass


# ═══════════ assemble fetch 首页即止 ═══════════

def test_assemble_fetch_caps_on_first_page_has_next():
    """巨型字典（首页 has_next）→ 只调一次 API、返回首页值；limit=2000。"""
    import graphs.nodes.assemble_ozon_product_node as asm

    payloads = []

    class _R:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"result": [{"id": i, "value": f"b{i}"} for i in range(2000)],
                    "has_next": True}

    # F-F01: _fetch_dict_values_from_ozon 已委托 utils.fetch_dictionary_values，
    # mock 其底层 ozon_post（解析后 dict 应答）
    def _post(client_id, api_key, endpoint, body=None, timeout=30, **kw):
        payloads.append(body)
        return {"result": [{"id": i, "value": f"b{i}"} for i in range(2000)],
                "has_next": True}

    with mock.patch("utils.ozon_dict_values.ozon_post", side_effect=_post):
        vals = asm._fetch_dict_values_from_ozon("cid", "key", 1, 2, 85)
    assert len(payloads) == 1, "首页即 has_next 不再翻页"
    assert payloads[0]["limit"] == 2000  # 契约上限（旧 5000 违约）
    assert len(vals) == 2000


def test_assemble_cache_routes_by_attr_row():
    """_cache_dict_values 按 attr_row 的 cat_dep 路由；缺省保守 scoped。"""
    import graphs.nodes.assemble_ozon_product_node as asm
    from utils.dict_value_cache import BUCKET_GLOBAL, BUCKET_SCOPED

    p, calls = _patch_ldb()
    with p:
        bucket = asm._cache_dict_values(
            4389, 85282223, 970988646, [{"id": 1, "value": "Китай"}],
            attr_row={"category_dependent": False})
        assert bucket == BUCKET_GLOBAL
        assert calls["args"][1:3] == (0, 0)

        bucket = asm._cache_dict_values(
            8229, 85282223, 970988646, [{"id": 9, "value": "Ларь"}])
        assert bucket == BUCKET_SCOPED  # 无 attr_row → 保守 scoped（行为同旧版）
        assert calls["args"][1:3] == (85282223, 970988646)


# ═══════════ warm fetch 探测模式 ═══════════

def test_warm_fetch_dict_values_probe_mode():
    """warm fetch：max_pages=1 首页即 has_next → (首页值, truncated=True) 且只调一次。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "warm_cache", os.path.join(os.path.dirname(__file__), "..", "scripts", "warm_category_cache.py"))
    warm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(warm)

    calls = []

    def _fake_api(endpoint, payload):
        calls.append(payload)
        return {"result": [{"id": i, "value": f"b{i}"} for i in range(2000)],
                "has_next": True}

    with mock.patch.object(warm, "_call_ozon_api", _fake_api):
        vals, truncated = warm.fetch_dict_values(85, 1, 2, max_pages=1)
    assert truncated is True and len(vals) == 2000
    assert len(calls) == 1 and calls[0]["limit"] == 2000

    # 小字典：单页拉完 truncated=False
    def _fake_small(endpoint, payload):
        calls.append(payload)
        return {"result": [{"id": 1, "value": "Ларь"}], "has_next": False}

    with mock.patch.object(warm, "_call_ozon_api", _fake_small):
        vals, truncated = warm.fetch_dict_values(8229, 1, 2)
    assert truncated is False and len(vals) == 1


# ═══════════ NULL type_id 写边界加固（v0.72 补漏） ═══════════
# 唯一键含 nullable type_id：PG 里 NULL≠NULL 不触发 ON CONFLICT，upsert 会退化
# 为纯 INSERT 无限裂行。routed_set 恒传 int 键绕开了雷，但写入边界必须设防。

def test_set_dictionary_value_cache_none_type_id_upserts_single_row():
    """type_id=None 两次写入必须命中同一行（历史行为：NULL 裂成两行）。"""
    from sqlalchemy import text

    from storage.database.db import get_session
    from utils.local_db_manager import LocalDBManager

    attr = -990003001
    db = LocalDBManager()
    try:
        db.set_dictionary_value_cache(attr, 111, None,
                                      [{"id": 1, "value": "a"}], language="ZH_HANS")
        db.set_dictionary_value_cache(attr, 111, None,
                                      [{"id": 1, "value": "a"}, {"id": 2, "value": "b"}],
                                      language="ZH_HANS")
        s = get_session()
        try:
            n = s.execute(text(
                "SELECT count(*) FROM dictionary_value_cache WHERE attribute_id = :a"),
                {"a": attr}).scalar()
            row = s.execute(text(
                "SELECT type_id, jsonb_array_length(values_data) FROM dictionary_value_cache "
                "WHERE attribute_id = :a"), {"a": attr}).fetchone()
        finally:
            s.close()
        assert n == 1, f"NULL type_id 裂行：{n} 行"
        assert row[0] == 0 and row[1] == 2
    finally:
        s = get_session()
        try:
            s.execute(text("DELETE FROM dictionary_value_cache WHERE attribute_id = :a"),
                      {"a": attr})
            s.commit()
        finally:
            s.close()
