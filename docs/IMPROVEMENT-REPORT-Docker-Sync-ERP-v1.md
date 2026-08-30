# 改进报告:Docker 测试环境 + 数据落盘/使用 + 店铺同步模型规划(ERP 视角)

> 日期:2026-08-30
> 范围:worker/skill 测试环境 Docker 化、数据落盘与数据使用、webui 绑定 key 后的同步策略
> 视角:ERP(多租户、数据资产、任务可见性、口径一致性、容量与安全)

---

## 0. 结论摘要

1. **测试环境 Docker 化基本可行,但缺"一键化"**:worker 已有完整 compose 与 CI(PG + init_data + pytest);skill 已在 CI 用 `python:3.12-slim` 无 Chrome 容器跑 85 个测试文件。缺口在本地:dev compose 的 PG 密码与测试默认值不一致、没有 worker 测试专用 compose、dsh 沙箱只验证 harness/插件/MCP 层(skill CDP 与编译产物不覆盖)。
2. **数据落盘方向正确,但同步链路有 3 个硬伤**:绑定后不落盘(要等 15min 调度器或用户手动);HTTP 路由直接调阻塞同步导致事件循环卡死;懒同步=首次全量同步,多店铺首屏会串行跑 N 次全量。
3. **同步模型需要从"全局 15min 轮询 + 懒同步兜底"升级为 ERP 标准三段式**:绑定即初始化 job → 按店定时增量 → 读取只读缓存(空则展示同步状态,不触发全量)。
4. **发现 1 个跨租户数据泄漏风险**:`/analytics/market-overview`、`sales-trend`、`categories` 聚合 SQL 未按 tenant 过滤,任意有效 token 可看全平台订单 GMV/订单数/选品关键词。
5. **身份模型(key 即租户)与 ERP 多账号冲突**:换 key / 多 key 导致数据碎片化、绑定店铺"消失",需要账号级身份 + key 仅作凭证。

---

## 1. Docker 测试/部署环境现状与问题

### 1.1 现状

| 目标 | 现状 | 证据 |
|---|---|---|
| worker 生产/测试容器 | `docker-compose.yml`(PG+worker)+ `docker-compose.dev.yml`(仅 PG),worker 镜像内置 pytest | `deploy/docker-compose*.yml`、`worker/Dockerfile` |
| worker CI 测试 | GitHub Actions:services.postgres + `init_data.py` + pytest | `.github/workflows/ci.yml:106-152` |
| skill CI 测试 | `python:3.12-slim` 容器、无 Chrome,跑 `tests/` 85 个文件 | `.github/workflows/ci.yml:154-165` |
| skill/dsh 沙箱 | dsh web + 插件 + MCP 管道层,CDP 采集明确"仍需真机" | `deploy/dsh/Dockerfile` 注释 |
| webui | worker 静态托管 `/app`,compose bind mount `../webui/dist:ro` | `docker-compose.yml`、`worker/src/main.py:2481-2511` |

### 1.2 问题

| ID | 级别 | 问题 | 证据 | 影响 |
|---|---|---|---|---|
| D1 | P1 | dev compose PG 密码 `ozon123`,worker 测试默认连接 `postgres:localdev123@localhost:5433` → 本地测试直接起不来,必须手动覆盖 `PGDATABASE_URL` | `deploy/docker-compose.dev.yml` vs `worker/tests/test_store_sync.py` 顶部 DB_URL | 本地复现测试门槛高 |
| D2 | P1 | 无 worker 测试专用 compose / 一键脚本(CI 有等价模式,本地没有) | `docker-compose.dev.yml` 只有 PG | 新人/回归成本高 |
| D3 | P2 | `deploy.sh` 不构建 webui;dist 缺失时 bind mount 会静默挂空目录,`/app` 无前端(仅日志 warn) | `deploy/deploy.sh`、`worker/src/main.py:2493-2498` | 手工部署容易漏 UI |
| D4 | P2 | compose 默认 `RATE_LIMIT_PER_MINUTE=10`,代码默认 300、`.env.example` 也是 300 → 配置漂移,未设 .env 时任务提交易 429 | `deploy/docker-compose.yml` vs `worker/src/main.py:208` | 误伤正常提交 |
| D5 | P2 | worker 生产镜像内置 pytest/测试依赖(~50MB,注释自认) | `worker/Dockerfile` | 镜像膨胀(可接受,建议拆 test target) |
| D6 | P2 | PG 无自动备份:仅 DEPLOY.md 文档化手动 `pg_dump`,无 cron/保留策略 | `docs/DEPLOY.md:301-308` | ERP 数据资产无灾备 |
| D7 | P2 | skill dist 只有 `darwin-arm64` 14 个 .so;linux 容器内 loader 可退回源码明文 import(dev checkout 可跑),但发布包对 linux 客户需 CI 构建 linux .so | `skill/dist/scripts/lib/_native/`、`skill/compile.py` | 容器内测的是"源码 fallback"路径,与发布二进制有差异 |
| D8 | 信息 | dsh 沙箱明确不含 CDP 采集能力(无 Chrome/登录态)——这是设计,不是缺陷 | `deploy/dsh/Dockerfile` | 采集链路必须真机验证 |

### 1.3 建议

- 新增 `deploy/docker-compose.test.yml`(PG + worker-test 服务,密码统一)+ `scripts/test-docker.sh`(起 PG → `init_data.py` → pytest),对齐 CI 已有模式。
- `deploy.sh` 增加 webui dist 检测:缺失时提示先 `cd webui && bun install && bun run build`,或直接构建。
- compose 默认值对齐代码/`.env.example`(`RATE_LIMIT_PER_MINUTE=300`)。
- PG 自动备份:宿主机 cron 或独立 backup 容器,`pg_dump` + 保留 N 天 + 恢复演练。
- skill 容器测试沿用 CI 的 `python:3.12-slim` 无 Chrome 模式,写成本地脚本;CDP 采集回归放真机/专门带 Chrome+Xvfb 的镜像(不含生产登录态)。

---

## 2. 数据落盘问题

### 2.1 已做对的部分

- 凭证三层防御:掩码 + AES-256-GCM 列级加密 + 轮换(旧行 revoked 释放唯一槽位)→ `credentials` 表。
- 订单/商品缓存落 PG,读取秒开;upsert 覆盖状态;商品未出现 → archived 软删;租户隔离硬约束(所有 SQL 带 tenant_id + credential_id,归属经 `get_decrypted` 校验)。
- 店铺指标快照 `store_metrics_history`、操作审计 `store_operation_log`、选品洞察 `selection_insights` 已落盘。
- 日志 RotatingFileHandler 50MB×5。

### 2.2 问题

| ID | 级别 | 问题 | 证据 | 影响 |
|---|---|---|---|---|
| P1 | **P0** | **绑定后不落盘**:`POST /credentials` 只插凭证行(status=active),无首次同步;数据要等全局 15min 调度器下一轮或用户手动点同步 | `credential_service.create_credential`、`store_sync_scheduler` | 用户绑定后看到"从未同步",体验断裂 |
| P2 | **P0** | **同步阻塞事件循环**:`store_sync_routes.sync_store` / `orders_routes?refresh=1` / 懒同步都在 `async def` 路由里直接调阻塞的 `sync_store`(订单+商品+图片批量,分钟级),期间全站 API 被卡 | `store_sync_routes.py:29-33`、`orders_routes.py:65`、`store_sync_service.list_cached_orders` | 首次打开页面可能让其他用户全部超时 |
| P3 | **P0** | **懒同步=全量同步**:读取订单/商品/统计时缓存为空 → 触发完整 `sync_store`;webui 店铺卡片 N 店并行 → 首屏 N 次全量 + 大量 Ozon 配额 | `store_sync_service.py:477/544/620` | 首屏慢、配额爆、事件循环阻塞 |
| P4 | **P0** | **无同步任务状态/进度/取消/重试**:`POST /sync` 同步阻塞等结果;调度器失败只写 error 字段,无 job 记录 | `store_sync_routes.py`、`credential_sync_state` | ERP 无法展示/审计同步过程 |
| P5 | **P0** | **无并发锁**:手动同步与调度器可同时同步同一店(仅 thread-local 防递归,不防跨线程),Ozon 双倍调用 + 指标快照重复 append | `store_sync_service.py:40-51` | 数据重复、配额浪费 |
| P6 | P1 | **订单同步静默截断**:单次最多 25 页 × 100 = 2500 单,大店首拉超过即停止且 error 置空、无 incomplete 标记 | `store_sync_service.py:34-36/145-177` | 首屏报表缺数据且不可感知 |
| P7 | P1 | **store_metrics_history 无保留策略**:15min 一次 ≈ 96 行/店/天,只增不清理;`profit_rate` 恒 NULL(无成本不编造)→ 趋势图拿不到利润 | `_append_metrics_snapshot`、model.py:997 | 表膨胀 + 趋势能力受限 |
| P8 | P1 | **订单"利润"口径失真**:`profit = amount − commission`,不含采购成本/物流;`order_notes.source_cost` 已存在但未接入利润计算 | `order_service._normalize_posting:166` | ERP 利润报表误导 |
| P9 | P1 | **采集箱图片不落盘**:草稿 envelope 只存外链 URL(1688/Ozon CDN),过期后重上架挂图;COS 只兜底上架管线生成图,不镜像草稿图 | `product_drafts.payload`、`cos_uploader` | 采集箱数据资产时效性差 |
| P10 | P1 | **身份=key**:`_key_user_id` 由 token 哈希派生租户;换 key/多 key → 数据碎片化、绑定店铺在新 key 下"消失" | `main.py:1092-1102`、webui API key 登录 | 与多店铺 ERP 模型冲突 |
| P11 | P1 | **凭证主密钥单版本**:未配 `CREDENTIAL_MASTER_KEY` 凭证 CRUD 报错;换密钥历史密文不可解密(需重建凭证) | `.env.example` 注释 | 运维事故面大,需双密钥轮换 |
| P12 | P2 | 全表只增不清理:orders cache / drafts / submissions / discovery_runs / audit_logs / image_tasks / operation_log 无保留策略 | model.py 各表 | PG 长期膨胀 |

---

## 3. 数据使用问题

| ID | 级别 | 问题 | 证据 | 影响 |
|---|---|---|---|---|
| U1 | **P0** | **analytics 跨租户聚合泄漏**:`market-overview`(全库 GMV/订单/商品)、`sales-trend`(全库按天 GMV/订单数)、`categories`(全库 discovery_runs 关键词)SQL 无 tenant 过滤,仅 token 有效性鉴权 | `analytics_routes.py:57-130/179-215` | 任意有效 token 可看全平台经营数据与竞品选品词;与"订单/商品/草稿隔离不动"约定冲突 |
| U2 | P1 | 店铺统计只有"今日"口径,无历史趋势页;`profit_trend` 依赖 metrics_history 但 profit_rate 恒 NULL | `store_sync_service.get_store_stats`、`store_analysis_service` | ERP 需要日/周/月趋势 |
| U3 | P1 | 店铺执行(`POST /stores/{id}/actions`)同步调 Ozon 批量接口,大列表阻塞事件循环;有 operation_log 但无 job/进度 | `store_actions_routes._exec_bulk_prices` 等 | 批量操作体验与可靠性 |
| U4 | P1 | 采集箱链路缺口(台账已登记):G1 discover CSV 不进 webui、G3 在线商品编辑未接线、G4 skill 批量并发弱 | `docs/TEST-ISSUES-2026-08.md` | ERP 完整度 |
| U5 | P2 | 调度器全局固定 15min,无按店启停/间隔配置;店多时一轮遍历耗时可能超过间隔(首尾漂移) | `store_sync_scheduler.py` | 灵活性差、无法按店 SLA |
| U6 | P2 | 多副本部署时每个 worker 都会跑 `store_sync_loop` → 重复同步(当前单副本无感) | `main.py:526-537` | 水平扩容即踩坑 |

---

## 4. 核心规划:绑定后的同步模型(ERP 视角)

### 4.1 现状模型

```
用户绑定 key → credentials(status=active)
    ├─ 15min 全局调度器轮询所有 active 凭证 → 逐店 sync_store(订单增量+商品全量+快照)
    ├─ 用户手动 POST /stores/{id}/sync(同步阻塞等待)
    └─ 读取兜底:订单/商品/统计缓存为空 → 懒触发全量 sync_store
```

问题:无"绑定即同步"、无任务状态、全量阻塞事件循环、无并发控制、全局固定频率、读取即触发全量。

### 4.2 推荐模型:三段式(ERP 标准)

#### ① 绑定即初始化(Initial Sync Job)

- `POST /credentials` 成功 → 立即入队 `initial` 同步 job(异步):凭证校验(复用 validate probe)→ 订单回补 90 天 → 商品全量 → 首条 metrics 快照。
- webui 店铺卡显示"首次同步中 xx%";校验失败 → 凭证标 `invalid` 且不拉数据。
- 端点改为 `POST /stores/{id}/sync` 返回 job id,前端轮询 job 状态(而非阻塞等结果)。

#### ② 按店定时增量(Scheduled Incremental)

- 每店独立调度配置:`sync_enabled` / `sync_interval_minutes`(默认 15-30)/ `paused` / `manual_only`,不再全局一刀切。
- 增量窗口保留现有"上次成功点 − 1h 重叠"逻辑;失败指数退避(如 5min→15min→1h→stale 标记),不无限重试。
- 调度器落地建议:
  - **阶段一(单副本)**:保留 worker 内循环 + **PG advisory lock**(`pg_try_advisory_xact_lock`)防手动/定时/多副本并发;循环用 `asyncio.to_thread`(已有)。
  - **阶段二(多副本/独立调度)**:拆独立 scheduler 容器 + `store_sync_jobs` 任务表,worker 只消费队列。
- 多副本时两个 worker 的 loop 都跑,但 advisory lock 保证同一店同时只有一个同步者。

#### ③ 读取只读缓存(Read From Cache Only)

- 移除"缓存空 → 触发全量同步";改为返回空列表 + `last_synced_at` + `sync_status`(syncing/ok/failed/stale)。
- webui 展示数据新鲜度,`刷新数据`/`同步数据` 按钮走 job 队列,页面即时返回。
- 店铺卡片/订单页/商品页全部依赖 ①+② 的落盘结果,首屏秒开且不爆配额。

### 4.3 配套数据模型

```text
store_sync_jobs(id, tenant_id, credential_id, kind=initial|incremental|manual,
                status=pending|running|ok|failed|canceled,
                progress, orders_synced, products_synced, error,
                trigger, started_at, finished_at)          -- 任务可见性/审计

credentials 增加:sync_enabled, sync_interval_minutes, last_sync_attempt_at
credential_sync_state 保留并扩展:last_success_at, consecutive_failures, stale_at

store_daily_metrics(store_id, stat_date, orders, gmv, commission, profit,
                    product_count, low_stock_count)          -- 从 metrics_history 定时聚合
                                                             -- 快照表只保留近 N 天,日表长期保留

orders 利润口径:接入 order_notes.source_cost + 物流费率 → real_profit
                (有成本才填,无成本保持 NULL,沿用"不编造利润"纪律)
```

### 4.4 ERP 视角的验收口径

1. 新用户绑定店铺 → 5 秒内看到"同步中",1-3 分钟看到订单/商品/统计,无需任何手动操作。
2. 店铺页永远秒开,不再因为同步阻塞其他用户请求。
3. 同一店铺同一时刻只有一个同步在跑(手动/定时互斥),快照不重复。
4. 同步失败可见、可重试、有审计(job 行 + operation_log 风格)。
5. 报表口径一致:订单利润含成本,趋势按日聚合,无成本商品利润留空。

---

## 5. 改进路线图

### P0(数据安全 / 链路断裂,先做)

| 项 | 说明 | 验证 |
|---|---|---|
| 同步异步化 | HTTP 路由包 `asyncio.to_thread` 或入 job 队列;懒同步改为只读缓存 | 并发请求压测首屏,事件循环不再卡 |
| 绑定即初始化 | create_credential 后入队 initial job + webui 进度展示 | 新绑店铺 5s 内可见同步中 |
| 同步并发锁 | PG advisory lock per (tenant, credential) | 手动+定时并发测试快照不重复 |
| analytics 隔离 | market-overview/sales-trend/categories 加 tenant 过滤或转 admin-only | 跨租户 token 只能看自己数据 |

### P1(ERP 正确性 / 容量)

| 项 | 说明 |
|---|---|
| 订单利润成本归集 | order_notes.source_cost + 物流 → real_profit,无成本保持 NULL |
| 订单同步截断修复 | 分页续传 / incomplete 标记,大店首拉完整 |
| 读取只读缓存 | 空缓存返回状态,不触发全量 |
| 指标聚合 + 保留策略 | store_daily_metrics + metrics_history 保留 N 天 |
| 身份账号化 | 账号级 tenant + key 多凭证;或至少提供 key 迁移工具 |
| 凭证密钥轮换 | 双主密钥版本 + 历史密文重加密迁移脚本 |

### P2(体验 / 运维)

| 项 | 说明 |
|---|---|
| 本地 Docker 测试一键化 | docker-compose.test.yml + test-docker.sh,密码统一 |
| deploy.sh 构建/校验 webui | dist 缺失即提示,避免空挂载 |
| 采集箱图片镜像 | 采集/编辑时镜像草稿图到 COS,防外链失效 |
| discover → webui 消费 | 落实 G1;编辑接线 G3;skill 并发 G4 |
| 店铺执行任务化 | actions 入 job 队列 + 进度,保留 operation_log |
| PG 自动备份 | cron pg_dump + 保留策略 + 恢复演练 |
| 调度器演进 | 多副本阶段拆独立 scheduler + store_sync_jobs 消费 |

---

## 5.1 M5b 落地补充(2026-08-30)

按 [PRD-store-sync-erp-v1.md](PRD-store-sync-erp-v1.md) 补齐三件收尾:

| 项 | 落地 | 竞态/守卫 |
|---|---|---|
| 草稿图片镜像(COS) | POST/PATCH /drafts 异步下载 ≤5 张草稿图转存 COS,`image_mirror_state` 状态列 + CollectionPanel 徽标;`worker/scripts/backfill_draft_image_mirror.py` 存量回填 | R8:回写按 payload version 守卫,版本已变丢弃并告警;COS 未配置静默降级保持外链 |
| 货源匹配 source_candidates | `POST /api/v1/source-candidates`(skill 跟卖/图搜上报,client_id→credential 解析,缺店全零占位)+ discover 回调派生 + 上架成功 envelope 回填;`GET /products/{id}/source-candidates` 供货源工作台 | 唯一键 (tenant, credential, product, source_offer) 幂等 upsert;match_method 白名单 |
| 采集箱 CSV 导入 | `POST /api/v1/drafts/import`(JSON rows / text/csv 双格式,≤500 行) | 逐行复用 create_draft(凭证剥离/字段校验/镜像),失败行报 error 不阻断;重复导入不幂等(采集箱语义) |

### 5.2 第二轮补充(2026-08-30)

| 项 | 落地 |
|---|---|
| 采集箱 CSV 导出 | `GET /api/v1/drafts/export`(UTF-8 BOM,Excel 兼容)+ webui「导出 CSV」按钮 |
| 店铺数据硬删除 | `DELETE /credentials/{id}/data?confirm=true`:管理端授权 + 二次确认 + `ADMIN_HARD_DELETE_ENABLED=1` 开关;16 张店级表单事务清除 + operation_log 审计;凭证吊销记录与用户草稿保留 |
| 阶段二设计预留 | `docs/DESIGN-PHASE2-PURCHASE-ADS-RECONCILIATION.md`:采购单/广告 OAuth/财务对账 + 用户视角补充项 |

### 5.3 第三轮:webui 演示数据清零(上生产前)

| 项 | 落地 |
|---|---|
| 工作台 `/` | 新 `GET /api/v1/dashboard/overview`(今日订单/销售额/在售/待办/14 天趋势/热销 Top5/最近订单,租户隔离只读 PG);Dashboard 静态数据全部移除 |
| 系统设置 `/settings` | 新 `user_settings` 表 + `GET/PUT /api/v1/settings`(业务参数/通知设置,数值范围校验);管理员附加物流费率 tab(列表/改价/CSV 导入) |
| 商品编辑抽屉 | 真实链路:读 `/products/{id}/edit`(新增 draft_version)→ PATCH 关联草稿 → submit(update_product_id 更新上架);原 mock 保存/发布按钮全部接 API |
| 采集箱 | 全部平台/全部状态筛选实装(select)、批量提交 UI(`/drafts/batch-submit`)、CSV 导入按钮(`/drafts/import`) |
| 商品管理 | 货源工作台:`/products/ozon?source=matched\|unmatched` 筛选 + 成本抽屉展示 skill/discover 上报候选 |
| 选品广场 | 品牌/月销区间/均价区间真实筛选(服务端),「采集」真实建草稿(market 源);失败不再静默展示示例数据(保留提示文案) |
| 站点管理 | 横幅/公告创建与删除(admin CRUD),字段对齐后端 `enabled`/`announcement_type`(原 `is_active`/`priority` 类型与后端不符) |
| 平台后台 | 新增「生图配置」tab(/admin/config 读写/备份回滚) |
| 死代码 | 删除未路由的 Listing 路由、Templates()/Pricing()/Admin()/DataTable 演示组件(约 100+ 行写死数据) |
| 登录余额卡 | `GET /api/v1/mxou/balance` 接 MXOU 平台真实余额,查询失败 fail-open 显示 —(不再静态「登录后读取」) |
| 接线契约测试 | `worker/tests/test_webui_contract.py`:扫描 webui 全部 API 调用(含 fetch/EventSource/downloadCsv),
  归一化后与 worker OpenAPI 实际路由比对,缺失即失败——「前端按钮 → 接口」链路进 CI 自动回归 |

回归:worker **1454 passed**、skill **607 passed**、webui `tsc -b` 0 错误 + `bun run build` 通过。

三端回归:worker **1444 passed**、skill **607 passed**、webui `tsc -b` + `bun run build` 全绿。

## 6. 附:本次核实的关键代码位置
## 6. 附:本次核实的关键代码位置

- 绑定入口:`worker/src/services/credential_service.py` `create_credential`(无首次同步)
- 调度器:`worker/src/services/store_sync_scheduler.py`(15min 全局轮询,无锁)
- 同步服务:`worker/src/services/store_sync_service.py`(阻塞实现、懒同步、2500 单截断、快照 NULL profit)
- 读取路由:`worker/src/routes/orders_routes.py:60-80`、`store_sync_routes.py:29-33`(async 路由直接阻塞调用)
- 跨租户聚合:`worker/src/routes/analytics_routes.py`(market-overview / categories / sales-trend)
- 身份派生:`worker/src/main.py:1092-1102` `_key_user_id`
- 采集箱:`worker/src/storage/database/shared/model.py` `ProductDraft/DraftSubmission`;`webui/src/components/CollectionPanel.tsx`
- 台账缺口:`docs/TEST-ISSUES-2026-08.md` G1/G3/G4
