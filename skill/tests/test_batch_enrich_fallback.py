"""P1c: 阶段②b 先批量畅销榜 map（fetch_bestseller_metrics_map），未命中降级逐 SKU。

背景: 逐 SKU fetch_sales_analytics（1 调用/SKU @1s）是瓶颈；T4 已提供
fetch_bestseller_metrics_map（单次 data/v3 批量，sku-keyed）。本测试验证
collect_and_analyze 的 enrich 逻辑：
1. map 命中 pids → 直接 apply_analytics_to_candidate(map[pid])，不再 per-SKU。
2. map 未命中 pids → 走 fetch_sales_analytics 兜底（只传未命中列表）。
3. 全缺失（map 空 + per-SKU 空）→ 保留原「运营数据全部缺失」warning 行为。
"""
from __future__ import annotations

import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.lib import ozon_discovery as od
from scripts.lib.ozon_discovery import ProductCandidate

SALES_MOD = "scripts.lib.ozon_seller_analytics"


def _mk(pid):
    c = ProductCandidate(ozon_product_id=pid, ozon_title=f"Title {pid}", ozon_price=100.0)
    c.status = "ok"
    return c


def _run_collect(pids, metrics_map, per_sku):
    fake_map = mock.MagicMock(return_value=metrics_map)
    fake_per_sku = mock.MagicMock(return_value=per_sku)
    fake_apply = mock.MagicMock(return_value=True)
    conn = mock.MagicMock()
    conn.new_tab.return_value = mock.MagicMock()
    with mock.patch.object(od, "_discover_workers", return_value=1), \
         mock.patch.object(od, "_analyze_product",
                           side_effect=lambda cdp_url, cdp, pid: _mk(pid)), \
         mock.patch.object(od, "_lazy_collect_urls", return_value=pids), \
         mock.patch("scripts.lib.cdp_client.CdpConnection", return_value=conn), \
         mock.patch.object(od, "_save_discovery_log"), \
         mock.patch(f"{SALES_MOD}.fetch_bestseller_metrics_map", fake_map), \
         mock.patch(f"{SALES_MOD}.fetch_sales_analytics", fake_per_sku), \
         mock.patch(f"{SALES_MOD}.apply_analytics_to_candidate", fake_apply), \
         mock.patch("time.sleep"):
        result = od.collect_and_analyze("http://127.0.0.1:9222", use_analytics=True)
    return result, fake_map, fake_per_sku, fake_apply


def test_map_hit_then_per_sku_fallback():
    """map 命中 hit1/hit2 → apply 直接; miss1 未命中 → fetch_sales_analytics 兜底。"""
    pids = ["hit1", "hit2", "miss1"]
    metrics_map = {
        "hit1": {"sold_count": 10, "category2_id": 17028929, "category3_id": 504866264},
        "hit2": {"sold_count": 20, "category2_id": 17028929, "category3_id": 504866264},
    }
    per_sku = {"miss1": {"sold_count": 5}}
    result, fake_map, fake_per_sku, fake_apply = _run_collect(pids, metrics_map, per_sku)

    # 批量 map 调用一次（单次 data/v3）
    fake_map.assert_called_once_with(mock.ANY, company_id=None)
    # per-SKU 只收到未命中 pids
    assert fake_per_sku.call_count == 1
    assert fake_per_sku.call_args.args[1] == ["miss1"], \
        f"未命中才走 per-SKU, got {fake_per_sku.call_args.args[1]}"
    # apply 3 次: hit 用 map 指标, miss 用 per-SKU 指标
    assert fake_apply.call_count == 3
    applied = {call.args[0].ozon_product_id: call.args[1] for call in fake_apply.call_args_list}
    assert applied["hit1"] == metrics_map["hit1"]
    assert applied["hit2"] == metrics_map["hit2"]
    assert applied["miss1"] == per_sku["miss1"]


def test_all_hit_skips_per_sku_entirely():
    """全部命中 map → 不调 fetch_sales_analytics（1 次批量调用全覆盖）。"""
    pids = ["a", "b"]
    metrics_map = {"a": {"sold_count": 1}, "b": {"sold_count": 2}}
    result, fake_map, fake_per_sku, fake_apply = _run_collect(pids, metrics_map, {})
    fake_map.assert_called_once()
    fake_per_sku.assert_not_called()
    assert fake_apply.call_count == 2


def test_no_metrics_triggers_missing_warning():
    """map 空 + per-SKU 空 → 保留「运营数据全部缺失」warning（不崩）。"""
    pids = ["x"]
    with mock.patch.object(od, "_discover_workers", return_value=1), \
         mock.patch.object(od, "_analyze_product",
                           side_effect=lambda cdp_url, cdp, pid: _mk(pid)), \
         mock.patch.object(od, "_lazy_collect_urls", return_value=pids), \
         mock.patch("scripts.lib.cdp_client.CdpConnection") as conn_cls, \
         mock.patch.object(od, "_save_discovery_log"), \
         mock.patch(f"{SALES_MOD}.fetch_bestseller_metrics_map", return_value={}), \
         mock.patch(f"{SALES_MOD}.fetch_sales_analytics", return_value={}), \
         mock.patch(f"{SALES_MOD}.apply_analytics_to_candidate"), \
         mock.patch("scripts.lib.ozon_discovery.logger.warning") as warn, \
         mock.patch("time.sleep"):
        result = od.collect_and_analyze("http://127.0.0.1:9222", use_analytics=True)
    assert len(result) == 1
    msgs = [c.args[0] for c in warn.call_args_list]
    assert any("运营数据全部缺失" in str(m) for m in msgs), f"应有缺失 warning, got {msgs}"


if __name__ == "__main__":
    import traceback

    failed = total = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            total += 1
            try:
                fn()
                print(f"PASS {name}")
            except Exception:
                failed += 1
                print(f"FAIL {name}")
                traceback.print_exc()
    print(f"\n{total - failed}/{total} passed")
    sys.exit(1 if failed else 0)
