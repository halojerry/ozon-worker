"""v0.71 类目链消费补全单测：cid 双源解析 + L0 写侧 cid 归并 + 8229 搜索不盲采。

背景（探索取证）：①assemble 的 L0 cid 直查只读 draft——图搜通道 cid 随
source.match_category_id 进信封后仍查不到（读侧断点）；②category_mapping
唯一键 (leaf,dc,tp)，同 cid 不同 1688 措辞裂行稀释 success_count（写侧断点）；
③assemble /values/search 8229 兜底 first=_results[0] 盲采是套娃错值残留通道。
"""
import os
import sys
from unittest import mock

os.environ.setdefault("GRSAI_API_KEY", "test-key")
os.environ.setdefault("LOG_FORMAT", "text")
os.environ.setdefault("LOG_LEVEL", "WARNING")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from graphs.nodes import assemble_ozon_product_node as asm  # noqa: E402
from utils.attr_value_sanitize import cap_attribute_values  # noqa: E402
from utils.local_db_manager import LocalDBManager  # noqa: E402


# ═══════════ cid 双源解析（读侧断点） ═══════════

def test_cid_prefers_draft_over_source():
    draft = {"source_category_id": "111"}
    source = {"category_id": "222"}
    assert asm.resolve_1688_source_category_id(draft, source) == "111"


def test_cid_falls_back_to_source_category_id():
    """draft 无数字时回 source.category_id（信封契约位）——此前恒空。"""
    assert asm.resolve_1688_source_category_id({}, {"category_id": " 222 "}) == "222"
    assert asm.resolve_1688_source_category_id({"source_category_id": ""}, {"category_id": 333}) == "333"


def test_cid_falls_back_to_match_category_id():
    """图搜通道 cid 在 source.match_category_id（wave 实证 approved 学习行
    cid 空的根因）——第三源兜底。"""
    assert asm.resolve_1688_source_category_id({}, {"match_category_id": "201162103"}) == "201162103"
    from utils.category_mapping_learn import resolve_1688_source_category_id as impl
    assert impl({"source_category_id": "1"}, {"category_id": "2", "match_category_id": "3"}) == "1"
    assert impl({}, {"category_id": "2", "match_category_id": "3"}) == "2"


def test_cid_both_missing_returns_empty():
    assert asm.resolve_1688_source_category_id({}, {}) == ""
    assert asm.resolve_1688_source_category_id(None, None) == ""
    assert asm.resolve_1688_source_category_id({"source_category_id": None}, "junk") == ""


# ═══════════ L0 写侧 cid 归并（不裂行） ═══════════

class _FakeRow:
    def __init__(self, leaf, cid, dc, tp, succ=1, source="learned_approved", conf=0.7):
        self.source_category_leaf = leaf
        self.source_category_id = cid
        self.description_category_id = dc
        self.type_id = tp
        self.success_count = succ
        self.source = source
        self.confidence = conf
        self.is_active = True
        self.last_used_at = None
        self.updates = []


def _patch_session_two_selects(row_for_canon):
    """第一次 execute（同 leaf 查询）→ None；第二次（同 cid 异措辞）→ row_for_canon。"""
    calls = {"n": 0}

    class _Res:
        def scalar_one_or_none(self):
            calls["n"] += 1
            return None if calls["n"] == 1 else row_for_canon

    class _Sess:
        def execute(self, *a, **k):
            return _Res()

        def commit(self):
            calls["committed"] = True

        def rollback(self):
            calls["rolled_back"] = True

        def close(self):
            pass

    return calls, _Sess()


def test_canonical_merge_same_cid_different_leaf():
    """同 cid 不同措辞已有行 → 原行 succ+1 + leaf 刷新，不插新行。"""
    row = _FakeRow("收纳盒旧措辞", 6001, 85282223, 970988646, succ=2)
    calls, sess = _patch_session_two_selects(row)
    lm = LocalDBManager()
    with mock.patch("utils.local_db_manager.get_session", return_value=sess):
        lm.add_category_mapping(
            "收纳盒新措辞", 85282223, 970988646,
            source="learned_approved", source_category_id=6001,
        )
    assert calls.get("committed") is True
    assert row.success_count == 3          # 原行累计（不裂行稀释）
    assert row.source_category_leaf == "收纳盒新措辞"  # 措辞刷新为最新
    assert row.source == "learned_approved"


def test_canonical_merge_skipped_when_same_leaf_row_exists():
    """同 (leaf,dc,tp) 已有行 → 走正常 ON CONFLICT upsert（不进归并分支）。"""
    existing = _FakeRow("同措辞", None, 85282223, 970988646)
    calls = {"n": 0}

    class _Res:
        def scalar_one_or_none(self):
            calls["n"] += 1
            return existing if calls["n"] == 1 else None

    class _Sess:
        def execute(self, *a, **k):
            return _Res()

        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

    lm = LocalDBManager()
    with mock.patch("utils.local_db_manager.get_session", return_value=_Sess()), \
         mock.patch("utils.local_db_manager.pg_insert"):
        lm.add_category_mapping(
            "同措辞", 85282223, 970988646,
            source="learned_approved", source_category_id=6001,
        )
    assert calls["n"] == 1  # 同 leaf 命中 → 不再做 cid 归并查询


def test_canonical_merge_skipped_without_cid():
    """无 cid（AK 详情通道）→ 行为与旧版一致，直接 upsert。"""
    calls, sess = _patch_session_two_selects(None)
    lm = LocalDBManager()
    with mock.patch("utils.local_db_manager.get_session", return_value=sess), \
         mock.patch("utils.local_db_manager.pg_insert"):
        lm.add_category_mapping("叶子", 1, 2, source="learned_approved")
    assert calls["n"] == 0  # 零预查


# ═══════════ 8229 集合属性多值回归（出口闸兜底） ═══════════

def test_8229_multivalue_trimmed_by_exit_gate():
    """8229 多值（套娃错值通道）在出口闸被裁到 1——真值=type_id 单值契约。"""
    schema = [{"id": 8229, "is_collection": True, "max_value_count": 1}]
    attrs = [{"id": 8229, "values": [
        {"dictionary_value_id": 93408, "value": "Ларь для овощей"},
        {"dictionary_value_id": 93409, "value": "Матрёшка"},
    ]}]
    out, marks = cap_attribute_values(attrs, schema)
    assert len(out[0]["values"]) == 1
    assert marks and marks[0]["cap"] == 1
