"""促销活动商品管理 v2 迁移契约锁定（2026-09-22 Ozon 公告，2026-10-13 v1 停用）。

背景：`/v1/actions/products/activate` 与 v1 `/v1/actions/products` 自 2026-10-13 起停用。
本仓零调用方（actions_register 走 seller-actions 族不受影响），本批把 promo_client
迁到 v2 四方法并锁契约——**零调用方方法的契约锁就是这些测试**，改端点/请求体形状
必须同步改这里。

契约来源：2026-09-22 Ozon 公告（本地 swagger 快照未收录 v2，已注明首次生产调用前
对线上 swagger 实证）。
"""
from __future__ import annotations

from unittest.mock import patch

import pytest


def _fake_ozon(result=None):
    def _fake(client_id, api_key, endpoint, body, timeout=60, language="ZH_HANS"):
        calls = getattr(_fake, "calls", [])
        calls.append({"endpoint": endpoint, "body": body})
        _fake.calls = calls
        return {"result": result if result is not None else {"ok": True}}
    _fake.calls = []
    return _fake


# ============================================================
# 1. action_products 已迁 /v2/actions/products（v1 2026-10-13 停用）
# ============================================================

def test_action_products_migrated_to_v2():
    from utils import promo_client
    fake = _fake_ozon({"items": []})
    with patch("utils.ozon_client.ozon_post", fake):
        r = promo_client.action_products("cid", "key", action_id=123)
    assert fake.calls[0]["endpoint"] == "/v2/actions/products"
    # 缺省只发 action_id（offset=0/limit 未设不再硬塞——v2 分页形状待线上实证）
    assert fake.calls[0]["body"] == {"action_id": 123}
    assert r == {"items": []}
    assert promo_client.METHOD_ENDPOINTS["action_products"] == "/v2/actions/products"


def test_action_products_v2_optional_pagination_passthrough():
    from utils import promo_client
    fake = _fake_ozon()
    with patch("utils.ozon_client.ozon_post", fake):
        promo_client.action_products("cid", "key", action_id=1, offset=100, limit=50)
    assert fake.calls[0]["body"] == {"action_id": 1, "offset": 100, "limit": 50}


# ============================================================
# 2. update_action_products（取代 activate 的通用增删）
# ============================================================

def test_update_action_products_contract():
    from utils import promo_client
    fake = _fake_ozon({
        "active_product_ids": [101, 102],
        "deactivated_product_ids": [103],
        "rejected": [],
        "warnings": [],
    })
    products = [
        {"product_id": 101, "action_price": "199.00"},
        {"product_id": 103, "action_price": "899.00", "stock": 5},
    ]
    with patch("utils.ozon_client.ozon_post", fake):
        r = promo_client.update_action_products("cid", "key", action_id=7, products=products)
    assert fake.calls[0]["endpoint"] == "/v1/actions/products/update"
    assert fake.calls[0]["body"] == {"action_id": 7, "products": products}
    # 响应两列表分拣语义（不再有统一 product_ids）
    assert r["active_product_ids"] == [101, 102]
    assert r["deactivated_product_ids"] == [103]


# ============================================================
# 3. deactivate_action_products（促销码类强制排除，≤1000）
# ============================================================

def test_deactivate_action_products_contract():
    from utils import promo_client
    fake = _fake_ozon([201, 202])
    with patch("utils.ozon_client.ozon_post", fake):
        r = promo_client.deactivate_action_products("cid", "key", action_id=9, product_ids=[201, 202])
    assert fake.calls[0]["endpoint"] == "/v2/actions/products/deactivate"
    assert fake.calls[0]["body"] == {"action_id": 9, "product_ids": [201, 202]}
    # 响应是扁平数组（无 result 对象嵌套），原样返回
    assert r == [201, 202]


def test_deactivate_action_products_over_1000_rejected():
    from utils import promo_client
    with pytest.raises(ValueError, match="1000"):
        promo_client.deactivate_action_products(
            "cid", "key", action_id=1, product_ids=list(range(1001)))


# ============================================================
# 4. list_action_candidates（limit≤100 + last_id 指针分页）
# ============================================================

def test_list_action_candidates_contract():
    from utils import promo_client
    fake = _fake_ozon({"items": [{"id": 1}], "last_id": 55})
    with patch("utils.ozon_client.ozon_post", fake):
        r = promo_client.list_action_candidates("cid", "key", action_id=4, limit=100, last_id=55)
    assert fake.calls[0]["endpoint"] == "/v2/actions/candidates"
    assert fake.calls[0]["body"] == {"action_id": 4, "limit": 100, "last_id": 55}
    assert r["last_id"] == 55


def test_list_action_candidates_limit_bounds():
    from utils import promo_client
    for bad in (0, 101, -1):
        with pytest.raises(ValueError, match="limit"):
            promo_client.list_action_candidates("cid", "key", action_id=1, limit=bad)


# ============================================================
# 5. v1 死端点绝迹（activate 从未实现；v1 商品列表已迁走）
# ============================================================

def test_dead_v1_endpoints_absent():
    from utils import promo_client
    all_eps = set(promo_client.ALLOWED_ENDPOINTS) | set(promo_client.METHOD_ENDPOINTS.values())
    assert "/v1/actions/products/activate" not in all_eps, "activate 已停用（2026-10-13），禁止复用"
    assert "/v1/actions/products" not in all_eps, "v1 商品列表已停用，必须走 /v2/actions/products"
    # seller-actions 族不受本次公告影响，应保持
    assert "/v1/seller-actions/products/add" in all_eps
