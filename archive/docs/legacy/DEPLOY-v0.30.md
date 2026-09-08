# 部署说明 — v0.30.0（worker 属性修复 + skill runtime 稳定化）

> 适用：从 v0.29.x 升级到 v0.30.0。涉及 **Worker 云端** + **Skill 客户端** 两侧。
> 关键改动：retry 止血、fetch-back 回读闭环、学习 provenance、skill 顶层 preflight、CDP 统一、zombie 安全开关。

---

## 0. 部署前必读（风险提示）

### ⚠️ zombie 任务复活（本版本修复的核心风险）

- **旧版本行为**：Worker 启动时会把 `retry_count < max_retries` 的 failed 任务复活为 pending → **重新上架**（用任务里存的真实凭证）。
- **实测事故**：本地 Docker 测试时 4 个旧任务被激活真实上传（Sentry 25 个新错误）。
- **v0.30.0 修复**：新增两个开关（`worker/src/main.py`）：

| 环境变量 | 作用 | 推荐场景 |
|---|---|---|
| `SKIP_ZOMBIE_RECOVERY=1` | 跳过全部恢复（含 running） | 本地/测试环境 |
| `SKIP_FAILED_REVIVE=1` | 只跳过 failed→pending 复活，**保留 running 恢复** | **云端生产推荐** |

### 云端数据库（自建 PostgreSQL）

- **不需要重置整个库**——学习缓存（category_mapping / ozon_attribute_mappings / dictionary_value_cache）是核心资产，**必须保留**。
- **需要清理任务表**（部署时执行，防止旧 failed 任务被复活）：

```sql
DELETE FROM ozon_product_tasks WHERE status IN ('pending','failed','running');
```

- 鉴权说明：token 有效性查 Supabase `tokens` 表；**余额查 MXOU 平台真实余额**（`/v1/dashboard/billing/subscription`，v0.29.3 已改）——Supabase 免费层够用，无需充值；MXOU 余额才是要充值的（生图/LLM 扣费）。

---

## 1. 云端 Worker 部署

### 1.1 更新代码

```bash
cd /path/to/ozon-worker
git pull origin dev          # 或 checkout v0.30.0 tag
git log --oneline -1         # 确认 6222d20 (v0.30.0)
```

### 1.2 修改 .env（关键）

编辑 `deploy/.env`：

```bash
# ① 防止部署重启复活旧 failed 任务（推荐必加）
SKIP_FAILED_REVIVE=1

# ② 确认现有配置不变
PGDATABASE_URL=postgresql://postgres:<密码>@postgres:5432/ozon
SUPABASE_URL=https://<project>.supabase.co
SUPABASE_KEY=<service_role_key>
RATE_LIMIT_PER_MINUTE=300    # v0.30 默认已对齐 300，显式写更稳
```

### 1.3 清理任务表（防 zombie）

```bash
# 进 PG 容器清理（或用 psql 直连）
docker exec -it <pg容器名> psql -U postgres -d ozon \
  -c "DELETE FROM ozon_product_tasks WHERE status IN ('pending','failed','running');"

# 查看清理后状态（completed/cancelled 可留可清）
docker exec -it <pg容器名> psql -U postgres -d ozon \
  -c "SELECT status, count(*) FROM ozon_product_tasks GROUP BY status;"
```

> 💡 可选：如果要保留 completed 历史，只删 pending/failed/running 即可（上面命令已覆盖）。

### 1.4 构建 + 启动

```bash
cd deploy
bash deploy.sh               # 一键部署（含 init_data 自动初始化）
# 或手动：
docker compose up -d --build
```

### 1.5 验证

```bash
# 健康检查
curl -s http://<服务器IP>:8080/api/v1/health
# 期望: {"status":"ok","message":"Service is running","db":"connected"}

# 确认 zombie 开关生效（日志应有）
docker compose logs worker | grep -E "SKIP|启动清理"
# SKIP_FAILED_REVIVE=1 时：failed 任务不再复活，但 running 中断任务仍恢复

# 提交一个测试任务确认全链路
curl -s -X POST http://<服务器IP>:8080/api/v1/submit_task \
  -H "Content-Type: application/json" \
  -d '{"token":"sk-真实token","ozon_client_id":"真实","ozon_api_key":"真实","envelope":{...}}'
```

### 1.6 回滚

```bash
# 保留旧镜像标签
docker compose up -d --build   # 若失败，用旧镜像重启
git checkout v0.29.x           # 代码回滚 + 重新构建
```

---

## 2. Skill 客户端更新

### 2.1 自动更新

```bash
python3.12 scripts/cli.py check    # 自动检测新版本（v0.30.0 包发布后）
skill update                        # 或手动更新
```

### 2.2 Chrome profile 迁移（v0.30 变更）

- v0.30 统一 profile 路径：`data/browser/profiles/1688/default`（旧路径 `data/browser/profile` 弃用）。
- **已登录用户**首次运行新版本后执行：

```bash
# dry-run 预览
python3.12 scripts/migrate_profile.py --check

# 实际迁移（只复制不删除旧 profile，安全）
python3.12 scripts/migrate_profile.py --apply
```

- 不迁移的后果：登录态（1688/Ozon cookie）在新路径找不到 → 需要重新登录。迁移脚本只复制，旧目录保留可随时回退。

### 2.3 验证

```bash
python3.12 scripts/cli.py check
# 期望：Python ≥3.12 通过、依赖探测通过、Chrome CDP 就绪、1688 已登录、Worker 连通、MXOU 余额显示
# 缺依赖时现在会直接提示 "pip install -r requirements.txt"（不再深处炸 traceback）
```

---

## 3. 学习数据回填（可选但推荐）

v0.30 给 `ozon_attribute_mappings` 加了 `source` 列（provenance）。历史数据默认是 `learned_approved`，但其中混着「默认兜底值」（如 Нет бренда / Унисекс）被学习成正确映射的毒数据。**首次部署后执行回填**：

```bash
cd worker
# dry-run 预览（需 PGDATABASE_URL 指向云端 PG）
PGDATABASE_URL=<云端连接串> python3 scripts/backfill_mapping_source.py

# 确认数量合理后执行
PGDATABASE_URL=<云端连接串> python3 scripts/backfill_mapping_source.py --apply
```

> 回填效果：fabricated `[{属性名}]` source_value 和 attr_defaults 默认值 → 标记为 `default_fallback`（可出场但 success_count 不增长），切断学习毒棘轮。

---

## 4. 上线后监控清单（第一周）

| 观察项 | 方法 | 期望 |
|---|---|---|
| fetch-back 日志 | `docker compose logs worker \| grep fetch_back` | approved 后出现「回读 N 属性 diff」 |
| attr.outcome 遥测 | Sentry 搜 `attr.outcome` | 出现 erased/mismatch 事件（不再静默） |
| 8229 中文错误 | Sentry 搜 `8229含中文字符` | **不再新增**（v0.30 已修 assemble type_id 分支） |
| 学习门生效 | 日志搜「学习门」 | 被擦除/默认化属性不写入学习 |
| zombie 复活 | 日志搜「启动清理」 | SKIP_FAILED_REVIVE 生效，failed 不复活 |
| 成功率 | Worker 任务统计 `/api/v1/task_statistics` | 审核拒绝率下降（retry 不再盲填错值） |

---

## 5. 变更摘要（v0.29.x → v0.30.0）

| 类别 | 内容 |
|---|---|
| Worker 修复 | retry 删盲补首值、hazard/is_aspect 守卫、limit 2000、post-fill 中文清零、8229 type_id 中文清零、rejected_unfixable 早退、retry_count 跨入口累积 |
| Worker 新能力 | fetch-back 回读闭环（/v4 diff + attr.outcome 遥测 + 学习门）、learning provenance（source 列 + 按置信消费 + 回填脚本） |
| Worker 运维 | zombie 双开关（SKIP_ZOMBIE_RECOVERY / SKIP_FAILED_REVIVE）、RATE_LIMIT 默认 300 |
| Skill 体验 | 顶层 preflight（Python+deps 探测）、4 处误导文案精确归因、check 全量诊断、命令入口前置 Chrome、updater 跨进程锁 |
| Skill CDP | profile 统一 + 迁移脚本、删无锁 Popen、find_tab 释放契约、零 tab 登录检测 |
| 契约 | follow 信封透传 ozon_attributes_category |

## 6. 已知边界（本版本明确不做）

- PR-2 assemble 大函数物理移动（utils/attribute_matching 全量抽取）→ 后续
- PR-5 attr_defaults 扩消费（9554/18270/9160 无实证不盲加）→ 后续
- D1 类目错路径 category-repair 节点 → 后续
- Cython 4 个 CDP 模块移回明文 → CI smoke test 先行，迁移后续
