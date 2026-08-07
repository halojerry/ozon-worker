#!/usr/bin/env python3
"""pounding-ozon-probe CLI — 1688 search + CDP probe + GraphInput assembly.

Commands:
check                    诊断前置条件（Chrome / 凭证 / Worker / Ozon API）
search <关键词>          1688 搜索商品
probe <URL>              CDP 浏览器探针抓取商品
graph <URL|item_id>      组装 GraphInput envelope（不上架）
follow --ozon-url <URL>  跟卖 Ozon 商品
discover                 Ozon 中国站选品（蓝海+利润筛选）
image_search --image <URL>  以图搜款
get_ak                   自动获取 1688 AK
set_store                配置 Ozon 店铺
list_stores              列出所有店铺
set_token                设置 MXOU_TOKEN
set_ak                   设置 1688 AK
batch_test               批量处理 URL 列表
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

# Ensure scripts/ is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _out(obj: dict) -> None:
    """输出 JSON（⚠️ v0.26: 凭证脱敏 — api_key/token 打码，防终端/日志泄漏）。"""
    import copy as _copy
    safe = _copy.deepcopy(obj)
    _redact_keys(safe, {"api_key", "token", "ozon_api_key", "mxou_token", "ak", "ali_1688_ak"})
    print(json.dumps(safe, ensure_ascii=False, indent=2), flush=True)


def _redact_keys(obj, keys: set, _depth: int = 0) -> None:
    """递归把 dict 中命中的键值打码（保留前 4 位便于区分）。"""
    if _depth > 10 or obj is None:
        return
    if isinstance(obj, dict):
        for k, v in list(obj.items()):
            if isinstance(k, str) and k in keys and isinstance(v, str) and v:
                obj[k] = v[:4] + "****"
            else:
                _redact_keys(v, keys, _depth + 1)
    elif isinstance(obj, list):
        for item in obj:
            _redact_keys(item, keys, _depth + 1)


# ═══════════════════════════════════════════════════════════════════════════
# Config Commands
# ═══════════════════════════════════════════════════════════════════════════


def cmd_set_store(args: argparse.Namespace) -> int:
    """配置 Ozon 店铺凭证."""
    from scripts.lib.config_store import set_store
    store = set_store(
        store_id=args.name,
        client_id=args.client_id,
        api_key=args.api_key,
        currency=args.currency or "",
    )
    _out({"success": True, "store": args.name, "config": store})
    return 0


def cmd_list_stores(args: argparse.Namespace) -> int:
    """列出所有已配置的 Ozon 店铺."""
    from scripts.lib.config_store import list_stores
    stores = list_stores()
    if not stores:
        _out({"stores": {}, "message": "未配置任何店铺。使用 set_store 命令配置。"})
        return 0

    result = {}
    for name, config in stores.items():
        result[name] = {
            "client_id": config.get("client_id", "")[:8] + "***" if config.get("client_id") else "",
            "has_api_key": bool(config.get("api_key")),
            "currency": config.get("currency", "RUB"),
        }
    _out({"stores": result, "total": len(stores)})
    return 0


def cmd_set_token(args: argparse.Namespace) -> int:
    """设置 MXOU_TOKEN."""
    from scripts.lib.config_store import set_mxou_token
    set_mxou_token(args.token)
    _out({"success": True,
          "message": "MXOU_TOKEN 已保存到 settings.json",
          "hint": "如还没有 Token，请访问 https://api.mxou.cn 注册并获取"})
    return 0


def cmd_set_ak(args: argparse.Namespace) -> int:
    """设置 1688 AK."""
    from scripts.lib.config_store import set_ali_1688_ak
    set_ali_1688_ak(args.ak)
    _out({"success": True, "message": "1688 AK 已保存到 settings.json"})
    return 0


# ═══════════════════════════════════════════════════════════════════════════
# Business Commands
# ═══════════════════════════════════════════════════════════════════════════


def cmd_search(args: argparse.Namespace) -> int:
    """搜索 1688 商品."""
    from scripts.lib.ak_1688_client import search_products

    products = search_products(args.query, page_size=args.page_size)
    _out({"count": len(products), "products": products})
    return 0


def cmd_probe(args: argparse.Namespace) -> int:
    """CDP 浏览器抓取 1688 商品."""
    from scripts.lib.ak_1688_client import enrich_product_with_cdp

    result = enrich_product_with_cdp(detail_url=args.url, timeout_seconds=args.timeout)
    data = result.get("data", {})
    _out({
        "source": result.get("source", "?"),
        "degraded": result.get("degraded", True),
        "title": data.get("title"),
        "price": data.get("price"),
        "brand": data.get("brand"),
        "seller": data.get("seller"),
        "weight_grams": data.get("weight_grams"),
        "images": len(data.get("images") or []),
        "attributes": len(data.get("attributes") or []),
        "sku_count": len(data.get("sku_details") or []),
        "option_groups": len(data.get("option_groups") or []),
        "packaging_rows": len(data.get("packaging_rows") or []),
    })
    return 0


def cmd_graph(args: argparse.Namespace) -> int:
    """组装 GraphInput envelope（1688 API + CDP → 完整请求）."""
    from scripts.lib.config_store import AuthError, preflight_check, print_setup_guide
    try:
        from scripts.cloud_probe import (
            ProductValidationError,
            build_graph_envelope_with_retry,
        )
    except ModuleNotFoundError:
        print("❌ 未找到 scripts.cloud_probe（版本过旧，缺云上架模块）。"
              "请升级：运行 `python3.12 bootstrap_update.py` 或重新下载最新包", flush=True)
        return 1

    missing = preflight_check()
    if missing:
        print_setup_guide(missing)
        return 1

    # Extract item_id from URL if needed
    item_id = args.item_id
    if not item_id and args.url:
        import re
        m = re.search(r"/(\d+)\.html", args.url)
        if m:
            item_id = m.group(1)
    if not item_id:
        _out({"error": "需要 --item-id 或 --url (含 offer ID)"})
        return 1

    detail_url = args.url or f"https://detail.1688.com/offer/{item_id}.html"

    try:
        graph = build_graph_envelope_with_retry(
            item_id=item_id,
            detail_url=detail_url,
            category_query=args.category_query,
            max_retries=args.retries,
            store_id=args.store or "",
        )
    except ProductValidationError as e:
        _out({"error": str(e), "skipped": True, "item_id": item_id})
        return 2
    except AuthError as e:
        _out({"error": str(e)})
        return 1

    # Summary + full envelope
    env = graph.get("envelope", {})
    if isinstance(env, dict) and "draft" in env:
        draft = env.get("draft", {})
    else:
        draft = env
    summary = {
        "item_id": draft.get("item_id"),
        "title": draft.get("title", "")[:80],
        "purchase_cost": draft.get("purchase_cost"),
        "weight": draft.get("weight"),
        "dimensions": draft.get("dimensions"),
        "images": len(draft.get("images", [])),
        "attributes": len(draft.get("attributes", {})),
        "variants": len(draft.get("variants", [])),
        "category": draft.get("category"),
        "supplier": draft.get("supplier", "")[:30],
        "shipping": draft.get("shipping"),
    }
    # ✅ v0.10: 默认自动提交到 Worker（对齐 SKILL.md），--no-submit 跳过
    submit_result = None
    if not getattr(args, 'no_submit', False):
        try:
            from scripts.cloud_probe import submit_envelope
        except ModuleNotFoundError:
            print("❌ 未找到 scripts.cloud_probe（版本过旧，缺云上架模块）。"
                  "请升级：运行 `python3.12 bootstrap_update.py` 或重新下载最新包", flush=True)
            return 1
        submit_result = submit_envelope(graph)
        if submit_result.get("ok"):
            import logging
            _logger = logging.getLogger(__name__)
            _logger.info("✅ 已提交 Worker: task_id=%s", submit_result.get("task_id"))
            summary["task_id"] = submit_result.get("task_id")
        else:
            import logging
            _logger = logging.getLogger(__name__)
            _logger.error("❌ 提交失败: %s", submit_result.get("error"))
    _out({"summary": summary, "envelope": graph, "submit_result": submit_result})
    return 0


def cmd_image_search(args) -> int:
    """以图搜款: 上传图片搜索 1688 同款/相似商品。

    --source ak  → 1688 AK API 图搜（默认，无需浏览器）
    --source cdp → 1688 网页版 CDP 图搜（更准确，需 Chrome 登录 1688）
    """
    from scripts.lib.config_store import AuthError, preflight_check, print_setup_guide

    missing = preflight_check(skip_store=True)
    if missing:
        print_setup_guide(missing)
        return 1

    try:
        if args.source == "cdp":
            from scripts.lib.ozon_image_search import search_by_image_cdp
            results = search_by_image_cdp(
                image_url=args.image,
                page_size=max(args.limit, 20),
                wait_seconds=10,
                try_crop_regions=True,
            )
        else:
            from scripts.lib.ak_1688_client import search_by_image
            results = search_by_image(
                image_path=args.image if not args.image.startswith("http") else "",
                image_url=args.image if args.image.startswith("http") else "",
                page_size=args.limit,
                sort_type=args.sort or "",
            )
    except AuthError as e:
        _out({"success": False, "error": str(e)})
        return 1
    except FileNotFoundError as e:
        _out({"success": False, "error": str(e)})
        return 1
    except Exception as e:
        _out({"success": False, "error": str(e)})
        return 1

    products = []
    for p in results:
        products.append({
            "id": p.get("product_id", ""),
            "title": p.get("title", "")[:100],
            "price": p.get("price", ""),
            "image": p.get("image_url", ""),
            "detail_url": p.get("detail_url", ""),
            "supplier": p.get("supplier", ""),
            "sold_count": p.get("sold_count", 0),
        })

    _out({
        "success": True,
        "source_image": args.image,
        "total_results": len(products),
        "products": products,
    })
    return 0


def cmd_get_ak(args) -> int:
    """通过浏览器获取 1688 AK，自动保存到 settings.json."""
    from scripts.lib.ak_callback import get_ak_via_browser

    result = get_ak_via_browser(timeout=args.timeout)

    # ✅ get_ak_via_browser() 内部已通过 _save_ak() 保存真实 AK
    # 这里只标记成功，不再二次保存（result["ak"] 是 masked 值，会破坏真实 AK）
    if result.get("success"):
        result["saved_to"] = "settings.json"

    _out(result)
    return 0 if result.get("success") else 1


def _chrome_profile_dir() -> str:
    """独立 Chrome profile 目录。

    ⚠️ Chrome 130+ 禁止在系统默认数据目录启用远程调试
    （"DevTools remote debugging requires a non-default data directory"），
    必须用 --user-data-dir 指向独立 profile。与 browser_probe/service.py
    的 _profile_dir('default') 保持一致，登录态在独立 profile 中维护，
    不污染用户日常 Chrome。
    """
    return str(Path(__file__).resolve().parent.parent
               / "data" / "browser" / "profiles" / "1688" / "default")


def cmd_check(args) -> int:
    """诊断前置条件：浏览器 / CDP / 1688 / Ozon / 凭证 / Worker"""
    import os as _os
    import shutil

    import requests as req

    from scripts.capabilities.browser_probe.service import _candidate_browser_paths
    from scripts.lib.config_store import (
        check_config,
        get_ali_1688_ak,
        get_mxou_token,
        list_stores,
    )

    config = check_config()
    all_ok = True

    def _ok(b: bool) -> str:
        return "✅" if b else "❌"

    def _open_tab(url: str) -> None:
        """在 CDP Chrome 中打开一个标签页（方便用户登录/建立信任），失败静默。"""
        try:
            from scripts.lib.cdp_client import CdpConnection
            conn = CdpConnection("http://127.0.0.1:9222")
            tab = conn.new_tab(url)
            tab.close(close_remote=False)  # 保留标签页给用户，只关 WS
            conn.close()
        except Exception:
            pass

    # ═══════════════════════════════════════════
    # 1. 浏览器检测（Chromium 内核）
    # ═══════════════════════════════════════════
    print("🖥️ 浏览器检测（需要 Chromium 内核，Firefox/Safari 不支持）:")

    BROWSER_NAMES: dict[str, list[str]] = {
        "Google Chrome": ["chrome", "google-chrome", "google-chrome-stable"],
        "Chromium": ["chromium", "chromium-browser"],
        "Microsoft Edge": ["msedge", "microsoft-edge"],
        "Brave": ["brave", "brave-browser"],
        "Opera": ["opera"], "Vivaldi": ["vivaldi"],
        "360 浏览器": ["360chrome", "360se"],
        "QQ 浏览器": ["qqbrowser"],
        "搜狗浏览器": ["sogou", "sogou-explorer"],
        "猎豹浏览器": ["liebao"], "傲游浏览器": ["maxthon"],
        "豆包浏览器": ["doubao", "doubao-browser"],
    }

    found_browsers: list[tuple[str, str]] = []
    candidates = _candidate_browser_paths()

    for path in candidates:
        if not path or not path.strip():
            continue
        path = path.strip()
        basename = _os.path.basename(path).lower().replace('.exe', '').replace('.app', '')
        name = None
        for n, aliases in BROWSER_NAMES.items():
            if basename in aliases or any(a in basename for a in aliases):
                name = n
                break
        if not name:
            name = basename.title()

        if _os.path.exists(path) or shutil.which(path):
            actual = shutil.which(path) or path
            if _os.path.exists(actual):
                found_browsers.append((name, actual))

    seen = set()
    unique_browsers = []
    for name, path in found_browsers:
        key = _os.path.realpath(path)
        if key not in seen:
            seen.add(key)
            unique_browsers.append((name, path))

    if unique_browsers:
        for name, _ in unique_browsers:
            tag = "（推荐）" if name == "Google Chrome" else ""
            print(f"  ✅ {name} {tag}")
    else:
        print("  ❌ 未检测到可用浏览器")
        print("  → 请安装 Google Chrome: https://www.google.com/chrome/")
        all_ok = False
        return 0 if all_ok else 1

    # ═══════════════════════════════════════════
    # 2. CDP 远程调试检查（自动启动 Chrome）
    # ═══════════════════════════════════════════
    print("\n🔗 CDP 远程调试 (127.0.0.1:9222):")

    try:
        from scripts.lib.chrome_launcher import ensure_chrome_cdp, get_chrome_info
        info = get_chrome_info()
        if info["chrome_found"]:
            print(f"  🌐 Chrome: {Path(info['chrome_path']).name}")
        if not info["cdp_available"]:
            print("  ⏳ CDP 未运行，正在自动启动 Chrome...")
            ok, msg = ensure_chrome_cdp(auto_restart=True, profile_dir=_chrome_profile_dir())
            if ok:
                print(f"  ✅ {msg}")
                session_ok = True
            else:
                print(f"  ❌ {msg}")
                session_ok = False
        elif not info["has_remote_allow_origins"]:
            print("  ⚠️ CDP 运行中但缺少 --remote-allow-origins，正在重启 Chrome...")
            ok, msg = ensure_chrome_cdp(auto_restart=True, profile_dir=_chrome_profile_dir())
            if ok:
                print(f"  ✅ {msg}")
                session_ok = True
            else:
                print(f"  ❌ {msg}")
                session_ok = False
        else:
            session_ok = True
            print("  ✅ CDP 已启动")
    except ImportError:
        cdp = config.get("cdp", {})
        session_ok = cdp.get("session_available", False) or cdp.get("cdp_running", False)
        print(f"  {_ok(session_ok)} CDP 已启动")
        if not session_ok:
            print("  ⚠️ 自动启动模块不可用，请手动启动 Chrome:")
            print("  macOS: /Applications/Google\\ Chrome.app/Contents/MacOS/Google\\ Chrome \\")
            print("    --remote-debugging-port=9222 --remote-allow-origins='*'")

    cdp = config.get("cdp", {})
    login_ok = not cdp.get("login_required", True)

    # ═══════════════════════════════════════════
    # 3. 1688 CDP 连通检查 + 登录检测
    # ═══════════════════════════════════════════
    alibaba_cdp_ok = False
    login_ok = False  # ⚠️ v0.14 E4: 显式初始化（原代码 ws 分支异常时可能 NameError）
    if session_ok:
        print("\n  🔗 1688 CDP 连通检查...")
        try:
            from scripts.lib.cdp_client import CdpTab

            # ⚠️ v0.14 E4: 复用已有 1688 tab（只读检查），替代手写 websocket + Runtime.evaluate
            tab = None
            try:
                _tabs = req.get("http://127.0.0.1:9222/json", timeout=5).json()
                for _t in _tabs:
                    if _t.get("type") == "page" and "1688.com" in _t.get("url", ""):
                        tab = CdpTab("http://127.0.0.1:9222", _t.get("id", ""), _t.get("webSocketDebuggerUrl", ""))
                        break
            except Exception:
                pass

            if tab:
                alibaba_cdp_ok = bool(tab.evaluate(
                    "!!location.href && location.href.indexOf('1688.com')>=0 && location.href.indexOf('login.1688.com')<0"
                ))
                if alibaba_cdp_ok:
                    val = tab.evaluate(
                        "document.cookie.match(/cookie2=|__cn_logon__=/) ? 'LOGGED_IN' : 'NOT_LOGGED_IN'"
                    )
                    login_ok = val == "LOGGED_IN"
                tab.close(close_remote=False)  # 只关 WS，保留用户 1688 标签页
            else:
                print("  ⚠️ 未找到已打开的 1688 标签页")
        except Exception as _dbg_e:
            print(f"  ⚠️ 1688 CDP 异常: {_dbg_e}")
        print(f"  {_ok(alibaba_cdp_ok)} 1688 页面可访问 (仅影响 1688 URL)")
    else:
        print("\n  🔗 1688 CDP: ⏭️ 跳过（CDP 未启动）")

    if session_ok and alibaba_cdp_ok or session_ok:
        print(f"  {_ok(login_ok)} 1688 已登录 (影响 1688 抓取)")
        if not login_ok:
            print("  → 需登录 1688")
            all_ok = False

    # ── v0.28.4: 登录引导 — 打开 1688 + Ozon Seller 登录页, 交互等待 ──
    # 用户方案: 先打开让用户登录, 之后工具 Chrome 常驻复用, 静默工作。
    if session_ok and not login_ok:
        _open_tab("https://login.1688.com/")
        _open_tab("https://seller.ozon.ru/")  # discover 运营指标(what_to_sell)需要卖家登录
        print("  → 已在浏览器打开 1688 登录页 + Ozon Seller 卖家后台")
        if sys.stdin.isatty():
            try:
                input("  ⏸ 请在浏览器中完成 1688 与 Ozon Seller 登录, 完成后按 Enter 继续...")
            except EOFError:
                pass
            print("  → 继续验证...")
        else:
            print("  → 非交互环境: 请登录后重跑 `check` 确认")

    # ═══════════════════════════════════════════
    # 4. Ozon CDP 连通检查
    # ═══════════════════════════════════════════
    ozon_cdp_ok = False
    if session_ok:
        print("\n  🔗 Ozon CDP 连通检查 (DataDome)...")
        try:
            from scripts.lib.cdp_client import CdpConnection, CdpTab

            tab = None
            tab_is_new = False

            # ✅ v0.10: 优先复用已有 ozon.ru/product tab（保留 cookie/session，避免 DataDome）
            # ⚠️ v0.14 E4: 用封装替代手写 websocket + Runtime.evaluate
            try:
                tabs_resp = req.get("http://127.0.0.1:9222/json", timeout=5)
                if tabs_resp.status_code == 200:
                    for t in tabs_resp.json():
                        if t.get("type") == "page" and "ozon.ru" in t.get("url", "") and "ozon.ru/product/" in t.get("url", ""):
                            tab = CdpTab("http://127.0.0.1:9222", t.get("id", ""), t.get("webSocketDebuggerUrl", ""))
                            break
            except Exception:
                pass

            # 没有已有 tab → 创建新 tab（仅检查后关闭，不残留）
            if tab is None:
                try:
                    conn = CdpConnection("http://127.0.0.1:9222")
                    tab = conn.new_tab("https://www.ozon.ru/")
                    tab.wait_for_load(timeout=10)
                    tab_is_new = True
                except Exception:
                    pass

            if tab:
                # 检查实际页面内容（不只是 URL），含 DataDome 拦截检测
                ozon_cdp_ok = bool(tab.evaluate(
                    "!!(document.body && document.body.innerText.length > 200 && document.title.length > 5 "
                    "&& !document.querySelector('#datadome-captcha, iframe[src*=\"datadome\"]'))"
                ))
                # 新建 tab → 全关；复用的用户 tab → 只关 WS 不关远程
                tab.close(close_remote=tab_is_new)
        except Exception:
            pass
        print(f"  {_ok(ozon_cdp_ok)} Ozon 可通过 DataDome")
    else:
        print("\n  🔗 Ozon CDP: ⏭️ 跳过（CDP 未启动）")

    if session_ok and not ozon_cdp_ok:
        print("  → 在浏览器中打开 https://www.ozon.ru/ 浏览任意商品即可建立信任")
        _open_tab("https://www.ozon.ru/")
        all_ok = False

    # ═══════════════════════════════════════════
    # 4.5 seller.ozon.ru 卖家后台登录检查（运营数据）
    # ═══════════════════════════════════════════
    # www.ozon.ru 是选品端；seller.ozon.ru 是卖家后台（运营数据/月销/利润率判断靠它）
    print("\n  🔗 seller.ozon.ru 卖家后台登录检查（选品运营数据依赖）...")
    seller_ok = False
    if session_ok:
        try:
            from scripts.lib.cdp_client import CdpConnection
            from scripts.lib.ozon_seller_analytics import check_seller_login
            conn = CdpConnection("http://127.0.0.1:9222")
            seller_ok = check_seller_login(conn)
            conn.close()
        except Exception:
            pass
    print(f"  {_ok(seller_ok)} seller.ozon.ru 卖家后台已登录（运营数据可用）")
    if not seller_ok:
        print("  → 请在 Chrome 中打开 https://seller.ozon.ru/ 登录卖家后台")
        print("    （选品去 www.ozon.ru，运营数据在 seller.ozon.ru，两个登录态都要）")
        if session_ok:
            _open_tab("https://seller.ozon.ru/")

    # ═══════════════════════════════════════════
    # 5. 凭证检查
    # ═══════════════════════════════════════════
    print("\n📋 凭证:")

    # MXOU_TOKEN
    mxou = get_mxou_token()
    print(f"  {_ok(bool(mxou))} MXOU_TOKEN")
    if not mxou:
        print("  → python3 scripts/cli.py set_token --token <你的token>")

    # 1688 AK
    ak = get_ali_1688_ak()
    print(f"  {_ok(bool(ak))} 1688 AK")
    if not ak:
        print("  → python3 scripts/cli.py get_ak  # 自动获取")

    # Ozon stores
    stores = list_stores()
    if stores:
        print(f"  {_ok(True)} Ozon 店铺: {len(stores)} 个")
        for name, cfg in stores.items():
            cid = cfg.get("client_id", "")
            print(f"    • {name}: {cid[:8]}***" if cid else f"    • {name}: (未配置)")
    else:
        print(f"  {_ok(False)} Ozon 店铺未配置")
        print("  → python3 scripts/cli.py set_store --name \"店铺名\" --client-id <ID> --api-key <KEY>")
        all_ok = False

    # ═══════════════════════════════════════════
    # 6. Worker 连通检查
    # ═══════════════════════════════════════════
    from scripts._const import CLOUD_API_BASE
    worker_url = CLOUD_API_BASE.rstrip("/")
    print(f"\n🌐 Worker ({worker_url}):")
    try:
        resp = req.get(f"{worker_url}/api/v1/health", timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            print(f"  {_ok(True)} {data.get('message', '')} (DB: {data.get('db', '?')})")
        else:
            print(f"  {_ok(False)} 返回 {resp.status_code}")
            all_ok = False
    except Exception as e:
        print(f"  {_ok(False)} 不可达: {e}")
        all_ok = False

    # ═══════════════════════════════════════════
    # 7. Ozon API 验证（用第一个店铺）
    # ═══════════════════════════════════════════
    print("\n🏪 Ozon API:")
    if stores:
        first_name = next(iter(stores))
        first_cfg = stores[first_name]
        cid = first_cfg.get("client_id", "")
        akey = first_cfg.get("api_key", "")
        if cid and akey:
            print(f"  {_ok(True)} 店铺「{first_name}」Client ID: {cid[:8]}***")
            try:
                resp = req.post("https://api-seller.ozon.ru/v1/product/info/description",
                    headers={"Client-Id": cid, "Api-Key": akey, "Content-Type": "application/json"},
                    json={"product_id": 1}, timeout=10)
                if resp.status_code in (401, 403):
                    print(f"  {_ok(False)} API 认证失败，请检查 client_id / api_key")
                    all_ok = False
                else:
                    print(f"  {_ok(True)} API 认证通过")
            except Exception:
                print("  ⚠️ 网络超时（不影响后续使用）")
    else:
        print(f"  {_ok(False)} 无可用店铺配置")

    # ═══════════════════════════════════════════
    # 汇总
    # ═══════════════════════════════════════════
    print(f"\n{'='*55}")
    if all_ok:
        print("✅ 所有前置条件满足！")
        print("\n  python3 scripts/cli.py search <关键词>         # 1688搜索")
        print("  python3 scripts/cli.py graph --url <1688 URL>   # 组装信封")
        print("  python3 scripts/cli.py follow --ozon-url <Ozon URL>  # 跟卖")
        print("  python3 scripts/batch_test.py --urls-file <文件> --submit  # 批量")
    else:
        print("❌ 请先解决以上问题")
    print(f"{'='*55}")

    # Session 登录提醒
    if session_ok:
        print()
        if login_ok and ozon_cdp_ok:
            print("✅ Chrome 会话就绪：1688 已登录 + Ozon 信任已建立")
        else:
            print("⚠️ Chrome 会话待完善，请在新打开的 Chrome 窗口中：")
            step = 1
            if not login_ok:
                print(f"  {step}. 登录 1688: https://login.1688.com/member/signin.htm")
                step += 1
            if not ozon_cdp_ok:
                print(f"  {step}. 访问 Ozon: https://www.ozon.ru/ （随便点一个商品即可，不需要注册账号）")
                step += 1
            print("\n  完成后重新运行: python3 scripts/cli.py check")

    return 0 if all_ok else 1


def cmd_follow(args) -> int:
    """跟卖 Ozon 商品: Ozon URL → import-by-sku → 1688搜索 → CDP探针 → 上架"""
    from scripts.lib.config_store import AuthError, preflight_check, print_setup_guide
    try:
        from scripts.cloud_probe import follow_sell_cloud
    except ModuleNotFoundError:
        print("❌ 未找到 scripts.cloud_probe（版本过旧，缺云上架模块）。"
              "请升级：运行 `python3.12 bootstrap_update.py` 或重新下载最新包", flush=True)
        return 1

    missing = preflight_check()
    if missing:
        print_setup_guide(missing)
        return 1

    try:
        result = follow_sell_cloud(args.ozon_url, auto_submit=args.auto_submit, store_id=args.store or "")
    except AuthError as e:
        _out({"success": False, "error": str(e)})
        return 1
    _out(result)
    return 0 if result.get("success") else 1


# ═══════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════

def _parse_indexes(raw: str, total: int) -> list[int]:
    """解析序号输入 "1,3,5-8" → 0-based 索引列表（去重排序）。"""
    idxs: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo_s, hi_s = part.split("-", 1)
            lo, hi = int(lo_s), int(hi_s)
            if lo < 1 or hi > total or lo > hi:
                raise ValueError(f"序号越界: {part}")
            idxs.extend(range(lo - 1, hi))
        else:
            n = int(part)
            if n < 1 or n > total:
                raise ValueError(f"序号越界: {part}")
            idxs.append(n - 1)
    return sorted(set(idxs))


def _print_discover_table(candidates: list) -> None:
    """打印全量候选表格（指标 + 状态）。

    has_analytics=False（seller.ozon.ru 未登录/降级）时运营列显示 —，
    避免 0 误导为"真实销量为 0"。
    """
    status_map = {
        "ok": "✅可挑", "uncertain": "⚠️夹带?", "error": "❌失败",
        "filtered": "⏭️价区间外", "matched": "🔗已匹配", "profitable": "💰有利",
        "rejected": "⚠️利润低", "no_match": "❌无货源",
    }
    print(f"\n{'─' * 112}")
    print(f"{'#':>3} {'状态':<9} {'标题':<30} {'价格₽':>8} {'月销':>6} "
          f"{'增长%':>6} {'广告%':>6} {'跟卖':>4} {'上架天':>6} {'评分':>5}")
    print(f"{'─' * 112}")
    for i, c in enumerate(candidates, 1):
        title = c.ozon_title or c.error or "(无标题)"
        if len(title) > 30:
            title = title[:29] + "…"
        if c.has_analytics:
            sales_s, growth_s, drr_s, create_s = (
                f"{c.monthly_sales:>6}", f"{c.sales_growth:>6.1f}",
                f"{c.drr:>6.1f}", f"{c.create_days:>6}")
        else:
            sales_s = growth_s = drr_s = create_s = f"{'—':>6}"
        print(f"{i:>3} {status_map.get(c.status, c.status):<9} {title:<30} "
              f"{c.ozon_price:>8.0f} {sales_s} {growth_s} {drr_s} "
              f"{c.competing_sellers:>4} {create_s} {c.rating:>5.1f}")
    print(f"{'─' * 112}")


def _interactive_select(candidates: list) -> list | None:
    """交互挑选：输入序号（1,3,5-8 / all / 回车=全选可挑 / q=取消）。"""
    print("\n🎯 挑选要分析货源的产品（只对选中产品花 1688 识图配额）")
    print("   输入序号如 1,3,5-8 · all 全选 · 回车全选可挑 · q 取消", flush=True)
    while True:
        try:
            raw = input("挑选: ").strip()
        except (EOFError, KeyboardInterrupt):
            return None
        if raw.lower() in ("q", "quit", "exit"):
            return None
        if raw in ("", "all"):
            return [c for c in candidates if c.status in ("ok", "uncertain")]
        try:
            idxs = _parse_indexes(raw, len(candidates))
        except ValueError as e:
            print(f"  输入无效: {e}，请重试", flush=True)
            continue
        picked = [candidates[i] for i in idxs if candidates[i].status in ("ok", "uncertain")]
        if not picked:
            print("  所选产品都不可分析（失败/无数据），请重试", flush=True)
            continue
        return picked


def cmd_discover(args: argparse.Namespace) -> int:
    """Ozon 选品 v2 — 先全量采集 → 表格分析 → 挑完再找货源。"""
    from scripts.lib.chrome_launcher import ensure_chrome_cdp
    from scripts.lib.ozon_discovery import (
        DISCOVERY_CACHE_DIR,
        apply_selection_rules,
        collect_and_analyze,
        export_to_csv,
        export_to_json,
        match_selected,
    )

    print("🔍 Ozon 选品 v2（先采集 → 表格分析 → 挑完再找货源）", flush=True)
    print(f"   采集上限: {args.max_products} 个 | 最低利润率: {args.min_margin}% | 汇率: 1 RUB = {args.fx_rate} CNY", flush=True)
    if args.url or args.keyword:
        print(f"   来源: {'URL=' + args.url if args.url else '关键词=' + args.keyword}", flush=True)
    if args.rules:
        print(f"   自动筛选规则: {args.rules}", flush=True)
    print(flush=True)

    ok, msg = ensure_chrome_cdp(auto_restart=True, profile_dir=_chrome_profile_dir())
    if not ok:
        print(f"❌ Chrome 启动失败: {msg}")
        return 1
    cdp_url = "http://127.0.0.1:9222"

    def _collect_progress(current, total, candidate):
        mark = '✅' if candidate.status in ("ok", "uncertain") else '❌'
        print(f'  [{current}/{total}] {mark} {candidate.ozon_title[:36]}', flush=True)

    # ── 阶段①+② 采集 + 全量数据 + 运营指标 ──
    print("\n⏳ 阶段 1/3：采集产品列表 + 全量数据...", flush=True)
    try:
        candidates = collect_and_analyze(
            cdp_url=cdp_url,
            url=args.url or "",
            keyword=args.keyword or "",
            max_products=args.max_products,
            use_analytics=not args.no_analytics,
            min_price=args.min_price,
            max_price=args.max_price,
            brand_filter=args.brand_filter,
            progress_callback=_collect_progress,
        )
    except KeyboardInterrupt:
        print("\n⚠️ 用户中断")
        return 0

    print(f"\n📊 采集完成: {len(candidates)} 个产品（全量已落盘 {DISCOVERY_CACHE_DIR}/）")
    if not candidates:
        print("未采集到产品。检查关键词/URL 或增大 --max-products。")
        return 0

    # ── 阶段③ 表格展示 + 挑选 ──
    _print_discover_table(candidates)

    if args.rules:
        try:
            selected = apply_selection_rules(candidates, args.rules)
        except ValueError as e:
            print(f"❌ 规则错误: {e}")
            return 1
        print(f"\n🎯 规则筛选: {len(selected)}/{len(candidates)} 个命中", flush=True)
    else:
        selected = _interactive_select(candidates)
        if selected is None:
            print("已取消")
            return 0

    if not selected:
        print("未选择任何产品。")
        return 0

    # 全量导出（含 rejected/error，供表格分析）
    if args.export in ("csv", "both"):
        output = args.output or "data/discovery/discover_export.csv"
        csv_path = export_to_csv(candidates, output)
        print(f"📄 CSV 已导出（全量）: {csv_path}")

    if args.export in ("json", "both"):
        output = args.output or "data/discovery/discover_export.json"
        if args.export == "both":
            output = (args.output.replace(".csv", ".json")
                      if args.output and args.output.endswith(".csv")
                      else "data/discovery/discover_export.json")
        json_path = export_to_json(candidates, output)
        print(f"📄 JSON 已导出（全量）: {json_path}")

    # ── 阶段④ 批量货源（只对选中产品花 1688 配额）──
    print(f"\n⏳ 阶段 2/3：对选中的 {len(selected)} 个产品批量找 1688 货源...", flush=True)
    from scripts.lib.config_store import get_store_profile

    store_profile = {}
    try:
        store_profile = get_store_profile(args.store or "")
    except Exception:
        pass
    commission_rate = float(store_profile.get("commission_rate", 0) or 0)

    def _match_progress(current, total, candidate):
        mark = {'profitable': '💰', 'rejected': '⚠️', 'no_match': '❌'}.get(candidate.status, '·')
        print(f'  [{current}/{total}] {mark} {candidate.ozon_title[:36]}  '
              f'1688=¥{candidate.match_1688_price:.0f} 利润={candidate.profit_margin:.1f}%', flush=True)

    try:
        from scripts.lib.config_store import get_mxou_token as _get_tok
        match_selected(
            selected,
            cdp_url,
            fx_rate=args.fx_rate,
            min_margin_pct=args.min_margin,
            commission_rate=commission_rate,
            progress_callback=_match_progress,
            mxou_token=_get_tok() or "",
        )
    except KeyboardInterrupt:
        print("\n⚠️ 用户中断")
        return 0

    # ── 结果展示 ──
    print("\n📊 阶段 3/3：货源分析结果\n")
    _print_discover_table(selected)

    profitable = [c for c in selected if c.status == "profitable"]
    print(f"\n✅ 符合条件: {len(profitable)} 个 | 利润不足: {sum(1 for c in selected if c.status == 'rejected')} 个 | 无货源: {sum(1 for c in selected if c.status == 'no_match')} 个")

    # 选中+货源结果导出（仅当请求导出时追加一份 _matched 文件）
    if args.export in ("csv", "both"):
        base = args.output or "data/discovery/discover_export.csv"
        matched_csv = base.replace(".csv", "_matched.csv") if base.endswith(".csv") \
            else "data/discovery/discover_matched.csv"
        csv_path = export_to_csv(selected, matched_csv)
        print(f"📄 CSV 已导出（选中+货源）: {csv_path}")

    # ── auto-submit ──
    if args.auto_submit:
        to_submit = [c for c in selected if c.status == "profitable" and c.match_1688_url]
        if not to_submit:
            print("\n⚠️ 没有符合条件的 profitable 产品可提交")
            return 0
        print(f"\n🚀 提交 {len(to_submit)} 个产品到 Worker...", flush=True)
        confirm = input("确认提交？(y/N) ")
        if confirm.lower() != 'y':
            print("已取消")
            return 0
        try:
            from scripts.cloud_probe import (
                build_envelope_from_discovery,
                submit_envelope,
            )
        except ModuleNotFoundError:
            print("❌ 未找到 scripts.cloud_probe（版本过旧，缺云上架模块）。"
                  "请升级：运行 `python3.12 bootstrap_update.py` 或重新下载最新包", flush=True)
            return 1
        from scripts.lib.config_store import get_store

        store = get_store(args.store or "") or {}
        store_config = {
            "client_id": store.get("client_id", ""),
            "api_key": store.get("api_key", ""),
        }
        store_id = args.store or ""
        for c in to_submit:
            try:
                envelope = build_envelope_from_discovery(c, store_config, store_id=store_id)
                if not envelope:
                    print(f"  ✗ 跳过（无 1688 URL）: {c.ozon_title[:40]}")
                    continue
                result = submit_envelope(envelope)
                task_id = result.get("task_id", "")
                print(f"  ✓ 已提交: {c.ozon_title[:40]} → task_id={task_id}")
            except Exception as e:
                print(f"  ✗ 提交失败: {c.ozon_title[:40]} — {e}")

    print(f"\n📁 选品日志已缓存: {DISCOVERY_CACHE_DIR}/")
    return 0


# ═══════════════════════════════════════════════════════════════════════════
# 管线 E：趋势驱动选品（v0.25 S3）
# ═══════════════════════════════════════════════════════════════════════════
def cmd_trend(args: argparse.Namespace) -> int:
    """趋势驱动选品：web 趋势 → AI 细分关键词 → 1688 AK 搜索（满 3 即停）→ 展示。"""
    from scripts.lib.config_store import get_mxou_token
    from scripts.lib.trend_selection import (
        collect_market_info,
        render_results,
        search_by_keywords,
        summarize_keywords,
    )
    token = get_mxou_token() or ""
    if not token:
        _out({"error": "缺少 MXOU_TOKEN，请先 set_token"})
        return 1
    searxng = os.environ.get("SEARXNG_URL", "")
    info = collect_market_info(args.category, args.market_info, searxng)
    if "（未提供市场信息" in info:
        print("⚠️ 未提供市场信息（--market-info 或 SEARXNG_URL），AI 将基于品类名总结，建议先用 web_search 收集趋势结果传入。", flush=True)
    try:
        keywords = summarize_keywords(token, info, args.category)
    except ValueError as e:
        _out({"error": str(e)})
        return 1
    if not keywords:
        _out({"error": "AI 未提炼出有效关键词"})
        return 1
    filters = {}
    for k in ("max_price", "max_moq", "min_ship_rate_48h", "min_sales"):
        v = getattr(args, k, None)
        if v is not None:
            filters[k] = v
    results = search_by_keywords(keywords, max_results=3, **filters)
    if args.with_skus:
        _attach_skus_via_cdp(results)
    print(render_results(results), flush=True)
    if args.export:
        _export_trend(results, args.export, args.output)
    return 0


def _attach_skus_via_cdp(results: list) -> None:
    """用 CDP 详情抓取补 SKU 明细（失败跳过，不阻断）。"""
    from scripts.cloud_probe import build_graph_envelope
    for r in results:
        url = (r.get("item") or {}).get("detail_url") or ""
        if not url:
            continue
        import re as _re
        m = _re.search(r"/(\d+)\.html", url)
        if not m:
            continue
        try:
            env = build_graph_envelope(item_id=m.group(1), detail_url=url)
            variants = ((env or {}).get("draft") or {}).get("variants") or []
            skus = []
            for v in variants:
                try:
                    price = float(v.get("price") or 0)
                except (TypeError, ValueError):
                    price = 0
                skus.append({
                    "name": str(v.get("name") or v.get("spec") or ""),
                    "price": price,
                    "suggestedPrice": round(price * 3, 2),
                    "stock": v.get("stock", ""),
                })
            if skus:
                r["item"]["skus"] = skus
        except Exception as e:
            print(f"⚠️ SKU 抓取失败（跳过）: {e}", flush=True)


def _export_trend(results: list, fmt: str, output: str) -> None:
    import csv as _csv
    rows = [{
        "keyword": r["keyword"], "title": r["item"].get("title", ""),
        "price": r["item"].get("price", ""), "moq": r["item"].get("moq", ""),
        "ship_rate_48h": r["item"].get("ship_rate_48h", ""),
        "sales": r["item"].get("sales", ""), "supplier": r["item"].get("supplier", ""),
        "detail_url": r["item"].get("detail_url", ""),
    } for r in results]
    base = output or f"data/discovery/trend_{int(time.time())}"
    if fmt in ("json", "both"):
        with open(f"{base}.json", "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
    if fmt in ("csv", "both"):
        with open(f"{base}.csv", "w", encoding="utf-8-sig", newline="") as f:
            w = _csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else [])
            w.writeheader()
            w.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="pounding-ozon-probe — 1688 数据采集 + GraphInput 组装")
    sub = parser.add_subparsers(dest="command", help="命令")

    # ── Config commands ──

    # set_store
    ss = sub.add_parser("set_store", help="配置 Ozon 店铺凭证")
    ss.add_argument("--name", required=True, help="店铺名称")
    ss.add_argument("--client-id", required=True, help="Ozon Client ID")
    ss.add_argument("--api-key", required=True, help="Ozon API Key")
    ss.add_argument("--currency", default="", help="货币（默认 RUB）")
    ss.set_defaults(func=cmd_set_store)

    # list_stores
    ls = sub.add_parser("list_stores", help="列出所有已配置的 Ozon 店铺")
    ls.set_defaults(func=cmd_list_stores)

    # set_token
    st = sub.add_parser("set_token", help="设置 MXOU_TOKEN")
    st.add_argument("--token", required=True, help="MXOU API Token")
    st.set_defaults(func=cmd_set_token)

    # set_ak
    sa = sub.add_parser("set_ak", help="手动设置 1688 AK")
    sa.add_argument("--ak", required=True, help="1688 Access Key")
    sa.set_defaults(func=cmd_set_ak)

    # ── Business commands ──

    # check (诊断)
    cp = sub.add_parser("check", help="诊断前置条件（Chrome / 凭证 / Worker / Ozon API）")
    cp.set_defaults(func=cmd_check)

    # search
    sp = sub.add_parser("search", help="搜索 1688 商品")
    sp.add_argument("query", help="搜索关键词")
    sp.add_argument("--page-size", type=int, default=5, help="返回数量")
    sp.set_defaults(func=cmd_search)

    # probe
    pp = sub.add_parser("probe", help="CDP 探针抓取 1688 商品")
    pp.add_argument("--url", required=True, help="1688 商品详情页 URL")
    pp.add_argument("--timeout", type=int, default=30, help="CDP 超时秒数")
    pp.set_defaults(func=cmd_probe)

    # graph
    gp = sub.add_parser("graph", help="组装 GraphInput envelope")
    gp.add_argument("--item-id", default="", help="1688 商品 ID")
    gp.add_argument("--url", default="", help="1688 商品详情页 URL（也可提供）")
    gp.add_argument("--category-query", default="", help="Ozon 类目关键词（俄语）")
    gp.add_argument("--retries", type=int, default=3, help="CDP 重试次数")
    gp.add_argument("--store", default="", help="Ozon 店铺名称（不指定则用默认店铺）")
    gp.add_argument("--no-submit", action="store_true", help="只组装信封不提交 Worker")
    gp.set_defaults(func=cmd_graph)

    # image_search
    ip = sub.add_parser("image_search", help="以图搜款 — 上传图片搜索 1688 同款")
    ip.add_argument("--image", required=True, help="图片路径或 URL")
    ip.add_argument("--limit", type=int, default=10, help="返回数量")
    ip.add_argument("--sort", default="", help="排序: price_asc/price_desc/sold_desc/yx_desc")
    ip.add_argument("--source", choices=["ak", "cdp"], default="ak",
                    help="图搜引擎: ak=1688 AK API（默认），cdp=1688 网页 CDP（更准确）")
    ip.set_defaults(func=cmd_image_search)

    # get_ak
    akp = sub.add_parser("get_ak", help="通过浏览器自动获取 1688 AK")
    akp.add_argument("--timeout", type=int, default=300, help="超时秒数")
    akp.set_defaults(func=cmd_get_ak)

    # follow
    fp = sub.add_parser("follow", help="跟卖 Ozon 商品（Ozon URL → 1688找同款 → 上架）")
    fp.add_argument("--ozon-url", required=True, help="Ozon 商品页 URL")
    fp.add_argument("--auto-submit", action="store_true", help="自动提交到 Worker")
    fp.add_argument("--store", default="", help="Ozon 店铺名称")
    fp.set_defaults(func=cmd_follow)

    dp = sub.add_parser("discover", help="Ozon 选品 v2（先采集 → 表格分析 → 挑完再找货源）")
    dp.add_argument("--url", default="", help="Ozon 页面 URL（搜索页/类目页等，直接采集该页）")
    dp.add_argument("--keyword", default="", help="搜索关键词（自动构造 /search/?text= 搜索页）")
    dp.add_argument("--max-products", type=int, default=50, help="最多采集产品数（默认 50）")
    dp.add_argument("--min-margin", type=float, default=15.0, help="最低利润率%%")
    dp.add_argument("--max-sellers", type=int, default=10, help="最大跟卖人数（兼容保留，v2 中不硬过滤）")
    dp.add_argument("--fx-rate", type=float, default=0.075, help="RUB→CNY 汇率")
    dp.add_argument("--store", default="", help="Ozon 店铺名（定价参数/提交凭证来源）")
    dp.add_argument("--no-analytics", action="store_true", help="不查 seller.ozon.ru 运营指标（默认自动尝试）")
    dp.add_argument("--min-price", type=float, default=0, help="价格下限（RUB，0=不限），区间外产品标记价区间外")
    dp.add_argument("--max-price", type=float, default=0, help="价格上限（RUB，0=不限）")
    dp.add_argument("--brand-filter", choices=["nobrand", "known", "all"], default="nobrand",
                    help="品牌过滤: nobrand=只要无品牌/白牌（默认），known=只过滤知名品牌黑名单，all=不过滤")
    dp.add_argument("--rules", default="", help="自动筛选规则，如 \"monthly_sales>=200,drr<=30\"（跳过交互挑选）")
    dp.add_argument("--export", choices=["csv", "json", "both"], default="", help="导出格式（全量+选中）")
    dp.add_argument("--output", default="", help="导出文件路径")
    dp.add_argument("--auto-submit", action="store_true", help="确认后提交 profitable 产品到 Worker")
    dp.set_defaults(func=cmd_discover)

    # ── 管线 E：趋势驱动选品（web_search/SearXNG → AI 关键词 → AK 搜索）──
    tr = sub.add_parser("trend", help="趋势驱动选品：web 趋势 → AI 细分关键词 → 1688 AK 搜索")
    tr.add_argument("--category", required=True, help="大品类，如 玩具/家居/3C数码")
    tr.add_argument("--market-info", default="", help="web_search 结果文本文件（推荐）；不传则用 SEARXNG_URL")
    tr.add_argument("--max-price", type=float, default=None, help="最高单价（元）")
    tr.add_argument("--max-moq", type=int, default=None, help="最大起批量（件）")
    tr.add_argument("--min-ship-rate-48h", type=float, default=None, help="最低48H发货率（%%）")
    tr.add_argument("--min-sales", type=int, default=None, help="最低年销量（件）")
    tr.add_argument("--with-skus", action="store_true", help="用 CDP 抓 Top 商品 SKU 明细")
    tr.add_argument("--export", choices=["json", "csv", "both"], default="", help="导出格式")
    tr.add_argument("--output", default="", help="导出文件路径")
    tr.set_defaults(func=cmd_trend)

    # ── 自动更新 ──
    up = sub.add_parser("update", help="检查并应用 Skill 自动更新")
    up.set_defaults(func=cmd_update)

    # ── 任务查询(v0.28.5 C1)──
    q = sub.add_parser("query", help="查询 Worker 任务状态(任务 ID)")
    q.add_argument("task_id", help="submit/follow 返回的任务 ID(UUID)")
    q.set_defaults(func=cmd_query)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return 0

    # ⚠️ 每次命令静默检查更新（后台不阻塞，失败静默；update 命令本身跳过）
    _silent_update_check(args.command)

    # ⚠️ v0.28.6: 工具 Chrome 改回「独立 profile + 用完即关」(v0.28.3 方案)。
    # v0.28.4 的「常驻复用」实测有坑: 常驻实例被用户手动关闭后, 下次命令检测
    # CDP 不可用 → 重启撞上「用户 Chrome 占用默认 profile」单实例锁 → 反复
    # 杀/重启失败(用户电脑实测复现)。独立 profile + 命令出口关闭:
    #   - 独立 profile 不与用户 Chrome 冲突, 启动必成功
    #   - 用完即关, 不累积窗口、不常驻、不反复
    #   - close_tool_chrome 仅关本进程启动过的实例(PID 文件), 复用已有 CDP 不关
    try:
        return args.func(args)
    finally:
        try:
            from scripts.lib.chrome_launcher import close_tool_chrome
            close_tool_chrome()
        except Exception:
            pass


def _silent_update_check(command: str) -> None:
    """每次命令检查 Skill 更新（不阻塞主流程，失败静默）。

    默认自动应用（v0.18.0）：有新版本即备份-覆盖-失败回滚；
    SKILL_AUTO_UPDATE=0 时退回「提示 + 手动 skill update」模式。
    """
    if command == "update":
        return
    try:
        from scripts.lib import updater
        if os.environ.get("SKILL_AUTO_UPDATE", "1") != "0":
            result = updater.auto_update_if_available()
            if result and result.get("ok"):
                print(f"\n✅ 已自动更新至 v{result['new_version']}，请重启终端后重新运行命令", flush=True)
            elif result:
                print(f"\n❌ 自动更新失败（已回滚）: {result['error']}", flush=True)
            return
        info = updater.check_update()
        if info:
            print(f"\n📦 发现新版本 v{info.get('version')}（当前 v{updater.get_local_version()}）"
                  f"—— 运行 `skill update` 更新", flush=True)
    except Exception:
        pass


def cmd_update(args: argparse.Namespace) -> int:
    """检查并应用 Skill 自动更新（从 COS manifest 下载全覆盖）。"""
    from scripts.lib.updater import run_update_command
    return run_update_command()


def cmd_query(args: argparse.Namespace) -> int:
    """查询 Worker 任务状态（v0.28.5 C1: agent 不再只能盲等, 可主动查进度）。

    返回: completed → 0; 终态(failed/cancelled/not_found) → 0(信息完整);
          非终态(processing/queued) → 0(展示进度); 查询失败 → 1。
    """
    from scripts.cloud_probe import check_task_status

    r = check_task_status(args.task_id)
    status = r.get("status", "unknown")
    print(f"任务 {args.task_id}: {status}")
    if r.get("started_at"):
        print(f"  开始: {r.get('started_at')}")
    if r.get("completed_at"):
        print(f"  完成: {r.get('completed_at')}")
    if r.get("retry_count"):
        print(f"  重试次数: {r.get('retry_count')}")

    if r.get("ok"):
        result = r.get("result_json") or {}
        ps = result.get("product_summary") or []
        if ps:
            print(f"  ✅ 产品明细({len(ps)}):")
            for p in ps:
                pid = p.get("product_id") or "-"
                price = p.get("price") or "-"
                profit = p.get("profit_rate") or "-"
                ostatus = p.get("ozon_status") or "-"
                err = f" | 备注: {p.get('ozon_error')}" if p.get("ozon_error") else ""
                print(f"    - OzonID {pid} | 售价 {price} | 净利润率 {profit} | 审核 {ostatus}{err}")
        else:
            print("  ✅ 任务完成(无产品明细)")
    elif r.get("error_message"):
        print(f"  ❌ 错误: {r.get('error_message')}")
    elif status == "not_found":
        print("  ⚠️ 任务不存在(可能已过期或 ID 有误)")
    elif status == "worker_unreachable":
        print("  ⚠️ Worker 不可达, 请检查网络/Worker 地址")
    else:
        print("  ⏳ 处理中...")

    return 0


if __name__ == "__main__":
    sys.exit(main())
