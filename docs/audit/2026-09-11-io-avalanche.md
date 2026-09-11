# 审计：2026-09-10/11 生产 I/O 雪崩 9 小时不可用事故

> 定稿：2026-09-11。触发批次：`fix/deploy-hardening-v1`（本审计所列 P0 项的修复实现）。
> 证据来源：sar（sa10/sa11）、PG 日志、/var/log/messages、journal --list-boots、
> pytest 日志 mtime 反推、仓库侧代码核对（conftest / docker-compose / 部署脚本）。

## 一、事故时间线（北京时间）

| 时间 | 事件 | 证据 |
|---|---|---|
| 09-10 12:10 | **sar 数据文件停写**（sa10 最后一条；此前负载正常 <0.2）——比 pytest 启动还早 ~2 分钟，见 §三.1 | /var/log/sa/sa10 |
| 09-10 12:11:57 | v0.74 全量测试套件启动（**直连生产库**），将跑 18 小时 | pytest 日志时长 18:10:58 反推 |
| 09-11 06:22:17 | PG 首次告警：autovacuum worker took too long to start | PG 日志 |
| 09-11 06:22:29 | 峰值快照：load=187.56 / iowait=72.65% / 磁盘 util 93%、队列 72.5、1428tps / 内存 75%、commit 138%；同分钟 Hermes agent 500K token 压缩 + deepseek 402 | sar sa11、/var/log/messages |
| 09-11 06:22:55 / 06:23:47 | 两个测试进程先后终止（日志停写） | pytest 日志 mtime |
| 09-11 10:36:28 | journald 最后一条写入（此后磁盘无法写入） | journal --list-boots |
| 09-11 10:52:38 | PG checkpoint 完成：sync 累计 14,095s（3.9h），单文件 fsync 最长 2,431s | PG 日志 |
| 09-11 15:43:29 | 实例硬重启（无优雅关机记录） | journal boot 0 |
| 09-11 15:43:39 | PG crash recovery → redo → ready（**零数据丢失**；listing_result_log 216 条完好） | PG 恢复日志 |
| 09-11 15:45 | 服务全恢复（health 200） | curl |

影响：约 9 小时不可用（06:23 实质僵死 → 15:43 恢复；10:36 起完全无响应）。中断窗口无用户任务。

## 二、根因（分层）

**直接原因：单云盘 I/O 资源耗尽 → 系统级 I/O 雪崩。** 内核日志零 I/O 错误
（无 blk_update_request / hung_task）→ 纯饱和，非硬件故障；零数据丢失与此自洽。

**触发链（权重从主到次）：**
1. **v0.74 全量测试套件直连生产库跑 18 小时**——唯一持续供压的进程。与生产流量
   抢同一块盘的 IOPS，1.3GB 测试临时文件也写同盘 /tmp。
2. **PG checkpoint/autovacuum 背压正反馈**——把持续压力转化为雪崩的机制：
   fsync 越等越久 → 脏页越积越多 → 单文件 fsync 40 分钟。
3. Hermes agent 500K token 会话压缩（06:22:29，与峰值快照同分钟）——最后一根
   稻草/同刻症状，非并列主因（一次几百 MB 顺序写在 128MB/s 盘上只占数秒）。
   deepseek 402 为 LLM 配额问题，与磁盘无关。

**放大因素：**
- 规格：4c / 3.7G / 单云盘（PG + Docker + 系统 + /tmp + 备份共用）；
- PG **全默认参数**（shared_buffers=128MB），compose **零资源限制**（无 mem_limit/
  cpus/blkio——仓库侧核实 deploy/docker-compose.yml 全文件无任何限制键）；
- 页缓存仅 473MB、kbcommit 138%（超配）→ 大表反复穿透到盘；
- **无外部监控**：sar 09-10 12:10 死、journald 09-11 10:36 死、Sentry 09-01 起断流
  ——机器级僵死 9 小时无任何外部信号。

### 1. 被忽略的信号：sa10 停写时间

sa10 最后一条在 09-10 12:10，**比 pytest 启动（12:11:57）早约 2 分钟**。两种解释：
(a) sadc 在 pytest 启动后第一个采样点（12:20）即被 I/O 饿死 → 饱和从 ~12:20 就开始，
18 小时不是「缓慢累积」而是「持续煎熬」，06:22 只是 PG 耐心耗尽第一次报警；
(b) sysstat 采集早已损坏 → 18 小时负载曲线全盲区，又一个监控缺失实锤。
无论哪种，「三件事在 06:22 叠加触发」的时间叙事证据不足——06:22 是第一个有数据的
时刻，不一定是饱和起点。

### 2. 机制补全：测试为什么能连上生产库（仓库侧答案）

- `worker/tests/conftest.py` 默认回落 `postgresql://...@localhost:5433/ozon`，
  对 `PGDATABASE_URL` 指向哪**零校验**；
- 生产 `deploy/docker-compose.yml` 把 PG 发布在 `127.0.0.1:5433`——与本地开发
  惯例端口（localhost:5433 = 本地 Docker PG）**撞车**：同一条在开发机安全的命令，
  在服务器上原样执行 = 直连生产库；
- AGENTS.md「禁止打生产」的纪律一直存在，但无技术强制，且此场景下违规是**无声的**。

### 3. 与「特征属性缓存」的关系（澄清）

不是缓存导致的：去重后仅 175MB(dict)+50MB(schema)，PG 无错误、死元组 0。
但缓存灌满生产库后，直连测试从 62s（空库）膨胀到 18h（复合结果：随机读放大 +
与生产流量锁竞争 + /tmp 同盘 + 云盘 IOPS 配额低 + checkpoint 背压），把夜间负载
从轻变重。缓存是「无害的数据」，事故是「小盘 + 并发重负载 + 无监控」的组合。

## 三、隐患清单（H1-H12）与本批修复映射

| # | 隐患 | 等级 | 处置 |
|---|---|---|---|
| H1 | PG 容器日志无上限（v0.72 防复发漏了 postgres） | P0 | ✅ compose 补 logging 50m×3 + 卫生测试锁不变式 |
| H2 | 备份与数据同盘、零异地副本、恢复零演练 | P0 | ✅ backup-upload-cos.sh（cron 上传 COS）；演练见 ops 清单 |
| H3 | PG 全默认参数 + 全服务零资源限制 | P0 | ✅ compose 调参 command + mem_limit/shm_size（4c/3.7G 校准） |
| H4 | 测试可直连生产库（端口撞车 + conftest 零校验） | P0 | ✅ 端口 5433→15433 + prod_marker 闸（conftest 拒跑 exit 2） |
| H5 | 无外部存活监控（机器级死亡无感知 9h） | P0 | ✅ DEPLOY.md 新增 dead-man 节；注册动作在 ops 清单 |
| H6 | 服务器 ops 欠账（v0.72 TRUNCATE+重预热未执行） | P1 | 📋 ops 清单（盘上仍压着旧放大器） |
| H7 | cos-update 首次升级盲区（旧脚本执行首次升级） | P1 | 📋 ops 清单（先手动落新脚本一次性交接） |
| H8 | CREDENTIAL_MASTER_KEY 缺失仅事后提示 | P1 | ✅ 前移至步骤 0 fail-fast（逃生门 env） |
| H9 | schema 迁移无版本闸（漏登记新列=运行时 500） | P1 | ✅ 二批：cos-update init_data 失败 warn→fail（schema 半就绪不再静默）；「期望版本清单」闸不做——schema_migrations 设计上是观测面非闸门（init_data.py:28 注释），且防不了「忘了登记」本身，DB-SCHEMA-AUDIT 纪律维持 |
| H10 | 升级=本机 no-cache 全量重建（升级窗口即 I/O 负载事件） | P1 | 📋 DEPLOY.md 升级窗口纪律（避开高峰） |
| H11 | pg_isready 硬编码用户/库名；容器 root 运行；0.0.0.0:8080 直暴 | P2 | ✅ healthcheck env 化；二批补 `WORKER_BIND_IP` 可收紧绑定（默认不变）；**root 缓期**：存量命名卷（logs）与 bind mount（config rw）均为 root 属主，切 USER 会断 `/admin/config` 写入与日志卷——需带服务器实测的专项迁移，收益不抵断产风险 |
| H12 | tag 无 CI 闸（cd.yml 由 tag 直接触发，绿的保证纯靠纪律） | P2 | ✅ 二批：cd.yml version-check 加闸——tag commit 必须有 conclusion=success 的 CI run（轮询等待 15min；逃生门 `CD_SKIP_CI_GATE=1` 兜 docs-only 被 paths-ignore 跳过的场景） |

## 四、遗留（结构性，单独立项）

- 数据盘分离 / 内存升档（4c/3.7G 低于「PG + 50 并发 worker + webui + 运维」负载
  下限；升配后 compose 调参等比上调——注释已写明）；**需用户拍板（花钱）**；
- staging 环境或严格本地测试纪律的技术强制面扩大；**需用户拍板（建环境）**；
- Sentry 09-01 起断流：**2026-09-11 服务器重启（15:43）后复测仍 7 天零 error 事件**
  （halo-fx/pouding_ozon）——SDK 未恢复上报，基本坐实生产 .env 的 SENTRY_DSN
  缺失/被吞；进 ops 清单第 7 条；
- H11 root 运行专项（见 §三 行内缓期理由）。

## 五、服务器 ops 清单（随本批 PR 交付，人工执行）

1. W1-W8 欠账：TRUNCATE dictionary_value_cache → ≤2 分片重预热 → export 上 COS；
2. 首次升级前手动落新 cos-update.sh（自举盲区一次性交接）；0.74→0.75 升级带入
   新 compose（调参/端口/mem_limit 生效）；
3. 外部 dead-man 注册（healthchecks.io / UptimeRobot，1-5min ping /api/v1/health）；
4. crontab 部署备份上传行（`10 4 * * * cd <deploy 目录> && bash backup-upload-cos.sh`）
   + 确认服务器 coscli 可用（与 CACHE-WARM-RUNBOOK 同配置）；
5. PG 恢复核查：WAL 回收正常 / autovacuum 正常启动 / 无残留告警 / dead tuples；
6. 恢复演练一次并回填 RESTORE-RUNBOOK（含 prod_marker 重写验证）；
7. Sentry 断流核验：重启后仍零事件——检查生产 `deploy/.env` 的 `SENTRY_DSN`
   是否为空（为空则按 .env.example 补 DSN 并 `docker compose up -d` 重建生效）。
