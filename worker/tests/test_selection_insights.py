"""selection_insight_service 单测（mock 隔离，不需真联网/PG）。

锁定（harness-store-analysis 计划 todo 4）：
1. candidates_json 为空 → 跳过不写（无 INSERT 执行，返回 False）
2. 含 2 候选的 candidates_json → 聚合字段正确（avg_price_rub 均值 / avg_profit_margin 均值 /
   match_1688_count 计数 / sold_count 求和），并写入 selection_insights 一行
3. 同 (keyword, token) 二次 upsert → SQL 含 ON CONFLICT DO UPDATE（去重更新，非重复插入）
4. 聚合结果只含白名单标量字段（不含 match_1688_images / competing_seller_list 等大字段）
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

from services.selection_insight_service import (
    aggregate_candidates,
    upsert_from_discovery_run,
)


class FakeResult:
    def __init__(self, rowcount=None):
        self.rowcount = rowcount
        self.scalar_value = None


class FakeConn:
    def __init__(self, engine):
        self._engine = engine

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self._engine.commits += 1
        return False

    def execute(self, stmt, params=None):
        self._engine.calls.append(
            {"sql": str(stmt) if stmt is not None else "", "params": params}
        )
        return FakeResult(self._engine.rowcount)


class FakeEngine:
    def __init__(self):
        self.calls = []
        self.commits = 0
        self.rowcount = 1

    def begin(self):
        return FakeConn(self)

    def connect(self):
        return FakeConn(self)


@pytest.fixture
def fake_engine(monkeypatch):
    eng = FakeEngine()
    monkeypatch.setattr("services.selection_insight_service.get_engine", lambda: eng)
    return eng


def _candidate(price, margin, match_url="", sold=0, category=""):
    d = {
        "ozon_product_id": "p",
        "ozon_price": price,
        "profit_margin": margin,
        "monthly_sales": sold,
        "category": category,
    }
    if match_url:
        d["match_1688_url"] = match_url
    return d


def test_aggregate_whitelist_no_big_fields():
    """聚合结果只含 5 个白名单标量字段，绝不产出大字段。"""
    agg = aggregate_candidates([_candidate(100.0, 0.2, match_url="u1")])
    assert agg is not None
    assert set(agg.keys()) == {
        "category_path", "avg_price_rub", "avg_profit_margin",
        "match_1688_count", "sold_count",
    }
    assert "match_1688_images" not in agg
    assert "competing_seller_list" not in agg
    assert "ozon_images" not in agg


def test_upsert_from_empty_candidates(fake_engine):
    """candidates_json 为空 → 跳过不写，无 INSERT 执行。"""
    ok = upsert_from_discovery_run("tk-a", "宠物饮水机", [])
    assert ok is False
    assert fake_engine.calls == [], "空候选不应触发任何 INSERT"


def test_upsert_from_none_candidates(fake_engine):
    """candidates_json 为 None → 同样跳过不写（防御空值）。"""
    ok = upsert_from_discovery_run("tk-a", "宠物饮水机", None)  # type: ignore[arg-type]
    assert ok is False
    assert fake_engine.calls == []


def test_upsert_aggregates(fake_engine):
    """2 候选 → 聚合正确 + 写入 selection_insights 一行（含 ON CONFLICT）。"""
    candidates = [
        _candidate(200.0, 0.30, match_url="http://1688/a", sold=100, category="宠物饮水机"),
        _candidate(400.0, 0.50, match_url="", sold=300, category="宠物饮水机"),
    ]
    ok = upsert_from_discovery_run("tk-a", "宠物饮水机", candidates)
    assert ok is True
    assert len(fake_engine.calls) == 1

    sql = fake_engine.calls[0]["sql"]
    assert "INSERT INTO selection_insights" in sql
    assert "ON CONFLICT (keyword, contributed_by_token_id) DO UPDATE" in sql

    params = fake_engine.calls[0]["params"]
    assert params["keyword"] == "宠物饮水机"
    assert params["contributed_by_token_id"] == "tk-a"
    assert params["source"] == "fetched"
    assert params["avg_price_rub"] == 300.0  # (200+400)/2
    assert params["avg_profit_margin"] == 0.4  # (0.30+0.50)/2
    assert params["match_1688_count"] == 1  # 仅 1 个带 match_1688_url
    assert params["sold_count"] == 400  # 100+300
    assert params["category_path"] == "宠物饮水机"


def test_upsert_dedupe(fake_engine):
    """同 (keyword, token) 二次 upsert → 不产生第二行（ON CONFLICT 更新路径）。"""
    candidates = [_candidate(200.0, 0.30, match_url="http://1688/a", sold=100)]
    ok1 = upsert_from_discovery_run("tk-a", "宠物饮水机", candidates)
    assert ok1 is True
    # 第二次上报同一 keyword, 同 token（shift 后数值变化）
    candidates2 = [_candidate(300.0, 0.40, match_url="http://1688/b", sold=200)]
    ok2 = upsert_from_discovery_run("tk-a", "宠物饮水机", candidates2)
    assert ok2 is True

    assert len(fake_engine.calls) == 2, "两次都应走 upsert（非重复插入）"
    for call in fake_engine.calls:
        assert "ON CONFLICT (keyword, contributed_by_token_id) DO UPDATE" in call["sql"]
    # 第二次参数已更新为最新聚合值
    assert fake_engine.calls[1]["params"]["avg_price_rub"] == 300.0
    assert fake_engine.calls[1]["params"]["sold_count"] == 200


def test_upsert_different_token_same_keyword(fake_engine):
    """不同 token 同 keyword → 各自 upsert（唯一键含 token，非跨用户合并）。"""
    c = [_candidate(200.0, 0.30, match_url="u", sold=100)]
    upsert_from_discovery_run("tk-a", "宠物饮水机", c)
    upsert_from_discovery_run("tk-b", "宠物饮水机", c)
    assert len(fake_engine.calls) == 2
    assert fake_engine.calls[0]["params"]["contributed_by_token_id"] == "tk-a"
    assert fake_engine.calls[1]["params"]["contributed_by_token_id"] == "tk-b"


def test_upsert_bad_values_safe(fake_engine):
    """缺值/坏值候选不炸：均价用有效值，匹配数按 match_1688_price 判定。"""
    candidates = [
        {"ozon_product_id": "x", "ozon_price": "abc", "match_1688_price": 12.5},
        {"ozon_product_id": "y", "monthly_sales": "50"},
    ]
    ok = upsert_from_discovery_run("tk-a", "宠物饮水机", candidates)
    assert ok is True
    params = fake_engine.calls[0]["params"]
    assert params["avg_price_rub"] is None  # 两个候选 ozon_price 均为坏值/缺失
    assert params["avg_profit_margin"] is None
    assert params["match_1688_count"] == 1  # match_1688_price=12.5 判定命中
    assert params["sold_count"] == 50
