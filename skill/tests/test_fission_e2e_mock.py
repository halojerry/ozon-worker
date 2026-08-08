"""ozon_fission e2e mock 测试（v0.31 P5）— 3 种子 depth=1 + source_chain。"""
from __future__ import annotations

import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "lib"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import ozon_discovery
import ozon_fission
from ozon_discovery import ProductCandidate


def _mk_seed(pid: str, sellers: list) -> ProductCandidate:
    c = ProductCandidate(ozon_product_id=pid, ozon_title=f"种子{pid}", ozon_price=1500)
    c.competing_sellers = len(sellers)
    c.competing_seller_list = sellers
    return c


def test_e2e_three_seeds_depth1_mocked():
    """3 种子 depth=1 全 mock：<5s 完成，候选含 source_chain，环路段截断。"""
    sellers_a = [
        {"seller_id": "10001", "seller_name": "卖家A"},
        {"seller_id": "10002", "seller_name": "卖家B"},
    ]
    sellers_b = [
        {"seller_id": "10002", "seller_name": "卖家B"},  # 与 A 重复 → visited 截断
        {"seller_id": "10003", "seller_name": "卖家C"},
    ]
    seeds = [_mk_seed("A", sellers_a), _mk_seed("B", sellers_b), _mk_seed("C", [])]

    store_products = {
        "10001": ["A1", "A2"],
        "10002": ["B1"],
        "10003": ["C1", "C2", "C3"],
    }

    with mock.patch("scripts.lib.cdp_client.CdpConnection") as conn_cls:
        conn = mock.MagicMock()
        conn_cls.return_value = conn
        with mock.patch.object(ozon_discovery, "fetch_seller_products",
                               side_effect=lambda **kw: store_products.get(kw["seller_id"], [])):
            with mock.patch.object(ozon_discovery, "_analyze_product") as ap:
                ap.side_effect = lambda cdp_url, cdp, pid: _mk_seed(pid, [])
                with mock.patch("scripts.lib.ozon_widget.fetch_competing_sellers") as fcs:
                    fcs.side_effect = lambda cdp_url, pid, cdp: {"count": 0, "min_price": 0, "sellers": []}
                    t0 = __import__("time").time()
                    result = ozon_fission.run_fission(
                        seed_products=seeds, max_depth=1,
                        max_total_products=20, max_sellers_per_product=10,
                        max_products_per_seller=5, time_budget=30,
                    )
                    elapsed = __import__("time").time() - t0
    assert elapsed < 5, f"e2e mock 应 <5s, got {elapsed:.1f}s"
    assert len(result) >= 6, f"3 种子 + 至少 3 裂变商品, got {len(result)}"
    fissioned = [c for c in result if c.chain_depth > 0]
    assert fissioned, "应有裂变深度 >0 的候选"
    assert all(c.source_chain for c in fissioned), "裂变候选应带 source_chain"
    chain = fissioned[0].source_chain
    assert chain[0]["type"] == "seller", f"证据链首跳应为 seller, got {chain[0]}"
    assert chain[0]["id"] == "10001" or chain[0]["id"] == "10002", f"卖家 ID 来自种子 A, got {chain[0]}"


def test_e2e_loop_truncated_by_visited():
    """卖家 B 在种子 A 和 B 都出现 → 只展开一次（visited_sellers 截断）。"""
    sellers_a = [{"seller_id": "10001", "seller_name": "卖家A"}]
    sellers_b = [{"seller_id": "10001", "seller_name": "卖家A"}]
    seeds = [_mk_seed("A", sellers_a), _mk_seed("B", sellers_b)]

    with mock.patch("scripts.lib.cdp_client.CdpConnection") as conn_cls:
        conn_cls.return_value = mock.MagicMock()
        with mock.patch.object(ozon_discovery, "fetch_seller_products",
                               side_effect=lambda **kw: ["P1"]):
            with mock.patch.object(ozon_discovery, "_analyze_product",
                                   side_effect=lambda cdp_url, cdp, pid: _mk_seed(pid, [])):
                with mock.patch("scripts.lib.ozon_widget.fetch_competing_sellers",
                                       side_effect=lambda cdp_url, pid, cdp: {"count": 0, "min_price": 0, "sellers": []}):
                    result = ozon_fission.run_fission(
                        seed_products=seeds, max_depth=1, max_total_products=20,
                        max_sellers_per_product=10, max_products_per_seller=5, time_budget=30,
                    )
    seller_fetches = [c for c in result]
    assert len(seller_fetches) >= 3, f"种子2 + 裂变1 = 3, got {len(seller_fetches)}"
    # 卖家 10001 只应被展开一次：种子 A + 种子 B + 店铺产品 P1 = 3 个候选（去重后）
    # 如果 visited 失效，P1 会被重复添加（A→P1, B→P1 = 4 个）
    assert len(seller_fetches) == 3, f"visited 截断应产生 3 候选, got {len(seller_fetches)}"


if __name__ == "__main__":
    import traceback
    failed = total = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            total += 1
            try:
                fn(); print(f"PASS {name}")
            except Exception:
                failed += 1; print(f"FAIL {name}"); traceback.print_exc()
    print(f"\n{total - failed}/{total} passed")
    sys.exit(1 if failed else 0)
