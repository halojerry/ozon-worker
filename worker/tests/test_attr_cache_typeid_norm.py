# worker/tests/test_attr_cache_typeid_norm.py
"""B2a-2: set_attribute_cache type_id 归一（纯 mock，无 PG 依赖）。

背景：attribute_cache 唯一键 (description_category_id, type_id, language) 含
type_id，PG 里 NULL≠NULL——type_id 裸传 None 时 ON CONFLICT 永不命中，upsert
退化为纯 INSERT 无限裂行。对齐 v0.72 set_dictionary_value_cache 同款修法。

运行：PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_attr_cache_typeid_norm.py -q
"""
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from utils import local_db_manager  # noqa: E402


class _FakeInsert:
    """pg_insert 替身：捕获 values(**kw) 写入值（链式 on_conflict_do_update 透传）。"""

    def __init__(self, captured: list):
        self._captured = captured

    def values(self, **kw):
        self._captured.append(kw)
        return self

    def on_conflict_do_update(self, **kw):
        return self


def _run_set_attribute_cache(type_id):
    """以假 session/假 pg_insert 跑一次 set_attribute_cache，返回写入 values。"""
    captured: list = []
    fake_session = mock.MagicMock()
    with mock.patch.object(local_db_manager, "pg_insert", lambda model: _FakeInsert(captured)), \
         mock.patch.object(local_db_manager, "get_session", return_value=fake_session):
        local_db_manager.LocalDBManager().set_attribute_cache(12345, type_id, {"attrs": []})
    assert fake_session.commit.call_count == 1  # 单次 commit 语义保持
    return captured[0]


def test_type_id_none_normalized_to_zero():
    """None → 0（NULL 唯一键永不命中 ON CONFLICT，必须归一）。"""
    values = _run_set_attribute_cache(None)
    assert values["type_id"] == 0
    assert isinstance(values["type_id"], int)


def test_type_id_zero_stays_zero_int():
    """0 → 0（int，不变形）。"""
    values = _run_set_attribute_cache(0)
    assert values["type_id"] == 0
    assert isinstance(values["type_id"], int)


def test_type_id_positive_passthrough_int():
    """正数 → 原值 int 透传。"""
    values = _run_set_attribute_cache(8229)
    assert values["type_id"] == 8229
    assert isinstance(values["type_id"], int)


def test_other_fields_untouched():
    """归一只动 type_id，其余字段原样写入。"""
    values = _run_set_attribute_cache(8229)
    assert values["description_category_id"] == 12345
    assert values["language"] == "ZH_HANS"
    assert values["attributes_schema"] == {"attrs": []}
    assert isinstance(values["expires_at"], int)
    assert isinstance(values["created_at"], int)
