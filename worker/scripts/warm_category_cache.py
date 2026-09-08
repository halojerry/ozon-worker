#!/usr/bin/env python3.12
"""
预热类目属性缓存 — 遍历所有 type 节点，从 Ozon API 获取 ZH_HANS 属性 schema 和字典值，
写入 PostgreSQL（运行时查询）和 JSON 文件（部署时自动导入）。

用法:
  python scripts/warm_category_cache.py [--limit N] [--all] [--offset N] [--export-only] [--pg-only]
                                        [--import-only] [--force] [--coverage] [--coverage-sample N]

  --limit N      只处理 N 个 type（测试用，默认全部）
  --all          显式全量预热（与不带 --limit 等价；与 --limit 互斥，同时给报错退出 2）。
                 全量 ~7400 类目约 16h，建议配合 --offset 分片跑（每 1000 个一段）
  --offset N     从第 N 个开始（断点续传）
  --export-only  只导出 JSON 文件，不写 PG（流式写，内存 O(单节点)）
  --pg-only      只写 PG，不导出 JSON 文件（逐节点小事务写，内存 O(单节点)）
  --import-only  只从 JSON 文件导入 PG（分批事务）
  --force        强制刷新已有缓存
  --coverage     只读审计：schema/字典值缓存对 ZH_HANS type 节点的覆盖率 + 缺失类目抽样
  --coverage-sample N   coverage 模式下随机抽 N 个缺失 (dc,tp) 打印（默认 0 不抽样）

⚠️ v1.1 修复（2026-08-01 云端崩溃根因）：
1. 不再全量攒内存 —— 原实现把全部类目的 schema/字典值堆积在内存
   （全量 ~600MB+，写入时 json.dumps 再复制一份 → 峰值 1.5GB+ OOM），
   且最后用单事务提交全部 → PG 内存暴涨/锁表 → 整个服务卡死。
2. 429 限流重试加次数上限 + 指数退避（原实现无限递归，0.05s 延迟 +
   3 并发 = 每秒 60 请求必然触发 429 风暴）。
3. 降低并发与延迟：max_workers=2, API_DELAY=0.3。
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
# ✅ v0.70: 移除硬编码测试店铺 fallback——凭证只能从环境变量来（代码不落 key）。
# 预热/导出模式在 main() 启动时校验；--coverage / --import-only 无需凭证。
OZON_CLIENT_ID = os.getenv("OZON_CLIENT_ID_WARM", "") or os.getenv("OZON_CLIENT_ID", "")
OZON_API_KEY = os.getenv("OZON_API_KEY_WARM", "") or os.getenv("OZON_API_KEY", "")

API_DELAY = 0.3   # 每个 API 调用后的延迟（秒）—— 0.05 太激进，3 并发时每秒 60 请求必触发 429
DICT_FETCH_WORKERS = 2  # 并发拉字典值线程数（3 → 2，配合延迟控制限流）
MAX_429_RETRIES = 3     # 429 最大重试次数（原实现无限递归）
ASSETS_DIR = os.path.join(os.path.dirname(__file__), "..", "assets")

# Files to export
SCHEMAS_FILE = os.path.join(ASSETS_DIR, "attribute_schemas_zh.json")
DICT_VALUES_FILE = os.path.join(ASSETS_DIR, "dictionary_values_zh.json")

import requests as _requests
_session = _requests.Session()


def _call_ozon_api(endpoint: str, payload: dict, timeout: int = 30, _retries: int = 0) -> Optional[dict]:
    """调用 Ozon API，返回 JSON 响应。

    429 限流：指数退避重试（最多 MAX_429_RETRIES 次），超限返回 None。
    """
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
            if _retries >= MAX_429_RETRIES:
                logger.warning(f"   ⚠️ 429 重试超过 {MAX_429_RETRIES} 次，放弃 {endpoint}")
                return None
            wait = 5 * (2 ** _retries)  # 5s → 10s → 20s
            logger.warning(f"   ⚠️ 限流 (429)，指数退避 {wait}s...")
            time.sleep(wait)
            return _call_ozon_api(endpoint, payload, timeout, _retries + 1)
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
            "limit": 5000,
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


def _write_node_to_pg(dc: int, tid: int, schema: list[dict], dict_values: dict, now: int):
    """写入单个节点的 schema + 字典值（小事务，逐节点 commit）。

    ⚠️ 必须逐节点小事务：原实现把全部数据塞一个事务，PG 内存暴涨
    锁表导致整个服务卡死（云端崩溃根因）。
    """
    from storage.database.db import get_session
    from sqlalchemy import text

    # ✅ v0.70: 30 天 TTL——schema/字典值低频变化（Ozon 类目结构月级稳定），
    # 1 天字典 TTL 使「全量预热」一周内自动衰减回懒加载（全量化策略见
    # docs/CACHE-WARM-RUNBOOK.md），warm/init_data/local_db_manager 三处一致。
    expires_schema = now + 30 * 86400  # schema 30 天过期
    expires_dict = now + 30 * 86400    # 字典值 30 天过期

    session = get_session()
    try:
        session.execute(text("""
            INSERT INTO attribute_cache (description_category_id, type_id, language, attributes_schema, expires_at, created_at)
            VALUES (:dc, :tid, 'ZH_HANS', :schema::jsonb, :expires, :now)
            ON CONFLICT (description_category_id, type_id, language)
            DO UPDATE SET attributes_schema = EXCLUDED.attributes_schema,
                          expires_at = EXCLUDED.expires_at,
                          created_at = EXCLUDED.created_at
        """), {"dc": dc, "tid": tid, "schema": json.dumps(schema, ensure_ascii=False),
               "expires": expires_schema, "now": now})

        for key, val in dict_values.items():
            parts = key.split(":", 2)
            attr_id = int(parts[0])
            session.execute(text("""
                INSERT INTO dictionary_value_cache (attribute_id, description_category_id, type_id, language, values_data, expires_at, created_at)
                VALUES (:aid, :dc, :tid, 'ZH_HANS', :vals::jsonb, :expires, :now)
                ON CONFLICT (attribute_id, description_category_id, type_id, language)
                DO UPDATE SET values_data = EXCLUDED.values_data,
                              expires_at = EXCLUDED.expires_at,
                              created_at = EXCLUDED.created_at
            """), {"aid": attr_id, "dc": dc, "tid": tid, "vals": json.dumps(val, ensure_ascii=False),
                   "expires": expires_dict, "now": now})

        session.commit()
    except Exception as e:
        session.rollback()
        logger.error(f"❌ PG 写入失败 {dc}/{tid}: {e}")
        raise
    finally:
        session.close()


def write_to_pg(schemas: dict, dict_values: dict, batch: int = 200):
    """批量写入 PG（分批事务，兼容 import_only / 小批量场景）。

    ⚠️ 每 batch 条 commit 一次，禁止单事务提交全部（PG 卡死根因）。
    """
    from storage.database.db import get_session
    from sqlalchemy import text
    import time as _time

    now = int(_time.time())
    expires_schema = now + 30 * 86400  # v0.70: 30 天（与 _write_node_to_pg 一致）
    expires_dict = now + 30 * 86400

    session = get_session()
    try:
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
                   "expires": expires_schema, "now": now})
            count_schema += 1
            if count_schema % batch == 0:
                session.commit()
                logger.info(f"   ⏱️ attribute_cache 已提交 {count_schema} 条")
        session.commit()
        logger.info(f"✅ PG 写入 attribute_cache: {count_schema} 条")

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
                   "expires": expires_dict, "now": now})
            count_dict += 1
            if count_dict % batch == 0:
                session.commit()
                logger.info(f"   ⏱️ dictionary_value_cache 已提交 {count_dict} 条")
        session.commit()
        logger.info(f"✅ PG 写入 dictionary_value_cache: {count_dict} 条")
    except Exception as e:
        session.rollback()
        logger.error(f"❌ PG 写入失败: {e}")
        raise
    finally:
        session.close()


class _JsonStreamWriter:
    """流式 JSON 对象写入器：逐 key 写入，内存 O(单条 value)。

    全量导出 ~600MB 时原实现 json.dump 整体序列化（内存峰值 2x），
    流式写只占单节点大小。
    """

    def __init__(self, path: str):
        self.path = path
        self.f = open(path, "w", encoding="utf-8")
        self.f.write("{")
        self._first = True

    def write(self, key: str, value):
        if not self._first:
            self.f.write(",")
        self.f.write(json.dumps(key, ensure_ascii=False))
        self.f.write(":")
        for chunk in json.JSONEncoder(ensure_ascii=False, separators=(",", ":")).iterencode(value):
            self.f.write(chunk)
        self._first = False

    def close(self):
        self.f.write("}")
        self.f.close()


def export_to_files(schemas: dict, dict_values: dict):
    """导出为 JSON 文件（流式写，避免全量内存）。"""
    os.makedirs(ASSETS_DIR, exist_ok=True)

    w = _JsonStreamWriter(SCHEMAS_FILE)
    for k, v in schemas.items():
        w.write(k, v)
    w.close()
    logger.info(f"✅ 导出属性 schema: {SCHEMAS_FILE} ({len(schemas)} 个类目)")

    w2 = _JsonStreamWriter(DICT_VALUES_FILE)
    for k, v in dict_values.items():
        w2.write(k, v)
    w2.close()
    logger.info(f"✅ 导出字典值: {DICT_VALUES_FILE} ({len(dict_values)} 个条目)")


def import_from_files() -> tuple[dict, dict]:
    """从 JSON 文件导入（部署时 init_data.py 调用）。

    ⚠️ 全量文件 ~600MB，json.load 会占 ~1.2GB 内存；部署导入为低频
    一次性操作，可接受；写入走 write_to_pg 分批事务。
    """
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


def build_arg_parser() -> argparse.ArgumentParser:
    """✅ v0.69 T3.3: argparse 构造独立成函数（可单测），参数语义不变 + 新增 --all/--coverage。"""
    parser = argparse.ArgumentParser(description="预热 Ozon 类目属性缓存")
    parser.add_argument("--limit", type=int, default=None, help="只处理 N 个 type")
    parser.add_argument("--all", action="store_true",
                        help="显式全量预热（等价于不带 --limit；全量 ~16h，建议配合 --offset 分片）")
    parser.add_argument("--offset", type=int, default=None, help="从第 N 个开始")
    parser.add_argument("--export-only", action="store_true", help="只导出 JSON，不写 PG")
    parser.add_argument("--pg-only", action="store_true", help="只写 PG，不导出 JSON")
    parser.add_argument("--import-only", action="store_true", help="只从 JSON 文件导入 PG")
    parser.add_argument("--coverage", action="store_true",
                        help="只读审计：schema/字典值缓存覆盖率 + 缺失类目抽样，不发任何 Ozon API 请求")
    parser.add_argument("--coverage-sample", type=int, default=0,
                        help="coverage 模式下随机抽 N 个缺失 (dc,tp) 打印")
    parser.add_argument("--force", action="store_true", help="强制刷新已有缓存")
    return parser


def parse_args(argv: Optional[list] = None):
    """解析参数；--all 与 --limit 互斥（同时给 → argparse error，退出码 2）。"""
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    if args.all and args.limit is not None:
        parser.error("--all 与 --limit 互斥（--all 即全量，无需 limit；分片请用 --offset）")
    return args


def resolve_limit(all_flag: bool, limit: Optional[int]) -> Optional[int]:
    """✅ v0.69 T3.3: --all → None（全量，对齐 AGENTS.md 文档语义）；否则保持原语义。"""
    return None if all_flag else limit


def collect_coverage(now: Optional[int] = None) -> dict:
    """✅ v0.69 T3.3: 只读统计 schema/字典值缓存覆盖率（纯查询，零写入零 Ozon API）。

    口径（与 get_type_nodes 的预热人口完全一致）：
    - 总数 = category_tree_nodes 中 node_type='type' AND type_id>0 AND language='ZH_HANS'
      的 DISTINCT (dc,tp)
    - 覆盖 = 各缓存表中 language='ZH_HANS' 且未过期（expires_at > now）的 DISTINCT (dc,tp)
      与总数的交集（过期行不计入覆盖——运行时读不到，计入会虚高）
    """
    from storage.database.db import get_session
    from sqlalchemy import text
    now = int(now or time.time())
    session = get_session()
    try:
        total_rows = session.execute(text("""
            SELECT description_category_id, type_id
            FROM category_tree_nodes
            WHERE node_type = 'type'
              AND type_id IS NOT NULL AND type_id > 0
              AND language = 'ZH_HANS'
            GROUP BY description_category_id, type_id
        """)).fetchall()
        schema_rows = session.execute(text("""
            SELECT DISTINCT description_category_id, type_id
            FROM attribute_cache
            WHERE language = 'ZH_HANS' AND expires_at > :now
        """), {"now": now}).fetchall()
        dict_rows = session.execute(text("""
            SELECT DISTINCT description_category_id, type_id
            FROM dictionary_value_cache
            WHERE language = 'ZH_HANS' AND expires_at > :now
        """), {"now": now}).fetchall()
    finally:
        session.close()

    total_pairs = {(int(r[0]), int(r[1])) for r in total_rows}
    schema_pairs = {(int(r[0]), int(r[1])) for r in schema_rows}
    dict_pairs = {(int(r[0]), int(r[1])) for r in dict_rows}
    covered_schema = total_pairs & schema_pairs
    covered_dict = total_pairs & dict_pairs
    total = len(total_pairs)
    return {
        "total": total,
        "schema_covered": len(covered_schema),
        "dict_covered": len(covered_dict),
        "schema_pct": round(len(covered_schema) * 100.0 / total, 1) if total else 0.0,
        "dict_pct": round(len(covered_dict) * 100.0 / total, 1) if total else 0.0,
        "missing_pairs": sorted(total_pairs - covered_schema),
        "schema_covered_pairs": covered_schema,
        "dict_covered_pairs": covered_dict,
    }


def format_coverage_report(report: dict) -> str:
    """人话输出；最后一行固定为机器可读摘要 `COVERAGE schema=x/y(z%) dict=a/b(c%)`。"""
    summary = (
        f"COVERAGE schema={report['schema_covered']}/{report['total']}({report['schema_pct']}%) "
        f"dict={report['dict_covered']}/{report['total']}({report['dict_pct']}%)"
    )
    lines = [
        f"📊 ZH_HANS type 类目总数: {report['total']}",
        f"   attribute_cache schema 覆盖: {report['schema_covered']}/{report['total']} ({report['schema_pct']}%)",
        f"   dictionary_value_cache 覆盖: {report['dict_covered']}/{report['total']} ({report['dict_pct']}%)",
        f"   缺失 schema 的类目数: {len(report['missing_pairs'])}",
        summary,
    ]
    return "\n".join(lines)


def run_coverage_report(sample: int = 0) -> None:
    """--coverage 模式入口：打印覆盖率 + 可选缺失抽样（摘要行恒为最后一行）。"""
    report = collect_coverage()
    lines = format_coverage_report(report).splitlines()
    # 抽样块插在摘要行之前，保证 COVERAGE 摘要恒为最后一行
    summary = lines[-1]
    body = lines[:-1]
    if sample > 0 and report["missing_pairs"]:
        import random
        picked = random.sample(report["missing_pairs"], min(sample, len(report["missing_pairs"])))
        body.append(f"   🔍 缺失抽样 {len(picked)}/{len(report['missing_pairs'])} 个 (dc/tp):")
        body.extend(f"      - {dc}/{tp}" for dc, tp in picked)
    body.append(summary)
    print("\n".join(body))


def main():
    args = parse_args()

    # ✅ v0.69 T3.3: 覆盖率审计模式（纯只读，不碰 Ozon API 不写 PG）
    if args.coverage:
        run_coverage_report(sample=args.coverage_sample)
        return

    # ✅ v0.69 T3.3: --all → 全量（limit=None）；冲突已在 parse_args 拒绝
    limit = resolve_limit(args.all, args.limit)

    # ✅ v0.70: 预热/导出模式需要 Ozon 凭证（缺失显式退出，不再静默落硬编码店）
    if not OZON_CLIENT_ID or not OZON_API_KEY:
        logger.error(
            "❌ 预热/导出需要 Ozon 凭证：export OZON_CLIENT_ID=<id> OZON_API_KEY=<key> "
            "（--coverage / --import-only 模式无需凭证）")
        sys.exit(1)

    # 仅导入模式：从 JSON 文件读取 → 写入 PG（分批事务）
    if args.import_only:
        schemas, dict_values = import_from_files()
        if schemas or dict_values:
            write_to_pg(schemas, dict_values)
        else:
            logger.error("❌ JSON 文件为空或不存在，无法导入")
            sys.exit(1)
        return

    # 获取所有 type 节点
    nodes = get_type_nodes(limit=limit, offset=args.offset)
    total = len(nodes)
    logger.info(f"📊 共 {total} 个 type 节点需要处理")

    success = 0
    failed = 0
    now = int(time.time())

    # 导出模式才需要攒全量内存（流式写文件降低峰值）；PG 模式逐节点小事务
    schemas = {} if args.export_only else None
    dict_values = {} if args.export_only else None

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
                ), {"dc": dc, "tid": tid, "now": now}).fetchone()
                if existing:
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

            # 获取字典值（并发获取，延迟受控）
            dict_attrs = [a for a in schema if a.get("dictionary_id", 0) > 0]
            node_dict_values: dict = {}
            if dict_attrs:
                from concurrent.futures import ThreadPoolExecutor, as_completed
                def _fetch_one_dict(attr):
                    attr_id = int(attr["id"])
                    dkey = f"{attr_id}:{dc}:{tid}"
                    vals = fetch_dict_values(attr_id, dc, tid)
                    time.sleep(API_DELAY)
                    return dkey, vals
                with ThreadPoolExecutor(max_workers=DICT_FETCH_WORKERS) as pool:
                    futures = {pool.submit(_fetch_one_dict, a): a for a in dict_attrs}
                    for fut in as_completed(futures):
                        try:
                            dkey, vals = fut.result()
                            if vals:
                                node_dict_values[dkey] = vals
                        except Exception as _de:
                            logger.debug(f"      ⚠️ 字典值获取失败: {_de}")

            # ── 立即写 PG（小事务，不攒内存）──
            if not args.export_only:
                _write_node_to_pg(dc, tid, schema, node_dict_values, now)

            # 导出模式：收进内存（最后流式导出）
            if args.export_only:
                schemas[key] = schema
                dict_values.update(node_dict_values)

            success += 1
            if success % 50 == 0:
                logger.info(f"   ⏱️ 进度: {i+1}/{total} 成功 {success} 失败 {failed}")

        except Exception as e:
            logger.error(f"   ❌ [{i+1}/{total}] {dc}/{tid} 失败: {e}")
            failed += 1
            time.sleep(2)  # 出错后多等待一会

    # 导出 JSON（仅 export-only，流式写）
    if args.export_only and schemas:
        export_to_files(schemas, dict_values)

    logger.info(f"\n🎉 完成！成功: {success}, 失败: {failed}")
    logger.info(f"   PG 模式: 已逐节点写入 | 导出模式: Schema {len(schemas or {})} 个类目, 字典值 {len(dict_values or {})} 个条目")


if __name__ == "__main__":
    main()
