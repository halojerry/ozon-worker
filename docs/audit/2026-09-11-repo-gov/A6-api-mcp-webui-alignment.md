# A6 — API / MCP / WebUI 能力对齐审计

> 审计基线：`/Volumes/os/dev/ozon-worker-gov` @ v0.74.0（VERSION 四源一致）。审计日期 2026-09-11。
> 方法：纯静态审计（读源码 + openapi.json 快照 + 程序化交叉比对），零 Docker、零生产调用。
> 结论速览：**三层接口面（REST 147 canonical path / 远程 MCP 22 工具 / 本地 MCP 29 工具 / WebUI 17 路由 / harness 白名单 6 条）存在性对齐质量很高——远程 MCP `_call` 22/22 零漂移、WebUI 零真实缺口、harness 白名单与前端调用 100% 吻合**。主要问题集中在「示例覆盖（91% 操作请求例+响应例双无）」「头部计数口径误导」「MCP 工具参数落后 CLI（manual 类目直传/expend-shop/session-sync 对话场景不可达）」三处，均非断裂性缺陷。

---

## §1 API-REFERENCE 计数对账结论

**三个数字全部对上，但「158 paths」是原始 key 口径，对集成方有误导性（P2 文档问题，非 bug）。**

| 数字 | 值 | 口径定义 | 证据 |
|---|---|---|---|
| A. 头部/AGENTS 宣称 | **158** | `app.openapi()["paths"]` 原始 key 数 = 137 个 `/api/v1/*` + 21 个裸路径；其中 11 个裸路径是与 v1 双挂载的**兼容别名**（同一 handler 两路） | `gen_api_docs.py:302` 用 `len(paths)`；openapi.json 实测 158 key |
| B. canonical 去重 path | **147** | 158 − 11（别名对只算一个端点面） | 程序化去重 |
| C. 渲染的 (method,path) 操作 | **183** | 仅在 canonical path 上渲染；裸别名以「> 兼容别名」引用行标注（MD 实测 11 行，与 11 对别名严格吻合） | MD 正文 `### \`METHOD path\`` 标题计数 = 183 |
| D. 全操作（含别名双计） | 194 | 集成方实际可打的 URL 面 | — |
| E. schema 数 | 63 | `components.schemas` | 头部宣称一致 |

- **零漂移实证**：本机复跑 `gen_api_docs.py --check` → 「API 文档与快照一致（158 paths）」（借主仓 `skill/.venv314`，gov 仓无自带 venv）。AGENTS.md L68「158 paths 零漂移」**属实**。
- **口径问题**：头部「158 个 path」是 raw key 数，读者数正文只数得出 147 个 path / 183 个操作，天然对不上。且 **AGENTS.md 内部新旧值打架**：L68（v0.74 块）说 158，L204（v0.72 块）仍写「153 path，勿手改」——后者是历史陈述但紧跟现在时语气，易被当成现值。
- **计数 bug 定位**：无算法 bug；`canonical()`（gen_api_docs.py:166-174）有意跳过裸别名渲染，头部却用未去重的 `len(paths)`。**建议**头部改为 `{len(canon)} 个 path / {ops} 个操作（{len(paths)} 含兼容别名）`，并同步 AGENTS.md L204。

---

## §2 三示例齐全度打分（模块 4.1 核心）

**总评分：D+（183 操作中 167 个 = 91% 请求例+响应例双无；schema 显式示例率 11%）。**
骨架层（参数表/响应状态码表/schema 字段表/错误码叙述）完备，示例层是唯一短板。

### 2.1 全局统计（程序化解析 openapi.json + API-REFERENCE.md）

| 指标 | 值 | 说明 |
|---|---|---|
| 请求示例 | **9 / 183（5%）** | 全部来自 6 处 `openapi_extra`（submit_task、drafts assemble、session、seller-sync、…）+ 3 个 Pydantic body（ProductSourceUpdate/StoreSyncConfigUpdate/QueryImportIn）+ image-tasks×2 |
| **无 requestBody 声明的 POST/PUT/PATCH** | **77 / 86（90%）** | 手读 raw Request 且未补 `openapi_extra` → 文档完全无法体现请求体（P1 级对接障碍，集成方只能读代码） |
| 成功响应示例 | **9 / 183（5%）** | 仅当 200/201 schema 解引用后带 `examples` 才渲染（gen_api_docs.py:245）；7 个带示例 schema 中 6 个被响应引用 → 9 个操作受益 |
| 带 `_examples` 的 schema | **7 / 63（11%）** | AuthVerifyResponse、CredentialOut、DraftAssembleResponse、ErrorBody、SubmitResponse、SubmitTaskResponse、TaskStatusResponse |
| 代码级 `_examples()` 调用 | 13 处 | 其余 6 处（SubmitTaskRequest/AuthVerifyRequest/DraftCreate/DraftSubmitRequest/CredentialCreate/SellerSyncIn）是 openapi_extra 展示用请求 schema，不进 components，示例经 `model_json_schema()` 内联生效 |
| 声明 4xx/5xx 的操作 | 100 / 183（55%） | **90 个操作零错误响应声明**（多为读面，但 analytics/admin 写面也有裸奔） |
| 错误示例 | 间接 100% | `ErrorBody` schema 带示例且在附录渲染；但 4xx 响应只渲染 type 链接，不渲染内联错误示例 |

### 2.2 路由组打分表（43 组全列；操作/请求体/请求例/响应例/错码声明）

| 组 | 操作 | 请求例 | 响应例 | 错码 | 组 | 操作 | 请求例 | 响应例 | 错码 |
|---|---|---|---|---|---|---|---|---|---|
| admin | 47 | 1 | 0 | 25 | mappings | 1 | 0 | 0 | 0 |
| analytics | 11 | 1 | 0 | 1 | mxou | 9 | 0 | 0 | 3 |
| api(newapi代理) | 5 | 0 | 0 | 5 | node_run | 1 | 0 | 0 | 1 |
| async_run | 1 | 0 | 0 | 0 | orders | 12 | 0 | 0 | 9 |
| auth | 1 | 0 | 1 | 0 | products | 10 | 1 | 0 | 5 |
| cancel | 1 | 0 | 0 | 1 | progress | 2 | 0 | 0 | 2 |
| cancel_task | 1 | 0 | 0 | 1 | resubmit_task | 1 | 0 | 1 | 1 |
| categories | 2 | 0 | 0 | 0 | run | 1 | 0 | 0 | 0 |
| commissions | 1 | 0 | 0 | 0 | seo | 2 | 0 | 0 | 0 |
| credentials | 9 | 1 | 2 | 7 | settings | 2 | 0 | 0 | 0 |
| dashboard | 1 | 0 | 0 | 0 | site | 2 | 0 | 0 | 0 |
| discovery | 2 | 0 | 0 | 0 | source-candidates | 1 | 0 | 0 | 0 |
| drafts | 14 | 1 | 3 | 10 | store | 1 | 0 | 0 | 1 |
| error_reports | 2 | 0 | 0 | 0 | stores | 12 | 1 | 0 | 10 |
| estimate | 1 | 0 | 0 | 0 | stream_run | 1 | 0 | 0 | 0 |
| forensics | 1 | 0 | 0 | 1 | submit_task | 1 | 1 | 1 | 1 |
| graph_parameter | 1 | 0 | 0 | 0 | sync-jobs | 1 | 0 | 0 | 1 |
| health | 1 | 0 | 0 | 0 | task | 1 | 0 | 0 | 1 |
| image-tasks | 6 | 2 | 0 | 6 | task_statistics | 1 | 0 | 0 | 0 |
| logistics | 1 | 0 | 0 | 0 | task_status | 1 | 0 | 1 | 1 |
| | | | | | tasks | 5 | 0 | 0 | 4 |
| | | | | | templates | 5 | 0 | 0 | 3 |
| | | | | | v1(chat代理) | 1 | 0 | 0 | 0 |
| **合计** | | **9** | **9** | **100** | | | | | |

**完全无示例重灾区 Top10**（操作数 × 零请求例/零响应例）：admin(47/仅1请求例/0响应例)、orders(12/0/0)、stores(12/1/0)、drafts 次要端点(14/1/3)、image-tasks(6/2/0)、mxou(9/0/0)、tasks(5/0/0)、templates(5/0/0)、analytics(11/1/0)、products(10/1/0)。

### 2.3 API-OVERVIEW 对接规范要素清单

| 要素 | 状态 | 备注 |
|---|---|---|
| Base URL 双环境 | ✓ | §2 |
| 鉴权矩阵（body token / Bearer / 租户口径 / 免鉴权 5 端点） | ✓ | §3，含 v0.73 task_status Bearer 语义 |
| 402 余额判定红绿灯 | ✓ | §3.5 |
| 状态码语义表 | ✓ | §3.6 |
| 限流（300/min、MCP 计 2 次、并发 30） | ✓ | §4 |
| 错误信封双形态 + MCP 信封 | ✓ | §5 |
| 错误码表 14 个 | ✓ | §6 |
| 分页约定（limit/offset + cursor 例外） | ✓ | §7 |
| 任务生命周期 + progress | ✓ | §8 |
| 版本策略 / 变更记录 | ✓ | §9/§10 |
| **客户端超时建议** | ✗ | 无任何端点的建议超时/长轮询边界（submit 异步语义有，timeout 数值无） |
| **重试/退避策略** | ✗ | 429/5xx 该等多久、幂等重试安全区未叙述 |
| **幂等规则专节** | ✗ | `DUPLICATE_SUBMIT 409` 只在错误码表出现一行；何时判重（offer_id? task 去重窗?）、drafts submit 重入语义无叙述 |

### 2.4 「示例强制化」方案（可行）

改动点集中在 `worker/scripts/gen_api_docs.py`，零运行时风险：
1. **example-lint（低成本，建议随下版落地）**：新增 `lint_examples(spec)`（`load_spec()` 之后、渲染之前调用）——遍历 components 收集无 `examples` 的 schema 名单 + 遍历 operations 收集「有 requestBody 却无示例」「200/201 无示例」的操作名单，`main()` 打印 warning 段；`--check` 字节比对不受影响（lint 只打 stderr 不进产物）。可加 `--fail-on-missing-examples` 供渐进收紧。
2. **推广 `openapi_extra` 先例（中成本，高收益）**：77 个无 body 声明的 POST 中，admin/analytics/orders 类 raw Request 路由按 `main.py:2216`（submit_task）与 `drafts_routes.py:289`（assemble）先例逐个补 `openapi_extra`——schema 可复用 `schemas.py` 里已存在但零引用的 DraftCreate/CredentialCreate 等。
3. **响应示例补齐（按重要性排序）**：给 `TaskListResponse/OrderListResponse/DraftOut/HealthResponse/TaskStatisticsResponse` 补 `_examples`——7 个→12 个即可把响应示例覆盖率从 5% 提到约 15%，覆盖全部高频集成面。
4. **CI 门禁**：`ci.sh` Step 5d 在 `--check` 后追加 lint 输出非空则黄标（先警告后阻断，两版过渡）。

---

## §3 Skill 命令 vs MCP 工具覆盖对齐

**结论：本地 MCP 29 工具（20 CLI 封装 + 5 worker REST 直调 + 4 job_*）对 25 个 SKILL.md 命令覆盖 20 个；4 个未封装中 1 个应封装（session-sync）。反向抽查 5 个工具发现 2 处实质性参数漂移。远程 MCP 22 工具 `_call` 回调路径 22/22 命中现路由，仓库红线（改路由必须同步 mcp_server.py）零违规。**

### 3.1 正向映射表（SKILL.md 25 命令 → pounding-mcp）

| SKILL.md 命令 | MCP 工具 | 判定 |
|---|---|---|
| check/list_stores/set_store/set_token/set_ak/get_ak | 同名 6 工具 | ✓ |
| search/probe/image_search/category/follow/discover/discover-multi/discover-task/seller/queries/graph/query | 同名 12 工具（含 background/force 增强） | ✓（参数缺口见 3.3） |
| update/cleanup | 同名 2 工具 | ✓ |
| report | `report_issue` | ✓ 等价路由 |
| migrate_profile | — | **不需要**：一次性升级迁移（独立脚本 `skill/scripts/migrate_profile.py`，本就不是 cli.py 子命令）；但 SKILL.md 命令表把它与普通命令并列、未标注「独立脚本」，`check_doc_sync.py` 也仅靠「只补不删」规避（P3 文档标注） |
| import-cookies | — | **低优/可不封装**：readiness 已自动兜底登录态注入（SKILL.md L85）；对话场景偶发需要时可并入 check 的自愈路径（P3 候选封装） |
| session-sync | — | **应封装（P2）**：SKILL.md 明确「worker 提示 409 session_expired / check 提示会话过期」时 agent 执行——这是对话内自愈场景，dsh agent 只能经 MCP 调工具，无 Bash 通道；现状 agent 撞到 409 只能让用户手跑 CLI |
| batch_test.py | — | **不需要**：独立脚本；无人值守批量已被 discover-task `--auto-submit` + job_* 轮询覆盖 |

### 3.2 反向：MCP 工具数与文档

pounding-mcp 29 工具与 AGENTS.md「29」、docs/MCP-SERVER.md「22 个」（worker 远程面）两处文档计数均与代码一致。`analyze_store/run_store_action/report_issue/list_error_reports/get_task_forensics` 5 个 REST 直调走 `worker_http.py`，不在本审计红线内。

### 3.3 签名漂移抽查（5 个）

| 工具 | 抽查结果 | 严重度 |
|---|---|---|
| `follow` | MCP 6 参数 = CLI 6 flag，逐字对齐 | ✓ |
| `query` | task_id/watch/timeout 对齐 | ✓ |
| `graph` | **MCP 缺 `--category-id`/`--type-id`**（manual 权威类目直传——v0.69 起进 `_is_skill_authoritative` 白名单的唯一入口）与 `--min-density`。对话场景用户指定类目时 agent 无法走权威直传，只能落 search_kw 猜测链 | **P2** |
| `discover_task` | **MCP 缺 `--expend-shop`**（v0.74 拓店新能力，SKILL.md 主推卖点）及 `--filters`/`--filter-profile`/`--base-filter`/`--min-price`/`--max-price`/`--brand-filter`/`--no-match-streak-stop`/`--fx-rate`。核心漏斗参数（target_count/max_scan/to_box/auto_submit/dry_run/resume/export）已齐 | **P2**（expend-shop）/ P3（筛选族） |
| `discover` | MCP 缺 `--note`（采集箱备注，未发版批 A）、`--min-price/--max-price/--brand-filter`、`--filter-profile`、`--no-analytics` 等；docstring 已写「更多参数见 --help」属**有意的子集封装** | P3 |
| `queries` | MCP 缺 `--export/--output`（工具响应已回数据，落盘需求弱） | P3 |

> 模式总结：pounding-mcp 的参数表是**快照式手工映射**（server.py 自述「参数 1:1 映射 CLI：下划线转连字符」），CLI 新增 flag 不会自动进 MCP。`check_doc_sync.py` 只校验「SKILL.md ↔ cli.py 命令名」，**无「MCP 参数 ↔ CLI flag」校验**——建议给 pounding-mcp 加同款 diff 校验（P3）。

### 3.4 worker 远程 MCP（mcp_server.py 红线核查）

`TOOLS` 22 个工具的 `_call` 路径程序化比对 openapi.json：**22/22 全部命中**（含 `/task_status/{id}`、`/cancel_task/{id}`、`/task_statistics` 三个走裸别名路径与 `/api/v1/logistics/quote` 等规范路径）。145→158 path 扩容未造成任何 MCP 回调漂移。工具表与 docs/MCP-SERVER.md 22 行一致。

---

## §4 WebUI 功能 → 后端映射（静态审计）

**结论：零真实缺口。** 归一化后 webui 引用 142 个端点面，全部存在于 openapi；反向未暴露的 15 条均为有意的 agent/skill/内部渠道。调用形态：60 GET / 30 POST / 6 PATCH / 5 PUT / 5 DELETE + 3 处原生 fetch（client.ts 请求/下载封装 + drafts/import CSV 上传）。

- 路由 17 条（app.tsx:145）：`/`(Dashboard)、`data`、`products`、`orders`、`collection`、`pricing`、`studio`、`tasks`、`bestsellers`、`stores`、`templates`、`settings`、`admin`、`keys`、`discovery`、`site`、`/login` + `*` NotFound → 16 个 Panel 组件（src/components/）。
- **清单一（前端有入口后端无对应）：空。** 表面 MISS 经归一化排查全为误报：`/api/v1` 常量、LangGraph 裸调试路径（`/run`、`/node_run/{x}` 等——它们是独立裸路径而非 /api/v1 子路径）、newapi 代理 `/api/v1/chat/completions`（实际命中 `/api/{x}` 通配路由）、grep 噪音。
- **清单二（后端有能力前端无入口）：15 条，判定全部「有意不暴露」**——
  - agent/skill 专属渠道 4 条：`analytics/seller-sync`（skill 上报）、`analytics/sku-metrics`（消费方=skill `cloud_probe/ozon_discovery/metrics_pool_client`）、`analytics/what-to-sell` 与 `credentials/{id}/session`（未发版会话代管批 C，skill CDP 收割上传）；
  - LangGraph 内部调试面 8 条（async_run/run/node_run/graph_parameter/stream_run/task/cancel/progress）；
  - newapi 代理 2 条（`/api/{x}`、`/v1/chat/completions`）。
  - 曾嫌疑的 `drafts/{id}/assemble` **已被消费**：EditDraftDrawer「AI 预组装」按钮（CollectionPanel.tsx:309 `assembleDraft`）。
- **清单三（参数/返回不匹配疑点）：未发现结构性疑点**（类型经 `generated.d.ts` 迁移，openapi 快照同步）。唯一观察项：`webui/src/api/client.ts` 401 处理只认 HTTP 状态码，若后端改为 200+错误信封形态需同步（现状无此形态，不构成缺陷）。

### 四个关键交互链抽查

| 交互 | 调用链 | 错误兜底 |
|---|---|---|
| **login / auth:expired** | `verify()`（includeToken=false）→ 会话存储；每次请求带 Bearer；`request()` 捕 401 → `clearSession()` + `auth:expired` 事件（client.ts:39-41）→ App.tsx:56 监听跳登录；另以 `/mxou/me` 刷新角色/邮箱权威化（App.tsx:59-74），401 同样清会话 | **完整**，错误信息统一 `apiErrorMessage` |
| **采集箱 EditDraftDrawer** | `categories/search?q=` 类目搜索（L187）→ 选中写 manual source；`categories/attributes?dc=&tp=` schema 懒加载（L139）；字典属性 `?attr_id=` 按需分页（L170）；`assembleDraft`（L309）assembleNotice/assembleError 双态提示；提交 `POST /drafts/{id}/submit`（L362）**前置 credentialId 校验**（缺失直接人话报错不发请求）；CSV 导入走原生 fetch 带 Bearer + text/csv（L643） | **完整**（try/catch + inline error；无静默失败） |
| **任务提交/取消** | TasksPanel `GET /tasks` 列表 → `/task_status/{id}` 轮询 + `/progress/{id}/stream` SSE（失败回退轮询，L108-114 双通道）→ images/regen、cancel_task、resubmit | 基本完整；progress 事件表缺失**有意静默**（注释「事件表未迁移/无事件 → 静默」，合理降级） |
| **店铺同步** | StoresPanel `POST /stores/{id}/sync` → 轮询 `/sync-jobs?limit=5` → `/sync-status` + `/stats` 刷新（L173-177）；validate 按钮 `POST /credentials/{id}/validate` catch → setValidateMsg（L164-166） | 完整；`sync-status` 读取失败静默不阻断卡片（L143，注释明示有意） |

---

## §5 harness 网关白名单对齐（/Volumes/os/dev/pounding-harness/web/boujoy_server.py）

**结论：白名单与前端实际调用 100% 吻合，无 403 风险；前端零 PATCH/PUT/DELETE 调用。**

| 方法 | 网关白名单（正则 fullmatch） | 前端 app.js 实际调用（`we()` 封装，6 处） | worker 端点存在 | 判定 |
|---|---|---|---|---|
| GET | `drafts` | `drafts` | ✓（147 面内） | 吻合 |
| GET | `tasks` | `tasks` | ✓ | 吻合 |
| GET | `drafts/[^/]+` | （前端未用单草稿读取，预留） | ✓ | 白名单略宽于前端，无害 |
| GET | `dashboard/overview` | `dashboard/overview?days=N`（3-90 clamp） | ✓ | 吻合 |
| GET | `credentials` | `credentials` | ✓ | 吻合 |
| GET | `products/ozon` | `products/ozon?credential_id=` | ✓ | 吻合 |
| POST | `drafts/[^/]+/submit` | `drafts/{id}/submit`（唯一 UI 写路由，`_inject_mxou_token` 注入 body.token） | ✓ | 吻合 |

- 查询串不参与正则（`re.fullmatch` 只对 path 段），`?days=`/`?credential_id=` 均透传，无误伤。
- harness 本地端点（`/api/collect/*`、`/api/mcp/config|test`、`/api/knowledge/*`）不走 worker 白名单，与 worker 能力面解耦正确。
- 前端 `app.js` 全文 PATCH/PUT/DELETE 调用数 = **0**——「写操作收口 agent → MCP」（PLAN-harness-mcp-adoption-v1 §3）在前端侧成立。
- 风险提示（已知项，非新发现）：`_read_mxou_token` 从 skill settings.json 读明文 token 注入 submit（AGENTS.md 已列「明文代管链退役」待办）；白名单**不含** `drafts/{id}/resubmit`，UI 重提场景（若有）会被网关 404，现状 UI 无此按钮，属有意收口。

---

## §6 汇总（P0-P3）

> P0（断裂/资损）：**无**。P1（对接障碍）：2。P2（能力不可达/口径）：5。P3（打磨）：6。

| # | 级别 | 发现 | 证据 | 影响 | 建议 | 探针（复核命令/位置） |
|---|---|---|---|---|---|---|
| 1 | **P1** | 77/86 POST 无 requestBody 声明 → 文档无请求示例，集成方只能读代码 | openapi.json 程序化统计；§2.1 | 第三方对接成本高、 Swagger /docs 同样缺失 | 按 submit_task/assemble 先例分批补 `openapi_extra`，优先 admin 写面与 orders 操作面 | `python3 -c "…统计 paths 中无 requestBody 的 post"` 或 docs/API-REFERENCE.md 查「请求体」段缺失 |
| 2 | **P1** | 示例覆盖 91% 双无：schema `_examples` 率 11%，响应示例 9/183 | §2.1/§2.2 表 | 文档可读性差；LLM/agent 按文档组装请求易错 | 落地 §2.4 example-lint + 给 5 个高频响应 schema 补 `_examples`（TaskList/OrderList/DraftOut/Health/TaskStatistics） | `gen_api_docs.py` 加 `lint_examples(spec)`；grep `_examples` worker/src/api/schemas.py |
| 3 | **P2** | 头部计数口径误导：158（raw）vs 正文 147 canonical/183 操作；AGENTS L68=158 与 L204=153 并存 | §1 对账表 | 集成方对不上数；内部文档互斥 | 头部改双口径（canonical+操作数+别名数）；AGENTS L204 标注「v0.72 时点值」 | `grep -cE '^### \`(GET|POST…' docs/API-REFERENCE.md` = 183 |
| 4 | **P2** | MCP `graph` 缺 `--category-id/--type-id`：manual 权威类目直传在对话场景不可达 | server.py graph 签名 vs cli.py:3003-3029 | agent 只能走猜测链，放弃 v0.69 权威白名单收益 | graph 工具补 category_id/type_id/min_density 三参 | 对比 `pounding-mcp/pounding_mcp/server.py:211` 与 `skill/scripts/cli.py:3003` |
| 5 | **P2** | MCP `discover_task` 缺 `--expend-shop`（v0.74 拓店卖点）及筛选族 flag | server.py:161 vs cli.py:3158-3238（27 flag 中 MCP 暴露 13） | 拓店功能 agent 不可达；SKILL.md 宣传与工具面脱节 | 补 expend_shop；筛选族按需补 | server.py:161-185 |
| 6 | **P2** | `session-sync` 无 MCP 封装：worker 409 session_expired 后 agent 无自愈通道 | SKILL.md L90；server.py 无对应工具 | 对话内会话过期必须人工跑 CLI | 封装 session-sync（脱敏语义已有，参数仅 credential-id） | `grep session-sync pounding-mcp/pounding_mcp/server.py` 零命中 |
| 7 | **P2** | API-OVERVIEW 缺「超时/重试/退避」与「幂等规则」两节 | §2.3 要素表 | 集成方重试策略无依据，可能对 submit 盲重 | §4 后补两节：建议超时值、429/5xx 退避、DUPLICATE_SUBMIT 判重语义 | docs/API-OVERVIEW.md 目录 |
| 8 | P3 | MCP discover/queries 参数子集缺口（--note/--export/--filter-profile 等） | §3.3 | 对话场景功能面缩水；--note 使备注功能 agent 不可用 | 按对话需求补 --note、--export；其余维持子集+docstring 指引 | server.py:126-159 |
| 9 | P3 | 90/183 操作零 4xx/5xx 响应声明 | §2.1 | 错误处理不可发现 | 装饰器统一补 `responses={401: ErrorBody, ...}`（admin 面优先） | openapi.json 按 op 统计 |
| 10 | P3 | `migrate_profile` 在 SKILL.md 命令表与普通命令并列，实为独立脚本 | cli.py 零 subparser 命中；skill/scripts/migrate_profile.py | agent/用户误按 `cli.py migrate_profile` 调用 | 表格标注「独立脚本 scripts/migrate_profile.py」 | `grep add_parser skill/scripts/cli.py \| grep migrate` 零命中 |
| 11 | P3 | pounding-mcp 无「MCP 参数 ↔ CLI flag」自动校验（check_doc_sync 只查命令名） | check_doc_sync.py 头注 | CLI 新 flag 静默落后于 MCP（#4/#5 根因） | 给 pounding-mcp 加参数级 diff 测试 | `grep extract_doc_commands skill/scripts/check_doc_sync.py` |
| 12 | P3 | harness `_read_mxou_token` 明文代管链仍在用（已知待办） | boujoy_server.py:1189 | 本地明文 token 暴露面 | 按 PLAN-harness-mcp-adoption-v1 §7 退役 | boujoy_server.py `_read_mxou_token` 调用点 |

### 复核探针速查（全部零凭证、只读）

```bash
# §1/§2 计数与零漂移
cd /Volumes/os/dev/ozon-worker-gov && python3 worker/scripts/gen_api_docs.py --check   # 需 fastapi 环境（skill/.venv314）
grep -cE '^### `(GET|POST|PUT|PATCH|DELETE) ' docs/API-REFERENCE.md                    # = 183
# §2 示例
grep -c '^```json' docs/API-REFERENCE.md; grep -c '响应示例：' docs/API-REFERENCE.md     # = 25 / 9
# §3 红线核查（22 路径比对）
grep -n '_call(' worker/src/mcp_server.py   # 逐条对 docs/API-REFERENCE.md
# §5 白名单
grep -n 'drafts|tasks|drafts' /Volumes/os/dev/pounding-harness/web/boujoy_server.py | head
```
