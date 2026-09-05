"""v0.66.0 L0 学习表复活测试（写侧 + 负反馈 + 读侧信任分档）。

覆盖：
- c1 写侧: LearningRecordInput 补 source/envelope/product_id（Task0 实证 langgraph 按节点
  Input schema 过滤 channel，未声明字段节点不可见）；is_follow 守卫（envelope extensions +
  draft.ozon_product_id 兜底）；W1 source_category 三源兜底。
- W3/c9/c10: add_category_mapping 原子 upsert（冲突累加而非 SELECT→INSERT 竞态炸库）+
  mark_category_mapping_failed 负反馈语义（learned 3 次下线 / curated 只 +1）。
- Task2: validation_retry_loop final_result 每任务一次 declined 负反馈挂点；R4 重配成功
  跳离旧行降权；读排序（is_active DESC, (success_count-fail_count) DESC）。
- Task3: _l0_authoritative 分档 / _l0_guard_action 守卫决策（curated 不在 top5 也权威采用、
  learned≥2 同、success==1 不一致 → arbitrate）/ _l0_weak_arbitrate 弱档 LLM 仲裁。

DB 依赖：mark/fail/upsert 端到端断言需本地 PG（5433）；纯逻辑（guard/仲裁）不依赖。
"""
import os
import sys
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault(
    "PGDATABASE_URL", "postgresql://postgres:localdev123@localhost:5433/ozon"
)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# ⚠️ 提前导入 assemble 模块：本文件有 mock.patch("utils.ozon_category_query.sensitive_*")
# 的 R4 测试——若 assemble 模块在此 mock 激活期间才首次导入，其模块级
# sensitive_adoption_blocked 绑定会被 mock 永久污染（影响 test_r1_veto_applies_to_l0）。
import graphs.nodes.assemble_ozon_product_node

# ═══════════════════════════════════════════════════════════════
# 通用夹具
# ═══════════════════════════════════════════════════════════════

_NODE_LEAF = "震动棒"
_NODE_PATH = "成人用品 > 女用器具 > 震动棒"
_TEST_DC = "17028959"
_TEST_TP = 96513


class _FakeRow:
    """fetchone 恒返回 truthy 行（cat_zh/cat_ru/exists 校验全放行）。"""

    def fetchone(self):
        return ("成人用品 > 女用器具 > 震动棒",)


class _FakeSession:
    """storage.database.db.get_session 的替身（with get_session() as s 用法）。"""

    def execute(self, *a, **k):
        return _FakeRow()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _make_state(draft=None, envelope=None, source=None, product_id="",
                moderation_status="approved", status="approved",
                upload_status="success"):
    return SimpleNamespace(
        description_category_id=_TEST_DC,
        type_id=_TEST_TP,
        moderation_status=moderation_status,
        status=status,
        upload_status=upload_status,
        ozon_upload_success=False,
        product_id=product_id,
        final_attributes=[],
        attributes_schema=[],
        fetch_back_result={},
        draft=draft or {},
        envelope=envelope or {},
        source=source,
    )


def _run_learning_record_node(state):
    from graphs.nodes.learning_record_node import learning_record_node

    runtime = SimpleNamespace(context=SimpleNamespace())
    with mock.patch("storage.database.db.get_session", return_value=_FakeSession()), \
         mock.patch("graphs.nodes.learning_record_node.LocalDBManager") as mock_db:
        mock_db.return_value = mock_db
        learning_record_node(state, SimpleNamespace(), runtime)
    return mock_db


def _leaf_draft(path=_NODE_PATH):
    return {"title": "测试", "source_category": path}


# ═══════════════════════════════════════════════════════════════
# c1: LearningRecordInput schema（Task0 结论 = langgraph 过滤生效，需补字段）
# ═══════════════════════════════════════════════════════════════

def test_learning_record_input_declares_l0_fields():
    """LearningRecordInput 必须声明 source/envelope/product_id（类型对齐 GlobalState）。"""
    from graphs.state import LearningRecordInput, GlobalState

    fields = LearningRecordInput.model_fields
    assert "source" in fields
    assert "envelope" in fields
    assert "product_id" in fields
    assert fields["source"].annotation == GlobalState.model_fields["source"].annotation
    assert fields["product_id"].annotation == GlobalState.model_fields["product_id"].annotation


# ═══════════════════════════════════════════════════════════════
# 写侧: is_follow 守卫（跟卖不入学习表）
# ═══════════════════════════════════════════════════════════════

def test_follow_via_envelope_skips_category_mapping():
    """envelope.extensions.follow_sell=True → 跟卖跳过 category_mapping（图搜噪音）。"""
    state = _make_state(draft=_leaf_draft(),
                        envelope={"extensions": {"follow_sell": True}})
    mock_db = _run_learning_record_node(state)
    mock_db.add_category_mapping.assert_not_called()


def test_follow_via_follow_type_skips_category_mapping():
    """envelope.extensions.follow_type（hand/api）也算跟卖。"""
    state = _make_state(draft=_leaf_draft(),
                        envelope={"extensions": {"follow_type": "hand"}})
    mock_db = _run_learning_record_node(state)
    mock_db.add_category_mapping.assert_not_called()


def test_follow_fallback_from_draft_ozon_product_id():
    """envelope 不可见（空）时，draft.ozon_product_id 存在 → 推导跟卖 → 不写。"""
    state = _make_state(draft={**_leaf_draft(), "ozon_product_id": "1234567"})
    mock_db = _run_learning_record_node(state)
    mock_db.add_category_mapping.assert_not_called()


def test_non_follow_source_category_written_learned_approved():
    """普通 1688 商品 + source_category → 写 category_mapping（source=learned_approved）。"""
    state = _make_state(draft=_leaf_draft())
    mock_db = _run_learning_record_node(state)
    mock_db.add_category_mapping.assert_called_once()
    assert mock_db.add_category_mapping.call_args.kwargs["source"] == "learned_approved"
    assert mock_db.add_category_mapping.call_args.kwargs["source_category_leaf"] == _NODE_LEAF


# ═══════════════════════════════════════════════════════════════
# W1: source_category 三源兜底
# ═══════════════════════════════════════════════════════════════

def test_w1_source_fallback_from_state_source():
    """draft 无 source_category → 兜底 state.source.source_category_path。"""
    state = _make_state(
        draft={"title": "测试"},
        source={"source_category_path": "母婴用品 > 玩具 > 儿童滑梯"},
    )
    mock_db = _run_learning_record_node(state)
    mock_db.add_category_mapping.assert_called_once()
    assert mock_db.add_category_mapping.call_args.kwargs["source_category_leaf"] == "儿童滑梯"


def test_w1_source_fallback_from_draft_source_category_path():
    """draft.source_category 缺失但 source_category_path 在 → 兜底第二个 key。"""
    state = _make_state(
        draft={"title": "测试", "source_category_path": "母婴用品 > 玩具 > 儿童滑梯"},
    )
    mock_db = _run_learning_record_node(state)
    mock_db.add_category_mapping.assert_called_once()
    assert mock_db.add_category_mapping.call_args.kwargs["source_category_leaf"] == "儿童滑梯"


def test_no_source_anywhere_skips_category_mapping():
    """draft/source 均无 1688 类目信息 → 不写（W2 跳过原因日志）。"""
    state = _make_state(draft={"title": "测试"})
    mock_db = _run_learning_record_node(state)
    mock_db.add_category_mapping.assert_not_called()


# ═══════════════════════════════════════════════════════════════
# Task2: declined 负反馈挂点（final_result 每任务一次）
# ═══════════════════════════════════════════════════════════════

def _retry_state_failed_category(**over):
    from graphs.validation_retry_loop import ValidationRetryLoopState

    base = dict(
        error_code="DESCRIPTION_DECLINE",
        attribute_id=8229,
        error_message="Тип не соответствует товару",
        upload_status="failed",
        description_category_id=_TEST_DC,
        type_id=str(_TEST_TP),
        draft={**_leaf_draft(), "source_category_id": 90210},
    )
    base.update(over)
    return ValidationRetryLoopState(**base)


def test_final_result_marks_category_failed_once():
    """任务终态 failed + 类目错特征 → final_result 触发 mark_category_mapping_failed。"""
    from graphs.validation_retry_loop import final_result

    state = _retry_state_failed_category()
    with mock.patch("utils.local_db_manager.LocalDBManager") as mock_db:
        mock_db.return_value.mark_category_mapping_failed.return_value = 1
        final_result(state)
    mock_db.return_value.mark_category_mapping_failed.assert_called_once_with(
        source_category_id=90210,
        source_category_leaf=_NODE_LEAF,
        description_category_id=int(_TEST_DC),
        type_id=_TEST_TP,
    )


def test_final_result_success_does_not_mark():
    """修复成功（upload_status=success）→ 不触发负反馈。"""
    from graphs.validation_retry_loop import final_result

    state = _retry_state_failed_category(upload_status="success", error_message="")
    with mock.patch("utils.local_db_manager.LocalDBManager") as mock_db:
        final_result(state)
    mock_db.return_value.mark_category_mapping_failed.assert_not_called()


def test_final_result_non_category_failure_does_not_mark():
    """终态 failed 但非类目错（描述/图片错）→ 不触发负反馈（不误伤）。"""
    from graphs.validation_retry_loop import final_result

    state = _retry_state_failed_category(
        error_code="DESCRIPTION_DECLINE", attribute_id=0,
        error_message="описание содержит латиницу",
        draft={"title": "测试"},
    )
    with mock.patch("utils.local_db_manager.LocalDBManager") as mock_db:
        final_result(state)
    mock_db.return_value.mark_category_mapping_failed.assert_not_called()


def test_r4_recategorize_success_downgrades_old_row():
    """R4 整卡重配成功 → 旧 (leaf, old_dc, old_tp) 行补 mark_category_mapping_failed。"""
    from graphs.validation_retry_loop import _try_recategorize_card, ValidationRetryLoopState

    OLD_DC, OLD_TP = 17028959, 96513
    NEW_DC, NEW_TP = 17028653, 92147
    cap = {}

    class _FakeQuery:
        def search_nodes(self, *a, **k):
            return [{"description_category_id": NEW_DC, "type_id": NEW_TP,
                     "full_path": "儿童玩具 > 滑梯", "node_name": "滑梯",
                     "similarity": 0.9}]

    def _fake_rebuild(new_dc, new_type, **kw):
        cap["new_dc"], cap["new_type"] = new_dc, new_type
        return {"items": [{"description_category_id": new_dc, "type_id": new_type,
                           "name": "Горка детская", "attributes": []}],
                "final_attributes": [{"attribute_id": 4180, "value": "Горка",
                                      "dictionary_value_id": 0}],
                "llm_name": "Горка детская", "attr_list": [], "dict_lookup": {}}

    state = ValidationRetryLoopState(
        token="t", ozon_client_id="c", ozon_api_key="k",
        description_category_id=str(OLD_DC), type_id=str(OLD_TP),
        draft={**_leaf_draft(_NODE_PATH), "source_category_id": 90210},
        product_name="Горка",
        ozon_payload={"items": [{"name": "Горка", "description_category_id": OLD_DC,
                                 "type_id": OLD_TP, "price": "1990", "currency_code": "RUB",
                                 "depth": 10, "width": 20, "height": 30, "weight": 200,
                                 "images": []}]},
    )
    with mock.patch("utils.ozon_category_query.get_category_query",
                    return_value=_FakeQuery()), \
         mock.patch("utils.ozon_category_query.sensitive_candidate_filter",
                    side_effect=lambda c, s: c), \
         mock.patch("utils.ozon_category_query.sensitive_adoption_blocked",
                    return_value=False), \
         mock.patch("graphs.nodes.assemble_ozon_product_node._rebuild_for_new_category",
                    side_effect=_fake_rebuild), \
         mock.patch("utils.local_db_manager.LocalDBManager") as mock_db:
        ok = _try_recategorize_card(state)
        assert ok is True
        assert cap == {"new_dc": NEW_DC, "new_type": NEW_TP}
        mock_db.return_value.mark_category_mapping_failed.assert_called_once_with(
            source_category_id=90210,
            source_category_leaf=_NODE_LEAF,
            description_category_id=OLD_DC,
            type_id=OLD_TP,
        )


# ═══════════════════════════════════════════════════════════════
# Task3: 读侧信任分档
# ═══════════════════════════════════════════════════════════════

def _l0(**over):
    base = {"description_category_id": 17028653, "type_id": 92147,
            "category_path": "儿童玩具 > 滑梯", "source": "learned_approved",
            "success_count": 1, "fail_count": 0}
    base.update(over)
    return base


def _text_cands(*pairs):
    return [{"description_category_id": dc, "type_id": tp,
             "full_path": f"文本路径 {dc}", "similarity": 0.8}
            for dc, tp in pairs]


def test_l0_authoritative_tiers():
    """curated / learned success>=2 → 权威档；success==1 弱档；其它 source 非权威。"""
    from graphs.nodes.assemble_ozon_product_node import _l0_authoritative

    assert _l0_authoritative(_l0(source="curated", success_count=5)) is True
    assert _l0_authoritative(_l0(source="learned_approved", success_count=2)) is True
    assert _l0_authoritative(_l0(source="learned_approved", success_count=1)) is False
    assert _l0_authoritative(_l0(source="llm", success_count=9)) is False
    assert _l0_authoritative(None) is False


def test_guard_authoritative_curated_not_in_top5_kept():
    """curated 行不在文本 top5 → authoritative_keep（不再被一致性丢弃）。"""
    from graphs.nodes.assemble_ozon_product_node import _l0_guard_action

    l0 = _l0(source="curated", success_count=5)
    cands = _text_cands((111, 222), (333, 444))  # top5 不含 L0 的 dc/tp
    assert _l0_consistent_check(l0, cands) is False
    assert _l0_guard_action(l0, "L1", cands) == "authoritative_keep"


def test_guard_learned_success2_not_in_top5_kept():
    """learned success>=2 不在文本 top5 → authoritative_keep（两次 approved 已可信）。"""
    from graphs.nodes.assemble_ozon_product_node import _l0_guard_action

    l0 = _l0(source="learned_approved", success_count=2)
    cands = _text_cands((111, 222), (333, 444))
    assert _l0_guard_action(l0, "L1", cands) == "authoritative_keep"


def test_guard_weak_success1_inconsistent_arbitrates():
    """learned success==1 且不在文本 top5 → arbitrate（不静默丢弃）。"""
    from graphs.nodes.assemble_ozon_product_node import _l0_guard_action

    l0 = _l0(source="learned_approved", success_count=1)
    cands = _text_cands((111, 222), (333, 444))
    assert _l0_guard_action(l0, "L1", cands) == "arbitrate"


def test_guard_consistent_kept():
    """L0 命中且在文本 top5 → consistent_keep（弱档也直接保留）。"""
    from graphs.nodes.assemble_ozon_product_node import _l0_guard_action

    dc, tp = 17028653, 92147
    l0 = _l0(source="learned_approved", success_count=1, description_category_id=dc, type_id=tp)
    cands = _text_cands((dc, tp), (333, 444))
    assert _l0_guard_action(l0, "L1", cands) == "consistent_keep"


def test_guard_skill_exempt():
    """match_layer=Skill（权威 Skill 直采）一贯豁免一致性丢弃。"""
    from graphs.nodes.assemble_ozon_product_node import _l0_guard_action

    l0 = _l0(source="page", success_count=0)
    assert _l0_guard_action(l0, "Skill", _text_cands((111, 222))) == "skill"
    assert _l0_guard_action(None, "L1", []) == "no_l0"


def test_skill_precedence_over_l0_conflict_prefers_skill():
    """P2-3: 权威 Skill 与聚合 L0 dc/tp 冲突 → Skill 接管（优先真实卡类目）。"""
    from graphs.nodes.assemble_ozon_product_node import _skill_precedence_over_l0

    l0_aggregate = _l0(source="curated", success_count=5)  # dc=17028653/92147
    skill_hit = {"description_category_id": 17028959, "type_id": 96513,
                 "full_path": "成人用品 > 女用器具 > 震动棒", "source": "page"}
    assert _skill_precedence_over_l0(l0_aggregate, skill_hit, "page", "") is True
    # 无 l0（skill 兜底直采）也应接管
    assert _skill_precedence_over_l0(None, skill_hit, "what_to_sell", "") is True


def test_skill_precedence_over_l0_same_dc_keeps_l0():
    """P2-3: Skill 与 L0 dc/tp 一致 → 不覆盖（保留 L0 provenance，无行为差异）。"""
    from graphs.nodes.assemble_ozon_product_node import _skill_precedence_over_l0

    dc, tp = 17028653, 92147
    l0_aggregate = _l0(source="learned_approved", success_count=2,
                       description_category_id=dc, type_id=tp)
    skill_hit = {"description_category_id": dc, "type_id": tp, "source": "mapping"}
    assert _skill_precedence_over_l0(l0_aggregate, skill_hit, "mapping", "") is False
    # 无 skill 可接管
    assert _skill_precedence_over_l0(l0_aggregate, None, "page", "") is False


def _l0_consistent_check(l0, cands):
    from graphs.nodes.assemble_ozon_product_node import _l0_consistent
    return _l0_consistent(l0, cands)


class _FakeQueryNode:
    """query.get_node 返回 L0 对应树节点（供弱档仲裁并入候选池）。"""

    def get_node(self, dc, tp):
        return {"description_category_id": dc, "type_id": tp,
                "full_path": "儿童玩具 > 滑梯 > 儿童滑梯", "node_name": "儿童滑梯"}


def test_weak_arbitrate_llm_confirms_l0_keeps():
    """弱档不一致 → LLM 仲裁在并入 L0 的池中仍选 L0 → keep_l0。"""
    from graphs.nodes.assemble_ozon_product_node import _l0_weak_arbitrate

    l0 = _l0(source="learned_approved", success_count=1)
    cands = _text_cands((111, 222), (333, 444))
    state = SimpleNamespace(token="t")

    def _fake_llm(pool, *a, **k):
        # 模拟 LLM 选中被并入的 L0 节点（识别 _l0_dc 标记）
        return next(c for c in pool if c.get("_l0_dc") == 17028653)

    with mock.patch("graphs.nodes.assemble_ozon_product_node._llm_rank_categories",
                    side_effect=_fake_llm):
        assert _l0_weak_arbitrate(l0, cands, "儿童滑梯", {}, state, _FakeQueryNode()) == "keep_l0"


def test_weak_arbitrate_llm_picks_text_drops():
    """LLM 仲裁选文本候选（未确认 L0）→ drop（走文本保守路径）。"""
    from graphs.nodes.assemble_ozon_product_node import _l0_weak_arbitrate

    l0 = _l0(source="learned_approved", success_count=1)
    cands = _text_cands((111, 222), (333, 444))
    state = SimpleNamespace(token="t")

    def _fake_llm(pool, *a, **k):
        return pool[0]  # 文本 top1

    with mock.patch("graphs.nodes.assemble_ozon_product_node._llm_rank_categories",
                    side_effect=_fake_llm):
        assert _l0_weak_arbitrate(l0, cands, "儿童滑梯", {}, state, _FakeQueryNode()) == "drop"


def test_weak_arbitrate_llm_fails_drops():
    """LLM 无返回/异常 → drop（保守，等价旧 v0.21 丢弃）。"""
    from graphs.nodes.assemble_ozon_product_node import _l0_weak_arbitrate

    l0 = _l0(source="learned_approved", success_count=1)
    cands = _text_cands((111, 222), (333, 444))
    state = SimpleNamespace(token="t")
    with mock.patch("graphs.nodes.assemble_ozon_product_node._llm_rank_categories",
                    return_value=None):
        assert _l0_weak_arbitrate(l0, cands, "儿童滑梯", {}, state, _FakeQueryNode()) == "drop"


def test_r1_veto_applies_to_l0():
    """R1 敏感闸不按 match_layer 豁免——权威 L0 落敏感大类且源无敏感信号 → veto。"""
    from graphs.nodes.assemble_ozon_product_node import _r1_veto

    # 敏感大类路径（成人用品 > 成人的糖果点心）+ 源无敏感信号 → veto（保持 v0.65.1 P1-2）
    res = {"description_category_id": 200001462,
           "category_path": "成人用品 > 成人的糖果点心", "type_id": 1}
    assert _r1_veto(res, "冬季保暖帽 渔夫帽") is True
    # 源含敏感信号词（情趣用品）→ 不 veto
    assert _r1_veto(res, "情趣用品 飞机杯") is False


# ═══════════════════════════════════════════════════════════════
# 真实 PG 端到端（无 DB 时 skip）：原子 upsert / 负反馈 / 读排序
# ═══════════════════════════════════════════════════════════════

def _pg_available():
    try:
        from sqlalchemy import create_engine, text
        eng = create_engine(os.environ["PGDATABASE_URL"])
        with eng.connect() as c:
            c.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


_PG_OK = _pg_available()

_MARK = "L0T066叶"


def _cleanup_marker():
    if not _PG_OK:
        return
    from sqlalchemy import create_engine, text
    eng = create_engine(os.environ["PGDATABASE_URL"])
    with eng.connect() as c:
        c.execute(text("DELETE FROM category_mapping WHERE source_category_leaf LIKE :p"),
                  {"p": f"{_MARK}%"})
        c.commit()


def test_db_atomic_upsert_and_fail_negative_feedback():
    """add_category_mapping 原子 upsert + mark_category_mapping_failed 语义（真实 PG）。"""
    if not _PG_OK:
        import pytest
        pytest.skip("本地 PG 不可用")
    _cleanup_marker()
    from utils.local_db_manager import LocalDBManager
    from sqlalchemy import create_engine, text

    eng = create_engine(os.environ["PGDATABASE_URL"])
    leaf = f"{_MARK}原子"
    ldb = LocalDBManager()
    try:
        # 首插 + 冲突更新（同 leaf+dc+tp）→ success_count 累加、path/conf 刷新、is_active=True
        ldb.add_category_mapping(leaf, 902001, 9020001, "A>B>叶",
                                 source="learned_approved", source_category_id=90211,
                                 category_path_zh="Z初", confidence=0.8)
        ldb.add_category_mapping(leaf, 902001, 9020001, "A>B>叶2",
                                 source="learned_approved", source_category_id=90211,
                                 category_path_zh="Z新", confidence=0.95)
        with eng.connect() as c:
            row = c.execute(text(
                "SELECT success_count, fail_count, is_active, confidence, category_path_zh, "
                "source, source_category_id FROM category_mapping "
                "WHERE source_category_leaf=:l"), {"l": leaf}).fetchone()
        assert row is not None
        assert row[0] == 2, f"success_count 应累加到 2: {row}"
        assert row[4] == "Z新", f"path 应刷新为新值: {row}"
        assert abs(row[3] - 0.95) < 1e-6, f"confidence 应取最大: {row}"
        assert row[6] == 90211
        # 无 source_category_id 的冲突 → coalesce 保留存量 id
        ldb.add_category_mapping(leaf, 902001, 9020001, source="learned_approved")
        with eng.connect() as c:
            keep = c.execute(text(
                "SELECT success_count, source_category_id FROM category_mapping "
                "WHERE source_category_leaf=:l"), {"l": leaf}).fetchone()
        assert keep[0] == 3 and keep[1] == 90211
        # learned 负反馈 3 次 → is_active=False
        for _ in range(3):
            ldb.mark_category_mapping_failed(source_category_id=90211,
                                             description_category_id=902001,
                                             type_id=9020001)
        with eng.connect() as c:
            row2 = c.execute(text(
                "SELECT success_count, fail_count, is_active FROM category_mapping "
                "WHERE source_category_leaf=:l"), {"l": leaf}).fetchone()
        assert row2[2] is False, f"learned 3 次失败应下线: {row2}"
    finally:
        _cleanup_marker()


def test_db_curated_never_auto_inactive():
    """curated 行只 +1 不自动 inactive（人工种子信任）。"""
    if not _PG_OK:
        import pytest
        pytest.skip("本地 PG 不可用")
    _cleanup_marker()
    from utils.local_db_manager import LocalDBManager
    from sqlalchemy import create_engine, text

    eng = create_engine(os.environ["PGDATABASE_URL"])
    leaf = f"{_MARK}cur"
    ldb = LocalDBManager()
    try:
        ldb.add_category_mapping(leaf, 902002, 9020002, source="curated",
                                 source_category_id=90212)
        for _ in range(4):
            ldb.mark_category_mapping_failed(source_category_id=90212,
                                             description_category_id=902002,
                                             type_id=9020002)
        with eng.connect() as c:
            row = c.execute(text(
                "SELECT source, fail_count, is_active FROM category_mapping "
                "WHERE source_category_leaf=:l"), {"l": leaf}).fetchone()
        assert row[0] == "curated"
        assert row[1] == 4, f"curated 失败累计: {row}"
        assert row[2] is True, f"curated 不应自动下线: {row}"
    finally:
        _cleanup_marker()


def test_db_curated_source_preserved_after_learned_conflict():
    """P1-1: curated 行被 learned_approved 同 key 冲突写 → source 仍 curated 且 active。"""
    if not _PG_OK:
        import pytest
        pytest.skip("本地 PG 不可用")
    _cleanup_marker()
    from utils.local_db_manager import LocalDBManager
    from sqlalchemy import create_engine, text

    eng = create_engine(os.environ["PGDATABASE_URL"])
    leaf = f"{_MARK}p11"
    ldb = LocalDBManager()
    try:
        ldb.add_category_mapping(leaf, 902010, 9020010, source="curated",
                                 source_category_id=90230)
        # 同 key 的 learned_approved 冲突写（模拟 approved 学习命中 curated 种子）
        ldb.add_category_mapping(leaf, 902010, 9020010, source="learned_approved",
                                 source_category_id=90230)
        with eng.connect() as c:
            row = c.execute(text(
                "SELECT source, success_count, is_active FROM category_mapping "
                "WHERE source_category_leaf=:l"), {"l": leaf}).fetchone()
        assert row[0] == "curated", f"learned 写不得把 curated 行降级: {row}"
        assert row[1] == 2, f"success_count 仍应累加: {row}"
        assert row[2] is True, f"curated 行应保持 active: {row}"
        # 反向：非 curated 存量被 curated 写冲突 → 提为 curated（种子恢复）
        ldb.add_category_mapping(f"{_MARK}p11b", 902011, 9020011, source="learned_approved",
                                 source_category_id=90231)
        ldb.add_category_mapping(f"{_MARK}p11b", 902011, 9020011, source="curated",
                                 source_category_id=90231)
        with eng.connect() as c:
            row2 = c.execute(text(
                "SELECT source FROM category_mapping WHERE source_category_leaf=:l"),
                {"l": f"{_MARK}p11b"}).fetchone()
        assert row2[0] == "curated", f"curated 写应能恢复被污染行的 source: {row2}"
    finally:
        _cleanup_marker()


def test_db_read_order_prefers_higher_net():
    """读排序 (success_count - fail_count) DESC——负反馈降权后排名靠后。"""
    if not _PG_OK:
        import pytest
        pytest.skip("本地 PG 不可用")
    _cleanup_marker()
    from utils.local_db_manager import LocalDBManager
    from sqlalchemy import create_engine, text

    eng = create_engine(os.environ["PGDATABASE_URL"])
    leaf = f"{_MARK}序"
    ldb = LocalDBManager()
    try:
        # A: 1 succ 0 fail → net 1；B: curated(succ=5 种子) 0 fail；C: 2 succ 3 fail → net -1
        ldb.add_category_mapping(leaf, 902003, 9020003, source="learned_approved",
                                 source_category_id=90221)
        ldb.add_category_mapping(leaf, 902004, 9020004, source="learned_approved",
                                 source_category_id=90222)
        for _ in range(3):
            ldb.mark_category_mapping_failed(source_category_id=90222,
                                             description_category_id=902004,
                                             type_id=9020004)
        ldb.mark_category_mapping_failed(source_category_id=90221,
                                         description_category_id=902003,
                                         type_id=9020003)
        ldb.add_category_mapping(leaf, 902005, 9020005, source="curated",
                                 source_category_id=90223)
        with eng.connect() as c:
            c.execute(text(
                "UPDATE category_mapping SET success_count=5 WHERE "
                "source_category_leaf=:l AND description_category_id=902005"),
                {"l": leaf})
            c.commit()
        rows = ldb.get_category_mapping_by_leaf(leaf)
        # curated 行 active + net=5 → 应第一；A(net 0)、C(net -1) 随后
        nets = [(int(r["source"] == "curated"), r["success_count"] - r["fail_count"],
                 r["description_category_id"]) for r in rows]
        assert nets[0][0] == 1 and nets[0][2] == 902005, f"curated 应排第一: {nets}"
        assert [n[1] for n in nets] == sorted(
            [n[1] for n in nets], reverse=True), f"应按净分降序: {nets}"
        # 下线行（learned fail>=3 已 inactive）不应出现（读过滤 is_active）
        assert not any(r["description_category_id"] == 902004 for r in rows), \
            f"inactive 行不应出现在读取结果: {rows}"
    finally:
        _cleanup_marker()
