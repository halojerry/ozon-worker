"""管线就绪预检（readiness）——命令启动前统一环境检测 + 结果缓存 + 静默预热。

背景（漏斗 v2 收尾）：此前各命令独立检测登录态/cookie 且互不共享——
1688 反爬 cookie 预热只在 check 里做，业务命令冷启动直接静默降级 CDP 图搜；
seller 登录等待可能在一条命令里被触发多次。本模块提供：

- ``probe_*`` 原子探针：只检测不修复（check 与业务命令共用同一实现）；
- ``ensure_pipeline_ready(pipeline)``：按管线裁剪所需探针 + 磁盘缓存
  （成功才缓存，TTL 600s）+ 静默修复（aibuy 冷启动预热一次 / seller
  未登录时交互等待或无人值守 fail-fast）；
- ``print_readiness_report``：一行就绪摘要 + 修复/指引明细。

纪律：除 Chrome CDP 与 discover-task 的 seller 登录外，一切失败都是
warning 不阻断（后续流程自有降级路径）——预检只提前给出可行动的指引，
不改变既有失败语义。
"""
from __future__ import annotations

import logging
import os
import sys

logger = logging.getLogger(__name__)

CDP_URL = "http://127.0.0.1:9222"
READINESS_TTL_SECONDS = 600  # 成功结果缓存 10 分钟：10 分钟内重跑免重复检测


def _under_pytest() -> bool:
    return bool(os.environ.get("PYTEST_CURRENT_TEST"))

# 各管线所需探针（graph/follow 的 1688 登录、DataDome 只做早知道 warning，
# 不硬阻断——cookie 检测对个别登录变体可能误报，保持既有失败语义）。
PIPELINE_PROBES: dict[str, tuple[str, ...]] = {
    "graph": ("chrome_cdp", "alibaba_login"),
    "follow": ("chrome_cdp", "ozon_datadome", "alibaba_login", "aibuy_token"),
    "discover": ("chrome_cdp", "seller_login", "aibuy_token"),
    "discover-multi": ("chrome_cdp", "seller_login", "aibuy_token"),
    "discover-task": ("chrome_cdp", "seller_login", "aibuy_token"),
}

PROBE_LABELS = {
    "chrome_cdp": "Chrome CDP",
    "seller_login": "seller 卖家后台登录",
    "aibuy_token": "1688 反爬 cookie",
    "alibaba_login": "1688 登录",
    "ozon_datadome": "Ozon DataDome",
}

_HINTS = {
    "seller_login": "在工具 Chrome 打开 https://seller.ozon.ru 登录卖家后台"
                    "（选品运营指标依赖；未登录指标降级）",
    "aibuy_token": "工具 Chrome 未预热 1688 反爬 cookie，aibuy 免浏览器图搜不可用"
                   "（自动降级 CDP 图搜）；打开 https://www.1688.com/ 一次即可预热（无需登录）",
    "alibaba_login": "1688 未登录（影响 1688 商品页抓取），请在工具 Chrome 登录 1688",
    "ozon_datadome": "Ozon 页面暂未通过 DataDome，在工具 Chrome 浏览 ozon.ru"
                     " 任意商品即可建立信任",
    "chrome_cdp": "Chrome CDP 不可用，请运行 `python3 scripts/cli.py check` 查看环境诊断",
}


# ── 原子探针（只检测，不修复；check 与 ensure_pipeline_ready 共用）──


def probe_chrome_cdp(profile_dir: str | None = None,
                     cdp_url: str = CDP_URL) -> bool:
    """Chrome CDP 可用（必要时自动拉起工具 Chrome；已运行则秒回）。"""
    from scripts.lib.chrome_launcher import ensure_chrome_cdp
    ok, _ = ensure_chrome_cdp(auto_restart=True, profile_dir=profile_dir)
    return bool(ok)


def probe_alibaba_login(cdp_url: str = CDP_URL) -> bool:
    """1688 登录态（cookie2/__cn_logon__ 任一存在；不要求已开 1688 标签页）。"""
    conn = None
    tab = None
    try:
        from scripts.lib.cdp_client import CdpConnection
        conn = CdpConnection(cdp_url)
        tab = conn.new_tab("about:blank")
        msg_id = tab._send("Network.getCookies",
                           {"urls": ["https://www.1688.com/"]})
        resp = tab._recv_until_id(msg_id, timeout=10) or {}
        names = {c.get("name", "")
                 for c in (resp.get("result", {}).get("cookies") or [])}
        return bool(names & {"cookie2", "__cn_logon__"})
    except Exception:
        return False
    finally:
        for closer in (lambda: tab.close(), lambda: conn.close()):
            try:
                closer()
            except Exception:
                pass


def probe_ozon_datadome(cdp_url: str = CDP_URL) -> bool:
    """Ozon 页面可信度（优先复用已开 product tab；否则临时 tab 检查后关闭）。"""
    conn = None
    tab = None
    tab_is_new = False
    try:
        import requests as _req
        from scripts.lib.cdp_client import CdpConnection, CdpTab
        try:
            tabs_resp = _req.get(f"{cdp_url}/json", timeout=5)
            if tabs_resp.status_code == 200:
                for t in tabs_resp.json():
                    if (t.get("type") == "page" and "ozon.ru" in t.get("url", "")
                            and "ozon.ru/product/" in t.get("url", "")):
                        tab = CdpTab(cdp_url, t.get("id", ""),
                                     t.get("webSocketDebuggerUrl", ""))
                        break
        except Exception:
            pass
        if tab is None:
            conn = CdpConnection(cdp_url)
            tab = conn.new_tab("https://www.ozon.ru/")
            tab.wait_for_load(timeout=10)
            tab_is_new = True
        return bool(tab.evaluate(
            "!!(document.body && document.body.innerText.length > 200 "
            "&& document.title.length > 5 "
            "&& !document.querySelector('#datadome-captcha, iframe[src*=\"datadome\"]'))"
        ))
    except Exception:
        return False
    finally:
        try:
            if tab is not None:
                tab.close(close_remote=tab_is_new)
        except Exception:
            pass
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass


def probe_seller_login(cdp_url: str = CDP_URL, *, mark: bool = True) -> bool:
    """seller.ozon.ru 卖家后台登录态；成功时记进程内 memo（免同进程重复等待）。"""
    from scripts.lib import ozon_seller_analytics as _osa
    conn = None
    try:
        from scripts.lib.cdp_client import CdpConnection
        conn = CdpConnection(cdp_url)
        ok = bool(_osa.check_seller_login(conn))
    except Exception:
        return False
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass
    if ok and mark:
        _osa.mark_seller_login_confirmed()
    return ok


def probe_aibuy_token(cdp_url: str = CDP_URL) -> bool:
    """1688 反爬 cookie 已预热（静默只读，不导航任何页面）。"""
    try:
        from scripts.lib.ozon_image_search import _read_1688_cookies_silent
        return bool(_read_1688_cookies_silent(cdp_url))
    except Exception:
        return False


_PROBES = {
    "chrome_cdp": None,  # 需要 profile_dir，单独处理
    "alibaba_login": probe_alibaba_login,
    "ozon_datadome": probe_ozon_datadome,
    "seller_login": probe_seller_login,
    "aibuy_token": probe_aibuy_token,
}


# ── 缓存（成功才写；任何失败/异常都不缓存，下次重跑现检）──


def _cached_ok(probe: str) -> bool:
    from scripts.lib.cache import cache_get
    try:
        return cache_get("readiness", probe) is not None
    except Exception:
        return False


def _mark_ok(probe: str) -> None:
    from scripts.lib.cache import cache_set
    try:
        cache_set("readiness", probe, {"ok": True}, ttl=READINESS_TTL_SECONDS)
    except Exception:
        pass


# ── 管线就绪入口 ──


def _is_interactive() -> bool:
    try:
        return bool(sys.stdin and sys.stdin.isatty())
    except Exception:
        return False


def ensure_pipeline_ready(pipeline: str, *, profile_dir: str | None = None,
                          interactive: bool | None = None,
                          prewarm: bool = True,
                          use_cache: bool = True) -> dict:
    """按管线检测环境就绪度；返回 report（``print_readiness_report`` 渲染）。

    report = {ok, results, cached, repaired, hints}
    - ok：False 仅当 Chrome CDP 不可用，或 discover-task 的 seller 未登录
      （无人值守 fail-fast，替代流程深处 90s 黑等）；
    - repaired：已自动修复项（aibuy 冷启动预热 / seller 登录等待通过）。
    """
    probes = PIPELINE_PROBES.get(pipeline)
    if probes is None:
        raise ValueError(f"未知管线: {pipeline}（支持: {', '.join(PIPELINE_PROBES)}）")
    if _under_pytest():
        # 单测密闭：pytest 下探针不触真实 CDP/网络（命令层测试未 mock readiness，
        # 且 CI Docker 无 Chrome——真检必失败）。readiness 行为由 test_readiness.py
        # 显式 mock 验证（该文件用 monkeypatch 摘除此守卫）。
        return {"ok": True, "results": {p: True for p in probes},
                "cached": [], "repaired": [], "hints": {}}
    if interactive is None:
        interactive = _is_interactive()

    results: dict[str, bool] = {}
    cached: list[str] = []
    repaired: list[str] = []
    hints: dict[str, str] = {}

    for probe in probes:
        if probe == "chrome_cdp":
            results[probe] = probe_chrome_cdp(profile_dir=profile_dir)
            if not results[probe]:
                hints[probe] = _HINTS[probe]
            continue

        if use_cache and _cached_ok(probe):
            results[probe] = True
            cached.append(probe)
            continue

        ok = bool(_PROBES[probe](CDP_URL))

        if not ok and probe == "aibuy_token" and prewarm:
            # 冷启动静默预热：导航一次 1688 首页触发反爬 cookie 下发（≤8s），
            # 之后 10 分钟内（缓存）不再重复。仍失败仅 warning——图搜自动降级 CDP。
            try:
                from scripts.lib.ozon_image_search import (
                    _fetch_aibuy_cookies_from_chrome,
                )
                cookies = _fetch_aibuy_cookies_from_chrome(CDP_URL)
                ok = bool(cookies)
                if ok:
                    repaired.append("已自动预热 1688 反爬 cookie"
                                    "（一次性导航，之后缓存期内免检测）")
            except Exception as exc:
                logger.warning("aibuy cookie 预热失败（不影响主流程）: %s", exc)

        if not ok and probe in ("seller_login", "alibaba_login"):
            # ✅ v0.69 自动兜底：扫描本机其他浏览器的登录 cookie 导入工具 Chrome
            # （冷却落盘：每探针 1h 最多一次，防 Keychain 授权框循环弹；用户拒绝
            # 授权/无 cookie 可搬 → 静默跳过，走下方原有人工流程，降级语义不变）。
            try:
                from scripts.lib import cookie_harvest
                if cookie_harvest.try_auto_import(probe, CDP_URL):
                    ok = True
                    repaired.append("已从本机其他浏览器导入登录 cookie（免手动登录）")
            except Exception as exc:
                logger.warning("cookie 自动导入失败（忽略，走原流程）: %s", exc)

        if not ok and probe == "seller_login":
            from scripts.lib import ozon_seller_analytics as _osa
            if pipeline == "discover-task":
                hints[probe] = ("seller.ozon.ru 未登录：无人值守模式下直接退出。"
                                + _HINTS[probe] + "，完成后重跑本命令")
            elif interactive:
                # 交互：开头给足登录窗口（复用现有 wait；标记"已等待"→ 流程深处
                # 蓝海/富化路径的等待经 memo 秒过，不再重复等）。
                try:
                    from scripts.lib.cdp_client import CdpConnection
                    _osa.mark_seller_login_wait_attempted()
                    with CdpConnection(CDP_URL) as cdp:
                        ok = bool(_osa.wait_for_seller_login(cdp))
                    if ok:
                        repaired.append("seller 卖家后台登录已确认")
                    else:
                        hints[probe] = _HINTS[probe]
                except Exception as exc:
                    logger.warning("seller 登录等待异常（不影响主流程）: %s", exc)
                    hints[probe] = _HINTS[probe]
            else:
                # 非交互交互 discover：不在此等待（流程内保留既有 90s 窗口），只预告。
                hints[probe] = _HINTS[probe]

        results[probe] = bool(ok)
        if ok:
            _mark_ok(probe)
        elif probe not in hints:
            hints[probe] = _HINTS[probe]

    hard_fail = (not results.get("chrome_cdp", False)) or (
        pipeline == "discover-task" and not results.get("seller_login", False))
    return {"ok": not hard_fail, "results": results, "cached": cached,
            "repaired": repaired, "hints": hints}


def print_readiness_report(report: dict, *, prefix: str = "🧭") -> None:
    """渲染就绪摘要：成功一行带 ✅/⚠️ 标记；失败项逐条给指引。"""
    results = report.get("results", {})
    parts = [f"{'✅' if results.get(p) else '⚠️'} {PROBE_LABELS.get(p, p)}"
             for p in results]
    cached = report.get("cached") or []
    cache_note = ""
    if cached:
        names = "、".join(PROBE_LABELS.get(p, p) for p in cached)
        cache_note = f"（{names} 缓存命中，{READINESS_TTL_SECONDS // 60} 分钟内免重复检测）"
    print(f"{prefix} 环境预检: {' / '.join(parts)}{cache_note}", flush=True)
    for line in report.get("repaired") or []:
        print(f"   🔧 {line}", flush=True)
    for probe, hint in (report.get("hints") or {}).items():
        if report.get("ok"):
            print(f"   ⚠️ {hint}", flush=True)
        else:
            print(f"   ❌ {hint}", flush=True)
