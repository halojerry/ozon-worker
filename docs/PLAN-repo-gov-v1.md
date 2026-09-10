# PLAN-repo-gov-v1 — 仓库全量验证与 Debug 治理（阶段闸模式）

> 状态：**Phase A 已完成（2026-09-11，9 份审计+SUMMARY+BACKLOG），Phase B 待用户拍板批次**（主会话=审计/规划/分配/验收，调研与执行 subagent=glm-5.3-flash）。
> 探针先行：任何变更批次先探针（代码埋点/单测/数据校验/只读查询）→ 探针报告+影响评估 → 计划 → 实施 → review → 测试 → PR。
> 硬边界：生产仅 SSH 只读（零写入）；功能测试只打本地 Docker；改 API 必跑 `gen_api_docs.py`；worker 全量测试先 `lsof -iTCP:5433`。

## Phase 0：基线同步与工作树卫生（已完成 2026-09-11）

| 项 | 处置 | 结果 |
|---|---|---|
| AGENTS.md 未提交改动 | 判定为协作规范 v1 接线（他会话遗留）；`git apply --3way` 重放到 v0.74.0 基线，单冲突手工解决 | 044b0c61 已 push dev |
| worker/uv.lock +755 行 | W1-W8 会话遗留 WIP（上游 pyproject 零变化、该会话已收工），弃置可再生 | 已 checkout 还原 |
| pounding-sidebar/ 整目录删除 | 上游 origin/dev tip 仍包含该目录（无删除意图）→ 恢复 | 已 checkout 还原 |
| pounding-mcp/uv.lock（未跟踪） | 无任何文档引用的 `uv lock` 误产物 | 已删除 |
| docs/参考项目/（155MB 未跟踪） | 竞品解包二进制不入库，本地保留 | .gitignore 已加（6f716ce7） |
| worker/data/（未跟踪） | 类目树刷新运行时快照 | .gitignore 已加 |
| docs/.bok/ | 已被既有 .gitignore:132 覆盖，无泄露面 | 不动，登记为 A1 审计项 |
| dev 落后 70 提交 | rebase 同步到 origin/dev tip（v0.74.0 基线） | 完成 |
| 4 个已合 worktree/分支 | datapool/release/v0.74.0/xmatch/xsrc 全部已并入 dev、worktree 全干净 | worktree 5→1，本地+远端分支收敛 dev+main |

## Phase A：只读审计波（产物落 docs/audit/2026-09-11-repo-gov/A*.md）

每份报告结构：事实基线 → 发现（P0-P3 分级）→ 修复建议 → 探针方案。主会话逐份 review + 交叉校验。

| # | 主题 | 对应用户模块 | 覆盖要点 |
|---|---|---|---|
| A1 | 文档资产治理 | 1.1 | 归档清单/目录重构/frontmatter 规范/索引设计/PLAN-* 状态字段/双真相源治理 |
| A2 | 参考项目能力对标 | 1.2 前半 | shopbang 3 篇 md+goldminer 源码+docs/competitor → 《能力对标与缺口清单》（订单/物流/商品/店铺/财务/流量维度） |
| A3 | 数据质量/一致性/备份 | 1.2 尾部 | 值域枚举格式校验、脏/孤儿/重复识别、对账机制、备份 RTO/RPO 与演练 |
| A4 | 类目-属性-特征体系 | 1.2 专项 | attribute_cache schema+ozon mcp 契约 → 《映射规则文档》+校验器设计+5 类目抽样 |
| A5 | 管线拓扑+死代码+逻辑冲突 | 2 | 28 节点现状图核对、Skill 25 命令全景、死代码扫描（shelf/newapi_proxy/env 门）、findings.md 26 条增量 |
| A6 | API/MCP/WebUI 对齐 | 4 | REFERENCE 计数口径、三示例齐全度、skill 25 vs MCP 20、WebUI 17 路由×按钮映射、harness 白名单 |
| A7 | 数据模型与缓存映射 | 5 | 44 表修正、缓存层全清单（key/TTL/失效/一致性）、三核心表、排查手册 |
| A8 | 用户资产与对账 | 6 | 资产映射图、SSH 只读孤儿扫描、mxou 流水+对账方案 |
| A9 | 体系化风险审计 | 9-12 | 并发限流/速率适配/超时重试熔断/大任务分批/缓存三防/耦合扩展/测试发布闭环/多租户隔离 → 风险登记册 |

收口：《SUMMARY 总报告》+《BACKLOG 修复清单》（P0-P3，含探针方案与批次归属）→ **用户闸**。

## Phase B：修复落地（闸后，每批独立分支+PR）

- B1 文档治理落地（探针=全仓链接检查防断链）
- B2 高优代码修复（以 backlog 为准；候选：logistics_rates 唯一约束、mxou 流水表+对账、孤儿清扫、REFERENCE 计数、DB-SCHEMA-AUDIT 补表、MCP 封装补漏、缓存 TTL）
- B3 规范固化（子 Agent 规范/WORKFLOW 探针闸门增补/备份演练 runbook）
- 验收基线：worker 全量（PG 5433 先核）/skill/pounding-mcp 自身 venv/ruff CI 口径绿；触 API 则 gen_api_docs --check 零漂移
