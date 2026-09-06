"""姊妹词假满分治理回归（wave ④号缺陷 TDD）。

wave A2/A3/A7/A8 实证：jieba sim=token 命中占比——单 token 查询「头巾」子串命中
「三角头巾」= 1/1 = 1.0，与多 token 诚实查询（遮阳帽 0.29）同门槛竞争 → 姊妹词抢占；
低置信换池通道再把诚实池整池丢弃；parent 豁免 overlap 被假 1.0 白嫖。
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("GRSAI_API_KEY", "test-key")
os.environ.setdefault("LOG_FORMAT", "text")
os.environ.setdefault("LOG_LEVEL", "WARNING")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def test_score_token_hit_tiers():
    """分档：相等 1.0 / 前缀 0.7 / 包含 0.6 / 无关 0（与 score_residual_rows 同档）。"""
    from utils.ozon_category_query import _score_token_hit
    assert _score_token_hit("三角头巾", "头巾") == 0.6      # 包含（A2 实证路径）
    assert _score_token_hit("头巾", "头巾") == 1.0          # 精确相等仍满分
    assert _score_token_hit("头巾佩饰", "头巾") == 0.7       # 前缀
    assert _score_token_hit("遮阳帽", "头巾") == 0.0
    assert _score_token_hit("", "头巾") == 0.0
    assert _score_token_hit("头巾", "") == 0.0


def test_search_single_token_tiered_similarity():
    """端到端（真实 PG 树）：单 token jieba 搜索的包含命中 similarity < 1.0。"""
    from utils.ozon_category_query import get_category_query
    q = get_category_query()
    rows = q.search_nodes("头巾", top_k=5, node_type="type")
    assert rows, "树中应有头巾类节点（需本地 PG + 类目树）"
    exact = [r for r in rows if r["node_name"] == "头巾"]
    for r in rows:
        if r in exact:
            continue
        assert float(r["similarity"]) < 1.0, f"包含命中不得满分: {r}"


def test_low_conf_no_parent_repool():
    """低置信时不得再用 parent 词换池（A2/A3/A7/A8 错配路径）——源码静态断言。"""
    import inspect
    from graphs.nodes import assemble_ozon_product_node as asm
    src = inspect.getsource(asm)
    assert "上级类目词重搜+LLM选子类" not in src, "低置信换池通道必须移除"
    assert "上级类目词重搜: " not in src, "低置信换池通道必须移除"


def test_parent_fallback_confidence_capped():
    """0 候选 parent 通道仍保留（工业品救回），但置信度必须封顶走 Step 6.5 复核。"""
    import inspect
    from graphs.nodes import assemble_ozon_product_node as asm
    src = inspect.getsource(asm)
    anchor = src.index("✅ 上级类目回退采用")
    window = src[max(0, anchor - 600):anchor + 400]
    assert "min(_confidence_from_sim" in window, \
        "parent 通道采纳处 confidence 需封顶"


def test_matcher_log_label_honest():
    """:1307 日志标签用真实 matcher（原硬编码 pg_trgm 误导取证）。"""
    import inspect
    from graphs.nodes import assemble_ozon_product_node as asm
    src = inspect.getsource(asm)
    assert '✅ 类目匹配 (pg_trgm)' not in src, "硬编码 pg_trgm 标签必须改为动态 matcher"
