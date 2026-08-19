# Pounding E-commerce Harness · PRD v2.0

> 版本：v2.0（2026-08-19）— **底座改为 DAY1-Clean（Boujoy Harness）**，替代 v1 的「dsh web UI + better-sidebar」平行方案。
> 关联文档：`DAY1-CLEAN-ANATOMY.md`（底座解剖真相源）、`../ozonharness/ARCHITECTURE.md`、`api-integration/`、`design-deliverables/`

---

## 1. 背景与问题

我们已经拥有完整的 Ozon AI 自动化运营栈：
- **worker**（FastAPI，109+ 端点）：上架编排、商品/订单/任务、图片生成、管理后台
- **skill**（pounding-ozon-probe，19 个 CLI）：本地 CDP 采集 1688/Ozon、图搜、选品引擎、上架组装
- **webui + 设计交付包**：15 页 ERP 原型、设计规范书、design-tokens
- **API 对接包**：`api-integration/` + `integration-workplan/`

缺失的是**产品壳**：让用户「跟 agent 对话就把事情做了」的客户端。

**底座选定**：DAY1-Clean（Boujoy Harness）——已验证的 dsh 套壳产品（6 板块 UI + 本地网关 + vault 知识库 + 双 profile + 完整安全基线），源码级解剖完成（见 ANATOMY）。

**MVP 决策（已锁定）**：
1. **设计语言先复用 DAY1-Clean**（暗色霓虹朋克风），换肤推迟到 MVP 后
2. 6 板块骨架全保留，只做「改造 + 新增」，不复刻 UI 层
3. 壳最终 Electron（跨平台），网关/vault/web 不动

---

## 2. 产品定位

**一句话**：电商卖家的一体化 AI 运营台——对话即完成（agent 调度 skill）+ 手动操作（侧边栏板块）。

- **受众**：Ozon 跨境卖家（个人/小团队为主）
- **双向驱动**：同一套 skill 能力，两个入口（对话 / 手动按钮）
- **核心价值**：把「选品 → 采集 → 定价 → 上架 → 复盘」从多系统手工操作，收敛到一个 agent + 9 板块的客户端

---

## 3. 功能范围（MVP）

### 3.1 侧边栏 9 板块

| # | 板块 | 来源 | 内容 |
|---|---|---|---|
| 01 | **AGENT** | 复用 DAY1-Clean | 对话 + 会话 + 审批/提问 + LIVE SIGNAL |
| 02 | **知识库** | 复用 | vault 索引/搜索/捕获（对齐我们的 vault 落盘）|
| 03 | **专家** | 复用 + 改造 | 专家卡 = skill 能力（采集/图搜/选品/上架/类目/配置），纯 prompt 注入 |
| 04 | **风格** | 复用 | 输出风格卡（MVP 保留原样）|
| 05 | **监控** | 复用 | token 用量/轨迹/推理强度（dsh 会话投影）|
| 06 | **新闻** | **改造** | AI 通用新闻 → **电商爆品情报**（热销/热搜/汇率/政策）|
| 07 | **采集箱** | **新增** | worker `/api/v1/drafts` 商品卡片（图片+采购价+运费+利润），筛选/批量导出/转上架 |
| 08 | **任务中心** | **新增** | worker `/task_status` 采集+上架任务，状态/进度/审批 |
| 09 | **计算器** | **新增** | OZON 跨境定价器（worker compute_price 公式前端直算，CNY/RUB/USD）|

### 3.2 能力归属矩阵（开发视角）

| 能力 | 实现层 | 触发 | agent 能调 |
|---|---|---|---|
| 采集（1688 CDP）/ 图搜 / 选品 / 上架组装 | **skill**（本地）| 手动按钮 / 对话 | ✅ MCP |
| 上架执行 / 商品 / 订单 / 任务 / 采集箱 / 图片生成 | **worker REST**（云端）| 前端直调 | ✅ 经 MCP/网关 |
| 定价器 / 汇率换算 | **脚本**（前端直算）| 填表直算，无需 agent | ✅ |
| vault 读写 / 知识卡捕获 | **网关 + dsh**（本地）| 自动 / 对话沉淀 | ✅ |
| 爆品/热搜数据 | **skill**（queries/bestsellers）| 手动 / 对话 | ✅ |
| 用量/轨迹 | **dsh 会话投影** | 前端展示 | — |
| 组合工作流（"找蓝海+导出"）| **agent 驱动** | 对话 | ✅ |

### 3.3 核心用户故事

1. **采集**：粘贴 1688 链接 → agent 调 skill 采集 → 商品卡片进采集箱 → 一键转上架
2. **对话选品**：说"找蓝海家居品" → agent 调 discover → 结果落 vault + 生成 Excel
3. **定价**：计算器板块填成本/重量/毛利 → 秒出售价/划线价/利润明细
4. **任务监控**：任务中心看采集/上架进度；上架任务需老板审批（guard 门控）
5. **复盘**：监控板块看 token/成功率；知识库沉淀踩坑

### 3.4 黑盒透明化（沿用 v1 决策）

- 进度：skill 长任务写 `Active-Context.md`（agent/用户可见）
- 浏览器：CDP 关键节点 + 验证码人工干预提示
- 组装：graph 字段映射落盘成 Markdown 表
- 错误：错误码 + 可操作建议落盘

---

## 4. 技术架构（DAY1-Clean 底座）

### 4.1 三层（保留）

```
浏览器 web/ ── 8766 网关（反代 + 读盘 + WS 桥）── dsh 3080 知识 / 3081 纯净
```

保留：网关 RPC 反代 + WS 双向字节桥、vault 捕获（capture 工具写盘 + 安全校验）、双 profile、CORS/访问码/Origin 安全基线。

### 4.2 改造点

| # | 项 | 内容 | 依赖 |
|---|---|---|---|
| P1 | **profile 挂载** | ✅ 已在底座 rc.6 上挂 pounding-guard + mcp-pounding（patch 层），端到端验证通过（agent→guard→skill→结果）| 无 |
| P2 | **网关扩展** | ✅ `/api/worker/*` 桥已实现（→ worker `/api/v1/*`，Bearer 注入，配置 `pounding-gateway.json`），health/鉴权透传验证通过 | P1 |
| P3 | **+3 板块** | ✅ 采集箱/任务中心/计算器板块已上线（worker 桥数据 + 示例兜底 + 跨境定价器 compute_price），新闻已改电商爆品情报（e-commerce RSS）| P2 |
| P3 | **新闻改造** | 新闻源 → 电商爆品情报 | P1 |
| P4 | **专家对接** | skill 能力卡 → 专家卡 | P1 |
| P5 | **壳** | Swift → Electron（跨平台，含 Windows）| 全 |
| 后 MVP | **换肤** | app.css → 我们的 design-tokens（暖白/黑/红）| 全 |

> **P1 结论（2026-08-19 实测）**：dsh **rc.7 升级不需要**——guard + mcp-pounding 在底座 rc.6 上直接挂载并跑通（headless 事件流：agent→tool/call mcp__pounding__list_stores→guard 放行→skill 返回 5 店铺→中文总结）。PRD v2.0 里原「rc.7 硬关卡」已划除。剩余 P1 待办：vault 布局对齐。

### 4.3 关键技术决策（沿用 ANATOMY 结论）

- 专家/风格 = 纯 prompt 注入（不改 dsh 注册机制）
- 凭证走 `$DSH_HOME/.credentials.yaml`（0600）+ `DEEPSEEK_BASE_URL` 环境变量
- 会话即执行单元（dsh 会话绑定工作区）
- 安全基线不降级（新增电商写接口走同一套 Origin/路径白名单防护）

---

## 5. 非功能需求

- **安全**：沿用访问码/Origin/CORS 白名单/路径逃逸防护/凭证 0600；worker Bearer 不落盘明文
- **性能**：vault 索引签名缓存；新闻 6h 缓存；商品卡片分页
- **数据**：本地优先（vault/会话本地），云端 worker 只做业务执行
- **模型**：可配置（DEEPSEEK_API_KEY + DEEPSEEK_BASE_URL，已实测通过）
- **跨平台**：MVP 先 macOS（底座现状），Electron 壳引入后支持 Windows

---

## 6. 验收标准

### 6.1 板块验收
| 板块 | 验收 |
|---|---|
| 采集箱 | 显示 worker drafts 商品卡片（图/采购价/运费/利润）；筛选 + 导出 + 转上架可用 |
| 任务中心 | 采集/上架任务列表 + 进度；上架触发审批显示 |
| 计算器 | 填入参数出售价₽/划线价/利润，与 worker compute_price 一致 |
| 新闻 | 显示电商爆品/热搜/汇率/政策，刷新可用 |
| 专家 | skill 能力卡可见可派发，prompt 注入生效 |
| 其余 4 板块 | DAY1-Clean 原样可用（agent 对话/知识库/风格/监控）|

### 6.2 端到端验收
1. 对话："把 1688 <链接> 采集下来" → 采集箱出现商品卡片
2. 计算器定价 → 转上架 → 任务中心出现上架任务（审批流）→ 完成
3. 对话选品 → vault 落盘 → 知识库可检索

---

## 7. 执行计划

| 阶段 | 任务 | 产出 | 时长 |
|---|---|---|---|
| **P0** | 底座就绪：clone/fork boujoy-harness + 本地跑通（网关/dsh/UI）| 可运行 base | 0.5-1 天 |
| **P1** | dsh rc.7 升级 + knowledge profile 挂 pounding-guard/mcp-pounding + vault 对齐 | 插件进底座 | 1-2 天 |
| **P2** | 网关扩展：worker/skill 桥（Bearer 注入、drafts/task_status/定价 API）| 网关 API | 1-2 天 |
| **P3** | +3 板块（采集箱/任务中心/计算器）+ 新闻改电商爆品 | 电商板块 | 2-3 天 |
| **P4** | 专家对接 skill 能力卡 + 对话采集/选品端到端 | 端到端闭环 | 1-2 天 |
| **P5** | Electron 壳（跨平台）+ 打包 | 安装包 | 2-3 天 |
| 后 MVP | 换肤 design-tokens + webui 完整页接入 | 品牌统一 | 后续 |

**总工期估：8-13 天（1 人全职）**，skill/worker 零改动。

---

## 8. 风险与对策

| 风险 | 对策 |
|---|---|
| ~~dsh rc.6→rc.7 兼容~~ | ✅ **已消除**：guard+mcp 在 rc.6 实测跑通（2026-08-19）|
| DAY1-Clean 纯 macOS | 上游有 Windows Beta（ps1 启动器，未实机验证）；正式支持靠 Electron 壳（P5）|
| 网关反代 + WS 桥改动影响稳定性 | 改造不动 `_proxy`/`_ws_upgrade` 核心，只加路由 |
| 新增板块破坏原 UI | 板块以独立 section 追加，不改 6 板块渲染 |
| 换肤工作量大 | 明确后 MVP，CSS 变量化后再做 |

---

## 9. 范围外（MVP 不做）

- Windows 原生打包（P5 之后）
- 完整 webui 15 页嵌入（官网承载）
- 多平台（Amazon 等）
- 手机端 PWA 完善
- 换肤与品牌视觉统一
