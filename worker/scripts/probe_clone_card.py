#!/usr/bin/env python3
"""探针：跟卖克隆模式可行性（2026-10-03，PLAN-follow-clone-v1 B0 取证）。

四轮实录结论（详见 PLAN B0 节）：
  A. Seller API 读不到竞品卡（/v3 info/list 空 + /v4 404）→ 竞品取数走 CDP 链；
  B. 跨店逐字克隆三轮全 INDEPENDENT（无自动并卡）→「绑定」无 API 通道，克隆独立卡唯一路径；
  C. Ozon 自家 CDN 图 URL 直传 import 被接受并挂图（primary_image → 新卡 images_n=1）；
  D. 回读形状：v3 body {product_id:[str]}、顶层 items、主键 id、images 恒空主图在
     primary_image；v4 属性主键 id、dictionary_value_id snake_case。

零业务耦合（独立 httpx，不 import 仓内模块），跑在 worker 容器内（stdin 管道）。
用法：
  python /tmp/probe_clone_card.py inspect --client-id X --api-key Y --pid 2062059459
  python /tmp/probe_clone_card.py clone   --client-id X --api-key Y --pid 5830685285 \
         --read-client-id 卡主CID --read-api-key 卡主KEY --price 9 [--archive-if-merged]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import httpx

BASE = "https://api-seller.ozon.ru"
# 媒体类属性我们产不了（content_enrich 同款排除），克隆时跳过防 import 拒收
MEDIA_ATTR_IDS = frozenset({21841, 21845, 4195})


def _post(c: httpx.Client, ep: str, body: dict) -> dict:
    r = c.post(BASE + ep, json=body, timeout=60)
    body_out: dict
    try:
        body_out = r.json()
    except Exception:
        body_out = {"_raw": r.text[:800]}
    body_out["_status"] = r.status_code
    print(f"[{r.status_code}] POST {ep}", flush=True)
    return body_out


def _extract_image_urls(item: dict | None) -> list[str]:
    """v3 实测（2026-10-03）：跨店铺卡 `images` 恒空，主图真身在 `primary_image`
    （Ozon 自家 CDN URL 列表）；富文本 11254 JSON 内的 src 是补充图源。"""
    if not item:
        return []
    out: list[str] = []
    for x in (item.get("images") or []):
        u = x.get("file_name") if isinstance(x, dict) else str(x or "")
        if u:
            out.append(u)
    if not out:
        for x in (item.get("primary_image") or []):
            u = x if isinstance(x, str) else (x.get("src") or x.get("file_name") or "")
            if u:
                out.append(u)
    return out


def _extract_items(info: dict) -> list:
    """v3/product/info/list 形状防御：result 数组 / result.items / 顶层 items 兜底。"""
    res = info.get("result")
    if isinstance(res, dict):
        out = res.get("items") or res.get("products") or []
    elif isinstance(res, list):
        out = res
    else:
        out = info.get("items") or info.get("products") or []
    return [x for x in out if isinstance(x, dict)]


def fetch_card(c: httpx.Client, pid: int):
    """两通道读卡：/v3/product/info/list（基本信息+图+重量尺寸）+ /v4/product/info/attributes（属性全表）。"""
    info = _post(c, "/v3/product/info/list",
                 {"product_id": [str(pid)]})  # 仓内正宗形状：字符串数组、无空 sku/offer_id
    items = _extract_items(info)
    item = items[0] if items else None
    v4 = _post(c, "/v4/product/info/attributes",
               {"filter": {"product_id": [pid], "offer_id": [], "sku": [], "visibility": "ALL"},
                "limit": 1000, "sort": "id", "language": "DEFAULT"})
    v4res = v4.get("result")
    v4item = None
    if isinstance(v4res, list) and v4res:
        v4item = v4res[0]
    elif isinstance(v4res, dict):
        v4item = (v4res.get("items") or [None])[0]
    return info, item, v4, v4item


def card_summary(item: dict | None, v4item: dict | None) -> dict:
    attrs = (v4item or {}).get("attributes") or []
    return {
        "name": (item or {}).get("name"),
        "description_category_id": (v4item or item or {}).get("description_category_id"),
        "type_id": (v4item or item or {}).get("type_id"),
        "images_n": len(_extract_image_urls(item)),
        "images_first": _extract_image_urls(item)[:2],
        "v4_attr_count": len(attrs),
        "v4_attr_ids": sorted({a.get("attribute_id") for a in attrs if a.get("attribute_id") is not None})[:40],
        "v4_complex_n": len((v4item or {}).get("complex_attributes") or []),
    }


def build_clone_item(target_pid: int, item: dict | None, v4item: dict | None,
                     offer_id: str, price: int) -> tuple[dict, dict]:
    """从回读数据构造 /v3/product/import item。

    属性对象 = 生产 prepare 同形状 {"complex_id": 0, "id": N, "values": [两键]}
    （B0-E 实录 2026-10-04：attribute_id 键无 complex_id 的形状对 optional 属性
    可绑、required 属性被判 error_attribute_values_empty 丢弃；两键值恒发——
    单发 dictionary_value_id 同样判空）。重量尺寸：v3 回显无 weight 字段
    （B0-D），从 4497 类属性取或信封 weight_g。
    """
    name = (item or {}).get("name") or (v4item or {}).get("name") or f"probe-clone-{target_pid}"
    dc = (v4item or item or {}).get("description_category_id")
    tp = (v4item or item or {}).get("type_id")
    images = _extract_image_urls(item) or _extract_image_urls(v4item)
    attrs_out: list[dict] = []
    skipped: dict[str, int] = {"media": 0, "no_values": 0, "no_attr_id": 0}
    for a in ((v4item or {}).get("attributes") or []):
        aid = a.get("attribute_id") or a.get("id")
        if aid in MEDIA_ATTR_IDS:
            skipped["media"] += 1
            continue
        if aid is None:
            skipped["no_attr_id"] += 1
            continue
        vals = a.get("values") or []
        if not vals:
            skipped["no_values"] += 1
            continue
        vout = []
        for v in vals:
            did = v.get("dictionaryValueId") or v.get("dictionary_value_id") or 0
            val = v.get("value")
            if not did and val in (None, ""):
                continue
            vout.append({"dictionary_value_id": did,
                         **({"value": str(val)} if val not in (None, "") else {})})
        if vout:
            attrs_out.append({"complex_id": int(a.get("complex_id") or 0),
                              "id": aid, "values": vout})
    imp_item = {
        "name": name,
        "offer_id": offer_id,
        "price": str(price),
        "vat": "0",
        "currency_code": "CNY",
        "description_category_id": dc,
        "type_id": str(tp) if tp is not None else None,
        "images": images[:8],
        "attributes": attrs_out,
    }
    # 重量尺寸：v3 回显无 weight/dims 字段（B0-D 实录）——4497（Вес товара）属性
    # 兜底取重量；尺寸信封/CDP 侧提供。
    if all(k not in imp_item for k in ("weight",)) and attrs_out:
        for a in attrs_out:
            if a["id"] == 4497 and a["values"]:
                try:
                    w4497 = int(float(a["values"][0].get("value") or 0))
                    if w4497 > 0:
                        imp_item["weight"] = w4497
                        imp_item["weight_unit"] = "g"
                except (TypeError, ValueError):
                    pass
                break
    # v3 若有回显（老形状兼容）原值透传
    for dim_key in ("weight", "depth", "width", "height"):
        v = (item or {}).get(dim_key)
        if v not in (None, "", 0, "0"):
            imp_item[dim_key] = v
    imp_item = {k: v for k, v in imp_item.items() if v is not None}
    meta = {"skipped": skipped, "attrs_n": len(attrs_out), "images_n": len(images[:8])}
    return imp_item, meta


def clone(c: httpx.Client, args) -> int:
    # 跟卖模拟：读卡用卡主凭证（product info 只覆盖本店），提交用卖方凭证——
    # 两套凭证时 = 跨店克隆一比一（dedup/并卡判定正是跨店场景）。
    rc = None
    if args.read_client_id and args.read_api_key:
        rc = httpx.Client(headers={"Client-Id": args.read_client_id,
                                   "Api-Key": args.read_api_key})
    info, item, v4, v4item = fetch_card(rc or c, args.pid)
    print("== card summary ==")
    print(json.dumps(card_summary(item, v4item), ensure_ascii=False, indent=2))
    if item is None and v4item is None:
        print("!! 两通道都读不到竞品卡 → 问题 A 结论：API 不可读竞品，需 CDP 链。raw：")
        print(json.dumps({"info": {k: v for k, v in info.items() if k != '_status'},
                          "v4": {k: v for k, v in v4.items() if k != '_status'}},
                         ensure_ascii=False, indent=2)[:2000])
        return 2
    offer_id = args.offer_id or f"probe-clone-{int(time.time())}"
    imp_item, meta = build_clone_item(args.pid, item, v4item, offer_id, args.price)
    print(f"== clone item（offer={offer_id} meta={meta}）==")
    print(json.dumps(imp_item, ensure_ascii=False, indent=2)[:3000])
    resp = _post(c, "/v3/product/import", {"items": [imp_item]})
    if resp.get("_status") != 200:
        print("!! import 拒收：", json.dumps(resp, ensure_ascii=False)[:1500])
        return 3
    task_id = resp.get("result", {}).get("task_id")
    print(f"import task_id = {task_id}")
    for i in range(30):
        time.sleep(3)
        pr = _post(c, "/v1/product/import/info", {"task_id": task_id})
        items = ((pr.get("result") or {}).get("items")) or []
        if items:
            it = items[0]
            print("== import/info item ==")
            print(json.dumps(it, ensure_ascii=False, indent=2)[:2000])
            new_pid = it.get("product_id")
            verdict = ("MERGED（并到原卡，product_id 相同）→ 绑定分支零成本成立"
                       if new_pid == args.pid else
                       f"INDEPENDENT（独立新卡 pid={new_pid}）→ 克隆分支成立，观察审核")
            print(f"== 结论：{verdict} ==")
            if new_pid:
                # 克隆后验证读：新卡归属卖方店铺（写凭证可读）——图有没有真的挂上
                vi, vitem, _, vv4 = fetch_card(c, int(new_pid))
                print("== 新卡验证读 ==")
                print(json.dumps(card_summary(vitem, vv4), ensure_ascii=False, indent=2))
                print(f"新卡 images_n = {len(_extract_image_urls(vitem))}（>0 = 图 URL 直传 import 实锤）")
            if new_pid == args.pid and args.archive_if_merged:
                ar = _post(c, "/v1/product/archive", {"product_id": [new_pid]})
                print("archive（清理测试 offer）：", json.dumps(ar, ensure_ascii=False)[:300])
            return 0
        if i % 5 == 4:
            print(f"  ...轮询中 ({(i + 1) * 3}s)")
    print("!! import/info 90s 无结果")
    return 4


def inspect(c: httpx.Client, args) -> int:
    info, item, v4, v4item = fetch_card(c, args.pid)
    print("== /v3/product/info/list 概要 ==")
    print(json.dumps(card_summary(item, None), ensure_ascii=False, indent=2))
    print("== /v4/product/info/attributes 概要 ==")
    print(json.dumps(card_summary(None, v4item), ensure_ascii=False, indent=2))
    if item is None and v4item is None:
        print("!! 两通道都读不到")
        print(json.dumps({"info": info, "v4": v4}, ensure_ascii=False, indent=2)[:2500])
        return 2
    print("== v4 原始首 3 属性（键形取证）==")
    for a in ((v4item or {}).get("attributes") or [])[:3]:
        print(json.dumps(a, ensure_ascii=False))
    return 0


def main() -> int:
    """凭证只从环境变量读（OZON_CID/OZON_KEY；读卡方 OZON_READ_CID/OZON_READ_KEY）。

    ⚠️ 2026-10-04 安全批：曾有 --client-id/--api-key argv 形参——凭证挂进程列表
    与 shell history（当天真店 key 实跑三轮后按 Mimosa finding 根修）。argv 形态
    不复活；跑法：
        OZON_CID=... OZON_KEY=... python probe_clone_card.py inspect --pid N
        （docker exec 传 -e，勿 -e KEY=value 明文落在命令行）"""
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["inspect", "clone"])
    ap.add_argument("--pid", type=int, required=True)
    ap.add_argument("--price", type=int, default=154)
    ap.add_argument("--offer-id", default="")
    ap.add_argument("--archive-if-merged", action="store_true")
    args = ap.parse_args()
    args.client_id = os.environ.get("OZON_CID", "")
    args.api_key = os.environ.get("OZON_KEY", "")
    args.read_client_id = os.environ.get("OZON_READ_CID", "")
    args.read_api_key = os.environ.get("OZON_READ_KEY", "")
    if not args.client_id or not args.api_key:
        print("缺少凭证：export OZON_CID / OZON_KEY（读卡方另配 OZON_READ_CID/OZON_READ_KEY）")
        return 2
    with httpx.Client(headers={"Client-Id": args.client_id, "Api-Key": args.api_key}) as c:
        return inspect(c, args) if args.action == "inspect" else clone(c, args)


if __name__ == "__main__":
    sys.exit(main())
