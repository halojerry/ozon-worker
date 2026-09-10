---
title: 子 Agent 执行规范
purpose: 主会话派发调研/执行 subagent 的模型选择、提示词规格、并行纪律与质量闸（repo-gov 治理实践固化）
applies-version: ">=v0.74.0"
last-updated: 2026-09-11
owner: docs-gov
depends: [WORKFLOW, PLAN-repo-gov-v1]
status: active
---

# SUBAGENT-SPEC — 子 Agent 执行规范（模块 7）

> **结论先行**：主会话（GLM-5.3）= 审计/规划/分配/验收；调研与执行 subagent = **一律 glm-5.3-flash**。
> 派发五要素缺一不可：自包含提示词 + 文件白名单 + 禁 git + 结构化输出契约 + 唯一产出路径。
> 主会话对 P0/P1 断言**必须亲核**，不信 subagent 报告原文。本文以 2026-09-11 repo-gov 治理
> （3 波 × 3 并行审计 + 3 批修复，9+ subagent）的真实实践为底稿。

## 1. 模型统一

| 角色 | 模型 | 职责 |
|---|---|---|
| 主会话 | GLM-5.3 | 审计结论交叉验证、任务拆分与分配、接口签名预锁定、P0/P1 断言亲核、验收与统一提交 |
| 调研 subagent | glm-5.3-flash | 只读探查（代码/文档/数据取证），产出报告不产码 |
| 执行 subagent | glm-5.3-flash | 白名单内改码/建码，跑指定测试 |

- **无法 per-call 指定模型时**（Agent 工具无 model 参数，按 harness 默认派发），必须在配置层保证 flash
  （harness/CLI 配置文件里把 subagent 档位固定为 glm-5.3-flash），派发前验证一次实际生效模型。
- 证据：repo-gov 本轮全程 flash 规格，成本与时长可控；主会话只做高价值判断，不替 subagent 写码。

## 2. 提示词规格（每个 prompt 必须五要素齐）

1. **自包含**：新会话零上下文——仓库路径、分支/worktree、领域背景、术语定义全部写进 prompt
   （subagent 看不到主会话历史；AGENTS.md 摘要可直接内联）。
2. **窄域单目标**：一个 prompt 一个目标；「顺便看看 X」一律拆单。
3. **文件白名单**：「只允许改/建这些文件」逐个列绝对路径；白名单外发现的问题**只报告不动手**。
4. **禁 git 操作**：subagent 不做 add/commit/push/分支——git 由主会话统一逐文件提交
   （防 index 竞争与误提交他人 WIP，同 WORKFLOW §2）。
5. **结构化输出契约**：预先声明报告形态——章节清单、行数上限（报告 ≤150 行）、**结论先行**
   （第一段即答案，证据指针在后）、每条断言附文件:行号或 grep 命令。

## 3. 并行派发纪律

- **文件组严格不相交**：N 个并行 subagent 的白名单两两无交集；有共享文件就串行或合并派发。
- **跨组接口先锁定签名**：两组产出需要对接时，主会话先把接口契约**写进双方 prompt**，
  再各自实现（本轮先例：`backup_heartbeat_service.last_backup_at() -> float | None` 与
  `mxou_ledger_service.record_call(*, tenant_id, token_fp, endpoint)` 的签名先定后写；
  时序解耦用 lazy import，不要求对方模块先存在）。
- **产出物唯一文件路径预先指定**：报告/新文件路径由主会话在 prompt 里给死（如
  `docs/audit/<date-topic>/A<N>-*.md`），杜绝两个 agent 写同一文件互相覆盖。

## 4. 质量闸（主会话验收）

- **P0/P1 级断言必须亲核**：主会话用 grep/读码复核后才可写进结论与 BACKLOG。
  本轮四条亲核实锤先例：variant_v2 三段断链、`set_exchange_rate` 汇率死缓存、
  `_rate_limiter.acquire` 返回值被忽略（限流 fail-open）、DraftSubmission 无 tenant_id。
- **「我方无 X」型断言必须附验证方式**：subagent 报告「仓库无 Y 引用」必须给出
  grep 命令与搜索范围（目录/语言过滤），主会话照跑一遍；给不出验证方式的否证断言按未证实处理。
- P2/P3 抽查即可，但修复派发前对「现状」描述仍需亲核（先例：B2-α 每任务「已亲核现状」列）。

## 5. 异常兜底

- **超时/失败重派一次**：同一目标最多重派 1 次；再失败则收窄任务范围拆单或主会话亲自做。
- **并行 WIP 归属靠白名单判定**：工作树检出非自己白名单的未提交文件 = 其他会话/代理的 WIP，
  **正确行为是不触碰、不提交、不 stash**，并在报告里注明（本轮 D1 先例：检出他代理 WIP 正确未触碰）。
- subagent 报告矛盾时不裁决「谁对」——主会话各跑一次双方的验证命令，以可复现结果为准。

## 6. 已知限制（如实记录）

- **Agent 工具无 model 参数**：模型档位由 harness 派发决定；未在配置层固定 flash 时，
  派发后第一件事是验证实际模型，不符则中止改由配置层修。
- **`-e` 安装的 venv 会测错源码**：可编辑安装（`pip install -e .`）的 venv 指向创建时的仓库路径——
  在 worktree 里用它跑测试，import 到的是**原 worktree 的源码**（本轮 pounding-mcp 教训）。
  规则：worktree 内测试须**自建 venv** 或使用与路径无关的调用方式（PYTHONPATH 指向本 worktree、
  importlib 按文件路径加载被测脚本）。
- subagent 无主会话的对话记忆：跨任务复用的结论必须落盘（audit 报告/BACKLOG），不能靠「上次说过」。
