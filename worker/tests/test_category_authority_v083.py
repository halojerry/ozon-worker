#!/usr/bin/env python3
"""fix/category-authority-v1 v083 — 类目权威边界（堵后门 + Step6.5 守卫 + L0 清洗）。

覆盖（纯 mock，不连 PG/不触网/不跑 CDP）：
  ① R4（`_try_recategorize_card`）不再伪造 R2b 标记——直过 overlap 守卫的候选置
     L1；零 overlap 走门控仲裁（真过 `_r2b_confirm_adoption`）才置 R2b；仲裁不过
     保留 needs_recategorization（不静默换类目）。
  ② Step 6.5 采纳点源词 overlap 守卫（`_step65_adoption_overlap` 纯函数）——
     full_path 字面 / 叶子名子串命中才放行。
  ③ L0 清洗脚本 `audit_category_mapping.classify_mapping_row` 三档判据。

运行:
    cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_category_authority_v083.py -q
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest import mock

os.environ.setdefault("GRSAI_API_KEY", "test-key")

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
_SCRIPTS = os.path.join(os.path.dirname(__file__), "..", "scripts")
sys.path.insert(0, _SRC)
sys.path.insert(0, _SCRIPTS)

from graphs.nodes import assemble_ozon_product_node as asm  # noqa: E402


# ═══════════════════════════════════════════════════════════════════════
# ② Step 6.5 采纳点源词 overlap 守卫（纯函数）
# ═══════════════════════════════════════════════════════════════════════

class TestStep65AdoptionOverlap:
    def test_full_path_overlap_passes(self):
        ov = asm._step65_adoption_overlap(
            "儿童玩具 > 滑梯", "滑梯", ["儿童滑梯 儿童玩具 > 滑梯"])
        assert "滑梯" in ov

    def test_leaf_substring_overlap_passes(self):
        # full_path 无字面命中，但 node_name 子串命中
        ov = asm._step65_adoption_overlap(
            "Детские товары > Горки", "滑梯", ["儿童滑梯 滑梯"])
        assert ov

    def test_zero_overlap_rejects(self):
        ov = asm._step65_adoption_overlap(
            "儿童玩具 > 滑梯", "滑梯", ["震动棒 成人用品"])
        assert ov == set()

    def test_generic_word_only_rejects(self):
        # 「用品」是泛化词，不算重叠
        ov = asm._step65_adoption_overlap(
            "儿童用品", "用品", ["成人用品"])
        assert ov == set()


# ═══════════════════════════════════════════════════════════════════════
# ① R4 换类目：overlap 守卫 + 不伪造 R2b
# ═══════════════════════════════════════════════════════════════════════

_OLD_DC, _OLD_TP = 17028959, 96513
_NEW_DC, _NEW_TP = 17028653, 92147


class _FakeQuery:
    def __init__(self, candidates=None):
        self._candidates = candidates if candidates is not None else [
            {"description_category_id": _NEW_DC, "type_id": _NEW_TP,
             "full_path": "儿童玩具 > 滑梯", "node_name": "滑梯",
             "similarity": 0.9},
        ]

    def search_nodes(self, *a, **k):
        return list(self._candidates)

    def get_node(self, dc, tp=None, language="ZH_HANS"):
        return {"description_category_id": int(dc), "type_id": int(tp or 0),
                "full_path": "儿童玩具 > 滑梯", "node_name": "滑梯"}

    def get_attribute_schema(self, *a, **k):
        return {"result": []}


def _fake_rebuild(new_dc, new_type, **kw):
    return {"items": [{"description_category_id": new_dc, "type_id": new_type,
                       "name": "Горка детская", "attributes": []}],
            "final_attributes": [{"attribute_id": 4180, "value": "Горка",
                                  "dictionary_value_id": 0}],
            "llm_name": "Горка детская", "attr_list": [], "dict_lookup": {}}


def _r4_state(*, title_cn: str, source_cat: str):
    from graphs.validation_retry_loop import ValidationRetryLoopState
    return ValidationRetryLoopState(
        token="t", ozon_client_id="c", ozon_api_key="k",
        description_category_id=str(_OLD_DC), type_id=str(_OLD_TP),
        draft={"title": title_cn, "source_category": source_cat,
               "source_category_id": 90210, "weight": 300,
               "dimensions": {"length": 20, "width": 30, "height": 10},
               "images": []},
        product_name="Горка",
        ozon_payload={"items": [{"name": "Горка", "description_category_id": _OLD_DC,
                                 "type_id": _OLD_TP, "price": "1990",
                                 "currency_code": "RUB", "depth": 10, "width": 20,
                                 "height": 30, "weight": 200, "images": []}]},
    )


def _run_r4(monkeypatch, state, *, query, arbitration=None):
    from graphs import validation_retry_loop as vrl
    with mock.patch("utils.ozon_category_query.get_category_query", return_value=query), \
         mock.patch("utils.ozon_category_query.sensitive_candidate_filter",
                    side_effect=lambda c, s: c), \
         mock.patch("utils.ozon_category_query.sensitive_adoption_blocked",
                    return_value=False), \
         mock.patch("graphs.nodes.assemble_ozon_product_node._rebuild_for_new_category",
                    side_effect=_fake_rebuild), \
         mock.patch("utils.local_db_manager.LocalDBManager") as mock_db:
        if arbitration is not None:
            monkeypatch.setattr(
                "graphs.nodes.follow_sell_import_node._gated_category_arbitration",
                arbitration, raising=False)
        ok = vrl._try_recategorize_card(state)
        return ok, mock_db


def test_r4_overlap_present_adopts_as_l1(monkeypatch):
    """候选与源词有 overlap → 直采，match_layer=L1（**不再伪造 R2b**）。"""
    state = _r4_state(title_cn="儿童滑梯", source_cat="儿童玩具 > 滑梯")
    ok, _ = _run_r4(monkeypatch, state, query=_FakeQuery())
    assert ok is True
    assert state.description_category_id == str(_NEW_DC)
    assert state.category_match_meta["match_layer"] == "L1"
    assert state.category_match_meta["confidence"] == 0.7


def test_r4_zero_overlap_arbitration_fails_keeps_needs_recat(monkeypatch):
    """零 overlap + 门控仲裁不过 → 返回 False（保留 needs_recategorization，不换类目）。"""
    state = _r4_state(title_cn="震动棒", source_cat="成人用品 > 震动棒")
    ok, _ = _run_r4(monkeypatch, state, query=_FakeQuery(),
                    arbitration=lambda *a, **k: ("", ""))
    assert ok is False
    assert state.description_category_id == str(_OLD_DC), "零 overlap 不得静默换类目"


def test_r4_zero_overlap_arbitration_passes_sets_r2b(monkeypatch):
    """零 overlap + 门控仲裁通过（真过 _r2b_confirm_adoption）→ 采纳且置 R2b。"""
    state = _r4_state(title_cn="震动棒", source_cat="成人用品 > 震动棒")
    ok, _ = _run_r4(monkeypatch, state, query=_FakeQuery(),
                    arbitration=lambda *a, **k: (str(_NEW_DC), str(_NEW_TP)))
    assert ok is True
    assert state.description_category_id == str(_NEW_DC)
    assert state.category_match_meta["match_layer"] == "R2b"


# ═══════════════════════════════════════════════════════════════════════
# ③ L0 清洗脚本三档判据（纯函数）
# ═══════════════════════════════════════════════════════════════════════

def _acm():
    import audit_category_mapping as m
    return m


class TestAuditCategoryMapping:
    _TREE = {(_NEW_DC, _NEW_TP)}

    def test_curated_never_touched(self):
        m = _acm()
        action, _ = m.classify_mapping_row(
            {"source": "curated", "description_category_id": 1, "type_id": 2,
             "category_path_zh": "任何", "source_category_leaf": "x"}, set())
        assert action == m.ACT_SKIP_CURATED

    def test_dc_tp_not_in_tree_offline(self):
        m = _acm()
        action, reason = m.classify_mapping_row(
            {"source": "learned_approved", "description_category_id": 111,
             "type_id": 222, "category_path_zh": "儿童玩具 > 滑梯",
             "source_category_leaf": "滑梯"}, self._TREE)
        assert action == m.ACT_OFFLINE and "不在 Ozon 类目树" in reason

    def test_empty_cat_zh_report_only(self):
        m = _acm()
        action, _ = m.classify_mapping_row(
            {"source": "learned_approved", "description_category_id": _NEW_DC,
             "type_id": _NEW_TP, "category_path_zh": "", "source_category_leaf": "滑梯"},
            self._TREE)
        assert action == m.ACT_REPORT_ONLY

    def test_zero_overlap_downgrade(self):
        m = _acm()
        action, reason = m.classify_mapping_row(
            {"source": "learned_approved", "description_category_id": _NEW_DC,
             "type_id": _NEW_TP, "category_path_zh": "儿童玩具 > 滑梯",
             "source_category_leaf": "震动棒"}, self._TREE)
        assert action == m.ACT_DOWNGRADE and "零 overlap" in reason

    def test_overlap_ok(self):
        m = _acm()
        action, _ = m.classify_mapping_row(
            {"source": "learned_approved", "description_category_id": _NEW_DC,
             "type_id": _NEW_TP, "category_path_zh": "儿童玩具 > 滑梯",
             "source_category_leaf": "滑梯"}, self._TREE)
        assert action == m.ACT_OK


if __name__ == "__main__":
    import traceback

    failed = total = 0
    classes = (TestStep65AdoptionOverlap, TestAuditCategoryMapping)
    for _cls in classes:
        inst = _cls()
        for _n, _f in sorted(vars(_cls).items()):
            if _n.startswith("test_") and callable(_f):
                total += 1
                try:
                    _f(inst)
                except Exception:
                    failed += 1
                    traceback.print_exc()
    print(f"{total - failed}/{total} passed")
    sys.exit(1 if failed else 0)
