r"""v0.76 安全修复（inj-L1）: ILIKE 通配符转义测试。

背景：用户搜索词里的 `%`/`_` 此前原样进 ILIKE 模式——`q=%` 等价全表扫
（行为混淆 + 资源消耗）。修复 = `utils/like_escape.escape_like`（`\`→`\\`、
`%`→`\%`、`_`→`\_`，反斜杠先行）+ 接线点 SQL 补 `ESCAPE '\'`。

覆盖（接线三处全部真实 PG 行为断言 + 两 SQL 形态各至少一测）：
1. 纯函数：`escape_like("100%_x") == "100\\%\\_x"`、反斜杠先行、恒等情形
2. text() 形态行为：search_public / list_queries（blue_ocean_queries）+
   list_bestsellers（ozon_bestsellers）——probe_ 前缀种子行，搜 `%`
   恰命中含字面 `%` 的行、不扩表命中无 % 的行；附不转义对照（演示扩表）
3. ORM 形态：`.ilike(escape_like(q), escape="\\")` 编译出 `ESCAPE '\'`
"""
import os
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import column, create_engine, text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from utils.like_escape import escape_like  # noqa: E402

DB_URL = os.environ.get(
    "PGDATABASE_URL",
    "postgresql://postgres:localdev123@localhost:5433/ozon",
)

# probe_ 前缀纪律：本文件所有落库行可按 token / uuid 前缀整体清理
PROBE_TOKEN = "probe_like_escape"


# ---------- 1. 纯函数 ----------

def test_escape_percent_and_underscore():
    assert escape_like("100%_x") == "100\\%\\_x"


def test_escape_backslash_first():
    # 反斜杠必须先转义自身：尾随 `\` 不得被当成后续 `%` 的转义符吞掉
    assert escape_like("a\\") == "a\\\\"
    assert escape_like("a\\%b") == "a\\\\\\%b"
    assert escape_like("\\_%") == "\\\\\\_\\%"


def test_escape_plain_string_identity():
    assert escape_like("宠物用品") == "宠物用品"
    assert escape_like("100x") == "100x"
    assert escape_like("") == ""


# ---------- 2. text() 形态行为（真实 PG） ----------

@pytest.fixture(scope="module")
def _pg():
    try:
        eng = create_engine(DB_URL)
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        eng.dispose()
    except Exception as exc:  # pragma: no cover - 环境不可用时跳过
        pytest.skip(f"PG 不可用（{exc}），跳过 ILIKE 转义行为测试")
    from storage.database.shared.model import Base
    Base.metadata.tables["blue_ocean_queries"].create(
        bind=create_engine(DB_URL), checkfirst=True
    )
    Base.metadata.tables["ozon_bestsellers"].create(
        bind=create_engine(DB_URL), checkfirst=True
    )


@pytest.fixture
def _probe_rows(_pg):
    """种子 probe_ 行：蓝海表（含字面 % / _ 行 + 无通配符对照行）+ 榜单表同理。

    断言一律按本轮 uuid 前缀圈定（对表内既有行免疫）；yield 后按
    PROBE_TOKEN 整体清理（探针数据不残留）。
    """
    h = uuid.uuid4().hex[:8]
    pct_query = f"probe_{h} 100% 折扣"       # 含字面 %
    plain_query = f"probe_{h} plain row"     # 无通配符（仅前缀单 _）
    us_query = f"probe_{h} under_score"      # 含字面 _（单）
    us2_query = f"probe_{h} a__b deep"       # 含字面 __（连续双）
    eng = create_engine(DB_URL)
    with eng.begin() as conn:
        conn.execute(text(
            "INSERT INTO blue_ocean_queries "
            "(query, count, contributed_by_token_id, source) VALUES "
            "(:p, 10, :t, 'admin'), (:q, 10, :t, 'admin'), "
            "(:u, 10, :t, 'admin'), (:u2, 10, :t, 'admin')"
        ), [{"p": pct_query, "q": plain_query, "u": us_query,
             "u2": us2_query, "t": PROBE_TOKEN}])
        conn.execute(text(
            "INSERT INTO ozon_bestsellers "
            "(sku_or_id, brand, category_path, ordering_count, contributed_by_token_id, source) VALUES "
            "(:s1, :b1, :c1, 10, :t, 'skill'), (:s2, :b2, :c2, 10, :t, 'skill')"
        ), {
            "s1": f"probe-{h}-1", "b1": f"probe_br{h}%off", "c1": f"probe_cat_{h} pct",
            "s2": f"probe-{h}-2", "b2": f"probe_br{h}plain", "c2": f"probe_cat_{h} plain",
            "t": PROBE_TOKEN,
        })
    eng.dispose()
    yield {"h": h, "pct_query": pct_query, "plain_query": plain_query,
           "us_query": us_query, "us2_query": us2_query,
           "brand_pct": f"probe_br{h}%off"}
    eng = create_engine(DB_URL)
    with eng.begin() as conn:
        conn.execute(text(
            "DELETE FROM blue_ocean_queries WHERE contributed_by_token_id = :t"
        ), {"t": PROBE_TOKEN})
        conn.execute(text(
            "DELETE FROM ozon_bestsellers WHERE contributed_by_token_id = :t"
        ), {"t": PROBE_TOKEN})
    eng.dispose()


def test_search_public_percent_matches_literal_only(_pg, _probe_rows):
    """修复主断言：q='%' 只命中含字面 % 的行，不再全表扫（对照行不命中）。"""
    from services import queries_service
    rows = queries_service.search_public(q="%", limit=50)
    prefix = f"probe_{_probe_rows['h']}"
    assert [r["query"] for r in rows if r["query"].startswith(prefix)] == [
        _probe_rows["pct_query"]
    ]


def test_search_public_underscore_matches_literal_only(_pg, _probe_rows):
    """q='__'（连续双下划线）只命中含字面 __ 的行。

    设计约束：probe_ 前缀自带单 _，故不能用单 _ 做区分（修前修后都全命中）；
    修前 q='__' 通配扩表全命中，修后仅 us2 行含字面 __。
    """
    from services import queries_service
    rows = queries_service.search_public(q="__", limit=50)
    prefix = f"probe_{_probe_rows['h']}"
    assert [r["query"] for r in rows if r["query"].startswith(prefix)] == [
        _probe_rows["us2_query"]
    ]


def test_list_queries_search_escaped(_pg, _probe_rows):
    """list_queries（admin 浏览）同语义：search='100%' 按字面命中。"""
    from services import queries_service
    result = queries_service.list_queries(search="100%")
    prefix = f"probe_{_probe_rows['h']}"
    assert [i["query"] for i in result["items"] if i["query"].startswith(prefix)] == [
        _probe_rows["pct_query"]
    ]


def test_unescaped_contrast_shows_expansion(_pg, _probe_rows):
    """不转义对照：同一数据上，未转义 `%{q}%` 扩表命中无 % 行（旧缺陷演示）。

    _probe_rows 依赖保证种子行在场；本轮前缀圈定，与表内既有行无关。
    """
    prefix = f"probe_{_probe_rows['h']}%"
    eng = create_engine(DB_URL)
    with eng.connect() as conn:
        raw_bad = conn.execute(text(
            "SELECT count(*) FROM blue_ocean_queries "
            "WHERE query ILIKE :p AND query LIKE :prefix"
        ), {"p": "%%%", "prefix": prefix}).scalar()
        raw_good = conn.execute(text(
            "SELECT count(*) FROM blue_ocean_queries "
            "WHERE query ILIKE :p ESCAPE '\\' AND query LIKE :prefix"
        ), {"p": "%\\%%", "prefix": prefix}).scalar()
    eng.dispose()
    assert int(raw_bad) >= 2   # 旧形态：无 % 的对照行也被扫中（扩表）
    assert int(raw_good) == 1  # 转义后：只中含字面 % 的一行


def test_list_bestsellers_brand_percent_literal_only(_pg, _probe_rows):
    """榜单 brand 筛选：brand='%' 只命中含字面 % 的行（analytics 接线点）。"""
    from services.analytics_service import list_bestsellers
    result = list_bestsellers(PROBE_TOKEN, brand="%")
    prefix = f"probe_br{_probe_rows['h']}"
    assert [i["brand"] for i in result["items"] if i["brand"].startswith(prefix)] == [
        _probe_rows["brand_pct"]
    ]


# ---------- 3. ORM 形态（编译级，无需 PG） ----------

def test_orm_ilike_escape_clause_rendered(_pg):
    """ORM 接线配方：`.ilike(escape_like(q), escape="\\")` 渲染出 ESCAPE 子句。

    ⚠️ 必须用「活连接后」的 engine dialect 编译：psycopg2 dialect 连接时探测
    服务器 standard_conforming_strings（本库=on）后才把转义符渲染成单反斜杠
    `'\'`（PG 实测有效）；未连接的离线 dialect 默认 backslash_escapes=True
    会渲染成 '\\'，那是面向 scs=off 解析器的形态，照发 scs=on 库会报
    invalid escape string——勿拿离线 compile 文本当可执行 SQL。
    """
    col = column("query")
    stmt = col.ilike(escape_like("100%_x"), escape="\\")
    eng = create_engine(DB_URL)
    try:
        with eng.connect():
            pass  # 触发连接时 dialect 校准（scs 探测）
        compiled = stmt.compile(dialect=eng.dialect)
    finally:
        eng.dispose()
    assert "ESCAPE '\\'" in str(compiled)  # SQL 文本内单引号包单反斜杠
    # .ilike() 不自动包 %——两侧 % 由调用方拼（f"%{escape_like(q)}%"）
    assert compiled.params == {"query_1": "100\\%\\_x"}


def test_pattern_builder_with_wrapping_percent():
    """两侧包 % 的模式构造与转义协同（接线点统一写法）。"""
    q = "a_b%c\\d"
    assert f"%{escape_like(q)}%" == "%a\\_b\\%c\\\\d%"
