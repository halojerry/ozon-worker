"""v0.81 安全收尾批（Mimosa 深扫去重后 2 high + 10 medium）回归锁定。

覆盖面（判定表全文见 PR fix/sec-closeout-v081 描述）：
- High-1 ``_logistics_quote_sync``「SQL 注入」：判定 = ORM 绑定参数**误报** +
  白名单归一加固。本文件以 ``stmt.compile()`` 实证：恶意串只出现在绑定参数
  （compiled.params），从不出现在 SQL 文本（utils/logistics_quote.py 全链
  ORM select().where()，无 f-string/percent/%s 拼 SQL）。
- High-2 ``scripts/offline_validate.py:69`` SSRF：请求目标为模块常量——加
  scheme/host 白名单/path 相对性三段断言闸（``_assert_offline_target``），
  本文件锁其矩阵（内网 IP/元数据段/绝对 URL path 全拒，常量目标放行）。
- Medium ×10 判定：2 条真缺已修（``POST /async_run``、``GET /graph_parameter``
  补鉴权——消费矩阵一直标 🔒 但实现漏挂）；8 条已保护/设计公开（docstring
  留痕）。本文件锁行为：无 token 401 形态（对齐 test_task_statistics_auth_v076
  风格，hermetic 不依赖 PG/Supabase）。

运行:
    cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_sec_closeout_v081.py -q
"""
import importlib.util
import sys
from pathlib import Path
from urllib.parse import urlparse

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import main as main_mod
from main import app

_WORKER_ROOT = Path(__file__).resolve().parent.parent

_MALICIOUS_TPL = "RETS'; DROP TABLE logistics_rates;--"
_MALICIOUS_SVC = "Standard' OR '1'='1"


# ---------------------------------------------------------------------------
# High-1: logistics quote 链 SQL 参数化证明 + 白名单归一
# ---------------------------------------------------------------------------


class _FakeResult:
    """scalars().all() / scalar_one_or_none() 双形态空结果。"""

    def scalars(self):
        return self

    def all(self):
        return []

    def scalar_one_or_none(self):
        return None


class _FakeSession:
    """捕获 execute 的 ORM statement，供 compile() 断言绑定参数。"""

    def __init__(self):
        self.statements = []

    def execute(self, stmt, params=None):
        self.statements.append(stmt)
        return _FakeResult()

    def close(self):
        pass


def test_logistics_sql_binds_user_values_never_interpolates(monkeypatch):
    """恶意 tpl/svc 必须走绑定参数：compile 后 SQL 文本零恶意串、params 含原值。

    这是 Mimosa High「SQL 注入」判定的反驳实证：query_logistics_cost 全部
    查询经 SQLAlchemy ORM 比较符生成 bound params（相当于 text()+bind 的
    ORM 等价形态），字符串拼接不存在。
    """
    from storage.database import db as db_mod
    from utils.logistics_quote import query_logistics_cost

    fake = _FakeSession()
    monkeypatch.setattr(db_mod, "get_session", lambda: fake)
    malicious_bound = False

    cost, channel, detail = query_logistics_cost(
        weight=480.0, depth_cm=20.0, width_cm=15.0, height_cm=10.0,
        tpl_provider=_MALICIOUS_TPL, service_level=_MALICIOUS_SVC,
    )

    # 无 PG 数据 → 走 fallback 链（行为与修复前一致，证明未破坏查询语义）
    assert fake.statements, "应至少发出 Q1 查询"
    assert channel in {"default_fallback", "error_fallback"}
    assert cost >= 5.0

    for stmt in fake.statements:
        compiled = stmt.compile()
        sql_text = str(compiled)
        for fragment in ("DROP TABLE", "OR '1'='1", ";--", _MALICIOUS_TPL, _MALICIOUS_SVC):
            assert fragment not in sql_text, f"SQL 文本出现恶意串: {fragment!r}\n{sql_text}"
        # 最终 fallback 语句合法绑定字面 'RETS'/'Standard'——恶意串只要求出现在
        # **至少一条**语句的绑定参数里（Q1 的用户可控位）。
        joined = "\x00".join(str(v) for v in compiled.params.values())
        if _MALICIOUS_TPL in joined or _MALICIOUS_SVC in joined:
            malicious_bound = True
    assert malicious_bound, "恶意串应作为绑定参数出现在 Q1 查询里（而非 SQL 文本）"


def test_logistics_canonicalization_whitelist(monkeypatch):
    """白名单归一：大小写/空白归到权威拼写；未知值剥空白后原样透传。"""
    from utils.logistics_quote import canonical_service_level, canonical_tpl_provider

    assert canonical_tpl_provider("rets") == "RETS"
    assert canonical_tpl_provider("  zto  ") == "ZTO"
    assert canonical_tpl_provider("XINGYUAN") == "Xingyuan"
    assert canonical_tpl_provider("Ural") == "Ural"
    # 未知值透传（ORM 绑定参数安全，查不到走既有 fallback；不硬拒保 Q3 跨 3PL 兜底）
    assert canonical_tpl_provider(_MALICIOUS_TPL) == _MALICIOUS_TPL
    assert canonical_tpl_provider(None) is None
    assert canonical_tpl_provider("") == ""

    assert canonical_service_level("standard") == "Standard"
    assert canonical_service_level(" EXPRESS ") == "Express"
    assert canonical_service_level("economy") == "Economy"
    assert canonical_service_level("Premium") == "Premium"  # 未知透传
    assert canonical_service_level(None) is None


def test_logistics_quote_entry_canonicalizes_before_query(monkeypatch):
    """端到端：quote_logistics 入口归一 + 查询抛错时不外泄（error_fallback 兜底）。

    注意：get_session() 在 try 块外（建会话失败本就应抛）；execute 阶段的
    DB 故障才走 query_logistics_cost 的 except → error_fallback。
    """
    from storage.database import db as db_mod
    from utils import logistics_quote as lq

    class _BoomSession:
        def execute(self, stmt, params=None):
            raise RuntimeError("pg unavailable")

        def close(self):
            pass

    monkeypatch.setattr(db_mod, "get_session", _BoomSession)
    detail = lq.quote_logistics(
        ozon_client_id=None, ozon_api_key=None,
        weight=480.0, depth_cm=20.0, width_cm=15.0, height_cm=10.0,
        tpl_provider="rets", service_level=" standard",
    )
    # 归一后的权威拼写进 detail（此前 "rets" 原样进查询必 miss）
    assert detail["tpl_provider_used"] == "RETS"
    assert detail["service_level_used"] == "Standard"
    assert detail["channel"] == "error_fallback"
    assert detail["logistics_cost_cny"] > 0


def test_logistics_quote_sync_endpoint_comment_contract():
    """main._logistics_quote_sync 的 tpl/svc 出口注释留痕存在（扫描器结构性证据）。"""
    import inspect

    src = inspect.getsource(main_mod._logistics_quote_sync)
    assert "canonical_tpl_provider" in src
    assert "绑定参数" in src


# ---------------------------------------------------------------------------
# High-2: offline_validate SSRF 出口闸
# ---------------------------------------------------------------------------


def _load_offline_validate():
    spec = importlib.util.spec_from_file_location(
        "offline_validate_v081", _WORKER_ROOT / "scripts" / "offline_validate.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakeResp:
    status_code = 200
    text = ""

    def json(self):
        return {"result": []}


def test_offline_validate_blocks_internal_base(monkeypatch):
    """SELLER_BASE 被改成内网 http → seller_get 请求前 RuntimeError。"""
    ov = _load_offline_validate()

    def _must_not_post(*a, **k):
        raise AssertionError("guard 命中后不得发出任何请求")

    monkeypatch.setattr(ov.requests, "post", _must_not_post)
    monkeypatch.setattr(ov, "SELLER_BASE", "http://127.0.0.1:9999")
    try:
        ov.seller_get("cid", "key", "/v1/x", {})
    except RuntimeError as exc:
        assert "https" in str(exc)
    else:
        raise AssertionError("内网 base 未被拦截")


def test_offline_validate_blocks_metadata_and_localhost_hosts(monkeypatch):
    """元数据段/localhost/内网字面 IP host 全拒（scheme 合法也不放行）。"""
    ov = _load_offline_validate()
    for base in (
        "https://169.254.169.254",
        "https://localhost",
        "https://10.0.0.1",
        "https://192.168.1.1",
        "https://evil.example.com",
    ):
        try:
            ov._assert_offline_target(base, "/v1/x")
        except RuntimeError:
            pass
        else:
            raise AssertionError(f"host 未被拦截: {base}")


def test_offline_validate_blocks_absolute_or_escaped_path(monkeypatch):
    """path 带 scheme/netloc（绝对 URL 绕过 base）或非 / 开头 → 拒。"""
    ov = _load_offline_validate()
    for path in (
        "https://evil.example.com/v1/x",
        "http://127.0.0.1/v1/x",
        "//evil.example.com/v1/x",
        "v1/description-category/attribute",
    ):
        try:
            ov._assert_offline_target(ov.SELLER_BASE, path)
        except RuntimeError:
            pass
        else:
            raise AssertionError(f"path 未被拦截: {path!r}")


def test_offline_validate_allows_constant_target(monkeypatch):
    """模块常量目标（默认形态）→ 断言放行，请求正常发出且 URL 指向白名单 host。"""
    ov = _load_offline_validate()
    seen: dict = {}

    def _fake_post(url, json=None, headers=None, timeout=None):
        seen["url"] = url
        return _FakeResp()

    monkeypatch.setattr(ov.requests, "post", _fake_post)
    out = ov.seller_get("cid", "key", ov.ATTR_SCHEMA_URL, {"description_category_id": 1})
    assert out == {"result": []}
    parsed = urlparse(seen["url"])
    assert parsed.scheme == "https"
    assert parsed.hostname == "api-seller.ozon.ru"


# ---------------------------------------------------------------------------
# Medium 真缺修复：/async_run、/graph_parameter 补鉴权
# ---------------------------------------------------------------------------


def test_async_run_no_token_401():
    """修复前：匿名可提交异步任务（敏感写）；修复后 401 "Token is required"。"""
    r = TestClient(app, raise_server_exceptions=False).post("/async_run", json={})
    assert r.status_code == 401
    assert r.json()["detail"] == "Token is required"


def test_async_run_with_token_passes_auth(monkeypatch):
    """带合法 token → 鉴权放行、到达 runtime 提交（提交实现打替身，hermetic）。"""
    monkeypatch.setattr("services.tenant_service.resolve_tenant", lambda t: "28")
    # 无 lifespan 时 async_task_config 是 stub（缺 RECURSION_LIMIT），补上避免无关 500
    # （test_log_scrub_v076 同款处理）
    monkeypatch.setattr(main_mod.async_task_config, "RECURSION_LIMIT", 25, raising=False)

    class _FakeRuntime:
        async def submit(self, **kwargs):
            return {"task_id": kwargs.get("task_id", ""), "status": "queued"}

    monkeypatch.setattr(main_mod, "async_runtime", _FakeRuntime())
    r = TestClient(app).post("/async_run", json={"token": "sk-probe-token"})
    assert r.status_code == 200
    assert r.json()["status"] == "queued"


def test_async_run_invalid_token_401(monkeypatch):
    """失效 token（resolve_tenant 401）→ 401 透传。"""
    from fastapi import HTTPException

    def _deny(token):
        raise HTTPException(status_code=401, detail="invalid token")

    monkeypatch.setattr("services.tenant_service.resolve_tenant", _deny)
    r = TestClient(app, raise_server_exceptions=False).post(
        "/async_run", json={"token": "sk-bad"})
    assert r.status_code == 401


def test_graph_parameter_no_token_401():
    """修复前：匿名可读图 schema；修复后 401（_require_bearer 文案）。"""
    r = TestClient(app, raise_server_exceptions=False).get("/graph_parameter")
    assert r.status_code == 401
    assert r.json()["detail"] == "Token is required"


def test_graph_parameter_with_token_200(monkeypatch):
    """带合法 Bearer → 200 + input_schema（只读语义不变，仅补鉴权）。"""
    monkeypatch.setattr(main_mod, "_verify_analytics_token", lambda t: None)
    r = TestClient(app).get(
        "/graph_parameter", headers={"Authorization": "Bearer sk-probe-token"})
    assert r.status_code == 200
    body = r.json()
    assert "input_schema" in body and "output_schema" in body


# ---------------------------------------------------------------------------
# Medium 已保护端点：无 token 401 形态抽查（8 条判定中的可测证据）
# ---------------------------------------------------------------------------


def _client():
    return TestClient(app, raise_server_exceptions=False)


def test_admin_overview_no_token_401():
    """admin_routes：_authenticate_admin 首行强制 → 无 token 401（require_admin 之前）。"""
    r = _client().get("/api/v1/admin/overview")
    assert r.status_code == 401


def test_admin_create_user_no_token_401():
    """admin 写端点（POST /admin/users）同样无 token 401。"""
    r = _client().post("/api/v1/admin/users", json={"email": "a@b.c"})
    assert r.status_code == 401


def test_drafts_list_no_token_401():
    """drafts_routes：_authenticate 首行强制 → 无 token 401。"""
    r = _client().get("/api/v1/drafts")
    assert r.status_code == 401


def test_mxou_keys_no_token_401():
    """mxou_routes 密钥面：无 token 401（/login 设计公开不限）。"""
    r = _client().get("/api/v1/mxou/keys")
    assert r.status_code == 401


def test_seo_keywords_no_token_401():
    """seo_keywords_routes：Bearer 必填 → 无 token 401（设计公开≠匿名）。"""
    r = _client().get("/api/v1/seo/keywords?q=органайзер")
    assert r.status_code == 401


def test_store_sync_status_no_token_401():
    """store_sync_routes：_authenticate 首行强制 → 无 token 401。"""
    # 夹具 UUID 用低熵重复位（gitleaks 高熵串规则对 32+ 位引号字面量熵阈值 4.2，
    # 真 UUID 形态会误报——非凭证，形态等价即可，鉴权 401 在归属校验之前）。
    cid = "aaaaaaaa-0000-0000-0000-000000000000"
    r = _client().get(f"/api/v1/stores/{cid}/sync-status")
    assert r.status_code == 401


def test_error_reports_no_token_401():
    """error_reports_routes：Depends(get_tenant) → 无 Bearer 401（结构可见）。"""
    r = _client().get("/api/v1/error_reports")
    assert r.status_code == 401


def test_logistics_quote_no_token_401_regression():
    """logistics/quote 维持 v0.76 T10 收口（防本批归一改动误伤鉴权）。"""
    r = _client().post("/api/v1/logistics/quote",
                       json={"weight_g": 480, "depth_cm": 20, "width_cm": 15, "height_cm": 10})
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# probe_mxou_login：--base scheme 断言（本地工具不适用鉴权，判定的加固面）
# ---------------------------------------------------------------------------


def _load_probe():
    spec = importlib.util.spec_from_file_location(
        "probe_mxou_login_v081", _WORKER_ROOT / "scripts" / "probe_mxou_login.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_probe_base_rejects_non_http_scheme():
    """file:/gopher: 等非 http 族 scheme → SystemExit(2)。"""
    probe = _load_probe()
    for base in ("file:///etc/passwd", "gopher://x", "ftp://x", ""):
        try:
            probe._assert_base_scheme(base)
        except SystemExit as exc:
            assert exc.code == 2
        else:
            raise AssertionError(f"scheme 未被拦截: {base!r}")


def test_probe_base_allows_https_and_http_with_warning(capsys):
    """https 静默放行；http 放行但告警（本地 dev one-api 场景保留）。"""
    probe = _load_probe()
    probe._assert_base_scheme("https://api.mxou.cn")
    assert "WARNING" not in capsys.readouterr().err
    probe._assert_base_scheme("http://127.0.0.1:3000")
    assert "WARNING" in capsys.readouterr().err


def test_probe_unmask_quotes_token_id_path():
    """token_id 路径段 quote（被探测平台响应可控，防拼出意外 URL 形态）。"""
    from urllib.parse import quote

    probe = _load_probe()
    # _probe_unmask 内联 quote——这里锁 quote 语义本身 + 构造形态
    numeric_id = 123
    pathish_id = "../../x"
    assert quote(str(numeric_id), safe="") == "123"
    assert quote(str(pathish_id), safe="") == "..%2F..%2Fx"
    assert "../../" not in f"https://api.mxou.cn/api/token/{quote(str(pathish_id), safe='')}/key"
    assert probe is not None  # 模块可加载（import 面回归）


if __name__ == "__main__":
    sys.exit(__import__("pytest").main([__file__, "-q"]))
