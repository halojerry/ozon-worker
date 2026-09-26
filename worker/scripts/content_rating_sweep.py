#!/usr/bin/env python3
"""存量卡内容评级清扫（v0.81 内容评分闭环配套，一次性运维工具）。

背景（2026-09-26 实机取证）：Ozon 内容评级 rating<40 几乎不展示；我们存量卡
Аннотация(4191)/Rich-контент(11254) 缺失（文本描述组占 50% 权重），import 补齐
实测卡分 42-57 → 90+。本脚本遍历店铺存量商品，批量评级，对 rating<阈值 且
有可填缺口的卡执行「/v4 全量回显 + 缺口补填 → 一次 /v3/product/import UPDATE」，
与过审后复检闭环共用唯一构造器（utils/content_enrich.build_enrich_update_body，
防逻辑漂移）。

清扫口径（保守）：
- 无 draft 证据 → 只补 4191（build_annotation 标题版）/11254（现卡图 Rich JSON），
  其余 improve 属性无 schema/证据一律跳过（审计块如实列出）；
- 卡上已有值的属性不覆盖（Ozon 已过审）；视频/图片缺口（21841/21845/4195）
  产不了，只在汇总里如实列出；
- 全量替换语义：UPDATE 必须带回 /v4 回显的现卡全部特征 + 扁平 dimensions +
  price/old_price/currency + images 回显（防洗卡，同 A6 纪律）；
- 被 Ozon 擦掉的值（erased_attribute_value）是警告不是失败。

用法（需能访问 api-seller.ozon.ru）：
  python scripts/content_rating_sweep.py --dry-run            # 只评级 + 打印动作计划，不动卡
  python scripts/content_rating_sweep.py --min-rating 90      # 默认 90
  python scripts/content_rating_sweep.py --product-id 6443821910   # 只扫指定卡
  python scripts/content_rating_sweep.py --visibility ALL     # 默认 ALL（含超价/隔离等非在售）

凭证：--client-id/--api-key 或环境变量 OZON_CLIENT_ID/OZON_API_KEY。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from utils.ozon_client import ozon_post
from utils.content_enrich import (
    build_enrich_update_body,
    extract_improve_attrs,
    parse_rating_products,
)

# /v3/product/list、/v4 批量回显、rating-by-sku 的契约批量上限
_BATCH = 1000


def list_products(client_id: str, api_key: str, visibility: str) -> list[dict]:
    """遍历全部商品（/v3/product/list 分页），返回 [{product_id, offer_id}]。"""
    out: list[dict] = []
    last_id = ""
    while True:
        body: dict = {
            "filter": {"visibility": visibility},
            "last_id": last_id,
            "limit": _BATCH,
        }
        resp = ozon_post(client_id, api_key, "/v3/product/list", body, timeout=60)
        result = (resp or {}).get("result") or {}
        items = result.get("items") or []
        out.extend(
            {"product_id": str(it.get("product_id")), "offer_id": str(it.get("offer_id") or "")}
            for it in items if isinstance(it, dict) and it.get("product_id")
        )
        last_id = str(result.get("last_id") or "")
        if len(items) < _BATCH or not last_id:
            break
    return out


def rating_by_skus(client_id: str, api_key: str, skus: list[str]) -> list[dict]:
    """批量评级（≤1000/批），返回 products 列表。"""
    products: list[dict] = []
    for i in range(0, len(skus), _BATCH):
        chunk = skus[i:i + _BATCH]
        resp = ozon_post(
            client_id, api_key, "/v1/product/rating-by-sku",
            {"skus": chunk}, timeout=60, language="RU",
        )
        products.extend(parse_rating_products(resp))
    return products


def fetch_card_echoes(client_id: str, api_key: str, product_ids: list[str]) -> dict[str, dict]:
    """批量拉 /v4/product/info/attributes 全量回显，{product_id: stored_item}。

    ⚠️ Ozon 对 info 族接口限流下会静默返回空——空批次退避重试一次（同
    shelf_service 口径）。单卡失败不影响其余卡（失败卡进 failures）。
    """
    echoes: dict[str, dict] = {}
    for i in range(0, len(product_ids), _BATCH):
        chunk = product_ids[i:i + _BATCH]
        int_ids = [int(p) for p in chunk if str(p).isdigit()]
        if not int_ids:
            continue
        items: list = []
        for attempt in range(2):
            resp = ozon_post(
                client_id, api_key, "/v4/product/info/attributes",
                {
                    "filter": {"product_id": [str(x) for x in int_ids]},
                    "limit": _BATCH,
                    "sort_by": "id",
                    "sort_dir": "asc",
                },
                timeout=60, language="RU",
            )
            items = (resp or {}).get("result") or []
            if items:
                break
            time.sleep(1 + attempt)
        for it in items if isinstance(items, list) else []:
            if isinstance(it, dict) and it.get("id"):
                echoes[str(it["id"])] = it
    return echoes


def fetch_price_map(client_id: str, api_key: str, product_ids: list[str]) -> dict[str, dict]:
    """批量拉 /v3/product/info/list 现价（price/old_price/currency_code 顶层字符串）。

    ⚠️ 契约坑（shelf_service 实证）：product_id 必须整数数组（字符串数组返回空）。
    """
    prices: dict[str, dict] = {}
    for i in range(0, len(product_ids), _BATCH):
        int_ids = [int(p) for p in product_ids[i:i + _BATCH] if str(p).isdigit()]
        if not int_ids:
            continue
        resp = ozon_post(
            client_id, api_key, "/v3/product/info/list",
            {"product_id": int_ids}, timeout=60, language="RU",
        )
        items = ((resp or {}).get("result") or {}).get("items") or []
        for it in items:
            if not isinstance(it, dict) or not it.get("id"):
                continue
            prices[str(it["id"])] = {
                "price": it.get("price"),
                "old_price": it.get("old_price") or it.get("marketing_price") or it.get("price"),
                "currency_code": str(it.get("currency_code") or "CNY"),
            }
    return prices


def main() -> int:
    ap = argparse.ArgumentParser(description="存量卡内容评级清扫（rating<阈值 补 4191/11254）")
    ap.add_argument("--client-id", default=os.getenv("OZON_CLIENT_ID", ""))
    ap.add_argument("--api-key", default=os.getenv("OZON_API_KEY", ""))
    ap.add_argument("--dry-run", action="store_true", help="只评级 + 打印动作计划，不提交 UPDATE")
    ap.add_argument("--min-rating", type=float, default=90.0, help="评级阈值（默认 90）")
    ap.add_argument("--visibility", default="ALL", help="/v3/product/list visibility（默认 ALL）")
    ap.add_argument("--product-id", action="append", default=[], help="只扫指定 product_id（可多次）")
    ap.add_argument("--limit", type=int, default=0, help="最多处理 N 张低分卡（0=全部）")
    args = ap.parse_args()

    if not args.client_id or not args.api_key:
        print("❌ 缺凭证：--client-id/--api-key 或 export OZON_CLIENT_ID=... OZON_API_KEY=...")
        return 2

    failures: list[dict] = []
    updated = 0
    skipped_no_gap = 0

    # ① 商品清单
    if args.product_id:
        products = [{"product_id": str(p), "offer_id": ""} for p in args.product_id]
        print(f"指定卡 {len(products)} 张，跳过 /v3/product/list 遍历")
    else:
        print(f"遍历商品（visibility={args.visibility}）...")
        products = list_products(args.client_id, args.api_key, args.visibility)
    if not products:
        print("无商品，结束")
        return 0
    print(f"共 {len(products)} 张卡")

    # ② 批量评级
    skus = [p["product_id"] for p in products]
    rating_products = rating_by_skus(args.client_id, args.api_key, skus)
    ratings = {str(p.get("sku")): p for p in rating_products if p.get("sku") is not None}
    scored = [r for r in ratings.values() if isinstance(r.get("rating"), (int, float))]
    avg = sum(float(r["rating"]) for r in scored) / len(scored) if scored else 0.0
    below = sorted(
        (r for r in scored if float(r["rating"]) < args.min_rating),
        key=lambda r: float(r["rating"]),
    )
    if args.limit:
        below = below[:args.limit]
    print(f"评级完成：{len(scored)} 张有分，均分 {avg:.1f}，低于 {args.min_rating:g} 的 {len(below)} 张")
    if not below:
        print("全部达标，结束")
        return 0

    # ③ 回显 + 现价
    below_ids = [str(r.get("sku")) for r in below]
    print(f"拉取 {len(below_ids)} 张低分卡的 /v4 回显 + 现价...")
    echoes = fetch_card_echoes(args.client_id, args.api_key, below_ids)
    price_map = fetch_price_map(args.client_id, args.api_key, below_ids)

    # ④ 逐卡构造并（非 dry-run 时）提交
    plans: list[dict] = []
    for r in below:
        pid = str(r.get("sku"))
        rating = float(r.get("rating") or 0)
        stored = echoes.get(pid)
        if not stored:
            failures.append({"product_id": pid, "stage": "echo", "error": "no_v4_echo"})
            continue
        px = price_map.get(pid) or {}
        body, audit = build_enrich_update_body(
            pid, stored, extract_improve_attrs(r), {}, {},
            price=px.get("price"),
            old_price=px.get("old_price"),
            currency_code=px.get("currency_code") or "CNY",
        )
        plan = {
            "product_id": pid,
            "rating": rating,
            "filled": audit.get("filled", []),
            "skipped": audit.get("skipped", []),
            "media_gap": audit.get("media_gap", []),
            "reason": audit.get("reason", ""),
        }
        if not body:
            skipped_no_gap += 1
            plans.append(plan)
            continue
        plans.append(plan)
        if args.dry_run:
            print(f"  [dry-run] product_id={pid} rating={rating:.0f} 将补填 {plan['filled']}（跳过 {plan['skipped']}，媒体缺口 {plan['media_gap']}）")
            updated += 1
            continue
        try:
            resp = ozon_post(args.client_id, args.api_key, "/v3/product/import", body, timeout=60)
            task_id = str(((resp or {}).get("result") or {}).get("task_id") or "")
            updated += 1
            print(f"  ✅ product_id={pid} rating={rating:.0f} UPDATE 已提交（import task_id={task_id}）filled={plan['filled']}")
        except Exception as exc:
            failures.append({"product_id": pid, "stage": "import", "error": str(exc)[:200]})
            print(f"  ❌ product_id={pid} UPDATE 失败: {str(exc)[:150]}")

    # ⑤ 汇总
    summary = {
        "total_cards": len(products),
        "rated": len(scored),
        "avg_rating": round(avg, 1),
        "below_threshold": len(below),
        "planned_or_updated": updated,
        "skipped_no_gap": skipped_no_gap,
        "failures": failures,
        "dry_run": args.dry_run,
        "min_rating": args.min_rating,
        "plans": plans,
    }
    print("\n===== 汇总 =====")
    print(json.dumps({k: v for k, v in summary.items() if k != "plans"}, ensure_ascii=False, indent=2))
    if args.dry_run:
        print("（dry-run：未提交任何 UPDATE；plans 见上，需实操去掉 --dry-run）")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
