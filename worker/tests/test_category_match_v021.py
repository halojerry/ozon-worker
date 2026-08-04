"""类目匹配 v0.21 修复单测：同义词外置 / 叶节点加权 / L0 一致性校验 / 泛化词拒绝。"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from graphs.nodes.assemble_ozon_product_node import (
    _load_category_synonyms,
    _apply_leaf_bonus,
    _l0_consistent,
    _merge_candidates,
)


def _cand(dc, tp, name, score):
    return {"description_category_id": dc, "type_id": tp, "node_name": name,
            "full_path": name, "_score": score, "similarity": score}


def test_synonyms_loaded_from_json():
    syn = _load_category_synonyms()
    assert "震动棒" in syn
    assert "振动器" in syn["震动棒"]
    assert "后视镜" in syn


def test_leaf_bonus_promotes_synonym_match():
    """震动棒：同义词『振动器』应被 +0.5 提升到首位。"""
    cands = [
        _cand(200001551, 971069563, "适应性器具、餐具", 1.6),
        _cand(17028959, 96513, "振动器", 1.6),
    ]
    syn = _load_category_synonyms()
    out = _apply_leaf_bonus(cands, "震动棒", syn)
    assert out[0]["type_id"] == 96513, f"振动器应排第一: {out}"


def test_leaf_bonus_exact_beats_contains():
    """精确同名(+0.6) 必须胜过 包含(+0.3)：振动器 > 振动器配件。"""
    cands = [
        _cand(17028959, 971096778, "振动器配件", 1.6),
        _cand(17028959, 96513, "振动器", 1.6),
    ]
    syn = _load_category_synonyms()
    out = _apply_leaf_bonus(cands, "震动棒", syn)
    assert out[0]["type_id"] == 96513, f"精确匹配应排第一: {out}"


def test_leaf_bonus_promotes_exact_leaf():
    """后视镜：节点名含『后视镜』应胜出。"""
    cands = [
        _cand(200000933, 785353054, "单车裤", 1.3),
        _cand(17027929, 970849653, "摩托车后视镜", 2.3),
    ]
    out = _apply_leaf_bonus(cands, "后视镜", {})
    assert out[0]["type_id"] == 970849653


def test_leaf_bonus_suffix_beats_contains_tie():
    """同名后缀(+0.5) 应胜过仅包含(+0.3)：摩托车后视镜 > 儿童摩托车配件（同分时）。"""
    cands = [
        _cand(63444126, 970870421, "儿童汽车、摩托车配件及备件", 2.3),
        _cand(17027929, 970849653, "摩托车后视镜", 2.3),
    ]
    out = _apply_leaf_bonus(cands, "后视镜", {"后视镜": ["后视镜", "摩托车后视镜", "汽车后视镜"]})
    assert out[0]["type_id"] == 970849653, f"后缀匹配应排第一: {out}"


def test_merge_candidates_dedupe_keep_high_score():
    a = [_cand(17028959, 96513, "振动器", 2.1), _cand(1, 2, "x", 0.5)]
    b = [_cand(17028959, 96513, "振动器", 1.9), _cand(3, 4, "y", 0.8)]
    out = _merge_candidates(a, b)
    assert len(out) == 3
    top = [c for c in out if c["type_id"] == 96513][0]
    assert top["_score"] == 2.1


def test_l0_consistent_accepts_matching():
    l0 = {"description_category_id": 17028959, "type_id": 96513}
    cands = [_cand(17028959, 96513, "振动器", 2.1), _cand(1, 2, "x", 1.0)]
    assert _l0_consistent(l0, cands) is True


def test_l0_consistent_rejects_poisoned():
    """L0 记录（残疾人辅助器具）不在 L1 候选里 → 判定为污染，忽略。"""
    l0 = {"description_category_id": 200001551, "type_id": 971069563}
    cands = [_cand(17028959, 96513, "振动器", 2.1)]
    assert _l0_consistent(l0, cands) is False
