"""v0.27 方案B: 直采 Skill 类目消费回归测试。

背景：直采链路 poll_category 曾为 False(search_categories 从不执行)，
且 assemble 只在 follow_sell 分支消费 draft.ozon_category → 直采类目
全靠 pg_trgm 猜(32% 盲区实证)。v0.27 打开 poll_category + assemble
Step 0.5 校验采用 Skill 类目(Seller 空间 dc+tp 树中有效即用)。

运行(Docker + PG)：
    docker run --rm -v /Volumes/os/dev/ozon-worker/worker:/app -w /app \
      -e PYTHONPATH=/app/src -e APP_WORKSPACE_PATH=/app -e GRSAI_API_KEY= \
      -e PGDATABASE_URL="postgresql://postgres:localdev123@host.docker.internal:5433/ozon" \
      ozon-worker:latest python tests/test_skill_category_direct.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _resolve(cat):
    from graphs.nodes.assemble_ozon_product_node import _resolve_skill_category
    return _resolve_skill_category(cat)


def test_valid_skill_category_consumed():
    """树中有效的 dc/tp(棘轮扳手 17028653/92147) → 返回 l0_hit 供 assemble 采用。"""
    hit = _resolve({"description_category_id": "17028653", "type_id": "92147"})
    assert hit is not None, "有效类目应被采用"
    assert hit["description_category_id"] == 17028653
    assert hit["type_id"] == 92147
    assert hit["confidence"] >= 0.9
    assert hit["full_path"], "应带 ZH_HANS 路径"


def test_brand_page_id_rejected():
    """品牌页 ID(甩脂机 101029485,树中不存在) → None,退回 pg_trgm。"""
    hit = _resolve({"description_category_id": "101029485", "type_id": "101029485"})
    assert hit is None, "品牌页 ID 必须被拒绝(防甩脂机污染重演)"


def test_wrong_type_rejected():
    """dc 有效但 tp 不匹配(dc 存在而 tp 组合不存在) → None。"""
    hit = _resolve({"description_category_id": "17028653", "type_id": "99999999"})
    assert hit is None, "tp 不匹配应拒绝"


def test_non_numeric_rejected():
    """文本值(Widget 面包屑路径) → None(不猜)。"""
    hit = _resolve({"description_category_id": "Тарелки", "type_id": "92532"})
    assert hit is None, "文本类目名不适用此路径(交给 pg_trgm)"


def test_empty_rejected():
    assert _resolve({}) is None
    assert _resolve(None) is None


if __name__ == "__main__":
    import traceback
    failed = total = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            total += 1
            try:
                fn()
                print(f"PASS {name}")
            except Exception:
                failed += 1
                print(f"FAIL {name}")
                traceback.print_exc()
    print(f"\n{total - failed}/{total} passed")
    sys.exit(1 if failed else 0)
