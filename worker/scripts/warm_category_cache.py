#!/usr/bin/env python3.12
"""
预热类目属性缓存 — 遍历所有 type 节点，从 Ozon API 获取 ZH_HANS 属性 schema 和字典值，
写入 PostgreSQL（运行时查询）和 JSON 文件（部署时自动导入）。

用法:
  python scripts/warm_category_cache.py [--limit N] [--all] [--offset N] [--export-only] [--pg-only]
                                        [--import-only] [--force] [--coverage] [--coverage-sample N]
                                        [--export-from-pg] [--export-schema-manifest PATH]

  --limit N      只处理 N 个 type（测试用，默认全部）
  --all          显式全量预热（与不带 --limit 等价；与 --limit 互斥，同时给报错退出 2）。
                 全量 ~7400 类目约 16h，建议配合 --offset 分片跑（每 1000 个一段）
  --offset N     从第 N 个开始（断点续传）
  --export-only  只导出 JSON 文件，不写 PG（流式写，内存 O(单节点)）
  --pg-only      只写 PG，不导出 JSON 文件（逐节点小事务写，内存 O(单节点)）
  --import-only  只从 JSON 文件导入 PG（分批事务）
  --export-from-pg 只从 PG 缓存导出 JSON（✅ v0.73 W2：不调 Ozon API、无需凭证；
                 runbook「预热→导出→上 COS」的导出环节——此前 --export-only 是
                 「边拉边导」语义，单独跑只导本次进程内拉取的部分）
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

import requests as _requests

# Ensure PYTHONPATH includes src
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("warm_cache")

# Ozon API credentials (from env, same as worker)
# ✅ v0.70: 移除硬编码测试店铺 fallback——凭证只能从环境变量来（代码不落 key）。
# 预热/导出模式在 main() 启动时校验；--coverage / --import-only / --export-from-pg 无需凭证。
OZON_CLIENT_ID = os.getenv("OZON_CLIENT_ID_WARM", "") or os.getenv("OZON_CLIENT_ID", "")
OZON_API_KEY = os.getenv("OZON_API_KEY_WARM", "") or os.getenv("OZON_API_KEY", "")

API_DELAY = 0.3   # 每个 API 调用后的延迟（秒）—— 0.05 太激进，3 并发时每秒 60 请求必触发 429
DICT_FETCH_WORKERS = 2  # 并发拉字典值线程数（3 → 2，配合延迟控制限流）
MAX_429_RETRIES = 3     # 429 最大重试次数（原实现无限递归）
ASSETS_DIR = os.path.join(os.path.dirname(__file__), "..", "assets")

# ✅ v0.72 三桶策略 + 防复发守卫（字典缓存撑爆 40G 盘事故）：
# - 全局桶 (cat_dep=false) 每次运行只拉一次/写一份（seen-set 跨节点复用）
# - 巨型字典（首页即 has_next）ephemeral 不物化
# - 磁盘余量守卫：起步 + 每 50 节点检查，<5G 自动中止
_MIN_DISK_FREE_GB = 5.0
_global_dict_seen: set = set()


def _disk_free_gb() -> float:
    """容器根文件系统剩余空间（GB）。docker 卷与容器同宿主盘，可作为代理指标。"""
    import shutil
    try:
        return shutil.disk_usage("/").free / 2**30
    except Exception:
        return 999.0  # 检查失败不阻断（宁误跑勿误停）


def _disk_guard(context: str) -> bool:
    """磁盘余量守卫：<5G 返回 False（调用方中止预热）。"""
    free = _disk_free_gb()
    if free < _MIN_DISK_FREE_GB:
        logger.error(
            f"🛑 磁盘余量守卫触发（{context}）：仅剩 {free:.1f}G < {_MIN_DISK_FREE_GB}G，"
            "自动中止预热——先清理磁盘（见 docs/CACHE-WARM-RUNBOOK.md）再续跑 --offset")
        return False
    return True


# Files to export
SCHEMAS_FILE = os.path.join(ASSETS_DIR, "attribute_schemas_zh.json")
DICT_VALUES_FILE = os.path.join(ASSETS_DIR, "dictionary_values_zh.json")

_session = _requests.Session()


def _call_ozon_api_status(endpoint: str, payload: dict, timeout: int = 30,
                         _retries: int = 0) -> tuple[Optional[dict], int]:
    """v0.73 W3: 带状态码版本——400/404=类目永久失效（供死节点表），与瞬态故障区分。"""
    headers = {
        "Client-Id": OZON_CLIENT_ID,
        "Api-Key": OZON_API_KEY,
        "Content-Type": "application/json",
    }
    url = f"https://api-seller.ozon.ru{endpoint}"
    try:
        resp = _session.post(url, json=payload, headers=headers, timeout=timeout)
        if resp.status_code == 200:
            return resp.json(), 200
        if resp.status_code == 429 and _retries < MAX_429_RETRIES:
            wait = 5 * (2 ** _retries)
            logger.warning(f"   ⚠️ 限流 (429)，指数退避 {wait}s...")
            time.sleep(wait)
            return _call_ozon_api_status(endpoint, payload, timeout, _retries + 1)
        logger.warning(f"   ⚠️ API {endpoint} 返回 {resp.status_code}: {resp.text[:200]}")
        return None, resp.status_code
    except Exception as e:
        logger.warning(f"   ⚠️ API {endpoint} 异常: {e}")
        return None, 0


def _call_ozon_api(endpoint, payload, timeout=30, _retries=0):
    """调用 Ozon API，返回 JSON 响应（签名/语义不变；✅ v0.73 W3 委托状态化版本）。

    429 限流：指数退避重试（最多 MAX_429_RETRIES 次），超限返回 None。
    """
    data, _status = _call_ozon_api_status(endpoint, payload, timeout, _retries)
    return data


def _fetch_attribute_schema_with_status(dc: int, type_id: int) -> tuple[list[dict], int]:
    """获取类目属性 schema 并外露 HTTP 状态码（✅ v0.73 W3：400/404 → 主循环标死节点）。"""
    data, status = _call_ozon_api_status("/v1/description-category/attribute", {
        "description_category_id": dc, "type_id": type_id, "language": "ZH_HANS",
    })
    if data:
        return data.get("result", []), status
    return [], status


def fetch_attribute_schema(dc: int, type_id: int) -> list[dict]:
    """获取类目的属性 schema (ZH_HANS)。✅ v0.73 W3: 委托状态化版本（签名/语义不变）。"""
    return _fetch_attribute_schema_with_status(dc, type_id)[0]


def fetch_dict_values(attr_id: int, dc: int, type_id: int,
                      max_pages: Optional[int] = None) -> tuple[list[dict], bool]:
    """获取属性的字典值 (ZH_HANS)，支持分页。返回 (values, truncated)。

    ✅ v0.72 三桶策略：limit 5000→2000（Ozon 契约 max=2000，5000 被静默钳）；
    max_pages=1 为「首页探测」——返回 truncated=True 表示字典 >2000 值
    （ephemeral，调用方跳过物化，不再翻页——品牌类无底洞的刹车）。
    """
    all_values: list[dict] = []
    last_id = 0
    page = 0
    while True:
        page += 1
        payload = {
            "attribute_id": attr_id,
            "description_category_id": dc,
            "type_id": type_id,
            "language": "ZH_HANS",
            "limit": 2000,
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
            return all_values, False
        if max_pages is not None and page >= max_pages:
            return all_values, True  # 首页即翻页 → ephemeral
        last_id = result[-1].get("id", 0)
        time.sleep(API_DELAY)
    return all_values, False


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
            VALUES (:dc, :tid, 'ZH_HANS', CAST(:schema AS jsonb), :expires, :now)
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
                VALUES (:aid, :dc, :tid, 'ZH_HANS', CAST(:vals AS jsonb), :expires, :now)
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
                VALUES (:dc, :tid, 'ZH_HANS', CAST(:schema AS jsonb), :expires, :now)
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
                VALUES (:aid, :dc, :tid, 'ZH_HANS', CAST(:vals AS jsonb), :expires, :now)
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


def export_from_pg() -> None:
    """v0.73 W2: --export-from-pg——从 PG 缓存导出 JSON（不调 Ozon API、无需凭证）。

    此前 --export-only 是「边拉边导」语义（v1.1 OOM 修复引入），预热完成后
    单独跑只会导出本次进程内 API 拉取的部分；runbook「预热→导出→上 COS」
    需要的是把 PG 里已预热的数据落 JSON，本函数补上这一环（流式，内存 O(单行)）。
    """
    from storage.database.db import get_session
    from sqlalchemy import text
    os.makedirs(ASSETS_DIR, exist_ok=True)
    now = int(time.time())
    session = get_session()
    try:
        w = _JsonStreamWriter(SCHEMAS_FILE)
        # stream_results=True：psycopg2 服务端游标真流式——不加则 execute() 时整个
        # 结果集已物化到客户端内存，fetchmany 只是切片（v1.1 OOM 事故同形态）
        rows = session.execute(text(
            "SELECT description_category_id, type_id, attributes_schema "
            "FROM attribute_cache WHERE language='ZH_HANS' AND expires_at > :now "
            "ORDER BY description_category_id, type_id"
        ).execution_options(stream_results=True), {"now": now}).mappings()
        n = 0
        while chunk := rows.fetchmany(500):
            for r in chunk:
                w.write(f"{int(r['description_category_id'])}:{int(r['type_id'])}",
                        r["attributes_schema"])
                n += 1
        w.close()
        logger.info(f"✅ [export-from-pg] 属性 schema: {SCHEMAS_FILE} ({n} 个类目)")

        w2 = _JsonStreamWriter(DICT_VALUES_FILE)
        rows = session.execute(text(
            "SELECT attribute_id, description_category_id, type_id, values_data "
            "FROM dictionary_value_cache WHERE language='ZH_HANS' AND expires_at > :now "
            "ORDER BY attribute_id, description_category_id, type_id"
        ).execution_options(stream_results=True), {"now": now}).mappings()
        n = 0
        while chunk := rows.fetchmany(500):
            for r in chunk:
                w2.write(f"{int(r['attribute_id'])}:{int(r['description_category_id'])}:"
                         f"{int(r['type_id'])}", r["values_data"])
                n += 1
        w2.close()
        logger.info(f"✅ [export-from-pg] 字典值: {DICT_VALUES_FILE} ({n} 个条目)")
    finally:
        session.close()


# ── BL-19（A4 F-P0-1 资产化）：--export-schema-manifest ──
# 从 PG attribute_cache 全表导出「类目 schema 清单」JSON 资产——每类目一行的
# 必填/字典/集合/数值/值数上限聚合面，支撑类目维度规则地图与 A4 §5 抽样回填。
# 与预热完全解耦：单独 flag 可用，不触发 warm 循环、不调 Ozon API、无需凭证。
# ⚠️ schema 行字段口径以 docs/audit/2026-09-11-repo-gov/A4-category-attribute-mapping.md
#    §2.1 消费矩阵为准：本清单只消费已消费字段（id/is_required/dictionary_id/
#    is_collection/max_value_count/type）；description/group_*/complex_* 未消费不进清单。


def build_schema_manifest_row(dc: int, tp: int, language: str, schema) -> Optional[dict]:
    """单类目 schema（attribute_cache.attributes_schema）→ 清单行（纯函数）。

    - schema 空（None/非 list/[]）或没有任何可归类属性行 → None（调用方跳过该类目）。
    - 属性行缺 id / id 非整数 → 跳过该属性（id 是全链主键，缺失无法归类）。
    - 数值判定复用唯一入口 utils.attr_numeric_sanitize.is_numeric_attr_type
      （大小写不敏感 integer/int/decimal/number/float/double）——禁止内联第二套名单。
    - max_value_count_max：各属性行 max_value_count 的最大值；全缺省 → 0。
    """
    if not isinstance(schema, list) or not schema:
        return None
    from utils.attr_numeric_sanitize import is_numeric_attr_type

    required: list = []
    dict_attrs: list = []
    collection_attrs: list = []
    numeric_attrs: list = []
    max_vcm = 0
    attr_total = 0
    for row in schema:
        if not isinstance(row, dict):
            continue
        try:
            attr_id = int(row.get("id"))
        except (TypeError, ValueError):
            continue
        attr_total += 1
        if row.get("is_required"):
            required.append(attr_id)
        try:
            if int(row.get("dictionary_id") or 0) > 0:
                dict_attrs.append(attr_id)
        except (TypeError, ValueError):
            pass
        if row.get("is_collection"):
            collection_attrs.append(attr_id)
        if is_numeric_attr_type(row.get("type")):
            numeric_attrs.append(attr_id)
        mvc = row.get("max_value_count")
        if isinstance(mvc, (int, float)) and not isinstance(mvc, bool) and mvc > max_vcm:
            max_vcm = int(mvc)
    if attr_total == 0:
        return None
    return {
        "dc": int(dc),
        "tp": int(tp),
        "language": language,
        "attr_total": attr_total,
        "required": sorted(required),
        "dict_attrs": sorted(dict_attrs),
        "collection_attrs": sorted(collection_attrs),
        "numeric_attrs": sorted(numeric_attrs),
        "max_value_count_max": max_vcm,
    }


def _atomic_write_json(path: str, payload) -> None:
    """UTF-8 原子写（tmp + os.replace），防半截文件被部署链灌回。"""
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    tmp = f"{path}.tmp.{os.getpid()}"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


MANIFEST_SQL = (
    "SELECT description_category_id, type_id, language, attributes_schema "
    "FROM attribute_cache "
    "ORDER BY description_category_id, type_id, language"
)
# ✅ 纯 SELECT 零绑定参数（记忆 sqlalchemy-jsonb-cast-trap 红线自查通过）：
# jsonb 列由 psycopg2 自动适配为 Python list，无需也不得做任何 bind cast。


def export_schema_manifest(path: str, session=None) -> int:
    """--export-schema-manifest 入口：全表读 attribute_cache → 清单 JSON。

    流式读（stream_results，内存 O(单行)）；空 schema 行跳过并计数。
    返回写入的类目行数。session 参数供测试注入 mock（缺省自建）。
    """
    owned = session is None
    if owned:
        from storage.database.db import get_session
        session = get_session()
    from sqlalchemy import text as _text
    rows_out: list = []
    skipped = 0
    try:
        cursor = session.execute(_text(MANIFEST_SQL)
                                 .execution_options(stream_results=True)).mappings()
        while chunk := cursor.fetchmany(500):
            for r in chunk:
                row = build_schema_manifest_row(
                    r["description_category_id"], r["type_id"],
                    r["language"], r["attributes_schema"])
                if row is None:
                    skipped += 1
                    continue
                rows_out.append(row)
    finally:
        if owned:
            session.close()
    manifest = {
        "generated_at": int(time.time()),
        "total_categories": len(rows_out),
        "skipped_empty_schema": skipped,
        "categories": rows_out,
    }
    _atomic_write_json(path, manifest)
    logger.info(
        f"✅ [export-schema-manifest] 类目 schema 清单: {path} "
        f"({len(rows_out)} 个类目, 跳过空 schema {skipped} 行)")
    return len(rows_out)


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
    parser.add_argument("--export-from-pg", action="store_true",
                        help="只从 PG 缓存导出 JSON（不调 Ozon API、无需凭证；与其他模式互斥）")
    parser.add_argument("--export-schema-manifest", metavar="PATH", default=None,
                        help="BL-19: 从 PG attribute_cache 全表导出类目 schema 清单 JSON "
                             "（纯只读，不调 Ozon API、无需凭证；独立模式与其他模式互斥）")
    parser.add_argument("--force", action="store_true", help="强制刷新已有缓存")
    return parser


def parse_args(argv: Optional[list] = None):
    """解析参数；互斥校验：--all×--limit、--export-from-pg×其他模式（违规 → 退出码 2）。"""
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    if args.all and args.limit is not None:
        parser.error("--all 与 --limit 互斥（--all 即全量，无需 limit；分片请用 --offset）")
    # ✅ v0.73 W2: --export-from-pg 是独立只读导出模式，与任何预热/导入/审计模式互斥
    # ✅ BL-19: --export-schema-manifest 同为独立只读模式，冲突面一致
    for solo_flag, solo_set in (
        ("--export-from-pg", args.export_from_pg),
        ("--export-schema-manifest", args.export_schema_manifest is not None),
    ):
        if not solo_set:
            continue
        conflicts = []
        if solo_flag == "--export-schema-manifest" and args.export_from_pg:
            conflicts.append("--export-from-pg")
        if solo_flag == "--export-from-pg" and args.export_schema_manifest is not None:
            conflicts.append("--export-schema-manifest")
        if args.export_only:
            conflicts.append("--export-only")
        if args.pg_only:
            conflicts.append("--pg-only")
        if args.import_only:
            conflicts.append("--import-only")
        if args.coverage:
            conflicts.append("--coverage")
        if args.all:
            conflicts.append("--all")
        if args.limit is not None:
            conflicts.append("--limit")
        if args.offset is not None:
            conflicts.append("--offset")
        if args.force:
            conflicts.append("--force")
        if conflicts:
            parser.error(f"{solo_flag} 是独立只读模式，与 {', '.join(conflicts)} 互斥")
    return args


def resolve_limit(all_flag: bool, limit: Optional[int]) -> Optional[int]:
    """✅ v0.69 T3.3: --all → None（全量，对齐 AGENTS.md 文档语义）；否则保持原语义。"""
    return None if all_flag else limit


DEAD_NODES_DDL = """
CREATE TABLE IF NOT EXISTS warm_dead_nodes (
    description_category_id BIGINT NOT NULL,
    type_id BIGINT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    created_at BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (description_category_id, type_id)
)"""
# v0.73 W3: Ozon 已删类目（400 category not found）永久跳过——此前每次预热恒计失败，
# coverage 分母含死节点 → 100% 数学不可达（服务器被迫把看护阈值调 7350）。


def ensure_dead_nodes_table(session):
    from sqlalchemy import text
    session.execute(text(DEAD_NODES_DDL))
    session.commit()


def load_dead_nodes(session) -> set:
    from sqlalchemy import text
    rows = session.execute(text(
        "SELECT description_category_id, type_id FROM warm_dead_nodes")).fetchall()
    return {(int(r[0]), int(r[1])) for r in rows}


def mark_dead_node(session, dc: int, tid: int, reason: str):
    from sqlalchemy import text
    session.execute(text(
        "INSERT INTO warm_dead_nodes (description_category_id, type_id, reason, created_at) "
        "VALUES (:dc, :tid, :reason, :now) ON CONFLICT DO NOTHING"),
        {"dc": dc, "tid": tid, "reason": reason[:200], "now": int(time.time())})
    session.commit()


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
        # ✅ v0.73 W3: 死节点（Ozon 已删类目）从分母剔除；仅「表不存在」（未跑过 warm 的库）
        # 视为空——其余 DB 异常如实上抛（收窄 except：静默吞掉会拿假分母出报告）
        from sqlalchemy.exc import ProgrammingError
        try:
            dead_pairs = load_dead_nodes(session)
        except ProgrammingError:
            dead_pairs = set()
    finally:
        session.close()

    total_pairs = {(int(r[0]), int(r[1])) for r in total_rows}
    total_pairs -= dead_pairs
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
        # 死节点表整表规模（生产中死节点恒为真实树节点，等价于分母实际缩减量；
        # 用整表计数使哨兵/负数测试行也可观测）
        "dead_excluded": len(dead_pairs),
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
        f"   已剔除失效类目(400): {report.get('dead_excluded', 0)}",
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

    # ✅ BL-19: 类目 schema 清单导出（独立只读模式，与 --export-from-pg 同位、无需凭证）
    if args.export_schema_manifest:
        export_schema_manifest(args.export_schema_manifest)
        return

    # ✅ v0.73 W2: --export-from-pg 纯 PG 读导出（无需 Ozon 凭证，置于凭证检查前）
    if args.export_from_pg:
        export_from_pg()
        return

    # ✅ v0.69 T3.3: --all → 全量（limit=None）；冲突已在 parse_args 拒绝
    limit = resolve_limit(args.all, args.limit)

    # 仅导入模式：从 JSON 文件读取 → 写入 PG（分批事务）
    # ✅ v0.73: 上移到凭证检查之前——原顺序使 --import-only 无凭证会误退出，
    # 与文件头凭证注释「--coverage / --import-only / --export-from-pg 无需凭证」矛盾。
    if args.import_only:
        schemas, dict_values = import_from_files()
        if schemas or dict_values:
            write_to_pg(schemas, dict_values)
        else:
            logger.error("❌ JSON 文件为空或不存在，无法导入")
            sys.exit(1)
        return

    # ✅ v0.70: 预热/导出模式需要 Ozon 凭证（缺失显式退出，不再静默落硬编码店）
    if not OZON_CLIENT_ID or not OZON_API_KEY:
        logger.error(
            "❌ 预热/导出需要 Ozon 凭证：export OZON_CLIENT_ID=<id> OZON_API_KEY=<key> "
            "（--coverage / --import-only / --export-from-pg 模式无需凭证）")
        sys.exit(1)

    # 获取所有 type 节点
    nodes = get_type_nodes(limit=limit, offset=args.offset)
    total = len(nodes)
    logger.info(f"📊 共 {total} 个 type 节点需要处理")

    # ✅ v0.72 防复发守卫：起步查磁盘，<5G 不开工
    if not _disk_guard("预热起步"):
        sys.exit(1)

    # ✅ v0.73 W3: 死节点表（Ozon 已删类目永久跳过）——幂等建表 + 载入既有名单
    from storage.database.db import get_session as _get_session
    _pg_s = _get_session()
    try:
        ensure_dead_nodes_table(_pg_s)
        dead_set = load_dead_nodes(_pg_s)
    finally:
        _pg_s.close()
    if dead_set:
        logger.info(f"🪦 死节点表载入 {len(dead_set)} 个 (dc/tp)，本次预热将跳过")

    success = 0
    failed = 0
    dead = 0          # 本次新增标记的死节点（不计入 failed）
    skipped_dead = 0  # 因已在死节点表而跳过
    now = int(time.time())

    # 导出模式才需要攒全量内存（流式写文件降低峰值）；PG 模式逐节点小事务
    schemas = {} if args.export_only else None
    dict_values = {} if args.export_only else None

    for i, node in enumerate(nodes):
        # ✅ v0.72 防复发守卫：每 50 节点查一次磁盘余量
        if i > 0 and i % 50 == 0 and not _disk_guard(f"进度 {i}/{total}"):
            break
        dc = node["description_category_id"]
        tid = node["type_id"]
        key = f"{dc}:{tid}"

        # ✅ v0.73 收口: 死节点永久跳过（400 已删类目，重试无意义；打点日志每 100 个）
        # —— 置于「已缓存」检查之前，死节点不必白付一次 PG 查询
        if (dc, tid) in dead_set:
            skipped_dead += 1
            if skipped_dead % 100 == 0:
                logger.info(f"   ⏭️ 已跳过死节点 {skipped_dead} 个")
            continue

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
                try:
                    s.close()
                except Exception:
                    pass

        logger.info(f"   [{i+1}/{total}] {dc}/{tid} ...")

        try:
            # 获取属性 schema（✅ v0.73 W3: 状态化——400/404=类目永久失效，标死节点）
            schema, schema_status = _fetch_attribute_schema_with_status(dc, tid)
            time.sleep(API_DELAY)

            if not schema:
                if schema_status in (400, 404):
                    _ms = _get_session()
                    try:
                        mark_dead_node(_ms, dc, tid,
                                       f"HTTP {schema_status}: category not found")
                    finally:
                        _ms.close()
                    dead_set.add((dc, tid))
                    dead += 1
                    logger.warning(
                        f"   🪦 [{i+1}/{total}] {dc}/{tid} HTTP {schema_status} "
                        "类目失效，已标记死节点永久跳过")
                else:
                    logger.warning(f"   ⚠️ [{i+1}/{total}] {dc}/{tid} schema 为空，跳过")
                    failed += 1
                continue

            # 获取字典值（并发获取，延迟受控）
            # ✅ v0.72 三桶分流（utils/dict_value_cache.py 策略）：
            # - cat_dep=false → 全局桶 (attr,0,0)，每次运行只拉一次/写一份（seen-set）
            # - cat_dep=true 小字典 → scoped (attr,dc,tp)（现状）
            # - 首页即 has_next（>2000 值，如品牌 85）→ ephemeral，丢弃不再翻页
            #   （品牌无底洞的刹车——warm 不再为每个节点整份复制 5.18MB）
            from utils.dict_value_cache import is_category_dependent
            dict_attrs = [a for a in schema if a.get("dictionary_id", 0) > 0]
            node_dict_values: dict = {}
            if dict_attrs:
                from concurrent.futures import ThreadPoolExecutor, as_completed
                global _global_dict_seen
                todo: list[tuple[dict, str]] = []
                for a in dict_attrs:
                    aid = int(a["id"])
                    if not is_category_dependent(a):
                        if aid in _global_dict_seen:
                            continue  # 全局桶本轮已拉过/写过，跨节点复用
                        _global_dict_seen.add(aid)
                        todo.append((a, f"{aid}:0:0"))
                    else:
                        todo.append((a, f"{aid}:{dc}:{tid}"))

                def _fetch_one_dict(attr, dkey: str):
                    vals, truncated = fetch_dict_values(
                        int(attr["id"]), dc, tid, max_pages=1)
                    if truncated:
                        return dkey, None, True
                    return dkey, vals, False

                if todo:
                    with ThreadPoolExecutor(max_workers=DICT_FETCH_WORKERS) as pool:
                        futures = {pool.submit(_fetch_one_dict, a, dk): (a, dk)
                                   for a, dk in todo}
                        for fut in as_completed(futures):
                            try:
                                dkey, vals, truncated = fut.result()
                                if truncated:
                                    _aid = dkey.split(":", 1)[0]
                                    logger.info(
                                        f"      ⏭️ 字典 {_aid} >2000 值，ephemeral 不物化")
                                    continue
                                if vals:
                                    node_dict_values[dkey] = vals
                            except Exception as _de:
                                logger.debug(f"      ⚠️ 字典值获取失败: {_de}")
                    time.sleep(API_DELAY)

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

    logger.info(f"\n🎉 完成！成功: {success}, 失败: {failed}, 新增死节点: {dead}")
    logger.info(f"   跳过死节点 {skipped_dead} | PG 模式: 已逐节点写入 | 导出模式: Schema {len(schemas or {})} 个类目, 字典值 {len(dict_values or {})} 个条目")


if __name__ == "__main__":
    main()
