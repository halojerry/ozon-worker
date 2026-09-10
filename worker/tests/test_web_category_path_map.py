"""web_category_path_map 读写单测（F-B04：Web 面包屑 → Seller dc/tp 映射）。

需本地 PG（5433）。覆盖：键规范化 / upsert+lookup 往返（hit_count 递增）/
重复 upsert 覆盖不裂行 / 守卫（空面包屑、缺 dc/tp）/ learning 写侧 source=page 守卫。
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

PG = os.getenv("PGDATABASE_URL", "postgresql://postgres:localdev123@localhost:5433/ozon")

pytestmark = pytest.mark.skipif(
    not os.getenv("PGDATABASE_URL") and not os.path.exists("/tmp/has_pg"),
    reason="需要 PGDATABASE_URL",
)


def _engine():
    from sqlalchemy import create_engine
    return create_engine(PG)


def _mk_tables(engine):
    from storage.database.shared.model import Base
    Base.metadata.create_all(bind=engine)


def _ldb():
    from utils.local_db_manager import LocalDBManager
    return LocalDBManager()


def test_normalize_breadcrumb_key(tmp_path):
    ldb = _ldb()
    assert ldb.normalize_breadcrumb_key("  A  >   B > c ") == ldb.normalize_breadcrumb_key("a > b > c")
    assert ldb.normalize_breadcrumb_key("Туризм  >  Термосы") == "туризм > термосы"
    assert ldb.normalize_breadcrumb_key("") == ""


def test_upsert_lookup_roundtrip_hit_count():
    eng = _engine()
    _mk_tables(eng)
    ldb = _ldb()
    key = "туризм > термосы, фляги и питьевые системы > термосы"
    assert ldb.upsert_web_category_path(key, 17027928, 92574, language="RU",
                                        task_id="task-a") is True
    hit = ldb.lookup_web_category_path(key)
    assert hit and hit["description_category_id"] == "17027928"
    assert hit["type_id"] == "92574"
    hit2 = ldb.lookup_web_category_path(key.upper())  # 大小写不敏感（规范化键）
    assert hit2 and hit2["description_category_id"] == "17027928"
    # hit_count 递增：查两次 + upsert 重置为 0 后又查一次 ≥2
    from sqlalchemy import text
    with eng.connect() as c:
        n = c.execute(text("SELECT hit_count FROM web_category_path_map WHERE breadcrumb_key=:k"),
                      {"k": ldb.normalize_breadcrumb_key(key)}).scalar()
    assert n >= 2


def test_upsert_same_key_overwrites_not_splits():
    eng = _engine()
    _mk_tables(eng)
    ldb = _ldb()
    key = "тест > уникальный путь xyz"
    ldb.upsert_web_category_path(key, 1, 11, task_id="t1")
    ldb.upsert_web_category_path(key, 2, 22, task_id="t2")
    from sqlalchemy import text
    with eng.connect() as c:
        rows = c.execute(text("SELECT description_category_id, type_id FROM web_category_path_map WHERE breadcrumb_key=:k"),
                         {"k": ldb.normalize_breadcrumb_key(key)}).fetchall()
    assert rows == [(2, 22)], "同键覆盖不裂行"


def test_guards_empty_breadcrumb_or_missing_ids():
    ldb = _ldb()
    assert ldb.upsert_web_category_path("", 1, 1) is False
    assert ldb.upsert_web_category_path("x > y", 0, 1) is False
    assert ldb.lookup_web_category_path("") is None


def test_learning_backfill_requires_page_source():
    """learning 写侧守卫：source≠page / 非 approved → 跳过（不写表）。"""
    from graphs.nodes.learning_record_node import _backfill_web_category_path
    from storage.database.db import get_engine as _ge  # noqa: F401 确认可导入

    ldb = _ldb()
    eng = _engine()
    _mk_tables(eng)

    # source=search_kw（猜测）→ 即使 approved 也不写
    state = SimpleNamespace(
        moderation_status="approved", upload_status="success", product_id="123",
        description_category_id="17027928", type_id="92574", task_id="t-x",
        draft={"ozon_category": {"source": "search_kw",
                                  "category_path": "туризм > термосы"}},
    )
    with mock.patch("utils.local_db_manager.LocalDBManager.upsert_web_category_path",
                    return_value=True) as m_up:
        _backfill_web_category_path(state)
        m_up.assert_not_called()

    # source=page + approved → 写
    state.draft["ozon_category"]["source"] = "page"
    from utils.local_db_manager import LocalDBManager
    with mock.patch.object(LocalDBManager, "upsert_web_category_path", return_value=True) as m_up2:
        _backfill_web_category_path(state)
        assert m_up2.called
        args, kwargs = m_up2.call_args
        assert args[0] == "туризм > термосы"
        assert args[1] == 17027928 and args[2] == 92574


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
