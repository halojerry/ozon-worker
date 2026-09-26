#!/usr/bin/env python3
"""fix/listing-quality-v081 修复3 — follow 图搜类目一致性语义闸（三处断链补齐）。

根因锚点（实锤错配：家用橡胶手套→月季修剪牛皮园艺手套、钓鱼腰包→宽檐渔夫帽）:
a) cloud_probe follow 组装候选时剥掉 aibuy 候选的 category_name/cate_*_id
   （_normalize_search_match 整形层，本批补透传 + match_meta 非空）
b) follow 调 _pick_best_match 没传 ozon_category_path（竞品面包屑在
   result["ozon_category"]["category_path"] 现成）
c) _pick_best_match aibuy trusted 直通（原始排名前 2 无条件放行）位于一切
   标题/LLM 护栏之前——require_category_consistency=True 闸插在直通之前
   （默认 False，discover 链行为零变化）

运行:
    cd skill && .venv314/bin/python -m pytest tests/test_follow_match_semantic_gate_v081.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import scripts.cloud_probe as cloud_probe  # noqa: E402
import scripts.lib.ozon_discovery as od  # noqa: E402

_GLOVE_RU = "Перчатки хозяйственные резиновые"
_GLOVE_BREADCRUMB = "Дом и сад > Домашние перчатки"


def _cand(idx: int, title: str, cat: str = "", norm: float = 0.0,
          price: float = 9.9) -> dict:
    """aibuy 形状候选（badge 恒空，靠官方排序 + normalization_score）。"""
    return {
        "id": f"offer{idx}",
        "title": title,
        "price": price,
        "image": "https://cbu01.alicdn.com/img/ibank/x.jpg",
        "badge": "",
        "badge_score": 0,
        "normalization_score": norm,
        "category_name": cat,
        "cate_level1_id": "21",
        "cate_level2_id": "1047",
    }


# ── 回归 1：手套（闸选类目一致候选；默认关不回归）──


class TestGloveRegression:
    def test_gate_picks_category_consistent_candidate(self, monkeypatch):
        """idx0 园艺手套（category_name=园艺用品）→ NO；idx1 家用橡胶手套
        （category_name=家务清洁）→ YES → 闸返回 idx1。"""
        cand0 = _cand(0, "月季修剪牛皮园艺手套 加厚耐磨", cat="园艺用品")
        cand1 = _cand(1, "家用橡胶手套厨房清洁耐用", cat="家务清洁")
        calls: list[tuple] = []

        def _fake_llm(ru, cn, token="", mode="product"):
            calls.append((ru, cn, mode))
            return "家务" in cn

        monkeypatch.setattr(od, "_llm_semantic_match", _fake_llm)
        blocked: list[dict] = []
        monkeypatch.setattr(od, "_log_review_record", lambda rec: blocked.append(rec))

        best = od._pick_best_match(
            [cand0, cand1], _GLOVE_RU, token="tok", trusted_source=True,
            ozon_category_path=_GLOVE_BREADCRUMB, require_category_consistency=True)

        assert best is not None
        assert best["title"].startswith("家用橡胶手套")
        assert best["category_name"] == "家务清洁"
        # match_meta 非空（3a 透传键 → _attach_match_meta 补 category_id/name）
        assert best["category_id"] == "1047"
        assert best["reject_reason"] == ""
        # LLM 判定语料 = 面包屑末级 vs 候选 category_name，mode="category"
        assert calls[0] == ("Домашние перчатки", "园艺用品", "category")
        assert calls[1] == ("Домашние перчатки", "家务清洁", "category")
        assert blocked == []  # 放行出口不写 block 记录

    def test_gate_default_off_keeps_trusted_pass(self, monkeypatch):
        """require_category_consistency 缺省 False → discover 链行为零变化
        （trusted 直通 idx0，全程零 LLM 调用）。"""
        cand0 = _cand(0, "月季修剪牛皮园艺手套 加厚耐磨", cat="园艺用品")
        cand1 = _cand(1, "家用橡胶手套厨房清洁耐用", cat="家务清洁")

        def _no_llm(*a, **k):
            raise AssertionError("默认关不得触发 LLM 闸")

        monkeypatch.setattr(od, "_llm_semantic_match", _no_llm)
        best = od._pick_best_match(
            [cand0, cand1], _GLOVE_RU, token="tok", trusted_source=True,
            ozon_category_path=_GLOVE_BREADCRUMB)
        assert best is not None
        assert best["title"].startswith("月季修剪")


# ── 回归 2：钓鱼腰包→帽（全部候选品类不一致 → None + cap 6）──


class TestFishingRegression:
    def test_all_divergent_returns_none_with_cap(self, monkeypatch):
        cands = [
            _cand(i, f"宽檐渔夫帽遮阳帽款式{i}", cat="帽子")
            for i in range(8)
        ]
        n_calls: list[int] = []

        def _fake_llm(ru, cn, token="", mode="product"):
            n_calls.append(1)
            return False

        monkeypatch.setattr(od, "_llm_semantic_match", _fake_llm)
        blocked: list[dict] = []
        monkeypatch.setattr(od, "_log_review_record", lambda rec: blocked.append(rec))

        best = od._pick_best_match(
            cands, "Сумка для рыбалки поясная", token="tok", trusted_source=True,
            ozon_category_path="Спорт и отдых > Рыболовные сумки",
            require_category_consistency=True)

        assert best is None
        assert len(n_calls) == 6  # cap 6 防 LLM 费用失控（8 候选只判前 6）
        assert blocked and blocked[0]["reject_reason"] == "category_divergent"


# ── 3a 字段透传 ──


class TestCategoryKeyPassthrough:
    def test_normalize_search_match_keeps_category_keys(self):
        p = {
            "product_id": "679836775118",
            "title": "家用橡胶手套厨房清洁耐用",
            "price": 3.2,
            "image": "https://cbu01.alicdn.com/img/ibank/x.jpg",
            "badge": "",
            "normalization_score": 0.8,
            "category_name": "家务清洁",
            "cate_level1_id": "21",
            "cate_level2_id": "1047",
        }
        m = cloud_probe._normalize_search_match(p)
        assert m is not None
        assert m["category_name"] == "家务清洁"
        assert m["cate_level1_id"] == "21"
        assert m["cate_level2_id"] == "1047"
        assert m["normalization_score"] == 0.8

    def test_normalize_search_match_no_id_returns_none(self):
        assert cloud_probe._normalize_search_match({"title": "x"}) is None

    def test_match_meta_carries_category_after_gate_pass(self, monkeypatch):
        """透传键经 _attach_match_meta 补 category_id/name（match_meta 非空）。"""
        m = cloud_probe._normalize_search_match({
            "product_id": "679836775118", "title": "家用橡胶手套",
            "price": 3.2, "badge": "",
            "category_name": "家务清洁",
            "cate_level1_id": "21", "cate_level2_id": "1047",
        })
        best = od._pick_best_match(
            [m], _GLOVE_RU, token="tok", trusted_source=True)
        assert best is not None
        assert best["category_id"] == "1047"
        assert best["category_name"] == "家务清洁"


# ── 数据不全 no-op（fail-open，不回归）──


class TestDataIncompleteNoOp:
    def test_no_breadcrumb_gate_skipped(self, monkeypatch):
        """面包屑缺失 → 闸整体跳过，行为与现状一致（trusted 直通 idx0）。"""

        def _no_llm(*a, **k):
            raise AssertionError("面包屑缺失不得触发 LLM 闸")

        monkeypatch.setattr(od, "_llm_semantic_match", _no_llm)
        cand0 = _cand(0, "月季修剪牛皮园艺手套", cat="园艺用品")
        best = od._pick_best_match(
            [cand0], _GLOVE_RU, token="tok", trusted_source=True,
            require_category_consistency=True)
        assert best is not None
        assert best["title"].startswith("月季修剪")

    def test_no_token_wordpair_fallback_hits_candidate(self, monkeypatch):
        """无 token → 词对快筛：перчатк→手套 映射词在候选命中则放行（沿排序首个）。"""

        def _no_llm(*a, **k):
            raise AssertionError("无 token 不得调 LLM")

        monkeypatch.setattr(od, "_llm_semantic_match", _no_llm)
        cand0 = _cand(0, "月季修剪牛皮园艺手套", cat="园艺用品")
        cand1 = _cand(1, "橡胶手套加厚防水", cat="家务清洁")
        best = od._pick_best_match(
            [cand0, cand1], _GLOVE_RU, token="", trusted_source=True,
            ozon_category_path=_GLOVE_BREADCRUMB, require_category_consistency=True)
        assert best is not None
        assert best["title"].startswith("月季修剪")  # 含「手套」→ 快筛放行 idx0

    def test_no_token_unmappable_breadcrumb_fail_open(self, monkeypatch):
        """无 token 且词典无法映射面包屑 → fail-open no-op（旧行为直通）。"""
        breadcrumb = "Тестовый раздел каталога"
        assert od._breadcrumb_zh_words(breadcrumb) is None  # 夹具自证不可映射

        def _no_llm(*a, **k):
            raise AssertionError("无 token 不得调 LLM")

        monkeypatch.setattr(od, "_llm_semantic_match", _no_llm)
        cand0 = _cand(0, "宽檐渔夫帽遮阳", cat="帽子")
        best = od._pick_best_match(
            [cand0], "Детский дождевик", token="", trusted_source=True,
            ozon_category_path=breadcrumb, require_category_consistency=True)
        assert best is not None
        assert best["title"].startswith("宽檐渔夫帽")


# ── 降级快筛语料 ──


class TestBreadcrumbZhWords:
    def test_glove_breadcrumb_maps(self):
        words = od._breadcrumb_zh_words(_GLOVE_BREADCRUMB)
        assert words is not None and "手套" in words

    def test_empty_returns_none(self):
        assert od._breadcrumb_zh_words("") is None
