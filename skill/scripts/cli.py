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
    from scripts.cloud_probe import build_graph_envelope_with_retry

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

    graph = build_graph_envelope_with_retry(
        item_id=item_id,
        detail_url=detail_url,
        category_query=args.category_query,
        max_retries=args.retries,
    )

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

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
