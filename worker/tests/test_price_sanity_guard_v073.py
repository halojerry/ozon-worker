# -*- coding: utf-8 -*-
"""v0.73 价差守卫 + ingest 空标题闸测试（Issue5b worker 侧防线）。

三块：
1. utils/price_sanity_guard.check_price_sanity 纯函数（锚价/回退/边界/env 覆盖）；
2. pricing_node 接线（block → [PRICING_FAILED] failed 出口 + 入采集箱；
   warn → 放行 + pricing_info.price_gap_warn；无锚 → 零误杀照常定价）；
3. ingest 空标题闸（draft.title 空/空白 → fail-fast + route_after_ingest END）。

生产实证背景：¥1.5 塑料转盘错配 3418₽ 锚 approved 卖 35₽（该例 discovery_meta
为空，守卫拦不到 graph 流本例——skill 侧防线另行任务）；本守卫只保「带锚」流。

运行：cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_price_sanity_guard_v073.py -q
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from utils.price_sanity_guard import (
    BLOCK_RATIO,
    WARN_RATIO,
    check_price_sanity,
    get_ratios,
)
from graphs.nodes import ingest_node as ingest_mod
from graphs.nodes import pricing_node as pn


# ══════════════ 1. 纯函数 check_price_sanity ══════════════

# ── 生产转盘本例形状：锚 3418₽ 终价 35₽（97.7×）→ block ──
def test_block_production_case():
    verdict, ev = check_price_sanity(35, {"ozon_price": 3418})
    assert verdict == "block"
    assert ev["anchor_price"] == 3418
    assert ev["anchor_source"] == "ozon_price"
    assert ev["final_price"] == 35
    assert ev["ratio"] >= BLOCK_RATIO


# ── warn 档：锚 3418 终价 800（4.3×，介于 3~10）──
def test_warn_mid_band():
    verdict, ev = check_price_sanity(800, {"ozon_price": 3418})
    assert verdict == "warn"
    assert WARN_RATIO <= ev["ratio"] < BLOCK_RATIO


# ── 边界：恰 10× → block；恰 3× → warn；2.9× → ok ──
def test_boundary_exact_10x_is_block():
    verdict, ev = check_price_sanity(100, {"ozon_price": 1000})
    assert verdict == "block"
    assert ev["ratio"] == 10.0


def test_boundary_exact_3x_is_warn():
    verdict, ev = check_price_sanity(100, {"ozon_price": 300})
    assert verdict == "warn"
    assert ev["ratio"] == 3.0


def test_below_warn_is_ok():
    verdict, ev = check_price_sanity(100, {"ozon_price": 290})
    assert verdict == "ok"
    # ok 档也带 evidence（审计留痕），锚在场
    assert ev["anchor_price"] == 290


# ── 无锚场景（graph 流 discovery_meta 空 = 生产转盘本例）→ 恒 ok 零误杀 ──
def test_no_anchor_empty_meta():
    assert check_price_sanity(35, {}) == ("ok", {})


def test_no_anchor_none_meta():
    assert check_price_sanity(35, None) == ("ok", {})


def test_invalid_anchor_zero_ignored():
    assert check_price_sanity(35, {"ozon_price": 0}) == ("ok", {})


def test_invalid_anchor_negative_and_garbage_ignored():
    assert check_price_sanity(35, {"ozon_price": -5}) == ("ok", {})
    assert check_price_sanity(35, {"ozon_price": "3418"}) == ("ok", {})  # 字符串不当锚
    assert check_price_sanity(35, {"ozon_price": True}) == ("ok", {})  # bool 不当锚


# ── 回退锚：ozon_price 缺 → min_competing_price ──
def test_fallback_min_competing_price():
    verdict, ev = check_price_sanity(35, {"min_competing_price": 3418})
    assert verdict == "block"
    assert ev["anchor_source"] == "min_competing_price"


def test_ozon_price_wins_over_competing():
    # ozon_price 在场且合法 → 不看 min_competing_price（3.4× 只 warn）
    verdict, ev = check_price_sanity(1000, {"ozon_price": 3400, "min_competing_price": 34000})
    assert verdict == "warn"
    assert ev["anchor_source"] == "ozon_price"


# ── final_price<=0 是定价层 bug，守卫不越界 → ok ──
def test_final_price_non_positive_not_our_business():
    assert check_price_sanity(0, {"ozon_price": 3418}) == ("ok", {})
    assert check_price_sanity(-5, {"ozon_price": 3418}) == ("ok", {})


# ── env 覆盖：BLOCK_RATIO 2.0 把 warn 档抬成 block；非法 env 回落默认 ──
def test_env_override_block_ratio(monkeypatch):
    # 4× 默认 warn；BLOCK_RATIO=2 → block
    monkeypatch.setenv("PRICE_GAP_BLOCK_RATIO", "2")
    assert check_price_sanity(100, {"ozon_price": 400})[0] == "block"


def test_env_override_warn_ratio(monkeypatch):
    # 4× 默认 warn；WARN_RATIO=5 → ok
    monkeypatch.setenv("PRICE_GAP_WARN_RATIO", "5")
    assert check_price_sanity(100, {"ozon_price": 400})[0] == "ok"


def test_env_invalid_falls_back_to_defaults(monkeypatch):
    monkeypatch.setenv("PRICE_GAP_BLOCK_RATIO", "abc")
    monkeypatch.setenv("PRICE_GAP_WARN_RATIO", "0")
    assert get_ratios() == (BLOCK_RATIO, WARN_RATIO)


# ══════════════ 2. pricing_node 接线 ══════════════

def _make_state(envelope=None, user_id="u-1", draft=None, extensions=None):
    """pricing_node 可直接消费的 state（SimpleNamespace，对齐 test_pricing_node_commission）。"""
    return SimpleNamespace(
        draft=draft
        or {
            "cost_cny": 5.5,
            "purchase_cost": 5.5,
            "weight": 227,
            "dimensions": {"length": 120, "width": 80, "height": 60},
        },
        extensions=extensions or {},
        supabase_url="http://supabase.local",
        supabase_key="k",
        currency_code="RUB",
        ozon_client_id="1",
        ozon_api_key="k",
        description_category_id="17028830",
        task_id="",
        tenant_id="",
        token="sk-test",
        user_id=user_id,
        envelope=envelope or {},
        variants=[],
        error_message="",
        pricing_info={},
    )


class _DummyRuntime:
    context = None


def _call_pricing(monkeypatch, state):
    """mock 重依赖后调用 pricing_node（纯内存，无 PG/HTTP）。"""
    from utils import logistics_quote

    monkeypatch.setattr(
        logistics_quote, "query_logistics_cost",
        lambda *a, **k: (10.0, "mock_channel", {}),
    )
    monkeypatch.setattr(
        logistics_quote, "get_store_logistics_config",
        lambda *a, **k: ("RETS", "Standard"),
    )
    monkeypatch.setattr(pn, "_get_exchange_rate", lambda *a, **k: 12.0)
    monkeypatch.setattr(pn, "get_category_commission", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(pn, "ozon_post", lambda *a, **k: {}, raising=False)
    return pn.pricing_node(state, None, _DummyRuntime())


def _envelope_with_anchor(anchor):
    return {"extensions": {"discovery_meta": {"ozon_price": anchor}}}


def test_pricing_no_anchor_untouched(monkeypatch):
    """无锚（graph 流 discovery_meta 空）→ 照常定价零误杀，无 warn 标记。"""
    out = _call_pricing(monkeypatch, _make_state(envelope={"extensions": {}}))
    assert out.error_message == ""
    assert out.price.strip() != ""
    assert "price_gap_warn" not in out.pricing_info


def test_pricing_warn_passes_with_mark(monkeypatch):
    """warn（4×）→ 放行 + pricing_info.price_gap_warn 留痕。"""
    ctrl = _call_pricing(monkeypatch, _make_state())
    base_price = int(ctrl.price)
    out = _call_pricing(monkeypatch, _make_state(envelope=_envelope_with_anchor(base_price * 4)))
    assert out.error_message == ""
    assert out.price.strip() != ""
    warn = out.pricing_info.get("price_gap_warn")
    assert warn, "warn 档必须留痕 pricing_info.price_gap_warn"
    assert abs(warn["ratio"] - 4.0) < 0.01
    assert warn["anchor_price"] == base_price * 4


def test_pricing_block_fails_with_box(monkeypatch):
    """block（12×）→ [PRICING_FAILED] failed 出口 + error_message 含「价差」+ 入采集箱被调。"""
    ctrl = _call_pricing(monkeypatch, _make_state())
    base_price = int(ctrl.price)
    anchor = base_price * 12

    calls = []

    def _fake_create(tenant_id, envelope, candidates, blocked_reason):
        calls.append({"tenant": tenant_id, "envelope": envelope, "reason": blocked_reason})
        return {"draft_id": "box-1", "reused": False, "top1_name": "", "top1_confidence": 0.0}

    monkeypatch.setattr(
        "utils.blocked_draft_box.create_blocked_draft", _fake_create
    )  # 节点内延迟 import → 打模块属性即可命中

    state = _make_state(envelope=_envelope_with_anchor(anchor))
    out = _call_pricing(monkeypatch, state)

    # failed 出口形状（route_after_pricing 按 [PRICING_FAILED] 阻断；终态判 failed）
    assert out.price == ""
    assert "[PRICING_FAILED]" in out.error_message
    assert "价差" in out.error_message
    assert "疑似货源错配" in out.error_message
    assert out.failed_stage == "pricing"
    # 入箱：tenant/user_id + reason 带两个价格 + notice 拼接成功
    assert len(calls) == 1
    assert calls[0]["tenant"] == "u-1"
    assert str(anchor) in calls[0]["reason"]
    assert str(base_price) in calls[0]["reason"]
    assert "价差守卫" in calls[0]["reason"]
    # ✅ v0.73 终审 I2: notice 前缀带原因（task_processor error_message 优先取 notice）
    assert "价差守卫拦截" in out.notice
    assert str(anchor) in out.notice
    assert str(base_price) in out.notice
    assert "疑似货源错配" in out.notice
    assert "box-1" in out.notice
    assert out.pricing_info.get("price_gap_block", {}).get("anchor_price") == anchor


def test_pricing_block_box_failure_non_fatal(monkeypatch):
    """入箱抛异常（PG 不可用等）→ 非致命，failed 出口仍然返回。"""
    ctrl = _call_pricing(monkeypatch, _make_state())
    anchor = int(ctrl.price) * 12

    def _boom(*a, **k):
        raise RuntimeError("pg down")

    monkeypatch.setattr("utils.blocked_draft_box.create_blocked_draft", _boom)
    out = _call_pricing(monkeypatch, _make_state(envelope=_envelope_with_anchor(anchor)))
    assert "[PRICING_FAILED]" in out.error_message
    assert out.price == ""


def test_pricing_block_without_tenant_skips_box(monkeypatch):
    """user_id 缺失 → 跳过入箱（无租户归属），failed 出口不受影响。"""
    ctrl = _call_pricing(monkeypatch, _make_state())
    anchor = int(ctrl.price) * 12

    def _fail_if_called(*a, **k):
        raise AssertionError("无租户不应调 create_blocked_draft")

    monkeypatch.setattr("utils.blocked_draft_box.create_blocked_draft", _fail_if_called)
    out = _call_pricing(monkeypatch, _make_state(user_id="", envelope=_envelope_with_anchor(anchor)))
    assert "[PRICING_FAILED]" in out.error_message
    # ✅ v0.73 终审 I2: 入箱跳过但原因仍在 notice（任务行 error_message 优先取 notice）
    assert "价差守卫拦截" in out.notice
    assert str(anchor) in out.notice
    assert "box-" not in out.notice


# ══════════════ 3. ingest 空标题闸 ══════════════

def _make_ingest_state(envelope):
    return SimpleNamespace(
        envelope=envelope,
        user_id="u-1",
        supabase_url="http://supabase.local",
        supabase_key="k",
        currency_code="RUB",
    )


def test_ingest_empty_title_fails_fast():
    state = _make_ingest_state({"draft": {"item_id": "123", "title": ""}, "source": {}, "extensions": {}})
    out = ingest_mod.ingest_node(state, None, _DummyRuntime())
    assert out.status == "error"
    assert out.task_id == ""
    assert "draft.title" in out.error_message
    assert out.failed_stage == "ingest"


def test_ingest_whitespace_title_fails_fast():
    state = _make_ingest_state({"draft": {"item_id": "123", "title": "   "}, "source": {}, "extensions": {}})
    out = ingest_mod.ingest_node(state, None, _DummyRuntime())
    assert out.status == "error"
    assert "draft.title" in out.error_message


def test_ingest_missing_title_key_fails_fast():
    state = _make_ingest_state({"draft": {"item_id": "123", "weight": 500}, "source": {}, "extensions": {}})
    out = ingest_mod.ingest_node(state, None, _DummyRuntime())
    assert out.status == "error"
    assert out.failed_stage == "ingest"


def test_ingest_good_title_passes():
    state = _make_ingest_state({"draft": {"item_id": "123", "title": "防晒帽"}, "source": {}, "extensions": {}})
    out = ingest_mod.ingest_node(state, None, _DummyRuntime())
    assert out.status == "accepted"
    assert out.failed_stage == ""
    assert out.error_message == ""
    assert out.draft["title"] == "防晒帽"


def test_ingest_flat_payload_empty_title_fails_fast():
    """扁平结构（envelope 即 draft）同样过闸。"""
    state = _make_ingest_state({"item_id": "123", "title": "", "weight": 500})
    out = ingest_mod.ingest_node(state, None, _DummyRuntime())
    assert out.status == "error"
    assert "draft.title" in out.error_message


def test_route_after_ingest_blocks_only_ingest_failures():
    """条件边路由：failed_stage 含 ingest 且 error_message 非空 → END；否则 → pricing。"""
    from graphs.graph import route_after_ingest

    assert route_after_ingest(SimpleNamespace(failed_stage="ingest", error_message="信封缺 draft.title，拒绝盲生成")) == "END"
    # 双条件：failed_stage 空或 error_message 空都放行（保持旧错误形状的既有流向）
    assert route_after_ingest(SimpleNamespace(failed_stage="", error_message="")) == "pricing"
    assert route_after_ingest(SimpleNamespace(failed_stage="ingest", error_message="")) == "pricing"
    assert route_after_ingest(SimpleNamespace(failed_stage="pricing", error_message="x")) == "pricing"


def test_pricing_input_schema_declares_envelope():
    """langgraph Input 过滤纪律：envelope/user_id 必须在 PricingInput 声明（守卫才读得到）。"""
    from graphs.state import PricingInput, PricingOutput

    pi = PricingInput(supabase_url="u", supabase_key="k", envelope={"draft": {}}, user_id="t1")
    assert pi.envelope == {"draft": {}}
    assert pi.user_id == "t1"
    # PricingOutput.notice 透传通道（入箱 notice → task_processor）
    assert PricingOutput().notice == ""
