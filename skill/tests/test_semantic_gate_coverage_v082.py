#!/usr/bin/env python3
"""fix/semantic-gate-coverage v082 — 图搜语义分歧信号出闸 + 三消费闸（TDD）。

审计锚点：v0.81 follow 语义闸（_pick_best_match require_category_consistency）
只覆盖 follow 一条腿——discover/batch_test/--auto-submit 入口的错货信封
（Ozon 竞品卡=A、1688 匹配到语义不符的 B）无任何拦截：
  a) _category_semantic_review 前提数据缺失静默 return（未复核≠已复核通过）；
  b) match_category_divergent 死在候选对象上，不进 extensions.match_evidence，
     worker 永远看不到分歧信号；
  c) batch_test._find_discover_source / _submit_gate_rejection / discover
     --auto-submit 对 divergent（conf 封顶 0.5 穿透 0.3 硬门）与 unknown 零消费。

本文件锁定四段行为：
  ① 信号出闸：_category_semantic_review divergent/semantic_unknown 候选级标记
     + _pick_best_match 降级 LLM 确认放行带 match_semantic_unknown（面包屑缺席
     fallback 的完整形态见 test_follow_match_semantic_gate_v081）；
  ② match_evidence：_assemble_match_evidence 写 divergent/semantic_unknown 键
     （True 才写，缺失省略；纯布尔标记非空壳）+ build_envelope_from_discovery
     与 follow 两装配口真实接线；
  ③ batch_test：_find_discover_source 分歧/未复核源不复用（打印原因行）+
     _submit_gate_rejection 拒绝分支（0.5 穿透 conf 闸的场景由新分支兜住）；
  ④ cli --auto-submit：divergent/unknown 候选排除（保留可见不硬删）。

运行:
    cd skill && .venv314/bin/python -m pytest tests/test_semantic_gate_coverage_v082.py -q
全部纯 mock，不触网、不跑 CDP。
"""
from __future__ import annotations

import argparse
import io
import sys
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import scripts.cloud_probe as cloud_probe  # noqa: E402
import scripts.lib.ozon_discovery as od  # noqa: E402
from scripts.lib.ozon_discovery import ProductCandidate  # noqa: E402

_RU = "Перчатки хозяйственные резиновые"
_CRUMB = "Дом и сад > Домашние перчатки"
_ZH_PATH = "家居日用 > 清洁用品 > 家务手套"
_KEY = ("category", _CRUMB[:60], _ZH_PATH[:60])


def _candidate(**kw) -> ProductCandidate:
    c = ProductCandidate(ozon_product_id="p1", ozon_title="自动饮用水器",
                         ozon_price=1500.0)
    c.match_1688_url = "https://detail.1688.com/offer/1001.html"
    c.match_1688_title = "家用橡胶手套"
    for k, v in kw.items():
        setattr(c, k, v)
    return c


# ═══════════════════════════════════════════════════════════════════════
# ① 信号出闸（候选级）
# ═══════════════════════════════════════════════════════════════════════

class TestCategorySemanticReview:
    def test_divergent_sets_flag_and_caps_conf(self, monkeypatch):
        """LLM 实锤分歧（缓存命中真 NO）→ match_category_divergent + conf 封顶 0.5。"""
        c = _candidate(page_category_path=_CRUMB, match_1688_category_name=_ZH_PATH,
                       match_confidence=0.945)
        od._LLM_SEMANTIC_CACHE[_KEY] = False  # 预置真 NO（区分 LLM 调用失败）
        try:
            od._category_semantic_review(c, "tok")
        finally:
            od._LLM_SEMANTIC_CACHE.pop(_KEY, None)
        assert c.match_category_divergent is True
        assert c.match_confidence == 0.5
        assert c.match_semantic_unknown is False

    def test_missing_premise_marks_semantic_unknown(self, monkeypatch):
        """前提数据缺失（token 缺）→ semantic_unknown=True（此前静默放行）。"""

        def _no_llm(*a, **k):
            raise AssertionError("前提缺失不得调 LLM")

        monkeypatch.setattr(od, "_llm_semantic_match", _no_llm)
        c = _candidate(page_category_path=_CRUMB, match_1688_category_name=_ZH_PATH,
                       match_confidence=0.9)
        od._category_semantic_review(c, "")  # 无 token
        assert c.match_semantic_unknown is True
        assert c.match_category_divergent is False

    def test_missing_breadcrumb_marks_semantic_unknown(self, monkeypatch):
        """竞品面包屑缺席（CDP 降级）→ semantic_unknown=True。"""
        c = _candidate(page_category_path="", match_1688_category_name=_ZH_PATH,
                       match_confidence=0.9)
        od._category_semantic_review(c, "tok")
        assert c.match_semantic_unknown is True

    def test_consistent_leaves_flags_clean(self, monkeypatch):
        """LLM 真 YES（缓存命中）→ 双标记全清。"""
        c = _candidate(page_category_path=_CRUMB, match_1688_category_name=_ZH_PATH,
                       match_confidence=0.9)
        od._LLM_SEMANTIC_CACHE[_KEY] = True
        try:
            od._category_semantic_review(c, "tok")
        finally:
            od._LLM_SEMANTIC_CACHE.pop(_KEY, None)
        assert c.match_category_divergent is False
        assert c.match_semantic_unknown is False


class TestPickBestMatchFallbackFlag:
    def test_fallback_confirmed_pick_carries_semantic_unknown(self, monkeypatch):
        """面包屑缺席 + 降级 LLM 确认放行 → match_semantic_unknown=True 出闸
        （消费方 _assemble_match_evidence 据此写 extensions.match_evidence）。"""

        def _fake_llm(ru, cn, token="", mode="product"):
            return mode == "category" and "家务" in cn

        monkeypatch.setattr(od, "_llm_semantic_match", _fake_llm)
        cand = {"id": "offer1", "title": "家用橡胶手套厨房清洁", "price": 9.9,
                "image": "https://cbu01.alicdn.com/img/ibank/x.jpg", "badge": "",
                "normalization_score": 0.8, "category_name": "家务清洁"}
        best = od._pick_best_match([cand], _RU, token="tok", trusted_source=True,
                                   require_category_consistency=True)
        assert best is not None
        assert best.get("match_semantic_unknown") is True
        assert "match_category_divergent" not in best  # 非 True 键省略


# ═══════════════════════════════════════════════════════════════════════
# ② match_evidence 装配（数据面出闸）
# ═══════════════════════════════════════════════════════════════════════

class TestAssembleMatchEvidence:
    def test_flags_written_when_true(self):
        mev = cloud_probe._assemble_match_evidence(
            method="aibuy", confidence=0.5, badge_eff=0,
            divergent=True, semantic_unknown=True)
        assert mev["divergent"] is True and mev["semantic_unknown"] is True
        assert mev["trusted"] is True and mev["method"] == "aibuy"

    def test_flags_omitted_when_false(self):
        mev = cloud_probe._assemble_match_evidence(method="cdp", confidence=0.4)
        assert "divergent" not in mev and "semantic_unknown" not in mev

    def test_flag_only_dict_is_not_a_shell(self):
        """conf/badge 全零但 divergent=True → 非空（携带真实信号不是空壳）。"""
        mev = cloud_probe._assemble_match_evidence(divergent=True)
        assert mev.get("divergent") is True
        assert mev.get("trusted") is False  # 无 conf/badge 信号 → 无直通依据

    def test_no_signals_no_flags_returns_empty(self):
        assert cloud_probe._assemble_match_evidence() == {}


class TestEnvelopeMatchEvidenceWiring:
    def _build(self, cand: ProductCandidate):
        envelope_result = {
            "ozon_client_id": "123", "ozon_api_key": "key",
            "envelope": {"draft": {"item_id": "1001", "title": "家用橡胶手套",
                                   "images": ["https://cbu01.alicdn.com/x.jpg"]},
                         "source": {}, "extensions": {}},
        }
        with mock.patch.object(cloud_probe, "build_graph_envelope_with_retry",
                               return_value=envelope_result), \
             mock.patch.object(cloud_probe, "_discover_page_truth",
                               return_value={}), \
             mock.patch("scripts.lib.config_store.get_mxou_token",
                        return_value="sk-test"), \
             mock.patch("scripts.lib.config_store.get_store_profile",
                        return_value={}):
            return cloud_probe.build_envelope_from_discovery(
                cand, {"client_id": "123", "api_key": "key"})

    def test_discover_envelope_carries_divergent(self):
        """discover 信封：divergent 候选 → extensions.match_evidence.divergent=True。"""
        c = _candidate(match_confidence=0.5, match_badge_eff=0.8,
                       match_category_divergent=True)
        env = self._build(c)
        mev = env["envelope"]["extensions"]["match_evidence"]
        assert mev["divergent"] is True
        assert "semantic_unknown" not in mev

    def test_discover_envelope_carries_semantic_unknown(self):
        c = _candidate(match_confidence=0.5,
                       match_semantic_unknown=True)
        env = self._build(c)
        mev = env["envelope"]["extensions"]["match_evidence"]
        assert mev["semantic_unknown"] is True
        assert "divergent" not in mev

    def test_discover_meta_carries_semantic_unknown_not_export_column(self):
        """semantic_unknown 进 discovery_meta 快照（采集箱可见），不加导出列。"""
        c = _candidate(match_semantic_unknown=True)
        meta = cloud_probe._assemble_discovery_meta(c)
        assert meta.get("match_semantic_unknown") is True
        assert "match_category_divergent" not in meta  # 非 True 不写
        assert "match_semantic_unknown" not in od._EXPORT_FIELDS

    def test_follow_envelope_carries_semantic_unknown_from_best(self):
        """follow 装配口：best（_pick_best_match 降级确认放行）的 unknown 标记
        → extensions.match_evidence.semantic_unknown（dict 形状单测，不触 CDP）。"""
        best = {"confidence": 0.5, "badge_eff": 0.0,
                "match_semantic_unknown": True, "category_id": "1047",
                "category_name": "家务清洁"}
        mev = cloud_probe._assemble_match_evidence(
            method="aibuy",
            confidence=best.get("confidence"),
            badge_eff=best.get("badge_eff"),
            divergent=bool(best.get("match_category_divergent")),
            semantic_unknown=bool(best.get("match_semantic_unknown")),
        )
        assert mev["semantic_unknown"] is True and "divergent" not in mev


# ═══════════════════════════════════════════════════════════════════════
# ③ batch_test 消费闸
# ═══════════════════════════════════════════════════════════════════════

class TestBatchTestGates:
    def _cache(self, cands: list[dict]):
        return mock.patch("scripts.lib.ozon_discovery.load_latest_discovery",
                          return_value=cands)

    @staticmethod
    def _entry(**kw) -> dict:
        e = {"ozon_product_id": "111", "status": "profitable",
             "match_1688_url": "https://detail.1688.com/offer/1.html"}
        e.update(kw)
        return e

    def test_divergent_source_not_reused(self, capsys):
        with self._cache([self._entry(match_category_divergent=True)]):
            assert __import__("batch_test")._find_discover_source("111") is None
        assert "语义分歧" in capsys.readouterr().out

    def test_semantic_unknown_source_not_reused(self, capsys):
        import batch_test
        with self._cache([self._entry(match_semantic_unknown=True)]):
            assert batch_test._find_discover_source("111") is None
        assert "语义未复核" in capsys.readouterr().out

    def test_clean_source_still_reused(self):
        import batch_test
        with self._cache([self._entry()]):
            assert batch_test._find_discover_source("111") is not None

    def test_gate_rejects_divergent(self):
        import batch_test
        c = _candidate(match_confidence=0.5, match_category_divergent=True)
        env = {"envelope": {"draft": {"title": "家用橡胶手套"}}}
        reason = batch_test._submit_gate_rejection(c, env)
        assert reason and "语义分歧" in reason

    def test_gate_rejects_semantic_unknown(self):
        import batch_test
        c = _candidate(match_confidence=0.5, match_semantic_unknown=True)
        env = {"envelope": {"draft": {"title": "家用橡胶手套"}}}
        reason = batch_test._submit_gate_rejection(c, env)
        assert reason and "语义未复核" in reason

    def test_gate_passes_conf05_clean_candidate(self):
        """回归锚：conf=0.5（trusted 放行封顶值）的干净候选原样放行——
        新分支只拦分歧/未复核，不扩大到普通候选。"""
        import batch_test
        c = _candidate(match_confidence=0.5)
        env = {"envelope": {"draft": {"title": "家用橡胶手套"}}}
        assert batch_test._submit_gate_rejection(c, env) == ""


# ═══════════════════════════════════════════════════════════════════════
# ④ discover --auto-submit 排除
# ═══════════════════════════════════════════════════════════════════════

def _profitable(pid="p1", **kw) -> ProductCandidate:
    c = ProductCandidate(ozon_product_id=pid, ozon_title="Перчатки " + pid,
                         ozon_price=1500.0)
    c.status = "profitable"
    c.match_1688_url = f"https://detail.1688.com/offer/{pid}.html"
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def _run_auto_submit(candidates, submitted: list):
    """最小 discover 驱动（对齐 test_cli_auto_submit_parallel 口径）。"""
    from scripts import cli
    args = argparse.Namespace(
        url="", keyword="перчатки", max_products=50, min_margin=15.0,
        fx_rate=0.075, store="", no_analytics=False, min_price=0, max_price=0,
        brand_filter="nobrand", rules="monthly_sales>=1", export="", output="",
        auto_submit=True, fission=False, max_depth=2, allow_depth_3=False,
        max_total_products=300, time_budget=600.0, max_sellers_per_product=20,
        max_products_per_seller=15, non_interactive=False,
        blue_ocean_source="", blue_ocean_csv="", china=False, local=False,
    )
    patches = [
        mock.patch("scripts.lib.chrome_launcher.ensure_chrome_cdp",
                   return_value=(True, "ok")),
        mock.patch("scripts.lib.ozon_discovery.collect_and_analyze",
                   return_value=candidates),
        mock.patch("scripts.lib.ozon_discovery.apply_selection_rules",
                   return_value=candidates),
        mock.patch("scripts.lib.ozon_discovery.match_selected"),
        mock.patch("scripts.lib.config_store.get_mxou_token", return_value=""),
        mock.patch("scripts.lib.config_store.get_store_profile", return_value={}),
        mock.patch("scripts.lib.config_store.get_store", return_value={}),
        mock.patch("builtins.input", return_value="y"),
        mock.patch("scripts.cloud_probe.build_envelope_from_discovery",
                   side_effect=lambda c, sc, store_id="": {
                       "draft": {"title": c.ozon_title,
                                 "item_id": c.ozon_product_id}}),
        mock.patch("scripts.cloud_probe.submit_envelope",
                   side_effect=lambda env: (
                       submitted.append(env["draft"]["item_id"]) or
                       {"ok": True, "task_id": f"T-{env['draft']['item_id']}"})),
    ]
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            rc = cli.cmd_discover(args)
    return rc, out.getvalue()


class TestAutoSubmitExcludesDivergent:
    def test_divergent_and_unknown_excluded_clean_submitted(self):
        submitted: list = []
        c_clean = _profitable("p_ok")
        c_div = _profitable("p_div", match_category_divergent=True)
        c_unk = _profitable("p_unk", match_semantic_unknown=True)
        rc, out = _run_auto_submit([c_clean, c_div, c_unk], submitted)
        assert rc == 0
        assert submitted == ["p_ok"], f"只提交干净候选, got {submitted}"
        assert "语义分歧" in out and "语义未复核" in out
        assert "⛔ 自动提交排除" in out

    def test_all_excluded_reports_no_candidates(self):
        submitted: list = []
        c_div = _profitable("p_div", match_category_divergent=True)
        rc, out = _run_auto_submit([c_div], submitted)
        assert rc == 0
        assert submitted == []
        assert "没有符合条件的 profitable 产品可提交" in out


if __name__ == "__main__":
    import traceback

    failed = total = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            total += 1
            try:
                fn()
            except Exception:
                failed += 1
                traceback.print_exc()
    for _cls in (TestCategorySemanticReview, TestPickBestMatchFallbackFlag,
                 TestAssembleMatchEvidence, TestEnvelopeMatchEvidenceWiring,
                 TestBatchTestGates, TestAutoSubmitExcludesDivergent):
        for _n, _f in sorted(vars(_cls).items()):
            if _n.startswith("test_") and callable(_f):
                total += 1
                try:
                    _f(None)
                except Exception:
                    failed += 1
                    traceback.print_exc()
    print(f"{total - failed}/{total} passed")
    sys.exit(1 if failed else 0)
