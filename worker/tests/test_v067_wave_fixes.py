"""v0.67 wave 实证缺陷修复回归（TDD）。

两个 P1（2026-09-06 wave 测试 archive/docs/legacy/TEST-v067-wave-plan.md 实测发现）：
1. 佣金缓存 0% 污染——A6 approved 后 `_backfill_category_commission` 把
   prices 响应缺 commissions 块解析出的 0 照样 upsert（dc=17028746
   fbs_leq_5000=0），而 `resolve_commission_rate` 只判 `pct is not None`
   → 后续该类目定价按 0% 佣金算利润虚高。
   修法：parse 非正值→None（无数据语义）+ resolver 缓存段 <=0 视同未命中。
2. skill search_kw 候选 sim=1.0 插队抢跑——`_resolve_skill_category` 对树
   校验通过的候选恒置 similarity=1.0，v0.65.1 R3 降级「普通 L1 候选」后仍在
   L1041 insert(0) + L1218 放回首位 → A1 辣椒帽被 skill 侧错猜的化妆刷
   (78032222) 带偏整卡（帽类必填属性全缺 → Ozon declined）。
   修法：非权威 skill 候选追加队尾（保留空池兜底与 LLM fallback 可见性，
   不插队）；权威（page/mapping/what_to_sell/widget 路径精配）插首不变。

运行：
    cd worker && PGDATABASE_URL="postgresql://postgres:localdev123@localhost:5433/ozon" \
      PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_v067_wave_fixes.py -q
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("GRSAI_API_KEY", "test-key")
os.environ.setdefault("LOG_FORMAT", "text")
os.environ.setdefault("LOG_LEVEL", "WARNING")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from utils.commission_resolver import parse_prices_commissions, resolve_commission_rate


# ═══ #1a: parse_prices_commissions 非正值 = 无数据 ═══

def test_parse_prices_commissions_zero_ratio_is_none():
    """sales_percent_rfbs=0（响应缺 commissions 块的常见形态）→ None，不是 0.0。"""
    assert parse_prices_commissions(
        {"items": [{"commissions": {"sales_percent_rfbs": 0.0}}]}
    ) is None


def test_parse_prices_commissions_negative_ratio_is_none():
    assert parse_prices_commissions(
        {"items": [{"commissions": {"sales_percent_rfbs": -3.0}}]}
    ) is None


def test_parse_prices_commissions_positive_unchanged():
    """正常值行为不变（15% → 0.15）。"""
    assert parse_prices_commissions(
        {"items": [{"commissions": {"sales_percent_rfbs": 15.0}}]}
    ) == 0.15
    assert parse_prices_commissions(
        {"items": [{"commissions": {"sales_percent_fbp": 12.5}}]}
    ) == 0.125


# ═══ #1b: resolver 缓存段 0/负值视同未命中 ═══

def test_resolver_cache_zero_percent_falls_through():
    """缓存行段值 0（已被污染的行）不得当 0% 佣金采信 → 继续 fallback。"""
    rate, source = resolve_commission_rate(
        17028746, 3000.0, None,
        get_category_commission_fn=lambda dc: {"fbs_leq_5000": 0.0, "source": "prices_api"},
    )
    assert rate > 0, "0% 佣金会让定价利润虚高，绝不能采信"
    assert source == "fallback"


def test_resolver_cache_negative_percent_falls_through():
    rate, source = resolve_commission_rate(
        17028746, 3000.0, None,
        get_category_commission_fn=lambda dc: {"fbs_leq_5000": -1.0},
    )
    assert rate > 0
    assert source == "fallback"


def test_resolver_cache_positive_still_used():
    """正常缓存行行为不变：13% → 0.13, cache:leq_5000。"""
    rate, source = resolve_commission_rate(
        41777465, 3000.0, None,
        get_category_commission_fn=lambda dc: {"fbs_leq_5000": 13.0},
    )
    assert abs(rate - 0.13) < 1e-9
    assert source == "cache:leq_5000"


# ═══ #1c: 回填侧不再 upsert 非正值 ═══

def _state(**kw):
    return SimpleNamespace(**kw)


def test_backfill_skips_zero_commission_upsert(monkeypatch):
    """prices 响应 commissions 全 0 → 不 upsert（防 dc=17028746 0% 污染重演）。"""
    called = {"upsert": 0}

    def fake_ozon_post(client_id, api_key, endpoint, body, timeout=60, language="ZH_HANS"):
        return {"items": [{"commissions": {"sales_percent_rfbs": 0.0}}]}

    def fake_upsert(*a, **kw):
        called["upsert"] += 1

    monkeypatch.setattr("utils.ozon_client.ozon_post", fake_ozon_post)
    monkeypatch.setattr("utils.commission_resolver.upsert_category_commission", fake_upsert)

    from graphs.nodes.learning_record_node import _backfill_category_commission
    _backfill_category_commission(_state(
        product_id="123456",
        description_category_id="17028746",
        ozon_client_id="c",
        ozon_api_key="k",
        pricing_info={"currency_code": "CNY"},
    ))
    assert called["upsert"] == 0


# ═══ #2: skill 候选入池位置（权威插首 / 非权威队尾） ═══

_BRUSH = {  # A1 实证：skill 侧错猜的化妆刷（树中有效 dc/tp 组合）
    "description_category_id": 78032222, "type_id": 93961,
    "node_name": "化妆刷", "full_path": "美容和卫生 > 化妆刷 > 化妆刷",
    "similarity": 1.0, "confidence": 0.95,
}
_HAT = {  # worker 文本链诚实匹配的遮阳帽
    "description_category_id": 41777465, "type_id": 93167,
    "node_name": "遮阳帽", "full_path": "服装 > 配饰 > 遮阳帽",
    "similarity": 0.29, "confidence": 0.5,
}


def _place(candidates, skill_hit, authoritative):
    from graphs.nodes.assemble_ozon_product_node import _place_skill_candidate
    return _place_skill_candidate(candidates, skill_hit, authoritative)


def test_skill_candidate_authoritative_takes_head():
    """权威 skill 类目（page/mapping/what_to_sell）插首——直采语义不变。"""
    pool = [dict(_HAT)]
    skill = dict(_BRUSH)
    out = _place(pool, skill, True)
    assert out[0] is skill
    assert out[1] is pool[0]


def test_skill_candidate_non_authoritative_goes_tail():
    """非权威（search_kw）skill 候选不得插队首位（A1 sim=1.0 抢跑实证）——
    追加队尾：诚实文本候选保持头部竞争位，skill 候选保留兜底可见性。"""
    pool = [dict(_HAT)]
    skill = dict(_BRUSH)
    out = _place(pool, skill, False)
    assert out[0]["description_category_id"] == 41777465, "诚实候选必须保持头部"
    assert out[-1] is skill
    assert len(out) == 2
    assert pool[0]["description_category_id"] == 41777465, "不得原地修改入参池"


def test_skill_candidate_non_authoritative_empty_pool_fallback():
    """搜索 0 命中时 skill 候选仍是唯一兜底（保留 v0.27 直采兜底语义）。"""
    skill = dict(_BRUSH)
    out = _place([], skill, False)
    assert out == [skill]


def test_place_none_skill_hit_returns_pool_unchanged():
    pool = [dict(_HAT)]
    out = _place(pool, None, True)
    assert len(out) == 1
