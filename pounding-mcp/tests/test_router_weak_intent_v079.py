"""v0.79 口径统一（PLAN-agent-ergonomics-v1 B2）：URL + 弱化词 → 展示态。

二分法：①URL+明确意图 → 直接提交腿（A/B 缺省，router 原行为，锁定）；
②URL+弱化词（看看/能不能上/多少钱…）→ graph --no-submit / follow 无 --auto-submit
（SKILL.md §1⑨ 的 router 侧落地）。needs_confirmation 恒 False——确认发生在
用户看完展示之后，不发生在路由层。
"""
from __future__ import annotations

from pounding_mcp.router import route_intent


def test_url_explicit_intent_direct_submit():
    r = route_intent("帮我上架这个 https://detail.1688.com/offer/723.html")
    assert r["pipeline"] == "A"
    assert r["command"] == "graph"
    assert "--no-submit" not in r["args"]
    assert r["needs_confirmation"] is False


def test_url_weak_intent_graph_no_submit():
    r = route_intent("看看这个能不能上 https://detail.1688.com/offer/723.html")
    assert r["pipeline"] == "A"
    assert "--no-submit" in r["args"]
    assert r["needs_confirmation"] is False
    assert r["note"]


def test_ozon_url_weak_intent_follow_display_mode():
    r = route_intent("https://www.ozon.ru/product/12345/ 多少钱，评估一下")
    assert r["pipeline"] == "B"
    assert r["command"] == "follow"
    assert "--auto-submit" not in r["args"]   # follow 缺省即展示
    assert r["note"]


def test_ozon_url_explicit_follow_direct():
    r = route_intent("跟卖 https://www.ozon.ru/product/12345/")
    assert r["pipeline"] == "B"
    assert r["needs_confirmation"] is False
    assert "--auto-submit" not in r["args"]    # 提交腿由 agent 按口径加参


def test_route_schema_has_note_field():
    r = route_intent("检查环境")
    assert "note" in r          # 加性字段，向后兼容
    assert r["note"] == ""      # 非弱意图路径 note 为空
