"""C3: 蓝海关键词库管理服务测试（真实 PG，admin_import 隔离）。

验收门（docs/PRD-skill-image-search-v1.md §C3）：
1. list_queries 返回 total + items（种子 2 行）
2. list_queries search 按关键词过滤（ILIKE）
3. import_queries 插入新行（contributed_by_token_id=admin_import, source=admin）
4. 同 query 重复导入 → 更新不重复（该 query 仅 1 行）
5. 缺 query → 跳过 + 记错误
6. import_queries_csv 解析 csv（BOM 处理）+ 去重
7. delete_query 删除行返回 True；不存在 → False
8. 混合导入：1 有效 + 1 无效 → imported=1, errors=1
"""
import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from services import queries_service  # noqa: E402

DB_URL = os.environ.get(
    "PGDATABASE_URL",
    "postgresql://postgres:localdev123@localhost:5433/ozon",
)
ADMIN_TOKEN = "admin_import"


@pytest.fixture(scope="module")
def _pg():
    """PG 可用性探针 + 建表（幂等，只建 blue_ocean_queries 单表）。"""
    try:
        eng = create_engine(DB_URL)
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        eng.dispose()
    except Exception as exc:  # pragma: no cover - 环境不可用时跳过
        pytest.skip(f"PG 不可用（{exc}），跳过 queries 服务测试")
    from storage.database.shared.model import Base
    Base.metadata.tables["blue_ocean_queries"].create(
        bind=create_engine(DB_URL), checkfirst=True
    )


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("PGDATABASE_URL", DB_URL)


@pytest.fixture(autouse=True)
def _cleanup(_pg):
    """每个测试后清理 admin_import 行，保证用例间隔离。"""
    yield
    eng = create_engine(DB_URL)
    with eng.begin() as conn:
        conn.execute(text(
            "DELETE FROM blue_ocean_queries WHERE contributed_by_token_id = :t"
        ), {"t": ADMIN_TOKEN})
    eng.dispose()


def _seed_rows(rows: list[dict]):
    """直接 SQL 种子 admin_import 行（绕过被测服务，验证读路径）。"""
    eng = create_engine(DB_URL)
    with eng.begin() as conn:
        conn.execute(text(
            "INSERT INTO blue_ocean_queries (query, count, ca, contributed_by_token_id, source) "
            "VALUES (:query, :count, :ca, :t, 'admin')"
        ), [{"query": r["query"], "count": r.get("count", 0),
             "ca": r.get("ca"), "t": ADMIN_TOKEN} for r in rows])
    eng.dispose()


def _fetch_rows():
    """读回 admin_import 行（验证写路径落库）。"""
    eng = create_engine(DB_URL)
    with eng.connect() as conn:
        rows = conn.execute(text(
            "SELECT id, query, count, ca, contributed_by_token_id, source "
            "FROM blue_ocean_queries WHERE contributed_by_token_id = :t ORDER BY id"
        ), {"t": ADMIN_TOKEN}).fetchall()
    eng.dispose()
    return rows


# ============================================================
# 1. list_queries 分页 + total
# ============================================================

def test_list_queries_returns_total_and_items(_pg):
    _seed_rows([{"query": "宠物饮水机", "count": 10}, {"query": "猫砂盆", "count": 5}])
    result = queries_service.list_queries()
    assert result["total"] == 2
    assert len(result["items"]) == 2
    queries = {it["query"] for it in result["items"]}
    assert queries == {"宠物饮水机", "猫砂盆"}


# ============================================================
# 2. list_queries search 过滤
# ============================================================

def test_list_queries_search_filters(_pg):
    _seed_rows([{"query": "宠物饮水机", "count": 10}, {"query": "猫砂盆", "count": 5}])
    result = queries_service.list_queries(search="宠物")
    assert result["total"] == 1
    assert len(result["items"]) == 1
    assert result["items"][0]["query"] == "宠物饮水机"


# ============================================================
# 3. import_queries 插入新行
# ============================================================

def test_import_queries_inserts_new_rows(_pg):
    result = queries_service.import_queries([{"query": "新关键词", "count": 3, "ca": 1.5}])
    assert result["imported"] == 1
    assert result["updated"] == 0
    assert result["errors"] == []
    rows = _fetch_rows()
    assert len(rows) == 1
    assert rows[0][1] == "新关键词"
    assert rows[0][2] == 3
    assert rows[0][3] == 1.5
    assert rows[0][4] == "admin_import"  # contributed_by_token_id 强制
    assert rows[0][5] == "admin"         # source 强制


# ============================================================
# 4. 同 query 重复导入 → 更新不重复
# ============================================================

def test_import_queries_reimport_updates(_pg):
    first = queries_service.import_queries([{"query": "重复词", "count": 1}])
    assert first["imported"] == 1
    second = queries_service.import_queries([{"query": "重复词", "count": 9}])
    assert second["imported"] == 0
    assert second["updated"] == 1
    rows = _fetch_rows()
    assert len(rows) == 1  # 无重复行
    assert rows[0][1] == "重复词"
    assert rows[0][2] == 9  # 值已更新


# ============================================================
# 5. 缺 query → 跳过 + 记错误
# ============================================================

def test_import_queries_missing_query_skipped(_pg):
    result = queries_service.import_queries([{"count": 3}])
    assert result["imported"] == 0
    assert len(result["errors"]) == 1
    assert result["errors"][0]["row"] == 1
    assert "query" in result["errors"][0]["error"]
    assert _fetch_rows() == []


# ============================================================
# 6. csv 导入（BOM 处理 + 去重）
# ============================================================

def test_import_queries_csv_parses_bom_and_dedups(_pg):
    csv_text = (
        "\ufeffquery,count,ca\n"
        "宠物饮水机,3,1.5\n"
        "猫砂盆,2,\n"
        "宠物饮水机,4,1.8\n"
    )
    result = queries_service.import_queries_csv(csv_text)
    assert result["imported"] == 2  # 两个不同关键词各插 1 行
    assert result["updated"] == 1   # 重复关键词走了更新
    assert result["errors"] == []
    rows = _fetch_rows()
    assert len(rows) == 2  # 去重：宠物饮水机仅 1 行
    by_query = {r[1]: r for r in rows}
    assert by_query["宠物饮水机"][2] == 4  # 最后一次值生效
    assert by_query["猫砂盆"][3] is None


# ============================================================
# 7. delete_query 删除 / 不存在
# ============================================================

def test_delete_query_removes_row(_pg):
    _seed_rows([{"query": "待删除", "count": 1}])
    qid = _fetch_rows()[0][0]
    assert queries_service.delete_query(qid) is True
    assert _fetch_rows() == []


def test_delete_query_missing_returns_false(_pg):
    assert queries_service.delete_query(99999999) is False


# ============================================================
# 8. 混合导入：1 有效 + 1 无效
# ============================================================

def test_import_queries_mixed_valid_invalid(_pg):
    result = queries_service.import_queries([
        {"query": "有效词", "count": 1},
        {"ca": "abc"},  # 缺 query
    ])
    assert result["imported"] == 1
    assert len(result["errors"]) == 1
    assert result["errors"][0]["row"] == 2
    rows = _fetch_rows()
    assert len(rows) == 1
    assert rows[0][1] == "有效词"
