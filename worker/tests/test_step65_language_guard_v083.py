#!/usr/bin/env python3
"""v0.83 gate B3: Step6.5 / R4 类目守卫语言一致比较 + R2b 阶梯。

gate v083 #3 实锤：Step 6.5 `_step65_adoption_overlap` 拿**中文源词**比 **RU 候选
路径/叶子名** → 跨语言恒空集 → 置信度 1.00 的重配候选也被拦入箱。本用例锁：

- RU 路径 × RU 标题（llm_name）同语言命中 → 放行（不再恒拦）；
- 中文源词 × ZH 候选路径 → 原字面判据不变；
- 跨语言且无 RU 标题 → 空集（调用方降 R2b 阶梯，不再直接入箱）；
- R4（validation_retry_loop）RU 回退候选 × RU 产品名同款放行。

运行:
    cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_step65_language_guard_v083.py -q
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("GRSAI_API_KEY", "test-key")

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
sys.path.insert(0, _SRC)

from graphs.nodes import assemble_ozon_product_node as asm  # noqa: E402


# ═══════════════════════════════════════════════════════════════════════
# 1. RU 语言一致 overlap 纯函数
# ═══════════════════════════════════════════════════════════════════════

class TestRuOverlap:
    def test_ru_path_vs_ru_title_hits(self):
        ov = asm._ru_non_generic_overlap_words(
            "Дом и сад > Хранение > Ключница",
            ["Ключница настенная деревянная для ключей"])
        assert "ключница" in ov, "同语言（RU×RU）命中应有判别力"

    def test_ru_path_vs_chinese_no_hit(self):
        assert asm._ru_non_generic_overlap_words(
            "Дом и сад > Хранение > Ключница", ["钥匙盒 收纳"]) == set()

    def test_ru_generic_word_excluded(self):
        # «для»/«товары» 等 RU 泛词不算重叠
        assert asm._ru_non_generic_overlap_words(
            "Товары для дома > Прочее", ["товары для дома"]) == set()

    def test_empty_inputs(self):
        assert asm._ru_non_generic_overlap_words("", ["Ключница"]) == set()
        assert asm._ru_non_generic_overlap_words("Ключница", None) == set()


# ═══════════════════════════════════════════════════════════════════════
# 2. _step65_adoption_overlap 语言分流
# ═══════════════════════════════════════════════════════════════════════

class TestStep65AdoptionOverlap:
    def test_zh_candidate_chinese_source_still_passes(self):
        """原判据保持：ZH 候选路径 × 中文源词字面命中。"""
        ov = asm._step65_adoption_overlap(
            "儿童玩具 > 滑梯", "滑梯", ["儿童滑梯 儿童玩具 > 滑梯"])
        assert "滑梯" in ov

    def test_ru_candidate_ru_title_now_passes(self):
        """gate #3 形态：中文源词 + RU 候选 + RU 标题 → 不再恒空集。"""
        ov = asm._step65_adoption_overlap(
            "Дом и сад > Хранение > Ключница",
            "Ключница",
            ["钥匙盒 收纳盒"],              # 中文 1688 源词（跨语言，无命中）
            ru_texts=["Ключница деревянная настенная"],  # LLM 俄语标题
        )
        assert ov, "RU 候选 × RU 标题必须放行（此前恒空集致置信度 1.00 也入箱）"

    def test_cross_language_without_ru_title_still_empty(self):
        """跨语言且无 RU 标题 → 空集（调用方降 R2b 阶梯，不再直接入箱）。"""
        ov = asm._step65_adoption_overlap(
            "Дом и сад > Хранение > Ключница", "Ключница", ["钥匙盒 收纳盒"],
        )
        assert ov == set()

    def test_ru_leaf_substring_passes(self):
        """RU 叶子名子串命中（路径词面不同但叶子一致）。"""
        ov = asm._step65_adoption_overlap(
            "Товары > Прочее", "Ключница", ["钥匙盒"],
            ru_texts=["Ключница для ключей"],
        )
        assert ov

    def test_semantically_unrelated_still_rejects(self):
        """真语义无关（RU 标题与候选 RU 路径零交集）→ 空集。"""
        ov = asm._step65_adoption_overlap(
            "Спорт > Тренажёры > Беговая дорожка", "Беговая дорожка",
            ["钥匙盒"], ru_texts=["Ключница деревянная"],
        )
        assert ov == set()


# ═══════════════════════════════════════════════════════════════════════
# 3. R4 RU 回退候选语言一致放行
# ═══════════════════════════════════════════════════════════════════════

_RU_PATH = "Дом и сад > Хранение > Ключница"
_OLD_DC, _OLD_TP = 17028959, 96513
_NEW_DC, _NEW_TP = 17028653, 92147


class _RuFakeQuery:
    """search_nodes 返回 RU 候选（模拟 ZH 搜索无候选 → RU 回退）。"""

    def search_nodes(self, *a, **k):
        return [{"description_category_id": _NEW_DC, "type_id": _NEW_TP,
                 "full_path": _RU_PATH, "node_name": "Ключница", "similarity": 0.8}]

    def get_node(self, dc, tp=None, language="ZH_HANS"):
        return {"description_category_id": int(dc), "type_id": int(tp or 0),
                "full_path": _RU_PATH, "node_name": "Ключница"}

    def get_attribute_schema(self, *a, **k):
        return {"result": []}


def _fake_rebuild(new_dc, new_type, **kw):
    return {"items": [{"description_category_id": new_dc, "type_id": new_type,
                       "name": "Ключница", "attributes": []}],
            "final_attributes": [{"attribute_id": 4180, "value": "Ключница",
                                  "dictionary_value_id": 0}],
            "llm_name": "Ключница", "attr_list": [], "dict_lookup": {}}


def _ru_r4_state():
    from graphs.validation_retry_loop import ValidationRetryLoopState
    return ValidationRetryLoopState(
        token="t", ozon_client_id="c", ozon_api_key="k",
        description_category_id=str(_OLD_DC), type_id=str(_OLD_TP),
        draft={"title": "钥匙盒", "source_category": "收纳 > 钥匙盒",
               "source_category_id": 90210, "weight": 300,
               "dimensions": {"length": 20, "width": 30, "height": 10},
               "images": []},
        product_name="Ключница деревянная",
        ozon_payload={"items": [{"name": "Ключница деревянная",
                                 "description_category_id": _OLD_DC,
                                 "type_id": _OLD_TP, "price": "1990",
                                 "currency_code": "RUB", "depth": 10, "width": 20,
                                 "height": 30, "weight": 200, "images": []}]},
    )


def test_r4_ru_candidate_matches_ru_product_name(monkeypatch):
    """R4：中文源词 × RU 候选路径恒空，但 RU 产品名命中 → 采纳为 L1（不再误弃）。"""
    from unittest import mock
    from graphs import validation_retry_loop as vrl

    state = _ru_r4_state()
    with mock.patch("utils.ozon_category_query.get_category_query", return_value=_RuFakeQuery()), \
         mock.patch("utils.ozon_category_query.sensitive_candidate_filter",
                    side_effect=lambda c, s: c), \
         mock.patch("utils.ozon_category_query.sensitive_adoption_blocked",
                    return_value=False), \
         mock.patch("graphs.nodes.assemble_ozon_product_node._rebuild_for_new_category",
                    side_effect=_fake_rebuild):
        ok = vrl._try_recategorize_card(state)
    assert ok is True, "RU 候选 × RU 产品名同语言命中应采纳（此前跨语言恒空被弃）"
    assert state.description_category_id == str(_NEW_DC)
    assert state.category_match_meta["match_layer"] == "L1"
