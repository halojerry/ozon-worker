"""v0.56: 店铺数据缓存同步测试（真实 PG + mock Ozon API）。

验收：
1. 租户隔离硬约束：A 看不到 B 的店铺缓存（orders/products/sync-status 均按 tenant 过滤）
2. upsert 覆盖：订单状态变化/商品价格变化 → 覆盖更新不重复
3. 商品全量同步：本次未出现 → archived=True（不硬删）
4. 懒同步：从未同步 → 读取前自动同步；已同步 → 直接读 PG 不调 Ozon
5. 手动同步端点：POST /stores/{id}/sync + GET sync-status（跨租户 404）
6. 调度器：遍历全部租户 active 凭证逐店同步

需要本地 Docker PG；PG 不可达时 skip。
"""
import asyncio
import datetime
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import main as main_mod

DB_URL = os.environ.get(
    "PGDATABASE_URL",
    "postgresql://postgres:localdev123@localhost:5433/ozon",
)

MASTER_KEY = "0123456789abcdef0123456789abcdef"

TOKEN_MAP = {"tokA": "tenant-a", "tokB": "tenant-b"}


class FakeSupabase:
    def __init__(self):
        self._tokens = FakeTokensTable()

    def table(self, name):
        if name == "tokens":
            return self._tokens
        raise AssertionError(f"unexpected table {name}")


class FakeTokensTable:
    def __init__(self):
        self._rows = [
            {"user_id": "tenant-a", "key": "tokA", "status": 1, "deleted_at": None},
            {"user_id": "tenant-b", "key": "tokB", "status": 1, "deleted_at": None},
        ]
        self._eqs: list = []

    def select(self, *cols):
        return self

    def eq(self, col, val):
        self._eqs.append((col, val))
        return self

    def is_(self, col, val):
        self._eqs.append((col, val))
        return self

    def limit(self, n):
        return self

    def execute(self):
        # 每次查询独立：清空累积的 eq 条件（fake 被多请求共享）
        eqs, self._eqs = self._eqs, []
        filtered = self._rows
        for col, val in eqs:
            if val == "null":
                filtered = [r for r in filtered if r.get(col) is None]
            else:
                filtered = [r for r in filtered if str(r.get(col)) == str(val)]
        return SimpleNamespace(data=filtered[:1])


@pytest.fixture(scope="module")
def client():
    try:
        eng = create_engine(DB_URL)
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        pytest.skip("本地 PG 不可达")
    # 测试环境必需：凭证加密密钥 + 禁僵尸恢复（AGENTS.md 红线——防本地测试复活旧任务真实上架）
    os.environ["CREDENTIAL_MASTER_KEY"] = MASTER_KEY
    os.environ["SKIP_ZOMBIE_RECOVERY"] = "1"
    os.environ["SKIP_FAILED_REVIVE"] = "1"
    with patch("main.get_supabase_client",
               return_value=FakeSupabase()):
        with TestClient(main_mod.app) as c:
            yield c


def _auth_headers(tenant: str) -> dict:
    token = "tokA" if tenant == "tenant-a" else "tokB"
    return {"Authorization": f"Bearer sk-{token}"}


def _db(sql: str, params: dict | None = None) -> list:
    eng = create_engine(DB_URL)
    with eng.connect() as conn:
        return conn.execute(text(sql), params or {}).fetchall()


def _db_execute(sql: str, params: dict | None = None) -> None:
    eng = create_engine(DB_URL)
    with eng.begin() as conn:
        conn.execute(text(sql), params or {})


def _create_cred(client, tenant: str, cid: str = "1010", key: str = "k1010") -> dict:
    resp = client.post("/api/v1/credentials", json={
        "ozon_client_id": cid, "api_key": key, "shop_name": f"店-{tenant}",
    }, headers=_auth_headers(tenant))
    assert resp.status_code == 201, resp.text
    return resp.json()


def _cleanup():
    for t in ("ozon_orders_cache", "ozon_products_cache", "credential_sync_state"):
        _db_execute(f"DELETE FROM {t}")
    # 清测试租户凭证（credentials 409 唯一约束——防上次运行残留）
    _db_execute("DELETE FROM credentials WHERE tenant_id IN ('tenant-a', 'tenant-b')")


# 模拟 Ozon 响应（v4 posting/fbs/list 扁平结构 + 商品 info 含主图）
def _ozon_post_ok(method_calls, tenant):
    """mock ozon_post：orders 1 页（v4）+ products 1 页 + info 详情。"""
    def _handler(client_id, api_key, path, body=None, **kw):
        method_calls.append((path, body))
        if path == "/v4/posting/fbs/list":
            # v4：扁平响应（cursor/has_next/postings），products[].price 对象，
            # financial_data commission 对象、product_id 与 posting sku 同值
            now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            return {"has_next": False, "cursor": "", "postings": [{
                "posting_number": f"PN-{tenant}-1",
                "status": "delivered",
                "in_process_at": now,
                "products": [{
                    "name": "Товар", "sku": 1, "quantity": 1,
                    "price": {"amount": "18.00", "currency": "RUB"}, "offer_id": "o1",
                }],
                "financial_data": {"products": [
                    {"price": 18.0, "quantity": 1, "product_id": 1,
                     "commission": {"amount": 1.8, "currency": "RUB", "percent": 10}},
                ], "services": []},
                "analytics_data": {"warehouse": "wh-1"},
                "delivery_method": {"name": "RETS"},
            }]}
        if path == "/v3/product/list":
            return {"result": {"total": 1, "items": [
                {"product_id": 555, "offer_id": f"offer-{tenant}"},
            ]}}
        if path == "/v3/product/info/list":
            return {"result": {"items": [
                {"product_id": 555, "name": "Товар 555", "images": ["http://img/1.jpg"],
                 "price": {"price": 999.0}, "stocks": {"present": 7}},
                {"product_id": 1, "name": "Товар 1", "images": ["http://img/order-1.jpg"],
                 "price": {"price": 18.0}, "stocks": {"present": 1}},
            ]}}
        return {"result": {}}
    return _handler


# ──────────────────────────────
# 1. 同步 + 缓存读取
# ──────────────────────────────


def test_sync_and_cached_reads(client):
    """同步后：订单/商品落 PG，读取走缓存（第二次不调 Ozon）。"""
    _cleanup()
    c = _create_cred(client, "tenant-a")
    calls: list = []
    with patch("utils.ozon_client.ozon_post",
               side_effect=_ozon_post_ok(calls, "A")):
        # 手动同步端点
        resp = client.post(f"/api/v1/stores/{c['id']}/sync", headers=_auth_headers("tenant-a"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["orders"]["synced"] == 1
    assert body["products"]["synced"] == 1

    # 缓存行落库
    orders = _db("SELECT posting_number, status, profit FROM ozon_orders_cache")
    assert len(orders) == 1
    assert orders[0].posting_number == "PN-A-1"
    assert orders[0].status == "delivered"
    products = _db("SELECT product_id, offer_id, price, stock, archived FROM ozon_products_cache")
    assert len(products) == 1
    assert products[0].price == 999.0
    assert products[0].stock == 7
    assert products[0].archived is False

    # T4.3：订单商品图缓存进 products JSONB（v4 同步时按 product_id 拉主图）
    cached = _db("SELECT products FROM ozon_orders_cache")
    assert cached[0][0][0]["image"] == "http://img/order-1.jpg"
    assert cached[0][0][0]["product_id"] == 1
    # v4 金额适配：financial price=18，commission 对象 amount=1.8 → profit=16.2
    assert cached[0][0][0]["price"] == 18.0

    # 同步状态
    st = client.get(f"/api/v1/stores/{c['id']}/sync-status", headers=_auth_headers("tenant-a"))
    assert st.status_code == 200
    assert st.json()["orders_last_synced_at"] is not None

    # 读取走缓存：已同步 → 不触发 Ozon（calls 数不变）
    calls.clear()
    with patch("utils.ozon_client.ozon_post",
               side_effect=_ozon_post_ok(calls, "A")):
        r = client.get(f"/api/v1/orders?credential_id={c['id']}", headers=_auth_headers("tenant-a"))
    assert r.status_code == 200
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["posting_number"] == "PN-A-1"
    # T4.3：响应商品行含 image + product_id
    assert r.json()["items"][0]["products"][0]["image"] == "http://img/order-1.jpg"
    assert r.json()["items"][0]["products"][0]["product_id"] == 1
    assert calls == []  # 缓存命中（图已随同步缓存），零 Ozon 调用

    pr = client.get(f"/api/v1/products/ozon?credential_id={c['id']}", headers=_auth_headers("tenant-a"))
    assert pr.status_code == 200
    assert pr.json()["total"] == 1
    assert pr.json()["items"][0]["product_id"] == "555"
    assert calls == []  # 同上


def test_store_stats_today_aggregation(client):
    """T4.6：店铺卡统计聚合今日订单数/销售额/佣金/利润（无评分字段）。"""
    _cleanup()
    c = _create_cred(client, "tenant-a", cid="1111", key="k1111")
    calls: list = []
    with patch("utils.ozon_client.ozon_post",
               side_effect=_ozon_post_ok(calls, "A")):
        client.post(f"/api/v1/stores/{c['id']}/sync", headers=_auth_headers("tenant-a"))
    # 订单 created_at 为今天（UTC）→ 计入今日统计
    stats = client.get(f"/api/v1/stores/{c['id']}/stats", headers=_auth_headers("tenant-a"))
    assert stats.status_code == 200
    body = stats.json()
    assert body["today_orders"] == 1
    assert body["today_sales_amount"] == 18.0   # financial price=18（v4 对象适配）
    assert body["today_commission"] == 1.8      # commission.amount 对象适配
    assert body["today_profit"] == 16.2
    assert body["today_product_count"] == 1
    assert "rating" not in body  # ⚠️ 缓存无评分字段，卡片不显示评分
    # 无鉴权 → 401
    r = client.get(f"/api/v1/stores/{c['id']}/stats")
    assert r.status_code == 401


def test_lazy_sync_on_first_read(client):
    """从未同步 → 首次读取自动同步（懒同步）；之后走缓存。"""
    _cleanup()
    c = _create_cred(client, "tenant-a", cid="2020", key="k2020")
    calls: list = []
    with patch("utils.ozon_client.ozon_post",
               side_effect=_ozon_post_ok(calls, "B")):
        r = client.get(f"/api/v1/orders?credential_id={c['id']}", headers=_auth_headers("tenant-a"))
    assert r.status_code == 200
    assert r.json()["total"] == 1  # 懒同步完成并返回
    assert any(p == "/v4/posting/fbs/list" for p, _ in calls)  # 首次调了 Ozon（v4）


def test_refresh_forces_sync(client):
    """?refresh=1 → 强制重新同步（即使已有缓存）。"""
    _cleanup()
    c = _create_cred(client, "tenant-a", cid="3030", key="k3030")
    calls: list = []
    with patch("utils.ozon_client.ozon_post",
               side_effect=_ozon_post_ok(calls, "C")):
        client.get(f"/api/v1/orders?credential_id={c['id']}", headers=_auth_headers("tenant-a"))
        n1 = len([x for x in calls if x[0] == "/v4/posting/fbs/list"])
        client.get(f"/api/v1/orders?credential_id={c['id']}&refresh=1", headers=_auth_headers("tenant-a"))
        n2 = len([x for x in calls if x[0] == "/v4/posting/fbs/list"])
    assert n2 > n1  # refresh 触发第二次同步


def test_upsert_overwrites_order_status(client):
    """订单状态变化 → upsert 覆盖（行数不变，状态更新）。"""
    _cleanup()
    c = _create_cred(client, "tenant-a", cid="4040", key="k4040")

    def _status(status):
        def _handler(client_id, api_key, path, body=None, **kw):
            if path == "/v4/posting/fbs/list":
                return {"has_next": False, "cursor": "", "postings": [{
                    "posting_number": "PN-UPSERT",
                    "status": status,
                    "in_process_at": "2026-07-19T06:19:51+00:00",
                    "products": [],
                    "financial_data": {"products": [], "services": []},
                    "analytics_data": {},
                    "delivery_method": {},
                }]}
            return {"result": {"total": 0, "items": []}}
        return _handler

    with patch("utils.ozon_client.ozon_post", side_effect=_status("awaiting_packaging")):
        client.post(f"/api/v1/stores/{c['id']}/sync", headers=_auth_headers("tenant-a"))
    with patch("utils.ozon_client.ozon_post", side_effect=_status("delivered")):
        client.post(f"/api/v1/stores/{c['id']}/sync", headers=_auth_headers("tenant-a"))
    rows = _db("SELECT COUNT(*) FROM ozon_orders_cache WHERE posting_number='PN-UPSERT'")
    assert rows[0][0] == 1  # 不重复
    row = _db("SELECT status FROM ozon_orders_cache WHERE posting_number='PN-UPSERT'")
    assert row[0][0] == "delivered"  # 覆盖为新状态


def test_products_archive_missing(client):
    """商品全量同步：本次未出现 → archived=True。"""
    _cleanup()
    c = _create_cred(client, "tenant-a", cid="5050", key="k5050")

    def _with_items(items):
        def _handler(client_id, api_key, path, body=None, **kw):
            if path == "/v3/product/list":
                return {"result": {"total": len(items), "items": items}}
            if path == "/v3/product/info/list":
                return {"result": {"items": [
                    {**it, "name": f"n-{it['product_id']}"} for it in items]}
                }
            return {"result": {"total": 0, "postings": []}}
        return _handler

    with patch("utils.ozon_client.ozon_post",
               side_effect=_with_items([{"product_id": 111, "offer_id": "o111"}])):
        client.post(f"/api/v1/stores/{c['id']}/sync", headers=_auth_headers("tenant-a"))
    with patch("utils.ozon_client.ozon_post",
               side_effect=_with_items([{"product_id": 111, "offer_id": "o111"},
                                        {"product_id": 222, "offer_id": "o222"}])):
        client.post(f"/api/v1/stores/{c['id']}/sync", headers=_auth_headers("tenant-a"))

    rows = _db("SELECT product_id, archived FROM ozon_products_cache ORDER BY product_id")
    assert len(rows) == 2
    by_pid = {r.product_id: r.archived for r in rows}
    assert by_pid["111"] is False   # 仍在线
    assert by_pid["222"] is False   # 新商品
    # 再同步一次（111 消失）→ 111 archived
    with patch("utils.ozon_client.ozon_post",
               side_effect=_with_items([{"product_id": 222, "offer_id": "o222"}])):
        client.post(f"/api/v1/stores/{c['id']}/sync", headers=_auth_headers("tenant-a"))
    rows = _db("SELECT product_id, archived FROM ozon_products_cache ORDER BY product_id")
    by_pid = {r.product_id: r.archived for r in rows}
    assert by_pid["111"] is True
    assert by_pid["222"] is False


# ──────────────────────────────
# 2. 租户隔离（硬约束）
# ──────────────────────────────


def test_tenant_isolation_cross_tenant_404(client):
    """A 拿 B 的 credential_id → 404（get_decrypted 归属校验）。"""
    _cleanup()
    ca = _create_cred(client, "tenant-a", cid="6060", key="k6060")
    cb = _create_cred(client, "tenant-b", cid="7070", key="k7070")
    with patch("utils.ozon_client.ozon_post", side_effect=_ozon_post_ok([], "A")):
        # A 同步 B 的店 → 404
        r = client.post(f"/api/v1/stores/{cb['id']}/sync", headers=_auth_headers("tenant-a"))
        assert r.status_code == 404
        # A 读 B 的订单缓存 → 404
        r = client.get(f"/api/v1/orders?credential_id={cb['id']}", headers=_auth_headers("tenant-a"))
        assert r.status_code == 404
        # A 读 B 的商品缓存 → 404
        r = client.get(f"/api/v1/products/ozon?credential_id={cb['id']}", headers=_auth_headers("tenant-a"))
        assert r.status_code == 404
        # A 查 B 的同步状态 → 404
        r = client.get(f"/api/v1/stores/{cb['id']}/sync-status", headers=_auth_headers("tenant-a"))
        assert r.status_code == 404
        # A 查 B 的店铺卡统计 → 404（T4.6 租户隔离锁定）
        r = client.get(f"/api/v1/stores/{cb['id']}/stats", headers=_auth_headers("tenant-a"))
        assert r.status_code == 404


def test_tenant_isolation_data_not_leaked(client):
    """A 同步后：B 读自己店看不到 A 的数据；A 读自己店看不到 B 的数据。"""
    _cleanup()
    ca = _create_cred(client, "tenant-a", cid="8080", key="k8080")
    cb = _create_cred(client, "tenant-b", cid="9090", key="k9090")
    with patch("utils.ozon_client.ozon_post", side_effect=_ozon_post_ok([], "A")):
        client.post(f"/api/v1/stores/{ca['id']}/sync", headers=_auth_headers("tenant-a"))
    with patch("utils.ozon_client.ozon_post", side_effect=_ozon_post_ok([], "B")):
        client.post(f"/api/v1/stores/{cb['id']}/sync", headers=_auth_headers("tenant-b"))

    # A 看自己的店：只有 A 的订单
    ra = client.get(f"/api/v1/orders?credential_id={ca['id']}", headers=_auth_headers("tenant-a"))
    assert ra.json()["total"] == 1
    assert ra.json()["items"][0]["posting_number"] == "PN-A-1"
    # B 看自己的店：只有 B 的订单
    rb = client.get(f"/api/v1/orders?credential_id={cb['id']}", headers=_auth_headers("tenant-b"))
    assert rb.json()["total"] == 1
    assert rb.json()["items"][0]["posting_number"] == "PN-B-1"
    # 数据库层面：每行 tenant_id 正确
    rows = _db("SELECT tenant_id, COUNT(*) FROM ozon_orders_cache GROUP BY tenant_id ORDER BY tenant_id")
    assert [(r.tenant_id, r[1]) for r in rows] == [("tenant-a", 1), ("tenant-b", 1)]


# ──────────────────────────────
# 3. 调度器
# ──────────────────────────────


def test_scheduler_syncs_all_tenants():
    """调度器遍历全部租户 active 凭证逐店同步。"""
    from services import store_sync_scheduler
    from services.store_sync_service import sync_store

    class _FakeRow:
        def __init__(self, tenant, cid):
            self.tenant_id = tenant
            self.id = cid

    fake_rows = [_FakeRow("tenant-a", "aaaa-0000"), _FakeRow("tenant-b", "bbbb-0000")]
    with patch.object(store_sync_scheduler, "_all_active_credentials",
                      return_value=[(r.tenant_id, str(r.id)) for r in fake_rows]), \
         patch.object(store_sync_scheduler, "store_sync_service") as svc, \
         patch.object(store_sync_scheduler, "SYNC_STORE_GAP_SECONDS", 0.0):
        asyncio.run(store_sync_scheduler.sync_all_now())
    assert svc.sync_store.call_count == 2


def test_sync_all_now_failure_isolation():
    """单店同步失败不阻断其他店。"""
    from services import store_sync_scheduler

    with patch.object(store_sync_scheduler, "_all_active_credentials",
                      return_value=[("t1", "c1"), ("t2", "c2")]), \
         patch.object(store_sync_scheduler, "store_sync_service") as svc, \
         patch.object(store_sync_scheduler, "SYNC_STORE_GAP_SECONDS", 0.0):
        svc.sync_store.side_effect = [RuntimeError("boom"), None]
        results = asyncio.run(store_sync_scheduler.sync_all_now())
    assert results == {"stores": 2, "ok": 1, "failed": 1}
