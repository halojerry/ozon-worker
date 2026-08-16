"""v0.55: 站点运营测试（mock Supabase 鉴权 + 真实 PG 站点表）。

验收门（系统设置-站点运营 P1）：
1. 公开 Banner 端点只返回 enabled=true（按 sort_order 升序）
2. 管理员创建 Banner → 201 + 出现在列表
3. 管理员更新 Banner → 字段变更
4. 管理员删除 Banner → 204 + 列表消失
5. 非管理员（role=1）写操作 → 403
6. announcement_type 非法 → 400
7. 公开通告端点只返回 enabled=true
8. 管理员通告 CRUD

需要本地 Docker PG；PG 不可达时 skip（表由 create_all 幂等创建）。
"""
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
from storage.database.shared.model import Base

DB_URL = os.environ.get(
    "PGDATABASE_URL",
    "postgresql://postgres:localdev123@localhost:5433/ozon",
)

# token key → (user_id, role)：admin=100(root)/user=1(common)，New API 整数角色体系
TOKENS = {
    "tok-admin": {"user_id": "u1", "key": "tok-admin", "remain_quota": 999,
                  "status": 1, "expired_time": -1, "unlimited_quota": True},
    "tok-user": {"user_id": "u2", "key": "tok-user", "remain_quota": 999,
                 "status": 1, "expired_time": -1, "unlimited_quota": True},
}
USERS = {
    "u1": {"id": "u1", "role": 100, "username": "boss"},
    "u2": {"id": "u2", "role": 1, "username": "user"},
}


class FakeSupabase:
    """tokens 表（鉴权）+ users 表（管理员判定）fake。"""

    def __init__(self, users: dict, tokens: dict):
        self._users = users
        self._tokens = tokens
        self._table = None
        self._eq = None

    def table(self, name):
        self._table = name
        return self

    def select(self, *cols, **kwargs):
        return self

    def eq(self, col, val):
        self._eq = (col, val)
        return self

    def is_(self, col, val):
        return self

    def limit(self, n):
        return self

    def execute(self):
        if self._table == "users":
            eq = self._eq
            data = [u for u in self._users.values() if eq is None or u.get(eq[0]) == str(eq[1])]
            return SimpleNamespace(data=data)
        if self._table == "tokens":
            eq = self._eq
            if eq and eq[0] == "key":
                rec = self._tokens.get(eq[1])
                return SimpleNamespace(data=[rec] if rec else [])
            return SimpleNamespace(data=[])
        return SimpleNamespace(data=[])


@pytest.fixture(scope="module")
def client():
    try:
        eng = create_engine(DB_URL)
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        eng.dispose()
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"PG 不可用（{exc}），跳过站点运营测试")
    # 幂等建表（checkfirst）：站点表随本测试文件首次落地
    eng = create_engine(DB_URL)
    Base.metadata.create_all(eng)
    eng.dispose()
    return TestClient(main_mod.app)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("CREDENTIAL_MASTER_KEY", "0123456789abcdef0123456789abcdef")
    monkeypatch.setenv("PGDATABASE_URL", DB_URL)
    with patch.object(main_mod.rate_limiter, "check", return_value=(True, 10)), \
         patch("main.get_supabase_client", return_value=FakeSupabase(USERS, TOKENS)):
        yield


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    eng = create_engine(DB_URL)
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM site_banners"))
        conn.execute(text("DELETE FROM site_announcements"))
    eng.dispose()


def _auth(token: str = "tok-admin") -> dict:
    return {"Authorization": f"Bearer {token}"}


def _mk_banner(image_url="https://img.example.com/b1.jpg", sort_order=0, enabled=True, title="B"):
    return {"image_url": image_url, "title": title, "sort_order": sort_order, "enabled": enabled}


def _mk_announcement(content="内容", announcement_type="banner", enabled=True, title="A"):
    return {"title": title, "content": content, "announcement_type": announcement_type, "enabled": enabled}


# ============================================================
# 1. 公开 Banner：只返回 enabled + sort_order 升序
# ============================================================

def test_public_banners_enabled_only(client):
    r1 = client.post("/api/v1/admin/site/banners", json=_mk_banner("a.jpg", sort_order=2), headers=_auth())
    assert r1.status_code == 201, r1.text
    r2 = client.post("/api/v1/admin/site/banners", json=_mk_banner("b.jpg", sort_order=1), headers=_auth())
    assert r2.status_code == 201, r2.text
    # 禁用第一个（sort_order=1）
    bid = r2.json()["id"]
    assert client.put(f"/api/v1/admin/site/banners/{bid}", json=_mk_banner("b.jpg", sort_order=1, enabled=False),
                      headers=_auth()).status_code == 200

    resp = client.get("/api/v1/site/banners")
    assert resp.status_code == 200
    banners = resp.json()
    assert len(banners) == 1
    assert banners[0]["image_url"] == "a.jpg"
    assert banners[0]["enabled"] is True


def test_public_banners_sort_order_asc(client):
    client.post("/api/v1/admin/site/banners", json=_mk_banner("z.jpg", sort_order=9), headers=_auth())
    client.post("/api/v1/admin/site/banners", json=_mk_banner("a.jpg", sort_order=1), headers=_auth())
    client.post("/api/v1/admin/site/banners", json=_mk_banner("m.jpg", sort_order=5), headers=_auth())
    resp = client.get("/api/v1/site/banners")
    orders = [b["sort_order"] for b in resp.json()]
    assert orders == sorted(orders)
    assert [b["image_url"] for b in resp.json()] == ["a.jpg", "m.jpg", "z.jpg"]


# ============================================================
# 2. 管理员创建 Banner → 201 + 列表可见
# ============================================================

def test_admin_create_banner_201(client):
    resp = client.post("/api/v1/admin/site/banners", json=_mk_banner("c.jpg", sort_order=3), headers=_auth())
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["id"] > 0
    assert body["image_url"] == "c.jpg"
    assert body["enabled"] is True

    lst = client.get("/api/v1/admin/site/banners", headers=_auth()).json()
    assert any(b["image_url"] == "c.jpg" for b in lst)


# ============================================================
# 3. 管理员更新 Banner
# ============================================================

def test_admin_update_banner(client):
    bid = client.post("/api/v1/admin/site/banners", json=_mk_banner("u1.jpg"), headers=_auth()).json()["id"]
    resp = client.put(f"/api/v1/admin/site/banners/{bid}",
                      json=_mk_banner("u2.jpg", sort_order=7, title="改名"), headers=_auth())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["image_url"] == "u2.jpg"
    assert body["sort_order"] == 7
    assert body["title"] == "改名"


def test_admin_update_banner_404(client):
    resp = client.put("/api/v1/admin/site/banners/99999999",
                      json=_mk_banner("x.jpg"), headers=_auth())
    assert resp.status_code == 404


# ============================================================
# 4. 管理员删除 Banner → 204 + 消失
# ============================================================

def test_admin_delete_banner_204(client):
    bid = client.post("/api/v1/admin/site/banners", json=_mk_banner("d.jpg"), headers=_auth()).json()["id"]
    resp = client.delete(f"/api/v1/admin/site/banners/{bid}", headers=_auth())
    assert resp.status_code == 204
    lst = client.get("/api/v1/admin/site/banners", headers=_auth()).json()
    assert all(b["id"] != bid for b in lst)


# ============================================================
# 5. 非管理员写操作 → 403
# ============================================================

def test_non_admin_create_forbidden_403(client):
    resp = client.post("/api/v1/admin/site/banners", json=_mk_banner("x.jpg"), headers=_auth("tok-user"))
    assert resp.status_code == 403


def test_non_admin_update_forbidden_403(client):
    bid = client.post("/api/v1/admin/site/banners", json=_mk_banner("y.jpg"), headers=_auth()).json()["id"]
    resp = client.put(f"/api/v1/admin/site/banners/{bid}", json=_mk_banner("z.jpg"), headers=_auth("tok-user"))
    assert resp.status_code == 403


# ============================================================
# 6. announcement_type 非法 → 400
# ============================================================

def test_announcement_type_invalid_400(client):
    resp = client.post("/api/v1/admin/site/announcements",
                       json=_mk_announcement(announcement_type="weird"), headers=_auth())
    assert resp.status_code == 400
    assert "announcement_type" in resp.json()["detail"]


# ============================================================
# 7. 公开通告：只返回 enabled
# ============================================================

def test_public_announcements_enabled_only(client):
    client.post("/api/v1/admin/site/announcements", json=_mk_announcement("公开1"), headers=_auth())
    aid = client.post("/api/v1/admin/site/announcements", json=_mk_announcement("隐藏"), headers=_auth()).json()["id"]
    client.put(f"/api/v1/admin/site/announcements/{aid}",
               json=_mk_announcement("隐藏", enabled=False), headers=_auth())
    resp = client.get("/api/v1/site/announcements")
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 1
    assert items[0]["content"] == "公开1"


# ============================================================
# 8. 管理员通告 CRUD + popup 类型
# ============================================================

def test_admin_announcements_crud(client):
    # create
    resp = client.post("/api/v1/admin/site/announcements",
                       json=_mk_announcement("弹窗通告", announcement_type="popup"), headers=_auth())
    assert resp.status_code == 201, resp.text
    aid = resp.json()["id"]
    assert resp.json()["announcement_type"] == "popup"
    # list
    assert any(a["id"] == aid for a in client.get("/api/v1/admin/site/announcements", headers=_auth()).json())
    # update
    resp = client.put(f"/api/v1/admin/site/announcements/{aid}",
                      json=_mk_announcement("更新后", announcement_type="banner"), headers=_auth())
    assert resp.status_code == 200
    assert resp.json()["content"] == "更新后"
    assert resp.json()["announcement_type"] == "banner"
    # delete
    assert client.delete(f"/api/v1/admin/site/announcements/{aid}", headers=_auth()).status_code == 204
    lst = client.get("/api/v1/admin/site/announcements", headers=_auth()).json()
    assert all(a["id"] != aid for a in lst)


# ============================================================
# 9. 服务层直接单测（不依赖 HTTP 层）
# ============================================================

def test_service_create_and_list_banners():
    from services import site_service
    row = site_service.create_banner({"image_url": "s.jpg", "title": "服务层", "sort_order": 4})
    assert row["id"] > 0
    banners = site_service.list_banners()
    assert any(b["id"] == row["id"] for b in banners)
    pub = site_service.list_public_banners()
    assert any(b["id"] == row["id"] for b in pub)


def test_service_update_delete_banner_not_found():
    from services import site_service
    assert site_service.update_banner(99999999, {"title": "x"}) is None
    assert site_service.delete_banner(99999999) is False
