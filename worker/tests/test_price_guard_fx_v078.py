"""批C Q8（v0.78 fix/guard-precision-v1）— 价差守卫跨币种汇率换算真比（TDD RED→GREEN）。

背景：价差守卫锚价 ``discovery_meta.ozon_price`` 恒为 RUB（Ozon 站内价），CNY
店铺终价是 CNY——v0.77.3 止血为「跨币种直接 skip」（宁缺勿假）。gate 实测换算后
真实可比：608₽ ÷ 13.3 ≈ 45.7¥ vs 30¥ = 1.52×（完全正常），垃圾比值 608₽÷30¥=20×
纯属单位错配。本批增强为 **用汇率换算后真比**：``exchange_rate``（CNY→RUB 方向，
与 pricing 语义一致）> 0 时 ``anchor_cmp = anchor / exchange_rate`` 再走既有
ratio 判定（block ≥10 / warn ≥3 不变）；rate 缺失/≤0 维持 skip 止血语义；
绝不 raise。

运行：cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_price_guard_fx_v078.py -q
纯 mock，无 PG/网络。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402

from utils.price_sanity_guard import check_price_sanity  # noqa: E402

_META = {"ozon_price": 608.0}


# ═══════════════ 换算真比（rate > 0）═══════════════

def test_fx_converted_real_ratio_ok():
    """①gate 复现场景：anchor=608₽、rate=13.3、price=30¥ → 换算 608/13.3≈45.7¥，
    ratio≈1.52 → ok（v0.77.3 时该场景只能 skip，现在真比且放行）。"""
    verdict, ev = check_price_sanity(
        30.0, _META, final_currency="CNY", exchange_rate=13.3)
    assert verdict == "ok", f"换算后 1.52× 必须 ok，实际 {verdict} / {ev}"
    assert ev.get("anchor_converted") == pytest.approx(608.0 / 13.3, abs=0.01), \
        f"evidence 需记 anchor_converted: {ev}"
    assert ev.get("exchange_rate") == 13.3, f"evidence 需记 exchange_rate: {ev}"
    assert ev.get("ratio") == pytest.approx(1.52, abs=0.01)


def test_fx_converted_block():
    """②换算后仍超阈 → block：anchor=60000₽、rate=13.3（≈4511¥）vs 30¥ ratio≈150
    → block（换算不放过真错配）。"""
    verdict, ev = check_price_sanity(
        30.0, {"ozon_price": 60000.0}, final_currency="CNY", exchange_rate=13.3)
    assert verdict == "block", f"4511¥ vs 30¥ ≈150× 必须 block，实际 {verdict} / {ev}"
    assert ev["ratio"] >= 10


def test_fx_converted_warn_band():
    """③换算后落在 warn 带（≥3 且 <10）→ warn：换算锚 45.7¥×5≈228 vs 30¥... 直接构
    anchor=200₽ rate=13.3 → ≈15¥ vs 3¥ = 5× → warn。"""
    verdict, ev = check_price_sanity(
        3.0, {"ozon_price": 200.0}, final_currency="CNY", exchange_rate=13.3)
    assert verdict == "warn", f"换算后 5× 必须 warn，实际 {verdict} / {ev}"
    assert ev["exchange_rate"] == 13.3


# ═══════════════ 止血语义保持（rate 缺失/≤0）═══════════════

def test_rate_missing_still_skips():
    """④rate 缺省（0.0）+ 非 RUB → 维持 skip（宁缺勿假，0.77.3 止血语义不变）。"""
    verdict, ev = check_price_sanity(30.0, _META, final_currency="CNY")
    assert verdict == "ok"
    assert ev.get("skipped") == "currency_mismatch", f"skip 留痕必须保持: {ev}"
    assert "anchor_price" in ev


def test_rate_zero_or_negative_still_skips():
    """⑤rate=0 / 负数 / 非法 → 同样 skip（绝不用垃圾汇率硬算假比值；绝不 raise）。"""
    for bad_rate in (0.0, -1.0, 0):
        verdict, ev = check_price_sanity(
            30.0, _META, final_currency="CNY", exchange_rate=bad_rate)
        assert verdict == "ok", f"rate={bad_rate!r} 必须 skip"
        assert ev.get("skipped") == "currency_mismatch"


def test_exchange_rate_is_keyword_only():
    """⑥exchange_rate 是 keyword-only 参数（简报硬约束）——位置传参必须 TypeError。"""
    with pytest.raises(TypeError):
        check_price_sanity(30.0, _META, "CNY", 13.3)


# ═══════════════ RUB 主形态回归（rate 不参与）═══════════════

def test_rub_path_ignores_exchange_rate():
    """⑦RUB 店（生产主形态）：同币种直接比，传入 rate 不影响比值。"""
    verdict, ev = check_price_sanity(
        30.0, _META, final_currency="RUB", exchange_rate=13.3)
    assert verdict == "block"
    assert ev["ratio"] == 20.27, f"RUB 路径 ratio 不受 rate 影响: {ev}"
    assert "anchor_converted" not in ev, "RUB 路径无换算，不得出现 anchor_converted"


def test_rub_path_default_regression():
    """⑧RUB 路径缺省回归（对齐 test_price_guard_currency_v0773 既有行为）。"""
    verdict, _ = check_price_sanity(30.0, {"ozon_price": 60000.0}, final_currency="RUB")
    assert verdict == "block"


def test_no_anchor_with_rate_still_ok():
    """⑨无锚恒 ok 零误杀教义不受换算影响（CNY + rate 在场 + 无锚）。"""
    verdict, ev = check_price_sanity(30.0, {}, final_currency="CNY", exchange_rate=13.3)
    assert verdict == "ok"
    assert ev == {}


# ═══════════════ pricing_node 接线（守卫拿到的是真实换算汇率）═══════════════

class _DummyRuntime:
    context = None


def test_pricing_node_passes_real_fx_rate_to_guard(monkeypatch):
    """⑩CNY 店：pricing_node 传给守卫的必须是真实 CNY→RUB 汇率。

    ⚠️ 坑：pricing_node :194 的 exchange_rate 对 CNY 店恒 1.0（_get_exchange_rate
    的 CNY 路径不使用汇率）——直接透传会把 608₽÷1.0=608¥ 又比出 20× 假阳性
    （本批要修的 bug 复活）。非 RUB 店必须另取真实汇率（fx 三级链）再传。"""
    from graphs.nodes import pricing_node as pn

    captured = {}

    def _spy(price, meta, final_currency="RUB", **kwargs):
        captured["final_currency"] = final_currency
        captured["exchange_rate"] = kwargs.get("exchange_rate")
        return "ok", {}

    monkeypatch.setattr(pn, "check_price_sanity", _spy)
    # 真实汇率链 mock 成 13.3（CNY 店的 :194 变量本身是 1.0，与真实汇率可区分）
    monkeypatch.setattr(pn, "_get_exchange_rate",
                        lambda *a, **k: 1.0 if k.get("currency_code") == "CNY"
                        or (a and len(a) >= 3 and a[2] == "CNY") else 13.3)

    from utils import logistics_quote
    monkeypatch.setattr(logistics_quote, "query_logistics_cost",
                        lambda *a, **k: (10.0, "mock_channel", {}))
    monkeypatch.setattr(logistics_quote, "get_store_logistics_config",
                        lambda *a, **k: ("RETS", "Standard"))
    monkeypatch.setattr(pn, "get_category_commission", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(pn, "ozon_post", lambda *a, **k: {}, raising=False)

    from types import SimpleNamespace
    state = SimpleNamespace(
        draft={"cost_cny": 5.5, "purchase_cost": 5.5, "weight": 227,
               "dimensions": {"length": 120, "width": 80, "height": 60}},
        extensions={},
        supabase_url="http://supabase.local",
        supabase_key="k",
        currency_code="CNY",  # CNY 测试店（gate 冤杀形态）
        ozon_client_id="1",
        ozon_api_key="k",
        description_category_id="17028830",
        task_id="",
        tenant_id="",
        token="sk-test",
        user_id="u-1",
        envelope={"extensions": {"discovery_meta": {"ozon_price": 608.0}}},
        variants=[],
        error_message="",
        pricing_info={},
    )
    out = pn.pricing_node(state, None, _DummyRuntime())
    assert out.error_message == "", f"ok 场景不得报错: {out.error_message}"
    assert captured["final_currency"] == "CNY"
    assert captured["exchange_rate"] == pytest.approx(13.3), (
        f"CNY 店守卫必须拿真实换算汇率，实际 {captured.get('exchange_rate')!r} "
        f"（:194 的 1.0 不是换算汇率，透传=假阳性复活）"
    )


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  ✅ {fn.__name__}")
            passed += 1
        except Exception as e:
            traceback.print_exc()
            print(f"  ❌ {fn.__name__}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{passed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
