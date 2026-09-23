# PLAN-agent-ergonomics-v1 — Agent 调用效率优化（实测驱动）

> 分支 `feat/agent-ergonomics-v1`（2026-09-24，基线 fc639c76）。跨仓：ozon-worker（本仓 skill/pounding-mcp）+ pounding-harness（环境注入）。
> 动因：用户实测反馈 agent 调用 skill「要很久、绕圈圈、一个动作多次工具调用做不好事」。

## 一、实测证据（2026-09-24 取证，方法与数据留痕）

数据源：pounding-harness `runtime/home/sessions`（54 会话解压分析）+ `runtime/Trash`（含已删）+ skill `data/logs/run_*.log`（真实 graph/follow/check 运行）。

| # | 证据 | 数据 |
|---|------|------|
| E1 | **harness 环境承诺未兑现**：专家模板（docs/ozon-experts.seed.json）声称「运行时已设 $SKILL_DIR」，grep 全仓源码无任何代码设它；实测会话 agent 首个调用探测 `SKILL_DIR=`（空），再 `ls /opt/homebrew/bin` 找 python3.12（PATH 缺），再自发明 `export PATH=... && cd <硬编码路径>` 补丁 | 每会话烧 2-3 个调用纯修环境；该会话跑完 check(22s) 即 turn/end，业务命令 0 条 |
| E2 | **确认口径四处分裂**：harness 模板「graph 默认 --no-submit 先展示」/ SKILL.md §0.5「用户没确认过先问」/ §3「graph/follow 自动执行」/ recipe ①「--wait 一条到终态」 | 同一动作四个说法，agent 每次现场仲裁 |
| E3 | **命令内部 30s 级串行 CDP**：真实 follow 日志 Ozon 抓取 9.5s + 图搜 3.2s + 信封组装 14.3s + 提交 1.9s ≈ 29s；check 22s | 无 NEXT 信号与轮询节奏，等待期 agent 只能瞎轮询 |

样本口径：本机 54 会话仅 10 个有工具调用（合计 56 次、业务 2 次）——重度使用在用户真机；但 E1-E3 是机制性缺陷，非样本偏差。

业界依据（subagent 调研，2026-09-23）：Anthropic《Writing effective tools for agents》（输出结尾 steer next action、错误带恢复命令）、《Agent Skills》规范（description ≤1024 字符第三人称、正文 <5000 tokens 只说做什么、渐进披露）；anthropics/skills claude-api 范例（分发表 Action 列写执行指令 + Quick Task Reference 话术→动作映射表）；obra/superpowers（Red Flags 借口对照表）；gh `run watch --exit-status`（阻塞式终态收敛）；微软 DevBlogs（保留参数式调用，勿改 JSON 信封）。

## 二、统一口径（B，拍板稿）

**提交确认二分法**（CLI 行为零变更，纯文档对齐）：

1. **明确上架意图**（用户发 1688/Ozon 链接 + 上架/跟卖/整一批等动词，或会话中已确认过提交）→ `graph`/`follow --auto-submit` 直接提交，建议带 `--wait` 到终态。不追问。
2. **弱意图**（看看/能不能上/多少钱）→ `graph --no-submit` / `follow`（不带 `--auto-submit`）展示信封+预估，等用户说提交。
3. **选品类双出口纪律不变**（SKILL.md §1⑯）：用户没说 `--to-box` 还是直上，先问。
4. CLI 缺省行为不动：graph 缺省提交；follow 缺省展示（须 `--auto-submit`）。

## 三、任务分解

### Task A（pounding-harness 仓）：兑现环境承诺
- **A1** `scripts/dev-up.sh` dsh 启动块（:241 附近）：`export SKILL_DIR="$KNOWLEDGE_HOME/skills/pounding-ozon-probe"` + PATH 注入 /opt/homebrew/bin 等（:108-109 的目录循环当前只用于找 node，未导出给 dsh 子进程——补 export）。
- **A2** `src-tauri/src/main.rs` dsh spawn（:486-498）：`.env("SKILL_DIR", knowledge_home/skills/pounding-ozon-probe)`；核对 `path` 构造含 homebrew。`cargo check` 过闸。
- **A3** 遗留 Swift/macOS 壳（AGENTS 标记 W3 待删）不投入——计划内豁免，记 defer。
- **A4** seed 修正见 B3。
- 验收：新起 dsh 会话，agent bash `echo $SKILL_DIR` 非空；`command -v python3.12` 直接命中。

### Task B：口径统一（三处文档 + router）
- **B1** SKILL.md §0.5 recipe ①② 括号说明改为二分法表述；§3 表格保持权威并引用同一口径。
- **B2** pounding-mcp `router.py`：pipeline A/B（URL+明确意图）`needs_confirmation=False`（对齐二分法第 1 条）；弱意图/选品类不变。跑 pounding-mcp 测试。
- **B3** harness `docs/ozon-experts.seed.json` 三个专家 instructions 的管线段按二分法重写（删除「默认 --no-submit 先展示」的错误声明）。

### Task C：输出侧 NEXT 行 + 轮询节奏 + 错误恢复（cli.py + pounding-mcp）
- **C1** `skill/scripts/cli.py` 新增 `_print_next(action)` helper（`👉 NEXT: …` 固定前缀，stdout flush）。接线出口：
  - `_wait_for_terminal` 终态：completed → `NEXT: 直接汇报 product_id，无需后续命令`；failed → `NEXT: 查 references/error-codes.md 对应码；连续 2 次失败勿重试，用 report 上报`。
  - 展示态出口（graph --no-submit / follow 无 --auto-submit）→ `NEXT: 向用户展示信封+预估；确认后带提交参数重跑（graph 去 --no-submit / follow 加 --auto-submit）`。
  - 入箱出口（cli.py :892/:1520/:2145/:3233）→ `NEXT: draft_id 已出，本次结束；上架由 WebUI 认领`。
  - 锁占用 exit 4（:655/:661）→ `NEXT: 加 --wait 排队；--force 仅用户明确要求时用`。
  - 提交成功无 --wait → `NEXT: query <task_id> --watch 等终态（分钟级，勿秒轮询）`。
  - check 失败 / 凭证缺失 → `NEXT: 补配置命令（set_store/set_token/set_ak）后重跑 check`。
- **C2** pounding-mcp `server.py job_status`：返回体加 `next_poll_s: 20` 与 `hint`（running 态：「分钟级任务建议 15-30s 间隔轮询；完成即停」）。加性字段，向后兼容。
- **C3** 测试：新 `skill/tests/test_next_hints_v079.py`（helper + 关键出口 capsys 断言）；pounding-mcp job_status 加性字段测试。

### Task D：SKILL.md 重构 + references 拆分
- **D1** frontmatter description 重写：第三人称、触发词前置、≤1024 字符、无流程摘要（obra 陷阱）。
- **D2** §1 重构：Quick Task Reference 表（用户话术 → 完整命令模板）+ 规则压缩；细则迁 `references/routing.md`。
- **D3** §2 命令表瘦身（命令/用途/自由度档），flag 细节按域拆：
  `references/commands-listing.md`（graph/follow/batch_test/image_search/search）、`commands-discovery.md`（discover 族/queries/seller/category）、`commands-ops.md`（check/query/凭证/cookie/清理/更新）。
- **D4** 新增 Red Flags 表（借口→现实，≥6 行）入 SKILL.md。
- **D5** `skill/compile.py` DOC_FILES 同步（删 command-reference.md，加 4 新文件）；查存量测试对 command-reference.md 的引用。
- 版本不动（发版时统一 bump 四源）。

## 四、执行顺序与门槛

1. Task C（代码+测试）→ 2. Task B1+D（文档重构与口径合并施工）→ 3. pounding-mcp B2+C2 → 4. harness A+B3（分支 feat/agent-env-caliber）→ 5. 测试全绿 → 6. 双仓 PR。
- 测试：skill 全量（.venv314）、pounding-mcp 自身 venv、worker 不动（本批零 worker 改动）、harness `cargo check` + `smoke_test.py --skip-live`。
- 回滚：纯文档/输出行/加性字段，逐 commit revert 即可；harness env 注入 revert 同理。

## 五、验收（对照 E1-E3）

- E1 消除：新会话 `echo $SKILL_DIR` 非空且 agent 零探测调用（人工跑一次专家派发确认）。
- E2 消除：grep 四处口径唯一（「明确意图直提/弱意图展示」二分法表述一致）。
- E3 缓解：每条命令出口有 NEXT 行；job_status 带 poll 节奏。真实 follow 会话工具调用数对比（复测一次专家派发全流程）。

## 六、defer

- A3 Swift 遗留壳环境注入（W3 删壳后自然消亡）。
- exit code 全表文档化收口（D3 顺带，不单独立项）。
- description 截断实测（skillListingMaxDescChars 场景）——下个发版观察项。
