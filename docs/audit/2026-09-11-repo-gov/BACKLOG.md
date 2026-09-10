# 修复 BACKLOG — repo-gov v1（Phase B 候选，待用户拍板优先级）

> 级别为综合判定（含主会话对个别条目的升降级：G-05 凭证泄露 P2→P1）。
> 每条的完整证据/方案/探针见对应 A 报告；预估：S=≤半天 / M=1-2 天 / L=3 天+。
> 批次归属为建议，拍板后每批出 bite-size 计划（docs/PLAN-repo-gov-batch-*.md）再派 subagent 实施。

## P1（19 条）

| ID | 来源 | 标题 | 批次 | 预估 |
|---|---|---|---|---|
| BL-01 | A3 G-01 / A7 | 汇率缓存死链：set_exchange_rate 零调用，定价恒 12.0 兜底且无告警 → 接每日刷新 + pricing marks `exchange_rate_fallback` + Sentry | B2-α | M |
| BL-02 | A9 S9-01 | Ozon 限流器 acquire() 返回值被忽略（饱和 fail-open）+ seller 桶全进程共享非 per-credential → 处理返回值（等待/降级）+ 评估 per-credential 桶 | B2-α | S~M |
| BL-03 | A5 | variant_v2 真值链三段断链：v0.74 宣称能力未生效（skill 定义存在、无调用方传值、消费端不可达）→ **产品决策：接线修通 or 降级标记实验性** | B2-α | M（接线）/ S（标记） |
| BL-04 | A3 G-05 ⬆ | repair_cards.py 硬编码真实店铺 API key 入 git → env 参数化 + 轮换该 key + git 历史清除（filter-repo，Tier A） | B2-α | S（代码）+运维 |
| BL-05 | A5 X-01 | sku_metrics_pool 写侧 SELECT-then-INSERT 非原子 → 并发 IntegrityError 误标 422 + 整批丢失 → 原子 upsert + 归因数组合并 | B2-α | S |
| BL-06 | A8 F6 ⬆ | MXOU key 明文落库（contributed_by_token_id / discovery_runs.tenant_id）→ 改 token_fp 指纹列 + 存量清洗 | B2-α | M |
| BL-07 | A3 G-04 | ozon_sessions 不在 STORE_SCOPED_TABLES → 吊销/硬删店铺后 cookie 密文残留 → 补清单 + revoke 级联 | B2-α | S |
| BL-08 | A3 G-02 | 备份无强制执行点：compose 加 cron sidecar + 备份心跳表 + /health 附 last_backup_at（超 26h 降级） | B2-β | M |
| BL-09 | A3 G-03 | 无迁移框架：schema_migrations 版本表 + migrate_*.py 纳管 + 破坏性变更预检 SQL + CI schema 漂移门禁 | B2-β | M |
| BL-10 | A8 F1 | MXOU 单侧记账零对账：mxou_call_ledger 流水表 + 埋点最终出口 + 双通道对账脚本（方案 DDL 已在 A8 §） | B2-β | M~L |
| BL-11 | A6 | API 示例覆盖 D+：91% 操作无请求/响应示例、77/86 POST 无 requestBody 声明 → gen_api_docs 加 example-lint + 分批补 _examples/openapi_extra | B1 | L（分批） |
| BL-12 | A6 | pounding-mcp 签名漂移 2 处：graph 缺 --category-id/--type-id、discover_task 缺 --expend-shop | B1 | S |
| BL-13 | A6 | session-sync 应封装 MCP 工具（worker 409 后 agent 无自愈通道） | B1 | S |
| BL-14 | A7 | logistics_rates 导入两提交间并发查询落 default fallback → 单事务化；同批评估唯一约束缺失 | B2-α | S |
| BL-15 | A1 P0×5 | 文档误导性事实五处：API-OVERVIEW「17 工具」实 29、AGENTS 158/153 新旧打架、§11 墓碑描述过期等（清单在 A1 §8） | B1 | S |
| BL-16 | A8 | draft_submissions 无 tenant_id + 任务 30 天物理删 = 永久悬空行 → 加列回填 or 归档表方案 | B2-β | M |
| BL-17 | A9 S12-01 | 租户过滤无集中 guard（v0.73 已实证四次复发）→ 评估 query guard/middleware 收敛 | B2-β | M（方案先行） |
| BL-18 | A2 P0×3 | 能力缺口立项（非缺陷）：财务对账 / premium 店铺分析（/v1/premium+/v1/finance 零调用）/ 订单全成本利润 | 产品线 | L×3（各自立项） |
| BL-19 | A4 P0 | 属性层无资产化产物 → warm 增 --export-schema-manifest（支撑类目维度规则地图与 A4 抽样回填） | B2-β | M |

## P2（30+ 条，摘代表性 12 条；全量见各报告）

| ID | 来源 | 标题 | 批次 |
|---|---|---|---|
| BL-20 | A5 | 死代码 11 组处置：error_classifier 整模块、state.py 死模型 8 个（VariantLoopOutput 是活的）、死配置 2、shelf 3 死端点、newapi_proxy 零消费、ozon_seller.py 死编译、TASK_NOT_CANCELLABLE 等 6 零接线码 → 按报告逐条删/接线/文档化 | B1/B2 混合 |
| BL-21 | A3 G-06 | 图片规格校验（分辨率/比例）缺失 → 先 warning 观测 | B2-α |
| BL-22 | A3 G-07 | 缓存过期行永不物理删 → _periodic_task_cleanup 顺带批量 DELETE | B2-α |
| BL-23 | A7 | attribute_cache type_id NULL→0 归一缺失（upsert 裂行隐患） | B2-α |
| BL-24 | A7 | category_cache TTL 10 年 + commission 无 TTL 的治理决策 | B2-β |
| BL-25 | A9 | 缓存三防（穿透/击穿/雪崩）+ PG statement_timeout + 队列 priority 消费 + checkpoint 清理 | B2-β（方案包） |
| BL-26 | A8 F2/F3 | 哈希租户遗迹迁移守卫 + 跨租户绑店拦截非原子（S3 探针实跑后定级） | B2-β |
| BL-27 | A2 | goldminer 三机制移植：结构化错误码/token bucket 精确排程/tab 租约+读写锁（cdp_client 多进程不安全修法） | 产品线/择项 |
| BL-28 | A1 | 文档治理落地主体：README 五层索引 + frontmatter + PLAN 状态行 + 2 项移动 + 归档 1 | B1 |
| BL-29 | A6 | API-OVERVIEW 补「超时重试」「幂等规则」两节 + gen_api_docs 头部计数去重口径 | B1 |
| BL-30 | A4 P1 | 数值 bounds 学习闭环（拒单回填 bounds 表，当前白名单仅 8962）+ is_aspect 兜底过宽 | B2-β |
| BL-31 | A9 | 单实例内存态 8 处清单化 + workers=1 隐式契约显式断言 | B2-β |

## P3（25+ 条）：CSV 幂等键、标题长度契约锚定、archived 生命周期、A2 资产收敛（shopbang md 入 competitor）、DB-SCHEMA-AUDIT 补表、类目树计数口径、webui-archive 引用清理等——随批次顺车。

## B3 规范固化（模块 7/8/11 落地）

- 子 Agent 执行规范文档（模型统一 glm-5.3-flash/超时重试兜底/日志追溯——本轮 9 subagent 实践为底稿）
- WORKFLOW.md 增补「探针先行」闸门模板（探针类型×报告模板×变更流程）
- RESTORE-RUNBOOK（恢复演练步骤 + RTO/RPO 运营化承接 PRD 目标）
- A8 生产探针 S1-S7 实跑（SSH 通道恢复后，需用户侧处理代理劫持/fail2ban）
- 文档同步 CI 门禁扩展（仿 test_compile_lists.py / gen_api_docs --check 先例）

## 建议执行序

**B2-α（止血，1-2 天）→ B1（文档，与 B2 并行可）→ B2-β（基建）→ B3（固化）**；BL-18 能力缺口单独产品立项不占本批次。
