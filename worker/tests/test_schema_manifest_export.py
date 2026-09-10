"""BL-19（A4 F-P0-1 资产化）: warm --export-schema-manifest 回归（纯 mock，零 PG 依赖）。

锁定四点：
1. flag 存在且为独立模式（与其他模式互斥，冲突退出码 2）；
2. 清单行聚合口径（required/dict/collection/numeric 归类 + max_value_count_max 统计）；
3. 空 schema / 无 id 属性行跳过；
4. 导出 SQL 纯 SELECT 零绑定参数（sqlalchemy-jsonb-cast-trap 红线自查）+ 原子写。
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pytest

warm = pytest.importorskip("warm_category_cache")


# ── CLI 面 ──

def test_manifest_flag_exists():
    parser = warm.build_arg_parser()
    opts = {a.option_strings[0] for a in parser._actions if a.option_strings}
    assert "--export-schema-manifest" in opts


def test_manifest_standalone_parse():
    args = warm.parse_args(["--export-schema-manifest", "/tmp/m.json"])
    assert args.export_schema_manifest == "/tmp/m.json"


def test_manifest_rejects_conflicting_flags():
    for argv in (
        ["--export-schema-manifest", "/tmp/m.json", "--force"],
        ["--export-schema-manifest", "/tmp/m.json", "--limit", "5"],
        ["--export-schema-manifest", "/tmp/m.json", "--export-from-pg"],
        ["--export-from-pg", "--export-schema-manifest", "/tmp/m.json"],
    ):
        with pytest.raises(SystemExit) as ei:
            warm.parse_args(argv)
        assert ei.value.code == 2


# ── 清单行聚合（纯函数）──

def test_build_row_aggregation_two_attrs():
    schema = [
        {   # 8229 Тип：必填 + 字典 + 恒单值（max_value_count=1），type 非数值
            "id": 8229, "name": "Тип", "is_required": True,
            "dictionary_id": 1960, "is_collection": False,
            "max_value_count": 1, "type": "String",
        },
        {   # 8962 件数：数值型（大小写不敏感）+ 集合 + 上限 10
            "id": 8962, "name": "Единиц", "is_required": False,
            "dictionary_id": 0, "is_collection": True,
            "max_value_count": 10, "type": "Integer",
        },
        {   # 无 max_value_count 的自由文本：不计入 max 统计
            "id": 4180, "name": "Название", "is_required": True,
            "dictionary_id": 0, "is_collection": False, "type": "String",
        },
    ]
    row = warm.build_schema_manifest_row(17028701, 971298965, "ZH_HANS", schema)
    assert row == {
        "dc": 17028701, "tp": 971298965, "language": "ZH_HANS",
        "attr_total": 3,
        "required": [4180, 8229],
        "dict_attrs": [8229],
        "collection_attrs": [8962],
        "numeric_attrs": [8962],
        "max_value_count_max": 10,
    }


def test_build_row_lowercase_numeric_type_and_bool_guard():
    row = warm.build_schema_manifest_row(1, 2, "ZH_HANS", [
        {"id": 100, "type": "decimal", "max_value_count": True},  # bool 不计 max
        {"id": 101, "type": "number", "max_value_count": 5.0},
    ])
    assert row is not None
    assert row["numeric_attrs"] == [100, 101]
    assert row["max_value_count_max"] == 5


def test_build_row_empty_or_idless_schema_skipped():
    assert warm.build_schema_manifest_row(1, 2, "ZH_HANS", None) is None
    assert warm.build_schema_manifest_row(1, 2, "ZH_HANS", []) is None
    assert warm.build_schema_manifest_row(1, 2, "ZH_HANS", "junk") is None
    # 属性行全缺 id → 无法归类 → 整行跳过
    assert warm.build_schema_manifest_row(1, 2, "ZH_HANS", [{"name": "x"}]) is None
    # 非 dict 行被忽略，有效行仍计数
    row = warm.build_schema_manifest_row(1, 2, "ZH_HANS", ["bad", {"id": 7}])
    assert row is not None and row["attr_total"] == 1


# ── 导出链路（mock session，零 PG）──

class _FakeResult:
    def __init__(self, rows):
        self._rows = list(rows)

    def mappings(self):
        return self

    def fetchmany(self, n):
        if not self._rows:
            return []
        chunk, self._rows = self._rows[:n], self._rows[n:]
        return chunk


class _FakeSession:
    """记录被执行的 SQL，供绑定参数红线断言。"""

    def __init__(self, rows):
        self.rows = rows
        self.executed_sql = []
        self.closed = False

    def execute(self, clause, *a, **k):
        self.executed_sql.append(str(clause))
        return _FakeResult(self.rows)

    def close(self):
        self.closed = True


_TWO_ROW_FIXTURE = [
    {   # 行 1：完整 schema（与聚合用例同构）
        "description_category_id": 17028701, "type_id": 971298965,
        "language": "ZH_HANS",
        "attributes_schema": [
            {"id": 8229, "is_required": True, "dictionary_id": 1960,
             "is_collection": False, "max_value_count": 1, "type": "String"},
            {"id": 8962, "is_required": False, "dictionary_id": 0,
             "is_collection": True, "max_value_count": 10, "type": "Integer"},
        ],
    },
    {   # 行 2：空 schema → 跳过并计数
        "description_category_id": 111, "type_id": 222,
        "language": "ZH_HANS", "attributes_schema": [],
    },
]


def test_export_manifest_mock_session(tmp_path):
    path = str(tmp_path / "manifest.json")
    fake = _FakeSession(_TWO_ROW_FIXTURE)
    n = warm.export_schema_manifest(path, session=fake)
    assert n == 1
    # 注入的 session 归调用方管——函数不得代关
    assert fake.closed is False

    with open(path, "rb") as f:
        raw = f.read()
    manifest = json.loads(raw.decode("utf-8"))
    assert manifest["total_categories"] == 1
    assert manifest["skipped_empty_schema"] == 1
    cat = manifest["categories"][0]
    assert cat["dc"] == 17028701 and cat["tp"] == 971298965
    assert cat["required"] == [8229]
    assert cat["dict_attrs"] == [8229]
    assert cat["collection_attrs"] == [8962]
    assert cat["numeric_attrs"] == [8962]
    assert cat["max_value_count_max"] == 10
    assert isinstance(manifest["generated_at"], int)

    # 原子写：无 tmp 残留
    leftovers = [p for p in os.listdir(tmp_path) if ".tmp." in p]
    assert leftovers == []


def test_manifest_sql_has_no_bind_params():
    """红线自查：纯 SELECT 零绑定参数（无 :bind → 无裸 ::cast 陷阱面）。"""
    fake = _FakeSession(_TWO_ROW_FIXTURE)
    warm.export_schema_manifest("/tmp/__unused__.json", session=fake)
    assert len(fake.executed_sql) == 1
    sql = fake.executed_sql[0]
    assert "attribute_cache" in sql
    assert ":" not in sql, f"发现绑定参数占位（须走 CAST 纪律或保持零绑定）: {sql}"
