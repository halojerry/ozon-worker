# PRD: CI/CD 规范化 + Worker 稳定性增强 v1

> 版本：v1.0 | 日期：2026-08-07 | 状态：Draft
> 依赖：CICD-ASSESSMENT.md（评估报告）、PRD-worker-stability v4-v9（应用层稳定性）

## 1. 背景与动机

### 1.1 为什么现在做

前轮评估（`CICD-ASSESSMENT.md`）发现 12 个问题，其中 3 个是 P0/P1 级别：

| 级别 | 问题 | 影响 |
|------|------|------|
| **P0** | `_current_task_id` 模块级全局变量，多并发互相覆盖 | 数据正确性 bug，日志/进度串号 |
| **P1** | CI quality 用 `\|\| true` 吞错误，CI 永远绿 | 形同虚设，无法阻断低质量合入 |
| **P1** | 无自动部署，人肉 SSH | 易错、无审计、慢 |
| **P1** | `update.sh --force-recreate` 中断运行中任务 | 用户任务被强杀，需等 zombie cleanup 60s 重拾 |

同时服务器无法连接 GitHub，需要 COS 作为部署包分发渠道。

### 1.2 目标用户与场景

- **开发者**：ozon-worker 维护团队（1-3 人），日常 push → 自动检查 → 自动部署
- **服务器**：单台 4C8G 云服务器（腾讯云/阿里云），无法访问 GitHub
- **用户**：Ozon 卖家（多租户），同时提交上架任务的并发数 ≤ 30

### 1.3 设计原则

1. **渐进式**：不改架构，在现有 Compose 体系上增量改进
2. **向后兼容**：所有改动可灰度，不影响现有生产环境
3. **先修 bug，再做增强**：P0 竞态 → CI 阻断 → 自动部署 → 多副本
4. **自动化优先**：一切人工操作都应被 CI/CD 替代

---

## 2. 范围与非范围

### 2.1 范围内

- CI 流水线合并 + 质量门禁
- CD 自动部署（含 COS 分发渠道）
- `_current_task_id` 竞态修复
- `_task_progress` 状态外置（内存 → Redis）
- 优雅关闭（drain → restart）
- 多副本支持（Compose scale）
- 安全扫描（镜像 + 依赖 + 密钥）
- Dockerfile 多阶段构建
- 监控与告警增强

### 2.2 非范围

- 引入 K8s（当前规模不需要，见 `CICD-ASSESSMENT.md` 第五节）
- 应用层稳定性（LLM 降级、Ozon API 重试、生图兜底 — 已有 v4-v9 PRD 覆盖）
- Skill 端改动（Skill 在客户本地运行，不涉及本次）

---

## 3. 需求详述

### 3.1 CI 流水线合并 + 质量门禁

#### 3.1.1 现状

```
ci.yml (push/PR)     worker-ci.yml (worker/** 变更)
├── repo-hygiene      ├── setup python
├── syntax (py_compile) ├── ruff lint
├── quality (pyflakes || true)  ← BUG  ├── pytest + PG service
├── imports           ├── docker build
└── docker build      └── docker test

     ↑ 职责重叠，同一个 push 跑两套，lint 标准不一致
```

#### 3.1.2 目标

合并为单一 `ci.yml`，使用 path filter 区分 worker/skill 子任务矩阵：

```
ci.yml (push/PR)
├── repo-hygiene（全仓）
├── secret-scan（gitleaks，全仓）
├── matrix: worker
│   ├── ruff check（阻断，不再 || true）
│   ├── pytest + PG service container
│   ├── docker build（仅 PR merge 到 main 时）
│   └── pip-audit（依赖漏洞扫描）
└── matrix: skill
    ├── ruff check
    ├── pytest
    └── compile check（Cython 能否编译）
```

**关键变更**：
- 去掉 `|| true`，ruff 报错直接 fail job
- 合并 ci.yml + worker-ci.yml，消除重复
- 加 gitleaks 全仓密钥扫描（替代当前 pre-commit 的单一 JWT 检查）
- 加 pip-audit 依赖漏洞扫描

#### 3.1.3 验收标准

- [ ] 有 lint 错误的 PR 被 CI 阻断（红色）
- [ ] push 到 main 只触发一套 CI（不再双跑）
- [ ] gitleaks 扫描到 AWS/AKSK/JWT 等密钥模式时阻断
- [ ] pip-audit 发现 CVE 时 CI 警告（首次不阻断，逐步收紧）

---

### 3.2 CD 自动部署 + COS 分发

#### 3.2.1 现状

```
tag v* 推送 → cd.yml
├── docker build + push GHCR  
└── create GitHub Release
     ↓ （无人触发部署）
部署靠人肉 SSH 服务器跑 update.sh（git pull + docker compose build）
     ↓
服务器无法访问 GitHub → git pull 失败 → 无法部署
```

#### 3.2.2 目标

```
tag v* 推送 → cd.yml
├── docker build + push GHCR（保留，备用）
├── create GitHub Release（保留，changelog 文档）
├── 打包部署源码 tar.gz
├── 上传 COS（/ozon-worker/ozon-worker-deploy-v{version}.tar.gz）
├── 更新 COS manifest.json（latest 版本指针 + sha256）
└── [可选] SSH 到生产服务器执行 cos-update.sh
```

**COS 路径规范**：
```
COS Bucket: {bucket}
├── ozon-skill/          # Skill 分发（已有）
│   ├── manifest.json
│   └── pounding-ozon-probe-v{version}.tar.gz
└── ozon-worker/         # Worker 部署（新增）
    ├── manifest.json    # {"version": "0.28.0", "package": "...", "sha256": "...", "url": "..."}
    └── ozon-worker-deploy-v{version}.tar.gz
```

**服务器端 cos-update.sh 流程**：
```
1. 读 COS manifest.json → 获取最新版本号 + sha256
2. 对比本地 VERSION 文件 → 已是最新则跳过
3. 下载 tar.gz → sha256 校验
4. 备份当前 deploy/ + worker/ + VERSION
5. 解压覆盖（保留生产 .env 不覆盖）
6. docker compose build --no-cache
7. 优雅关闭旧容器（drain → stop → start new）
8. 健康检查 → 失败则自动回滚到备份
```

#### 3.2.3 验收标准

- [ ] tag push 后 COS 出现 `ozon-worker-deploy-v{version}.tar.gz`
- [ ] COS manifest.json 指向最新版本，sha256 与本地一致
- [ ] 服务器执行 `bash deploy/cos-update.sh` 成功升级
- [ ] 服务器指定版本 `bash deploy/cos-update.sh v0.27.0` 可回滚
- [ ] .env 在生产更新后未被覆盖
- [ ] 健康检查失败时自动回滚到备份版本

---

### 3.3 并发竞态修复：`_current_task_id` → contextvars

#### 3.3.1 现状与 bug 分析

```python
# main.py 第 40 行
_current_task_id: str | None = None  # 注释说 "thread-local"，实际是模块级全局

def set_current_task_id(task_id: str | None):
    global _current_task_id      # ← 多并发任务互相覆盖
    _current_task_id = task_id
```

**复现路径**：
1. 任务 A 进入 `worker_loop`，`set_current_task_id("task-A-xxx")`
2. 任务 A 在 `await asyncio.sleep(...)` 处让出事件循环
3. 任务 B 进入 `worker_loop`，`set_current_task_id("task-B-yyy")`
4. `_current_task_id` 现在是 `"task-B-yyy"`
5. 任务 A 的 `ProgressLogger` 读取 `get_current_task_id()` → 拿到 `"task-B-yyy"`
6. **日志串号、进度写入错误 task_id、Sentry span 挂错 trace**

#### 3.3.2 修复方案

```python
import contextvars

_current_task_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_task_id", default=None
)

def set_current_task_id(task_id: str | None):
    _current_task_id.set(task_id)

def get_current_task_id() -> str | None:
    return _current_task_id.get()
```

**影响范围**：
- `main.py`：`set_current_task_id` / `get_current_task_id`（2 处改动）
- `task_processor.py`：进度回调中调用 `set_current_task_id`（不变，自动隔离）
- `utils/logger.py`：`set_trace_context` / `clear_trace_context`（需确认是否也受此影响）

#### 3.3.3 验收标准

- [ ] 3 个并发任务同时运行时，各任务 `get_current_task_id()` 返回自己的 ID
- [ ] 日志中 task_id 不串号
- [ ] 进度 API 返回正确 task 的进度

---

### 3.4 `_task_progress` 状态外置：内存 → Redis

#### 3.4.1 现状

```python
# main.py 第 39 行
_task_progress: Dict[str, Dict[str, Any]] = {}  # 进程内字典
```

**问题**：
- 容器重启 → 进度全清空（PG 有 2s 节流快照，但丢失了最近 2s 的更新）
- 多副本部署时各副本进度独立，请求落到不同副本看到不同进度
- 无过期清理，长期运行内存泄漏

#### 3.4.2 修复方案

**阶段 1：Redis 优先 + PG 持久化（推荐）**

```
写进度: Redis SETEX "task_progress:{task_id}" <json> EX 3600  +  PG UPDATE（2s 节流不变）
读进度: Redis GET → 回退 PG SELECT → 回退默认值
```

Redis key 设计：
```
task_progress:{task_id}  →  {"stage": "image_generation", "percent": 65, ...}
                          TTL: 3600s（任务最长 30min + buffer）
```

**阶段 2（可选）：纯 Redis + 异步落 PG**

进度只写 Redis，任务完成后一次性写入 PG 的 result 字段。减少 PG 写入压力。

#### 3.4.3 验收标准

- [ ] 容器重启后进度 API 仍返回正确进度（从 Redis 恢复）
- [ ] 2 副本同时运行时，任意副本的进度 API 返回一致
- [ ] Redis 不可用时优雅降级到 PG（不阻断主流程）
- [ ] 已完成任务的 Redis key 在 TTL 后自动清理

---

### 3.5 优雅关闭（Graceful Shutdown）

#### 3.5.1 现状

`update.sh` 用 `docker compose up -d --force-recreate`，docker 发 SIGTERM → 容器立即停 → 所有运行中的 LangGraph 任务被杀。任务状态在 PG 中是 `running`，需等 `_periodic_task_cleanup`（60s 一次）重置为 `pending` 才被重新拾取。

#### 3.5.2 修复方案

**FastAPI 生命周期管理**：

```python
import signal

SHUTDOWN_FLAG = False

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动 worker loops...
    yield
    # 关闭：发信号 → 等任务排空 → 停 worker
    SHUTDOWN_FLAG = True
    await drain_running_tasks(timeout=300)  # 最多等 5 分钟
    for task in running_tasks.values():
        task.cancel()
```

**worker_loop 改造**：
- 收到 shutdown 信号后不再从 PG 拉新任务（`process_next_task` 检查 `SHUTDOWN_FLAG`）
- 等待 `running_tasks` 全部完成（或超时 5 分钟）
- 超时后 cancel 剩余任务（任务状态保持 `running`，zombie cleanup 兜底）

**docker-compose.yml 改造**：
```yaml
worker:
  stop_grace_period: 5m  # 从默认 10s → 5 分钟
```

#### 3.5.3 验收标准

- [ ] `docker compose down` 后，运行中任务在 5 分钟内正常完成
- [ ] 5 分钟后仍未完成的任务被安全取消，状态保持 `running`
- [ ] zombie cleanup 在 60s 内将残留 `running` 任务重置为 `pending`
- [ ] shutdown 期间不接收新任务

---

### 3.6 多副本支持（Compose Scale）

#### 3.6.1 现状

单 worker 实例，`uvicorn workers=1`。docker-compose 无 scale 配置。

#### 3.6.2 目标

支持 `docker compose up -d --scale worker=2`，2 个 worker 实例共享 PG + Redis。

**前提条件**（必须在前面的改动完成后才能做）：
1. `_current_task_id` 已改为 contextvars（3.3）
2. `_task_progress` 已外置 Redis（3.4）
3. PG 任务认领已有 `FOR UPDATE SKIP LOCKED`（✅ 已实现，task_processor.py 第 220 行）

**docker-compose.yml 改造**：
```yaml
worker:
  deploy:
    replicas: 2  # 或通过 --scale 命令行指定
  stop_grace_period: 5m
  healthcheck:
    test: ["CMD", "curl", "-f", "http://localhost:5000/health"]
    interval: 10s   # 从 30s 缩短
    timeout: 3s     # 从 5s 缩短
    start_period: 15s
    retries: 3
```

**nginx / traefik 反向代理**（如需负载均衡）：
```yaml
nginx:
  image: nginx:alpine
  ports:
    - "8080:80"
  volumes:
    - ./nginx.conf:/etc/nginx/nginx.conf:ro
  depends_on:
    - worker
```

#### 3.6.3 验收标准

- [ ] `docker compose up -d --scale worker=2` 启动 2 个 worker
- [ ] 提交 5 个任务 → 2 个 worker 各认领不同任务（不重复）
- [ ] 进度 API 查询任意副本返回一致结果
- [ ] 停掉 1 个副本 → 其运行中任务被 zombie cleanup 重分配给剩余副本

---

### 3.7 安全扫描

#### 3.7.1 现状

无任何安全扫描。pre-commit 只检查一个固定的 JWT 样例字符串。

#### 3.7.2 目标

| 扫描类型 | 工具 | 触发时机 | 阻断策略 |
|----------|------|----------|----------|
| 密钥泄露 | gitleaks | CI（每次 push） | 阻断 |
| 依赖漏洞 | pip-audit | CI（每次 push） | 首次警告，稳定后阻断 |
| 镜像 CVE | trivy | CD（tag push） | 严重/高危阻断，中低危警告 |

#### 3.7.3 验收标准

- [ ] gitleaks 扫描到 AWS AK/SK 模式时 CI 变红
- [ ] pip-audit 发现 CVE-2024-xxxx 时 CI 输出警告
- [ ] trivy 发现 CRITICAL CVE 时 CD 不推送镜像

---

### 3.8 Dockerfile 多阶段构建

#### 3.8.1 现状

单阶段构建，最终镜像包含 `build-essential`、`libpq-dev`、`git` 等编译工具链（约 +200MB）。

#### 3.8.2 目标

```dockerfile
# Stage 1: builder
FROM python:3.12-slim AS builder
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev git && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --user --no-cache-dir -r requirements.txt

# Stage 2: runtime
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 curl && rm -rf /var/lib/apt/lists/*
COPY --from=builder /root/.local /root/.local
COPY worker/ /app/
ENV PATH=/root/.local/bin:$PATH
CMD ["python", "-m", "src.main", "-m", "http"]
```

**预期收益**：镜像从 ~800MB → ~350MB。

#### 3.8.3 验收标准

- [ ] 构建后镜像大小 < 400MB
- [ ] `docker compose up` 后 `/health` 正常
- [ ] 所有 pytest 通过

---

### 3.9 监控与告警增强

#### 3.9.1 现状

已接入 Sentry（v0.23），覆盖任务异常/超时。缺少：
- 资源使用告警（CPU/内存/磁盘）
- 任务队列深度告警
- 部署事件通知

#### 3.9.2 目标

| 指标 | 采集方式 | 告警阈值 | 通知渠道 |
|------|----------|----------|----------|
| Worker CPU > 80% | docker stats → Sentry metric | 持续 5 分钟 | 企业微信/钉钉 webhook |
| Worker 内存 > 85% | docker stats | 持续 5 分钟 | 同上 |
| PG 磁盘 > 80% | PG `pg_stat_database` | 即时 | 同上 |
| pending 任务堆积 > 50 | PG SELECT COUNT | 持续 10 分钟 | 同上 |
| 部署成功/失败 | CD pipeline | 每次 | 同上 |

#### 3.9.3 验收标准

- [ ] CPU/内存告警在企业微信收到通知
- [ ] pending 任务堆积时收到告警

---

## 4. 实施计划

### 4.1 Phase 1：修 bug + CI 阻断（Week 1，1-2 天）

| 序号 | 任务 | 预估 | 依赖 |
|------|------|------|------|
| 1.1 | `_current_task_id` → contextvars | 1h | - |
| 1.2 | ci.yml 合并 + quality 去 `\|\| true` + 统一 ruff | 2h | - |
| 1.3 | 加 gitleaks 扫描 | 1h | 1.2 |
| 1.4 | 加 pip-audit（警告模式） | 0.5h | 1.2 |

**交付物**：CI 阻断低质量代码，并发竞态 bug 修好。

### 4.2 Phase 2：CD 自动化 + COS 分发（Week 1-2，2-3 天）

| 序号 | 任务 | 预估 | 依赖 |
|------|------|------|------|
| 2.1 | cd.yml 加打包 + COS 上传 job | 2h | - |
| 2.2 | 新建 `deploy/cos-update.sh` | 2h | 2.1 |
| 2.3 | docker-compose.yml 加 `stop_grace_period` | 0.5h | - |
| 2.4 | 优雅关闭（FastAPI lifespan drain） | 3h | 2.3 |
| 2.5 | COS 公网端点验证 + 部署测试 | 1h | 2.1-2.4 |

**交付物**：tag push → COS 自动有包，服务器 `bash cos-update.sh` 一键升级。

### 4.3 Phase 3：状态外置 + 多副本（Week 2-3，3-4 天）

| 序号 | 任务 | 预估 | 依赖 |
|------|------|------|------|
| 3.1 | 部署 Redis（docker-compose 加 service） | 1h | - |
| 3.2 | `_task_progress` 写 Redis + PG 双写 | 3h | 3.1 |
| 3.3 | 进度读 Redis → PG 回退 | 1h | 3.2 |
| 3.4 | docker-compose scale=2 配置 + nginx 反向代理 | 2h | 3.1-3.3 |
| 3.5 | 多副本并发测试（5 task × 2 worker） | 2h | 3.4 |

**交付物**：2 副本稳定运行，进度不丢，任务不重复认领。

### 4.4 Phase 4：安全 + 镜像优化 + 监控（Week 3-4，2-3 天）

| 序号 | 任务 | 预估 | 依赖 |
|------|------|------|------|
| 4.1 | trivy 镜像扫描（CD job） | 1h | - |
| 4.2 | Dockerfile 多阶段构建 | 2h | - |
| 4.3 | 资源监控 + webhook 告警 | 3h | - |
| 4.4 | 部署事件通知（企业微信 webhook） | 1h | - |

**交付物**：镜像减半，安全扫描就绪，告警到位。

---

## 5. 技术决策记录

### 5.1 为什么不用 K8s

见 `CICD-ASSESSMENT.md` 第五节。核心原因：
- 单台 4C8G 服务器，K8s 控制面吃掉一半资源
- 并发瓶颈是外部 API 限流，不是 Worker 算力
- `docker compose scale` + `FOR UPDATE SKIP LOCKED` + Redis 已满足 2 副本需求
- 只有跨多节点 / 多租户隔离 / 99.9% SLA 时才值得引入 K8s

### 5.2 为什么用 Redis 而不是纯 PG

- Redis 读写延迟 < 1ms vs PG 2-5ms（进度更新高频，2s 节流前每次节点切换都写）
- Redis TTL 自动清理（已完成任务的进度不需要永久存储）
- Redis 本身就是 Compose 生态标配（加一个 service 即可）
- PG 仍做持久化（任务完成后 result 字段完整保留）

### 5.3 为什么 COS 源包而不是 Docker 镜像

- 源码 tar.gz ~15MB vs 镜像 tar.gz ~500MB
- COS 流量费便宜但大文件下载也慢
- 服务器已具备构建能力（Docker + docker compose）
- 以后需要快速回滚时可以补镜像推 COS 方案

---

## 6. 风险与缓解

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| Redis 不可用 | 低 | 进度读到旧数据 | PG 回退 + Redis 重连 |
| COS 上传失败 | 中 | 服务器拿不到更新包 | 3 次重试 + GitHub Release 兜底 |
| 多副本任务重复认领 | 低 | 同一商品上架 2 次 | `FOR UPDATE SKIP LOCKED` 已就绪 |
| 优雅关闭超时 | 中 | 任务被强杀 | 5 分钟 drain + zombie cleanup 兜底 |
| contextvars 兼容性 | 低 | 旧 Python 版本不支持 | 项目已用 Python 3.12，完全支持 |

---

## 7. 成功指标

| 指标 | 当前值 | 目标值 | 测量方式 |
|------|--------|--------|----------|
| CI 阻断率 | 0%（`\|\| true` 永不红） | > 0%（有 lint 错误就红） | CI job 状态 |
| 部署时间 | 人肉 10-30 分钟 | < 5 分钟（含构建） | cos-update.sh 计时 |
| 部署中断任务 | 每次（--force-recreate） | 0（drain 完成后再停） | PG running→pending 重置次数 |
| 并发 task_id 串号 | 高概率（3+ 并发必现） | 0 | contextvars 隔离后自动化测试 |
| 进度可恢复 | 重启丢失最近 2s | 重启不丢（Redis） | 重启前后进度 API 一致性 |
| 镜像大小 | ~800MB | < 400MB | docker images |
| 安全漏洞 | 未知 | 0 CRITICAL/HIGH CVE | trivy 扫描 |

---

## 8. 附录

### A. 相关文档

- `docs/CICD-ASSESSMENT.md` — 评估报告（本文档的前置分析）
- `docs/WORKER-TOPOLOGY.md` — Worker 拓扑 + 数据流
- `docs/DEPLOY.md` — 部署指南
- `docs/LOGGING.md` — 日志系统
- `docs/PRD-worker-stability-v9.md` — 应用层稳定性（LLM 降级、属性填写等）

### B. COS 配置一览

| 配置项 | 用途 | 存储位置 |
|--------|------|----------|
| `COS_SECRET_ID` | API 密钥 ID | GitHub Secrets + 服务器 .env |
| `COS_SECRET_KEY` | API 密钥 | GitHub Secrets + 服务器 .env |
| `COS_BUCKET` | 存储桶名称 | GitHub Secrets + 服务器 .env |
| `COS_REGION` | 地域（默认 ap-guangzhou） | GitHub Secrets + 服务器 .env |
| `COS_PUBLIC_DOMAIN` | 自定义域名（可选） | 服务器 .env |

### C. cos-update.sh 使用示例

```bash
# 自动更新到最新版
bash deploy/cos-update.sh

# 指定版本
bash deploy/cos-update.sh v0.28.0

# 回滚到指定版本
bash deploy/cos-update.sh v0.27.0
```
