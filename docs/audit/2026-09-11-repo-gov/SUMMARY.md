# 审计总报告 SUMMARY — ozon-worker 仓库全量验证与 Debug 治理 v1

> 2026-09-11 · 基线 v0.74.0（gov worktree，dev tip 044b0c61）· 方法：3 波 × 3 并行审计 subagent（glm-5.3-flash 规格）+ 主会话逐份交叉验证（四条 P1 断言亲核 grep 实锤）。
> 修复清单详见同目录 [BACKLOG.md](BACKLOG.md)；本报告只述结论与证据指针。

## 总体结论

1. **架构与写入链质量高于预期**：上架管线「清洗→预检→自愈」三层校验成熟（A3）；拓扑无隐藏节点、双 MCP 与 REST 路由零漂移（A5/A6）；WebUI↔后端 142 端点引用零缺口、harness 白名单 100% 吻合（A6）；凭证 AES-GCM AAD 绑定与跨租户 404 防线扎实（A8）。
2. **最大风险集中在四条横向链**：①汇率死缓存（定价恒 12.0 兜底）②Ozon 限流 fail-open ③备份/迁移无框架 ④variant_v2 真值链「定义存在、生产未接线」——v0.74 发版说明宣称的能力实际未生效。
3. **文档面是最大欠账**：API 示例覆盖 D+（91% 操作双无示例、77/86 POST 无 requestBody 声明）；docs 无索引无 frontmatter；多处头部元信息落后正文（含 5 处 P0 级误导性事实）。
4. **发现计数**（去重后）：P0×9（5 处文档误导事实 + 3 项能力对标缺口 + 1 项 schema 资产缺失）/ P1×19 / P2×30+ / P3×25+。无「正在发生的数据不可逆丢失」型 P0。

## 各审计核心结论

| # | 报告 | 一句话核心 | 重点发现 |
|---|---|---|---|
| A1 | [doc-governance](A1-doc-governance.md) | 治理主线=最小移动+虚拟索引（五层 README 索引+7 字段 frontmatter+PLAN 7 值状态行） | P0×5 失真：API-OVERVIEW「17 工具」实 29、AGENTS 158/153 打架、DESIGN-PHASE2 实为活文档勿归档；归档仅 IMAGE-PROMPT-GUIDE 1 个 |
| A2 | [competitor-parity](A2-competitor-parity.md) | 我方结构性领先于采集护栏/属性自动化/定价/生图；短板=单平台/财务对账/premium 分析/插件形态 | P0×3 缺口（财务对账、premium 店铺分析、订单全成本利润）；旧学习笔记 21 项缺口中 11 项已闭合（防重复立项清单） |
| A3 | [data-quality](A3-data-quality-consistency-backup.md) | 写入链强、对账全局无、备份最弱 | G-01 汇率死缓存（亲核）、G-02 备份无强制点、G-03 无迁移框架、G-05 repair_cards.py 硬编码 API key |
| A4 | [category-attribute](A4-category-attribute-mapping.md) | Ozon schema **无** min_length/pattern 字段（契约亲核推翻预设）；校验雏形三处非同源 | P0×1 属性层无资产化产物（schema manifest）；统一「类目合规校验器」设计已给出；attribute_cache 本地 0 行属懒加载正常态 |
| A5 | [pipeline-deadcode](A5-pipeline-deadcode-conflicts.md) | 拓扑验收通过；死代码 11 组；冲突增量 10 条 | **variant_v2 三段断链（P1，亲核：fetch_variant_truth skill 定义存在、variant_payloads 参数无人传值、needs_variant_sync 零消费）**；sku_metrics_pool 写侧非原子；shelf 3 端点死面；error_classifier 整模块死 |
| A6 | [api-mcp-webui](A6-api-mcp-webui-alignment.md) | 计数口径对账清楚（158=未去重/147 path/183 op）；MCP 红线零违规；WebUI 零真实缺口 | P1×2：91% 操作无示例（D+）+ 77 POST 无 requestBody；签名漂移 2 处（graph 缺 --category-id、discover_task 缺 --expend-shop）；session-sync 应封装 MCP |
| A7 | [data-model-cache](A7-data-model-cache-mapping.md) | 45 表（非 44/34）；缓存映射 12 链路入表；指引文档可直接提升为正式 docs | logistics_rates 导入两提交间并发查询落 fallback（P1）；attribute_cache type_id NULL 裂行隐患；category_cache TTL 10 年 |
| A8 | [user-assets](A8-user-assets-reconciliation.md) | SSH 双目标不可达（代理劫持/疑似 fail2ban）→ 7 组只读探针脚本交付待实跑；映射图+对账方案完整 | draft_submissions 无 tenant_id 列（亲核）+任务 30 天物理删=永久悬空；MXOU key 明文落库两列（F6）；单侧记账零对账（F1，附流水表 DDL+对账双通道方案） |
| A9 | [systemic-risks](A9-systemic-risks.md) | 22 条风险（P1×2/P2×12/P3×8）；六依赖超时重试熔断矩阵成表 | S9-01 限流器 acquire 返回值被忽略（亲核 ozon_client.py:91）+全局桶非 per-credential；租户过滤无集中 guard；全线无熔断器；三防全缺 |

## 主会话交叉验证记录（P1 断言亲核）

1. `fetch_variant_truth`：worker/src 零引用；skill 定义存在（ozon_widget.py:918），`_giveback_metrics(variant_payloads=None)` 默认空——**断链实锤**。
2. `set_exchange_rate`：全仓（worker+skill）仅定义零调用——**死缓存实锤**。
3. `_rate_limiter.acquire(endpoint)`：ozon_client.py:91 返回值未接收——**fail-open 实锤**。
4. `DraftSubmission`：字段 id/draft_id/credential_id/store_client_id/extensions/status——**无 tenant_id 实锤**。

## 模块 → 交付物 → 状态映射

| 用户模块 | 交付物 | 状态 |
|---|---|---|
| 1.1 文档治理 | A1 报告（归档清单/重构方案/frontmatter 规范/索引设计） | ✅ 审计完成，落地在 B1 |
| 1.2 参考项目对标 | A2《能力对标与缺口清单》 | ✅ 完成 |
| 1.2 类目-属性-特征 | A4《映射规则+校验器设计》（5 类目抽样受本地缓存 0 行限制，回填路径=探针） | ✅ 完成（抽样待生产/预热数据） |
| 1.2 质量/一致性/备份 | A3 报告（值域矩阵/副本对 6 张/备份 RTO-RPO） | ✅ 完成 |
| 2 拓扑/死代码/冲突 | A5《死代码清单 11 组》+《冲突与性能风险 10 条》 | ✅ 完成 |
| 3 分支盘点合并 | Phase 0 执行记录（PLAN-repo-gov-v1.md） | ✅ 完成（5→1 worktree，分支收敛 dev+main） |
| 4 API/MCP/WebUI | A6《对齐清单》+三示例打分+WebUI 审计（零缺口） | ✅ 完成，补齐在 B1 |
| 5 数据模型缓存映射 | A7《指引文档》（45 表+12 缓存链路+排查手册） | ✅ 完成 |
| 6 用户资产对账 | A8《映射图+对账方案+7 探针》（生产实跑受阻待 SSH 恢复） | ✅ 设计完成，实跑待定 |
| 7 子 Agent 规范 | 本计划执行模型已实践（3 波 9 subagent）；规范固化在 B3 | 🟡 半 |
| 8 变更管控 | 探针方案已入各报告；流程固化在 B3 | 🟡 半 |
| 9-12 体系风险 | A9《风险登记册 22 条》 | ✅ 完成 |
| 13 执行模型 | 主会话审计/规划/验收 + flash subagent 调研执行 | ✅ 本轮实践 |

## 建议的 Phase B 批次（详见 BACKLOG.md，待拍板）

- **B2-α 高优代码修复**（预计最先做）：汇率刷新接线、限流返回值处理+per-credential 桶、variant_v2 处置（需产品决策：接线 vs 标记实验性）、repair_cards 凭证清除+轮换、sku_metrics_pool 原子化、ozon_sessions 擦除补漏、缓存过期清扫。
- **B1 文档治理**：A1 方案落地（索引/frontmatter/状态行/失真修正）+ A6 示例补齐战役 + MCP 封装 session-sync + 签名漂移修正。
- **B2-β 基建**：备份 cron sidecar+health 心跳、schema 迁移版本表、对账探针三件套（汇率 mtime/last_backup_at/副本计数）。
- **B3 规范固化**：子 Agent 规范、WORKFLOW 探针闸门增补、RESTORE-RUNBOOK、A8 生产探针实跑（SSH 恢复后）。
