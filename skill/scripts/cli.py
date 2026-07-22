#!/usr/bin/env python3
"""pounding-ozon-probe CLI — 1688 search + CDP probe + GraphInput assembly.

Commands:
search <关键词>          1688 搜索商品
probe <URL>              CDP 浏览器探针抓取商品
graph <URL|item_id>      组装 GraphInput envelope（不上架）
"""

from __future__ import annotations

import argparse, json, os, sys
from pathlib import Path

# Ensure scripts/ is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _out(obj: dict) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2), flush=True)


# ═══════════════════════════════════════════════════════════════════════════
# Commands
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
        )
    except ProductValidationError as e:
        _out({"error": str(e), "skipped": True, "item_id": item_id})
        return 2

    # Summary + full envelope
    env = graph.get("envelope", {})
    # 兼容三层结构: 如果 envelope 有 draft key，从中提取字段
    if isinstance(env, dict) and "draft" in env:
        draft = env.get("draft", {})
    else:
        draft = env  # 扁平结构兼容
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


def cmd_check(args) -> int:
    """诊断前置条件：浏览器 / CDP / 1688 / Ozon / 凭证 / Worker"""
    from scripts.lib.config_store import load_env_file, check_config
    from scripts.capabilities.browser_probe.service import _candidate_browser_paths
    import requests as req
    import os as _os
    import shutil

    load_env_file()
    config = check_config()
    all_ok = True

    def _ok(b: bool) -> str:
        return "✅" if b else "❌"

    # ═══════════════════════════════════════════
    # 1. 浏览器检测（仅 Chromium 内核，CDP 协议）
    # ═══════════════════════════════════════════
    print("🖥️ 浏览器检测（仅 Chromium 内核，Firefox/Safari 不支持）:")
    print("  优先级: Chrome > Edge > Chromium > 其他国产浏览器")

    # 按优先级排序：Chrome 系 > Edge > Chromium > 其他
    BROWSER_NAMES = {
        # Tier 1: Chrome 系（最稳定，首选）
        "Google Chrome": ["chrome", "google-chrome", "google-chrome-stable"],
        "Chromium": ["chromium", "chromium-browser"],
        # Tier 2: Edge（Win 预装，覆盖率最高）
        "Microsoft Edge": ["msedge", "microsoft-edge"],
        # Tier 3: 其他 Chromium 浏览器
        "Brave": ["brave", "brave-browser"],
        "Opera": ["opera"],
        "Vivaldi": ["vivaldi"],
        # Tier 4: 国产浏览器（中国用户）
        "360 浏览器": ["360chrome", "360se"],
        "QQ 浏览器": ["qqbrowser"],
        "搜狗浏览器": ["sogou", "sogou-explorer"],
        "猎豹浏览器": ["liebao"],
        "傲游浏览器": ["maxthon"],
        "豆包浏览器": ["doubao", "doubao-browser"],
    }

    found_browsers: list[tuple[str, str]] = []  # (name, path)
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

    # Deduplicate
    seen = set()
    unique_browsers = []
    for name, path in found_browsers:
        key = _os.path.realpath(path)
        if key not in seen:
            seen.add(key)
            unique_browsers.append((name, path))

    if unique_browsers:
        # 按优先级排序：Chrome > Chromium > Edge > 其他
        priority_order = {"Google Chrome": 0, "Chromium": 1, "Microsoft Edge": 2}
        unique_browsers.sort(key=lambda x: priority_order.get(x[0], 99))
        for name, path in unique_browsers:
            tier = "⭐ 首选" if name == "Google Chrome" else ""
            print(f"  ✅ {name}: {path}  {tier}")
    else:
        print(f"  ❌ 未检测到 Chromium 内核浏览器")
        print(f"  ⚠️ 注意: 仅支持 Chromium 内核浏览器（Chrome/Edge/360/QQ 等）")
        print(f"     Firefox、Safari 等不支持 CDP 协议，无法使用")
        print(f"  → 请安装 Google Chrome（最稳定）:")
        print(f"     https://www.google.com/chrome/")
        print(f"  → 或 Microsoft Edge（Win10+ 预装）:")
        print(f"     https://www.microsoft.com/edge/")
        all_ok = False
        # 无法继续 CDP 检查
        return 0 if all_ok else 1

    # ═══════════════════════════════════════════
    # 2. CDP 远程调试检查
    # ═══════════════════════════════════════════
    cdp = config.get("cdp", {})
    session_ok = cdp.get("session_available", False) or cdp.get("cdp_running", False)
    login_ok = not cdp.get("login_required", True)
    
    print(f"\n🔗 CDP 远程调试 (127.0.0.1:9222):")
    print(f"  {_ok(session_ok)} CDP 已启动")

    if not session_ok:
        print(f"\n  ⚠️ 请用以下命令启动浏览器（任选一个已安装的）:")
        for name, path in unique_browsers[:3]:
            print(f"  {path} --remote-debugging-port=9222 --remote-allow-origins='*'")
        print(f"\n  macOS 示例（Chrome）:")
        print(f"  /Applications/Google\\ Chrome.app/Contents/MacOS/Google\\ Chrome \\")
        print(f"    --remote-debugging-port=9222 --remote-allow-origins='*'")
        print(f"\n  Windows 示例（Chrome）:")
        print(f"  \"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe\" \\")
        print(f"    --remote-debugging-port=9222 --remote-allow-origins=\"*\"")

    # ═══════════════════════════════════════════
    # 3. 1688 CDP 连通检查
    # ═══════════════════════════════════════════
    alibaba_cdp_ok = False
    if session_ok:
        print(f"\n  🔗 1688 CDP 连通检查...")
        try:
            import websocket as _ws
            import time as _time
            blank = req.put("http://127.0.0.1:9222/json/new?", timeout=5)
            if blank.status_code == 200:
                tab = blank.json()
                ws = _ws.create_connection(tab.get("webSocketDebuggerUrl", ""), timeout=10)
                ws.send(json.dumps({"id":1,"method":"Page.enable","params":{}}))
                ws.send(json.dumps({"id":2,"method":"Page.navigate",
                    "params":{"url":"https://www.1688.com/"}}))
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
                    "params":{"expression":"!!location.href && location.href.indexOf('1688.com')>=0 && location.href.indexOf('login.1688.com')<0","returnByValue":True}}))
                for __ in range(15):
                    try:
                        ws.settimeout(1)
                        m = json.loads(ws.recv())
                        if m.get("id") == 3:
                            val = m.get("result",{}).get("result",{}).get("value", False)
                            alibaba_cdp_ok = bool(val)
                            break
                    except _ws.WebSocketTimeoutException:
                        continue
                    except Exception:
                        break
                ws.close()
        except Exception:
            pass
        print(f"  {_ok(alibaba_cdp_ok)} 1688 页面可访问 (仅影响 1688 URL)")
    else:
        print(f"\n  🔗 1688 CDP: ⏭️ 跳过（CDP 未启动）")

    # 1688 登录检查
    if session_ok:
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
    # 5. 凭证 + Worker + Ozon API
    # ═══════════════════════════════════════════
    print(f"\n📋 凭证:")
    missing = config.get("missing", [])
    by_tier = config.get("by_tier", {})
    for tier, keys in by_tier.items():
        for k, present in keys.items():
            label = k
            print(f"  {_ok(present)} {label}")
    if missing:
        print(f"  ⚠️ 缺失: {', '.join(missing)}")
        all_ok = False
    if config.get("user_action"):
        # Only show user_action if there are actual missing keys
        if missing:
            print(f"\n{config['user_action']}")

    worker_url = _os.environ.get("WORKER_URL", "http://localhost:8080").rstrip("/")
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

    cid = _os.environ.get("OZON_CLIENT_ID", "")
    akey = _os.environ.get("OZON_API_KEY", "")
    print(f"\n🏪 Ozon API:")
    if cid and akey:
        print(f"  {_ok(True)} Client ID: {cid[:8]}***")
        try:
            resp = req.post("https://api-seller.ozon.ru/v1/product/info/description",
                headers={"Client-Id": cid, "Api-Key": akey, "Content-Type": "application/json"},
                json={"product_id": 1}, timeout=10)
            if resp.status_code in (401, 403):
                print(f"  {_ok(False)} API 认证失败，请检查 OZON_CLIENT_ID / OZON_API_KEY")
                all_ok = False
            else:
                print(f"  {_ok(True)} API 认证通过")
        except Exception:
            print(f"  ⚠️ 网络超时（不影响后续使用）")
    else:
        print(f"  {_ok(False)} OZON_CLIENT_ID 或 OZON_API_KEY 未配置")
        all_ok = False

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

    return 0 if all_ok else 1


def cmd_follow(args) -> int:
    """跟卖 Ozon 商品: Ozon URL → import-by-sku → 1688搜索 → CDP探针 → 上架"""
    from scripts.cloud_probe import follow_sell_cloud
    result = follow_sell_cloud(args.ozon_url, auto_submit=args.auto_submit)
    _out(result)
    return 0 if result.get("success") else 1


# ═══════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════

def main() -> int:
    parser = argparse.ArgumentParser(description="pounding-ozon-probe — 1688 数据采集 + GraphInput 组装")
    sub = parser.add_subparsers(dest="command", help="命令")

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
    gp.set_defaults(func=cmd_graph)

    # check (诊断)
    cp = sub.add_parser("check", help="诊断前置条件（Chrome / 凭证 / Worker / Ozon API）")
    cp.set_defaults(func=cmd_check)

    # follow (跟卖)
    fp = sub.add_parser("follow", help="跟卖 Ozon 商品（Ozon URL → 1688找同款 → 上架）")
    fp.add_argument("--ozon-url", required=True, help="Ozon 商品页 URL")
    fp.add_argument("--auto-submit", action="store_true", help="自动提交到 Worker")
    fp.set_defaults(func=cmd_follow)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
