# CI/CD 流程评估 + 多用户并发稳定性 / K8s 决策报告

> 评估时间：2026-08-07｜版本：v0.28.4｜范围：worker + skill + deploy 全链路

## 一、CI/CD 现状总览

当前 CI/CD 由 6 套机制组成，整体已经「够用」但不够「规范」：

| 机制 | 文件 | 触发 | 作用 |
|------|------|------|------|
| 本地 CI | `scripts/ci.sh` | 手动 | 语法→ruff→导入→pytest→docker build，6 步 |
| GitHub Actions CI | `.github/workflows/ci.yml` | push/PR to main,dev | repo-hygiene→syntax→quality→imports→build |
| Worker CI | `.github/workflows/worker-ci.yml` | worker/** 变更 | ruff lint→pytest(+PG service)→docker build |
| CD | `.github/workflows/cd.yml` | tag v* | docker build→push GHCR→create release |
| Skill 构建 | `.github/workflows/build-skill.yml` | tag v* | 4 平台 Cython 编译→COS 分发→release |
| Skill 补发 | `.github/workflows/skill-distribute.yml` | 手动 | 从 release 拉包重传 COS（兜底） |
| Pre-commit | `.pre-commit-config.yaml` | commit | ruff + .env 检查 + JWT 检查 |

**亮点**：repo-hygiene 防止运行时数据入库、build-skill 有 sha256 一致性硬校验、COS 上传有界超时 + 3 次重试、concurrency cancel-in-progress 防重复跑。这些都是生产级实践。

---

## 二、CI 规范性问题（按优先级）

### P1 — quality job 非阻断（必须修）

`ci.yml` 第 66-67 行：
```yaml
- run: pyflakes worker/src/ || true
- run: pyflakes skill/scripts/ || true
```
`|| true` 让 lint 错误被吞掉，CI 永远绿。这违背 CI 的核心目的——**阻断低质量代码合入**。

**改进**：去掉 `|| true`，让 pyflakes/ruff 错误直接 fail job；或统一用 `ruff check`（worker-ci.yml 已用 ruff，ci.yml 却用更弱的 pyflakes，两者标准不一致）。

### P1 — ci.yml 与 worker-ci.yml 职责重叠

两者都跑 syntax/lint/docker build，且触发条件重叠（push to main/dev 都会跑）。结果是同一个 push 触发两套流水线，浪费 runner 资源，且维护两份逻辑。

**改进**：合并为单一 `ci.yml`，用 path filter 区分 worker/skill 子任务矩阵；或明确 ci.yml 只管 skill+repo，worker-ci.yml 只管 worker，互不交叉。

### P2 — 无安全扫描 / 依赖审计

当前没有任何安全扫描：
- 无 `pip-audit` / `safety` 依赖漏洞扫描
- 无 `trivy` Docker 镜像 CVE 扫描
- 无 secret 扫描（pre-commit 只查一个固定 JWT 字符串，覆盖面太窄）

**改进**：CD 构建镜像后加 `trivy image` 扫描；CI 加 `pip-audit -r worker/pyproject.toml`；secret 扫描用 `gitleaks` 全仓扫描。

### P2 — 无测试覆盖率上报

`worker-ci.yml` 跑 pytest 但未加 `--cov` 也无覆盖率上报。无法追踪测试覆盖趋势。

**改进**：`pytest --cov=src --cov-report=xml` + `codecov` action 上报。

### P3 — Dockerfile 单阶段构建，镜像偏大

`worker/Dockerfile` 最终镜像保留了 `build-essential`、`libpq-dev` 等编译工具（约 +200MB），无多阶段优化。

**改进**：多阶段构建——builder 阶段装编译依赖 + pip install，runtime 阶段只复制 wheel + 运行时依赖，`python:3.12-slim` 即可，镜像可减半。

---

## 三、CD / 部署规范性问题

### P1 — 无自动部署，全靠手动 SSH

`cd.yml` 只构建并推送镜像到 GHCR，**不部署到生产服务器**。部署靠人 SSH 上服务器跑 `deploy/update.sh`，这带来：
- 易错（人肉操作，忘拉代码/忘 build cache）
- 无审计（谁何时部署了什么无记录）
- 慢（tag 推送后到实际上线有延迟）

**改进**：CD 末尾加 deploy job——SSH 到服务器 `docker pull` 新镜像 + `docker compose up -d`。用 GitHub Actions 的 `appleboy/ssh-action` + secrets 存 SSH key。先做 staging 服务器，再 prod。

### P1 — update.sh 非滚动更新，中断运行中任务

`deploy/update.sh` 用 `docker compose up -d --force-recreate`，单实例下这会**直接重建容器，所有在跑的 LangGraph 任务被杀掉**（任务状态虽持久化到 PG，但运行中的 async task 会中断，需要重启后被 worker_loop 重新拾取——而僵尸清理 60s 才跑一次）。

**改进**：
- 短期：部署前发「维护中」信号，等运行中任务排空再 recreate（或限时段部署）
- 长期：多副本 + 滚动更新（见下文 k8s 部分）

### P2 — 无 staging / 灰度环境

tag 直接构建生产镜像，无任何预发布验证。镜像 push 后立即 latest tag，旧服务器 `docker pull` 直接拿到。

**改进**：用 `ghcr.io/.../ozon-worker:v0.28.4`（精确版本）而非 `:latest`；staging 服务器先部署精确版本验证，再 promote 到 prod。

### P3 — update.sh --no-cache 全量重建

`docker compose build --no-cache` 每次全量重建，Dockerfile 的依赖层缓存失效，慢且浪费。而 CD 已经 push 了构建好的镜像到 GHCR——服务器应该 `docker pull` 镜像而非本地 rebuild。

**改进**：服务器端改为 `docker compose pull && docker compose up -d`，直接用 CI 构建好的镜像。

---

## 四、多用户并发稳定性分析

### 当前并发架构

| 层 | 机制 | 配置 |
|------|------|------|
| Web 入口 | uvicorn | **workers=1**（单进程，`main.py` start_http_server） |
| 任务调度 | asyncio.Semaphore | MAX_CONCURRENT 默认 30（.env.example 写 50） |
| Worker 池 | start_workers(N) | N 个 asyncio.create_task(worker_loop) |
| 限流 | RATE_LIMIT_PER_MINUTE | 每 token 每分钟（.env.example 300） |
| 持久化 | PG | 任务状态/进度写 PG（有 2s 节流） |
| 僵尸清理 | _periodic_task_cleanup | 每 60s 重置卡死的 running 任务 |

### 多用户并发的三个硬伤

**硬伤 1：uvicorn workers=1，单进程单事件循环**
`main.py` 中 `start_http_server` 硬编码 `workers=1`。asyncio 单线程，无法利用多核 CPU；GIL 下纯 Python 计算密集（如 jieba 分词、LLM 响应解析）会成为瓶颈。这是为 I/O 密集场景设计的，对外部 API 等待多的场景够用，但多用户高并发时会饱和。

**硬伤 2：`_task_progress` 进程内存字典（重启即丢）**
`main.py` 第 39 行 `_task_progress: Dict = {}` 是模块级全局变量。容器重启 → 进度全清空，前端查进度会回退到 PG 的节流快照（2s 延迟）。更严重的是：**多副本部署时各副本进度独立**，请求落到不同副本看到不同进度。

**硬伤 3：`_current_task_id` 全局变量（并发竞态）**
第 40 行 `_current_task_id` 注释自称「thread-local 当前处理中的 task_id」，但实际是**模块级全局变量**（`global _current_task_id`），不是 `threading.local()` 也不是 `contextvars.ContextVar`。多个并发任务同时 `set_current_task_id` 会互相覆盖——这是**真实的数据正确性 bug**，不只是性能问题。

**其他隐患**：
- `running_tasks: Dict` 也是进程内，多副本无法互相取消任务
- 无分布式锁，多副本 worker_loop 可能同时拾取同一个 pending 任务（PG `SELECT ... FOR UPDATE SKIP LOCKED` 可解决，需确认 task_processor 是否已用）

---

## 五、是否需要 K8s？—— 明确结论

### 结论：当前阶段**不需要** K8s，但需要先做「状态外置」为未来上 K8s 铺路

### 决策依据

**不需要 K8s 的理由（当前规模匹配）**

1. **部署形态**：单团队、单台服务器（DEPLOY.md 推荐 4C4G）、批量上架 Ozon 商品，不是面向 C 端的高并发服务
2. **并发量级**：MAX_CONCURRENT=30，单个 LangGraph 任务跑 3-10 分钟，实际并发瓶颈是**外部 API（Ozon/1688/LLM/生图）的限流**而非 Worker 本身——上 K8s 也突破不了外部配额
3. **运维成本**：K8s 引入 etcd/ingress/storage class/Helm 等组件，4C4G 单机跑 K8s 控制面会吃掉一半资源，ROI 极低
4. **当前痛点**：用户反馈的是「Chrome 无限重启」「生图额度」等业务问题，不是「Worker 扛不住并发」——K8s 解决不了这些
5. **任务特性**：长任务（3-10 分钟）+ 外部 API 依赖，I/O 密集，asyncio 单进程 + Semaphore 已是合理模型

**需要 K8s 的触发信号（达到任一即应评估上 K8s）**

| 信号 | 含义 |
|------|------|
| 并发用户数 > 50 且单实例 CPU 饱和 | 需要水平扩展 |
| 要求「更新不中断服务」 | 需要滚动更新 + 多副本 |
| 多租户强隔离需求 | 需要命名空间/资源配额 |
| 单台服务器扛不住，已加第二台 | 需要跨节点调度 |
| 要求 99.9%+ 可用性 | 需要自愈 + 自动调度 |

### 如果未来要上 K8s，**必须先完成的「状态外置」前提**

这是最重要的一点——**直接把当前单实例镜像复制成 K8s 多副本 Deployment，会立刻触发数据正确性 bug**。前提工作：

1. **`_task_progress` → Redis / PG**
   进度实时写 Redis（前端查 Redis），异步落 PG。所有副本共享同一进度源。

2. **`_current_task_id` → `contextvars.ContextVar`**
   改成真正的协程上下文变量，每个任务独立，消除并发竞态。这是**当前就该修的 bug**，不取决于是否上 K8s。

3. **`running_tasks` → 分布式任务注册表**
   用 PG 表 + `SELECT FOR UPDATE SKIP LOCKED` 让多副本 worker_loop 安全抢任务；或引入 Redis + 分布式锁。

4. **uvicorn workers 可调**
   支持 `WEB_CONCURRENCY` env（gunicorn 风格），多核时可多 worker 进程。

5. **会话粘性 / 无状态化**
   SSE 流式响应（如果有）需要会话粘性或改用「提交即返回 task_id，前端轮询」模式。

### 推荐演进路径（不上 K8s 也能扛住）

```
当前: Docker Compose 单实例 + PG  (扛 30 并发)
  ↓ 修 _current_task_id bug + 状态外置
阶段1: Docker Compose 单实例 + Redis (进度共享，可扩 worker)
  ↓ uvicorn workers 可调 + gunicorn
阶段2: Docker Compose 2 副本 + Redis + PG (滚动更新，扛 60+)
  ↓ 真正需要跨节点/多租户隔离时
阶段3: K8s (Deployment + HPA + PG operator + Redis operator)
```

**阶段 1-2 用 Docker Compose `scale: N` + 共享 PG/Redis 即可实现多副本 + 滚动更新**，不需要 K8s。只有阶段 3（跨多节点、需要自动扩缩容、多环境隔离）才值得引入 K8s 的复杂度。

---

## 六、立即可执行的改进清单（按 ROI 排序）

| 优先级 | 改进 | 工作量 | 收益 |
|--------|------|--------|------|
| **P0** | 修 `_current_task_id` → contextvars（并发竞态 bug） | 0.5h | 修真实 bug |
| **P1** | ci.yml quality 去掉 `|| true`，统一用 ruff | 0.5h | CI 真正阻断低质量 |
| **P1** | 合并 ci.yml + worker-ci.yml，去重 | 2h | 降维护成本 |
| **P1** | CD 加自动部署 job（SSH pull + up） | 2h | 免人肉部署 |
| **P1** | update.sh 改用 `docker compose pull`（用 CI 镜像） | 0.5h | 部署快 + 一致 |
| **P2** | 加 trivy 镜像扫描 + pip-audit 依赖扫描 | 1h | 安全 |
| **P2** | Dockerfile 多阶段构建 | 1h | 镜像减半 |
| **P2** | `_task_progress` 写 Redis（为多副本铺路） | 3h | 进度不丢 + 可扩 |
| **P3** | uvicorn workers 支持环境变量 | 0.5h | 多核利用 |
| **P3** | 加 pytest --cov + codecov 上报 | 1h | 覆盖率可见 |

---

## 七、一句话总结

CI/CD 基础设施已经搭得不错（有 hygiene、sha256 硬校验、concurrency 控制），但 **CI 的 quality 环节形同虚设、部署全靠人肉 SSH**，规范性有提升空间。多用户并发方面，**当前不需要 K8s**——单实例 + asyncio 适配现有规模，但有一个 `_current_task_id` 全局变量竞态 bug 必须立即修，且状态外置是未来扩展（无论 Compose 多副本还是 K8s）的共同前提。建议先修 bug + 补 CI 阻断 + 加自动部署，而非急着上 K8s。
