"""v0.85 discover 双真 bug 回归锁：profit_margin 汇率口径 + 采集池恒 8。

Bug 1（margin 恒 4.18%）根因链（2026-10-04 四类目实机波次实锤）：
  ``_build_estimate_item`` 曾传 ``currency_code="RUB"`` → worker 三档 RUB 路径
  ``profit_rate = profit_cny / price``（分子 CNY、分母 RUB 售价，
  worker/src/utils/pricing_estimate.py 三档段）——净利率被 CNY→RUB 汇率整除
  （44.9% → ~4.2%），且比值只依赖 margin/佣金/vcr/汇率常数、与成本无关 →
  所有候选恒同一个数，利润闸（DEFAULT_MIN_MARGIN_PCT=15）全灭。修复：不传
  currency（worker schema 契约"缺省按 CNY"＝货币中性净利率），与
  /estimate/batch 直调参考 profit_rate=0.4493 一致；worker 侧同口径自锁
  tests/test_estimate_batch_parity_v083.py:214（CNY 三档期望 0.4484）。

Bug 2（采集池恒 8）根因链（同日实机：--max-products 50，highlight 与 /search
均恒 8）：``collect_and_analyze`` 后台 tab 创建后从未 force_active → Chrome
冻结后台 tab 的 rAF → 缓动滚动 _EASE_SCROLL_JS（requestAnimationFrame 驱动）
step() 永不执行 → 页面不滚 → 懒加载不触发 → 采集恒等于首屏渲染的 ~8 卡。
v0.81 静默化批删 discover_from_url 时把 force_active 一并带走，
collect_and_analyze 接棒时没补（cli._collect_keyword_pids /
ozon_scraper.scrape_ozon_product_via_cdp 同批都有）。修复：new_tab 后立即
force_active（失败静默，老 Chrome 继续走翻页兜底）。

运行：
    cd skill && .venv314/bin/python -m pytest tests/test_discover_margin_v085.py -q
"""
from __future__ import annotations

import os
import sys
from unittest import mock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.lib import ozon_discovery as od  # noqa: E402
from scripts.lib.ozon_discovery import ProductCandidate  # noqa: E402


# ═══════════════ Bug 1：profit_margin 口径（4.18% 恒定 → 44.9%）═══════════════

def _mk_cand(pid: str, cost: float) -> ProductCandidate:
    c = ProductCandidate(ozon_product_id=pid, ozon_title=f"Товар {pid}",
                         ozon_price=2000.0)
    c.match_1688_price = cost
    c.weight_g = 500
    c.ozon_category = {"description_category_id": "17028892", "type_id": "1"}
    return c


def test_build_estimate_item_omits_currency_tag():
    """batch item 绝不携带 currency_code="RUB"——RUB 标签使 worker 三档路径
    profit_rate 分子 CNY/分母 RUB 售价（净利率被汇率整除）；缺省 → worker
    schema 契约"缺省按 CNY"（货币中性净利率）。"""
    item = od._build_estimate_item(_mk_cand("p1", 50.0))
    assert "currency_code" not in item, (
        f"禁止传 currency_code（RUB 标签 = margin 被汇率整除），got {item}")
    assert item["purchase_cost"] == 50.0
    assert item["dc"] == "17028892"


def test_apply_estimate_row_maps_ratio_to_percent_once():
    """worker profit_rate 是 ratio：0.4493 → 44.93%（×100 恰一次）。

    旧现象对照：直调同品 worker 返 0.4493，session 里却显示 4.18%
    （≈44.93/10.75，汇率整除签名）。回填只做 ratio→percent 一次换算。
    """
    cand = _mk_cand("p1", 50.0)
    row = {"ok": True, "profit_rate": 0.4493, "profit_cny": 33.7,
           "commission_rate": 0.10, "commission_source": "segments:leq_5000",
           "logistics_cost_cny": 12.0, "logistics_source": "store"}
    od._apply_estimate_row(cand, row, fx_rate=0.075)
    assert cand.profit_margin == pytest.approx(44.93), cand.profit_margin
    assert cand.estimated_profit_cny == pytest.approx(33.7)
    assert cand.estimate_source == "worker"
    # 利润闸口径：44.93% 远过 DEFAULT_MIN_MARGIN_PCT=15；4.18% 必被拦
    assert cand.profit_margin >= od.DEFAULT_MIN_MARGIN_PCT


def test_margin_constant_418_signature_gone():
    """恒定 4.18/4.19% 签名消失：不同成本候选经 worker canonical 行回填后，
    margin == ratio×100 逐条忠实映射（不再全体塌缩到汇率整除的同一个数）。

    行构造对齐 worker 三档 CNY 口径（test_estimate_batch_parity_v083.py:214
    同法独立手算：total=17.5、m=1.5、c=0.10、vcr=0.155 → profit_rate≈0.4484）。
    """
    cands = [_mk_cand("p1", 9.5), _mk_cand("p2", 68.0), _mk_cand("p3", 150.0)]
    ratios = [0.4484, 0.152, 0.40]  # worker 三档 CNY 不同佣金/成本档的合法输出
    rows = [{"index": i, "ok": True, "profit_rate": r, "profit_cny": 5.0 + i,
             "commission_rate": 0.10, "commission_source": "fallback",
             "logistics_cost_cny": 10.0, "logistics_source": "default_rets"}
            for i, r in enumerate(ratios)]
    with mock.patch.object(od, "estimate_batch", return_value=rows):
        out = od._estimate_candidates(cands, fx_rate=0.075)
    for cand, row in zip(cands, out):
        od._apply_estimate_row(cand, row, fx_rate=0.075)
    got = [c.profit_margin for c in cands]
    assert got == [44.84, 15.2, 40.0], got
    # 旧 bug 签名：全体落在 4.1-4.2 带内且互不相同的现象绝不允许复现
    assert all(not (4.0 < m < 4.3) for m in got), got
    assert len(set(got)) == len(got), f"margin 塌缩恒定: {got}"


def test_rub_tag_request_would_reproduce_418_signature():
    """文档化旧 bug 算术（防回归认知）：同一净利率 44.93% 若被汇率 10.75 整除
    恰为 4.18%——旧签名 4.18/4.19 = 实机汇率带的两种佣金档。锁定：修复后的
    请求形状（无 currency tag）下 worker 恒走 CNY 口径，此整除不再发生。"""
    assert pytest.approx(44.93 / 10.75, abs=0.01) == 4.18
    assert pytest.approx(44.93 / 10.72, abs=0.01) == 4.19


# ═══════════════ Bug 2：采集池恒 8（后台 tab rAF 冻结）═══════════════

class _FakeTab:
    """记录调用序的后台 tab 替身（force_active 顺序是本组断言核心）。"""

    def __init__(self, events: list, background: bool = True):
        self._events = events
        self.background = background

    def force_active(self):
        self._events.append("force_active")

    def close(self):
        self._events.append("close")

    def navigate(self, url, timeout=25):
        pass

    def evaluate(self, *a, **k):
        return ""


class _FakeCdp:
    def __init__(self, events: list):
        self._events = events
        self.tab = _FakeTab(self._events)

    def new_tab(self, url, background=True):
        self._events.append(("new_tab", url, background))
        return self.tab

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def close(self):
        pass


def _run_collect(events: list, max_products: int = 50):
    """跑 collect_and_analyze 串行路径（空 pids → 零 _analyze_product 触达）。"""
    fake_cdp = _FakeCdp(events)
    with mock.patch("scripts.lib.cdp_client.CdpConnection", return_value=fake_cdp), \
         mock.patch.object(od, "_lazy_collect_rows",
                           side_effect=lambda tab, mp, *a, **k:
                           events.append("lazy_collect") or []), \
         mock.patch.object(od, "_lazy_collect_urls", return_value=[]), \
         mock.patch.object(od, "_passes_base_filter", return_value=True), \
         mock.patch.object(od, "_discover_workers", return_value=1), \
         mock.patch("time.sleep"):
        return od.collect_and_analyze("http://127.0.0.1:9222",
                                      keyword="тест", max_products=max_products)


def test_collect_activates_background_tab_before_scroll_collect():
    """后台 tab 必须在滚动采集前 force_active——否则 rAF 冻结、缓动滚动不执行、
    懒加载不触发，采集恒首屏 ~8 卡（Bug 2 行为锁）。"""
    events: list = []
    result = _run_collect(events)
    assert result == []
    new_tab_evt = next(e for e in events if isinstance(e, tuple))
    assert new_tab_evt[2] is True, "采集 tab 必须后台创建（静默教义不变）"
    assert "force_active" in events, "collect_and_analyze 缺 force_active（Bug 2 复发）"
    assert events.index("force_active") < events.index("lazy_collect"), (
        "force_active 必须先于滚动采集（后调=无效，rAF 已冻结）")


def test_collect_passes_max_products_through():
    """--max-products 传透链：collect_and_analyze → _lazy_collect_rows 原值透传
    （Bug 2 排除项「上限参数没传透」的否定锁）。"""
    events: list = []
    captured = {}

    def _spy_rows(tab, mp, *a, **k):
        captured["max_products"] = mp
        events.append("lazy_collect")
        return []

    with mock.patch("scripts.lib.cdp_client.CdpConnection",
                    return_value=_FakeCdp(events)), \
         mock.patch.object(od, "_lazy_collect_rows", side_effect=_spy_rows), \
         mock.patch.object(od, "_lazy_collect_urls", return_value=[]), \
         mock.patch.object(od, "_passes_base_filter", return_value=True), \
         mock.patch.object(od, "_discover_workers", return_value=1), \
         mock.patch("time.sleep"):
        od.collect_and_analyze("http://127.0.0.1:9222", keyword="тест",
                               max_products=50)
    assert captured["max_products"] == 50
