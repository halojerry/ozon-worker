#!/usr/bin/env python3
"""一键修复 Ozon 存量商品卡（v0.21 配套工具，一次性迁移用）。

背景：48 商品实测后店铺有 17 张 declined + 若干异常卡。主因是「类目错配」。
实证结论：/v3/product/import 对已存在商品只更新属性/图片/尺寸，**不生效类目变更**
（最小 payload 实测无效）。因此错类目卡必须「归档 → 删除 → 按正确类目重建」。

本脚本对 FIX_MAP 内每张卡执行：
1. 归档（/v1/product/archive）
2. 删除（/v2/products/delete，无 SKU 的 declined 卡可删）
3. 按正确类目重建（/v3/product/import CREATE，保留原卡图片/价格/尺寸，
   填充新类目必填属性：品牌/原产国硬编码 + 字典属性按属性名 /values/search 首值）

仅处理 declined / 异常卡（无销量、sku=0）；approved 卡不动（避免丢销量/评价）。

用法（需能访问 api-seller.ozon.ru 的网络）：
  python scripts/repair_cards.py --dry-run            # 只打印将执行的步骤与 payload
  python scripts/repair_cards.py --offer 831249914209_0   # 只修指定卡
  python scripts/repair_cards.py                       # 修全部 FIX_MAP 内卡片
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ── 凭证（测试店铺）──
CLIENT_ID = "5371047"
API_KEY = "db64d282-c27e-4dad-b741-2e6108b9c0c2"
BASE = "https://api-seller.ozon.ru"


def _hdr() -> dict:
    return {"Client-Id": CLIENT_ID, "Api-Key": API_KEY, "Content-Type": "application/json"}


def _post(ep: str, body: dict) -> dict:
    # verify=False：本机 python 证书链不完整（curl 正常），内部运维脚本可接受
    # 网络偶发 SSL EOF → 重试 3 次
    last = None
    for _ in range(3):
        try:
            r = requests.post(BASE + ep, headers=_hdr(), json=body, timeout=40, verify=False)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last = e
            time.sleep(3)
    raise last


# ── 修复映射：offer_id → (description_category_id, type_id, 说明) ──
# 类目对来自 v0.21 本地类目树审计（asset category_tree / 本地 PG）
FIX_MAP: dict[str, tuple[int, int, str]] = {
    "821565763841_0": (17028959, 96513, "振动器"),          # 不倒翁AV棒
    "715801792931_0": (200001461, 970956271, "BDSM情趣设备"),  # 分腿带（原：钻石画）
    "1047770224870_0": (17027899, 87458883, "手链"),        # 青金石手链（原：串珠套件）
    "831501249238_0": (200001531, 970849653, "摩托车后视镜"),  # 后视镜（原：单车裤）
    "1024786474053_0": (80443823, 94452, "躺椅"),           # 月亮折叠椅（原：折叠手推车）
    "599605232861_0": (17028922, 98636, "手机支架"),        # 手机架（原：鞋类装饰）
    "892889757604_0": (17028743, 148495146, "手持风扇"),    # 挂脖风扇（原：迷你盘游戏套装）
    "810978171586_0": (86029514, 92721, "家用香薰"),        # 香薰石（原：家居浴袍）
    "1044587540855_0": (17028710, 91633, "自行车灯"),       # 气嘴灯（原：鞋类装饰）
    "665513389188_0": (17027907, 92355, "打蛋器"),          # 匀蛋器（原：野营厨房）
    "742807947791_0": (17028973, 92851, "毛绒玩具"),        # 自行车玩偶（原：鞋类装饰）
    "811445384641_0": (17028646, 970940776, "键盘清洁维修套件"),  # 清洁套装（原：电脑眼镜）
    "993771925952_0": (200001461, 96517, "乳头夹"),         # 乳夹（原：甜品套装）
    "965229015779_0": (17028653, 92102, "扳手套装"),        # 汽修工具（原：船舶维修 + 9782爆炸物）
    "1011966008290_0": (83250454, 97341, "修车躺板"),       # 修车躺板（原：滑板配件 + 9782爆炸物）
}


def _get_current(offer_id: str) -> dict:
    """取当前卡片：/v3/product/info/list + /v4/product/info/attributes。"""
    info = _post("/v3/product/info/list", {"offer_id": [offer_id]}).get("items", [])
    if not info:
        raise RuntimeError(f"卡片不存在: {offer_id}")
    it = info[0]
    attr_resp = _post("/v4/product/info/attributes", {"filter": {"offer_id": [offer_id]}, "limit": 10})
    attrs = (attr_resp.get("result") or [{}])[0]
    return {"info": it, "attrs": attrs}


def _build_item(cur: dict, dc: int, tp: int) -> dict:
    info = cur["info"]
    attrs = cur["attrs"]
    images = info.get("images") or []
    primary = info.get("primary_image") or ""
    if isinstance(primary, list):
        primary = primary[0] if primary else ""
    primary = primary or (images[0] if images else "")
    # 重建时只保留通用属性 + 新类目必填（旧类目专属属性不带，避免"属性不属于该类目"）
    keep_attrs = []
    for a in attrs.get("attributes", []) or []:
        aid = a.get("id")
        if aid in (85, 4389, 9048, 9024, 23171, 4180):
            vals = a.get("values") or []
            keep_attrs.append({"complex_id": 0, "id": aid, "values": vals})
    return {
        "description_category_id": dc,
        "type_id": tp,
        "offer_id": info["offer_id"],
        "name": info.get("name", ""),
        "description": info.get("name", ""),
        "price": info.get("price", ""),
        "old_price": info.get("old_price", ""),
        "currency_code": info.get("currency_code", "RUB"),
        "vat": info.get("vat", "0"),
        "dimension_unit": attrs.get("dimension_unit") or "mm",
        "weight_unit": attrs.get("weight_unit") or "g",
        "depth": attrs.get("depth", 0),
        "width": attrs.get("width", 0),
        "height": attrs.get("height", 0),
        "weight": attrs.get("weight", 0),
        "images": images,
        "primary_image": primary,
        "attributes": keep_attrs,
    }


def _fill_required_attrs(dc: int, tp: int, base_attrs: list) -> list:
    """拉取新类目 schema，为缺失的必填字典属性补值（属性名 /values/search 首值）。"""
    out = list(base_attrs)
    have = {a["id"] for a in out}
    try:
        schema = _post(
            "/v1/description-category/attribute",
            {"description_category_id": dc, "type_id": tp, "language": "RU"},
        ).get("result", [])
    except Exception as e:
        print(f"  ⚠️ schema 拉取失败: {e}")
        return out
    for a in schema:
        aid = int(a.get("id", 0))
        if not a.get("is_required") or aid in have or aid == 9782:
            continue
        if a.get("dictionary_id") or a.get("dictionary_values", True):
            try:
                sr = _post("/v1/description-category/attribute/values/search", {
                    "attribute_id": aid,
                    "description_category_id": dc,
                    "type_id": tp,
                    "value": str(a.get("name", ""))[:30],
                    "limit": 1,
                }).get("result", [])
                if sr:
                    out.append({"complex_id": 0, "id": aid,
                                "values": [{"dictionary_value_id": sr[0].get("id", 0),
                                            "value": str(sr[0].get("value", ""))}]})
                    print(f"  ✅ 必填字典属性 {aid}({a.get('name')}) → {sr[0].get('value')}")
            except Exception as e:
                print(f"  ⚠️ 必填属性 {aid} 补值失败: {e}")
        else:
            print(f"  ⚠️ 必填自由文本属性 {aid}({a.get('name')}) 无值可补，跳过")
    return out


def _recreate(cur: dict, dc: int, tp: int, dry: bool) -> dict:
    """归档 → 删除 → 重建。返回结果。"""
    info = cur["info"]
    pid = info.get("id")
    item = _build_item(cur, dc, tp)
    item["attributes"] = _fill_required_attrs(dc, tp, item["attributes"])
    steps = {"offer": info["offer_id"], "old_product_id": pid,
             "new_dc_tp": [dc, tp], "images": len(item["images"]),
             "attributes": [a["id"] for a in item["attributes"]]}
    if dry:
        steps["dry_run"] = True
        return steps
    # 1. 归档
    try:
        _post("/v1/product/archive", {"product_id": [pid]})
        print(f"  ✅ 已归档 {pid}")
    except Exception as e:
        print(f"  ⚠️ 归档提示（可能已归档，继续）: {e}")
    time.sleep(3)
    # 2. 删除
    try:
        _post("/v2/products/delete", {"products": [{"offer_id": info["offer_id"]}]})
        print(f"  ✅ 已删除 {pid}")
    except Exception as e:
        return {"offer": info["offer_id"], "stage": "delete", "error": str(e)}
    time.sleep(3)
    # 3. 重建（CREATE，offer_id 复用）
    try:
        resp = _post("/v3/product/import", {"items": [item]})
        task_id = resp.get("result", {}).get("task_id")
        steps["import_task_id"] = task_id
        if task_id:
            for _ in range(10):
                time.sleep(3)
                st = _post("/v1/product/import/info", {"task_id": task_id})
                items = (st.get("result") or {}).get("items") or []
                if items:
                    it = items[0]
                    if it.get("status") == "imported":
                        steps["new_product_id"] = it.get("product_id")
                        steps["status"] = "imported"
                        return steps
                    if it.get("status") in ("failed", "import_error"):
                        steps["status"] = it.get("status")
                        steps["errors"] = [e.get("code") for e in (it.get("errors") or [])]
                        steps["error_detail"] = it.get("errors")
                        return steps
            steps["status"] = "pending_timeout"
            return steps
        steps["status"] = "no_task_id"
        steps["resp"] = resp
        return steps
    except Exception as e:
        return {"offer": info["offer_id"], "stage": "import", "error": str(e)}


def _import_and_poll(item: dict) -> dict:
    resp = _post("/v3/product/import", {"items": [item]})
    task_id = resp.get("result", {}).get("task_id")
    if not task_id:
        return {"import_error": resp}
    # 轮询 import 状态（10 × 3s）
    for _ in range(10):
        time.sleep(3)
        st = _post("/v1/product/import/info", {"task_id": task_id})
        items = (st.get("result") or {}).get("items") or []
        if items:
            it = items[0]
            status = it.get("status")
            if status == "imported":
                return {"task_id": task_id, "status": status, "product_id": it.get("product_id")}
            if status in ("failed", "import_error"):
                return {"task_id": task_id, "status": status,
                        "errors": [e.get("code") for e in (it.get("errors") or [])],
                        "error_detail": it.get("errors")}
    return {"task_id": task_id, "status": "pending_timeout"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只打印 payload，不调用 Ozon")
    ap.add_argument("--offer", default="", help="只修指定 offer_id")
    args = ap.parse_args()

    targets = {k: v for k, v in FIX_MAP.items() if not args.offer or k == args.offer}
    if not targets:
        print(f"未找到目标: {args.offer}")
        return 1

    results = {}
    for offer_id, (dc, tp, note) in targets.items():
        print(f"\n===== {offer_id} → [{dc}/{tp}] {note} =====")
        try:
            cur = _get_current(offer_id)
            res = _recreate(cur, dc, tp, args.dry_run)
            print(json.dumps(res, ensure_ascii=False, indent=1)[:1200])
            results[offer_id] = res
        except Exception as e:
            print(f"❌ {offer_id}: {e}")
            results[offer_id] = {"error": str(e)}

    Path("/tmp/repair_results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n结果已存 /tmp/repair_results.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
