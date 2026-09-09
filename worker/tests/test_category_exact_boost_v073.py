"""v0.73 类目 exact type_name 命中加分 + 低置信阈值常量唯一化（Issue2 根治）。

生产实证：装饰枕套在树里有 exact type 节点（type_id 92607，住宅和花园 > 床上用品
> 装饰枕套），但 L1 文本链多 token 查询的打分 similarity=matched/len(tokens) 只看
token 覆盖数——exact 命中没有加成，token 一多就被稀释（装饰枕套沙发抱枕靠垫汽车
床头 → 0.33；更长查询 → 0.25）< 0.3 被拦入采集箱。

修复：
- 多 token 分支 exact 加分：候选 node_name/full_path 末段与整查询 strip 相等
  → sim=max(sim,0.95)；互为前后缀（startswith 任一方向且长度差 ≥1）→ max(sim,0.8)。
- 单 token 分支 _score_token_hit 分档（1.0/0.7/0.6）不动。
- 低置信入箱阈值 0.3 收敛为常量 MIN_CONF_BOX（utils.ozon_category_query），
  graph.py 裸字面量改引常量；assemble 的 MIN_SIM_BY_MATCHER 是「采纳阈值」
  另一语义，不动。

运行（前 3 个用例需真实 PG 树，本地 5433）:
    cd worker && PGDATABASE_URL="postgresql://postgres:localdev123@localhost:5433/ozon" \
      PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_category_exact_boost_v073.py -q
"""
import os
import sys
from unittest import mock

import pytest

os.environ.setdefault("GRSAI_API_KEY", "test-key")
os.environ.setdefault("LOG_FORMAT", "text")
os.environ.setdefault("LOG_LEVEL", "WARNING")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

PILLOW_DC = 17028731   # 住宅和花园 > 床上用品（装饰枕套所在 dc）
PILLOW_TYPE = 92607    # 装饰枕套 exact type 节点（生产实证锚）


# ═══════════════════════════════════════════════════════════════════════
# 真实 PG 树用例（PG 不可达时 skip，不阻塞纯 mock 用例）
# ═══════════════════════════════════════════════════════════════════════
_PG_UP: bool | None = None


def _pg_up() -> bool:
    global _PG_UP
    if _PG_UP is None:
        try:
            from sqlalchemy import create_engine, text
            url = os.environ.get(
                "PGDATABASE_URL",
                "postgresql://postgres:localdev123@localhost:5433/ozon",
            )
            eng = create_engine(url)
            with eng.connect() as conn:
                conn.execute(text("SELECT 1"))
            _PG_UP = True
        except Exception:
            _PG_UP = False
    return _PG_UP


def _search(query, top_k=15):
    from utils.ozon_category_query import OzonCategoryQuery
    return OzonCategoryQuery().search_nodes(
        query, top_k=top_k, node_type="type", language="ZH_HANS")


def test_pillow_exact_query_top1_ge_09():
    """查询「装饰枕套」→ top1 similarity ≥ 0.9 且名含「枕套」（生产 Issue2 主锚）。"""
    if not _pg_up():
        pytest.skip("本地 PG 不可达（真实树用例）")
    results = _search("装饰枕套")
    assert results, "装饰枕套 在树里有 exact type 节点，不应返回空"
    top = results[0]
    assert top["similarity"] >= 0.9, \
        f"exact 查询 top1 sim 应 ≥0.9: {top['node_name']} sim={top['similarity']}"
    assert "枕套" in (top["node_name"] or "") or "枕套" in (top["full_path"] or ""), \
        f"top1 应命中枕套类节点: {top['full_path']}"


def test_pillow_compound_query_exact_node_boosted():
    """多 token 稀释场景：exact 节点装饰枕套不再被 token 数拖到入箱线以下。

    现状（修复前）：「装饰枕套沙发抱枕靠垫汽车床头」6 token 只中 2 → sim=0.3333，
    更长查询 0.25 < 0.3 被拦入采集箱（Issue2）。修复后 exact 节点（node_name 是
    整查询的前缀）sim 应 ≥ 0.8。
    注：top1 排序按 _score（本修复不改排序），故只断言装饰枕套行的 sim。
    """
    if not _pg_up():
        pytest.skip("本地 PG 不可达（真实树用例）")
    results = _search("装饰枕套沙发抱枕靠垫汽车床头")
    pillow = [r for r in results if r.get("type_id") == PILLOW_TYPE]
    assert pillow, f"装饰枕套 type 节点应进候选: {[r['node_name'] for r in results[:5]]}"
    sim = pillow[0]["similarity"]
    assert sim >= 0.8, \
        f"exact 节点被多 token 稀释后应加到 ≥0.8（互为前后缀档）: sim={sim}"


def test_single_token_tiering_unchanged_neiku():
    """「内裤」单 token 分档保持：exact(1.0) > 前缀(0.7) > 包含(0.6)——锁
    _score_token_hit 不被 exact 加分改动破坏。"""
    if not _pg_up():
        pytest.skip("本地 PG 不可达（真实树用例）")
    results = _search("内裤")
    assert results, "内裤 应有候选"
    sims = {r["node_name"]: r["similarity"] for r in results}
    top = results[0]
    assert top["node_name"] == "内裤" and top["similarity"] == 1.0, \
        f"exact 节点应 top1 sim=1.0: {top}"
    assert any(s == 0.7 for s in sims.values()), f"应有前缀档 0.7（如 内裤套装）: {sims}"
    assert any(s == 0.6 for s in sims.values()), f"应有可能包含档 0.6: {sims}"
    assert top["similarity"] == max(sims.values()), "exact 必须仍是最高档"


# ═══════════════════════════════════════════════════════════════════════
# 纯函数 / seam 打桩用例（零 DB，恒跑）
# ═══════════════════════════════════════════════════════════════════════
def _rows_like(rows, pattern, name_only=False):
    out = []
    for r in rows:
        name = str(r.get("node_name") or "")
        path = str(r.get("full_path") or "")
        hit = (pattern in name) if name_only else (pattern in name or pattern in path)
        if hit:
            out.append(dict(r))
    return out


def _no_db():
    return mock.patch(
        "utils.ozon_category_query.get_session",
        side_effect=AssertionError("seam 测试不应触碰真实 DB（get_session 被调）"),
    )


_PILLOW_NODE = {
    "description_category_id": PILLOW_DC, "type_id": PILLOW_TYPE,
    "node_name": "装饰枕套", "full_path": "住宅和花园 > 床上用品 > 装饰枕套",
    "top_level_category_name": "住宅和花园", "depth": 2,
}
_BRAKE_COVER = {
    "description_category_id": 17028756, "type_id": 971852452,
    "node_name": "刹车卡钳装饰罩", "full_path": "汽车用品 > 乘用车配件 > 刹车卡钳装饰罩",
    "top_level_category_name": "汽车用品", "depth": 2,
}


def test_exact_boost_pure_function_tiers():
    """_exact_query_similarity_boost 分档：exact 0.95 / 互为前后缀 0.8 / 无关 0。"""
    from utils.ozon_category_query import _exact_query_similarity_boost
    # exact（node_name == 整查询）
    assert _exact_query_similarity_boost(["装饰枕套", "床上用品"], "装饰枕套") == 0.95
    # full_path 末段 == 整查询 也算 exact
    assert _exact_query_similarity_boost(["其他", "装饰枕套"], " 装饰枕套 ") == 0.95
    # 前缀：查询 = 节点名 + 后缀
    assert _exact_query_similarity_boost(["装饰枕套"], "装饰枕套布艺") == 0.8
    # 前缀：节点名 = 查询 + 后缀（任一方向）
    assert _exact_query_similarity_boost(["装饰枕套布艺"], "装饰枕套") == 0.8
    # 完全相等走 exact 档而非前后缀档
    assert _exact_query_similarity_boost(["装饰枕套"], "装饰枕套") == 0.95
    # 无关 / 空值
    assert _exact_query_similarity_boost(["刹车卡钳装饰罩"], "装饰枕套") == 0.0
    assert _exact_query_similarity_boost([], "装饰枕套") == 0.0
    assert _exact_query_similarity_boost(["x"], "") == 0.0
    # 多名同时命中时 exact 优先于前后缀（不被首个前缀命中短路）
    assert _exact_query_similarity_boost(["装饰枕套x", "装饰枕套"], "装饰枕套") == 0.95


def test_seam_multi_token_prefix_boost():
    """seam 打桩（Issue2 根因）：多 token 查询「装饰枕套布艺」中 exact 节点
    sim 从 2/3=0.667 加到 ≥0.8（互为前后缀档）。"""
    from utils.ozon_category_query import OzonCategoryQuery
    q = OzonCategoryQuery()

    def fake_fetch(pattern, node_type, top_k, name_only=False):
        return _rows_like([_PILLOW_NODE, _BRAKE_COVER], pattern, name_only)[:top_k]

    with _no_db(), \
         mock.patch.object(OzonCategoryQuery, "_ensure_nodes_synced", return_value=None), \
         mock.patch.object(q, "_fetch_rows_like", create=True, side_effect=fake_fetch):
        results = q._search_jieba_like("装饰枕套布艺", top_k=10, node_type="type")

    pillow = [r for r in results if r.get("type_id") == PILLOW_TYPE]
    assert pillow, f"装饰枕套 应在结果中: {results}"
    assert pillow[0]["similarity"] >= 0.8, \
        f"前后缀 exact 加分应生效（修复前 0.6667）: {pillow[0]['similarity']}"


def test_seam_single_token_no_boost_regression():
    """单 token 查询不经 exact 加分：分档仍由 _score_token_hit 决定（0.7/0.6 不变）。"""
    from utils.ozon_category_query import _score_token_hit
    assert _score_token_hit("内裤", "内裤") == 1.0
    assert _score_token_hit("内裤套装", "内裤") == 0.7
    assert _score_token_hit("成人纸尿裤、内裤", "内裤") == 0.6
    assert _score_token_hit("袜子", "内裤") == 0.0
    assert _score_token_hit("", "内裤") == 0.0


# ═══════════════════════════════════════════════════════════════════════
# 阈值常量唯一化（Issue2 收口）
# ═══════════════════════════════════════════════════════════════════════
def test_min_conf_box_constant_and_graph_wiring():
    """MIN_CONF_BOX=0.3 存在于唯一事实源，且 graph.py 入箱闸引用常量而非裸字面量。"""
    from utils.ozon_category_query import MIN_CONF_BOX
    assert MIN_CONF_BOX == 0.3

    graph_path = os.path.join(os.path.dirname(__file__), "..", "src", "graphs", "graph.py")
    with open(graph_path, encoding="utf-8") as f:
        src = f.read()
    assert "match_conf < MIN_CONF_BOX" in src, \
        "graph.py route_after_assemble 应引用 MIN_CONF_BOX 常量"
    assert "match_conf < 0.3" not in src, \
        "graph.py 不应残留裸 0.3 入箱阈值字面量"


def test_adoption_thresholds_untouched():
    """assemble 的 MIN_SIM_BY_MATCHER 是「采纳阈值」（另一语义），本任务不动它。"""
    from graphs.nodes.assemble_ozon_product_node import MIN_SIM_BY_MATCHER
    assert MIN_SIM_BY_MATCHER == {"jieba": 0.5, "pg_trgm": 0.3, "ili": 0.5}


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
