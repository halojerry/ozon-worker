#!/usr/bin/env python3
"""
批量测试脚本：1688 URL → CDP 抓取 → 组装信封 → 提交 Worker
           Ozon URL → 跟卖流程 → 提交 Worker

用法:
  # 试跑（只组装，不提交）
  python3 batch_test.py --urls-file urls.txt --dry-run

  # 干跑前 5 个
  python3 batch_test.py --urls-file urls.txt --dry-run --limit 5

  # 实际提交
  python3 batch_test.py --urls-file urls.txt --submit

  # 从第 10 个开始，跑 20 个
  python3 batch_test.py --urls-file urls.txt --submit --start 10 --limit 20

  # 指定新店铺凭证
  python3 batch_test.py --urls-file urls.txt --submit \
    --client-id 5371047 --api-key 411afbd4-c7ea-4fb3-b14f-3d9c2f246214

环境变量:
  WORKER_URL - Worker 地址（优先）
  MXOU_API_BASE - Worker 地址（回退，默认 https://worker.mxou.cn）
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Ensure skill/scripts/ is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data" / "batch_results"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def _default_worker_url() -> str:
    """Worker 地址默认值: WORKER_URL 优先，其次 MXOU_API_BASE，最后生产默认。"""
    return (os.environ.get("WORKER_URL") or os.environ.get("MXOU_API_BASE")
            or "https://worker.mxou.cn")


def _resolve_credentials(client_id: str, api_key: str, store_id: str = "") -> tuple[str, str]:
    """凭证解析: 显式 --client-id/--api-key 优先；空则回退 stores.json(get_ozon_credentials)。

    Returns (client_id, api_key)。未配置 → 空串。
    """
    cid = (client_id or "").strip()
    akey = (api_key or "").strip()
    if cid and akey:
        return cid, akey
    from scripts.lib.config_store import get_ozon_credentials
    creds = get_ozon_credentials(store_id)
    if creds:
        return (creds.get("client_id", "") or "").strip(), (creds.get("api_key", "") or "").strip()
    return cid, akey


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _poll_task_result(task_id: str, worker_url: str, timeout: int = 900) -> dict[str, Any]:
    """轮询 Worker task_status 直到终态（completed/failed），返回任务详情。

    v0.22: skill 提交后默认不等待；--wait 时调用，拿到 product_summary。
    """
    url = f"{worker_url.rstrip('/')}/task_status/{task_id}"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            resp = requests.get(url, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                status = data.get("status", "")
                if status in ("completed", "failed", "cancelled"):
                    return data
            elif resp.status_code == 404:
                return {"status": "not_found", "task_id": task_id}
        except Exception as _e:
            print(f"  ⏳ task_status 轮询异常（继续）: {_e}", flush=True)
        time.sleep(10)
    return {"status": "timeout", "task_id": task_id, "timeout_seconds": timeout}


def _print_product_summary(entry: dict[str, Any]) -> None:
    """打印单任务的产品经营明细（1688链接/利润率/售价/采购价/运费预估）。"""
    rows = entry.get("result_summary") or []
    if not rows:
        return
    label = entry.get("offer_id") or entry.get("product_id") or entry.get("id") or "?"
    print(f"  ── 产品明细（{label}）──", flush=True)
    for row in rows:
        print(
            f"    1688: {row.get('purchase_url', '')}\n"
            f"    采购价: ¥{row.get('purchase_cost', 0)} | 利润率: {row.get('margin_rate', 0)}"
            f" | 售价: {row.get('price', '')} | 运费预估: ¥{row.get('logistics_cost', 0)}"
            f" | 净利润率: {row.get('profit_rate', 0)} | OzonID: {row.get('product_id', '')}",
            flush=True,
        )


def parse_urls_file(filepath: str) -> list[dict[str, str]]:
    """Parse URL list file. Returns list of {type, url, id}."""
    results: list[dict[str, str]] = []
    seen_ids: set[str] = set()

    with open(filepath, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue

            line_lower = line.lower()
            if "ozon.ru" in line_lower:
                # v0.35.x: 兼容纯数字 product_id（/product/4767514314）与 slug 形式
                m = re.search(r"/products?/(?:[^/]+-)?(\d{6,20})", line)
                pid = m.group(1) if m else ""
                if pid and pid not in seen_ids:
                    seen_ids.add(pid)
                    results.append({"type": "ozon", "url": line, "id": pid})
            elif "1688.com" in line_lower:
                # v0.31.1: 兼容 m 站 detail.m.1688.com/page/index.html?offerId=xxx
                m = re.search(r"offer/(\d+)", line)
                if not m:
                    m = re.search(r"[?&]offerId=(\d+)", line)
                oid = m.group(1) if m else ""
                if oid and oid not in seen_ids:
                    seen_ids.add(oid)
                    results.append({"type": "1688", "url": line, "id": oid})

    return results


def process_1688_url(
    url: str,
    offer_id: str,
    client_id: str,
    api_key: str,
    worker_url: str,
    dry_run: bool,
    store_id: str = "",
) -> dict[str, Any]:
    """Process a single 1688 URL: CDP probe → graph envelope → submit."""
    from scripts.cloud_probe import build_graph_envelope_with_retry, submit_envelope

    result: dict[str, Any] = {
        "type": "1688",
        "url": url,
        "offer_id": offer_id,
        "timestamp": _now_iso(),
        "success": False,
    }

    try:
        # Step 1: Build GraphInput envelope (CDP + assembly)
        print(f"  🔍 [{offer_id}] CDP 抓取 + 组装信封...", flush=True)
        envelope = build_graph_envelope_with_retry(
            item_id=offer_id,
            detail_url=url,
            store_id=store_id,
            max_retries=3,
            retry_delay=15.0,
            max_skus=1,
        )

        if not envelope or not envelope.get("envelope"):
            result["error"] = "build_graph_envelope 返回空"
            print(f"  ❌ [{offer_id}] 信封为空", flush=True)
            return result

        draft = envelope.get("envelope", {}).get("draft", {})
        result["title"] = draft.get("title", "")[:80]
        result["price"] = draft.get("price", "")
        result["images_count"] = len(draft.get("images", []))
        result["envelope_saved"] = True

        print(f"  ✅ [{offer_id}] 信封组装完成: {result['title']}", flush=True)

        if dry_run:
            result["success"] = True
            result["dry_run"] = True
            return result

        # Step 2: Override store credentials
        envelope["ozon_client_id"] = client_id
        envelope["ozon_api_key"] = api_key

        # Step 3: Submit to Worker（v0.21: 429 限流指数退避重试 3 次：30/60/120s）
        print(f"  📤 [{offer_id}] 提交到 Worker...", flush=True)
        submit_result = None
        for attempt in range(4):
            try:
                submit_result = submit_envelope(envelope)
                break
            except requests.exceptions.HTTPError as _e:
                _status = _e.response.status_code if _e.response is not None else 0
                if _status == 429 and attempt < 3:
                    _wait = 30 * (2 ** attempt)
                    print(f"  ⏳ [{offer_id}] 429 限流，{_wait}s 后重试 ({attempt + 1}/3)...", flush=True)
                    time.sleep(_wait)
                    continue
                raise
        result["submit_result"] = submit_result
        result["task_id"] = submit_result.get("task_id", "")
        result["success"] = submit_result.get("ok", False)
        result["error"] = submit_result.get("error", "")

        if result["success"]:
            print(f"  🎉 [{offer_id}] 已提交 task_id={result['task_id']}", flush=True)
        else:
            print(f"  ⚠️ [{offer_id}] 提交失败: {result['error']}", flush=True)

    except Exception as e:
        result["error"] = str(e)
        print(f"  ❌ [{offer_id}] 异常: {e}", flush=True)

    return result


def process_ozon_url(
    url: str,
    product_id: str,
    client_id: str,
    api_key: str,
    worker_url: str,
    dry_run: bool,
    store_id: str = "",
) -> dict[str, Any]:
    """Process a single Ozon URL: follow-sell pipeline → submit."""
    from scripts.cloud_probe import follow_sell_cloud

    result: dict[str, Any] = {
        "type": "ozon",
        "url": url,
        "product_id": product_id,
        "timestamp": _now_iso(),
        "success": False,
    }

    old_cid = os.environ.get("OZON_CLIENT_ID", "")
    old_akey = os.environ.get("OZON_API_KEY", "")
    follow_result: dict[str, Any] = {}
    matches: list[dict[str, Any]] = []
    try:
        # Temporarily override env vars for this call
        os.environ["OZON_CLIENT_ID"] = client_id
        os.environ["OZON_API_KEY"] = api_key

        print(f"  🔗 [{product_id}] 跟卖流程 (Ozon抓图 → 1688搜同款 → 上架)...", flush=True)
        # ✅ v0.26 FIX: 透传 store_id — 此前漏传导致 follow_sell_cloud 用默认店铺：
        # ① extensions 定价参数（margin/commission/fx）取默认店铺为空 → Worker 用
        #    默认值（利润计算与主店铺不符）；② 物流费率/币种走默认店铺 profile。
        follow_result = follow_sell_cloud(url, auto_submit=not dry_run, store_id=store_id)

        result["follow_result"] = follow_result
        result["card_copied"] = follow_result.get("card_copied", False)
        result["search_keyword"] = follow_result.get("search_keyword", "")
        result["slug"] = follow_result.get("slug", "")

        matches = follow_result.get("1688_matches", []) or []
        result["matches_count"] = len(matches)
    except Exception as e:
        result["error"] = str(e)
        print(f"  ❌ [{product_id}] 异常: {e}", flush=True)
    finally:
        # ✅ 始终恢复环境变量（即使 follow_sell_cloud 异常）
        if old_cid:
            os.environ["OZON_CLIENT_ID"] = old_cid
        else:
            os.environ.pop("OZON_CLIENT_ID", None)
        if old_akey:
            os.environ["OZON_API_KEY"] = old_akey
        else:
            os.environ.pop("OZON_API_KEY", None)

    # v0.31.1: return 移出 finally（消除 SyntaxWarning: 'return' in 'finally' block）
    if not follow_result.get("success"):
        result["error"] = follow_result.get("error", "跟卖流程未找到匹配")
        print(f"  ⚠️ [{product_id}] 跟卖未找到匹配: {result['error']}", flush=True)
        if not dry_run:
            return result

    if matches:
        best = matches[0]
        result["best_match_id"] = best.get("id", "")
        result["best_match_title"] = best.get("title", "")
        print(f"  ✅ [{product_id}] 最佳匹配: {best.get('title', '')[:60]}", flush=True)

    if dry_run:
        result["success"] = follow_result.get("success", False)
        result["dry_run"] = True
        return result

    # Submit mode: result already includes task_id from auto_submit
    result["success"] = follow_result.get("success", False)
    result["task_id"] = follow_result.get("task_id", "")
    if follow_result.get("submit_result"):
        result["submit_result"] = follow_result["submit_result"]

    return result


def main() -> int:
    # ⚠️ PR-A (v0.31): 前置 runtime 检测 — 当前解释器非 3.12 时扫描 PATH 自动切换
    # （requests 等依赖 import 延迟到此处之后，错误解释器下不会在模块级崩）
    import sys as _sys
    from scripts.runtime_probe import re_exec_if_needed, resolve_python
    if _sys.version_info < (3, 12):
        _py_cmd, _is_cur = resolve_python()
        if not _is_cur:
            re_exec_if_needed(_py_cmd, str(Path(__file__).resolve()), list(_sys.argv[1:]))

    import requests

    parser = argparse.ArgumentParser(
        description="批量测试 1688/Ozon URL → Worker 上架"
    )
    parser.add_argument(
        "--urls-file", required=True, help="URL 列表文件（每行一个 URL）"
    )
    parser.add_argument(
        "--worker-url",
        default=_default_worker_url(),
        help="Worker 地址",
    )
    parser.add_argument(
        "--client-id",
        default=os.environ.get("OZON_CLIENT_ID", ""),
        help="Ozon Client ID",
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("OZON_API_KEY", ""),
        help="Ozon API Key",
    )
    parser.add_argument(
        "--store-id", default="", help="Store profile ID（用于物流费率）"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="只组装信封，不提交 Worker"
    )
    parser.add_argument(
        "--submit", action="store_true", help="实际提交到 Worker"
    )
    parser.add_argument(
        "--start", type=int, default=0, help="从第 N 个 URL 开始（0-based）"
    )
    parser.add_argument(
        "--limit", type=int, default=0, help="最多处理 N 个 URL（0=不限制）"
    )
    parser.add_argument(
        "--delay", type=float, default=3.0, help="每个 URL 之间的延迟秒数"
    )
    parser.add_argument(
        "--wait",
        action="store_true",
        help="提交后轮询任务结果，打印每个产品的 1688链接/利润率/售价/采购价/运费预估（v0.22）",
    )
    parser.add_argument(
        "--wait-timeout", type=int, default=900, help="--wait 轮询超时秒数（默认 900）"
    )
    parser.add_argument(
        "--type-filter",
        choices=["1688", "ozon", "all"],
        default="all",
        help="只处理特定类型的 URL",
    )

    args = parser.parse_args()

    if args.submit:
        # v0.31.1: 未显式传凭证 → 回退 stores.json(get_ozon_credentials)
        args.client_id, args.api_key = _resolve_credentials(
            args.client_id, args.api_key, args.store_id)
    if args.submit and (not args.client_id or not args.api_key):
        print("❌ --submit 需要 --client-id 和 --api-key（或设置 OZON_CLIENT_ID / OZON_API_KEY 环境变量，或 stores.json 已配置店铺凭证）")
        return 1

    # ── Pre-flight check ──
    from scripts.lib.config_store import check_config
    config = check_config()
    cdp = config.get("cdp", {})

    issues = []
    if config.get("missing"):
        issues.append(f"缺少凭证: {', '.join(config['missing'])}")

    # Auto-launch Chrome via chrome_launcher (same as check command)
    _cdp_ok = False
    try:
        from pathlib import Path as _P

        from scripts.lib.chrome_launcher import ensure_chrome_cdp
        _prof = str(_P(__file__).resolve().parent.parent / "data" / "browser" / "profiles" / "1688" / "default")
        ok, msg = ensure_chrome_cdp(auto_restart=True, profile_dir=_prof)
        _cdp_ok = ok
        if not ok:
            issues.append(f"Chrome CDP 启动失败: {msg}")
    except ImportError:
        if not cdp.get("browser_available"):
            issues.append("Chrome 浏览器未安装")
        if not cdp.get("session_available") and not cdp.get("cdp_running"):
            issues.append("CDP Chrome 未启动 (端口 9222)")
            issues.append("→ 启动: Chrome --remote-debugging-port=9222 --remote-allow-origins='*'")

    # 1688 login check via CDP cookies (not session file)
    # ⚠️ v0.14 E4: 用 CdpTab 封装替代手写 websocket（只读检查，不关远程 tab）
    if args.type_filter in ("1688", "all") and _cdp_ok:
        try:
            from scripts.lib.cdp_client import CdpTab
            _tabs = requests.get("http://127.0.0.1:9222/json", timeout=5).json()
            tab = None
            for _t in _tabs:
                if _t.get("type") == "page" and "1688.com" in _t.get("url", ""):
                    tab = CdpTab("http://127.0.0.1:9222", _t.get("id", ""), _t.get("webSocketDebuggerUrl", ""))
                    break
            if tab:
                val = tab.evaluate(
                    "document.cookie.match(/cookie2=|__cn_logon__=/) ? 'LOGGED_IN' : 'NOT_LOGGED_IN'",
                    timeout=8,
                )
                if val != "LOGGED_IN":
                    issues.append("1688 未登录 (仅影响 1688 URL)")
                    issues.append("→ 请在 Chrome 中登录 https://login.1688.com/")
                tab.close(close_remote=False)  # 只关 WS，保留用户 1688 标签页
        except Exception:
            pass  # Non-critical, actual probe will catch it

    # Check Ozon DataDome trust
    # ⚠️ v0.14 E4: 用 CdpConnection 封装替代手写 websocket（新建 tab 检查后全关）
    if args.type_filter in ("ozon", "all") and cdp.get("session_available"):
        try:
            from scripts.lib.cdp_client import CdpConnection
            conn = CdpConnection("http://127.0.0.1:9222")
            tab = conn.new_tab("https://www.ozon.ru/")
            tab.wait_for_load(timeout=10)
            val = tab.evaluate(
                "!!document.body && document.body.innerText.includes('OZON')",
                timeout=8,
            )
            if not val:
                issues.append("Ozon 被 DataDome 拦截！需先在 Chrome 中访问 ozon.ru")
                issues.append("→ 打开 https://www.ozon.ru/ 浏览一个商品即可建立信任")
            tab.close()  # 新建 tab → 全关，不残留
            conn.close()
        except Exception:
            pass

    if issues:
        print("⚠️ 前置条件检查发现问题:")
        for issue in issues:
            print(f"  • {issue}")
        print("\n运行 python3 scripts/cli.py check 查看详细诊断")
        if not args.dry_run:
            print("提示: 使用 --dry-run 可以先试跑不提交")
        if any("CDP" in i or "Chrome" in i for i in issues):
            print("\n❌ CDP Chrome 问题会阻止所有 1688 抓取和 Ozon 跟卖")
            print("   只有 Ozon URL 的 1688 AK 搜索不受影响")
            # Don't exit - let user continue with what works
    else:
        print("✅ 前置条件检查通过\n")

    # Parse URLs
    print(f"📖 读取 {args.urls_file}...")
    all_urls = parse_urls_file(args.urls_file)
    print(f"   总计 {len(all_urls)} 个唯一 URL")

    # Filter by type
    if args.type_filter != "all":
        all_urls = [u for u in all_urls if u["type"] == args.type_filter]
        print(f"   过滤后 ({args.type_filter}): {len(all_urls)} 个")

    # Apply start/limit
    urls = all_urls[args.start :]
    if args.limit > 0:
        urls = urls[: args.limit]

    if not urls:
        print("❌ 没有要处理的 URL")
        return 1

    print(f"📋 本批处理: {len(urls)} 个 URL (start={args.start}, limit={args.limit or 'all'})")
    if args.dry_run:
        print("🔍 模式: DRY RUN (只组装信封，不提交)")
    elif args.submit:
        print(f"🚀 模式: 实际提交到 {args.worker_url}")
        print(f"   Client ID: {args.client_id}")
    else:
        print("⚠️  模式: 既不 --dry-run 也不 --submit，不会做任何事")
        print("   请添加 --dry-run (试跑) 或 --submit (提交)")
        return 1

    # Output log file
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = OUTPUT_DIR / f"batch_{ts}.json"
    summary_file = OUTPUT_DIR / f"batch_{ts}_summary.json"

    results: list[dict[str, Any]] = []
    stats = {"total": len(urls), "success": 0, "failed": 0, "skipped": 0}

    print(f"\n{'='*60}")
    print(f"开始处理 {len(urls)} 个 URL...")
    print(f"{'='*60}\n")

    for i, item in enumerate(urls):
        idx = args.start + i + 1  # 1-based for display
        url_type = item["type"]
        url = item["url"]
        uid = item["id"]

        print(f"[{idx}/{args.start + len(urls)}] {url_type.upper()} {uid}", flush=True)

        if url_type == "1688":
            r = process_1688_url(
                url=url,
                offer_id=uid,
                client_id=args.client_id,
                api_key=args.api_key,
                worker_url=args.worker_url,
                dry_run=args.dry_run,
                store_id=args.store_id,
            )
        else:
            r = process_ozon_url(
                url=url,
                product_id=uid,
                client_id=args.client_id,
                api_key=args.api_key,
                worker_url=args.worker_url,
                dry_run=args.dry_run,
                store_id=args.store_id,
            )

        results.append(r)
        # v0.22: --wait 时轮询任务终态并展示产品明细
        if args.wait and r.get("success") and r.get("task_id"):
            _label = r.get("offer_id") or r.get("product_id") or uid
            print(f"  ⏳ [{_label}] 等待任务完成... task_id={r['task_id']}", flush=True)
            # ✅ v0.25: _poll_task_result 偶发返回 None（worker 非 JSON 响应）→ 防御
            final = _poll_task_result(r["task_id"], args.worker_url, timeout=args.wait_timeout) or {}
            r["final_status"] = final.get("status", "")
            # ✅ v0.25: worker 返回 error_message:null（JSON null）时 .get 默认值不生效
            r["final_error"] = (final.get("error_message") or "")[:300]
            r["result_summary"] = (final.get("result") or {}).get("product_summary", [])
            if final.get("status") == "completed" and r["result_summary"]:
                _print_product_summary(r)
            elif final.get("status") in ("failed", "cancelled"):
                print(f"  ❌ [{_label}] 任务{final.get('status')}: {r['final_error']}", flush=True)
            elif final.get("status") == "timeout":
                print(f"  ⏳ [{_label}] 轮询超时，可稍后查 task_status", flush=True)
        if r.get("success"):
            stats["success"] += 1
        else:
            stats["failed"] += 1

        # ⚠️ v0.14 E7: 移除循环内全量覆写（O(n²) 写入）— 改为每 5 条增量落盘一次，
        # 最终 summary 阶段完整写一次。崩溃时最多丢最近 5 条，而非全量重写 N 次。
        if (i + 1) % 5 == 0:
            log_file.write_text(
                json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
            )

        # Delay between URLs
        if i < len(urls) - 1:
            time.sleep(args.delay)

    # Final summary
    summary = {
        "timestamp": _now_iso(),
        "config": {
            "worker_url": args.worker_url,
            "client_id": args.client_id[:8] + "***" if args.client_id else "",
            "dry_run": args.dry_run,
            "type_filter": args.type_filter,
            "start": args.start,
            "limit": args.limit,
        },
        "stats": stats,
        "results": [
            {
                "type": r["type"],
                "id": r.get("offer_id") or r.get("product_id"),
                "title": r.get("title", r.get("best_match_title", ""))[:80],
                "success": r["success"],
                "task_id": r.get("task_id", ""),
                "error": r.get("error", "")[:200],
            }
            for r in results
        ],
    }
    # ⚠️ v0.14 E7: 循环结束后完整写一次 log_file（增量每 5 条 + 此处兜底，保证全量落盘）
    log_file.write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    summary_file.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"\n{'='*60}")
    print("📊 结果:")
    print(f"   成功: {stats['success']}")
    print(f"   失败: {stats['failed']}")
    print(f"   详情: {log_file}")
    print(f"   汇总: {summary_file}")
    print(f"{'='*60}")

    return 0 if stats["failed"] == 0 else 1


if __name__ == "__main__":
    # v0.29.x: 工具 Chrome「独立 profile + 常驻」(同 cli.py main 注释),
    # 命令出口不再关闭 —— 登录态常驻复用, 与用户 Chrome 互不干扰。
    sys.exit(main())
