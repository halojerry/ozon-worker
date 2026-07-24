#!/usr/bin/env python3
"""pounding-ozon-probe CLI — 1688 search + CDP probe + GraphInput assembly.

Commands:
check                    诊断前置条件（Chrome / 凭证 / Worker / Ozon API）
search <关键词>          1688 搜索商品
probe <URL>              CDP 浏览器探针抓取商品
graph <URL|item_id>      组装 GraphInput envelope（不上架）
follow --ozon-url <URL>  跟卖 Ozon 商品
image_search --image <URL>  以图搜款
get_ak                   自动获取 1688 AK
set_store                配置 Ozon 店铺
list_stores              列出所有店铺
set_token                设置 MXOU_TOKEN
set_ak                   设置 1688 AK
batch_test               批量处理 URL 列表
"""

from __future__ import annotations

import argparse, json, sys
from pathlib import Path

# Ensure scripts/ is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _out(obj: dict) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2), flush=True)


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
    from scripts.lib.config_store import list_stores, get_store
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
    _out({"success": True, "message": "MXOU_TOKEN 已保存到 settings.json"})
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
    from scripts.cloud_probe import build_graph_envelope_with_retry, ProductValidationError

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
    _out({"summary": summary, "envelope": graph})
    return 0


def cmd_image_search(args) -> int:
    """以图搜款: 上传图片搜索 1688 同款/相似商品."""
    from scripts.lib.ak_1688_client import search_by_image

    try:
        results = search_by_image(
            image_path=args.image if not args.image.startswith("http") else "",
            image_url=args.image if args.image.startswith("http") else "",
            page_size=args.limit,
            sort_type=args.sort or "",
        )
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
    from scripts.lib.config_store import set_ali_1688_ak

    result = get_ak_via_browser(timeout=args.timeout)

    # Auto-save AK if obtained successfully
    if result.get("success") and result.get("ak"):
        set_ali_1688_ak(result["ak"])
        result["saved_to"] = "settings.json"

    _out(result)
    return 0 if result.get("success") else 1


def cmd_check(args) -> int:
    """诊断前置条件：浏览器 / CDP / 1688 / Ozon / 凭证 / Worker"""
    from scripts.lib.config_store import check_config, list_stores, get_mxou_token, get_ali_1688_ak
    from scripts.capabilities.browser_probe.service import _candidate_browser_paths
    import requests as req
    import os as _os
    import shutil

    config = check_config()
    all_ok = True

    def _ok(b: bool) -> str:
        return "✅" if b else "❌"

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
        print(f"  ❌ 未检测到可用浏览器")
        print(f"  → 请安装 Google Chrome: https://www.google.com/chrome/")
        all_ok = False
        return 0 if all_ok else 1

    # ═══════════════════════════════════════════
    # 2. CDP 远程调试检查（自动启动 Chrome）
    # ═══════════════════════════════════════════
    print(f"\n🔗 CDP 远程调试 (127.0.0.1:9222):")

    try:
        from scripts.lib.chrome_launcher import ensure_chrome_cdp, get_chrome_info
        info = get_chrome_info()
        if info["chrome_found"]:
            print(f"  🌐 Chrome: {info['chrome_path'].split('/')[-1]}")
        if not info["cdp_available"]:
            print(f"  ⏳ CDP 未运行，正在自动启动 Chrome...")
            ok, msg = ensure_chrome_cdp(auto_restart=True)
            if ok:
                print(f"  ✅ {msg}")
                session_ok = True
            else:
                print(f"  ❌ {msg}")
                session_ok = False
        elif not info["has_remote_allow_origins"]:
            print(f"  ⚠️ CDP 运行中但缺少 --remote-allow-origins，正在重启 Chrome...")
            ok, msg = ensure_chrome_cdp(auto_restart=True)
            if ok:
                print(f"  ✅ {msg}")
                session_ok = True
            else:
                print(f"  ❌ {msg}")
                session_ok = False
        else:
            session_ok = True
            print(f"  ✅ CDP 已启动")
    except ImportError:
        cdp = config.get("cdp", {})
        session_ok = cdp.get("session_available", False) or cdp.get("cdp_running", False)
        print(f"  {_ok(session_ok)} CDP 已启动")
        if not session_ok:
            print(f"  ⚠️ 自动启动模块不可用，请手动启动 Chrome:")
            print(f"  macOS: /Applications/Google\\ Chrome.app/Contents/MacOS/Google\\ Chrome \\")
            print(f"    --remote-debugging-port=9222 --remote-allow-origins='*'")

    cdp = config.get("cdp", {})
    login_ok = not cdp.get("login_required", True)

    # ═══════════════════════════════════════════
    # 3. 1688 CDP 连通检查 + 登录检测
    # ═══════════════════════════════════════════
    alibaba_cdp_ok = False
    if session_ok:
        print(f"\n  🔗 1688 CDP 连通检查...")
        try:
            import websocket as _ws
            import time as _time

            _1688_ws_url = None
            _tabs = req.get("http://127.0.0.1:9222/json", timeout=5).json()
            for _t in _tabs:
                if _t.get("type") == "page" and "1688.com" in _t.get("url", ""):
                    _1688_ws_url = _t.get("webSocketDebuggerUrl", "")
                    break

            if _1688_ws_url:
                ws = _ws.create_connection(_1688_ws_url, timeout=10)
                ws.send(json.dumps({"id":1,"method":"Runtime.evaluate",
                    "params":{"expression":"!!location.href && location.href.indexOf('1688.com')>=0 && location.href.indexOf('login.1688.com')<0","returnByValue":True}}))
                ws.settimeout(8)
                for _ in range(15):
                    try:
                        m = json.loads(ws.recv())
                        if m.get("id") == 1:
                            alibaba_cdp_ok = bool(m.get("result",{}).get("result",{}).get("value", False))
                            break
                    except _ws.WebSocketTimeoutException:
                        continue
                    except Exception:
                        break
                if alibaba_cdp_ok:
                    ws.send(json.dumps({"id":2,"method":"Runtime.evaluate","params":{
                        "expression":"document.cookie.match(/cookie2=|__cn_logon__=/) ? 'LOGGED_IN' : 'NOT_LOGGED_IN'",
                        "returnByValue":True}}))
                    for _ in range(15):
                        try:
                            m = json.loads(ws.recv())
                            if m.get("id") == 2:
                                val = m.get("result",{}).get("result",{}).get("value", "NOT_LOGGED_IN")
                                login_ok = val == "LOGGED_IN"
                                break
                        except _ws.WebSocketTimeoutException:
                            continue
                        except Exception:
                            break
                ws.close()
            else:
                print(f"  ⚠️ 未找到已打开的 1688 标签页")
        except Exception as _dbg_e:
            print(f"  ⚠️ 1688 CDP 异常: {_dbg_e}")
        print(f"  {_ok(alibaba_cdp_ok)} 1688 页面可访问 (仅影响 1688 URL)")
    else:
        print(f"\n  🔗 1688 CDP: ⏭️ 跳过（CDP 未启动）")

    if session_ok and alibaba_cdp_ok:
        print(f"  {_ok(login_ok)} 1688 已登录 (影响 1688 抓取)")
        if not login_ok:
            print(f"  → 在浏览器中打开 https://login.1688.com/ 登录")
            all_ok = False
    elif session_ok:
        print(f"  {_ok(login_ok)} 1688 已登录 (影响 1688 抓取)")
        if not login_ok:
            print(f"  → 在浏览器中打开 https://login.1688.com/ 登录")
            all_ok = False

    # ═══════════════════════════════════════════
    # 4. Ozon CDP 连通检查
    # ═══════════════════════════════════════════
    ozon_cdp_ok = False
    if session_ok:
        print(f"\n  🔗 Ozon CDP 连通检查 (DataDome)...")
        try:
            import websocket as _ws
            import time as _time
            blank = req.put("http://127.0.0.1:9222/json/new?", timeout=5)
            if blank.status_code == 200:
                tab = blank.json()
                ws = _ws.create_connection(tab.get("webSocketDebuggerUrl", ""), timeout=10)
                ws.send(json.dumps({"id":1,"method":"Page.enable","params":{}}))
                ws.send(json.dumps({"id":2,"method":"Page.navigate",
                    "params":{"url":"https://www.ozon.ru/"}}))
                deadline = _time.time() + 8
                page_loaded = False
                while _time.time() < deadline:
                    try:
                        ws.settimeout(1)
                        m = json.loads(ws.recv())
                        if m.get("method") == "Page.frameStoppedLoading":
                            page_loaded = True
                            _time.sleep(0.5)
                            break
                    except _ws.WebSocketTimeoutException:
                        if page_loaded: break
                        continue
                    except Exception:
                        break
                ws.send(json.dumps({"id":3,"method":"Runtime.evaluate",
                    "params":{"expression":"location.href.indexOf('ozon.ru')>=0 && location.href.indexOf('captcha')<0","returnByValue":True}}))
                for __ in range(15):
                    try:
                        ws.settimeout(1)
                        m = json.loads(ws.recv())
                        if m.get("id") == 3:
                            ozon_cdp_ok = bool(m.get("result",{}).get("result",{}).get("value", False))
                            break
                    except _ws.WebSocketTimeoutException:
                        continue
                    except Exception:
                        break
                ws.close()
        except Exception:
            pass
        print(f"  {_ok(ozon_cdp_ok)} Ozon 可通过 DataDome")
    else:
        print(f"\n  🔗 Ozon CDP: ⏭️ 跳过（CDP 未启动）")

    if session_ok and not ozon_cdp_ok:
        print(f"  → 在浏览器中打开 https://www.ozon.ru/ 浏览任意商品即可建立信任")
        all_ok = False

    # ═══════════════════════════════════════════
    # 5. 凭证检查
    # ═══════════════════════════════════════════
    print(f"\n📋 凭证:")

    # MXOU_TOKEN
    mxou = get_mxou_token()
    print(f"  {_ok(bool(mxou))} MXOU_TOKEN")
    if not mxou:
        print(f"  → python3 scripts/cli.py set_token --token <你的token>")

    # 1688 AK
    ak = get_ali_1688_ak()
    print(f"  {_ok(bool(ak))} 1688 AK")
    if not ak:
        print(f"  → python3 scripts/cli.py get_ak  # 自动获取")

    # Ozon stores
    stores = list_stores()
    if stores:
        print(f"  {_ok(True)} Ozon 店铺: {len(stores)} 个")
        for name, cfg in stores.items():
            cid = cfg.get("client_id", "")
            print(f"    • {name}: {cid[:8]}***" if cid else f"    • {name}: (未配置)")
    else:
        print(f"  {_ok(False)} Ozon 店铺未配置")
        print(f"  → python3 scripts/cli.py set_store --name \"店铺名\" --client-id <ID> --api-key <KEY>")
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
    print(f"\n🏪 Ozon API:")
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
                print(f"  ⚠️ 网络超时（不影响后续使用）")
    else:
        print(f"  {_ok(False)} 无可用店铺配置")

    # ═══════════════════════════════════════════
    # 汇总
    # ═══════════════════════════════════════════
    print(f"\n{'='*55}")
    if all_ok:
        print("✅ 所有前置条件满足！")
        print(f"\n  python3 scripts/cli.py search <关键词>         # 1688搜索")
        print(f"  python3 scripts/cli.py graph --url <1688 URL>   # 组装信封")
        print(f"  python3 scripts/cli.py follow --ozon-url <Ozon URL>  # 跟卖")
        print(f"  python3 scripts/batch_test.py --urls-file <文件> --submit  # 批量")
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
            print(f"\n  完成后重新运行: python3 scripts/cli.py check")

    return 0 if all_ok else 1


def cmd_follow(args) -> int:
    """跟卖 Ozon 商品: Ozon URL → import-by-sku → 1688搜索 → CDP探针 → 上架"""
    from scripts.cloud_probe import follow_sell_cloud
    result = follow_sell_cloud(args.ozon_url, auto_submit=args.auto_submit, store_id=args.store or "")
    _out(result)
    return 0 if result.get("success") else 1


# ═══════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════

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
    gp.set_defaults(func=cmd_graph)

    # image_search
    ip = sub.add_parser("image_search", help="以图搜款 — 上传图片搜索 1688 同款")
    ip.add_argument("--image", required=True, help="图片路径或 URL")
    ip.add_argument("--limit", type=int, default=10, help="返回数量")
    ip.add_argument("--sort", default="", help="排序: price_asc/price_desc/sold_desc/yx_desc")
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

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
