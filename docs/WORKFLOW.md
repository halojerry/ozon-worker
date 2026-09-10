# WORKFLOW — 仓库协作规范 v1

> 生效 2026-09-09。规范多 AI 会话并行开发下的分支拓扑、会话隔离、合并门槛与发版流。
> AGENTS.md「纪律」节是本规范的摘要；冲突时以本文为准。
> 背景与动因见 `docs/GIT-STREAM-INDEX.md`（两天 142 提交 12 流交错的取证 + 撞车实录）。

## 1. 分支拓扑（三角色）

| 分支 | 角色 | 保护方式 |
|---|---|---|
| `main` | **发布线**。只接受 dev→main 的 PR 合入（merge commit），**tag 一律打在 main 上** | 建议开 GitHub branch protection（require PR；需 admin，一次性配置） |
| `dev` | **集成线**。一切工作的唯一汇合点 | 软保护：Tier B 允许直提，但须过本地 CI + 当日 push |
| `<type>/<topic>` | **工作流分支**：`feat/` `fix/` `docs/` `chore/` `refactor/` `hotfix/`。从最新 `origin/dev` 切出，PR 合回 dev 后即删 | 短生命周期，不留僵尸 |

规则：
- 历史 tag（≤v0.72.0）留在 dev 历史上不动；**今后新 tag 只打 main**。cd.yml 由 tag `v*` 推送触发、不分分支（已核实），发布链路无缝。
- 仓库不保留已合并分支：PR 合入即删（本地 `git branch -d` + 远端 `git push origin --delete`）。
- 2026-09-09 已一次性重建：main 从 dev FF 到 `3d2836b0`，清掉 19 个僵尸分支（全部实测 0 提交领先 dev，零数据损失）。

## 2. 多会话隔离：一会话一分支一 worktree

多个 AI 会话并行的物理隔离协议。**目的**：互不可见对方未提交文件，从根上消灭 mtime 撞车与重复实现（2026-09-09 实录：策略模块被两会话重复实现）。

### 开工仪式（每个非平凡任务的会话必须执行）

```bash
git fetch origin                              # 同步远端，别在过期基线上开工
git branch -a --sort=-committerdate           # 看已有流分支——防止和别人做同一件事
git worktree add ../ozon-worker-<topic> -b <type>/<topic> origin/dev
cd ../ozon-worker-<topic>                     # 之后所有工作、测试、提交都在这里
```

### 收工仪式

```bash
git push -u origin <type>/<topic>             # 没写完也推——防「N 个提交滞留本机」重演
gh pr create --base dev                       # draft PR 也行，占住「这条流存在」的事实
```

合并后清理：`git worktree remove ../ozon-worker-<topic>` + `git branch -d <type>/<topic>`。

### 主 worktree（`/Volumes/os/dev/ozon-worker`）只做三件事

1. Tier B 小改动直提 dev；
2. 发版操作（dev→main PR + tag）；
3. review 别人的 PR。

**禁止**在主 worktree 做多文件特性开发。开工前必看 `git status` + 目标文件 mtime——工作树里常有其他会话的 WIP，逐文件 `git add`，不用 `-a`/stash。

## 3. 两级合并门槛

| 级别 | 覆盖范围 | 流程 |
|---|---|---|
| **Tier A**（必须分支+PR） | 跨子系统改动（skill+worker 同时动）；新增/修改 API 端点；新表或迁移；发版；>3 文件或 ~200 行以上；有行为变更的 config/prompt | 分支 + PR → **CI 绿才可合** → self-merge 合法（solo 现实）→ merge commit 保留流边界（不 squash 不 rebase）→ 合后删分支 |
| **Tier B**（允许直提 dev） | ≤3 文件的 docs/注释/单点 fix；CHANGELOG/AGENTS 更新 | 两条铁律：①提交前本地 `bash scripts/ci.sh --quick`（至少 ruff + 相关单测）；②**当日 push，绝不过夜** |

PR 纪律：标题即工作流名（`<type>(<scope>): 中文描述`），正文贴 CHANGELOG 候选段。

## 4. 发版流

**常规发版**（VERSION 四源 + CHANGELOG + 实机 gate 纪律不变，tag 落点从 dev 换到 main）：

```
dev 上完成实机 gate → 开 dev→main PR（标题 release: vX.Y.Z）→ CI 绿合入
→ 在 main 上打 tag vX.Y.Z → push tag → cd.yml 自动部署
```

**hotfix**：从 main 切 `hotfix/<topic>` → PR 回 main → 打补丁 tag → 同一提交合回 dev（或 cherry-pick）。

**发版前适配同步检查**照旧走 AGENTS.md「发版前适配同步检查清单」。

## 5. 新会话开工提示词模板

给任何新 AI 会话粘贴这段，10 秒进入协议：

```
本仓库执行 docs/WORKFLOW.md 协作规范。你本次的任务是 <一句话任务>。
开工前依次执行：
1. git fetch origin && git branch -a --sort=-committerdate（确认没有别的会话在做同一件事）
2. 评估改动级别：跨子系统/新API/新表/发版/>3文件 → Tier A，走 worktree 分支：
   git worktree add ../ozon-worker-<topic> -b <type>/<topic> origin/dev
   ≤3文件的 docs/单点fix → Tier B，主 worktree 直提 dev，但当日必须 push
3. Tier A 收工：push 分支 + gh pr create --base dev；CI 绿后 merge commit 合入、删分支删 worktree
禁止：在主 worktree 开发多文件特性；git add -a / stash；提交别人会话的 WIP 文件；
     重写已推送历史；把 tag 打在 dev 上。
```

## 6. 明确不做的事（YAGNI）

- 不上 GitFlow release/* 分支（两天三版的节奏用不上）；
- 不强求每会话独立 git 身份（分支名即流标识）;
- 不做 PR 模板 / CI 分支命名强制（需要时后补）;
- 不用 rebase 整理已推送的共享分支——重写历史只允许发生在自己独占的未推送分支上；
- pounding-harness 是独立仓库，本规范不覆盖（未来可参考 adoption）。

## 7. 变更记录

- **v1（2026-09-09）**：首版。main 重建为发布线（FF 至 3d2836b0）+ 一会话一分支一 worktree + 两级合并门槛 + 清理 19 僵尸分支 + 历史索引 `docs/GIT-STREAM-INDEX.md`。触发事件：两天 142 提交 12 流交错直提 dev、78 提交滞留本机、共享工作树撞车实录、dev CI 红三轮。
