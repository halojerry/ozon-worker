"""上生产前接线契约测试:webui 每个按钮/调用 → worker 路由必须存在。

扫描 webui/src 里所有 api.get/post/patch/put/delete、downloadCsv、EventSource、
直接 fetch('/api/v1/...') 的路径字面量,归一化(去 query、${..}/路由参数 → {})
后与 main.app 实际注册路由比对。任何一个路径不存在 → 测试失败,阻止发版,
避免「前端调了不存在的接口」上生产才发现。

说明:本测试只验证「路径接线」,不验证响应 schema;响应字段由各端点单测覆盖。
"""
import os
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

os.environ["CREDENTIAL_MASTER_KEY"] = "0123456789abcdef0123456789abcdef"
os.environ["SKIP_ZOMBIE_RECOVERY"] = "1"
os.environ["SKIP_FAILED_REVIVE"] = "1"
os.environ["SKIP_STORE_SYNC"] = "1"

import main as main_mod  # noqa: E402

WEBUI_SRC = Path(__file__).resolve().parent.parent.parent / "webui" / "src"

# 非 api.<method> 但确实存在的端点(api.verify / api.login 走 request 封装)
KNOWN_EXTRA = {("/api/v1/auth/verify", "POST"), ("/api/v1/mxou/login", "POST")}

# v0.62.1 P1-1: newapi 代理动态路由（OpenAPI 快照只有 /api/{path}，看不到具体路径）。
# webui 同源 /api/* 命中 _NEWAPI_PREFIXES → 视为已接线（代理透传 api.mxou.cn），
# 不再误报缺失；未命中前缀的 /api/* 仍按缺失处理（真 404）。
from routes.newapi_proxy_routes import _NEWAPI_PREFIXES  # noqa: E402

# 契约实测 16 条真 404 路径的根前缀（加前缀后必须全部被代理覆盖，防未来新增认证页面再漏）
REQUIRED_NEWAPI_PREFIXES = {
    "verification", "verify", "oauth/", "home_page_content",
    "privacy-policy", "user-agreement", "reset_password",
}


def _normalize_path(path: str) -> str:
    """去 query;去 /api/v1 前缀;${var} 与 {var} 段统一为 {}。"""
    p = path.split("?")[0].strip().rstrip("/") or "/"
    if p.startswith("/api/v1"):
        p = p[len("/api/v1"):] or "/"
    # 查询参数用模板变量拼接的(如 /seo/keywords${q}):前一字符非 "/" 时视为 query 后缀
    p = re.sub(r"(?<=[^/])\$\{[^}]+\}$", "", p)
    p = re.sub(r"\$\{[^}]+\}", "{}", p)
    p = re.sub(r"\{[^}]+\}", "{}", p)
    return p


def _collect_route_set(app) -> set:
    """用 OpenAPI 快照收集全部注册路由(include_router 在本版本是懒挂载,app.routes 不全)。"""
    routes: set = set()
    for path, ops in (app.openapi() or {}).get("paths", {}).items():
        for method in ops:
            routes.add((method.upper(), _normalize_path(path)))
    for raw, m in KNOWN_EXTRA:
        routes.add((m, _normalize_path(raw)))
    return routes


def _is_newapi_proxied(raw_path: str) -> bool:
    """/api/* 非 v1 路径命中 newapi 代理前缀 → 视为已接线（动态路由 OpenAPI 不可见）。"""
    p = raw_path.split("?")[0]
    if not p.startswith("/api/") or p.startswith("/api/v1"):
        return False
    rel = p[len("/api/"):]
    for prefix in _NEWAPI_PREFIXES:
        if rel == prefix:
            return True
        if prefix.endswith("/") and rel.startswith(prefix):
            return True
        if not prefix.endswith("/") and rel.startswith(prefix + "/"):
            return True
    return False


def _scan_webui_calls() -> list:
    """返回 [(method, raw_path, file, line)]。"""
    calls: list = []
    patterns = [
        (r"api\.(get|post|put|patch|delete)(?:<[^>]*>)?\(\s*[`\"']([^`\"']+)", True),
        (r"downloadCsv\(\s*[`\"']([^`\"']+)", False),
        (r"new EventSource\(\s*[`\"']([^`\"']+)", False),
        (r"fetch\(\s*[`\"'](/api/v1/[^`\"']+)[`\"']\s*,\s*\{?([^)]*)", False),
    ]
    for f in WEBUI_SRC.rglob("*"):
        if f.suffix not in (".ts", ".tsx"):
            continue
        rel = f.relative_to(WEBUI_SRC)
        if "imports" in rel.parts or f.name in ("generated.d.ts", "client.ts"):
            continue
        text = f.read_text(encoding="utf-8")
        for pat, has_method in patterns:
            for m in re.finditer(pat, text):
                if has_method:
                    method, path = m.group(1), m.group(2)
                else:
                    # fetch/EventSource/downloadCsv 默认 GET;fetch 可带 method:"POST"
                    init = m.group(2) if len(m.groups()) > 1 else ""
                    method = "POST" if re.search(r"method\s*:\s*[\"']POST[\"']", init or "") else "GET"
                    path = m.group(1)
                line = text.count("\n", 0, m.start()) + 1
                calls.append((method.upper(), path, str(rel), line))
    return calls


def test_all_webui_api_paths_exist_on_worker():
    """webui 调用的每个路径都必须在 worker 注册(排除文档/客户端定义)。"""
    routes = _collect_route_set(main_mod.app)
    calls = _scan_webui_calls()
    assert calls, "webui 扫描结果为空,请检查扫描逻辑"
    missing = []
    for method, raw, rel, line in calls:
        norm = _normalize_path(raw)
        if (method, norm) not in routes and not _is_newapi_proxied(raw):
            missing.append(f"{method} {raw}  →  {norm}  ({rel}:{line})")
    assert not missing, (
        "webui 调用了 worker 未注册的路径(发版前必须接好):\n" + "\n".join(missing)
    )


def test_newapi_prefixes_cover_contract_missing_paths():
    """契约实测 16 条真 404 路径的根前缀必须全部在 _NEWAPI_PREFIXES（防回归）。"""
    missing_prefixes = REQUIRED_NEWAPI_PREFIXES - set(_NEWAPI_PREFIXES)
    assert not missing_prefixes, (
        f"newapi 代理缺前缀(契约实测 404 路径): {sorted(missing_prefixes)}"
    )


def test_newapi_proxy_detection():
    """动态代理识别：命中前缀 → 不算缺失；未命中 → 仍报缺失。"""
    assert _is_newapi_proxied("/api/verification") is True
    assert _is_newapi_proxied("/api/oauth/state") is True
    assert _is_newapi_proxied("/api/home_page_content") is True
    assert _is_newapi_proxied("/api/user/2fa/status") is True
    assert _is_newapi_proxied("/api/user/self") is True
    assert _is_newapi_proxied("/api/v1/drafts") is False
    assert _is_newapi_proxied("/api/unknown-not-proxied") is False


def test_webui_scan_covers_core_pages():
    """防扫描退化:核心页面的关键调用必须被扫到。"""
    calls = _scan_webui_calls()
    joined = "\n".join(f"{m} {_normalize_path(p)}" for m, p, _, _ in calls)
    for probe in (
        "GET /dashboard/overview",
        "GET /settings",
        "PUT /settings",
        "POST /drafts/batch-submit",
        "POST /drafts/import",
        "GET /products/ozon",
        "GET /analytics/bestsellers",
        "POST /stores/sync-all",
        "GET /progress/{}/stream",
    ):
        assert probe in joined, f"扫描未覆盖核心调用: {probe}"
