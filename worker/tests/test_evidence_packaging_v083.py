#!/usr/bin/env python3
"""v0.83 gate B4: 4191 数字锚定证据集补入 1688 规格表/SKU 明细/毛重箱规 + 重量单位归一。

gate v083 #4：`Вес: 121 г` 未剥、规格表真实数字不在证据集。修后证据集纳入
`draft.packagingRows/packagingTableText/skuDetails` 与毛重/箱规数值；重量 token 做
kg/кг↔g 单位归一（只归一不改数值，无容差——120≠121 宁剥勿放）。

运行:
    cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_evidence_packaging_v083.py -q
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("GRSAI_API_KEY", "test-key")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from utils import content_enrich as ce  # noqa: E402


# ═══════════════════════════════════════════════════════════════
# 1. collect_evidence_keys：规格表 / SKU 明细 / 毛重进证据集
# ═══════════════════════════════════════════════════════════════

class TestCollectEvidencePackaging:
    def test_packaging_rows_nested_numbers_collected(self):
        keys = ce.collect_evidence_keys(
            packaging_rows=[
                {"lengthText": "500", "widthText": "9", "weightGrams": 160},
                {"qty": 12},
            ],
        )
        for tok in ("500", "9", "160", "12"):
            assert ce._num_key(tok) in keys, f"规格表数字 {tok} 应进证据集：{keys}"

    def test_packaging_table_text_collected(self):
        keys = ce.collect_evidence_keys(packaging_table_text="箱装数量 500 шт，毛重 160 г")
        assert "500" in keys and "160" in keys

    def test_sku_details_collected(self):
        keys = ce.collect_evidence_keys(
            sku_details=[{"name": "A", "price": 3.37}, {"name": "B", "price": 5}],
        )
        assert "5" in keys  # 3.37 的 _num_key 会拆成 337/337? 只断言整数列 5
        assert any(k in keys for k in ("337", "3", "37"))

    def test_gross_weight_collected(self):
        keys = ce.collect_evidence_keys(gross_weight_g=160)
        assert "160" in keys


# ═══════════════════════════════════════════════════════════════
# 2. 重量单位归一（只归一无容差）
# ═══════════════════════════════════════════════════════════════

class TestWeightUnitNormalization:
    def test_kg_to_g(self):
        keys = ce.collect_evidence_keys(packaging_table_text="毛重 0.1 кг")
        assert "100" in keys, "0.1 кг 应归一为 100 克进证据集"

    def test_kg_cyrillic_and_grams(self):
        keys = ce.collect_evidence_keys(packaging_table_text="Вес 1.2 кг / 121 г")
        assert "1200" in keys and "121" in keys

    def test_no_numeric_tolerance(self):
        """只有 120 г 时 121 不在证据集（不做 ±容差，宁剥勿放）。"""
        keys = ce.collect_evidence_keys(packaging_table_text="Вес 120 г")
        assert "120" in keys
        assert "121" not in keys

    def test_grams_stay_grams(self):
        keys = ce.collect_evidence_keys(extra_texts=["масса 250 г"])
        assert "250" in keys


# ═══════════════════════════════════════════════════════════════
# 3. 锚定端到端：规格表可溯不剥 / 无证据重量仍剥
# ═══════════════════════════════════════════════════════════════

class TestAnchoringEndToEnd:
    def test_spec_table_number_kept(self):
        evidence = ce.collect_evidence_keys(
            packaging_rows=[{"qty": 500}],
            packaging_table_text="毛重 0.1 кг",
        )
        html = "<ul><li>Комплект: 500 шт</li><li>Вес: 100 г</li></ul>"
        out, stripped = ce.enforce_number_anchoring(html, evidence)
        assert "500" in out and "100" in out
        assert stripped == [], f"规格表数字应锚定，实际被剥 {stripped}"

    def test_unanchored_weight_number_stripped(self):
        """证据只有 100/160，撰写 121 → 剥（无容差）。"""
        evidence = ce.collect_evidence_keys(
            draft_attrs={}, weight_g=100, gross_weight_g=160,
            packaging_rows=[{"weightGrams": 160}],
        )
        html = "<ul><li>Вес: 121 г</li></ul><p>Обычный текст.</p>"
        out, stripped = ce.enforce_number_anchoring(html, evidence)
        assert "121" not in out and "121" in stripped

    def test_author_annotation_uses_packaging_evidence(self):
        """作者链：LLM 写规格表数字不被剥；写无证据数字被剥。"""
        def _llm(system, user, imgs):
            return "<ul><li>Количество: 500 шт</li><li>Вес: 121 г</li></ul>" + "а" * 600

        html, marks = ce.author_annotation(
            title_ru="Ключница",
            draft_attrs={},
            weight_g=100,
            token="t",
            llm=_llm,
            packaging_rows=[{"qty": 500}],
        )
        assert marks["description_source"] == "llm_authored"
        assert "500" in html, "规格表数字应锚定保留"
        assert "121" not in html and "121" in marks["numbers_stripped"], "无证据数字应剥"


# ═══════════════════════════════════════════════════════════════
# 4. v0.83.1 B4-evidence：管线内最终真值（reconcile + 体积兜底后）可溯不剥
# ═══════════════════════════════════════════════════════════════

class TestFinalWeightEvidence:
    """gate v083 重跑实况（REPORT-v083-rerun.md B4-evidence）：4191 的
    «Вес: 121 г» 是 100g→121g 体积密度兜底（ensure_volume_weight_floor）后的
    final weight（1688 无重量，毛重 160g 未被采用）——prepare 侧须把该管线内
    真值显式纳入证据集，使锚定闸可溯不剥（比 1688 毛重更接近卡面声明）。"""

    def test_final_weight_collected(self):
        assert "121" in ce.collect_evidence_keys(final_weight_g=121)

    def test_final_dims_collected(self):
        keys = ce.collect_evidence_keys(
            final_dims_mm={"length": 96, "width": 70, "height": 45})
        assert {"96", "70", "45"} <= keys

    def test_guard_adjusted_weight_kept(self):
        """体积兜底后的 121g + 最终尺寸 96×70×45 可溯不剥。"""
        evidence = ce.collect_evidence_keys(
            draft_attrs={}, gross_weight_g=160, final_weight_g=121,
            final_dims_mm={"length": 96, "width": 70, "height": 45},
        )
        html = "<ul><li>Вес: 121 г</li></ul><p>Размеры 96×70×45 мм</p>"
        out, stripped = ce.enforce_number_anchoring(html, evidence)
        assert out == html, f"guard 后真值被剥: {stripped}"
        assert stripped == []

    def test_without_final_truth_guard_number_stripped(self):
        """负向锁：不传管线最终真值（只有 1688 毛重 160）→ 121 仍被剥。

        证明 final_weight_g/final_dims_mm 是会让 guard 调整值可溯的来源，
        而非「恰好已在证据集里」。
        """
        evidence = ce.collect_evidence_keys(draft_attrs={}, gross_weight_g=160)
        out, stripped = ce.enforce_number_anchoring(
            "<ul><li>Вес: 121 г</li></ul>", evidence)
        assert "121" not in out and "121" in stripped

    def test_author_annotation_accepts_final_truth(self):
        """作者链：final_weight_g 透传后 LLM 写的 121 不被剥。"""
        def _llm(system, user, imgs):
            return "<ul><li>Вес: 121 г</li></ul>" + "а" * 600

        html, marks = ce.author_annotation(
            title_ru="Ключница", draft_attrs={}, weight_g=100,
            token="t", llm=_llm, gross_weight_g=160, final_weight_g=121,
            final_dims_mm={"length": 96, "width": 70, "height": 45},
        )
        assert marks["description_source"] == "llm_authored"
        assert "121" in html, "管线最终真值应锚定保留"
        assert "121" not in marks["numbers_stripped"]
