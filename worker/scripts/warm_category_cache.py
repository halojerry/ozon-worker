#!/usr/bin/env python3.12
"""
预热类目属性缓存 — 遍历所有 type 节点，从 Ozon API 获取 ZH_HANS 属性 schema 和字典值，
写入 PostgreSQL（运行时查询）和 JSON 文件（部署时自动导入）。

用法:
  python scripts/warm_category_cache.py [--limit N] [--offset N] [--export-only] [--pg-only]

  --limit N      只处理 N 个 type（测试用，默认全部）
  --offset N     从第 N 个开始（断点续传）
  --export-only  只导出 JSON 文件，不写 PG
  --pg-only      只写 PG，不导出 JSON 文件
  --force        强制刷新已有缓存
"""
import os
import sys
import json
import time
import argparse
import logging
from typing import Optional

# Ensure PYTHONPATH includes src
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("warm_cache")

# Ozon API credentials (from env, same as worker)
OZON_CLIENT_ID = os.getenv("OZON_CLIENT_ID_WARM", "") or os.getenv("OZON_CLIENT_ID", "")
OZON_API_KEY = os.getenv("OZON_API_KEY_WARM", "") or os.getenv("OZON_API_KEY", "")

# Fallback: hardcoded test store for development
if not OZON_CLIENT_ID or not OZON_API_KEY:
    OZON_CLIENT_ID = "5381204"
    OZON_API_KEY = "0b4d15cf-70a2-4505-9764-f64ac169b52f"
    logger.warning("⚠️ 使用默认测试店铺凭证，生产环境请设置 OZON_CLIENT_ID / OZON_API_KEY")

API_DELAY = 0.15  # 每个 API 调用后的延迟（秒），避免触发限流
ASSETS_DIR = os.path.join(os.path.dirname(__file__), "..", "assets")

# Files to export
SCHEMAS_FILE = os.path.join(ASSETS_DIR, "attribute_schemas_zh.json")
DICT_VALUES_FILE = os.path.join(ASSETS_DIR, "dictionary_values_zh.json")

import requests as _requests
_session = _requests.Session()


def _call_ozon_api(endpoint: str, payload: dict, timeout: int = 30) -> Optional[dict]:
    """调用 Ozon API，返回 JSON 响应"""
    headers = {
        "Client-Id": OZON_CLIENT_ID,
        "Api-Key": OZON_API_KEY,
        "Content-Type": "application/json",
    }
    url = f"https://api-seller.ozon.ru{endpoint}"
    try:
        resp = _session.post(url, json=payload, headers=headers, timeout=timeout)
        if resp.status_code == 200:
            return resp.json()
        elif resp.status_code == 429:
            logger.warning(f"   ⚠️ 限流 (429)，等待 5 秒...")
            time.sleep(5)
            return _call_ozon_api(endpoint, payload, timeout)
        else:
            logger.warning(f"   ⚠️ API {endpoint} 返回 {resp.status_code}: {resp.text[:200]}")
            return None
    except Exception as e:
        logger.warning(f"   ⚠️ API {endpoint} 异常: {e}")
        return None


def fetch_attribute_schema(dc: int, type_id: int) -> list[dict]:
    """获取类目的属性 schema (ZH_HANS)"""
    data = _call_ozon_api("/v1/description-category/attribute", {
        "description_category_id": dc,
        "type_id": type_id,
        "language": "ZH_HANS",
    })
    if data:
        return data.get("result", [])
    return []


def fetch_dict_values(attr_id: int, dc: int, type_id: int) -> list[dict]:
    """获取属性的字典值 (ZH_HANS)，支持分页"""
    all_values = []
    last_id = 0
    while True:
        payload = {
            "attribute_id": attr_id,
            "description_category_id": dc,
            "type_id": type_id,
            "language": "ZH_HANS",
            "limit": 1000,
        }
        if last_id > 0:
            payload["last_value_id"] = last_id

        data = _call_ozon_api("/v1/description-category/attribute/values", payload)
        if not data:
            break
        result = data.get("result", [])
        if not result:
            break
        all_values.extend(result)
        if not data.get("has_next", False):
            break
        last_id = result[-1].get("id", 0)
        time.sleep(API_DELAY)
    return all_values


def get_type_nodes(limit: Optional[int] = None, offset: Optional[int] = None) -> list[dict]:
    """从 PG 获取所有 type 节点"""
    from storage.database.db import get_session
    from sqlalchemy import text
    session = get_session()
    try:
        query = """
            SELECT DISTINCT description_category_id, type_id
            FROM category_tree_nodes
            WHERE node_type = 'type'
              AND type_id IS NOT NULL AND type_id > 0
              AND language = 'ZH_HANS'
            ORDER BY description_category_id, type_id
        """
        if limit is not None:
            query += f" LIMIT {int(limit)}"
        if offset is not None:
            query += f" OFFSET {int(offset)}"
        rows = session.execute(text(query)).mappings().all()
        return [{"description_category_id": r["description_category_id"], "type_id": r["type_id"]} for r in rows]
    finally:
        session.close()


def write_to_pg(schemas: dict, dict_values: dict):
    """写入 PG 缓存表"""
    from storage.database.db import get_session
    from sqlalchemy import text
    import time as _time

    now = int(_time.time())
    expires_7d = now + 7 * 86400  # schema 7天过期
    expires_1d = now + 86400      # 字典值 1天过期

    session = get_session()
    try:
        # 写入 attribute_cache
        count_schema = 0
        for key, val in schemas.items():
            dc_str, type_str = key.split(":", 1)
            dc = int(dc_str)
            tid = int(type_str)
            session.execute(text("""
                INSERT INTO attribute_cache (description_category_id, type_id, language, attributes_schema, expires_at, created_at)
                VALUES (:dc, :tid, 'ZH_HANS', :schema::jsonb, :expires, :now)
                ON CONFLICT (description_category_id, type_id, language)
                DO UPDATE SET attributes_schema = EXCLUDED.attributes_schema,
                              expires_at = EXCLUDED.expires_at,
                              created_at = EXCLUDED.created_at
            """), {"dc": dc, "tid": tid, "schema": json.dumps(val, ensure_ascii=False),
                   "expires": expires_7d, "now": now})
            count_schema += 1
        session.commit()
        logger.info(f"✅ PG 写入 attribute_cache: {count_schema} 条")

        # 写入 dictionary_value_cache
        count_dict = 0
        for key, val in dict_values.items():
            parts = key.split(":", 2)
            attr_id = int(parts[0])
            dc = int(parts[1])
            tid = int(parts[2])
            session.execute(text("""
                INSERT INTO dictionary_value_cache (attribute_id, description_category_id, type_id, language, values_data, expires_at, created_at)
                VALUES (:aid, :dc, :tid, 'ZH_HANS', :vals::jsonb, :expires, :now)
                ON CONFLICT (attribute_id, description_category_id, type_id, language)
                DO UPDATE SET values_data = EXCLUDED.values_data,
                              expires_at = EXCLUDED.expires_at,
                              created_at = EXCLUDED.created_at
            """), {"aid": attr_id, "dc": dc, "tid": tid, "vals": json.dumps(val, ensure_ascii=False),
                   "expires": expires_1d, "now": now})
            count_dict += 1
        session.commit()
        logger.info(f"✅ PG 写入 dictionary_value_cache: {count_dict} 条")
    except Exception as e:
        session.rollback()
        logger.error(f"❌ PG 写入失败: {e}")
        raise
    finally:
        session.close()


def export_to_files(schemas: dict, dict_values: dict):
    """导出为 JSON 文件（类目树同级目录）"""
    os.makedirs(ASSETS_DIR, exist_ok=True)

    with open(SCHEMAS_FILE, "w", encoding="utf-8") as f:
        json.dump(schemas, f, ensure_ascii=False, indent=2)
    logger.info(f"✅ 导出属性 schema: {SCHEMAS_FILE} ({len(schemas)} 个类目)")

    with open(DICT_VALUES_FILE, "w", encoding="utf-8") as f:
        json.dump(dict_values, f, ensure_ascii=False, indent=2)
    logger.info(f"✅ 导出字典值: {DICT_VALUES_FILE} ({len(dict_values)} 个条目)")


def import_from_files() -> tuple[dict, dict]:
    """从 JSON 文件导入（部署时 init_data.py 调用）"""
    schemas = {}
    dict_values = {}

    if os.path.exists(SCHEMAS_FILE):
        with open(SCHEMAS_FILE, "r", encoding="utf-8") as f:
            schemas = json.load(f)
        logger.info(f"📖 读取属性 schema: {SCHEMAS_FILE} ({len(schemas)} 个类目)")

    if os.path.exists(DICT_VALUES_FILE):
        with open(DICT_VALUES_FILE, "r", encoding="utf-8") as f:
            dict_values = json.load(f)
        logger.info(f"📖 读取字典值: {DICT_VALUES_FILE} ({len(dict_values)} 个条目)")

    return schemas, dict_values


def main():
    parser = argparse.ArgumentParser(description="预热 Ozon 类目属性缓存")
    parser.add_argument("--limit", type=int, default=None, help="只处理 N 个 type")
    parser.add_argument("--offset", type=int, default=None, help="从第 N 个开始")
    parser.add_argument("--export-only", action="store_true", help="只导出 JSON，不写 PG")
    parser.add_argument("--pg-only", action="store_true", help="只写 PG，不导出 JSON")
    parser.add_argument("--import-only", action="store_true", help="只从 JSON 文件导入 PG")
    parser.add_argument("--force", action="store_true", help="强制刷新已有缓存")
    args = parser.parse_args()

    # 仅导入模式：从 JSON 文件读取 → 写入 PG
    if args.import_only:
        schemas, dict_values = import_from_files()
        if schemas and dict_values:
            write_to_pg(schemas, dict_values)
        else:
            logger.error("❌ JSON 文件为空或不存在，无法导入")
            sys.exit(1)
        return

    # 获取所有 type 节点
    nodes = get_type_nodes(limit=args.limit, offset=args.offset)
    total = len(nodes)
    logger.info(f"📊 共 {total} 个 type 节点需要处理")

    schemas = {}
    dict_values = {}
    success = 0
    failed = 0

    for i, node in enumerate(nodes):
        dc = node["description_category_id"]
        tid = node["type_id"]
        key = f"{dc}:{tid}"

        # 跳过已缓存的（除非 --force）
        if not args.force:
            from storage.database.db import get_session
            from sqlalchemy import text
            s = get_session()
            try:
                existing = s.execute(text(
                    "SELECT 1 FROM attribute_cache WHERE description_category_id=:dc AND type_id=:tid AND language='ZH_HANS' AND expires_at > :now"
                ), {"dc": dc, "tid": tid, "now": int(time.time())}).fetchone()
                if existing:
                    logger.debug(f"   ⏭️ [{i+1}/{total}] {dc}/{tid} 已缓存，跳过")
                    s.close()
                    continue
            except Exception:
                pass
            finally:
                try: s.close()
                except: pass

        logger.info(f"   [{i+1}/{total}] {dc}/{tid} ...")

        try:
            # 获取属性 schema
            schema = fetch_attribute_schema(dc, tid)
            time.sleep(API_DELAY)

            if not schema:
                logger.warning(f"   ⚠️ [{i+1}/{total}] {dc}/{tid} schema 为空，跳过")
                failed += 1
                continue

            schemas[key] = schema

            # 获取字典值
            dict_attrs = [a for a in schema if a.get("dictionary_id", 0) > 0]
            for attr in dict_attrs:
                attr_id = int(attr["id"])
                dkey = f"{attr_id}:{dc}:{tid}"
                values = fetch_dict_values(attr_id, dc, tid)
                time.sleep(API_DELAY)
                if values:
                    dict_values[dkey] = values
                    logger.debug(f"      ✅ attr={attr_id}: {len(values)} 个字典值")

            success += 1
            logger.info(f"   ✅ [{i+1}/{total}] {dc}/{tid}: {len(schema)} 属性, {len(dict_attrs)} 字典属性")

        except Exception as e:
            logger.error(f"   ❌ [{i+1}/{total}] {dc}/{tid} 失败: {e}")
            failed += 1
            time.sleep(2)  # 出错后多等待一会

    # 写入 PG
    if not args.export_only and schemas:
        write_to_pg(schemas, dict_values)

    # 导出 JSON
    if not args.pg_only and schemas:
        export_to_files(schemas, dict_values)

    logger.info(f"\n🎉 完成！成功: {success}, 失败: {failed}")
    logger.info(f"   Schema: {len(schemas)} 个类目")
    logger.info(f"   字典值: {len(dict_values)} 个条目")


if __name__ == "__main__":
    main()
