---
title: docs/ 文档索引
purpose: 按五层组织的 docs/ 导航（改 docs/ 必须同步本表）
applies-version: ">=v0.74.0"
last-updated: 2026-09-11
owner: docs-gov
depends: []
status: active
---

# docs/ 文档索引

> 新会话先读仓库根 `AGENTS.md`「60 秒上手」；本页只做 docs/ 内导航。**改 docs/ 必须同步本表**（CI 探针校验覆盖度：每个 `docs/*.md` 必须在本页出现）。
>
> 分层判据：L1 回答「怎么协作」；L2 回答「怎么调」；L3 回答「系统是什么样」；L4 回答「怎么运维」；L5 回答「为什么这么做/打算做什么」。新文档入 docs/ 时必须在对应层登记，并按 CONVENTIONS 补 7 字段 frontmatter（title/purpose/applies-version/last-updated/owner/depends/status）。

## L1 入口与协作

- [WORKFLOW.md](WORKFLOW.md) — 仓库协作规范 v1：分支拓扑、一会话一分支一 worktree、两级合并门槛、发版流。
- [CONVENTIONS.md](CONVENTIONS.md) — 开发规范：分支命名、commit 规范、发版流程。
- [WEBUI-CONVENTIONS.md](WEBUI-CONVENTIONS.md) — webui 开发规范（视觉 token、构建、目录约定）。
- [GIT-STREAM-INDEX.md](GIT-STREAM-INDEX.md) — 12 工作流历史索引（2026-09-08~09-09 交错历史取证用）。

（仓库级入口 `AGENTS.md` 在根目录，不在 docs/。）

## L2 接口与契约

- [API-OVERVIEW.md](API-OVERVIEW.md) — 对外 API 叙述层：Base URL/双鉴权/限流/错误信封/13 阶段/版本策略。
- [API-REFERENCE.md](API-REFERENCE.md) — 全端点参考，**自动生成勿手改**（`worker/scripts/gen_api_docs.py`）。
- [CONTRACT-v4.md](CONTRACT-v4.md) — Skill↔Worker 信封契约 v4.0：端点、请求/响应、错误码、节点合约。
- [MCP-SERVER.md](MCP-SERVER.md) — worker 远程 MCP 接入指南 + 工具清单 + 客户端配置。
- [API-INTEGRATION-GUIDE.md](API-INTEGRATION-GUIDE.md) — （已废弃为重定向）集成方请读 API-OVERVIEW → API-REFERENCE。

## L3 架构与数据

- [WORKER-TOPOLOGY.md](WORKER-TOPOLOGY.md) — Worker 节点拓扑 + 错误映射 + 数据流，改代码快速参考（正文 v0.27 口径 + 头部增量摘要，增量以 AGENTS「最近更新」块为准）。
- [ARCHITECTURE-TOPOLOGY.md](ARCHITECTURE-TOPOLOGY.md) — 业务模型全景拓扑图（正文 v0.27 口径，增量以 AGENTS「最近更新」块为准）。
- [DB-SCHEMA-AUDIT.md](DB-SCHEMA-AUDIT.md) — 34 表分类、14 歧义点、ID 词汇表、status 取值域（建表/改列必读）。
- [ozon-field-map-v1.md](ozon-field-map-v1.md) — Ozon 字段映射表（M0 探针冻结快照，status: frozen）。
- [OZON-ATTRIBUTE-API.md](OZON-ATTRIBUTE-API.md) — Ozon 属性/类目 API 参考（长期维护，开发直接查这里）。
- [OZON-MULTI-SKU-QUOTA.md](OZON-MULTI-SKU-QUOTA.md) — 多 SKU 上传与商品配额机制调研结论（9048/model_id 并卡）。

## L4 运维最佳实践

- [DEPLOY.md](DEPLOY.md) — 云端部署完整指南（Docker/Nginx/HTTPS/cos-update 升级回滚）。
- [LOGGING.md](LOGGING.md) — 日志系统架构 + 查看命令 + 故障排查。
- [CACHE-WARM-RUNBOOK.md](CACHE-WARM-RUNBOOK.md) — 字典值缓存三桶预热/导出/COS 上传运维手册。
- [ERROR-REPORT-TEMPLATE.md](ERROR-REPORT-TEMPLATE.md) — 错误报告模板与 agent 上报纪律。

## L5 产品与方案

- PRD：[PRD-store-sync-erp-v1.md](PRD-store-sync-erp-v1.md)（店铺数据同步 ERP 化）· [PRD-skill-learn-shangpinbang-v1.md](PRD-skill-learn-shangpinbang-v1.md)（Skill 学习上品帮）。
- [DESIGN-PHASE2-PURCHASE-ADS-RECONCILIATION.md](DESIGN-PHASE2-PURCHASE-ADS-RECONCILIATION.md) — 阶段二预留设计（采购域/广告 OAuth/财务对账），新开里程碑时以此为准。
- [POUNDING-WORKER-STORE-ANALYSIS.md](POUNDING-WORKER-STORE-ANALYSIS.md) — 店铺分析对接文档（3 新表 + 分析/执行端点）。
- PLAN-\*（15 个）—— 状态一览见本页末「PLAN 状态表」；状态行规范：头部第 3 行 `> 状态: <值>`，值集见 docs/audit/2026-09-11-repo-gov/A1-doc-governance.md §6。

## 参考与审计与数据（物理目录）

- [refs/ozon-mcp/](refs/ozon-mcp/README.md) — Ozon API 参考库（只读，零凭证查询纪律；写 Ozon 调用前先 `mcp__ozon__search_methods`/`describe_method` 核对）。
- [competitor/](competitor/README.md) — 竞品逆向（上品帮/毛子全量逆向 + [COMPETITOR-ERP-ANALYSIS.md](competitor/COMPETITOR-ERP-ANALYSIS.md) 精简对比）。
- [ozonharness/](ozonharness/README.md) — harness 子产品文档集（架构/PRD/路线图）。
- [audit/](audit/) — 治理与审计报告（按 `<日期-主题>/` 分目录，最新：[2026-09-11-repo-gov/](audit/2026-09-11-repo-gov/SUMMARY.md)）。
- [data/](data/) — 数据源文件（`china_scoring_freight.xlsx` 物流费率源表、`ozon-api-docs-2026-07-05.json` Ozon 官方文档抓取；`worker/assets/` 有同名 xlsx 运行时副本，`import_logistics.py` 读 docs/data/ 侧、`init_data.py` 读 assets/ 侧）。

## PLAN 状态表（由各 PLAN 头部状态行生成，2026-09-11）

| PLAN | 状态 |
|---|---|
| [PLAN-card-merge-fix-v1](PLAN-card-merge-fix-v1.md) | shipped-with-v0.60.0 |
| [PLAN-conversation-entry-v1](PLAN-conversation-entry-v1.md) | shipped-with-v0.60.0 |
| [PLAN-user-feedback-fixes-v073](PLAN-user-feedback-fixes-v073.md) | shipped-with-v0.74.0 |
| [PLAN-w1w8-cos-deploy-fixes-v073](PLAN-w1w8-cos-deploy-fixes-v073.md) | shipped-with-v0.74.0 |
| [PLAN-shopbang-parity-v1](PLAN-shopbang-parity-v1.md) | shipped-with-v0.74.0 |
| [PLAN-data-pool-parity-v1](PLAN-data-pool-parity-v1.md) | shipped-with-v0.74.0 |
| [PLAN-discover-funnel-v2-v1](PLAN-discover-funnel-v2-v1.md) | shipped-with-v0.72.0 |
| [PLAN-skill-image-search-v1](PLAN-skill-image-search-v1.md) | shipped-with-v0.40.0 |
| [PLAN-cross-platform-sourcing-v1](PLAN-cross-platform-sourcing-v1.md) | completed-dev |
| [PLAN-repo-gov-batch-b2a](PLAN-repo-gov-batch-b2a.md) | completed-dev |
| [PLAN-harness-mcp-adoption-v1](PLAN-harness-mcp-adoption-v1.md) | in-progress（批次 3 跨仓） |
| [PLAN-discover-cross-source-v1](PLAN-discover-cross-source-v1.md) | in-progress |
| [PLAN-category-tree-refresh-v1](PLAN-category-tree-refresh-v1.md) | in-progress |
| [PLAN-repo-gov-v1](PLAN-repo-gov-v1.md) | in-progress |
| [PLAN-race-duplication-audit-v1](PLAN-race-duplication-audit-v1.md) | drafted |
