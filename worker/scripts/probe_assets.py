#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A8 用户资产七探针只读 CLI（探针来源：docs/audit/2026-09-11-repo-gov/A8-user-assets-reconciliation.md §7）。

把审计交付的 S1-S7 查询脚本固化为可重复执行的子命令式探针（WORKFLOW §6.5「数据探针」先例）。

只读纪律：全部 SELECT/COUNT + LIMIT，零写入、不触大 JSONB 列；S7 属服务器侧日志 grep，
本脚本只打印待执行命令清单（不连库）。

连库：--url 或环境变量 PGDATABASE_URL（本地常用 postgresql://postgres:localdev123@localhost:5433/ozon；
生产经 SSH 在服务器上执行，容器名/PG 密码见服务器 deploy/.env）。

用法：
    python3 worker/scripts/probe_assets.py --probe all      # S1-S7 全跑
    python3 worker/scripts/probe_assets.py --probe s1       # 单探针
    python3 worker/scripts/probe_assets.py --probe s3 --url postgresql://...

结论先行：每探针输出 = 结果表格 + 异常计数结论（0 → ✅ 正常 / >0 → ⚠️ 按 A8 §8 对应项处置，
红时处置见 A8 §7 注释与 BACKLOG 对应 BL 条目）。
"""
from __future__ import annotations

import argparse
import os
import sys

import psycopg2

# ---------------------------------------------------------------------------
# SQL 常量（与 A8 §7 逐字对齐，测试锁定形状；改 SQL 先改 A8 报告）
# ---------------------------------------------------------------------------

S1_COUNT_SQL = (
    "SELECT COUNT(*) AS orphan_submissions FROM draft_submissions ds "
    "WHERE ds.submitted_task_id IS NOT NULL "
    "AND NOT EXISTS (SELECT 1 FROM ozon_product_tasks t WHERE t.id::text = ds.submitted_task_id)"
)
S1_SAMPLE_SQL = (
    "SELECT id::text, draft_id::text, submitted_task_id, status, created_at FROM draft_submissions ds "
    "WHERE ds.submitted_task_id IS NOT NULL "
    "AND NOT EXISTS (SELECT 1 FROM ozon_product_tasks t WHERE t.id::text = ds.submitted_task_id) "
    "ORDER BY created_at DESC LIMIT 5"
)
_S2_BRANCH = ("SELECT '{t}' AS t, COUNT(*) AS residue FROM {t} s "
              "LEFT JOIN credentials c ON c.id = s.credential_id "
              "WHERE c.id IS NULL OR c.status='revoked'")
S2_SQL = " UNION ALL ".join(
    _S2_BRANCH.format(t=t)
    for t in ("ozon_sessions", "ozon_orders_cache", "ozon_products_cache", "credential_sync_state")
)
S3_DOUBLE_BIND_SQL = (
    "SELECT ozon_client_id, COUNT(DISTINCT tenant_id) AS tenants FROM credentials "
    "WHERE status='active' GROUP BY ozon_client_id "
    "HAVING COUNT(DISTINCT tenant_id) > 1 LIMIT 20"
)
S3_REVOKED_SQL = (
    "SELECT COUNT(*) AS revoked_pattern_rows FROM credentials "
    "WHERE ozon_client_id LIKE '%:revoked:%'"
)
_DIST_CASE = (
    "CASE WHEN tenant_id ~ '^user_[0-9a-f]{16}$' THEN 'hash_legacy' "
    "WHEN tenant_id ~ '^[0-9]+$' THEN 'supabase_numeric' ELSE 'other' END AS kind"
)
S4_TASKS_DIST_SQL = (
    f"SELECT {_DIST_CASE}, COUNT(*) AS cnt FROM ozon_product_tasks GROUP BY 1 ORDER BY 2 DESC"
)
S4_ERRREPORTS_DIST_SQL = (
    f"SELECT {_DIST_CASE}, COUNT(*) AS cnt FROM error_reports GROUP BY 1 ORDER BY 2 DESC"
)
S4_TOP_SQL = (
    "SELECT tenant_id, COUNT(*) AS cnt FROM ozon_product_tasks "
    "GROUP BY 1 ORDER BY 2 DESC LIMIT 10"
)
S5_SQL = (
    "SELECT COUNT(*) AS total, "
    "COUNT(*) FILTER (WHERE tenant_id ~ '^user_[0-9a-f]{16}$') AS hash_like, "
    "COUNT(*) FILTER (WHERE tenant_id ~ '^sk?') AS sk_like FROM discovery_runs"
)
S6_SQL = (
    "SELECT DISTINCT tenant_id FROM credentials "
    "WHERE tenant_id !~ '^[0-9]+$' AND tenant_id !~ '^user_[0-9a-f]{16}$' LIMIT 20"
)
# S7 余额对账素材（服务器侧执行；MXOU 平台侧差分对账属 A8 §6.2 方案 B 后续运维动作）
S7_COMMANDS = [
    "docker exec -it <postgres容器> psql -U postgres -d ozon   # 进入 PG（容器名见服务器 deploy/.env）",
    'docker logs --since 168h <worker容器> 2>&1 | grep -E "余额不足|低余额|402|BALANCE_ALERT|balance" | tail -80',
    "# MXOU 平台侧消耗差分：两次时点采样余额（A8 §6.2 方案 B），worker 无平台用量读端点",
]


def _rows(conn, sql):
    cur = conn.cursor()
    try:
        cur.execute(sql)
        return cur.fetchall()
    finally:
        cur.close()


def _one(conn, sql, default):
    """取聚合查询首行（COUNT 聚合恒有一行；default 仅为防御空结果集）。"""
    got = _rows(conn, sql)
    return got[0] if got else default


def _res(pid, title, headers, rows, conclusion):
    return {"id": pid, "title": title, "headers": headers, "rows": rows, "conclusion": conclusion}


# ---------------------------------------------------------------------------
# 探针实现（S1-S6 连库 / S7 打印命令清单）
# ---------------------------------------------------------------------------

def probe_s1(conn):
    """S1 孤儿 draft_submissions（引用任务已 30 天删/写失败；对应 A8 F4）。"""
    (orphan,) = _one(conn, S1_COUNT_SQL, (0,))
    rows = []
    if orphan:
        rows = _rows(conn, S1_SAMPLE_SQL)
    direct = sum(1 for r in rows if r[1] is None)
    concl = ("✅ 无孤儿提交行" if not orphan else
             f"⚠️ 孤儿提交 {orphan} 行（抽样含直连行 {direct} 行 draft_id=NULL 无法归租户）——按 A8 F4 处置")
    return _res("s1", "S1 孤儿 draft_submissions", ["id", "draft_id", "task_id", "status", "created_at"],
                rows, concl)


def probe_s2(conn):
    """S2 吊销/缺失凭证的 store-scoped 残留（按 credential_id join；对应 A8 F5）。"""
    rows = _rows(conn, S2_SQL)
    bad = [r for r in rows if r[1]]
    concl = ("✅ 四表零吊销/缺凭证残留" if not bad else
             f"⚠️ {len(bad)} 张表有残留（共 {sum(r[1] for r in bad)} 行）——按 A8 F5（级联清理/data_erasure 补 ozon_sessions）")
    return _res("s2", "S2 吊销/缺失凭证残留", ["table", "residue"], rows, concl)


def probe_s3(conn):
    """S3 跨租户双绑实证 + revoked 后缀行 + 会话/缓存指向 revoked（对应 A8 F3，双绑>0 升 P0）。"""
    rows = _rows(conn, S3_DOUBLE_BIND_SQL)
    revoked_list = _rows(conn, S3_REVOKED_SQL)
    revoked = revoked_list[0][0] if revoked_list else 0
    rows = rows + [["--", f"revoked_pattern_rows={revoked}"]]
    concl = ("✅ 无同店多租户双绑、无 revoked 后缀行" if len(rows) == 1 and not revoked else
             "⚠️ 存在双绑/revoked 痕迹——双绑>0 则 A8 F3 升 P0（pg_advisory_xact_lock / 部分唯一索引）")
    return _res("s3", "S3 跨租户双绑/revoked 痕迹", ["ozon_client_id", "tenants"], rows, concl)


def probe_s4(conn):
    """S4 租户漂移分布：哈希租户遗迹规模（任务表+error_reports 同口径；对应 A8 F2）。"""
    dist = _rows(conn, S4_TASKS_DIST_SQL)
    top = _rows(conn, S4_TOP_SQL)
    err = _rows(conn, S4_ERRREPORTS_DIST_SQL)
    rows = [["ozon_product_tasks", *r] for r in dist] + \
           [["error_reports", *r] for r in err] + \
           [["top10", *r] for r in top]
    legacy = sum(r[1] for r in dist if r[0] == "hash_legacy") + \
             sum(r[1] for r in err if r[0] == "hash_legacy")
    concl = ("✅ 两表零哈希租户遗迹" if not legacy else
             f"⚠️ 哈希租户(user_+16hex)遗迹 {legacy} 行——按 A8 F2（迁移归并/读侧兼容视图）")
    return _res("s4", "S4 租户漂移分布", ["source", "tenant_id/kind", "cnt"], rows, concl)


def probe_s5(conn):
    """S5 tenant_id 双语义盘点：discovery_runs 的「tenant_id」实为 token 明文（对应 A8 F6）。"""
    total, hash_like, sk_like = _one(conn, S5_SQL, (0, 0, 0))
    concl = ("✅ discovery_runs 无 token 形态值" if not (hash_like or sk_like) else
             f"⚠️ token 形态值 hash_like={hash_like} / sk_like={sk_like}（共 {total} 行）——按 A8 F6（哈希化迁移）")
    return _res("s5", "S5 discovery_runs.tenant_id 双语义", ["total", "hash_like", "sk_like"],
                [[total, hash_like, sk_like]], concl)


def probe_s6(conn):
    """S6 无主凭证：tenant_id 既非数字也非哈希（异常值盘点，配合 S4 口径）。"""
    rows = _rows(conn, S6_SQL)
    concl = ("✅ credentials.tenant_id 全部可归入数字/哈希两类" if not rows else
             f"⚠️ {len(rows)} 个异常形态 tenant_id（LIMIT 20 内）——人工逐个归因")
    return _res("s6", "S6 无主凭证异常租户", ["tenant_id"], rows, concl)


def probe_s7(_conn):
    """S7 余额对账素材：服务器侧日志 grep（不连库，打印待执行命令清单）。"""
    return {"id": "s7", "title": "S7 余额日志对账素材（服务器侧执行）",
            "commands": list(S7_COMMANDS),
            "conclusion": "ℹ️ 属服务器侧动作；对账双通道方案见 A8 §6.2（F1 处置）"}


PROBES = {"s1": probe_s1, "s2": probe_s2, "s3": probe_s3, "s4": probe_s4,
          "s5": probe_s5, "s6": probe_s6, "s7": probe_s7}


# ---------------------------------------------------------------------------
# 输出与入口
# ---------------------------------------------------------------------------

def _fmt(v):
    return "-" if v is None else str(v)


def render(res):
    print(f"== {res['title']} ==")
    if "commands" in res:
        for c in res["commands"]:
            print(c)
    else:
        headers = res["headers"]
        widths = [len(h) for h in headers]
        table = [[_fmt(v) for v in r] for r in res["rows"]]
        for row in table:
            for i, cell in enumerate(row):
                widths[i] = max(widths[i], len(cell))
        print("  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)))
        print("  ".join("-" * w for w in widths))
        for row in table:
            print("  ".join(c.ljust(widths[i]) for i, c in enumerate(row)))
        if not table:
            print("(0 行)")
    print(f"结论: {res['conclusion']}")


def _parse_args(argv=None):
    p = argparse.ArgumentParser(description="A8 用户资产七探针只读 CLI（S1-S7，详见脚本 docstring）")
    p.add_argument("--probe", choices=[*PROBES, "all"], default="all",
                   help="探针选择：s1..s7 单跑或 all 全跑（默认 all）")
    p.add_argument("--url", default=None,
                   help="PG 连接串；缺省读 PGDATABASE_URL（S7 不需要连库）")
    return p.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv)
    ids = list(PROBES) if args.probe == "all" else [args.probe]
    conn = None
    if any(i != "s7" for i in ids):
        url = args.url or os.environ.get("PGDATABASE_URL", "")
        if not url:
            print("❌ 未指定连库：用 --url 或环境变量 PGDATABASE_URL"
                  "（本地 postgresql://postgres:localdev123@localhost:5433/ozon）")
            return 2
        conn = psycopg2.connect(url)
    try:
        for pid in ids:
            render(PROBES[pid](conn))
            print()
    finally:
        if conn is not None:
            conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
