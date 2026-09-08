# 执行 PRD: pounding-ozon-probe Skill 架构落地(合并版)

> 版本: v1.0 | 日期: 2026-08-06 | 状态: **已执行完成 (2026-08-06, commit ec780d4)**
> 来源: 基于 Codex `SKILL-ARCHITECTURE-PRD.md` review 后合并 —— 已做项作为基线,增量项本次执行,过时/冲突点已修正。

---

## 1. 基线(已完成,不重做)

以下内容在 `SKILL-OPTIMIZATION-PLAN` 执行轮(commit d55b29c/6cded2c/98f4851)已落地:

| 项 | 状态 | 证据 |
|---|---|---|
| P5 命令覆盖 | ✅ | §4 速查表 15 行(14 命令+batch_test)+ 参数补全 + search/probe/list_stores/get_ak 入册 |
| P6 内部矛盾 | ✅ | §5.1 删「流程完成后我会通知你」;§5.3 承认 batch_test --wait 轮询 |
| P7 VERSION 死循环 | ✅ | 3744ca8: CI 用 tag 写包内 VERSION + 硬校验(比 Codex 方案更完整,含 fail 门禁) |
| 意图路由 | ✅(增强版) | §3 优先级决策表: URL 类型先判/蓝海默认C/指代追问/B降级语义(R1-R6 竞态全解决) |
| frontmatter version | ✅ | `version: "0.27.1"`(与 skill/VERSION 一致) |
| §4.1 输出字段解析 | ✅ | submit_result/product_summary 字段表 + 汇报模板 |

**基线验收**:skill 全量测试 81 passed;3 commit 推送 dev;deploy/skill 同步一致。

## 2. 本次执行(增量)

### Phase A — Frontmatter 合规(小)

- [x] `agent_created: true` 加入 frontmatter(WorkBuddy SkillManage 可管理)
- [x] description 改第三人称客观描述(当前仍条件句):
  ```yaml
  description: >
    Ozon 跨境电商上架工具。此技能在以下场景触发：用户发送 1688 商品链接时
    直接上架到 Ozon；用户发送 Ozon 商品链接时跟卖；用户发送图片时以图搜款
    找 1688 同款；用户说"选品""找蓝海""找趋势商品"时自动搜索 Ozon 中国站
    并匹配 1688 货源；用户发送多个链接时批量处理。覆盖选品、跟卖、上架、
    以图搜款、趋势选品全流程。
  ```
- [x] version 保持 `"0.27.1"`(Codex PRD 写 0.27.0 已过时)

**验收**:frontmatter 含 name + description(第三人称)+ version + agent_created 4 项。

### Phase B — references/ 渐进式披露(核心,大)

**目标**:SKILL.md 455 行 → ≤160 行骨架;详细内容拆 4 个 references 文件。

**B1 拆出文件**(从当前 SKILL.md 提取,保留已有优化内容):

| 新文件 | 内容来源(当前章节) | 内容 |
|---|---|---|
| `references/command-reference.md` | §4 管线 A-E + 批量处理 + 其他命令 | 14 命令+batch_test 完整参数表 + 示例 + 输入输出 + 管线触发条件 |
| `references/error-codes.md` | §5 Worker 响应处理(错误码表) | Worker 错误码表 + 回复模板 + 进度查询口径(保留 §5.3 已修正版) |
| `references/output-schema.md` | §4.1 输出字段解析 | submit_result/product_summary 字段解析 + 成败判定 + 汇报模板 |
| `references/env-setup.md` | §2 环境准备 + §2.3 故障表 + §11 data/ 语义 | 安装/凭证/check 故障排查表/data 目录语义 |

**B2 SKILL.md 骨架**(保留全量,≤160 行):
- frontmatter(Phase A 后)
- §0 定位 Skill 目录(Phase D)
- §1 意图路由决策表(**全量保留**,核心)
- §2 命令速查表(一张表,自包含核心参数——防 agent 不加载 references 时也能操作)
- §3 决策边界(精简)
- §4 越界行为(精简)
- §5 参考文件索引(指向 references/ 4 文件)
- §6 更新机制(精简)

**B3 compile.py**:
- [x] `DOC_FILES` 加 4 个 references/ 路径(纯列表追加,不改编译逻辑)

**B4 打包验证**:
- [x] `python3.12 compile.py` 后 dist 含 references/ 4 文件(CI 会做,本地 darwin-arm64 验证)

**风险缓解**:速查表自包含核心参数;SKILL.md 明确「完整参数见 references/command-reference.md」;拆后跑全量测试 + import 验证。

### Phase C — 祈使句全文改写(中)

- [x] SKILL.md + references/ 全文第二人称 → 祈使句/客观描述
- [x] 保留规则(Codex PRD §4.2):
  - 「告诉用户」= 对 agent 的指令,保留
  - 引号内用户原文(如"帮我选品")保留
  - 「Role: operator. Execute CLI commands to complete tasks.」式开场
- [x] 当前残留少(仅 1 处「你」模式),重点检查 references/ 拆出后的表述

**验收**:grep 无「你只需/你的角色/你用以下」(引号内用户原文除外)。

### Phase D — 跨 Agent 兼容(小)

- [x] §0 新增「定位 Skill 目录」说明(SKILL_DIR / 当前目录 / 上级查找)
- [x] `python3.12` → `python3`(38 处,SKILL.md + references/),注明 Python ≥ 3.12
- [x] 不修改 cli.py(shebang 已是 `#!/usr/bin/env python3`)

**验收**:SKILL.md 无 `python3.12` 硬编码(除版本要求说明)。

### Phase E — 意图路由补「批量处理」分支(小)

- [x] 决策表加分支:`多个 URL / "批量处理这些" → 管线 F batch_test`(当前只有「多 URL 追问」,补显式批量触发)
- [x] 保留现有增强(URL 类型/蓝海/指代/降级),**不退回 Codex 的 7 分支**

### Phase F — 同步与回归(收尾)

- [x] deploy/skill 同步(rsync,排除 .1688-AK/data/tests/dist)
- [x] skill 全量测试(.venv314 pytest tests/)
- [x] compile.py DOC_FILES 完整性核对(4 references 文件在列表)
- [x] git 提交拆分:docs(skill) frontmatter+references / refactor(skill) 祈使句 / docs(skill) python3+管线F
- [x] push dev

## 3. 修正点(相对 Codex PRD)

1. version = 0.27.1(非 0.27.0)
2. 现状行数 455(非 345)
3. P7 已实现,PRD 该节删除(不留「CI 方案文档」占位)
4. 意图路由保留我们决策表增强,不退回 7 分支
5. 「不改任何 .py」注明 compile.py DOC_FILES 为例外(打包清单,非业务逻辑)
6. 验收「无'你'」边界:引号内用户原文 + 「告诉用户」指令保留

## 4. 不做

- Phase 2 子 Skill 拆分、Phase 3 Expert 包(Codex 自标可选,后续评估)
- audit_products.py 打包(开发者工具定位不变)
- Worker 端任何改动;信封契约不变

## 5. 验收总清单

- [x] frontmatter 4 项合规(name/description/version/agent_created)
- [x] SKILL.md ≤ 160 行(骨架);references/ 含 4 文件
- [x] dist 打包含 references/(compile.py DOC_FILES 核对)
- [x] 无 python3.12 硬编码(除版本要求);§0 目录定位说明在
- [x] 意图路由含批量分支 + 全部现有增强
- [x] skill 全量测试通过(81+);deploy/skill 一致;无凭证泄漏
