"""v0.79 口径统一（PLAN-agent-ergonomics-v1 B2）：URL + 弱化词 → 展示态。

二分法：①URL+明确意图 → 直接提交腿（A/B 带 --wait，v2 对齐 SKILL.md §1
「graph --url --wait / follow --auto-submit --wait」直提口径）；
②URL+弱化词（看看/能不能上/多少钱…）→ graph --no-submit / follow 无 --auto-submit
（SKILL.md §1⑨ 的 router 侧落地）。needs_confirmation 恒 False——确认发生在
用户看完展示之后，不发生在路由层；--wait 只属强意图直提腿，弱意图腿不带。
"""
from __future__ import annotations

from pounding_mcp.router import route_intent


def test_url_explicit_intent_direct_submit():
    r = route_intent("帮我上架这个 https://detail.1688.com/offer/723.html")
    assert r["pipeline"] == "A"
    assert r["command"] == "graph"
    assert "--no-submit" not in r["args"]
    assert "--wait" in r["args"]              # v2：直提腿带 --wait
    assert r["needs_confirmation"] is False


def test_url_weak_intent_graph_no_submit():
    r = route_intent("看看这个能不能上 https://detail.1688.com/offer/723.html")
    assert r["pipeline"] == "A"
    assert "--no-submit" in r["args"]
    assert "--wait" not in r["args"]          # 弱意图腿不带 --wait（展示态不轮询终态）
    assert r["needs_confirmation"] is False
    assert r["note"]


def test_ozon_url_weak_intent_follow_display_mode():
    r = route_intent("https://www.ozon.ru/product/12345/ 多少钱，评估一下")
    assert r["pipeline"] == "B"
    assert r["command"] == "follow"
    assert "--auto-submit" not in r["args"]   # follow 缺省即展示
    assert "--wait" not in r["args"]          # 弱意图腿不带 --wait
    assert r["note"]


def test_ozon_url_explicit_follow_direct():
    r = route_intent("跟卖 https://www.ozon.ru/product/12345/")
    assert r["pipeline"] == "B"
    assert r["needs_confirmation"] is False
    # arch-findings 修复：强意图由 router 直带 --auto-submit（follow 缺省只展示，
    # 旧口径「agent 按需加参」实际导致照令牌执行零提交）
    assert "--auto-submit" in r["args"]
    assert "--wait" in r["args"]              # v2：直提腿带 --wait（§1 逐字口径）


def test_route_schema_has_note_field():
    r = route_intent("检查环境")
    assert "note" in r          # 加性字段，向后兼容
    assert r["note"] == ""      # 非弱意图路径 note 为空
