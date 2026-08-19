"""commission_resolver 单测 — 佣金解析共享模块（任务 1.2，TDD）。

覆盖：价格分段 / /v5/product/info/prices 响应解析（items[0].commissions）/
优先级链解析（explicit > 缓存表 > extensions segments > 0.10）/ 缓存表读写（mock session，不连真实 PG）。
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from utils.commission_resolver import (
    get_category_commission,
    parse_prices_commissions,
    pick_price_band,
    resolve_commission_rate,
    select_segment,
    upsert_category_commission,
)


def test_pick_price_band_boundaries():
    assert pick_price_band(1500) == "leq_1500"
    assert pick_price_band(1501) == "leq_5000"
    assert pick_price_band(5000) == "leq_5000"
    assert pick_price_band(5001) == "gt_5000"
    assert pick_price_band(0) == "leq_1500"
    assert pick_price_band(None) == "leq_1500"
    assert pick_price_band(-5) == "leq_1500"


def test_parse_prices_commissions_rfbs_path():
    assert parse_prices_commissions(
        {"items": [{"commissions": {"sales_percent_rfbs": 15.0}}]}
    ) == 0.15
    assert parse_prices_commissions({}) is None
    assert parse_prices_commissions({"items": []}) is None
    # fbp 回退：rfbs 缺失时读 sales_percent_fbp
    assert parse_prices_commissions(
        {"items": [{"commissions": {"sales_percent_fbp": 8.0}}]}
    ) == 0.08
    # rfbs 存在但为 None → 也回退 fbp
    assert parse_prices_commissions(
        {"items": [{"commissions": {"sales_percent_rfbs": None, "sales_percent_fbp": 10.0}}]}
    ) == 0.10
    # commissions 缺失 / 为空 → None
    assert parse_prices_commissions({"items": [{}]}) is None
    assert parse_prices_commissions({"items": [{"commissions": {}}]}) is None
    # 响应本身不是 dict / 为 None → None
    assert parse_prices_commissions(None) is None


def test_select_segment_missing_and_non_numeric():
    assert select_segment({"fbs_leq_1500": 8.0}, "fbs", "leq_1500") == 8.0
    assert select_segment({"fbs_leq_1500": None}, "fbs", "leq_1500") is None
    assert select_segment({"fbs_leq_1500": ""}, "fbs", "leq_1500") is None
    assert select_segment({}, "fbs", "leq_1500") is None
    # prefix 为空 → 直接取 band 键（extensions segments 内层结构）
    assert select_segment({"leq_1500": 9.0}, "", "leq_1500") == 9.0
    assert select_segment({"fbs_leq_1500": "abc"}, "fbs", "leq_1500") is None


_CACHE_ROW = {
    "fbs_leq_1500": 8.0,
    "fbs_leq_5000": 12.0,
    "fbs_gt_5000": 18.0,
    "source": "what_to_sell",
}


def test_resolve_explicit_rate_wins():
    rate, source = resolve_commission_rate(
        description_category_id=123,
        price_rub=800,
        explicit_commission=0.12,
        get_category_commission_fn=lambda _dc: _CACHE_ROW,
    )
    assert (rate, source) == (0.12, "explicit")


def test_resolve_cache_band_select():
    def resolve(price):
        return resolve_commission_rate(
            description_category_id=123,
            price_rub=price,
            explicit_commission=0,
            get_category_commission_fn=lambda _dc: _CACHE_ROW,
        )

    assert resolve(800) == (0.08, "cache:leq_1500")
    assert resolve(3000) == (0.12, "cache:leq_5000")
    assert resolve(8000) == (0.18, "cache:gt_5000")


def test_resolve_segments_source():
    segments = {
        "fbs": {"leq_1500": 9.0, "leq_5000": 13.0, "gt_5000": 20.0},
        "fbo": {"leq_1500": 6.0},
    }
    rate, source = resolve_commission_rate(
        description_category_id=456,
        price_rub=800,
        explicit_commission=0,
        extensions_commission_segments=segments,
    )
    assert (rate, source) == (0.09, "segments:leq_1500")


def test_resolve_miss_fallback_010():
    rate, source = resolve_commission_rate(
        description_category_id=999, price_rub=800, explicit_commission=0
    )
    assert (rate, source) == (0.10, "fallback")


# ---- DB 读写：mock session（不连真实 PG）----


class _FakeSession:
    """最小 mock：execute().scalars().first() / commit / close，记录调用。"""

    def __init__(self, first_result=None):
        self._first = first_result
        self.executed = []
        self.committed = 0
        self.closed = False

    def execute(self, stmt):
        self.executed.append(stmt)
        return self

    def scalars(self):
        return self

    def first(self):
        return self._first

    def commit(self):
        self.committed += 1

    def close(self):
        self.closed = True


def _row_obj():
    return SimpleNamespace(
        fbs_leq_1500=8.0,
        fbs_leq_5000=12.0,
        fbs_gt_5000=18.0,
        fbo_leq_1500=7.0,
        fbo_leq_5000=11.0,
        fbo_gt_5000=16.0,
        source="what_to_sell",
    )


def test_get_category_commission_hit_with_mock_session():
    sess = _FakeSession(first_result=_row_obj())
    row = get_category_commission(123, session=sess)
    assert row is not None
    assert row["fbs_leq_1500"] == 8.0
    assert row["fbs_gt_5000"] == 18.0
    assert row["fbo_gt_5000"] == 16.0
    assert row["source"] == "what_to_sell"
    assert len(sess.executed) == 1
    assert sess.closed is False  # 注入的 session 不归模块管


def test_get_category_commission_miss_with_mock_session():
    sess = _FakeSession(first_result=None)
    assert get_category_commission(123, session=sess) is None
    assert len(sess.executed) == 1


def test_upsert_category_commission_with_mock_session():
    sess = _FakeSession()
    upsert_category_commission(
        123, "prices_api", session=sess, fbs_leq_1500=8.0, fbs_gt_5000=18.0
    )
    assert len(sess.executed) == 1
    assert sess.committed == 1
