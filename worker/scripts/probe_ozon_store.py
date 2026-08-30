"""M0 数据域探针:逐域调 Ozon 只读端点,落原始响应并出映射校验报告。

用途(PRD docs/PRD-store-sync-erp-v1.md §13):
1. 用真实测试店凭证逐域调 Ozon Seller API(只读,分页限 1-5 条)。
2. 原始响应落 docs/ozon-probe/<domain>.json(不入库)。
3. 对照 docs/ozon-field-map-v1.md 输出字段校验报告(observed vs expected)。
4. 冻结标准:每域字段 100% 对齐后标记映射表冻结,再进入实现。

用法:
    python scripts/probe_ozon_store.py --store 测试店铺5423887
    python scripts/probe_ozon_store.py --store 主店铺 --days 7
    python scripts/probe_ozon_store.py --dry-run          # 不联网,打印探针计划
    OZON_CLIENT_ID=xxx OZON_API_KEY=yyy python scripts/probe_ozon_store.py

凭证来源优先级:env OZON_CLIENT_ID/OZON_API_KEY > skill/data/config/stores.json --store > 默认店。
只读端点;绝不把 api_key 写入任何输出文件。
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

import requests

BASE_URL = "https://api-seller.ozon.ru"
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
STORES_JSON = REPO_ROOT / "skill" / "data" / "config" / "stores.json"
DEFAULT_OUT_DIR = REPO_ROOT / "docs" / "ozon-probe"


# ── 期望字段(与 docs/ozon-field-map-v1.md 对应;探针只报差异,不判死) ──
EXPECTED_FIELDS: dict[str, dict[str, list[str]]] = {
    "products_info": {
        "item": [
            "id", "offer_id", "name", "images", "primary_image",
            "price", "old_price", "min_price", "stocks", "is_archived",
            "is_autoarchived", "errors", "statuses", "visibility_details",
            "commissions", "price_indexes", "updated_at",
        ],
    },
    "returns": {
        "item": [
            "id", "posting_number", "order_id", "order_number", "type",
            "schema", "return_reason_name", "compensation_status",
            "product", "exemplars", "status", "logistic", "storage",
        ],
    },
    "analytics": {
        "row": ["dimensions", "metrics"],
        "result": ["data", "totals"],
    },
    "rating": {"top": ["groups", "localization_index"]},
    "warehouse": {"item": ["warehouse_id", "name", "is_rfbs"]},
    "prices": {"item": ["product_id", "offer_id", "price", "price_indexes", "commissions"]},
}


def _load_credentials(store: Optional[str]) -> tuple[str, str]:
    """读取 Ozon 凭证;绝不回显/落盘 api_key。"""
    env_cid = os.environ.get("OZON_CLIENT_ID", "").strip()
    env_key = os.environ.get("OZON_API_KEY", "").strip()
    if env_cid and env_key:
        return env_cid, env_key
    if not STORES_JSON.exists():
        raise SystemExit(f"stores.json 不存在: {STORES_JSON};请传 env OZON_CLIENT_ID/OZON_API_KEY")
    data = json.loads(STORES_JSON.read_text(encoding="utf-8"))
    stores = data.get("stores") or {}
    name = store or data.get("default") or (next(iter(stores)) if stores else "")
    row = stores.get(name)
    if not row or not row.get("client_id") or not row.get("api_key"):
        raise SystemExit(f"store 不存在或无凭证: {name};可选: {list(stores)}")
    return str(row["client_id"]), str(row["api_key"])


def _dump(out_dir: Path, domain: str, store: dict, request: dict, response: Any, error: str = "") -> None:
    """落原始响应(只含 client_id,绝不含 api_key)。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{domain}.json"
    payload = {
        "store": store,
        "request": request,
        "response": response if error == "" else None,
        "error": error,
        "probed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _call(client_id: str, api_key: str, endpoint: str, body: dict, timeout: int = 30) -> tuple[Any, str]:
    """POST 只读端点;返回 (json 或 None, error 文本)。"""
    try:
        resp = requests.post(
            f"{BASE_URL}{endpoint}",
            headers={"Client-Id": client_id, "Api-Key": api_key, "Content-Type": "application/json"},
            json=body,
            timeout=timeout,
        )
        if resp.status_code >= 400:
            return None, f"HTTP {resp.status_code}: {resp.text[:300]}"
        return resp.json(), ""
    except requests.RequestException as exc:
        return None, f"REQUEST_ERROR: {exc}"


def _first_items(data: Any, keys: tuple[str, ...] = ("items", "result", "data", "groups", "warehouses")) -> list[dict]:
    """从响应中递归捞第一个数组,便于字段观察。"""
    if isinstance(data, dict):
        for k in keys:
            v = data.get(k)
            if isinstance(v, list) and v:
                if isinstance(v[0], dict):
                    return v
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return data
    return []


def _observed_fields(data: Any) -> dict[str, list[str]]:
    """观察到的字段:顶层 + 首个数组元素 + result 嵌套(仅 dict 键名,不落值)。"""
    out: dict[str, list[str]] = {"top": list(data.keys()) if isinstance(data, dict) else []}
    items = _first_items(data)
    if items:
        out["item"] = list(items[0].keys())
    if isinstance(data, dict):
        res = data.get("result")
        if isinstance(res, dict):
            out["result"] = list(res.keys())
            rows = _first_items(res, ("data",))
            if rows:
                out["row"] = list(rows[0].keys())
    return out


def _report(observed: dict[str, dict[str, list[str]]], errors: dict[str, str], out_dir: Path) -> None:
    rows: list[dict[str, Any]] = []
    for domain, o in observed.items():
        exp = EXPECTED_FIELDS.get(domain, {})
        missing: dict[str, list[str]] = {}
        extra: dict[str, list[str]] = {}
        for kind, want in exp.items():
            got = o.get(kind, [])
            miss = [f for f in want if f not in got]
            ex = [f for f in got if f not in want]
            if miss:
                missing[kind] = miss
            if ex:
                extra[kind] = ex
        rows.append({
            "domain": domain,
            "error": errors.get(domain, ""),
            "observed": o,
            "missing_expected": missing,
            "extra_observed": extra,
            "aligned": not missing and not errors.get(domain, ""),
            "note": "期望字段全部命中;多出字段仅信息展示" if (not missing and extra) else "",
        })
    report_path = out_dir / "probe-report.json"
    report_path.write_text(
        json.dumps({"generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "domains": rows},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n===== 映射校验报告: {report_path} =====")
    for r in rows:
        tag = "✅" if r["aligned"] else ("⚠️ 有差异" if not r["error"] else "❌ 调用失败")
        print(f"{tag} {r['domain']}")
        if r["error"]:
            print(f"    error: {r['error'][:200]}")
        if r.get("note"):
            print(f"    {r['note']}")
        if r["missing_expected"]:
            print(f"    缺期望字段: {r['missing_expected']}")
        if r["extra_observed"]:
            print(f"    多出字段: {r['extra_observed']}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Ozon 数据域探针(M0)")
    ap.add_argument("--store", default=None, help="stores.json 中的店铺名")
    ap.add_argument("--days", type=int, default=7, help="订单/退货/分析回看天数(默认 7)")
    ap.add_argument("--limit", type=int, default=5, help="每个域最多取 N 条(默认 5)")
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    ap.add_argument("--dry-run", action="store_true", help="不联网,打印探针计划")
    args = ap.parse_args()

    out_dir = Path(args.out_dir).resolve()
    client_id, api_key = _load_credentials(args.store)
    store_meta = {"client_id": client_id, "store": args.store or "(default)"}
    to_dt = datetime.datetime.now(datetime.timezone.utc)
    since_dt = to_dt - datetime.timedelta(days=args.days)
    since = since_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    to = to_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    lim = max(1, min(args.limit, 50))

    probes: dict[str, tuple[str, dict]] = {
        "products_all": ("/v3/product/list", {"filter": {"visibility": "ALL"}, "limit": lim, "offset": 0}),
        "products_archived": ("/v3/product/list", {"filter": {"visibility": "ARCHIVED"}, "limit": lim, "offset": 0}),
        "orders": ("/v4/posting/fbs/list", {
            "filter": {"since": since, "to": to}, "limit": lim, "cursor": "",
            "with": {"analytics_data": True, "financial_data": True},
        }),
        "returns": ("/v1/returns/list", {"filter": {"since": since, "to": to}, "limit": lim, "offset": 0}),
        "analytics": ("/v1/analytics/data", {
            "date_from": since_dt.strftime("%Y-%m-%d"), "date_to": to_dt.strftime("%Y-%m-%d"),
            "metrics": ["hits_view_search", "hits_view_pdp", "orders_count", "revenue"],
            "dimension": ["day"], "limit": lim * 6, "offset": 0,
        }),
        "rating": ("/v1/rating/summary", {}),
        "actions": ("/v1/actions", {"limit": lim, "offset": 0}),
        "warehouse": ("/v2/warehouse/list", {}),
        "delivery_method": ("/v2/delivery-method/list", {"limit": 5}),
    }

    if args.dry_run:
        print(f"dry-run:store={store_meta} out={out_dir}")
        for domain, (endpoint, body) in probes.items():
            print(f"  {domain:18s} POST {endpoint} {json.dumps(body, ensure_ascii=False)[:120]}")
        return 0

    observed: dict[str, dict[str, list[str]]] = {}
    errors: dict[str, str] = {}

    # 先取商品 id 供 info/prices 二次探针
    product_ids: list[int] = []
    resp_all, err_all = _call(client_id, api_key, "/v3/product/list",
                              {"filter": {"visibility": "ALL"}, "limit": lim, "offset": 0})
    _dump(out_dir, "products_all", store_meta, probes["products_all"], resp_all, err_all)
    errors["products_all"] = err_all
    if resp_all:
        observed["products_all"] = _observed_fields(resp_all)
        for it in (resp_all.get("result") or {}).get("items") or []:
            pid = it.get("product_id") if isinstance(it, dict) else None
            if pid is not None:
                product_ids.append(int(pid))

    for domain, (endpoint, body) in probes.items():
        if domain == "products_all":
            continue
        resp, err = _call(client_id, api_key, endpoint, body)
        _dump(out_dir, domain, store_meta, {"endpoint": endpoint, "body": body}, resp, err)
        errors[domain] = err
        if resp is not None:
            observed[domain] = _observed_fields(resp)

    # 商品详情 + 三档价(依赖 product_ids)
    if product_ids:
        info_resp, info_err = _call(client_id, api_key, "/v3/product/info/list",
                                    {"product_id": product_ids[:lim]})
        _dump(out_dir, "products_info", store_meta,
              {"endpoint": "/v3/product/info/list", "body": {"product_id": product_ids[:lim]}},
              info_resp, info_err)
        errors["products_info"] = info_err
        if info_resp is not None:
            observed["products_info"] = _observed_fields(info_resp)

        prices_resp, prices_err = _call(client_id, api_key, "/v5/product/info/prices",
                                        {"filter": {"product_id": product_ids[:lim]}, "limit": lim, "offset": 0, "cursor": ""})
        _dump(out_dir, "prices", store_meta,
              {"endpoint": "/v5/product/info/prices", "body": {"filter": {"product_id": product_ids[:lim]}}},
              prices_resp, prices_err)
        errors["prices"] = prices_err
        if prices_resp is not None:
            observed["prices"] = _observed_fields(prices_resp)
    else:
        print("⚠️ 商品列表为空,跳过 info/prices 二次探针")

    _report(observed, errors, out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
