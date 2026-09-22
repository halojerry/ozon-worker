r"""v0.76 修复波（PR#29 code-review 75 分项）: 类目链 ILIKE 通配符转义行为测试。

背景：AGENTS.md「ILIKE 用户输入一律 `escape_like`」此前只接了
queries/analytics 两处，`utils/ozon_category_query.py`（_fetch_rows_like /
_search_fallback——L1 类目匹配主通道与回退通道）还有 9 处裸
`.ilike(f"%{pattern}%")` 吃信封用户数据（search_kw / 标题 jieba token）。
本文件锁接线后的真实 PG 行为：含 `%`/`_` 的搜索词按字面匹配，不再当 SQL
通配符扩表（v0.65.1 已知痛点：多义 token 子串匹配 full_path 牵引错类目）。

probe_ 纪律：种子行按本轮 uuid 圈定 + yield 后按 dc 清理；type_id 用高位
随机数避开既有类目——本地库类目树为空也能自足跑（不依赖 init_data）。
种子/清理走 ORM Session（零裸 SQL 字符串，值全部经模型绑定）。
"""
import os
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

DB_URL = os.environ.get(
    "PGDATABASE_URL",
    "postgresql://postgres:localdev123@localhost:5433/ozon",
)


@pytest.fixture(scope="module")
def _pg():
    try:
        eng = create_engine(DB_URL)
        with eng.connect():
            pass  # 连通性探测：连不上即 skip
        eng.dispose()
    except Exception as exc:  # pragma: no cover - 环境不可用时跳过
        pytest.skip(f"PG 不可用（{exc}），跳过类目链 ILIKE 转义行为测试")
    from storage.database.shared.model import Base
    Base.metadata.tables["category_tree_nodes"].create(
        bind=create_engine(DB_URL), checkfirst=True
    )


@pytest.fixture
def _probe_nodes(_pg):
    """种子两个 probe 类目节点（type 叶子）：
    - wild 行：node_name 含字面 `%` 与 `_`（probe{h}A_100% off）
    - plain 行：node_name 无任何通配符、且不含 wild 行的任何子串特征
    断言一律按本轮 type_id 圈定（对表内既有/为空的类目树免疫）。
    """
    from sqlalchemy.orm import Session
    from storage.database.shared.model import CategoryTreeNode
    h = uuid.uuid4().hex[:8]
    dc = 900000000 + int(h[:4], 16)
    tp_wild = dc + 1
    tp_plain = dc + 2
    eng = create_engine(DB_URL)
    try:
        with Session(eng) as session:
            session.add_all([
                CategoryTreeNode(
                    description_category_id=dc,
                    type_id=tp_wild,
                    node_name=f"probe{h}A_100% off",
                    full_path=f"probe{h}/类别/probe{h}A_100% off",
                    top_level_category_name="probe",
                    depth=3,
                    language="ZH_HANS",
                    node_type="type",
                ),
                CategoryTreeNode(
                    description_category_id=dc,
                    type_id=tp_plain,
                    node_name=f"probe{h}B-plain-row",
                    full_path=f"probe{h}/类别/probe{h}B-plain-row",
                    top_level_category_name="probe",
                    depth=3,
                    language="ZH_HANS",
                    node_type="type",
                ),
            ])
            session.commit()
    finally:
        eng.dispose()
    yield {"h": h, "tp_wild": tp_wild, "tp_plain": tp_plain}
    eng = create_engine(DB_URL)
    try:
        with Session(eng) as session:
            session.query(CategoryTreeNode).filter(
                CategoryTreeNode.description_category_id == dc
            ).delete(synchronize_session=False)
            session.commit()
    finally:
        eng.dispose()


def _tps(rows):
    return {r["type_id"] for r in rows}


def test_fetch_rows_like_percent_matches_literal_only(_pg, _probe_nodes):
    """主断言（%）：pattern='%' 只命中 node_name 含字面 % 的行。

    修前行为（对照）：'%' 原样进 `%{%}%` = 全表通配，plain 行也被扫中。
    top_k 放大绕开 limit 截断——探针行必须确定性在场/缺席。
    """
    from utils.ozon_category_query import OzonCategoryQuery
    rows = OzonCategoryQuery()._fetch_rows_like("%", None, 1_000_000)
    tps = _tps(rows)
    assert _probe_nodes["tp_wild"] in tps
    assert _probe_nodes["tp_plain"] not in tps


def test_fetch_rows_like_underscore_matches_literal_only(_pg, _probe_nodes):
    """主断言（_）：pattern='_' 只命中含字面下划线的 wild 行，plain 行不扩表命中。"""
    from utils.ozon_category_query import OzonCategoryQuery
    rows = OzonCategoryQuery()._fetch_rows_like("_", None, 1_000_000)
    tps = _tps(rows)
    assert _probe_nodes["tp_wild"] in tps
    assert _probe_nodes["tp_plain"] not in tps


def test_fetch_rows_like_name_only_channel_escaped(_pg, _probe_nodes):
    """name_only 通道（单字品类词兜底）同语义：pattern='_' 不扩表。"""
    from utils.ozon_category_query import OzonCategoryQuery
    rows = OzonCategoryQuery()._fetch_rows_like("_", None, 1_000_000, name_only=True)
    tps = _tps(rows)
    assert _probe_nodes["tp_wild"] in tps
    assert _probe_nodes["tp_plain"] not in tps


def test_search_fallback_percent_word_literal_only(_pg, _probe_nodes):
    """回退通道（jieba 失败/非中文路径共用）：词 '100%' 按字面匹配 wild 行。

    修前 '100%' = '100'+任意 → 树内 '100X…' 行即噪声扩表；探针层面
    锁 plain 行（无 '100' 前缀特征）不被 '100%' 词的通配语义扫中。
    """
    from utils.ozon_category_query import OzonCategoryQuery
    rows = OzonCategoryQuery()._search_fallback(
        f"probe{_probe_nodes['h']}A 100% off", 50, None, "ZH_HANS"
    )
    tps = _tps(rows)
    assert _probe_nodes["tp_wild"] in tps
    assert _probe_nodes["tp_plain"] not in tps
