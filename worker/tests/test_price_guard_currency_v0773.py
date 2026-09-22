"""v0.77.3（gate 发现修复）：价差守卫币种可比性校验。

实测（2026-09-19 gate，dtw2 六连杀）：测试店 currency_code=CNY（pricing 以 CNY
出终价 30），锚价 discovery_meta.ozon_price=608 是 **RUB**（Ozon 站内价）——
守卫直接 608/30=20.27 倍 block，六张卡全冤杀。真实可比价：608₽×0.075≈45.6¥
vs 30¥ → 1.52 倍，完全正常。RUB 店（生产主形态）同币种不受影响。

修复口径（对齐模块「无锚恒 ok 零误杀」教义）：**币种不可比 = 不比**——
final_currency 非 RUB 时锚价（恒 RUB）与终价不同单位，直接 ok 放行并留
skipped 留痕。
"""
from utils.price_sanity_guard import check_price_sanity

_META = {"ozon_price": 608.0}


def test_block_still_fires_same_currency_rub():
    """RUB 店：终价 30 RUB vs 锚 608 RUB = 20.27× → block（既有行为保持）。"""
    verdict, ev = check_price_sanity(30.0, _META, final_currency="RUB")
    assert verdict == "block"
    assert ev["ratio"] == 20.27


def test_skip_when_currency_mismatch():
    """CNY 店：同数字但终价是 CNY → 币种不可比，ok 放行 + skipped 留痕。"""
    verdict, ev = check_price_sanity(30.0, _META, final_currency="CNY")
    assert verdict == "ok"
    assert ev.get("skipped") == "currency_mismatch"
    assert "anchor_price" in ev  # 留痕供审计


def test_default_currency_is_rub_backcompat():
    """不传币种（旧调用方）默认 RUB——行为零变化。"""
    verdict, _ = check_price_sanity(30.0, _META)
    assert verdict == "block"


def test_no_anchor_still_ok():
    """无锚恒 ok（既有教义不受影响）。"""
    verdict, ev = check_price_sanity(30.0, {}, final_currency="CNY")
    assert verdict == "ok"
    assert ev == {}
