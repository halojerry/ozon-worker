"""离线试填校验器（v0.26）— 不真实上架，本地模拟 Ozon 属性校验。

用途：改填充逻辑（帽类性别/数字类型/字典全量填满）后，先用离线校验器验证
「应该怎么填」——拉真实类目 schema + 字典值 → 模拟填值 → 本地校验 → 输出
对齐 Ozon 错误码的模拟错误清单。完全不调用 import（Ozon 无 dry-run API，
import 提交即创建商品卡，本工具避开）。

流程：
  1. 类目解析：优先信封 draft.ozon_category（数字直查 PG 类目树），否则文本兜底；
     也可直接 --dc/--type 指定（已从 Ozon 页面/其他渠道得知的类目）。
  2. 拉真实 schema：/v1/description-category/attribute（language=DEFAULT，RU）
  3. 拉字典值：/values + /values/search（需凭证）
  4. 模拟填值：复用 prepare 的 3 个填充函数（必填字典兜底/可选字典全量填满/数字类型转换）
  5. 校验：必填缺失（对齐「必填属性缺失: X (id=N)」）、字典值合法性（dictionary_value_id>0）、
     类型（对齐 VALUE_MUST_BE_INTEGER/DECIMAL）、语言（中文/拉丁 → DESCRIPTION_DECLINE）

用法：
  # 信封模式（含 1688 属性 + 标题 + 类目 hint）
  python scripts/offline_validate.py --store 5381204 --envelope envelope.json

  # 直接指定类目 + 1688 属性
  python scripts/offline_validate.py --store 5381204 --dc 41777465 --type 93040 \
      --title "Панама" --attrs '{"颜色分类":"黑色","适用性别":"中性"}'

输出：模拟 Ozon 错误清单（JSON），每个错误带对齐的错误码。
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import requests  # noqa: E402

SELLER_BASE = "https://api-seller.ozon.ru"
ATTR_SCHEMA_URL = "/v1/description-category/attribute"
VALUES_URL = "/v1/description-category/attribute/values"
VALUES_SEARCH_URL = "/v1/description-category/attribute/values/search"


def load_store_credentials(client_id: Optional[str], api_key: Optional[str], store_name: str, stores_file: str = "") -> tuple[str, str]:
    """读 skill/data/config/stores.json 拿凭证（--store 用店名或 client_id）。"""
    if not stores_file:
        stores_file = os.path.join(os.path.dirname(__file__), "..", "..", "skill", "data", "config", "stores.json")
    if not os.path.exists(stores_file):
        return (client_id or "", api_key or "")
    try:
        data = json.load(open(stores_file, encoding="utf-8"))
    except Exception:
        return (client_id or "", api_key or "")
    stores = data.get("stores", data) if isinstance(data, dict) else {}
    if not isinstance(stores, dict):
        return (client_id or "", api_key or "")
    for name, cfg in stores.items():
        if not isinstance(cfg, dict):
            continue
        if store_name and (name == store_name or str(cfg.get("client_id", "")) == store_name):
            return str(cfg.get("client_id", "")), str(cfg.get("api_key", ""))
    return (client_id or "", api_key or "")


def seller_get(client_id: str, api_key: str, path: str, body: dict) -> dict:
    headers = {"Client-Id": client_id, "Api-Key": api_key, "Content-Type": "application/json"}
    resp = requests.post(SELLER_BASE + path, json=body, headers=headers, timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(f"Ozon API {path} 失败: HTTP {resp.status_code} {resp.text[:200]}")
    return resp.json()


def fetch_schema(client_id: str, api_key: str, dc: int, tp: int) -> list[dict]:
    data = seller_get(client_id, api_key, ATTR_SCHEMA_URL, {
        "description_category_id": dc, "type_id": tp, "language": "DEFAULT",
    })
    return data.get("result", []) or []


def fetch_dict_values(client_id: str, api_key: str, dc: int, tp: int, attr_id: int) -> list[dict]:
    """拉字典值（分页，最多 200×5）。"""
    values: list[dict] = []
    last_id = 0
    for _ in range(5):
        body = {
            "description_category_id": dc, "type_id": tp, "attribute_id": attr_id,
            "language": "DEFAULT", "limit": 200, "last_value_id": last_id,
        }
        try:
            data = seller_get(client_id, api_key, VALUES_URL, body)
        except RuntimeError:
            break
        result = data.get("result") or []
        values.extend(result)
        if not data.get("has_next", False) or not result:
            break
        last_id = result[-1].get("id", 0)
        if not last_id:
            break
    return values


class OfflineState:
    """模拟 prepare 节点所需的最小 state。"""
    def __init__(self, cid: str, key: str, dc: str, tp: str, dict_vals: dict):
        self.ozon_client_id = cid
        self.ozon_api_key = key
        self.token = ""
        self.description_category_id = dc
        self.type_id = tp
        self.dictionary_values = dict_vals


def simulate_fill(items: list, schema: list, draft: dict, state: OfflineState) -> list:
    """复用 prepare 的 3 个填充函数模拟填值（必填兜底 + 可选全量 + 数字类型）。"""
    import graphs.nodes.prepare_ozon_upload_node as prep

    items = prep._fill_missing_required_dict_attrs(items, schema, draft, state)
    items = prep._fill_optional_dict_attrs(items, schema, draft, state)
    # 数字类型转换作用在 final_attributes 格式（attribute_id/value），
    # 这里 items 是 Ozon 格式（id/values），单独模拟：
    # 收集所有属性 → _convert_numeric_attrs → 回填
    flat = [
        {"attribute_id": a["id"], "value": v.get("value", "")}
        for item in items for a in item.get("attributes", [])
        for v in a.get("values", [])
        if isinstance(a, dict) and isinstance(v, dict) and a.get("values")
    ]
    # 去重（同 attribute_id 多值）
    seen: set = set()
    uniq = []
    for f in flat:
        if f["attribute_id"] not in seen:
            seen.add(f["attribute_id"])
            uniq.append(f)
    orig_map = {f["attribute_id"]: f["value"] for f in uniq}
    converted = prep._convert_numeric_attrs(uniq, schema)
    # 只回写「值真的被转换」的属性（数字属性），字典属性原样透传不算转换。
    # 多值属性（如 9163 男+女双值）不能被首值标签覆盖。
    changed_ids = {
        c["attribute_id"] for c in converted
        if c["attribute_id"] in orig_map and c["value"] != orig_map[c["attribute_id"]]
    }
    conv_map = {c["attribute_id"]: c["value"] for c in converted if c["attribute_id"] in changed_ids}
    for item in items:
        for a in item.get("attributes", []):
            if isinstance(a, dict) and a.get("id") in conv_map:
                for v in a.get("values", []):
                    if isinstance(v, dict):
                        v["value"] = conv_map[a["id"]]
    return items


def validate_items(items: list, schema: list) -> List[Dict[str, str]]:
    """本地校验，输出对齐 Ozon 错误码的模拟错误清单。"""
    errors: List[Dict[str, str]] = []
    schema_map = {int(a.get("id") or 0): a for a in schema if isinstance(a, dict)}
    required_ids = [aid for aid, a in schema_map.items() if a.get("is_required")]
    dict_attr_ids = {aid for aid, a in schema_map.items() if int(a.get("dictionary_id") or 0) > 0}
    import re
    for i, item in enumerate(items):
        attr_ids = {int(a.get("id") or 0) for a in item.get("attributes", []) if isinstance(a, dict)}
        # 必填缺失
        for rid in required_ids:
            if rid not in attr_ids:
                errors.append({
                    "code": "REQUIRED_ATTR_MISSING",
                    "message": f"必填属性缺失: {schema_map[rid].get('name','')} (id={rid})",
                    "item": i,
                })
        for a in item.get("attributes", []):
            if not isinstance(a, dict):
                continue
            aid = int(a.get("id") or 0)
            vals = a.get("values", [])
            # 字典值合法性
            if aid in dict_attr_ids:
                for v in vals:
                    if int(v.get("dictionary_value_id") or 0) <= 0:
                        errors.append({
                            "code": "DICTIONARY_VALUE_INVALID",
                            "message": f"字典属性 {aid} 缺 dictionary_value_id（请从列表选择）",
                            "item": i,
                        })
            # 类型（对齐 VALUE_MUST_BE_INTEGER / VALUE_MUST_BE_DECIMAL）
            atype = str(schema_map.get(aid, {}).get("type") or "")
            for v in vals:
                val = str(v.get("value") or "")
                if not val:
                    continue
                if atype == "Integer" and not val.lstrip("-").isdigit():
                    errors.append({"code": "VALUE_MUST_BE_INTEGER",
                                   "message": f"属性 {aid} 应为整数，实际 '{val}'", "item": i})
                elif atype == "Decimal":
                    try:
                        float(val.replace(",", "."))
                    except ValueError:
                        errors.append({"code": "VALUE_MUST_BE_DECIMAL",
                                       "message": f"属性 {aid} 应为小数，实际 '{val}'", "item": i})
            # 语言（中文/纯拉丁 → DESCRIPTION_DECLINE）
            for v in vals:
                val = str(v.get("value") or "")
                if re.search(r'[\u4e00-\u9fff]', val):
                    errors.append({"code": "DESCRIPTION_DECLINE",
                                   "message": f"属性 {aid} 含中文字符: {val[:40]}", "item": i})
    return errors


def _info(msg: str) -> None:
    """进度信息：--json 模式走 stderr，保证 stdout 只有纯 JSON。"""
    print(msg, file=sys.stderr if _JSON_MODE else sys.stdout)


def _force_logs_to_stderr() -> None:
    """--json 模式：把根 logger 全部转到 stderr。

    worker 的 setup_structured_logging 会把 handler 绑到 sys.stdout 文件对象，
    prepare 模块链上的 get_logger 首次调用会触发该配置 → INFO 日志污染 stdout。
    这里清空根 logger 的 handler 改绑 stderr，并锁死 _root_configured 防后续重配。
    """
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()
    _h = logging.StreamHandler(sys.stderr)
    _h.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S"
    ))
    root.addHandler(_h)
    try:
        import utils.logger as _ul
        _ul._root_configured = True  # noqa: SLF001 — 防 get_logger 重新绑 stdout
    except Exception:
        pass


_JSON_MODE = False


def main() -> int:
    ap = argparse.ArgumentParser(description="离线试填校验器（不真实上架）")
    ap.add_argument("--store", default="", help="店铺名或 client_id（读 skill/data/config/stores.json）")
    ap.add_argument("--stores-file", default="", help="stores.json 路径（容器内挂载时用）")
    ap.add_argument("--client-id", default="")
    ap.add_argument("--api-key", default="")
    ap.add_argument("--dc", default="", help="description_category_id（已知道时直接指定，跳过类目匹配）")
    ap.add_argument("--type", default="", dest="tp", help="type_id")
    ap.add_argument("--title", default="", help="产品标题")
    ap.add_argument("--attrs", default="{}", help="1688 属性 JSON，如 '{\"颜色分类\":\"黑色\"}'")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()
    global _JSON_MODE
    _JSON_MODE = args.json
    if _JSON_MODE:
        _force_logs_to_stderr()

    cid, key = load_store_credentials(args.client_id, args.api_key, args.store, args.stores_file)
    if not cid or not key:
        print("❌ 凭证缺失：用 --store <店名/client_id> 或 --client-id/--api-key", file=sys.stderr)
        return 2

    try:
        draft_attrs = json.loads(args.attrs)
    except json.JSONDecodeError:
        print("❌ --attrs 不是合法 JSON", file=sys.stderr)
        return 2

    dc = args.dc
    tp = args.tp
    if not dc or not tp:
        print("⚠️ 未指定 --dc/--type，跳过类目匹配（试填校验需明确类目）", file=sys.stderr)
        return 2

    # 1. 拉真实 schema + 字典值
    _info(f"📥 拉取类目 schema: dc={dc} type={tp} ...")
    schema = fetch_schema(cid, key, int(dc), int(tp))
    if not schema:
        print("❌ schema 为空（类目无效或无属性）", file=sys.stderr)
        return 2
    dict_vals: Dict[str, list] = {}
    for a in schema:
        if int(a.get("dictionary_id") or 0) > 0:
            aid = int(a.get("id") or 0)
            dict_vals[str(aid)] = fetch_dict_values(cid, key, int(dc), int(tp), aid)
    _info(f"✅ schema {len(schema)} 属性, 字典属性 {len(dict_vals)} 个")

    # 2. 模拟填值
    draft = {"title": args.title, "attributes": draft_attrs, "supplier": ""}
    items = [{"offer_id": "offline-test", "name": args.title or "Test", "attributes": []}]
    state = OfflineState(cid, key, dc, tp, dict_vals)
    filled = simulate_fill(items, schema, draft, state)
    _info(f"✅ 模拟填值完成: 填了 {sum(len(i.get('attributes', [])) for i in filled)} 个属性")

    # 3. 校验
    errors = validate_items(filled, schema)
    _info(f"\n📋 模拟 Ozon 校验结果: {len(errors)} 个错误")
    for e in errors:
        _info(f"  [{e['code']}] {e['message']}")

    if args.json:
        print(json.dumps({
            "dc": dc, "type": tp,
            "schema_count": len(schema),
            "errors": errors,
            "filled_attrs": [
                {"id": a.get("id"), "values": a.get("values")}
                for i in filled for a in i.get("attributes", [])
            ],
        }, ensure_ascii=False, indent=1))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
