"""v0.73 W1 补漏: init_data / mappings_lookup 的裸 bind cast 修复回归。"""
import os
import re
import sys
import pathlib
import pytest

from sqlalchemy import text

_ROOT = pathlib.Path(__file__).resolve().parents[1]

_PG_URL = os.environ.get(
    "PGDATABASE_URL", "postgresql://postgres:localdev123@localhost:5433/ozon")


def _pg_ready():
    try:
        import psycopg2
        psycopg2.connect(_PG_URL, connect_timeout=3).close()
        return True
    except Exception:
        return False


pg = pytest.mark.skipif(not _pg_ready(), reason="local PG 5433 不可达")


def test_init_data_and_query_have_no_bare_bind_cast():
    for rel in ("scripts/init_data.py", "src/utils/ozon_category_query.py"):
        src = (_ROOT / rel).read_text(encoding="utf-8")
        hits = re.findall(r":[A-Za-z_]\w*::[a-zA-Z]+", src)
        assert hits == [], f"{rel} 发现裸 bind cast: {hits}"


def test_import_attribute_cache_scope_has_sql_text_import():
    """fix round 1: import_attribute_cache 体内缺 `from sqlalchemy import text as sql_text`
    局部导入 → 首次执行即 NameError（ruff F821），本文件锁定的 CAST 修复成死代码。
    同文件 import_category_tree/import_logistics_rates 均有同款局部导入，唯它缺失。"""
    import ast

    tree = ast.parse((_ROOT / "scripts" / "init_data.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "import_attribute_cache":
            imported = set()
            for sub in ast.walk(node):
                if isinstance(sub, ast.ImportFrom) and sub.module == "sqlalchemy":
                    for a in sub.names:
                        if a.name == "text":
                            imported.add(a.asname or a.name)
                elif isinstance(sub, ast.Import):
                    for a in sub.names:
                        imported.add(a.asname or a.name.split(".")[0])
            assert "sql_text" in imported, (
                "import_attribute_cache 作用域内缺 sql_text 导入——"
                "补 `from sqlalchemy import text as sql_text` 局部导入（对齐 :150/:248 两函数）"
            )
            return
    raise AssertionError("init_data.py 未找到 import_attribute_cache 函数")


@pg
def test_category_mapping_keyword_lookup_actually_queries():
    """端点此前恒空：SQLAlchemy 不识别 :kw::text[] → 异常被 except 吞掉 return []。"""
    os.environ.setdefault("PGDATABASE_URL", _PG_URL)
    sys.path.insert(0, str(_ROOT / "src"))
    from storage.database.db import get_session
    from storage.database.shared.model import CategoryMapping
    from utils.ozon_category_query import OzonCategoryQuery

    s = get_session()
    try:
        row = CategoryMapping(
            source_category_leaf="__probe_cast_v073__",
            source_category_path="__probe_cast_v073__",
            source_keywords=["__probecastkw__"],
            description_category_id=-999011, type_id=-999011,
            category_path_zh="", category_path_ru="",
            confidence=0.9, success_count=1, fail_count=0,
            source="learned_approved", is_active=True,
        )
        s.add(row)
        s.commit()
        hits = OzonCategoryQuery().get_category_mapping_by_keywords(
            ["__probecastkw__"], top_k=5)
        assert hits, "keyword lookup 仍返回空——CAST 修复未生效"
        assert hits[0]["source_category_leaf"] == "__probe_cast_v073__"
    finally:
        s.rollback()
        s.execute(text("DELETE FROM category_mapping WHERE source_category_leaf='__probe_cast_v073__'"))
        s.commit()
        s.close()
