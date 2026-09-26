#!/usr/bin/env python3
"""fix/category-root-cause-v1 回归：search_kw 同形异义毒猜两道闸收紧 + divergent 信号透传。

实机 gate 取证（2026-09，docs/ARCHITECTURE/09-findings.md）两个 skill 侧根因：

(a) search_kw 猜测被 1688 类目词同形异义毒中：«去核器»商品猜到 «切面器»
    （=压面机）、«多功能切菜器»猜到 «多功能造型器»（=美发造型器）。
    `_category_guess_consistent` 两道自校验被击穿：
      - R1 bigram 覆盖 0 后单字回退：«切/器»共字放行（阈值 0.34）；
      - R2 尾字核对：«器==器»（同为泛尾字）恒过。
    收紧：回退命中须共享字含非泛尾字且 guess 尾字非泛尾字；R2 须末两字
    bigram 共现或与证据尾字同字且非泛尾字。F-B04 原意图（«保暖杯»×«保温杯»
    音近词救回）保留为正例锚点。

(b) divergent 信号死在候选对象上：`ozon_discovery._category_semantic_review`
    命中分歧置 match_category_divergent=True + match_confidence 封顶 0.5，
    但该字段不在任何透传面（CSV/Excel 导出、discovery_runs 上报、信封
    extensions.discovery_meta）→ worker 侧永远看不到。本批四面接线；
    match_evidence.confidence 的 0.5 封顶语义随既有 match_confidence 键走
    （同一候选对象同源同值），不重复透传。

运行：
    cd skill && .venv314/bin/python -m pytest tests/test_category_root_cause_v081.py -q
"""
from __future__ import annotations

import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts import cloud_probe  # noqa: E402
from scripts.lib.ozon_discovery import ProductCandidate  # noqa: E402

_guess_ok = cloud_probe._category_guess_consistent


# ── (a) 同形异义毒猜四组正反例 ────────────────────────────────────────────


class TestHomonymPoisonGate:
    """R1 单字回退收紧 + R2 尾字核对加严（泛尾字集合 _GENERIC_TAIL_CHARS）。"""

    def test_pitter_vs_sheeter_shared_generic_tail_only(self):
        """①去核器×切面器 拒：共享字只有泛尾字「器」。

        实机取证还原：1688 去核器商品，search_kw 猜中 «切面器»（=压面机）。
        旧口径 R1 单字回退 «切/器»共字放行（2/3 ≥ 0.34）+ R2 «器»在证据
        任意位置 → 毒类目照写。收紧后：回退 ②guess 尾字 泛 → 拒。
        """
        assert _guess_ok(
            "切面器", "多功能切果器 樱桃去核器", "餐厨工具 > 切果器") is False

    def test_sheeter_vs_fruit_cutter_r2_generic_same_tail(self):
        """②切面器×切果器 拒（R2 加严独立拦截）：即便 R1 回退过了（共享
        切 非泛），R2 也不再把「尾字同为泛尾字 器」当强一致。

        旧口径：«切面器» 尾字 器 vs 源 «切果器» 尾字 器 → 器在证据里 → 过；
        新口径：bigram 面器 不在证据 + 器 是泛尾字 → 同字不算品类证据 → 拒。
        """
        assert _guess_ok(
            "切面器", "切果器 多功能", "餐厨工具 > 切果器") is False

    def test_multifunction_cutter_vs_styler_r1_main_path_still_r2_blocked(self):
        """③多功能切菜器×多功能造型器 拒：R1 主路径 bigram 命中（多功/功能
        2/5 ≥ 0.15，不经回退），旧 R2 «器»在证据 → 放行毒类目；新 R2
        「bigram 型器 不在证据 + 尾字 器 泛」→ 拒（=美发造型器，跨品类）。"""
        assert _guess_ok(
            "多功能造型器", "多功能切菜器 家用", "厨房工具 > 切菜器") is False

    def test_thermos_near_synonym_anchor_still_passes(self):
        """④保温杯×保暖杯 过（F-B04 锚点，收紧不得回退错杀）：共享 保 非泛、
        尾字 杯 非泛 → R1 回退放行 + R2 走 B（同字且非泛）放行。"""
        assert _guess_ok(
            "保暖杯", "Термос 0.5л для напитков",
            "日用餐厨饮具 > 饮水用具 > 保温杯") is True

    def test_normal_guess_still_passes(self):
        """正常词零影响：猜中类目与证据本一致 → True（bigram 强一致）。"""
        assert _guess_ok(
            "多功能切菜器", "多功能切菜器 家用", "厨房工具 > 切菜器") is True

    def test_generic_tail_with_bigram_evidence_still_passes(self):
        """泛尾字类目不误杀：尾字 泛 但 bigram 属桶 在 source 末段出现
        （金属桶锚点正例，R2 走 A）→ True。"""
        assert _guess_ok(
            "金属桶", "加厚汽油桶铁桶手提 20L Канистра",
            "包装 > 金属包装容器 > 金属桶") is True


# ── (b) divergent 信号四面透传 ────────────────────────────────────────────


def _cand(**kw) -> ProductCandidate:
    c = ProductCandidate(
        ozon_product_id="p1", ozon_title="Термос", ozon_price=1500.0)
    c.status = "ok"
    c.match_confidence = 0.945
    c.match_1688_category_name = "日用餐厨饮具 > 咖啡具 > 咖啡杯"
    c.page_category_path = "Дом и сад > Посуда > Термосы"
    for k, v in kw.items():
        setattr(c, k, v)
    return c


class TestDivergentTransmission:
    """match_category_divergent 此前死在候选对象上；本批接齐导出/上报/信封三面。"""

    def test_discovery_meta_emits_flag_only_when_true(self):
        """信封面：_assemble_discovery_meta 仅 True 落键（非 True 键省略）。"""
        from scripts.cloud_probe import _assemble_discovery_meta
        divergent = _assemble_discovery_meta(_cand(match_category_divergent=True))
        assert divergent.get("match_category_divergent") is True
        normal = _assemble_discovery_meta(_cand())
        assert "match_category_divergent" not in normal, "默认 False 不写键"

    def test_discovery_meta_conf_cap_flows_via_existing_confidence(self):
        """封顶语义不重复透传：分歧候选 match_confidence 已被
        _category_semantic_review 封顶 0.5，随既有 match_confidence 键
        （与 match_evidence.confidence 同源）走——本断言锁定同源同值。"""
        cand = _cand()
        cand.match_confidence = min(cand.match_confidence, 0.5)  # 复核封顶后形态
        from scripts.cloud_probe import _assemble_match_evidence, _assemble_discovery_meta
        meta = _assemble_discovery_meta(cand)
        mev = _assemble_match_evidence(confidence=cand.match_confidence)
        assert meta["match_confidence"] == 0.5
        assert mev["confidence"] == 0.5
        # 无 _capped/_divergent_conf 之类重复透传键
        assert not any(k.endswith(("_capped", "_conf")) for k in meta)

    def test_csv_row_and_fields_tail_appended(self):
        """导出面：CSV 列尾追加 + 行值仅 True 落值（非 True 空串）。"""
        from scripts.lib.ozon_discovery import _EXPORT_FIELDS, _candidate_row
        assert _EXPORT_FIELDS[-1] == "match_category_divergent", "列序契约：尾追加"
        assert _candidate_row(_cand(match_category_divergent=True))[
            "match_category_divergent"] is True
        assert _candidate_row(_cand())["match_category_divergent"] == ""

    def test_xlsx_zone_covers_flag(self):
        """xlsx 四区字段集与 CSV 一致（test_discovery_export_xlsx 锁定的不变式）。"""
        from scripts.lib.ozon_discovery import _EXPORT_FIELDS, _EXPORT_XLSX_ZONES
        keys = [k for _, cols in _EXPORT_XLSX_ZONES for k, _ in cols]
        assert sorted(keys) == sorted(_EXPORT_FIELDS)
        assert keys.count("match_category_divergent") == 1

    def _report(self, cand):
        with mock.patch("scripts.lib.config_store.get_mxou_token",
                        return_value="sk-test"), \
             mock.patch("requests.post") as mock_post:
            from scripts.lib import ozon_discovery as od
            od._report_discovery_run("термос", None, [cand])
            mock_post.assert_called_once()
            return mock_post.call_args.kwargs["json"]["candidates"][0]

    def test_report_emits_flag_only_when_true(self):
        """上报面：REPORT_FIELDS 白名单 + 非 True 不写（默认 False 键省略）。"""
        from scripts.lib import ozon_discovery as od
        assert "match_category_divergent" in od.REPORT_FIELDS
        row = self._report(_cand(match_category_divergent=True))
        assert row["match_category_divergent"] is True
        normal_row = self._report(_cand())
        assert "match_category_divergent" not in normal_row, "默认 False 键省略"

    def test_local_json_dump_carries_flag(self):
        """本地落盘面：asdict 整包 → 分歧标记自然进 discovery_*.json（不裁剪）。"""
        import dataclasses
        assert "match_category_divergent" in dataclasses.asdict(_cand())


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
