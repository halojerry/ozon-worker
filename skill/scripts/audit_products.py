#!/usr/bin/env python3
"""只读审计：拉取本地 stores.json 中各 Ozon 店铺的全部商品及其审核状态/错误项。

用途：结合 Sentry + 本地代码调查，汇总「所有商品的问题项」。
只读操作：仅调用 Ozon Seller API 的查询端点（/v3/product/list、/v3/product/info/list），
不修改任何商品/属性/价格。

用法：
    python3 audit_products.py                # 全部店铺
    python3 audit_products.py --store 5381204
    python3 audit_products.py --json out.json   # 输出聚合 JSON
"""
from __future__ import annotations

import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import requests

OZON_BASE = "https://api-seller.ozon.ru"
ROOT = Path(__file__).resolve().parent.parent
STORES_FILE = ROOT / "data" / "config" / "stores.json"


def load_stores() -> dict:
    raw = json.loads(STORES_FILE.read_text(encoding="utf-8"))
    stores = raw.get("stores", raw)
    return stores if isinstance(stores, dict) else {}


def seller_post(client_id: str, api_key: str, path: str, body: dict, timeout: int = 40) -> dict:
    headers = {"Client-Id": str(client_id), "Api-Key": api_key, "Content-Type": "application/json"}
    resp = requests.post(f"{OZON_BASE}{path}", json=body, headers=headers, timeout=timeout)
    if resp.status_code in (401, 403):
        return {"_auth_error": resp.status_code}
    if resp.status_code == 429:
        time.sleep(2)
        resp = requests.post(f"{OZON_BASE}{path}", json=body, headers=headers, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def list_all_offer_ids(client_id: str, api_key: str) -> list[str]:
    """分页拉取全部商品 offer_id（/v3/product/list）。"""
    offer_ids: list[str] = []
    last_id = ""
    for _ in range(200):  # 安全上限，防止死循环
        body: dict = {"filter": {"visibility": "ALL"}, "limit": 1000}
        if last_id:
            body["last_id"] = last_id
        data = seller_post(client_id, api_key, "/v3/product/list", body)
        items = data.get("result", {}).get("items", [])
        offer_ids.extend(i.get("offer_id", "") for i in items if i.get("offer_id"))
        last_id = data.get("result", {}).get("last_id", "")
        if not last_id or not items:
            break
    return offer_ids


def chunk(seq: list, size: int):
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def fetch_product_infos(client_id: str, api_key: str, offer_ids: list[str]) -> list[dict]:
    """批量查商品状态/错误（/v3/product/info/list，含 statuses.errors）。"""
    out: list[dict] = []
    for batch in chunk(offer_ids, 100):
        data = seller_post(client_id, api_key, "/v3/product/info/list", {"offer_id": batch})
        for item in data.get("items", []):
            statuses = item.get("statuses", {}) or {}
            errors = item.get("errors", []) or statuses.get("errors", []) or []
            out.append({
                "offer_id": item.get("offer_id"),
                "product_id": item.get("id"),
                "name": (item.get("name") or "")[:80],
                "validation_status": statuses.get("validation_status"),
                "moderate_status": statuses.get("moderate_status"),
                "errors": errors,
                "is_created": statuses.get("is_created"),
                "visible": item.get("visible"),
                "state": (item.get("visible") and "visible") or "hidden/absent",
            })
    return out


def err_code(err: dict) -> str:
    c = err.get("code") or ""
    return f"{c}:{err.get('message','')[:60]}" if c else (err.get("message") or "")[:80]


def main() -> None:
    stores = load_stores()
    only = None
    json_out = None
    args = sys.argv[1:]
    if "--store" in args:
        only = args[args.index("--store") + 1]
    if "--json" in args:
        json_out = args[args.index("--json") + 1]

    agg: dict = {}
    for name, cfg in stores.items():
        if not isinstance(cfg, dict):
            continue
        client_id = str(cfg.get("client_id", ""))
        api_key = str(cfg.get("api_key", ""))
        if not client_id or not api_key:
            continue
        if only and only not in (client_id, name):
            continue
        print(f"\n{'='*70}\n店铺: {name} (client_id={client_id})\n{'='*70}")
        try:
            offer_ids = list_all_offer_ids(client_id, api_key)
        except Exception as e:
            print(f"  ⚠️ 拉取商品列表失败: {e}")
            continue
        print(f"  商品总数: {len(offer_ids)}")

        infos = []
        for batch in chunk(offer_ids, 300):
            infos.extend(fetch_product_infos(client_id, api_key, batch))
            time.sleep(0.3)
        # 补充直接 /v2/product/info 不返回错误的商品，用 attributes 接口看真实审核问题
        time.sleep(0.3)

        problem = [i for i in infos if i["errors"] or i["validation_status"] not in ("success", None) or i["moderate_status"] in ("rejected", "declined")]
        err_counter: Counter = Counter()
        err_examples: dict = defaultdict(list)
        status_counter: Counter = Counter()
        for i in infos:
            status_counter[(i["validation_status"], i["moderate_status"])] += 1
            for e in i["errors"]:
                ec = err_code(e)
                err_counter[ec] += 1
                if len(err_examples[ec]) < 3:
                    err_examples[ec].append(i["offer_id"])

        print("  状态分布: validation_status×moderate_status")
        for k, v in status_counter.most_common(12):
            print(f"    {k} = {v}")
        print(f"\n  问题商品数: {len(problem)} / {len(infos)}")
        if err_counter:
            print("\n  错误项 TOP（按频率）:")
            for ec, cnt in err_counter.most_common(25):
                print(f"    [{cnt:4d}] {ec}")
                for oid in err_examples[ec]:
                    print(f"          例: {oid}")
        agg[name] = {
            "client_id": client_id,
            "total": len(infos),
            "problem": len(problem),
            "status": {f"{a}|{b}": c for (a, b), c in status_counter.items()},
            "errors": {ec: cnt for ec, cnt in err_counter.most_common()},
            "error_examples": {ec: ex[:3] for ec, ex in err_examples.items()},
            "problem_products": [
                {k: i[k] for k in ("offer_id", "name", "validation_status", "moderate_status", "errors")}
                for i in problem[:200]
            ],
        }
        time.sleep(0.5)

    if json_out:
        Path(json_out).write_text(json.dumps(agg, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n✅ 聚合结果已写入 {json_out}")


if __name__ == "__main__":
    main()
