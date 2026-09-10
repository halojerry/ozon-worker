---
title: PG 备份恢复演练手册
purpose: backup-pg.sh 备份体系的恢复演练五步法与 RPO/RTO 运营化（承接 PRD-store-sync M5）
applies-version: ">=v0.74.0"
last-updated: 2026-09-11
owner: worker-ops
depends: [DEPLOY, DB-SCHEMA-AUDIT, PRD-store-sync-erp-v1]
status: active
---

# RESTORE-RUNBOOK — PG 备份恢复演练手册

> **结论先行**：备份没演练过 = 没备份。目标口径（承接 PRD-store-sync M5）：**RPO ≤24h（备份间隔），
> RTO ≤1h**。⚠️ 本 runbook 首次演练后回填本页「演练记录」实测值。
> 前提设施（repo-gov B2-β，PR #16 即将合入口径）：`deploy/backup-pg.sh` 双模式（host/container 自动检测）、
> gpg 加密可选（`PG_BACKUP_PASSPHRASE`，无 gpg 拒绝落明文）、保留 14 天、ofelia sidecar 24h 调度、
> 每次（成功/失败）写 `backup_heartbeat` 心跳行，worker `/health` 暴露 `last_backup_at`
> （>26h 无成功心跳 = `backup_stale` 告警）。

## 0. 备份体系速览

| 项 | 值 |
|---|---|
| 备份方式 | `pg_dump`（plain SQL）→ 可选 gpg 对称加密；文件名 `backup_YYYYMMDD_HHMMSS.sql[.gpg]`（**含时间戳**） |
| 位置 | `deploy/backups/`（`PG_BACKUP_DIR` 可覆盖） |
| 保留 | 14 天（`PG_BACKUP_RETENTION_DAYS` 可覆盖） |
| 调度 | ofelia job-exec 24h 一次（宿主机 crontab 亦可） |
| 心跳 | `backup_heartbeat(finished_at, ok, detail)`；detail=备份文件名 |
| 恢复通道 | `bash deploy/backup-pg.sh --restore <file>`（仅 host 模式；gpg 自动解密到 /tmp 后导入） |

## 1. 演练五步（建议季度一次；任何恢复相关变更后必做）

### ① 选定备份文件（读心跳，不翻目录猜）

```sql
SELECT to_char(to_timestamp(finished_at), 'YYYY-MM-DD HH24:MI') AS at, ok, detail
FROM backup_heartbeat ORDER BY finished_at DESC LIMIT 5;
```

取最近 `ok=true` 行的 `detail`（即备份文件名）。无心跳行（升级后 worker 未启动过/表未建）时退化为
`ls -lt deploy/backups/ | head` 取最新 .sql/.gpg。

### ② 临时库恢复（不覆盖生产）

```bash
cd deploy
docker compose exec -T postgres createdb -U postgres restore_test
# 未加密：
docker compose exec -T postgres psql -U postgres -d restore_test < backups/backup_X.sql
# 加密（.gpg）：先解密再导入
gpg --decrypt --batch --yes --passphrase "$PG_BACKUP_PASSPHRASE" backups/backup_X.sql.gpg > /tmp/restore_test.sql
docker compose exec -T postgres psql -U postgres -d restore_test < /tmp/restore_test.sql && rm -f /tmp/restore_test.sql
```

**红时处置**：导入报错中断 → 换上一次 ok=true 的备份重试；连续两份都失败 = 备份链断裂，
立即手动跑一次 `bash deploy/backup-pg.sh` 并升级处理（此时 RPO 已不可控）。

### ③ 行数抽样比对（5 张核心表，生产 vs restore_test）

```bash
cd deploy
for db in ozon restore_test; do
  docker compose exec -T postgres psql -U postgres -d $db -t -c \
    "SELECT '$db' AS src, (SELECT count(*) FROM ozon_product_tasks) AS tasks, \
     (SELECT count(*) FROM product_drafts) AS drafts, (SELECT count(*) FROM credentials) AS creds, \
     (SELECT count(*) FROM ozon_orders_cache) AS orders, (SELECT count(*) FROM draft_submissions) AS subs;"
done
```

两行逐列对比：restore_test ≤ 生产且差值 ≈ 备份时刻之后的写入量 = 正常；restore_test 明显偏小（差值超一天量）
= 备份不完整，回②换文件。表缺列/缺表报错 = dump 时代与现库 schema 漂移，登记 DB-SCHEMA-AUDIT。

### ④ 切换演练（真恢复主库；低峰期做，破坏性操作）

```bash
cd deploy
docker compose stop worker                      # 停写入（webui/MCP 同进程随之不可写）
bash deploy/backup-pg.sh                        # 最终一次备份（兜底可回退点）
docker compose exec -T postgres psql -U postgres -c "DROP DATABASE ozon;" -c "CREATE DATABASE ozon OWNER postgres;"
bash deploy/backup-pg.sh --restore backups/backup_X.sql[.gpg]   # 全量导入
docker compose start worker
curl -s http://localhost:8080/api/v1/health | grep -E "last_backup_at|backup_stale"
```

- `--restore` 走正式通道（gpg 自动解密）；DROP 前必须确认步骤④的最终备份已成功落盘。
- `/health` 的 `last_backup_at` 应为最近成功心跳时间且 `backup_stale=false`（26h 判定）。
- **红时处置**：恢复后服务起不来 → 回滚用第②步的最终备份按同法再导；仍失败升级人工介入。

### ⑤ 记录演练结果（回填本页「演练记录」）

必录四项：**RTO 实测**（①→④ 完成+health 绿的总耗时）、**RPO 实际**（备份时刻→故障演练时刻的间隔）、
丢数据评估（备份后写入量）、异常与处置。超目标（RTO>1h）登记改进项（如备份改 custom 格式+pg_restore 并行）。

## 2. 演练记录（首次演练后回填实测值）

| 日期 | 备份文件 | RTO 实测 | RPO 实际 | 丢数据 | 异常/处置 |
|---|---|---|---|---|---|
| （待回填） | | | | | |

## 3. 已知边界

- `--restore` 仅 host 模式可用（container 模式无宿主机文件访问，脚本内已显式拒绝）。
- plain SQL 导入为单线程串行，45+ 表全库约分钟级（RTO 主要消耗在人工决策段——正是演练要压缩的）。
- 心跳/备份脚本均「观测面不阻断主流程」：心跳写失败只告警；恢复演练时勿以心跳存在性替代步骤①实测。
