"""v0.66.1 discover 类目学习闭环测试（worker 写侧）。

覆盖：
- 断点1: 跟卖豁免判定细化 —— discover 变体（extensions.follow_sell=True 无 follow_type）
  approved → **仍写** category_mapping（不再整体跳过，discover 主流场景开始学习）；
  真跟卖（follow_type=hand / 旧信封 draft.ozon_product_id 单独出现）→ 写但 confidence 压 0.6
  （读侧 conf>=0.6 门槛线 + succ==1 必走 LLM 仲裁，不盲信图搜）。
- 断点2: match_layer 透传防自证 —— category_match_meta.match_layer=L0（本次 dc 来自学习表）
  → add_category_mapping 不被调（学习表不给自己加证据）；Skill→0.9 / L1→0.7 / 缺省 0.85。
- 断点3: 写侧语义预检 —— 1688 leaf 与 Ozon 类目 ZH 路径零重叠 → 拒写（疑似货源错配）；
  有重叠 → 写。
- 增强5: extensions.match_evidence 不可信（method!=aibuy 且 conf<0.3）→ conf 压 0.6。

纯 mock（get_session + LocalDBManager），无需 PG/GPU，风格对齐 test_l0_revive_v066.py。
"""
import os
import sys
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault(
    "PGDATABASE_URL", "postgresql://postgres:localdev123@localhost:5433/ozon"
)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

_TEST_DC = "17028959"
_TEST_TP = 96513


class _Row:
    """fetchone 返回固定 tuple（cat_zh/cat_ru/exists 校验按 SQL 分流）。"""

    def __init__(self, value):
        self._value = value

    def fetchone(self):
        return self._value


class _FakeSession:
    """storage.database.db.get_session 的替身。

    execute 按 SQL 内容分流：ZH_HANS 查询 → zh_path（=预检用的 cat_zh）；
    RU 查询 / 存在性查询 → truthy 占位（mapping_valid 放行）。
    """

    def __init__(self, zh_path):
        self._zh = zh_path

    def execute(self, sql, *a, **k):
        if "ZH_HANS" in str(sql):
            return _Row((self._zh,))
        return _Row(("RU>placeholder",))

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _make_state(draft=None, envelope=None, source=None, product_id="",
                moderation_status="approved", status="approved",
                upload_status="success", category_match_meta=None,
                zh_path="服饰配件 > 手套",
                description_category_id=_TEST_DC, type_id=_TEST_TP):
    """构造学习节点 state。默认 zh_path 与 _DEFAULT_LEAF(手套) 语义一致，预检放行。"""
    if category_match_meta is None:
        category_match_meta = {}
    return SimpleNamespace(
        description_category_id=description_category_id,
        type_id=type_id,
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
        category_match_meta=category_match_meta,
        user_id="", ozon_client_id="", ozon_api_key="", pricing_info={},
    )


def _run_learning_record_node(state, zh_path="服饰配件 > 手套"):
    from graphs.nodes.learning_record_node import learning_record_node

    runtime = SimpleNamespace(context=SimpleNamespace())
    with mock.patch("storage.database.db.get_session",
                    return_value=_FakeSession(zh_path)), \
         mock.patch("graphs.nodes.learning_record_node.LocalDBManager") as mock_db:
        mock_db.return_value = mock_db
        learning_record_node(state, SimpleNamespace(), runtime)
    return mock_db


def _leaf_draft(path="服饰配件 > 手套"):
    return {"title": "测试", "source_category": path}


# ═══════════════════════════════════════════════════════════════
# 断点1: discover 变体 approved → 写 mapping；真跟卖 → 写但 conf 压 0.6
# ═══════════════════════════════════════════════════════════════

def test_discover_variant_approved_writes_mapping():
    """discover 变体（follow_sell=True 但无 follow_type，skill discover 信封不设）→
    不算真跟卖 → approved 正常写 mapping（断点1 修复前 is_follow 整体跳过，discover 不学习）。"""
    state = _make_state(draft=_leaf_draft(),
                        envelope={"extensions": {"follow_sell": True}})
    mock_db = _run_learning_record_node(state)
    mock_db.add_category_mapping.assert_called_once()
    assert mock_db.add_category_mapping.call_args.kwargs["confidence"] == 0.85


def test_true_follow_writes_conf_060():
    """真跟卖（follow_sell=True + follow_type=hand）→ 写但 confidence 恒 0.6（弱档）。"""
    state = _make_state(draft=_leaf_draft(),
                        envelope={"extensions": {"follow_sell": True, "follow_type": "hand"}})
    mock_db = _run_learning_record_node(state)
    mock_db.add_category_mapping.assert_called_once()
    assert mock_db.add_category_mapping.call_args.kwargs["confidence"] == 0.6


def test_true_follow_from_ozon_product_id_conf_060():
    """兼容旧信封：draft.ozon_product_id 单独出现（无 follow_sell/follow_type）仍视为真跟卖。"""
    state = _make_state(draft={**_leaf_draft(), "ozon_product_id": "1234567"},
                        envelope={"extensions": {}})
    mock_db = _run_learning_record_node(state)
    mock_db.add_category_mapping.assert_called_once()
    assert mock_db.add_category_mapping.call_args.kwargs["confidence"] == 0.6


# ═══════════════════════════════════════════════════════════════
# 断点2: match_layer 透传 —— L0 自证跳过 / Skill 0.9 / L1 0.7 / R2b 0.7
# ═══════════════════════════════════════════════════════════════

def test_l0_hit_approved_skips_upsert():
    """match_layer=L0（本次 dc 来自学习表命中）→ add_category_mapping 不被调（防自证回环）。"""
    state = _make_state(draft=_leaf_draft(),
                        category_match_meta={"match_layer": "L0"})
    mock_db = _run_learning_record_node(state)
    mock_db.add_category_mapping.assert_not_called()


def test_l0_hit_with_same_dc_skips_upsert():
    """L0 + meta dc 与本次 state dc 一致 → 自证，跳过（防御性：meta 带 dc 时同 dc 才跳）。"""
    state = _make_state(draft=_leaf_draft(),
                        category_match_meta={"match_layer": "L0",
                                             "description_category_id": _TEST_DC,
                                             "type_id": str(_TEST_TP)})
    mock_db = _run_learning_record_node(state)
    mock_db.add_category_mapping.assert_not_called()


def test_l0_layer_but_dc_changed_writes_conf_070():
    """L0 但 state dc 已被上游更换（重配/修复换类目）→ 新 dc 非学习表直采，等同重配确认档 0.7。"""
    state = _make_state(draft=_leaf_draft(),
                        description_category_id="17028653", type_id=92147,
                        category_match_meta={"match_layer": "L0",
                                             "description_category_id": "99999",
                                             "type_id": "88888"})
    mock_db = _run_learning_record_node(state)
    mock_db.add_category_mapping.assert_called_once()
    assert mock_db.add_category_mapping.call_args.kwargs["confidence"] == 0.7


def test_skill_layer_conf_090():
    """match_layer=Skill（权威 Ozon 卡/榜单类目直采）→ conf 0.9。"""
    state = _make_state(draft=_leaf_draft(),
                        category_match_meta={"match_layer": "Skill"})
    mock_db = _run_learning_record_node(state)
    mock_db.add_category_mapping.assert_called_once()
    assert mock_db.add_category_mapping.call_args.kwargs["confidence"] == 0.9


def test_l1_layer_conf_070():
    """match_layer=L1（文本/pg_trgm 匹配）→ conf 0.7。"""
    state = _make_state(draft=_leaf_draft(),
                        category_match_meta={"match_layer": "L1"})
    mock_db = _run_learning_record_node(state)
    mock_db.add_category_mapping.assert_called_once()
    assert mock_db.add_category_mapping.call_args.kwargs["confidence"] == 0.7


def test_r2b_layer_conf_070():
    """match_layer=R2b（retry 重配 LLM 确认档）→ conf 0.7。"""
    state = _make_state(draft=_leaf_draft(),
                        category_match_meta={"match_layer": "R2b"})
    mock_db = _run_learning_record_node(state)
    mock_db.add_category_mapping.assert_called_once()
    assert mock_db.add_category_mapping.call_args.kwargs["confidence"] == 0.7


# ═══════════════════════════════════════════════════════════════
# 断点3: 写侧语义预检（leaf ↔ Ozon ZH 路径）
# ═══════════════════════════════════════════════════════════════

def _leaf_path_overlap(leaf, path_zh):
    from graphs.nodes.learning_record_node import _leaf_path_overlap as _f
    return _f(leaf, path_zh)


def test_leaf_path_overlap_pure():
    """纯函数语义：无有效 token 放行 / 有 token 命中放行 / 零重叠拒写。"""
    from graphs.nodes.learning_record_node import _leaf_path_overlap as _f
    # 手套 vs 成人糖果 → 无重叠 → 拒写
    assert _f("手套", "成人用品>成人的糖果点心") is False
    # 手套 vs 服饰配件>手套 → 命中 → 写
    assert _f("手套", "服饰配件>手套") is True
    # 全泛词（无有效 token）→ 不拦（走既有分级）
    assert _f("通用配件", "儿童玩具>滑梯") is True


def test_semantic_precheck_rejects_mismatch():
    """节点级：leaf=手套、cat_zh=成人用品>成人的糖果点心 → 零语义重叠 → 拒写。"""
    state = _make_state(draft=_leaf_draft("服饰配件 > 手套"),
                        zh_path="成人用品 > 成人的糖果点心")
    mock_db = _run_learning_record_node(state, zh_path="成人用品 > 成人的糖果点心")
    mock_db.add_category_mapping.assert_not_called()


def test_semantic_precheck_allows_match():
    """节点级：leaf=手套、cat_zh=服饰配件>手套 → 有重叠 → 写。"""
    state = _make_state(draft=_leaf_draft("服饰配件 > 手套"),
                        zh_path="服饰配件 > 手套")
    mock_db = _run_learning_record_node(state, zh_path="服饰配件 > 手套")
    mock_db.add_category_mapping.assert_called_once()


# ═══════════════════════════════════════════════════════════════
# 增强5: match_evidence 不可信 → conf 压 0.6
# ═══════════════════════════════════════════════════════════════

def test_match_evidence_untrusted_caps_conf():
    """match_evidence={method:text, confidence:0.2}（非 aibuy 且低置信）→ conf 压 0.6。"""
    state = _make_state(draft=_leaf_draft(),
                        envelope={"extensions": {
                            "match_evidence": {"method": "text", "confidence": 0.2}}})
    mock_db = _run_learning_record_node(state)
    mock_db.add_category_mapping.assert_called_once()
    assert mock_db.add_category_mapping.call_args.kwargs["confidence"] == 0.6


def test_match_evidence_aibuy_not_capped():
    """match_evidence method=aibuy（真实以图搜款）→ 不压（缺省 conf 0.85 保持）。"""
    state = _make_state(draft=_leaf_draft(),
                        envelope={"extensions": {
                            "match_evidence": {"method": "aibuy", "confidence": 0.9}}})
    mock_db = _run_learning_record_node(state)
    mock_db.add_category_mapping.assert_called_once()
    assert mock_db.add_category_mapping.call_args.kwargs["confidence"] == 0.85


def test_true_follow_plus_untrusted_evidence_caps_conf_060():
    """真跟卖 + match_evidence 不可信同时命中 → 取最低仍 0.6（写不炸）。"""
    state = _make_state(draft=_leaf_draft(),
                        envelope={"extensions": {
                            "follow_sell": True, "follow_type": "hand",
                            "match_evidence": {"method": "text", "confidence": 0.1}}})
    mock_db = _run_learning_record_node(state)
    mock_db.add_category_mapping.assert_called_once()
    assert mock_db.add_category_mapping.call_args.kwargs["confidence"] == 0.6


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
