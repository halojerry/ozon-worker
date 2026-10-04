"""收敛批②回归（2026-10 W4，A4/B5/B6）。

- A4 ``ozon_post_expect_items``：限流静默空 items 退避重试 + 网络异常重试 +
  耗尽返空列表（三态）；三形状提取（顶层 items / result.items / result 列表）；
  三处调用方（shelf/store_sync/card_audit）收敛断言。
- B5 ``api.security.authenticate_admin``：行为等价（无 token 401 / 非 admin 403 /
  admin 通过）+ 八文件别名同一性 + 防「本地定义」复发棘轮。
- 纯 mock，无网络无 PG。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from api.security import authenticate_admin
from utils import ozon_client
from utils.ozon_client import ozon_post_expect_items


# ---------------------------------------------------------------------------
# A4: ozon_post_expect_items 三态
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_real_io():
    """退避 sleep 置空 + 限流器放行（与 test_ozon_client_enhanced 同口径）。"""
    with patch("time.sleep", return_value=None) as _sleep, \
         patch.object(ozon_client._rate_limiter, "acquire", return_value=True):
        yield _sleep


def test_expect_items_empty_then_success_backoff():
    """空 items → 退避 1s 后重试成功；第二次调用返回 items。"""
    sleeps: list[float] = []
    calls: list[dict] = []

    def _fake(client_id, api_key, endpoint, body, timeout=60, language="ZH_HANS"):
        calls.append(endpoint)
        if len(calls) == 1:
            return {"result": {}}  # 限流静默空（旧形状 result.items 缺失）
        return {"items": [{"id": 1, "name": "x"}]}  # 新形状顶层 items

    with patch.object(ozon_client, "ozon_post", side_effect=_fake), \
         patch("time.sleep", side_effect=sleeps.append):
        out = ozon_post_expect_items("cid", "key", "/v3/product/info/list", {"product_id": [1]})

    assert out == [{"id": 1, "name": "x"}]
    assert len(calls) == 2
    assert sleeps == [1]  # 首次失败后退避 1s


def test_expect_items_exception_then_success():
    """网络异常 → 退避重试成功（info 是幂等读）。"""
    calls: list[int] = []

    def _fake(client_id, api_key, endpoint, body, timeout=60, language="ZH_HANS"):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("conn reset")
        return {"result": {"items": [{"id": 7}]}}

    with patch.object(ozon_client, "ozon_post", side_effect=_fake):
        out = ozon_post_expect_items("cid", "key", "/v3/product/info/list", {"product_id": [7]})

    assert out == [{"id": 7}]
    assert len(calls) == 2


def test_expect_items_exhausted_empty_returns_empty_list():
    """三态之「耗尽」：全空 → 返 []（绝不 raise、绝不误判异常），恰好 3 次 + 1s/2s 退避。"""
    calls: list[int] = []
    sleeps: list[float] = []

    def _fake(client_id, api_key, endpoint, body, timeout=60, language="ZH_HANS"):
        calls.append(1)
        return {"result": {}}  # 恒空（疑似限流）

    with patch.object(ozon_client, "ozon_post", side_effect=_fake), \
         patch("time.sleep", side_effect=sleeps.append):
        out = ozon_post_expect_items("cid", "key", "/v3/product/info/list", {"product_id": [1]})

    assert out == []
    assert len(calls) == 3
    assert sleeps == [1, 2]


def test_expect_items_exhausted_exception_returns_empty_list():
    """三态之「异常耗尽」：恒异常 → 返 [] 不上抛（调用方按降级处理）。"""
    calls: list[int] = []

    def _fake(client_id, api_key, endpoint, body, timeout=60, language="ZH_HANS"):
        calls.append(1)
        raise TimeoutError("boom")

    with patch.object(ozon_client, "ozon_post", side_effect=_fake):
        out = ozon_post_expect_items("cid", "key", "/v4/product/info/attributes", {})

    assert out == []
    assert len(calls) == 3


def test_expect_items_attempts_param():
    """attempts 可调：=2 → 恰好 2 次。"""
    calls: list[int] = []

    def _fake(client_id, api_key, endpoint, body, timeout=60, language="ZH_HANS"):
        calls.append(1)
        return {}

    with patch.object(ozon_client, "ozon_post", side_effect=_fake):
        out = ozon_post_expect_items("cid", "key", "/v3/product/info/list", {}, attempts=2)

    assert out == []
    assert len(calls) == 2


def test_expect_items_shape_result_items():
    """旧形状 result.items（shelf 实测）提取。"""
    with patch.object(ozon_client, "ozon_post",
                      return_value={"result": {"items": [{"id": 5}]}}):
        assert ozon_post_expect_items("c", "k", "/v3/product/info/list", {}) == [{"id": 5}]


def test_expect_items_shape_result_is_list():
    """v4 attributes 形状：result 本身是列表（card_audit sweep 实测）。"""
    with patch.object(ozon_client, "ozon_post",
                      return_value={"result": [{"id": 9, "attributes": []}]}):
        assert ozon_post_expect_items("c", "k", "/v4/product/info/attributes", {}) == \
            [{"id": 9, "attributes": []}]


def test_expect_items_first_call_success_no_retry():
    """首发即中 → 恰好 1 次、零退避。"""
    calls: list[int] = []

    def _fake(client_id, api_key, endpoint, body, timeout=60, language="ZH_HANS"):
        calls.append(1)
        return {"items": [{"id": 1}]}

    with patch.object(ozon_client, "ozon_post", side_effect=_fake):
        out = ozon_post_expect_items("c", "k", "/v3/product/info/list", {})

    assert out == [{"id": 1}]
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# A4: 三处调用方收敛（防本地退避循环复发）
# ---------------------------------------------------------------------------


def test_callers_use_unified_expect_items():
    """shelf / store_sync / card_audit 三处均走唯一实现，本地手写退避已拆除。"""
    shelf = Path("src/services/shelf_service.py").read_text(encoding="utf-8")
    sync = Path("src/services/store_sync_service.py").read_text(encoding="utf-8")
    audit = Path("src/services/card_audit_service.py").read_text(encoding="utf-8")
    assert "ozon_post_expect_items" in shelf
    assert "ozon_post_expect_items" in sync
    assert "ozon_post_expect_items" in audit
    # 手写退避循环不复发
    for name, src in (("shelf", shelf), ("store_sync", sync), ("card_audit", audit)):
        assert "for _attempt in range(" not in src, name
        assert "for attempt in range(" not in src, name


# ---------------------------------------------------------------------------
# B5: authenticate_admin 行为等价
# ---------------------------------------------------------------------------


class _FakeRequest:
    def __init__(self, auth: str = ""):
        self.headers = {"Authorization": auth} if auth else {}


def test_authenticate_admin_no_token_401():
    """无 Bearer → 401「Token is required」（require_admin 之前，不泄漏端点存在性）。"""
    with pytest.raises(HTTPException) as ei:
        asyncio.run(authenticate_admin(_FakeRequest()))
    assert ei.value.status_code == 401
    assert ei.value.detail == "Token is required"


def test_authenticate_admin_invalid_token_401():
    """Bearer 头但 token 为空串 → 同样 401（与九份拷贝旧行为一致）。"""
    with pytest.raises(HTTPException) as ei:
        asyncio.run(authenticate_admin(_FakeRequest("Bearer ")))
    assert ei.value.status_code == 401


def test_authenticate_admin_non_admin_403():
    """token 有效但非管理员 → require_admin 403。"""
    with patch("api.security._authenticate_token", return_value="u-common"), \
         patch("services.admin_service.require_admin",
               side_effect=HTTPException(status_code=403, detail="需要管理员权限")):
        with pytest.raises(HTTPException) as ei:
            asyncio.run(authenticate_admin(_FakeRequest("Bearer sk-tok")))
    assert ei.value.status_code == 403


def test_authenticate_admin_pass_returns_user_id():
    """admin 通过 → 返回 user_id（供 handler 记 operator）。"""
    with patch("api.security._authenticate_token", return_value="admin-1") as fa, \
         patch("services.admin_service.require_admin") as rq:
        out = asyncio.run(authenticate_admin(_FakeRequest("Bearer tok")))
    assert out == "admin-1"
    fa.assert_called_once_with("tok")  # Bearer 剥前缀后传入（与旧拷贝同语义）
    rq.assert_called_once_with("admin-1")


def test_route_modules_alias_unified_guard():
    """八份 routes 文件的 ``_authenticate_admin`` 与唯一实现是同一函数对象。"""
    import importlib

    from api import security as api_security
    for mod in ("admin_routes", "admin_audit_routes", "admin_categories_routes",
                "admin_data_sources_routes", "admin_config_routes",
                "admin_logistics_routes", "admin_queries_routes", "admin_site_routes"):
        m = importlib.import_module(f"routes.{mod}")
        assert getattr(m, "_authenticate_admin") is api_security.authenticate_admin, mod


def test_no_local_authenticate_admin_defs_ratchet():
    """棘轮：routes/ 下不得再出现本地 ``async def _authenticate_admin`` 定义（含内联拷贝）。"""
    routes_dir = Path(__file__).resolve().parent.parent / "src" / "routes"
    offenders = [f.name for f in sorted(routes_dir.glob("*.py"))
                 if "async def _authenticate_admin" in f.read_text(encoding="utf-8")]
    assert offenders == [], f"本地 admin 守卫拷贝复发: {offenders}"


def test_lazy_import_sites_converged():
    """credentials_routes 跨文件引用 / store_sync_routes 第 9 份内联均改走唯一实现。"""
    routes_dir = Path(__file__).resolve().parent.parent / "src" / "routes"
    cred = (routes_dir / "credentials_routes.py").read_text(encoding="utf-8")
    assert "from api.security import authenticate_admin as _authenticate_admin" in cred
    assert "from routes.admin_routes import _authenticate_admin" not in cred
    sync = (routes_dir / "store_sync_routes.py").read_text(encoding="utf-8")
    # sync-health handler 不再内联 require_admin 拷贝（其余端点的 _authenticate 不在 B5 范围）
    assert "from api.security import authenticate_admin" in sync
    assert "require_admin(user_id)" not in sync
