#!/usr/bin/env python3
"""PRD M2: 存量租户迁移 — key 哈希派生租户 → 账号级 user_id。

映射来源:
  --from-supabase  直接读 Supabase tokens(key→user_id,status=1,deleted_at null)
  --tokens-file x.json  [{key, user_id, status}] (离线/备份)

流程(dry-run 默认,--apply 才写):
1. 映射 old_tenant = key_derived_tenant(key) → new_tenant = user_id。
2. 13 张 tenant 表 + image_tasks/audit_logs.user_id 逐表 remap。
3. ozon_product_tasks 先重写 sku_key 前缀(old→new),再按 (tenant, sku_key) 合并
   (保留 created_at 最新,draft_submissions.submitted_task_id 重指向)。
4. credentials 同 (tenant, client) 合并:保留 active+updated_at 最大者,
   其余 revoked+后缀;子表 credential_id/store_id 重指向幸存者。
5. credentials / listing_templates 的 is_default 部分唯一冲突:保留最新,其余清默认。
6. 幂等:重跑 no-op;apply 前自动建 _mig_backup_<table> 全量备份。

孤儿(无 tokens 映射的 key 哈希租户)保留并告警,不删除。
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.getenv("APP_WORKSPACE_PATH", os.path.dirname(os.path.dirname(__file__))), "src"))

from sqlalchemy import create_engine, text  # noqa: E402

logger = logging.getLogger(__name__)

from services.tenant_service import key_derived_tenant  # noqa: E402

# (表名, tenant 列, 是否需要 credential 合并处理)
TENANT_TABLES: list[tuple[str, str, bool]] = [
    ("ozon_product_tasks", "tenant_id", False),
    ("product_drafts", "tenant_id", False),
    ("credentials", "tenant_id", True),
    ("product_task_index", "tenant_id", True),
    ("listing_templates", "tenant_id", False),
    ("order_notes", "tenant_id", False),
    ("order_messages", "tenant_id", False),
    ("ozon_orders_cache", "tenant_id", True),
    ("ozon_products_cache", "tenant_id", True),
    ("credential_sync_state", "tenant_id", True),
    ("store_metrics_history", "tenant_id", True),
    ("store_operation_log", "tenant_id", True),
    ("discovery_runs", "tenant_id", False),
    ("store_sync_jobs", "tenant_id", True),
    ("scheduled_listings", "tenant_id", False),
    ("product_costs", "tenant_id", True),
    ("product_cost_history", "tenant_id", True),
    ("source_candidates", "tenant_id", True),
    ("ozon_returns_cache", "tenant_id", True),
    ("ozon_store_analytics_daily", "tenant_id", True),
    ("warehouse_cache", "tenant_id", True),
    ("order_line_costs", "tenant_id", True),
]
USER_ID_TABLES = [("image_tasks", "user_id"), ("audit_logs", "user_id")]

# credentials 子表:credential_id 重指向幸存者
CREDENTIAL_CHILD_TABLES = [
    ("ozon_orders_cache", "credential_id"),
    ("ozon_products_cache", "credential_id"),
    ("credential_sync_state", "credential_id"),
    ("store_metrics_history", "credential_id"),
    ("store_operation_log", "credential_id"),
    ("draft_submissions", "credential_id"),
    ("product_task_index", "credential_id"),
    ("store_sync_jobs", "credential_id"),
]
# store_id(字符串型凭证 id)同步重指向的表
STORE_ID_TABLES = ["store_metrics_history", "store_operation_log"]


def load_mapping(tokens_file: str | None, from_supabase: bool) -> dict[str, str]:
    """key(clean)→user_id;重复 key 取 status=1 优先,其次后者。"""
    rows: list[dict] = []
    if from_supabase:
        from storage.database.supabase_client import get_supabase_client
        supabase = get_supabase_client()
        if supabase is None:
            raise SystemExit("Supabase 未配置(SUPABASE_URL/KEY),请改用 --tokens-file")
        resp = supabase.table("tokens").select("key,user_id,status").is_("deleted_at", "null").execute()
        rows = [dict(r) for r in (resp.data or [])]
    elif tokens_file:
        rows = json.load(open(tokens_file, encoding="utf-8"))
    else:
        raise SystemExit("必须提供 --tokens-file 或 --from-supabase")

    mapping: dict[str, str] = {}
    for r in rows:
        key = str(r.get("key") or "").strip()
        user_id = str(r.get("user_id") or "").strip()
        status = int(r.get("status", 1) or 1)
        if not key or not user_id:
            continue
        clean = key[3:] if key.startswith("sk-") else key
        old = key_derived_tenant(clean)
        if status == 1:
            mapping[old] = user_id
        else:
            mapping.setdefault(old, user_id)
    return mapping


def _old_tenants(mapping: dict[str, str]) -> set[str]:
    return set(mapping.keys())


def report(engine, mapping: dict[str, str]) -> dict:
    """dry-run 报告:待迁移行数 / 孤儿租户 / 合并冲突预览。"""
    olds = _old_tenants(mapping)
    out: dict = {"mapping": len(mapping), "tables": {}, "orphans": {}, "merge_preview": {}}
    with engine.connect() as conn:
        for table, col, _merge in TENANT_TABLES:
            try:
                rows = conn.execute(text(
                    f"SELECT {col}, COUNT(*) FROM {table} WHERE {col} = ANY(:olds) GROUP BY {col}"
                ), {"olds": list(olds)}).fetchall()
                out["tables"][table] = {str(r[0]): int(r[1]) for r in rows}
                orphans = conn.execute(text(
                    f"SELECT {col}, COUNT(*) FROM {table} "
                    f"WHERE {col} LIKE 'user_%' AND NOT ({col} = ANY(:olds)) GROUP BY {col}"
                ), {"olds": list(olds)}).fetchall()
                if orphans:
                    out["orphans"][table] = {str(r[0]): int(r[1]) for r in orphans}
            except Exception:
                pass  # 表不存在跳过
        for table, col in USER_ID_TABLES:
            try:
                orphans = conn.execute(text(
                    f"SELECT {col}, COUNT(*) FROM {table} "
                    f"WHERE {col} LIKE 'user_%' AND NOT ({col} = ANY(:olds)) GROUP BY {col}"
                ), {"olds": list(olds)}).fetchall()
                if orphans:
                    out["orphans"][table] = {str(r[0]): int(r[1]) for r in orphans}
            except Exception:
                pass  # 表不存在则跳过
        # 合并冲突预览:跨租户同店(按 target 组)
        for new, tenants in _target_tenants(mapping).items():
            dup_cred = conn.execute(text(
                """
                SELECT ozon_client_id, COUNT(*) FROM credentials
                WHERE tenant_id = ANY(:ts) AND status='active'
                GROUP BY ozon_client_id HAVING COUNT(*) > 1
                """
            ), {"ts": tenants}).fetchall()
            out["merge_preview"].setdefault("credential_dups", []).extend(
                [[str(new), str(r[0]), int(r[1])] for r in dup_cred])
    return out


def _backup(conn, tables: list[str]) -> None:
    """全量备份一次(IF NOT EXISTS 保证幂等)。"""
    for t in tables:
        conn.execute(text(f"CREATE TABLE IF NOT EXISTS _mig_backup_{t} AS SELECT * FROM {t}"))


def _remap_tenant(conn, table: str, col: str, mapping: dict[str, str]) -> None:
    for old, new in mapping.items():
        try:
            conn.execute(text(f"UPDATE {table} SET {col}=:new WHERE {col}=:old"),
                         {"new": new, "old": old})
        except Exception:
            pass  # 表不存在跳过


def _target_tenants(mapping: dict[str, str]) -> dict[str, list[str]]:
    """new → [old...](含 new 自身) 分组。"""
    out: dict[str, list[str]] = {}
    for old, new in mapping.items():
        out.setdefault(new, [new]).append(old)
    return out


def _revoke_and_reassign(conn, survivor: str, loser: str) -> None:
    """吊销 loser 凭证并重指向子表到 survivor(迁移专用)。"""
    conn.execute(text(
        "UPDATE credentials SET status='revoked', is_default=false, "
        "ozon_client_id = ozon_client_id || ':revoked:' || :suffix, updated_at=NOW() "
        "WHERE id::text=:id"
    ), {"suffix": uuid.uuid4().hex[:8], "id": loser})
    for table, col in CREDENTIAL_CHILD_TABLES:
        if table in ("credential_sync_state", "store_sync_jobs", "scheduled_listings"):
            # 状态/在途任务/定时行:删被合并行,保留幸存行(避免唯一冲突/陈旧引用)
            conn.execute(text(f"DELETE FROM {table} WHERE {col}::text=:id"), {"id": loser})
            continue
        if table == "draft_submissions":
            # 进行中 submission 删除(防 (draft, store) 唯一冲突),其余重指向
            conn.execute(text(
                "DELETE FROM draft_submissions WHERE credential_id::text=:id "
                "AND status IN ('pending','uploading')"
            ), {"id": loser})
        conn.execute(text(
            f"UPDATE {table} SET {col}=:keep WHERE {col}::text=:drop"
        ), {"keep": uuid.UUID(survivor), "drop": loser})
    for table in STORE_ID_TABLES:
        conn.execute(text(
            f"UPDATE {table} SET store_id=:keep WHERE store_id=:drop"
        ), {"keep": survivor, "drop": loser})


def _presolve_credentials(conn, mapping: dict[str, str]) -> None:
    """跨租户同店合并(remap 前):每 client 在 target 组内保留 updated_at 最大 active 行。"""
    for new, tenants in _target_tenants(mapping).items():
        clients = conn.execute(text(
            "SELECT DISTINCT ozon_client_id FROM credentials "
            "WHERE tenant_id = ANY(:ts) AND status='active'"
        ), {"ts": tenants}).fetchall()
        for (client,) in clients:
            rows = conn.execute(text(
                "SELECT id::text, updated_at FROM credentials "
                "WHERE tenant_id = ANY(:ts) AND ozon_client_id=:c AND status='active' "
                "ORDER BY updated_at DESC NULLS LAST"
            ), {"ts": tenants, "c": client}).fetchall()
            survivor = str(rows[0][0])
            for rid, _ in rows[1:]:
                _revoke_and_reassign(conn, survivor, str(rid))


def _presolve_defaults(conn, mapping: dict[str, str]) -> None:
    """跨租户 is_default 清重(remap 前),每 target 组保留 updated_at 最大。"""
    for new, tenants in _target_tenants(mapping).items():
        for table in ("credentials", "listing_templates"):
            try:
                rows = conn.execute(text(
                    f"SELECT id::text, updated_at FROM {table} "
                    "WHERE tenant_id = ANY(:ts) AND is_default "
                    "ORDER BY updated_at DESC NULLS LAST"
                ), {"ts": tenants}).fetchall()
                for rid, _ in rows[1:]:
                    conn.execute(text(
                        f"UPDATE {table} SET is_default=false WHERE id::text=:id"
                    ), {"id": rid})
            except Exception:
                pass


def _presolve_product_tasks(conn, mapping: dict[str, str]) -> None:
    """sku_key 前缀重写 + 跨租户同 sku_key 去重(remap 前,防 (tenant, sku_key) 冲突)。"""
    for old, new in mapping.items():
        conn.execute(text(
            "UPDATE ozon_product_tasks SET sku_key = :new || substring(sku_key from :len) "
            "WHERE tenant_id=:old AND sku_key LIKE :prefix"
        ), {"new": new, "old": old, "len": len(old) + 1, "prefix": f"{old}:%"})
    for new, tenants in _target_tenants(mapping).items():
        rows = conn.execute(text(
            "SELECT id::text, sku_key, created_at FROM ozon_product_tasks "
            "WHERE tenant_id = ANY(:ts) ORDER BY sku_key, created_at DESC NULLS LAST"
        ), {"ts": tenants}).fetchall()
        seen: dict[str, str] = {}
        for rid, sk, _ in rows:
            if not sk:
                continue
            if sk in seen:
                conn.execute(text(
                    "UPDATE draft_submissions SET submitted_task_id=:keep "
                    "WHERE submitted_task_id=:drop"
                ), {"keep": seen[sk], "drop": rid})
                conn.execute(text(
                    "DELETE FROM ozon_product_tasks WHERE id::text=:id"
                ), {"id": rid})
            else:
                seen[sk] = rid


def _presolve_cache_conflicts(conn, mapping: dict[str, str]) -> None:
    """跨租户唯一键预删(remap 前):orders/products 缓存与 sync_state、日聚合。"""
    for new, tenants in _target_tenants(mapping).items():
        # ozon_orders_cache (credential, posting) 保留 id 最大(最新)
        conn.execute(text(
            """
            DELETE FROM ozon_orders_cache a USING ozon_orders_cache b
            WHERE a.tenant_id = ANY(:ts) AND b.tenant_id = ANY(:ts)
              AND a.credential_id = b.credential_id AND a.posting_number = b.posting_number
              AND a.id < b.id
            """
        ), {"ts": tenants})
        # ozon_products_cache (credential, product_id)
        conn.execute(text(
            """
            DELETE FROM ozon_products_cache a USING ozon_products_cache b
            WHERE a.tenant_id = ANY(:ts) AND b.tenant_id = ANY(:ts)
              AND a.credential_id = b.credential_id AND a.product_id = b.product_id
              AND a.id < b.id
            """
        ), {"ts": tenants})
        # credential_sync_state:删非 new 租户的重复行
        conn.execute(text(
            """
            DELETE FROM credential_sync_state a USING credential_sync_state b
            WHERE a.tenant_id <> :new AND a.tenant_id = ANY(:ts)
              AND b.tenant_id = :new AND a.credential_id = b.credential_id
            """
        ), {"new": new, "ts": tenants})
        # store_daily_metrics (credential, stat_date)
        conn.execute(text(
            """
            DELETE FROM store_daily_metrics a USING store_daily_metrics b
            WHERE a.tenant_id = ANY(:ts) AND b.tenant_id = ANY(:ts)
              AND a.credential_id = b.credential_id AND a.stat_date = b.stat_date
              AND a.id < b.id
            """
        ), {"ts": tenants})


def apply(engine, mapping: dict[str, str]) -> dict:
    """执行迁移(幂等;自动备份;先预消解跨租户冲突再 remap)。"""
    all_tables = [t for t, _, _ in TENANT_TABLES] + [t for t, _ in USER_ID_TABLES]
    with engine.begin() as conn:
        _backup(conn, all_tables)
        # 预消解(remap 前,避免唯一约束冲突)
        _presolve_product_tasks(conn, mapping)
        _presolve_credentials(conn, mapping)
        _presolve_defaults(conn, mapping)
        _presolve_cache_conflicts(conn, mapping)
        # remap
        for table, col, merge in TENANT_TABLES:
            _remap_tenant(conn, table, col, mapping)
        for table, col in USER_ID_TABLES:
            try:
                _remap_tenant(conn, table, col, mapping)
            except Exception:
                pass  # 表不存在跳过
    return {"applied": True, "mapping": len(mapping)}


def main() -> int:
    ap = argparse.ArgumentParser(description="key 哈希租户 → user_id 迁移(PRD M2)")
    ap.add_argument("--tokens-file", default=None)
    ap.add_argument("--from-supabase", action="store_true")
    ap.add_argument("--apply", action="store_true", help="默认 dry-run;加此参数才写库")
    ap.add_argument("--db-url", default=os.getenv("PGDATABASE_URL", ""))
    args = ap.parse_args()
    if not args.db_url:
        logger.error("请设置 PGDATABASE_URL")
        return 1
    mapping = load_mapping(args.tokens_file, args.from_supabase)
    if not mapping:
        logger.error("映射为空,检查 tokens 来源")
        return 1
    engine = create_engine(args.db_url)
    rep = report(engine, mapping)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    if not args.apply:
        print("dry-run:未写库;确认后加 --apply")
        return 0
    result = apply(engine, mapping)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("⚠️ 迁移完成:建议先核对备份表 _mig_backup_*,再跑隔离回归")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(main())
