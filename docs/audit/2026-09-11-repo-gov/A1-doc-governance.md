# A1 — 文档资产治理审计报告（docs/）

> 日期 2026-09-11 · 基线 v0.74.0 · 分支 docs/repo-gov-v1（worktree /Volumes/os/dev/ozon-worker-gov）· 调研代理 glm-5.3-flash
> 范围：docs/ 全部资产（顶层 md / 数据文件 / 子目录 / archive 引用面 / PLAN 状态 / 双真相源）。**只审计不改动**——本报告是唯一产出文件。

## 0. 结论摘要

- docs/ 实际 tracked 资产 **96 文件**（58 md + 38 数据/参考），顶层 md **39 个**（主 worktree 38 + 本分支新增 PLAN-repo-gov-v1）。摸底数字（604/44/6 子目录）混淆了 **git 资产与本地 gitignore 目录**（参考项目 155M、ozon-probe 均未入库）。
- 真正需要动的很少：**建议归档仅 1 个**（IMAGE-PROMPT-GUIDE.md），**必须修的事实错误 5 处**（两份拓扑文档停 v0.70、API-OVERVIEW §11 两条失真、AGENTS 4 处指向 legacy、2 个 PLAN 自相矛盾状态行），**结构性缺口 1 个**（docs/ 无索引）。
- 治理主线：**最小移动 + 虚拟索引**。不搞大规模 git mv；建 `docs/README.md` 做「能力域→场景→接口/数据→最佳实践」分层索引，配套轻量 frontmatter 与 PLAN 状态行规范，用 CI 探针防三类漂移（断链/版本戳/legacy 指路）。

---

## 1. 事实基线（8 条抽验，含修正）

| # | 摸底事实 | 抽验结论 | 验证方式与结果 |
|---|---|---|---|
| 1 | docs/ 604 文件、44 顶层 md、6 子目录 | **部分不符**。主 worktree 实测 **606 文件**（含本地目录）；**git 跟踪资产 96**；顶层 md **38**（gov 分支 39，多 PLAN-repo-gov-v1.md）；跟踪子目录 **4**（audit/competitor/ozonharness/refs） | `find docs -type f \| wc -l`=606（主）；`git ls-files docs \| wc -l`=96（gov）；`ls docs/*.md \| wc -l`=38/39 |
| 2 | 参考项目/ 155MB 未跟踪已 gitignore | **属实**（155M，仅主 worktree 本地存在，.gitignore:137）。**补充**：ozon-probe/（.gitignore:134）与 .bok/ 同为本地未跟踪目录——「6 子目录」口径应改为「跟踪 4 + 本地 3」 | `du -sh`=155M；`git check-ignore -v` 两条命中；gov fresh checkout 中两目录不存在 |
| 3 | 无 docs 级索引；事实索引在 AGENTS「深入阅读」+ API-OVERVIEW §11；无统一 frontmatter；版本戳混乱 | **属实并强化**。无 docs/README.md；索引散落四处（AGENTS「先读什么」:40 + 目录树 ：681-700 + 根 README.md:261-265 仅 5 条 + API-OVERVIEW §11:247）；**39/39 顶层 md 零 frontmatter**；版本戳从 v0.27/v0.32/v1.0/日期到零元数据（DEPLOY.md 头部无任何元数据）均实测 | `ls docs/README.md` 不存在；39 文件 `head -1` 无 `---`；逐文件头部版本戳扫描表见 §2 |
| 4 | 疑似过期：IMAGE-PROMPT-GUIDE（0 引用）/ LOGGING（仍被引用）/ DESIGN-PHASE2（0 引用）/ API-INTEGRATION-GUIDE（9 行 stub） | **三属实一修正**。IMAGE-PROMPT-GUIDE 活引用 **0**（仅 CHANGELOG 历史提及）✓；LOGGING 被 README:264 + AGENTS×4 引用 ✓；API-INTEGRATION-GUIDE 恰 9 行墓碑 ✓ 但**仍被 6 处引用**且 API-OVERVIEW §11 对它的描述（「快照式」）已失真；**DESIGN-PHASE2 非 0 引用**——被 PRD-store-sync-erp-v1.md 引用，且自我定位是「阶段二设计预留、新开里程碑时以此为准」→ 不是过期，是活设计预留 | 全仓 grep 文件名（排除自引用/archive）；`wc -l`=9；读 DESIGN-PHASE2 头部 |
| 5 | legacy 76 文件；AGENTS ≥4 处引用 legacy 内文件 | **属实**。archive/docs/legacy/ 实测 76 文件；AGENTS **4 处实质指路**：`:322` PLAN-wave-p2p3-fixes-v1、`:378`/`:1159` TEST-v067-wave-plan、`:573` TEST-ISSUES-2026-08（另有 ：210/:688/:694/:1207 为「已归档」事实陈述，合法）。摸底行号 298/354/549/1134 已漂移 | `grep -n "archive/docs/legacy" AGENTS.md` |
| 6 | PLAN-* 11 个，7 个无状态行；card-merge-fix 自相矛盾 | **修正**：实际 **14 个 PLAN**（基线 13 + 本分支 repo-gov）；**10 个无状态行**（user-feedback-fixes 头部命中的 "status" 是 `task_status` 误匹配）；有状态行 4 个中 **2 个自相矛盾**——card-merge-fix **和** conversation-entry 均标「方案文档，只写不实现」，但两者均已随 v0.60.0 发版（AGENTS v0.60 块明确「执行记录见」两者） | 逐文件 `head -6` grep 状态词；对照 AGENTS v0.60 块与 CHANGELOG |
| 7 | 双真相源：WORKER-TOPOLOGY 正文 v0.27 + 头部增量；CONTRACT-v4 头部 07-30 vs mtime 09-09；API-OVERVIEW v0.70 vs VERSION | **属实并加强**。①WORKER-TOPOLOGY 头部「对应版本 v0.70.0」+ 显式声明「正文按 v0.27 口径撰写」+ v0.28–v0.70 增量摘要节——**模式已存在但停在 v0.70，落后现行 4 个 minor**；②CONTRACT-v4 头部「日期 2026-07-30」vs `git log` 最后内容变更 **2026-09-10**（fresh worktree mtime 全为 checkout 时间，日期对比必须用 git log，摸底的 mtime 口径不可复现）；③API-OVERVIEW 头部「对应 v0.70.0」vs VERSION 0.74.0，且 §11 文档地图两条事实失真（MCP-SERVER「17 工具」实为 29；API-INTEGRATION-GUIDE 描述为墓碑前旧态） | 读三文件头部 + `git log -1 -- docs/CONTRACT-v4.md` |
| 8 | 数据文件带下载残留名（被两脚本硬编码）；refs/ozon-mcp 8 个 .pyc 入库 | **一属一修**。残留名属实：`China_scoring_..._(1).xlsx`（151K）被 `worker/scripts/import_logistics.py:31` 以 **docs/ 为基目录**硬编码，`init_data.py:265` 同名但读 **worker/assets/** 副本（双副本双路径，改名需两处同步）；`ozon-api-docs-2026-07-05 (3).json`（1.2M）无代码消费者，仅 OZON-ATTRIBUTE-API.md:3 引用为来源。**pyc 修正：实测 18 个（非 8）且全部未入库**（`git ls-files` 计 0，gov checkout 无）——是本地卫生问题，不是 git 污染 | `grep -n` 两脚本路径拼接；`git ls-files docs/refs \| grep -c pyc`=0 |

**抽验带来的新事实**（摸底未覆盖）：v0.74.0 已于 2026-09-10 发版（PR #10 合 main 打 tag，tag 实测存在），0.73.0 未单独 tag 同车发出，CHANGELOG 已有 [0.74.0] 节——PLAN 状态判定（§6）以此为准；cross-platform-sourcing 批 3 提交在 v0.74.0 **之后**（`git merge-base --is-ancestor` 验证），属「已合 dev 未发版」。

---

## 2. 归档清单（39 顶层 md 逐文件判定 + 数据文件 + 子目录）

**结论：仅 1 个建议归档、1 个待定、1 个建议合并；其余保留。** 低引用 ≠ 过期——多数低引用文件是权威参考（OZON-ATTRIBUTE-API 仅 AGENTS 引用但它是「开发直接查这里」的活参考）。

### 2.1 顶层 md

| 文件 | 判定 | 理由 | 关联引用 |
|---|---|---|---|
| IMAGE-PROMPT-GUIDE.md | **归档** | v0.32 口径，头部自认 v0.64 生图链已换代（视觉模型+精简 10→5），「占位符以 config 当前内容为准」= 内容已让位 config；全仓活引用 0 | 仅 CHANGELOG 历史提及 |
| API-INTEGRATION-GUIDE.md | 保留（墓碑） | 9 行重定向 stub 是正确形态；但 6 处引用方与 API-OVERVIEW §11 对它的描述需同步改口（§8 P0-4） | api-integration/README、API-OVERVIEW §11、ozonharness/ARCHITECTURE、webui×2 |
| DESIGN-PHASE2-PURCHASE-ADS-RECONCILIATION.md | 保留 | 「阶段二预留设计」自我定位明确，被源 PRD 引用；非过期，是未到期的设计资产 | PRD-store-sync-erp-v1.md |
| LOGGING.md | **更新后保留** | 仍是被 AGENTS/README 指定的活文档，但头部「v1.0 / 2026-07-18」口径过旧（当前 13 阶段/进度 PG 回退未反映则需核） | README:264、AGENTS×4 |
| WORKER-TOPOLOGY.md | **更新后保留** | 被 AGENTS 指定为「改前必读」却停 v0.70；治理方案见 §7 | AGENTS、DB-SCHEMA-AUDIT 等 |
| ARCHITECTURE-TOPOLOGY.md | **更新后保留** | 「业务模型全景拓扑图」头部 v0.27，与 WORKER-TOPOLOGY 同款滞后（引用面含 ozonharness/ARCHITECTURE） | AGENTS、DB-SCHEMA-AUDIT、ozonharness |
| API-OVERVIEW.md | 更新后保留 | 叙述层活文档；版本戳与 §11 两条失真（§7/§8） | AGENTS、api-integration/README 等 |
| CONTRACT-v4.md | 保留 | 契约权威，头部日期治理见 §7 | 全仓最高引用（11 文件） |
| API-REFERENCE.md | 保留 | 生成物（gen_api_docs），版本头已随 515b05c8 修为读根 VERSION | API-OVERVIEW 等 |
| DEPLOY.md / CACHE-WARM-RUNBOOK.md / DB-SCHEMA-AUDIT.md / ERROR-REPORT-TEMPLATE.md / MCP-SERVER.md / WORKFLOW.md / CONVENTIONS.md / WEBUI-CONVENTIONS.md / GIT-STREAM-INDEX.md | 保留 | 各域权威活文档（RUNBOOK 头部已自带 v0.70/0.72/0.73 演进标注，是好范例） | 各自引用面健康 |
| OZON-ATTRIBUTE-API.md | 保留 | 「长期维护，开发直接查这里」，注明数据来源与实测日期——元数据实践的好范例 | AGENTS |
| OZON-MULTI-SKU-QUOTA.md | 保留（补状态行） | v0.59 调研结论，机制性内容仍有效（多 SKU 配额无时效） | AGENTS |
| ozon-field-map-v1.md | 保留 | 「M0 探针冻结」字段映射表，性质是冻结快照，无过期问题 | AGENTS、PRD-store-sync |
| COMPETITOR-ERP-ANALYSIS.md | 保留（移入 competitor/ 候选） | 竞品分析，仅 competitor/README.md 引用——**唯一建议的明确错放移动**：它逻辑上属于 docs/competitor/（见 §3） | competitor/README |
| POUNDING-WORKER-STORE-ANALYSIS.md | 保留（补状态行） | 对接文档，v0.74 数据池批后部分事实需核对（sku_metrics_pool 未反映与否待查） | AGENTS |
| PRD-skill-learn-shangpinbang-v1.md / PRD-store-sync-erp-v1.md | 保留 | PRD 是需求真相源 | 互引 + AGENTS |
| PLAN-*.md（14 个） | 逐个见 §6 | 治理动作是补状态行而非移动 | 见 §6 |

### 2.2 数据文件与子目录

| 资产 | 判定 | 理由 |
|---|---|---|
| `China_scoring_..._(1).xlsx` | **更新后保留**（改名候选） | 唯一代码消费者 import_logistics.py 硬编码 docs/ 路径；worker/assets 已有同名副本。建议：代码改读 assets 副本 → docs/ 副本删除（P2，涉代码一行） |
| `ozon-api-docs-2026-07-05 (3).json` | 更新后保留（改名候选） | 零代码消费者，仅 OZON-ATTRIBUTE-API.md 引用为来源；改名同步一处引用即可 |
| 4 个尺码表 CSV | 保留 | size_mapper.py 运行时读 **worker/assets/** 副本；docs/ 是源拷贝（双副本事实，P3 登记） |
| docs/refs/ozon-mcp/ | 保留 | 只读参考库（README 声明纪律），含上游留档 README.orig.md 合理；18 个 .pyc 为本地未跟踪（P3） |
| docs/competitor/ | 保留 | 5 文件竞品逆向 + README 索引，结构自洽 |
| docs/ozonharness/ | 保留 | 11 文件 harness 架构文档集（自含 README），AGENTS 有引用 |
| docs/audit/ | 保留 | 审计惯例目录（2026-09-09-race-duplication 先例 + 本次报告） |

---

## 3. 目录重构方案（能力域 → 业务场景 → 接口/数据 → 最佳实践）

**结论：零强制移动、两个建议移动、其余靠 docs/README.md 虚拟组织。** 理由：CONTRACT-v4/API-OVERVIEW/AGENTS 等存在大量**路径型引用**（如 `docs/CONTRACT-v4.md` 在 11 个文件中出现），大规模 git mv 的断链代价远超收益；且 legacy 归档先例（git mv 76 文件）已证明移动后必然产生指路漂移（§1-5）。

**建议移动（仅 2 项，各需同步引用方）**：
1. `docs/COMPETITOR-ERP-ANALYSIS.md` → `docs/competitor/`（唯一引用方 competitor/README 同目录，改动面最小）。
2. 3 个数据文件（xlsx/json）→ `docs/data/`（连带改 import_logistics.py 路径一行 + OZON-ATTRIBUTE-API.md 一处引用）。CSV 是否随迁待定：docs/ 副本无直接消费者，可与 worker/assets 副本二选一（超本文范围，P3 登记）。

**虚拟分层结构（docs/README.md 的组织骨架，文件不动）**：

```
docs/
├─ L1 入口与协作        AGENTS.md(仓库级,不在docs) · WORKFLOW · CONVENTIONS · WEBUI-CONVENTIONS · GIT-STREAM-INDEX
├─ L2 接口与契约        API-OVERVIEW · API-REFERENCE(生成物) · CONTRACT-v4 · MCP-SERVER · API-INTEGRATION-GUIDE(墓碑)
├─ L3 架构与数据        WORKER-TOPOLOGY · ARCHITECTURE-TOPOLOGY · DB-SCHEMA-AUDIT · ozon-field-map-v1
│                       OZON-ATTRIBUTE-API · OZON-MULTI-SKU-QUOTA
├─ L4 运维最佳实践      DEPLOY · LOGGING · CACHE-WARM-RUNBOOK · ERROR-REPORT-TEMPLATE
├─ L5 产品与方案        PRD×2 · DESIGN-PHASE2 · POUNDING-WORKER-STORE-ANALYSIS · PLAN-*(14)
├─ 参考（物理分组）     refs/ozon-mcp(外部API参考) · competitor/(竞品逆向) · ozonharness/(子产品文档)
├─ 审计（物理分组）     audit/<date-topic>/（既有惯例：2026-09-09-race-duplication）
└─ 数据（建议新建）     data/（xlsx/json/CSV 源拷贝）
```

分层判据：L1 回答「怎么协作」；L2 回答「怎么调」；L3 回答「系统是什么样」；L4 回答「怎么运维」；L5 回答「为什么这么做/打算做什么」。每个新文档入 docs/ 时必须在 README.md 声明所属层（见 §5 探针）。

---

## 4. frontmatter 元数据规范

**结论：7 字段最小集 + 三类豁免（生成物/根导航/冻结快照）+ 探针只验「存在性」不验「时效」。**

```yaml
---
title: 属性缓存全量化运维手册        # 唯一人读名（文件名以外）
purpose: 缓存预热/导出/COS 上传的操作手册  # 一句话适用场景，≤30 字
applies-version: ">=v0.72.0"        # 语义：内容经验证于该版本（tested-at 语义，非适用上限——避免每次发版必 bump）
last-updated: 2026-09-09            # 最后实质内容变更日（与 git log 对齐）
owner: worker-cache                 # 责任域（能力域词，非人名）：如 worker-api / skill-cdp / deploy
depends: [DEPLOY, CONTRACT-v4]      # 依赖的上游文档（文件名，不含路径）
status: active                      # active / archived / draft / frozen（冻结快照如 ozon-field-map）
---
```

| 权衡点 | 决策 |
|---|---|
| API-REFERENCE.md | **不手写 frontmatter**——gen_api_docs 生成物，头部版本戳已由脚本维护（515b05c8 先例）；探针豁免 |
| AGENTS/README/CHANGELOG（根级导航与流水） | 豁免——自身就是元数据载体 |
| ozon-field-map-v1 等「冻结快照」 | status: frozen，applies-version 写死冻结版本，永不要求更新 |
| last-updated 时效性 | **不做 CI 强校验**（避免发版机械 bump 失去信息量）；仅探针告警「last-updated 早于该文件最近 git 提交日期 >30 天」提示补写 |

**示例 1 — CACHE-WARM-RUNBOOK.md**（现头部：`# 属性缓存全量化运维手册（CACHE-WARM-RUNBOOK）— v0.70 / 三桶策略 v0.72 / 导出流程 v0.73`，标题行堆版本 → 移入 frontmatter）：

```yaml
---
title: 属性缓存全量化运维手册
purpose: dictionary_value_cache 三桶预热、--export-from-pg 导出、COS 上传与分片重预热的操作手册
applies-version: ">=v0.73.0"        # 导出流程 --export-from-pg 为 v0.73 口径
last-updated: 2026-09-09
owner: worker-cache
depends: [DEPLOY, DB-SCHEMA-AUDIT]
status: active
---
```

**示例 2 — DEPLOY.md**（现头部零元数据 → 最差样本改写）：

```yaml
---
title: Worker 云端部署指南
purpose: deploy/ docker compose 首次部署、cos-update.sh 升级、Nginx/HTTPS、部署数据初始化
applies-version: ">=v0.73.0"        # cos-update 自举/VERSION 剥 v 均为 v0.73 行为
last-updated: 2026-09-09
owner: deploy
depends: [CACHE-WARM-RUNBOOK, API-OVERVIEW]
status: active
---
```

落地顺序：先给 L2/L4 的 13 个高频文档补 frontmatter（一次 PR），L3/L5 随各文档下次实质修改顺带补（探针只对新文档强制）。

---

## 5. docs/README.md 索引设计

**结论：一页索引，按 §3 五层组织，每条一句话；索引本身带防漂移探针（每个 docs/*.md 必须在索引中出现）。**

````markdown
# docs/ 文档索引

> 新会话先读 AGENTS.md「60 秒上手」；本页只做 docs/ 内导航。**改 docs/ 必须同步本表**（CI 探针校验覆盖度）。

## L1 入口与协作
- [WORKFLOW.md](WORKFLOW.md) — 仓库协作规范 v1：分支拓扑、一会话一分支一 worktree、两级合并门槛、发版流。
- [CONVENTIONS.md](CONVENTIONS.md) — 开发规范：分支命名、commit 规范、发版流程。
- [WEBUI-CONVENTIONS.md](WEBUI-CONVENTIONS.md) — webui 开发规范（视觉 token、构建、目录约定）。
- [GIT-STREAM-INDEX.md](GIT-STREAM-INDEX.md) — 12 工作流历史索引（取证用）。

## L2 接口与契约
- [API-OVERVIEW.md](API-OVERVIEW.md) — 对外 API 叙述层：Base URL/双鉴权/限流/错误信封/13 阶段/版本策略。
- [API-REFERENCE.md](API-REFERENCE.md) — 全端点参考，**自动生成勿手改**（gen_api_docs.py）。
- [CONTRACT-v4.md](CONTRACT-v4.md) — Skill↔Worker 信封契约 v4.0：端点、请求/响应、错误码、节点合约。
- [MCP-SERVER.md](MCP-SERVER.md) — worker 远程 MCP 接入指南 + 工具清单 + 客户端配置。
- [API-INTEGRATION-GUIDE.md](API-INTEGRATION-GUIDE.md) — （已废弃为重定向）集成方请读 API-OVERVIEW → API-REFERENCE。

## L3 架构与数据
- [WORKER-TOPOLOGY.md](WORKER-TOPOLOGY.md) — Worker 节点拓扑 + 错误映射 + 数据流，改代码快速参考（⚠️ 正文 v0.27 口径 + 头部增量摘要，见 §7）。
- [ARCHITECTURE-TOPOLOGY.md](ARCHITECTURE-TOPOLOGY.md) — 业务模型全景拓扑图（v0.27 口径）。
- [DB-SCHEMA-AUDIT.md](DB-SCHEMA-AUDIT.md) — 34 表分类、14 歧义点、ID 词汇表、status 取值域（建表/改列必读）。
- [ozon-field-map-v1.md](ozon-field-map-v1.md) — Ozon 字段映射表（M0 探针冻结快照）。
- [OZON-ATTRIBUTE-API.md](OZON-ATTRIBUTE-API.md) — Ozon 属性/类目 API 参考（长期维护，开发直接查这里）。
- [OZON-MULTI-SKU-QUOTA.md](OZON-MULTI-SKU-QUOTA.md) — 多 SKU 上传与商品配额机制调研结论。

## L4 运维最佳实践
- [DEPLOY.md](DEPLOY.md) — 云端部署完整指南（Docker/Nginx/HTTPS/cos-update 升级回滚）。
- [LOGGING.md](LOGGING.md) — 日志系统架构 + 查看命令 + 故障排查。
- [CACHE-WARM-RUNBOOK.md](CACHE-WARM-RUNBOOK.md) — 字典值缓存三桶预热/导出/COS 上传运维手册。
- [ERROR-REPORT-TEMPLATE.md](ERROR-REPORT-TEMPLATE.md) — 错误报告模板与 agent 上报纪律。

## L5 产品与方案
- PRD：[PRD-store-sync-erp-v1.md](PRD-store-sync-erp-v1.md)（店铺数据同步 ERP 化）· [PRD-skill-learn-shangpinbang-v1.md](PRD-skill-learn-shangpinbang-v1.md)（Skill 学习上品帮）。
- [DESIGN-PHASE2-...md](DESIGN-PHASE2-PURCHASE-ADS-RECONCILIATION.md) — 阶段二预留设计（采购/广告 OAuth/财务对账）。
- [POUNDING-WORKER-STORE-ANALYSIS.md](POUNDING-WORKER-STORE-ANALYSIS.md) — 店铺分析对接文档（3 新表 + 分析/执行端点）。
- PLAN-*（14 个）—— 状态一览见本页末 PLAN 状态表（§6 规范落地后由状态行生成）。

## 参考与审计（物理目录）
- [refs/ozon-mcp/](refs/ozon-mcp/README.md) — Ozon API 参考库（只读，零凭证查询纪律）。
- [competitor/](competitor/README.md) — 竞品逆向（上品帮/毛子 + ERP 分析）。
- [ozonharness/](ozonharness/README.md) — harness 子产品文档集（架构/PRD/路线图）。
- audit/<日期-主题>/ — 治理与审计报告（本页所在目录即先例）。
- data/ — 数据源文件（物流费率 xlsx、Ozon 官方文档抓取 json、尺码表 CSV）。
````

探针：`diff <(ls docs/*.md) <(grep -oE '\((〔A-Z〕[^)]+\.md)\)' docs/README.md)` 变体——索引覆盖度校验进 ci.sh Step 5e（§8 P1 探针）。

---

## 6. PLAN-* 状态行统一规范

**结论：状态行固定为头部第 3 行 `> 状态: <值>`；值集 7 个；14 个 PLAN 逐一判定如下。已完成 PLAN 不移动**（AGENTS 大量按路径引用，如「执行记录见 docs/PLAN-xxx.md」，移动即断链），只补状态行。

状态值集：`drafted`（起草待批）/ `approved`（已批准未开工）/ `in-progress`（执行中）/ `completed-dev`（已实施合 dev，未发版）/ `shipped-with-vX.Y.Z`（已随版发布）/ `abandoned`（废弃）/ `superseded-by:<plan-name>`（被取代，指新计划）。

| PLAN | 判定状态 | 依据 |
|---|---|---|
| card-merge-fix-v1 | **shipped-with-v0.60.0** | 头部「只写不实现」**与事实矛盾**（AGENTS v0.60 块 Q4 + CHANGELOG）——P0 修正项 |
| conversation-entry-v1 | **shipped-with-v0.60.0** | 同上（Q3，router.py/POST /ask 已落地）——摸底未发现的第二处矛盾 |
| user-feedback-fixes-v073 | shipped-with-v0.74.0 | CHANGELOG [0.74.0]：0.73.0 修复批次同车，未单独 tag |
| w1w8-cos-deploy-fixes-v073 | shipped-with-v0.74.0 | 同上（W1-W8 部署链修复同车） |
| shopbang-parity-v1 | shipped-with-v0.74.0 | CHANGELOG [0.74.0]「shopbang-parity 三批」 |
| data-pool-parity-v1 | shipped-with-v0.74.0 | PR #9 同车；gate 诚实记录（what-to-sell 数据面平台侧阻断） |
| harness-mcp-adoption-v1 | in-progress（跨仓） | 批次 1 已落 v0.67.0；批次 3 在 harness 独立仓库施工（AGENTS：大部分已施工） |
| discover-funnel-v2-v1 | shipped-with-v0.72.0 | CHANGELOG「discover 选品漏斗 v2（同版收录）」（有状态行「已批准」需更新为已发版） |
| cross-platform-sourcing-v1 | completed-dev | PR #11 已合 dev、实机 gate 过；批 3 提交在 v0.74.0 之后 → 下一版随车 |
| discover-cross-source-v1 | in-progress | 2026-09-10 批 3 接线提交在 v0.74.0 后（80fe8f39/1f87ffd7），未发版 |
| category-tree-refresh-v1 | in-progress | 3939e109 脚本落地（--dry-run 验证过），真树重导 gate 未闭环（AGENTS 零提及 = 未收口） |
| race-duplication-audit-v1 | drafted | 自标「待用户审批」，Task 11 停在用户拍板关口 |
| repo-gov-v1 | in-progress | 自标「执行中」（本审计即其 A1 波） |
| skill-image-search-v1 | shipped-with-v0.40.0 | 2026-08-12 定稿，aibuy 通道等已落地；AGENTS 仍按「必读参考」引用 → 状态行补 shipped 后**保留原位**（它兼 Issue 落地记录） |

规范细则：状态变更只改状态行 + 追加一行变更记录（`> 状态更新: 2026-09-11 shipped-with-v0.74.0（CHANGELOG 链接）`），不改写正文（保留方案原貌供考古）；`superseded-by` 必须指到存活文档。

---

## 7. 双真相源治理方案

**结论：三个案例同根——文档头部复制了「会漂移的事实」（日期/版本/计数）。收敛原则：每类事实只允许一个真相源，文档头只留指针。**

| 案例 | 单一真相源 | 收敛动作 |
|---|---|---|
| WORKER-TOPOLOGY（正文 v0.27 + 增量摘要停 v0.70） | **代码是拓扑唯一真相源**；文档=导航+解释 | ①短期：把增量摘要补齐 v0.71–v0.74 四条（素材就在 AGENTS 对应版本块与 CHANGELOG，半天工作量）；②中期立规矩：**增量摘要 ≥8 条或跨 ≥3 个 minor → 强制正文重写**（写进 CONVENTIONS）；③头部版本戳改为「增量摘要覆盖至 v0.74」语义（摘要最后一条的版本），不再维护独立的「对应版本」字段——版本随摘要最后条自然推进 |
| CONTRACT-v4（头部日期 07-30 vs 内容 09-10） | **git log 是日期唯一真相源** | 头部 `日期:` 字段**删除**，替换为 `> 状态: active（契约版本 v4.0 不变；内容随 dev 演进，日期以 git log 为准）`。契约版本号（v4.0）只在**破坏性变更**时 bump，与发版号解耦——头部日期天然必腐，删掉即根治 |
| API-OVERVIEW（v0.70 vs VERSION 0.74.0 + §11 两条失真） | **VERSION 文件=版本真相；代码=端点/工具数真相** | ①头部「对应 v0.70.0」改由 gen_api_docs.py 顺手注入（该脚本已读根 VERSION，515b05c8 先例，改动约 3 行）——版本戳变成生成物，与 API-REFERENCE 同批刷新，发版 checklist 已有「bump 后重跑 gen_api_docs」步骤，零新增纪律；②§11 文档地图删去一切可计数事实（「17 工具」「快照式」），只留「工具清单见 MCP-SERVER.md」指针 |

**通用规范（写进 CONVENTIONS.md「文档纪律」节）**：文档正文禁止复制以下三类事实的**当前值**——版本号（指 VERSION/tag）、日期（git log 可查）、数量/清单（指权威文档），必须以指针形式引用；「改前必读」注释块里的 file:line 例外（行号漂移已在 AGENTS 用「行号以当前 dev 分支为准」口径处理，沿用）。

---

## 8. 发现分级与修复建议

| 级 | 发现 | 建议 | 探针方案 |
|---|---|---|---|
| **P0** | 误导性事实×5：WORKER-TOPOLOGY/ARCHITECTURE-TOPOLOGY 停旧版却被 AGENTS 指为「改前必读」；API-OVERVIEW §11 两条失真（17 工具/墓碑描述）；AGENTS 4 处把 legacy 内文件当活文档指路（:322/:378/:573/:1159）；2 个 PLAN「只写不实现」与发版史矛盾 | 补齐 WORKER-TOPOLOGY 增量至 v0.74 + 修 §11 两条 + AGENTS 4 处改为「（已归档，考古用）」措辞 + 2 个 PLAN 状态行改 shipped | `grep -n "archive/docs/legacy" AGENTS.md` 全部命中须含「已归档」或删除指路语义；`grep -c "17 工具\|快照式" docs/API-OVERVIEW.md` = 0 |
| **P0** | LOGGING.md 标 v1.0/07-18 但被 5 处指路，13 阶段/进度 PG 回退等待性未核 | 对照现行 task_processor 核一遍，过期段落更新 + 补 frontmatter | 人工核（半页文档，成本低于探针） |
| **P1** | docs/ 无索引：Agent 定位文档依赖 AGENTS 单点（其顶部历史块 1000+ 行噪声高） | 落地 §5 docs/README.md（一次 PR） | ci.sh 新增 Step 5e：索引覆盖度 diff（ls vs README 引用），缺失即红 |
| **P1** | PLAN 状态缺失 10 个 + 状态值无规范 | §6 状态行统一（一次 PR，14 文件各 1 行） | 探针：`head -3 docs/PLAN-*.md \| grep -L "^> 状态:"` = 空 |
| **P2** | 39 文件零 frontmatter；DEPLOY 零元数据 | §4 规范，L2/L4 13 文件先补 | 探针：新改动文档缺 frontmatter 时 CI warn（不 block） |
| **P2** | 物流 xlsx 双副本双路径 + 残留名硬编码（import_logistics 读 docs/、init_data 读 assets/） | 代码改 import_logistics 读 assets 副本 → docs/ 副本删除 + `docs/data/` 收纳数据文件（涉 1 行代码，随下个 worker PR） | `ls docs/*.xlsx docs/*" ("*.json` = 空；`grep -rn '" (1)\|"(3)' worker/scripts` = 空 |
| **P2** | IMAGE-PROMPT-GUIDE 过期未归档 | git mv → archive/docs/legacy/（沿用 legacy 惯例，CHANGELOG 提及不受影响） | 移动前跑下方全仓链接检查，断链清单入 PR 描述 |
| **P3** | 18 个 .pyc 滞留主 worktree docs/refs（未入库）；README.orig.md 上游留档 | 本地清理 `find docs/refs -name __pycache__ -exec rm -rf`；__pycache__ 已被全局 gitignore 覆盖，无需改规则 | `git status docs/refs` 应恒空 |
| **P3** | ozon-probe/、.bok/ 本地目录口径不明 | 在 .gitignore 注释块标注「本地工作目录，不入库」（1 行注释） | 无 |

**归档前断链检查命令草案**（P2 探针，亦适用于任何 docs/ 移动 PR）：

```bash
# 1) 收集全仓 md 对 docs/ 的路径引用；2) 校验目标存在。输出断链清单，非空即红。
grep -rhoE "docs/[A-Za-z0-9_./-]+\.(md|json|xlsx|csv)" \
  --include="*.md" AGENTS.md README.md CHANGELOG.md docs/ skill/ worker/ webui/src pounding-mcp/ deploy/ 2>/dev/null \
  | sort -u | while read -r p; do
    [ -f "$p" ] || [ -f "archive/$p" ] || echo "BROKEN: $p"
  done
# 说明：命中 archive/ 前缀视为「合法 legacy 引用」单独列白名单文件人工复核（对应 P0-AGENTS 措辞项）。
```

**执行批次建议**：B1（P0，半日）= 事实修正 5 处 + LOGGING 核对；B2（P1，半日）= docs/README.md + PLAN 状态行；B3（P2，随车）= frontmatter 13 文件 + IMAGE-PROMPT-GUIDE 归档 + xlsx 收纳；B4（P3，随手）= 本地卫生。全部动作均在 docs/repo-gov-v1 分支按 AGENTS「逐文件 git add」纪律提交，发版不依赖本批（纯文档）。
