#!/usr/bin/env python3
"""what_to_sell 字段实测探针（discover 漏斗 v2 · Task 4，一次性工具）。

目的：docs/PLAN-discover-funnel-v2-v1.md 附录 A —— 实测 seller.ozon.ru
what_to_sell data/v3 对候选 SKU 到底给哪些运营指标字段（浏览量/加购率/促销
天数/退货率/配送天数…），决定 ProductCandidate 指标扩容（Task 5）的实际清单。

与 fetch_sales_analytics 的区别：那个走 _extract_metrics 防御式子集解析；
本探针保留 RAW item，重点回答「原始响应里有哪些键、值长什么样」——
解析层 alias 覆盖不到的新字段只有看原始键才能发现。

用法（需本机工具 Chrome + seller.ozon.ru 已登录）：
    cd skill && python3.12 scripts/probe_what_to_sell_fields.py --skus 4767514314,2510249010
    python3.12 scripts/probe_what_to_sell_fields.py --from-discovery --limit 8

输出：逐 SKU 摘要打印 + 原始响应存 data/discovery/probe_what_to_sell_{ts}.json
只读探针：不写 seller_analytics 缓存、不上报 worker。
"""
from __future__ import annotations

import argparse
import contextlib
import glob
import json
import os
import sys
import time
from pathlib import Path

# Ensure repo skill/ root is on sys.path（scripts 包可导入，对齐 cli.py:31）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.lib import ozon_seller_analytics as osa  # noqa: E402

# 重点观察键（shopbang 63 列里有、我们候选里还没有/没确认的 + 基线字段）
TARGET_KEYS = [
    # 流量/转化（shopbang: sessionCount/convToCartPdp/convToCartSearch/clickRate）
    "sessionCount", "qtyViewPdp", "sessionCountSearch",
    "convToCartPdp", "convToCartSearch", "convViewToOrder", "customClickRate",
    # 促销（daysInPromo/discount/promoRevenueShare/daysWithTrafarets）
    "daysInPromo", "discount", "promoRevenueShare", "daysWithTrafarets",
    # 成交/退货（nullableRedemptionRate；shopbang 还有 avgDeliveryDays）
    "nullableRedemptionRate", "avgDeliveryDays",
    # 基线（应命中，作对照组）
    "soldCount", "gmvSum", "salesDynamics", "drr", "createDays",
    "nullableCreateDate", "rating", "reviewsCount",
    "fbp_rate", "rfbs_rate", "category2Id", "category3Id",
    "customVolume", "customWeight", "attributes", "followInfo",
]


def _load_discovery_skus(limit: int) -> list[str]:
    """从最新 discovery_*.json 取候选 product_id（无匹配数据时的兜底素材）。"""
    files = sorted(glob.glob(str(Path(__file__).resolve().parent.parent
                                 / "data" / "discovery" / "discovery_*.json")))
    if not files:
        return []
    with open(files[-1], encoding="utf-8") as f:
        data = json.load(f)
    # 日志两形态：裸 list（实测）或 {"candidates": [...]}（防御兼容）
    cands = data if isinstance(data, list) else data.get("candidates") or []
    skus = []
    for c in cands:
        pid = str(c.get("ozon_product_id") or "").strip()
        if pid.isdigit():
            skus.append(pid)
    return skus[:limit]


def _probe_sku(tab, company_id: str, sku: str) -> dict | None:
    """单 SKU 查询，返回 raw response dict（不解析不缓存）。"""
    js = osa._SELLER_ANALYTICS_JS.replace("__COMPANY_ID__", json.dumps(company_id)) \
                                 .replace("__SKU__", sku)
    raw = tab.evaluate(js, await_promise=True, timeout=osa.EVALUATE_TIMEOUT)
    if not raw:
        return None
    data = json.loads(raw)
    if data.get("error"):
        print(f"  ❌ sku {sku} error: {str(data['error'])[:160]}")
        return None
    return data


def _first_item(data: dict) -> dict:
    result = data.get("result") or {}
    items = result.get("items") or data.get("items") or []
    return items[0] if items else {}


def _describe_structure(data: dict, depth: int = 0) -> str:
    """响应结构速览（前两层键 + 各容器长度），定位 items 真实嵌套位置。"""
    if depth > 2 or not isinstance(data, dict):
        return type(data).__name__
    parts = []
    for k, v in data.items():
        if isinstance(v, dict):
            parts.append(f"{k}:dict({_describe_structure(v, depth + 1)})")
        elif isinstance(v, list):
            inner = f"[{len(v)}]" + (f" 首元键:{sorted(v[0].keys())[:8]}" if v and isinstance(v[0], dict) else "")
            parts.append(f"{k}:list{inner}")
        else:
            parts.append(f"{k}={str(v)[:40]}")
    return "; ".join(parts)


def _find_metric_dicts(data: dict, path: str = "$") -> list[tuple[str, dict]]:
    """递归找含 soldCount/sku/gmvSum 特征键的 dict（结构未知时的兜底定位）。"""
    found: list[tuple[str, dict]] = []
    if isinstance(data, dict):
        keys = set(data.keys())
        if keys & {"soldCount", "gmvSum", "salesDynamics", "sku", "variantId"}:
            found.append((path, data))
        for k, v in data.items():
            found.extend(_find_metric_dicts(v, f"{path}.{k}"))
    elif isinstance(data, list):
        for i, v in enumerate(data[:5]):
            found.extend(_find_metric_dicts(v, f"{path}[{i}]"))
    return found


def main() -> int:
    ap = argparse.ArgumentParser(description="what_to_sell 字段实测探针")
    ap.add_argument("--skus", default="", help="逗号分隔 Ozon SKU/商品 ID")
    ap.add_argument("--from-discovery", action="store_true",
                    help="从最新 discovery_*.json 取候选 ID 作素材")
    ap.add_argument("--limit", type=int, default=8, help="--from-discovery 取多少个")
    ap.add_argument("--delay", type=float, default=1.5, help="SKU 间隔秒（防反爬）")
    ap.add_argument("--cdp-url", default="http://127.0.0.1:9222")
    args = ap.parse_args()

    skus = [s.strip() for s in args.skus.split(",") if s.strip().isdigit()]
    if args.from_discovery:
        skus = skus or _load_discovery_skus(args.limit)
    if not skus:
        print("无 SKU 素材：--skus 传入或 --from-discovery（且本地有 discovery 日志）")
        return 2

    from scripts.lib.chrome_launcher import ensure_chrome_cdp
    ok, msg = ensure_chrome_cdp()
    print(f"Chrome CDP: {'OK' if ok else 'FAIL'} ({msg})")
    if not ok:
        return 2

    from scripts.lib.cdp_client import CdpConnection
    with contextlib.closing(CdpConnection(args.cdp_url)) as cdp:
        tab, reused = osa._tab_for_seller(cdp)
        try:
            # sc_company_id 轮询（与 fetch_sales_analytics 同款 8s 等待）
            company_id = ""
            deadline = time.time() + 8
            while time.time() < deadline:
                try:
                    company_id = str(tab.evaluate(osa._GET_COMPANY_ID_JS, timeout=10) or "")
                except Exception as exc:
                    print(f"  read company_id failed: {exc}")
                if company_id:
                    break
                time.sleep(0.5)
            if not company_id:
                print("❌ seller.ozon.ru 未登录（无 sc_company_id）——先在工具 Chrome 登录 seller 后台")
                return 3
            print(f"company_id OK（tab {'复用' if reused else '新建'}），探测 {len(skus)} SKU…\n")

            report: dict = {"probed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                            "skus": {}, "raw": {}}
            hit_counter: dict[str, int] = {}
            for i, sku in enumerate(skus):
                print(f"— sku {sku}")
                data = _probe_sku(tab, company_id, sku)
                if data is None:
                    report["skus"][sku] = {"error": "no_data"}
                else:
                    item = _first_item(data)
                    struct = _describe_structure(data)
                    metric_hits = _find_metric_dicts(data)
                    if not item:
                        # items 标准位为空：打印结构速览 + 特征 dict 定位，区分
                        # 「嵌套不同」vs「该 SKU 在 what_to_sell 真无数据」
                        print(f"    🔍 结构: {struct[:400]}")
                        if metric_hits:
                            p, d0 = metric_hits[0]
                            print(f"    🔍 特征 dict @ {p} 键: {sorted(d0.keys())[:20]}")
                        else:
                            print("    🔍 全响应无 soldCount/sku/gmvSum 特征 dict → 该 SKU 疑似无 seller 数据")
                    hits = {k: item.get(k) for k in TARGET_KEYS if k in item}
                    missing = [k for k in TARGET_KEYS if k not in item]
                    for k in hits:
                        hit_counter[k] = hit_counter.get(k, 0) + 1
                    report["skus"][sku] = {"hits": hits, "missing": missing,
                                           "raw_key_count": len(item),
                                           "structure": struct,
                                           "metric_dict_paths": [p for p, _ in metric_hits[:3]]}
                    report["raw"][sku] = item or (metric_hits[0][1] if metric_hits else data)
                    for k, v in hits.items():
                        vs = json.dumps(v, ensure_ascii=False)[:80]
                        print(f"    ✅ {k} = {vs}")
                    print(f"    ⛔ 缺失: {', '.join(missing) or '无'}")
                    print(f"    item 原始键总数: {len(item)}")
                if i < len(skus) - 1:
                    time.sleep(args.delay)

            # 汇总表（附录 A 素材：命中率）
            print("\n══ 命中率汇总（n=%d）══" % len(skus))
            for k in TARGET_KEYS:
                n = hit_counter.get(k, 0)
                mark = "✅" if n == len(skus) else ("⚠️ " if n else "⛔")
                print(f"  {mark} {k}: {n}/{len(skus)}")

            out = os.path.join(os.path.dirname(__file__), "data", "discovery",
                               f"probe_what_to_sell_{time.strftime('%Y%m%d_%H%M%S')}.json")
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with open(out, "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=1)
            print(f"\n原始响应已存: {out}")
            return 0
        finally:
            osa._close_seller_tab(cdp, tab, reused)


if __name__ == "__main__":
    raise SystemExit(main())
