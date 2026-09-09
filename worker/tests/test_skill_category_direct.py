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


# ============================================================
# v0.69 T0.2: manual source（人工指定类目直传）权威接纳
# ============================================================

def test_manual_source_is_authoritative():
    """manual（CLI --category-id 人工指定直传，namespace=seller）权威级与 page 同。"""
    from graphs.nodes.assemble_ozon_product_node import _is_skill_authoritative
    assert _is_skill_authoritative("manual", "seller", None) is True


def test_search_kw_still_not_authoritative():
    """回归：search_kw（关键词模糊）恒非权威语义不变。"""
    from graphs.nodes.assemble_ozon_product_node import _is_skill_authoritative
    assert _is_skill_authoritative("search_kw", "seller", None) is False


def test_manual_widget_namespace_still_gated():
    """widget 命名空间保护不变：manual+widget 无 category_path 精配 → 非权威。"""
    from graphs.nodes.assemble_ozon_product_node import _is_skill_authoritative
    assert _is_skill_authoritative("manual", "widget", None) is False


def test_manual_sensitive_subtree_still_r1_vetoed():
    """R1 敏感闸覆盖 manual：manual 权威直通后，dc/tp 直采形状（_resolved_by_path=False，
    _resolve_skill_category 产出）落 18+ 敏感子树且源无敏感信号词 → 仍被 veto 阻断。
    权威豁免只给竞品 category_path 精配，manual 不享受。"""
    from graphs.nodes.assemble_ozon_product_node import _is_skill_authoritative, _r1_veto
    hit = {
        "description_category_id": 200001462, "type_id": 971363842,
        "full_path": "成人用品 > 成人的糖果点心 > 成人糖果",
        "node_name": "成人糖果",
        "similarity": 1.0, "confidence": 0.95,
        "namespace": "seller", "source": "manual",
        "_resolved_by_path": False,  # dc/tp 直采形状（非路径精配）
    }
    assert _is_skill_authoritative("manual", "seller", hit) is True
    assert _r1_veto(hit, "冬季保暖帽 太阳帽") is True, \
        "manual 权威不得绕过 R1 敏感 veto（18+ 防线）"
    # 对照：竞品路径精配（_resolved_by_path=True）才豁免
    hit_path = dict(hit, _resolved_by_path=True)
    assert _r1_veto(hit_path, "冬季保暖帽 太阳帽") is False


def test_manual_direct_channel_adopts_skill_layer():
    """manual dc/tp 直通后沿用 Skill 层记法：权威插首 + Skill 接管 L0 →
    match_layer="Skill"（category_match_meta 非 blocked）。"""
    from graphs.nodes.assemble_ozon_product_node import (
        _is_skill_authoritative, _place_skill_candidate, _skill_precedence_over_l0,
    )
    hit = {"description_category_id": 17028976, "type_id": 95701,
           "full_path": "服装 > 帽子 > 帽子", "source": "manual"}
    other = {"description_category_id": 1, "type_id": 2, "full_path": "其他 > x"}
    assert _is_skill_authoritative("manual", "seller", hit) is True
    placed = _place_skill_candidate([other], hit, True)
    assert placed[0] is hit, "manual 权威候选应插首（权威直采语义）"
    # _skill_precedence_over_l0(True) → assemble 置 match_layer="Skill"（非 blocked）
    assert _skill_precedence_over_l0(None, hit, "manual", "seller") is True


def test_manual_whitelist_in_authoritative_source_list():
    """源级锁定：_is_skill_authoritative 白名单含 manual（防回退）。"""
    import inspect
    from graphs.nodes import assemble_ozon_product_node as asm
    src = inspect.getsource(asm._is_skill_authoritative)
    assert '"manual"' in src, "权威 source 白名单必须含 manual"


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


# ── F-B02 延续：path-only page hint（discover 信封形态）路径精配 ──

def test_path_only_page_hint_resolved_by_breadcrumb():
    """discover 信封形态：source=page 无 dc/tp 只有面包屑路径 → 路径确定性精配
    直出权威结构（修复前被「无 dc 早退」整个丢弃，用户口径：Ozon 有类目直接复用）。"""
    from unittest import mock
    from graphs.nodes.assemble_ozon_product_node import _resolve_skill_category

    fake_node = {"description_category_id": 17028653, "type_id": 92147,
                 "node_name": "Термосы",
                 "full_path": "Дом и сад > Посуда > Термосы"}
    with mock.patch("utils.ozon_category_query.get_category_query") as gq:
        gq.return_value.get_node_by_full_path.return_value = fake_node
        hit = _resolve_skill_category({
            "source": "page", "namespace": "widget",
            "category_path": "Дом и сад > Посуда > Термосы",
            "breadcrumb_language": "RU",
        })
    assert hit is not None, "path-only page hint 应走路径精配，不再被早退丢弃"
    assert hit["description_category_id"] == 17028653
    assert hit["type_id"] == 92147
    assert hit["_resolved_by_path"] is True
    assert hit["source"] == "page"
    assert hit["confidence"] >= 0.9
    gq.return_value.get_node_by_full_path.assert_called_once_with(
        "Дом и сад > Посуда > Термосы")


def test_path_only_hint_miss_falls_back_to_none():
    """面包屑未命中树 → None（退回 pg_trgm/jieba 文本链），不造数。"""
    from unittest import mock
    from graphs.nodes.assemble_ozon_product_node import _resolve_skill_category

    with mock.patch("utils.ozon_category_query.get_category_query") as gq:
        gq.return_value.get_node_by_full_path.return_value = None
        hit = _resolve_skill_category({
            "source": "page", "namespace": "widget",
            "category_path": "Несуществующее > Дерево",
        })
    assert hit is None


def test_numeric_dc_still_takes_tree_validation_path():
    """有 dc/tp + 路径时仍优先路径精配（行为保持），且不破坏原有数字校验分支。"""
    from unittest import mock
    from graphs.nodes.assemble_ozon_product_node import _resolve_skill_category

    fake_node = {"description_category_id": 17028653, "type_id": 92147,
                 "node_name": "Термосы", "full_path": "Дом и сад > Посуда > Термосы"}
    with mock.patch("utils.ozon_category_query.get_category_query") as gq:
        gq.return_value.get_node_by_full_path.return_value = fake_node
        hit = _resolve_skill_category({
            "description_category_id": "17028653", "type_id": "92147",
            "category_path": "Дом и сад > Посуда > Термосы",
            "source": "page", "namespace": "widget",
        })
    assert hit is not None and hit["_resolved_by_path"] is True
