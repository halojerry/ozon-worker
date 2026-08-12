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
import platform
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
    except ModuleNotFoundError as _e:
        # PR-3: 精确归因 — 缺依赖（preflight 兜底遗漏）vs 缺模块（包不完整）
        _ename = getattr(_e, "name", "") or ""
        if _ename and _ename not in ("scripts.cloud_probe", "cloud_probe"):
            print(f"❌ 缺少依赖模块 '{_ename}'。请运行: pip install -r requirements.txt", flush=True)
        else:
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

    # ⚠️ PR-3: CDP 前置 — 进 enrich 前确保 Chrome 就绪（不再等到链路深处 60s 才报）
    try:
        from scripts.lib.chrome_launcher import ensure_chrome_cdp
        _ok_cdp, _msg_cdp = ensure_chrome_cdp(profile_dir=_chrome_profile_dir())
        if not _ok_cdp:
            print(f"❌ Chrome CDP 不可用: {_msg_cdp}", flush=True)
            print("  → 请运行 `python3 scripts/cli.py check` 查看环境诊断", flush=True)
            return 1
    except Exception as _cdp_e:
        print(f"❌ Chrome CDP 前置检查异常: {_cdp_e}", flush=True)
        print("  → 请运行 `python3 scripts/cli.py check` 查看环境诊断", flush=True)
        return 1

    try:
        graph = build_graph_envelope_with_retry(
            item_id=item_id,
            detail_url=detail_url,
            category_query=args.category_query,
            max_retries=args.retries,
            store_id=args.store or "",
        )

        # ⚠️ v0.29.x 竞品属性复用: --ozon-ref-url 抓 Ozon 竞品属性表 → draft.ozon_attributes
        # (同类目竞品属性值大多一致, worker 对 1688 缺的属性用竞品值兜底)
        if getattr(args, 'ozon_ref_url', '') and isinstance(graph.get("envelope", {}), dict):
            try:
                # ✅ v0.36: 昂贵 CDP 抓取走磁盘缓存包装（_cached_ozon_scrape，6h）
                from scripts.cloud_probe import _cached_ozon_scrape
                _oz = _cached_ozon_scrape(
                    args.ozon_ref_url, cdp_url="http://127.0.0.1:9222", timeout=30)
                _oa = _oz.get("attributes") or {}
                _full = _oz.get("characteristics") or []
                _attrs_all = dict(_oa)
                for _fc in _full:
                    if isinstance(_fc, dict) and _fc.get("title") and _fc.get("value"):
                        _attrs_all.setdefault(str(_fc["title"]), str(_fc["value"]))
                if _attrs_all:
                    graph["envelope"]["draft"]["ozon_attributes"] = _attrs_all
                    # ⚠️ v0.29.x: 透传竞品类目(供 worker 校验类目一致才复用,
                    # 防跨类目属性错配——实测手持风扇 vs 护发素)
                    _oz_cat = _oz.get("description_category_id") or ""
                    if str(_oz_cat).isdigit():
                        graph["envelope"]["draft"]["ozon_attributes_category"] = int(_oz_cat)
                    print(f"✅ 竞品属性透传: {len(_attrs_all)} 个(ozon-ref-url)")
            except Exception as _oz_e:
                print(f"⚠️ 竞品属性抓取失败(继续): {_oz_e}")
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
        except ModuleNotFoundError as _e:
            # PR-3: 精确归因 — 缺依赖 vs 缺模块
            _ename = getattr(_e, "name", "") or ""
            if _ename and _ename not in ("scripts.cloud_probe", "cloud_probe"):
                print(f"❌ 缺少依赖模块 '{_ename}'。请运行: pip install -r requirements.txt", flush=True)
            else:
                print("❌ 未找到 scripts.cloud_probe（版本过旧，缺云上架模块）。"
                      "请升级：运行 `python3.12 bootstrap_update.py` 或重新下载最新包", flush=True)
            return 1
        # P1-4 --notify: 顶层透传，Worker 收到 payload.notify 后任务终态推 webhook
        if getattr(args, 'notify', False):
            graph["notify"] = True
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

    --source aibuy → 1688 mtop API 直调图搜（v0.39 默认，免浏览器官方排序）
    --source ak    → 1688 AK API 图搜（无需浏览器）
    --source cdp   → 1688 网页版 CDP 图搜（需 Chrome 登录 1688）
    """
    from scripts.lib.config_store import AuthError, preflight_check, print_setup_guide

    missing = preflight_check(skip_store=True)
    if missing:
        print_setup_guide(missing)
        return 1

    # ⚠️ PR-3: --source cdp 需要 Chrome；aibuy/ak 引擎无需浏览器不探测
    if args.source == "cdp":
        try:
            from scripts.lib.chrome_launcher import ensure_chrome_cdp
            _ok_cdp, _msg_cdp = ensure_chrome_cdp(profile_dir=_chrome_profile_dir())
            if not _ok_cdp:
                print(f"❌ Chrome CDP 不可用: {_msg_cdp}", flush=True)
                print("  → 请运行 `python3 scripts/cli.py check` 查看环境诊断", flush=True)
                return 1
        except Exception as _cdp_e:
            print(f"❌ Chrome CDP 前置检查异常: {_cdp_e}", flush=True)
            print("  → 请运行 `python3 scripts/cli.py check` 查看环境诊断", flush=True)
            return 1

    try:
        if args.source == "aibuy":
            from scripts.lib.ozon_image_search import search_by_image_aibuy
            results = search_by_image_aibuy(
                image_url=args.image,
                page_size=max(args.limit, 20),
            )
        elif args.source == "cdp":
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
            "id": p.get("product_id") or p.get("itemId") or p.get("id", ""),
            "title": p.get("title", "")[:100],
            "price": p.get("price", ""),
            "image": p.get("image_url") or p.get("image", ""),
            "detail_url": p.get("detail_url", "")
                or (f"https://detail.1688.com/offer/{p.get('id', '')}.html"
                    if p.get("id") else ""),
            "supplier": p.get("supplier") or p.get("company_name", ""),
            "sold_count": p.get("sold_count") or p.get("month_sold", ""),
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
        # PR-3: 不再 early return — 标记失败后继续探测 Worker/MXOU/凭证，
        # 让 check 一次性给出全环境诊断（浏览器缺失不影响远程探测项）
        print("  ❌ 未检测到可用浏览器")
        print("  → 请安装 Google Chrome: https://www.google.com/chrome/")
        all_ok = False

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
    # 6.5 MXOU 平台余额(v0.29.3 本地直查, 与 Worker 统一来源)
    # ═══════════════════════════════════════════
    try:
        from scripts.lib.config_store import fetch_mxou_balance, get_mxou_token
        _mtok = get_mxou_token()
        if _mtok:
            _mbal = fetch_mxou_balance(_mtok)
            if _mbal is None:
                print("  ⚠️ MXOU 余额查询失败(网络/接口)")
            elif _mbal <= 0:
                print(f"  {_ok(False)} MXOU 余额不足: {_mbal:.2f}(请到 https://api.mxou.cn 充值)")
                all_ok = False
            else:
                print(f"  {_ok(True)} MXOU 余额: {_mbal:.2f}")
        else:
            print("  ⚠️ 未配置 MXOU token(set_token)")
    except Exception:
        pass

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
    except ModuleNotFoundError as _e:
        # PR-3: 精确归因 — 缺依赖（preflight 兜底遗漏）vs 缺模块（包不完整）
        _ename = getattr(_e, "name", "") or ""
        if _ename and _ename not in ("scripts.cloud_probe", "cloud_probe"):
            print(f"❌ 缺少依赖模块 '{_ename}'。请运行: pip install -r requirements.txt", flush=True)
        else:
            print("❌ 未找到 scripts.cloud_probe（版本过旧，缺云上架模块）。"
                  "请升级：运行 `python3.12 bootstrap_update.py` 或重新下载最新包", flush=True)
        return 1

    missing = preflight_check()
    if missing:
        print_setup_guide(missing)
        return 1

    # ⚠️ PR-3: CDP 前置 — follow 全链路依赖 Chrome，启动失败立即报（不再 warning+continue 空跑）
    try:
        from scripts.lib.chrome_launcher import ensure_chrome_cdp
        _ok_cdp, _msg_cdp = ensure_chrome_cdp(profile_dir=_chrome_profile_dir())
        if not _ok_cdp:
            print(f"❌ Chrome CDP 不可用: {_msg_cdp}", flush=True)
            print("  → 请运行 `python3 scripts/cli.py check` 查看环境诊断", flush=True)
            return 1
    except Exception as _cdp_e:
        print(f"❌ Chrome CDP 前置检查异常: {_cdp_e}", flush=True)
        print("  → 请运行 `python3 scripts/cli.py check` 查看环境诊断", flush=True)
        return 1

    try:
        result = follow_sell_cloud(args.ozon_url, auto_submit=args.auto_submit,
                                   store_id=args.store or "",
                                   review=getattr(args, "review", False),
                                   notify=getattr(args, "notify", False))
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
    print(f"\n{'─' * 104}")
    print(f"{'#':>3} {'状态':<9} {'标题':<30} {'价格₽':>8} {'月销':>6} "
          f"{'增长%':>6} {'广告%':>6} {'跟卖':>4} {'上架天':>6} {'评分':>5} {'蓝海':>5}")
    print(f"{'─' * 104}")
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
              f"{c.competing_sellers:>4} {create_s} {c.rating:>5.1f} "
              f"{getattr(c, 'blue_ocean_score', 0):>5}")
    print(f"{'─' * 104}")


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


def _fetch_live_blue_ocean_queries(cdp_url: str, keyword: str) -> list[dict]:
    """实时 what_to_sell all-queries 蓝海行（--blue-ocean-source queries 专用）。

    复用 cmd_queries 的 seller tab 机制（check_seller_login + fetch_all_queries，
    fetch_all_queries 内部自建/复用 seller tab）。行结构与 load_blue_ocean_csv
    同构（query/count/ca/avg_ca_rub/uniq_sellers/ordering_amount/daily_avg/gmv/
    uniq_queries_w_ca/search_users_to_ord_users）。未登录/异常/空 → []，
    调用方降级本地 CSV（绝不崩/绝不阻塞）。
    """
    import logging
    _logger = logging.getLogger(__name__)
    try:
        from scripts.lib.cdp_client import CdpConnection
        from scripts.lib.ozon_seller_analytics import (
            check_seller_login,
            fetch_all_queries,
        )
        with CdpConnection(cdp_url) as cdp:
            if not check_seller_login(cdp):
                _logger.warning("seller.ozon.ru 未登录，蓝海实时查询降级本地 CSV")
                return []
            rows = fetch_all_queries(cdp, keyword=keyword or None)
            if rows:
                return rows
            _logger.warning("what_to_sell all-queries 无结果，蓝海降级本地 CSV")
    except Exception as exc:
        _logger.warning("蓝海实时查询失败，降级本地 CSV: %s", exc)
    return []


def cmd_discover(args: argparse.Namespace) -> int:
    """Ozon 选品 v2 — 先全量采集 → 表格分析 → 挑完再找货源。"""
    from scripts.lib.chrome_launcher import ensure_chrome_cdp
    from scripts.lib.ozon_discovery import (
        DEFAULT_FX_RATE,
        DISCOVERY_CACHE_DIR,
        apply_selection_rules,
        collect_and_analyze,
        export_to_csv,
        export_to_json,
        match_selected,
    )
    from scripts.lib.config_store import get_setting, get_store_profile

    # P2-6: fx_rate 三级解析 —— CLI 显式 > 店铺 stores.json fx_rate > settings.json fx_rate > 0.075
    # （卢布波动时按店铺/全局配置调整，避免利润估算失真）
    fx_rate = args.fx_rate if args.fx_rate is not None else float(
        (get_store_profile(args.store) or {}).get("fx_rate")
        or get_setting("fx_rate", DEFAULT_FX_RATE)
        or DEFAULT_FX_RATE)

    print("🔍 Ozon 选品 v2（先采集 → 表格分析 → 挑完再找货源）", flush=True)
    print(f"   采集上限: {args.max_products} 个 | 最低利润率: {args.min_margin}% | 汇率: 1 RUB = {fx_rate} CNY", flush=True)
    if args.url or args.keyword:
        print(f"   来源: {'URL=' + args.url if args.url else '关键词=' + args.keyword}", flush=True)
    if args.rules:
        print(f"   自动筛选规则: {args.rules}", flush=True)

    # ── C4 step2: all_queries 蓝海数据反哺（--blue-ocean-source）──
    # 可选增强源：加载成功 → 每候选按标题计算 competitor_keyword_density 注入蓝海评分；
    # CSV 缺失/解析失败 → 打印降级提示，走原流程（绝不崩）。
    # --blue-ocean-source queries + --keyword：优先实时 what_to_sell 查询
    # （复用 cmd_queries 的 seller tab 机制）；未登录/异常/空 → 静默降级本地 CSV。
    ok, msg = ensure_chrome_cdp(auto_restart=True, profile_dir=_chrome_profile_dir())
    if not ok:
        print(f"❌ Chrome 启动失败: {msg}")
        return 1
    cdp_url = "http://127.0.0.1:9222"

    blue_ocean_rows: list[dict] = []
    if args.blue_ocean_source:
        from scripts.lib.ozon_discovery import load_blue_ocean_csv
        csv_path = args.blue_ocean_csv or "/tmp/queries_all.csv"
        _live = False
        if args.blue_ocean_source == "queries" and (args.keyword or "").strip():
            blue_ocean_rows = _fetch_live_blue_ocean_queries(cdp_url, args.keyword)
            _live = bool(blue_ocean_rows)
        if not blue_ocean_rows:
            blue_ocean_rows = load_blue_ocean_csv(csv_path)
        if blue_ocean_rows:
            _src = "what_to_sell 实时查询" if _live else csv_path
            print(f"🌊 蓝海增强: 载入 {len(blue_ocean_rows)} 个关键词（{_src}）", flush=True)
        else:
            print("no blue_ocean data, fallback to original", flush=True)
    print(flush=True)

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
            china=(not args.local) or args.china,
        )
    except KeyboardInterrupt:
        print("\n⚠️ 用户中断")
        return 0

    # ── 阶段②b 裂变选品（v3, --fission）──
    if args.fission and candidates:
        from scripts.lib.ozon_fission import run_fission
        if args.max_depth > 3 and not args.allow_depth_3:
            print(f"❌ 裂变深度 {args.max_depth} > 3 需显式 --allow-depth-3（指数爆炸风险）")
            return 1
        print(f"\n⏳ 裂变选品（depth≤{args.max_depth}, 候选上限 {args.max_total_products}, 时间预算 {args.time_budget:.0f}s）...", flush=True)
        try:
            def _stage_done(total, sellers, depth):
                print(f"  [深度 {depth}] 候选 {total} 个 | 已展开卖家 {sellers} 个", flush=True)
            candidates = run_fission(
                seed_products=candidates,
                cdp_url=cdp_url,
                max_depth=args.max_depth,
                max_total_products=args.max_total_products,
                time_budget=args.time_budget,
                max_sellers_per_product=args.max_sellers_per_product,
                max_products_per_seller=args.max_products_per_seller,
                session_id=f"discover_{int(time.time())}",
                stage_callback=_stage_done,
            )
            if not args.non_interactive and len(candidates) > args.max_products:
                print(f"\n📊 裂变完成: {len(candidates)} 个候选（原始 {args.max_products} + 裂变新增）。继续展示表格？[y/n] ", end="", flush=True)
                if input().strip().lower() not in ("y", "yes", ""):
                    print("已取消")
                    return 0
        except KeyboardInterrupt:
            print("\n⚠️ 用户中断")
            return 0

    print(f"\n📊 采集完成: {len(candidates)} 个产品（全量已落盘 {DISCOVERY_CACHE_DIR}/）")
    if not candidates:
        print("未采集到产品。检查关键词/URL 或增大 --max-products。")
        return 0

    # ── C4 step2: 蓝海反哺预计算（阶段③ 表格展示前，便于挑选）──
    if blue_ocean_rows:
        from scripts.lib.ozon_discovery import (
            calculate_blue_ocean_score,
            compute_competitor_keyword_density,
        )
        for c in candidates:
            density = compute_competitor_keyword_density(
                blue_ocean_rows, c.ozon_title or args.keyword or "")
            c.blue_ocean_score = calculate_blue_ocean_score(
                c, competitor_keyword_density=density)

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
            fx_rate=fx_rate,
            min_margin_pct=args.min_margin,
            commission_rate=commission_rate,
            progress_callback=_match_progress,
            mxou_token=_get_tok() or "",
            blue_ocean_rows=blue_ocean_rows or None,
        )
    except KeyboardInterrupt:
        print("\n⚠️ 用户中断")
        return 0

    # ── 结果展示 ──
    print("\n📊 阶段 3/3：货源分析结果\n")
    _print_discover_table(selected)

    profitable = [c for c in selected if c.status == "profitable"]
    print(f"\n✅ 符合条件: {len(profitable)} 个 | 利润不足: {sum(1 for c in selected if c.status == 'rejected')} 个 | 无货源: {sum(1 for c in selected if c.status == 'no_match')} 个")

    # ── D3 L3: 人工评审暂停（--review 或设置 visual_review）──
    # 弱匹配候选（badge_eff<0.5 或 conf<0.3）逐个过目：y=approved / n=agent_reject，
    # 决策写 review_log（判定不静默消失）。默认（无 --review）零 input() 调用。
    from scripts.lib.config_store import get_setting
    if getattr(args, "review", False) or get_setting("visual_review", False):
        weak = [c for c in selected
                if c.match_1688_url
                and (c.match_badge_eff < 0.5 or c.match_confidence < 0.3)]
        if weak:
            from scripts.lib.review_log import write_review_record

            print(f"\n🔍 人工评审：{len(weak)} 个弱匹配候选需要确认"
                  f"（badge_eff<0.5 或 confidence<0.3）", flush=True)
            for i, c in enumerate(weak, 1):
                print(f"\n[{i}/{len(weak)}] {c.ozon_title[:60]}", flush=True)
                print(f"    1688: {c.match_1688_title[:60]}", flush=True)
                print(f"    URL: {c.match_1688_url}", flush=True)
                if c.match_1688_images:
                    print(f"    图: {', '.join(c.match_1688_images[:3])}", flush=True)
                print(f"    confidence={c.match_confidence:.2f} "
                      f"badge_eff={c.match_badge_eff:.2f}", flush=True)
                try:
                    ans = input("    接受该货源？[y/N/a=全部接受/s=跳过] ").strip().lower()
                except (EOFError, KeyboardInterrupt):
                    # 自动化/非交互模式：EOF 视为跳过（按自动规则处理），不崩溃
                    print("\n    非交互模式，按自动规则处理（跳过）", flush=True)
                    ans = "s"
                decision = ""
                if ans in ("a", "all"):
                    decision = "approved"
                    c.review_decision = "approved"
                    for c2 in weak[i:]:
                        c2.review_decision = "approved"
                    print(f"    ✅ 已接受（含剩余 {len(weak) - i} 个）", flush=True)
                elif ans in ("y", "yes", ""):
                    decision = "approved"
                    c.review_decision = "approved"
                elif ans in ("n", "no"):
                    decision = "agent_reject"
                    c.review_decision = "agent_reject"
                else:
                    print("    跳过（按自动规则处理）", flush=True)
                if decision:
                    write_review_record({
                        "task_id": "",
                        "product_id": c.ozon_product_id,
                        "ozon_title": c.ozon_title,
                        "match_title": c.match_1688_title,
                        "match_url": c.match_1688_url,
                        "confidence": c.match_confidence,
                        "badge_eff": c.match_badge_eff,
                        "score": 0.0,
                        "reject_reason": "" if decision == "approved"
                        else "agent_review_reject",
                        "decision": decision,
                        "image_urls": list(c.match_1688_images or []),
                    })
                if ans in ("a", "all"):
                    break

    # 选中+货源结果导出（仅当请求导出时追加一份 _matched 文件）
    if args.export in ("csv", "both"):
        base = args.output or "data/discovery/discover_export.csv"
        matched_csv = base.replace(".csv", "_matched.csv") if base.endswith(".csv") \
            else "data/discovery/discover_matched.csv"
        csv_path = export_to_csv(selected, matched_csv)
        print(f"📄 CSV 已导出（选中+货源）: {csv_path}")

    # ── 自动生成结构性分析文档（MD+JSON，供 Agent/用户直接汇报）──
    try:
        from scripts.lib.ozon_discovery import export_analysis_report
        _report = export_analysis_report(selected)
        if _report:
            print(f"📄 分析文档已生成: {_report['md']}")
            print(f"📄 结构化 JSON: {_report['json']}")
    except Exception as exc:
        import logging
        _logger = logging.getLogger(__name__)
        _logger.warning("分析文档生成失败（不影响选品主流程）: %s", exc)

    # ── auto-submit ──
    if args.auto_submit:
        # D3 L3: 人工评审拒绝的候选（review_decision=agent_reject）绝不提交
        to_submit = [c for c in selected
                     if c.status == "profitable" and c.match_1688_url
                     and c.review_decision != "agent_reject"]
        if not to_submit:
            print("\n⚠️ 没有符合条件的 profitable 产品可提交")
            return 0
        print(f"\n🚀 提交 {len(to_submit)} 个产品到 Worker...", flush=True)
        try:
            confirm = input("确认提交？(y/N) ")
        except (EOFError, KeyboardInterrupt):
            # 自动化/非交互模式：EOF 视为取消（提交是高风险操作，默认不提交）
            print("\n已取消（非交互模式不自动确认提交）")
            return 0
        if confirm.lower() != 'y':
            print("已取消")
            return 0
        try:
            from scripts.cloud_probe import (
                build_envelope_from_discovery,
                submit_envelope,
            )
        except ModuleNotFoundError as _e:
            # PR-3: 精确归因 — 缺依赖 vs 缺模块
            _ename = getattr(_e, "name", "") or ""
            if _ename and _ename not in ("scripts.cloud_probe", "cloud_probe"):
                print(f"❌ 缺少依赖模块 '{_ename}'。请运行: pip install -r requirements.txt", flush=True)
            else:
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

        def _submit_one(c):
            """worker 线程执行：构建信封 → 提交 → 返回 (c, task_id, state)。

            state: ok / skip（无 1688 URL）/ error 描述；异常内部捕获不中断批次。
            """
            try:
                envelope = build_envelope_from_discovery(c, store_config, store_id=store_id)
                if not envelope:
                    return c, "", "skip"
                # P1-4 --notify: 顶层透传，Worker 收到 payload.notify 后推 webhook
                if getattr(args, "notify", False):
                    envelope["notify"] = True
                result = submit_envelope(envelope)
                return c, result.get("task_id", ""), "ok"
            except Exception as e:
                return c, "", str(e)

        from concurrent.futures import ThreadPoolExecutor, as_completed
        submitted_task_ids: list[str] = []
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(_submit_one, c) for c in to_submit]
            for fut in as_completed(futures):
                c, task_id, state = fut.result()
                if state == "skip":
                    print(f"  ✗ 跳过（无 1688 URL）: {c.ozon_title[:40]}")
                elif state == "ok":
                    submitted_task_ids.append(task_id)
                    print(f"  ✓ 已提交: {c.ozon_title[:40]} → task_id={task_id}")
                else:
                    print(f"  ✗ 提交失败: {c.ozon_title[:40]} — {state}")

    print(f"\n📁 选品日志已缓存: {DISCOVERY_CACHE_DIR}/")
    return 0


# ═══════════════════════════════════════════════════════════════════════════
# Sentry 错误上报（v0.35）— 参考 worker/src/utils/sentry_setup.py 模式，内联实现。
# DSN 未设置 / sentry-sdk 未安装 / 测试进程 → 全部静默 no-op，绝不影响任何命令行为。
# ═══════════════════════════════════════════════════════════════════════════


def _read_skill_version() -> str:
    """读取本地 skill/VERSION（与 updater.get_local_version() 同源；读不到回退 0.0.0）。"""
    try:
        v = (Path(__file__).resolve().parent.parent / "VERSION").read_text(encoding="utf-8").strip()
        return v or "0.0.0"
    except Exception:
        return "0.0.0"


def _is_sentry_test_process() -> bool:
    """测试进程（sys.argv[0] 含 test_ / pytest / PYTEST_CURRENT_TEST）跳过上报，避免测试噪音污染监测。"""
    script = sys.argv[0] if sys.argv else ""
    return (
        "test_" in script
        or script.endswith(("pytest", "py.test"))
        or bool(os.environ.get("PYTEST_CURRENT_TEST"))
    )


def _init_sentry() -> bool:
    """初始化 Sentry SDK（environment="skill"，release=本地 VERSION）。返回是否启用。

    DSN: settings.json `sentry_dsn` → 内置默认（v0.37 起用户零配置）→ 空则禁用。
    sentry-sdk 缺失时靠 lazy import 自动降级（requirements.txt 已列为正式依赖，
    但客户机器可能未升级，绝不因此阻断命令）。测试进程跳过上报。
    """
    dsn = os.environ.get("SENTRY_DSN", "").strip()
    if not dsn:
        try:
            from scripts.lib.config_store import DEFAULT_SENTRY_DSN, get_setting
            dsn = str(get_setting("sentry_dsn", "")).strip() or DEFAULT_SENTRY_DSN
        except Exception:
            dsn = ""
    if not dsn:
        return False
    if _is_sentry_test_process():
        return False
    try:
        import sentry_sdk  # noqa: F401

        sentry_sdk.init(
            dsn=dsn,
            environment="skill",
            release=_read_skill_version(),
            traces_sample_rate=0.0,  # 仅错误事件上报，不上报性能 trace
        )
    except Exception:
        return False
    return True


def _capture_exception(exc: Exception, command: str) -> None:
    """上报命令异常到 Sentry（带非敏感 tags，绝不含 token/ak/api_key/client_id 等凭证）。

    失败静默——绝不因 Sentry 故障影响命令退出。同步 flush(1s) 确保事件送达。
    """
    try:
        import sentry_sdk  # noqa: F401

        sentry_sdk.set_tag("command", command)
        sentry_sdk.set_tag("skill_version", _read_skill_version())
        sentry_sdk.set_tag("os", platform.system())
        sentry_sdk.set_tag("platform", platform.machine())
        sentry_sdk.capture_exception(exc)
        sentry_sdk.flush(timeout=1)
    except Exception:
        pass


def main() -> int:
    # ⚠️ v0.35: Sentry 错误上报（environment="skill"）。argparse 解析之前调用——
    # DSN 未设置 / SDK 缺失 / 测试进程时静默 no-op，绝不影响 --help / argparse 报错。
    _init_sentry()

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
    gp.add_argument("--ozon-ref-url", default="", help="Ozon 竞品参考链接(抓同类目属性复用, 可选, v0.29.x)")
    gp.add_argument("--notify", action="store_true",
                    help="P1-4: 提交时 GraphInput 顶层携带 notify=True，Worker 完成推送通知")
    gp.set_defaults(func=cmd_graph)

    # image_search
    ip = sub.add_parser("image_search", help="以图搜款 — 上传图片搜索 1688 同款")
    ip.add_argument("--image", required=True, help="图片路径或 URL")
    ip.add_argument("--limit", type=int, default=10, help="返回数量")
    ip.add_argument("--sort", default="", help="排序: price_asc/price_desc/sold_desc/yx_desc")
    ip.add_argument("--source", choices=["aibuy", "ak", "cdp"], default="aibuy",
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
    fp.add_argument("--review", action="store_true",
                    help="人工评审暂停：展示全部 1688 候选，人工接受/改选/拒绝")
    fp.add_argument("--notify", action="store_true",
                    help="P1-4: 提交时 GraphInput 顶层携带 notify=True，Worker 完成推送通知")
    fp.set_defaults(func=cmd_follow)

    dp = sub.add_parser("discover", help="Ozon 选品 v2（先采集 → 表格分析 → 挑完再找货源）")
    dp.add_argument("--url", default="", help="Ozon 页面 URL（搜索页/类目页等，直接采集该页）")
    dp.add_argument("--keyword", default="", help="搜索关键词（默认走中国站 highlight 页内搜索；--local 走主站 /search/?text=）")
    dp.add_argument("--local", action="store_true", help="主站搜索（默认中国站）")
    dp.add_argument("--china", action="store_true", help=argparse.SUPPRESS)
    dp.add_argument("--max-products", type=int, default=50, help="最多采集产品数（默认 50）")
    dp.add_argument("--min-margin", type=float, default=15.0, help="最低利润率%%")
    dp.add_argument("--max-sellers", type=int, default=10, help="最大跟卖人数（兼容保留，v2 中不硬过滤）")
    dp.add_argument("--fx-rate", type=float, default=None,
                    help="RUB→CNY 汇率（默认取店铺 fx_rate → settings fx_rate → 0.075）")
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
    dp.add_argument("--fission", action="store_true",
                    help="裂变选品（v3）：种子商品 → 竞品卖家 → 店铺产品 BFS 扩散")
    dp.add_argument("--max-depth", type=int, default=2, help="裂变深度（默认 2；>3 需 --allow-depth-3）")
    dp.add_argument("--allow-depth-3", action="store_true", help="允许裂变深度 3（指数爆炸，需谨慎）")
    dp.add_argument("--max-total-products", type=int, default=300, help="裂变候选总量上限（默认 300）")
    dp.add_argument("--time-budget", type=float, default=600.0, help="裂变时间预算（秒，默认 600）")
    dp.add_argument("--max-sellers-per-product", type=int, default=20, help="每商品展开的卖家数（默认 20）")
    dp.add_argument("--max-products-per-seller", type=int, default=15, help="每卖家采集的产品数（默认 15）")
    dp.add_argument("--non-interactive", action="store_true", help="裂变阶段展示后不询问继续，直接跑完")
    dp.add_argument("--blue-ocean-source", choices=["csv", "queries"], default="",
                    help="蓝海增强数据源（C4 step2）: csv=本地 all-queries CSV（--blue-ocean-csv）；"
                         "queries=实时 what_to_sell 查询（需 --keyword + seller 登录，失败降级 CSV）")
    dp.add_argument("--blue-ocean-csv", default="",
                    help="蓝海关键词 CSV 路径（--blue-ocean-source 时；默认 /tmp/queries_all.csv）")
    dp.add_argument("--review", action="store_true",
                    help="人工评审暂停：弱匹配候选逐个确认（y/N/a=全部/s=跳过），决策写入 review_log")
    dp.add_argument("--notify", action="store_true",
                    help="P1-4: 提交时 GraphInput 顶层携带 notify=True，Worker 完成推送通知")
    dp.set_defaults(func=cmd_discover)


    # ── 自动更新 ──
    up = sub.add_parser("update", help="检查并应用 Skill 自动更新")
    up.set_defaults(func=cmd_update)

    # ── 任务查询(v0.28.5 C1)──
    q = sub.add_parser("query", help="查询 Worker 任务状态(任务 ID)")
    q.add_argument("task_id", help="submit/follow 返回的任务 ID(UUID)")
    q.add_argument("--watch", action="store_true",
                   help="P1-4: 轮询直到终态（每 10s 查一次，打印进度中间态）")
    q.add_argument("--timeout", type=int, default=900,
                   help="--watch 轮询超时秒数（默认 900）")
    q.set_defaults(func=cmd_query)

    # ── 卖家店铺分析(v0.29.x ②)──
    sel = sub.add_parser("seller", help="卖家店铺全产品运营分析(跟卖前20名卖家 → 店铺选品)")
    sel.add_argument("--seller-id", required=True, help="Ozon 卖家 ID(跟卖列表透传的 seller_id)")
    sel.add_argument("--max-products", type=int, default=60, help="采集店铺产品数上限(默认 60)")
    sel.add_argument("--max-skus", type=int, default=30, help="运营分析 SKU 数上限(默认 30, 受 what_to_sell 限速)")
    sel.set_defaults(func=cmd_seller)

    # ── what-to-sell SPA 查询(v0.33.2, C4 step1)──
    qp = sub.add_parser("queries", help="what-to-sell SPA 查询(all-queries/ozon-bestsellers/market-bestsellers)")
    qp.add_argument("--type", choices=["all-queries", "ozon-bestsellers", "market-bestsellers"],
                    default="all-queries", help="查询类型(默认 all-queries)")
    qp.add_argument("--keyword", default="", help="all-queries: 搜索关键词(默认空=全部)")
    qp.add_argument("--sku", default="", help="ozon-bestsellers: 按 SKU 过滤(默认空=全榜)")
    qp.add_argument("--category-id", default="", help="market-bestsellers: 类目 ID 过滤")
    qp.add_argument("--price-min", type=float, default=None, help="market-bestsellers: 价格下限 RUB")
    qp.add_argument("--price-max", type=float, default=None, help="market-bestsellers: 价格上限 RUB")
    qp.add_argument("--export", choices=["csv", "json"], default="csv", help="导出格式(默认 csv)")
    qp.add_argument("--output", default="", help="输出文件路径(默认打印到 stdout)")
    qp.set_defaults(func=cmd_queries)

    # ── 磁盘清理(N3 profile 缓存 + N7 垃圾文件清扫)──
    clp = sub.add_parser("cleanup", help="磁盘清理：profile 缓存 / 磁盘缓存 / 临时孤儿文件 / 过期结果文件")
    clp.add_argument("--profile-cache", action="store_true",
                     help="清理 Chrome profile 可再生缓存目录（登录态保留；Chrome 运行时跳过）")
    clp.add_argument("--cache", action="store_true", help="清理磁盘缓存（全部命名空间）")
    clp.add_argument("--temp", action="store_true", help="清理 .json.tmp 孤儿文件 + 旧任务/会话文件")
    clp.add_argument("--old-results", action="store_true", help="清理过期结果/日志文件（配合 --days）")
    clp.add_argument("--days", type=int, default=30, help="--old-results 保留天数（默认 30）")
    clp.add_argument("--dry-run", action="store_true", help="只预览将删除内容，不实际删除")
    clp.add_argument("--all", action="store_true", help="执行全部清理项")
    clp.set_defaults(func=cmd_cleanup)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return 0

    # ⚠️ PR-3: 顶层 runtime preflight — Python 版本 + 核心依赖探测。
    # 在 _silent_update_check 之前（update check 本身也 import 依赖），缺则立即退出。
    # ⚠️ PR-A (v0.31): 升级为 runtime_probe 自动发现——当前解释器非 3.12 时
    # 扫描 PATH 找 3.12/3.13 并 os.execve 无感切换（re-exec 后不会回到这里）。
    if args.command not in ("update", "set_token", "set_ak", "set_store"):
        _ok, _msg = _preflight_runtime()
        if not _ok:
            print(f"\n❌ {_msg}", flush=True)
            return 1

    # ⚠️ 每次命令静默检查更新（后台不阻塞，失败静默；update 命令本身跳过）
    _silent_update_check(args.command)

    # ⚠️ v0.29.x: 工具 Chrome 改为「独立 profile + 常驻」(不再命令出口关闭)。
    # v0.28.6 的「用完即关」被用户反馈体验不一致: 每次命令结束 Chrome 被
    # close_tool_chrome() 关闭, 用户手动开的浏览器却能保持 —— 且常驻登录态
    # 可复用, 下次命令 CDP 可用即直接使用, 无需重新启动/重新登录。
    # v0.28.4 常驻的坑(重启撞用户 Chrome 默认 profile 单实例锁)已被 v0.28.6
    # 独立 profile 消除: 工具 Chrome 用独立 profile, 与用户 Chrome 互不干扰,
    # 启动必成功; 用户手动关闭常驻实例后, 下次命令检测 CDP 不可用 → 用独立
    # profile 重新启动即可, 不再有单实例锁问题。
    # close_tool_chrome() 保留(不自动调用), 需要时显式执行。
    # ⚠️ v0.35: Sentry 异常上报——捕获后 re-raise（保留 traceback + 退出码 1，不吞异常）。
    # KeyboardInterrupt/SystemExit 是 BaseException 子类，不会被 Exception 捕获 → 自然透传。
    try:
        return args.func(args)
    except Exception as exc:
        _capture_exception(exc, getattr(args, "command", "unknown"))
        raise


def _preflight_runtime() -> tuple[bool, str]:
    """PR-3 + PR-A: 顶层 runtime preflight — 自动发现可用解释器 + 核心依赖探测。

    返回 (ok, message)。流程：
    1. 源码开发模式（scripts/lib 下无编译产物）→ 跳过版本门（纯 Python 任何 ≥3.12 都能跑）
    2. 当前解释器 ≥3.12 → 直接走依赖检查（零开销）
    3. 当前解释器 <3.12 → resolve_python() 扫 PATH 找 3.12/3.13：
       - 找到 → os.execve 无感切换（永不返回）
       - 没有 → 报版本错误（含 SKILL_PYTHON 提示）
    4. 依赖探测：缺 requests/websocket/PIL → 精确 pip 指引
    """
    import sys as _sys

    from scripts.runtime_probe import re_exec_if_needed, resolve_python

    # ⚠️ v0.31: 源码模式跳过版本门——本仓库脚本目录无 .so 时任何解释器均可运行
    #（纯 Python 明文），版本门只约束 dist 分发（.so ABI 绑定 cpython-312）。
    _lib_dir = Path(__file__).resolve().parent / "lib"
    _has_native = any(_lib_dir.glob("_native")) or any(_lib_dir.glob("*.so"))
    if _has_native:
        # ⚠️ PR-A (CF-2): 双边界——3.14+ 的 cpython-314 tag 与 cpython-312 .so 不匹配，
        # 即使版本 ≥3.12 也会 import .so 崩。只接受 3.12.x。
        if _sys.version_info < (3, 12) or _sys.version_info >= (3, 13):
            python_cmd, is_current = resolve_python()
            if not is_current:
                re_exec_if_needed(python_cmd, str(Path(__file__).resolve()), list(_sys.argv[1:]))
            return (False, f"Python 版本不匹配（{_sys.version.split()[0]}），需要 3.12.x（.so ABI 绑定 cpython-312）。"
                           f"请安装 Python 3.12，或设置 SKILL_PYTHON=/path/to/python3.12 后重试。")

    missing = []
    for _mod, _pkg in (("requests", "requests"), ("websocket", "websocket-client"), ("PIL", "Pillow")):
        try:
            __import__(_mod)
        except ImportError:
            missing.append(_pkg)
    if missing:
        # ⚠️ PR-B (L3): 自动 venv — 当前解释器是 3.12 但缺依赖 → 建 data/.venv + re-exec
        # SKILL_NO_VENV=1 逃生舱：跳过自动 venv，回退手动 pip 指引
        if _sys.version_info >= (3, 12) and _sys.version_info < (3, 13) \
                and os.environ.get("SKILL_NO_VENV", "0") != "1":
            from scripts.runtime_probe import ensure_venv
            _venv_py, _venv_status = ensure_venv(_sys.executable)
            if _venv_status in ("ok", "created"):
                from scripts.runtime_probe import re_exec_if_needed
                re_exec_if_needed(_venv_py, str(Path(__file__).resolve()), list(_sys.argv[1:]))
            # venv 失败 → 落回手动提示（带 .failed 诊断）
            _fb_path = Path(__file__).resolve().parent.parent / "data" / ".venv" / ".failed"
            _fb_hint = f"\n  ⚠️ 自动 venv 失败诊断见: {_fb_path}" if _fb_path.exists() else ""
            return (False, f"缺少依赖: {', '.join(missing)}，且自动 venv 创建失败。"
                           f"请运行: pip install -r requirements.txt（或 pip install {' '.join(missing)}）"
                           f"{_fb_hint}")
        return (False, f"缺少依赖: {', '.join(missing)}。请运行: "
                       f"pip install -r requirements.txt（或 pip install {' '.join(missing)}）")
    return (True, "")


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


def cmd_seller(args: argparse.Namespace) -> int:
    """卖家店铺全产品运营分析(v0.29.x): 采集店铺产品 → what_to_sell 逐 SKU 拉运营数据。"""
    from scripts.lib.ozon_discovery import fetch_seller_analysis
    import json

    result = fetch_seller_analysis(
        seller_id=args.seller_id,
        max_products=args.max_products,
        max_skus=args.max_skus,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_queries(args: argparse.Namespace) -> int:
    """what-to-sell SPA 三页查询(v0.33.2, C4 step1): all-queries / ozon-bestsellers / market-bestsellers。

    通过 CDP 复用已登录 seller.ozon.ru tab 页面内 fetch 真实端点（见
    .omo/evidence/sentry-attribute-fixes/task-5-c4a.endpoints.json），
    输出 CSV(utf-8-sig, Excel 兼容) 或 JSON。
    """
    import csv
    import io

    from scripts.lib.cdp_client import CdpConnection
    from scripts.lib import ozon_seller_analytics as osa

    if not args.output:
        try:
            from scripts.lib.chrome_launcher import ensure_chrome_cdp
            ensure_chrome_cdp()
        except Exception:
            pass

    rows: list[dict] = []
    try:
        with CdpConnection() as cdp:
            if not osa.check_seller_login(cdp):
                print("未登录 seller.ozon.ru", flush=True)
                return 0
            if args.type == "all-queries":
                rows = osa.fetch_all_queries(cdp, keyword=args.keyword or None)
            elif args.type == "ozon-bestsellers":
                rows = osa.fetch_ozon_bestsellers(cdp, sku_or_id=args.sku or None)
            else:
                rows = osa.fetch_market_bestsellers(
                    cdp,
                    category_id=args.category_id or None,
                    price_rub_min=args.price_min,
                    price_rub_max=args.price_max,
                )
    except Exception as exc:
        print(f"❌ queries 执行失败: {exc}", flush=True)
        return 1

    # C5 todo8: 采集成功后 fire-and-forget 上报 worker（失败/无 token 均不阻断主流程）
    if rows:
        try:
            from scripts.lib import analytics_upload
            kind = "queries" if args.type == "all-queries" else args.type
            analytics_upload.upload_in_background(kind, rows)
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("queries 上报触发失败: %s", exc)

    if not rows:
        print("（无数据）", flush=True)
        return 0

    if args.export == "json":
        text = json.dumps(rows, ensure_ascii=False, indent=2)
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(text)
        else:
            print(text, flush=True)
    else:
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
        text = buf.getvalue()
        if args.output:
            with open(args.output, "w", encoding="utf-8-sig", newline="") as f:
                f.write(text)
        else:
            print(text, end="", flush=True)
    return 0


def cmd_update(args: argparse.Namespace) -> int:
    """检查并应用 Skill 自动更新（从 COS manifest 下载全覆盖）。"""
    from scripts.lib.updater import run_update_command
    return run_update_command()


def _query_watch_progress(r: dict) -> None:
    status = r.get("status", "unknown")
    pct = ""
    prog = r.get("progress")
    if isinstance(prog, dict) and prog.get("percent"):
        pct = f" ({int(prog['percent'])}%)"
    print(f"⏳ {status}{pct}...", flush=True)


def _print_query_result(task_id: str, r: dict) -> None:
    status = r.get("status", "unknown")
    print(f"任务 {task_id}: {status}")
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
    elif status == "timeout":
        print(f"  ⏳ 轮询超时（--timeout {r.get('timeout_seconds')}s 内未到终态），可稍后 query 查询")
    else:
        print("  ⏳ 处理中...")


def cmd_query(args: argparse.Namespace) -> int:
    """查询 Worker 任务状态（v0.28.5 C1: agent 不再只能盲等, 可主动查进度）。

    --watch（P1-4）: 首次查询非终态后每 10s 轮询（poll_task_status）直到终态，
    中间态打印 "⏳ running (35%)..."，终态后复用 _print_query_result 输出明细。

    返回: completed → 0; 终态(failed/cancelled/not_found) → 0(信息完整);
          非终态(processing/queued) → 0(展示进度); 查询失败 → 1。
    """
    from scripts.cloud_probe import check_task_status, poll_task_status

    if getattr(args, "watch", False):
        r = poll_task_status(
            args.task_id,
            timeout=getattr(args, "timeout", 900),
            on_status=_query_watch_progress,
        )
        _print_query_result(args.task_id, r)
        return 0

    r = check_task_status(args.task_id)
    _print_query_result(args.task_id, r)
    return 0


# ═══════════════════════════════════════════════════════════════════════════
# Cleanup Commands（N3 profile 缓存 + N7 垃圾文件清扫）
# ═══════════════════════════════════════════════════════════════════════════

# 可再生 Chrome profile 子目录白名单（相对 profile_dir，即 --user-data-dir）。
# 全部可安全重建（缓存/临时模型/组件）；登录态文件（Default/Cookies、Local
# Storage、Login Data、Preferences 等）绝不在名单内，清理后保留。实测合计
# 4.9GB 级膨胀主源（Default/Cache 78M + Code Cache 37M + Service Worker 16M
# + optimization_guide_model_store 43M + component_crx_cache 29M 等）。
_PROFILE_CACHE_DIRS: tuple[str, ...] = (
    "Default/Cache",
    "Default/Code Cache",
    "Default/Service Worker",
    "Default/GPUCache",
    "optimization_guide_model_store",
    "component_crx_cache",
    "WasmTtsEngine",
    "GraphiteDawnCache",
)


def _dir_size(path: Path) -> int:
    """递归统计目录占用字节数（只读统计，失败降级为已扫部分）。"""
    total = 0
    try:
        for f in path.rglob("*"):
            if f.is_file():
                try:
                    total += f.stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


def _cleanup_profile_cache(profile_dir: str, dry_run: bool = False) -> dict[str, int]:
    """清理 Chrome profile 中可再生的缓存目录（保留登录态）。

    - 只删 ``_PROFILE_CACHE_DIRS`` 白名单目录（safe_rmtree，fail-open）
    - ``Default/(Cookies|Local Storage|Login Data|Preferences)`` 登录态绝不动
    - Chrome 进程运行时跳过（缓存文件被进程锁定，硬删会损坏）→ 返回
      ``skipped_chrome_running=1``，调用方打印 warning

    Returns: {removed, bytes_freed, errors, skipped_chrome_running}
    """
    from scripts.lib.chrome_launcher import _find_chrome_processes
    from scripts.lib.utils import safe_rmtree

    result = {"removed": 0, "bytes_freed": 0, "errors": 0, "skipped_chrome_running": 0}
    if _find_chrome_processes():
        print("⚠️ Chrome 正在运行，跳过 profile 缓存清理（缓存文件被进程锁定）。"
              "请先关闭 Chrome 再运行 --profile-cache", flush=True)
        result["skipped_chrome_running"] = 1
        return result

    root = Path(profile_dir)
    for rel in _PROFILE_CACHE_DIRS:
        target = root / rel
        if not target.is_dir():
            continue
        try:
            size = _dir_size(target)
            if dry_run:
                print(f"  [dry-run] 将删除: {target}", flush=True)
            elif not safe_rmtree(target):
                result["errors"] += 1
                print(f"  ⚠️ 删除失败: {target}", flush=True)
                continue
            result["removed"] += 1
            result["bytes_freed"] += size
        except Exception as exc:
            result["errors"] += 1
            print(f"  ⚠️ 清理失败 {target}: {exc}", flush=True)
    return result


def _cleanup_temp_files(dry_run: bool = False,
                        scan_dirs: list[Path] | None = None) -> dict[str, int]:
    """清理 .json.tmp 孤儿文件（cache_set 临时文件双败残留）+ 旧任务/会话文件。

    Returns: {removed, bytes_freed, errors, old_files}（old_files 为
    task_paths.cleanup_old_files 返回的 {deleted, bytes_freed, errors}）。
    """
    from scripts._const import CACHE_DIR, DATA_DIR
    from scripts.lib.utils import safe_unlink

    if scan_dirs is None:
        scan_dirs = [CACHE_DIR, DATA_DIR]
    result = {"removed": 0, "bytes_freed": 0, "errors": 0}
    seen: set[Path] = set()
    for scan_dir in scan_dirs:
        if not scan_dir.is_dir():
            continue
        try:
            for f in scan_dir.rglob("*.json.tmp"):
                if f in seen:
                    continue
                seen.add(f)
                try:
                    size = f.stat().st_size
                    if dry_run:
                        print(f"  [dry-run] 将删除: {f}", flush=True)
                    elif not safe_unlink(f):
                        result["errors"] += 1
                        continue
                    result["removed"] += 1
                    result["bytes_freed"] += size
                except OSError:
                    result["errors"] += 1
        except OSError as exc:
            result["errors"] += 1
            print(f"  ⚠️ 扫描失败 {scan_dir}: {exc}", flush=True)

    from scripts.lib import task_paths
    old = task_paths.cleanup_old_files(max_age_days=7, dry_run=dry_run)
    result["old_files"] = old
    result["errors"] += old.get("errors", 0)
    return result


def _cleanup_old_results(days: int, dry_run: bool = False,
                         scan_dirs: list[Path] | None = None) -> dict[str, int]:
    """清扫 batch_results / discovery / logs 中早于 ``days`` 天的旧文件（保留近期）。

    Returns: {removed, bytes_freed, errors}
    """
    from scripts._const import DATA_DIR
    from scripts.lib.utils import safe_unlink

    if scan_dirs is None:
        scan_dirs = [DATA_DIR / "batch_results", DATA_DIR / "discovery", DATA_DIR / "logs"]
    cutoff = time.time() - (days * 86400)
    result = {"removed": 0, "bytes_freed": 0, "errors": 0}
    for scan_dir in scan_dirs:
        if not scan_dir.is_dir():
            continue
        try:
            for f in scan_dir.iterdir():
                if not f.is_file():
                    continue
                try:
                    if f.stat().st_mtime >= cutoff:
                        continue
                    size = f.stat().st_size
                    if dry_run:
                        print(f"  [dry-run] 将删除: {f}", flush=True)
                    elif not safe_unlink(f):
                        result["errors"] += 1
                        continue
                    result["removed"] += 1
                    result["bytes_freed"] += size
                except OSError:
                    result["errors"] += 1
        except OSError as exc:
            result["errors"] += 1
            print(f"  ⚠️ 扫描失败 {scan_dir}: {exc}", flush=True)
    return result


def _cleanup_cache(dry_run: bool = False) -> dict[str, int]:
    """清理磁盘缓存（全部命名空间）。dry_run 只统计不删除。"""
    from scripts.lib import cache as cache_mod

    if dry_run:
        stats = cache_mod.cache_stats()
        total = sum(s.get("total", 0) for s in stats.values())
        print(f"  [dry-run] 缓存文件: {total} 个", flush=True)
        return {"removed": 0, "bytes_freed": 0, "errors": 0, "files": total}
    removed = cache_mod.cache_clear(None)
    return {"removed": removed, "bytes_freed": 0, "errors": 0}


def cmd_cleanup(args: argparse.Namespace) -> int:
    """磁盘清理：Chrome profile 缓存 / 磁盘缓存 / 临时孤儿文件 / 过期结果文件。

    N3 (P1-3) + N7 (P2-7): 一键控制 4.9GB profile 膨胀 + 数百个垃圾文件堆积。
    - ``--profile-cache``: 只删可再生缓存目录（登录态保留；Chrome 运行时跳过）
    - ``--cache``: 磁盘缓存全清（cache_clear(None)）
    - ``--temp``: .json.tmp 孤儿文件 + 旧任务/会话文件
    - ``--old-results --days N``: 清扫 batch_results/discovery/logs 中 N 天前文件
    - ``--dry-run``: 只预览不删除；``--all``: 以上全部
    任何删除失败仅 warning 不崩溃（safe_unlink/safe_rmtree fail-open）。
    """
    actions: list[str] = []
    if getattr(args, "all", False):
        actions = ["profile_cache", "cache", "temp", "old_results"]
    else:
        if args.profile_cache:
            actions.append("profile_cache")
        if args.cache:
            actions.append("cache")
        if args.temp:
            actions.append("temp")
        if args.old_results:
            actions.append("old_results")
    if not actions:
        print("未指定清理项。可用: --profile-cache / --cache / --temp / --old-results / --all"
              "（--dry-run 预演）", flush=True)
        return 0

    dry_run = bool(getattr(args, "dry_run", False))
    summary: dict[str, dict] = {}
    if "profile_cache" in actions:
        print("🧹 清理 Chrome profile 缓存:", flush=True)
        summary["profile_cache"] = _cleanup_profile_cache(_chrome_profile_dir(), dry_run=dry_run)
    if "cache" in actions:
        print("🧹 清理磁盘缓存:", flush=True)
        summary["cache"] = _cleanup_cache(dry_run=dry_run)
    if "temp" in actions:
        print("🧹 清理临时孤儿文件:", flush=True)
        summary["temp"] = _cleanup_temp_files(dry_run=dry_run)
    if "old_results" in actions:
        days = int(getattr(args, "days", 30) or 30)
        print(f"🧹 清理 {days} 天前的结果/日志文件:", flush=True)
        summary["old_results"] = _cleanup_old_results(days, dry_run=dry_run)

    # 汇总
    print("\n" + "=" * 52, flush=True)
    for name, r in summary.items():
        if name == "profile_cache":
            note = "（Chrome 运行中已跳过）" if r.get("skipped_chrome_running") else ""
            print(f"  profile-cache: 删除 {r['removed']} 项 / 释放 "
                  f"{r['bytes_freed'] // (1024 * 1024)} MB / 错误 {r['errors']}{note}", flush=True)
        elif name == "cache":
            print(f"  cache: 清理 {r['removed']} 个缓存文件 / 错误 {r['errors']}", flush=True)
        elif name == "temp":
            old = r.get("old_files") or {}
            print(f"  temp: 删除 {r['removed']} 个 .json.tmp / 旧任务文件 "
                  f"{old.get('deleted', 0)} / 错误 {r['errors']}", flush=True)
        elif name == "old_results":
            print(f"  old-results: 删除 {r['removed']} 个文件 / 释放 "
                  f"{r['bytes_freed'] // (1024 * 1024)} MB / 错误 {r['errors']}", flush=True)
    if dry_run:
        print("  （--dry-run: 仅预览，未实际删除）", flush=True)
    print("=" * 52, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
