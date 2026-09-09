#!/usr/bin/env python3
"""discover-task --filters 回归（fix round 1 评审确认语义固化）。

覆盖：
① loader：未知顶层/rules 键报错；null/缺省 = 不限；falsy 错误类型 rules 报错；
   同键区间倒置报错；文件不可读 OSError → ValueError（❌ --filters 加载失败 路径）；
② 显式区间闭区间（*_min/*_max 两端含端点）；
③ ai 销量阶梯：450₽ & 月销 400 → 淘汰；450₽ & 600 → 放行；显式 monthly_sales_min
   覆盖/弃用整个阶梯；
④ 优先级：显式 drr_max:20 覆盖 ai 默认 15（未覆盖键仍补齐；lib profile off 只判显式键）；
⑤ sales_schema 白名单 = 子串匹配（与库内 _match_sales_schema 同口径：FBS 含 rFBS，
   FBO 精确，rFBS 只命中 rFBS）；
⑥ ignored_keys：请求键在候选上缺底层数据、一次都未判定 → 汇总上报。

候选统一用合成 dict/_passes 直测 + SimpleNamespace（_apply 就地改 status 用）。

运行：
    cd skill && .venv314/bin/python -m pytest tests/test_discover_filters.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import scripts.cli as cli  # noqa: E402


# ── 夹具 ───────────────────────────────────────────────────────────────────

def _filters(rules=None, schema=None, brand=None, price_min=None,
             price_max=None) -> dict:
    return {"brand": brand, "price_min": price_min, "price_max": price_max,
            "rules": dict(rules or {}), "sales_schema": schema}


def _write_filters(tmp_path, payload) -> str:
    p = tmp_path / "filters.json"
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return str(p)


def _cand(status="ok", has_analytics=True, **fields) -> SimpleNamespace:
    return SimpleNamespace(status=status, has_analytics=has_analytics, **fields)


def _passes(cand, rules=None, profile_ai=False, schema=None):
    rules_list = cli._compose_discover_filter_rules(rules or {}, profile_ai)
    return cli._passes_discover_filters(cand, rules_list, schema)


# ── ① loader ───────────────────────────────────────────────────────────────

class TestLoader:
    def test_unknown_top_key_raises(self, tmp_path):
        p = _write_filters(tmp_path, {"brand": "nobrand", "keywords": ["x"]})
        with pytest.raises(ValueError, match="未知顶层键"):
            cli._load_discover_filters(p)

    def test_unknown_rules_key_raises(self, tmp_path):
        p = _write_filters(
            tmp_path, {"rules": {"monthly_sales_min": 100, "profit_min": 5}})
        with pytest.raises(ValueError, match="未知 rules 键"):
            cli._load_discover_filters(p)

    def test_null_and_missing_mean_unlimited(self, tmp_path):
        p = _write_filters(tmp_path, {"brand": None, "price_min": None,
                                      "rules": {"monthly_sales_min": None,
                                                "drr_max": 15}})
        f = cli._load_discover_filters(p)
        assert f["brand"] is None
        assert f["price_min"] is None and f["price_max"] is None
        assert f["rules"] == {"drr_max": 15.0}       # null 键丢弃 = 不限
        assert f["sales_schema"] is None

    def test_rules_missing_entirely(self, tmp_path):
        p = _write_filters(tmp_path, {"brand": "all"})
        assert cli._load_discover_filters(p)["rules"] == {}

    def test_rules_null_is_unlimited(self, tmp_path):
        p = _write_filters(tmp_path, {"rules": None})
        assert cli._load_discover_filters(p)["rules"] == {}

    def test_rules_falsy_wrong_type_raises(self, tmp_path):
        # 回归：旧实现 `raw.get("rules") or {}` 会把 falsy 错误类型静默吞成 {}
        for bad in (0, "", [], False):
            p = _write_filters(tmp_path, {"rules": bad})
            with pytest.raises(ValueError, match="rules 必须是 JSON 对象"):
                cli._load_discover_filters(p)

    def test_inverted_rule_intervals_raise(self, tmp_path):
        for pair in ({"monthly_sales_min": 200, "monthly_sales_max": 100},
                     {"monthly_revenue_min": 9, "monthly_revenue_max": 8},
                     {"create_days_min": 30, "create_days_max": 10},
                     {"weight_g_min": 500, "weight_g_max": 100}):
            p = _write_filters(tmp_path, {"rules": pair})
            with pytest.raises(ValueError, match="区间倒置"):
                cli._load_discover_filters(p)

    def test_inverted_price_raises(self, tmp_path):
        p = _write_filters(tmp_path, {"price_min": 100, "price_max": 50})
        with pytest.raises(ValueError, match="区间倒置"):
            cli._load_discover_filters(p)

    def test_valid_pair_passes_inversion_check(self, tmp_path):
        p = _write_filters(tmp_path, {"rules": {"weight_g_min": 100,
                                                "weight_g_max": 500}})
        assert cli._load_discover_filters(p)["rules"] == {
            "weight_g_min": 100.0, "weight_g_max": 500.0}

    def test_unreadable_file_raises_valueerror(self, tmp_path):
        # OSError（权限等）也要落进 ❌ --filters 加载失败（ValueError）路径
        p = _write_filters(tmp_path, {"rules": {}})
        with mock.patch.object(Path, "read_text",
                               side_effect=PermissionError(13, "Permission denied")):
            with pytest.raises(ValueError, match="规则文件不可读"):
                cli._load_discover_filters(p)

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(ValueError, match="规则文件不存在"):
            cli._load_discover_filters(str(tmp_path / "nope.json"))

    def test_schema_canonicalized_casefold_dedup(self, tmp_path):
        p = _write_filters(tmp_path, {"rules": {"sales_schema":
                                                ["fbo", "FBS", "FBS"]}})
        assert cli._load_discover_filters(p)["sales_schema"] == ["FBO", "FBS"]


# ── ② 显式区间闭区间 ───────────────────────────────────────────────────────

class TestIntervals:
    def test_inclusive_both_ends(self):
        rules = {"monthly_sales_min": 100, "monthly_sales_max": 200}
        for sales, want in ((100, True), (200, True), (99, False), (201, False)):
            ok, _ = _passes({"monthly_sales": sales, "has_analytics": True}, rules)
            assert ok is want, f"monthly_sales={sales} 应 {'过' if want else '汰'}"

    def test_missing_field_passes(self):
        ok, _ = _passes({"has_analytics": True}, {"monthly_sales_min": 100})
        assert ok  # 字段缺失 = 不限放行（库内 _check_rule 同款）


# ── ③ ai 销量阶梯 ──────────────────────────────────────────────────────────

class TestAiSalesLadder:
    def _apply(self, cands, rules):
        return cli._apply_discover_filters(cands, _filters(rules), True)

    def test_ladder_present_by_default(self):
        composed = cli._compose_discover_filter_rules({}, profile_ai=True)
        assert any(k == cli._LADDER_KEY for k, *_ in composed)

    def test_ladder_filters_450_400(self):
        c = _cand(ozon_price=450.0, monthly_sales=400)
        n, _ = self._apply([c], {})
        assert n == 1
        assert c.status == "filtered" and "销量阶梯" in c.error

    def test_ladder_passes_450_600(self):
        c = _cand(ozon_price=450.0, monthly_sales=600)
        n, _ = self._apply([c], {})
        assert n == 0 and c.status == "ok"

    def test_ladder_strict_upper_bound_of_tier(self):
        # 阶梯原生严格比较：500₽ 档需月销 > 500（500 不过、501 过）
        c1 = _cand(ozon_price=500.0, monthly_sales=500)
        c2 = _cand(ozon_price=500.0, monthly_sales=501)
        n, _ = self._apply([c1, c2], {})
        assert n == 1 and c1.status == "filtered" and c2.status == "ok"

    def test_explicit_monthly_sales_min_drops_ladder(self):
        rules = {"monthly_sales_min": 100}
        composed = cli._compose_discover_filter_rules(rules, profile_ai=True)
        assert not any(k == cli._LADDER_KEY for k, *_ in composed)
        assert ("monthly_sales_min", "monthly_sales", ">=", 100.0) in composed
        # 450₽ & 400：有阶梯必汰；显式 monthly_sales_min=100 后放行
        c = _cand(ozon_price=450.0, monthly_sales=400)
        n, _ = self._apply([c], rules)
        assert n == 0 and c.status == "ok"


# ── ④ 显式键覆盖 ai 默认（与 lib profile off 组合）────────────────────────

class TestAiPrecedence:
    def test_explicit_drr_max_20_overrides_ai_15(self):
        c_pass = _cand(drr=18)     # ai 默认 15 会汰，显式 20 放行
        c_fail = _cand(drr=25)
        n, _ = cli._apply_discover_filters(
            [c_pass, c_fail], _filters({"drr_max": 20}), True)
        assert n == 1
        assert c_pass.status == "ok" and c_fail.status == "filtered"
        assert "drr" in c_fail.error

    def test_ai_default_drr_15_without_override(self):
        c = _cand(drr=18)
        n, _ = cli._apply_discover_filters([c], _filters({}), True)
        assert n == 1 and c.status == "filtered"

    def test_compose_with_lib_profile_off_judges_explicit_only(self):
        # lib profile off（ai 覆盖时库内档位关闭）：cli 侧只按显式键判定
        composed = cli._compose_discover_filter_rules({"drr_max": 20}, False)
        assert composed == [("drr_max", "drr", "<=", 20.0)]

    def test_unoverridden_ai_defaults_still_applied(self):
        composed = cli._compose_discover_filter_rules({"drr_max": 20}, True)
        keys = {k for k, *_ in composed}
        assert {"create_days_max", "competing_sellers_max", "sales_growth_min",
                cli._LADDER_KEY} <= keys
        assert ("drr_max", "drr", "<=", 20.0) in composed  # 显式值替换默认


# ── ⑤ sales_schema 白名单 = 子串匹配（库内同口径）─────────────────────────

class TestSalesSchema:
    def _run(self, schema, whitelist, has_analytics=True):
        ok, reason = _passes({"sales_schema": schema,
                              "has_analytics": has_analytics}, schema=whitelist)
        return ok, reason

    def test_fbs_request_covers_rfbs(self):
        ok, _ = self._run("rFBS", ["FBS"])
        assert ok  # FBS 子串命中 rFBS（.includes() 同款）

    def test_fbo_request_exact(self):
        ok, _ = self._run("FBO,FBS", ["FBO"])
        assert ok
        ok, _ = self._run("rFBS", ["FBO"])
        assert not ok

    def test_rfbs_request_matches_rfbs_only(self):
        ok, _ = self._run("rFBS", ["rFBS"])
        assert ok
        ok, _ = self._run("FBS", ["rFBS"])
        assert not ok

    def test_comma_joined_schema_hits_any_request_mode(self):
        ok, _ = self._run("FBO,FBS", ["FBS"])
        assert ok

    def test_empty_schema_with_request_filters(self):
        ok, reason = self._run("", ["FBO"])
        assert not ok and "FBO" in reason

    def test_no_analytics_degrades_pass(self):
        ok, _ = self._run("", ["FBO"], has_analytics=False)
        assert ok


# ── ⑥ ignored_keys ─────────────────────────────────────────────────────────

class TestIgnoredKeys:
    def test_keys_without_data_reported(self):
        c = _cand()  # 无 monthly_sales / drr 字段
        n, ignored = cli._apply_discover_filters(
            [c], _filters({"monthly_sales_min": 100, "drr_max": 15}), False)
        assert n == 0 and c.status == "ok"
        assert ignored == ["drr_max", "monthly_sales_min"]

    def test_evaluable_keys_not_ignored(self):
        c = _cand(monthly_sales=50)
        n, ignored = cli._apply_discover_filters(
            [c], _filters({"monthly_sales_min": 100}), False)
        assert n == 1 and ignored == []

    def test_schema_ignored_without_analytics(self):
        c = _cand(has_analytics=False)
        n, ignored = cli._apply_discover_filters(
            [c], _filters(schema=["FBO"]), False)
        assert n == 0 and ignored == ["sales_schema"]

    def test_error_status_untouched(self):
        c = _cand(status="error", monthly_sales=0)
        n, _ = cli._apply_discover_filters(
            [c], _filters({"monthly_sales_min": 100}), False)
        assert n == 0 and c.status == "error"
