# AGENTS.md — ozon-worker 工作区

本文件是工作区级导航与不变量速查。**治理纪律（2026-10 W0 起）**：版本历史唯一载体是根 `CHANGELOG.md`——本文件**禁止再堆积「最近更新」版本块**；不变量的权威载体是代码注释与测试，本文件只留一行指针，散文与代码冲突时以代码为准。

## ⚡ 60 秒上手（新会话先读这节）

**是什么**：两段式 Ozon 上架系统。`skill/`（客户本地，CDP 抓 1688/Ozon → 组装 GraphInput 信封，**不上架**）→
`worker/`（云端 Docker，FastAPI + LangGraph：类目→定价→属性→生图→校验→上传→自学习）。周边：`pounding-mcp/`
（dsh agent 的 30 个 MCP 工具——21 CLI 封装（含 session-sync）+ 5 worker HTTP 直调 + 4 job_* 后台监控，薄封装）、`webui/`（React，**bun** 生态，产物随 worker 镜像多阶段构建内建、FastAPI 同进程伺服 `/app`）、
`pounding-sidebar/`（dsh 插件）、`docs/refs/ozon-mcp/`（Ozon API 参考库，只读）。pounding-harness 是独立仓库，只做消费方。

**命令（均已实测）**
| 目的 | 命令 |
|---|---|
| worker 全量测试（需本地 PG 5433） | `cd worker && PGDATABASE_URL="postgresql://postgres:ozon123@localhost:5433/ozon" PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/ -q` |
| worker 单文件（纯 mock） | `cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/<file>.py -q` |
| skill 测试 | `cd skill && .venv314/bin/python -m pytest tests/ -q` |
| pounding-mcp 测试（须自身 venv） | `cd pounding-mcp && .venv/bin/python -m pytest tests/ -q` |
| webui 类型检查 + 构建 | `cd webui && bun install && bunx tsc -b && bun run build` |
| lint | worker `ruff check src/ --select E,F,W --ignore E501`；skill `ruff check scripts/ --select E,F,W --ignore E501,E402`。⚠️ 本表只是本地快速自查口径；**CI 门禁实为 worker 裸 `ruff check src/`（走 `worker/ruff.toml` 全规则集，含 TRY 组）与 skill `ruff check scripts/ --select F`**——`--select E,F,W` 测不出 TRY 组等回归（2026-09-16 实录：TRY401×4 差点带红 CI），提交前以 CI 口径为准 |
| 本地 CI 全流程 | `bash scripts/ci.sh --quick`（跳 Docker；Step 5d 校验 API 文档漂移） |
| **改 API 后必跑** | `python worker/scripts/gen_api_docs.py`（重生成 `docs/API-REFERENCE.md` + openapi 快照；`--check` 即 CI 门禁） |
| 本地 worker | `cd deploy && docker compose up -d --build` → `http://localhost:8080`（Swagger `/docs`） |

**测试基线（v0.83.0 终验）**：worker **3764 passed / 2 skipped** · skill **1828 passed** · pounding-mcp **144 passed**。

**边界（改代码前的硬规则）**
- skill 不调任何 Ozon 上架 API；worker 不抓 1688。信封契约 `docs/CONTRACT-v4.md`；**键集合唯一权威 = `worker/src/utils/envelope_contract.py`**（EnvelopeExtensions extra="forbid"；2026-10 W2 起「改字段三处同步」废止）——改键 = 改模型字段+来源表 → 跑 `worker/scripts/gen_contract_docs.py`（CI `--check` 漂移即红；未知键提交层/ingest fail-closed，`ENVELOPE_STRICT=0` 降级 warn）。
- 唯一入口不得内联复制：定价 `utils/pricing_estimate.compute_price`、标题公式 `utils/title_formula`、佣金 `utils/commission_resolver`、错误码 `api/errors.py`（数量以文件为准）；鉴权族（token 校验/余额/Bearer 守卫）`api/security.py`、任务进度/当前任务上下文/优雅关闭 `runtime/progress.py`、API 限流 `runtime/rate_limit.py`、图执行 `runtime/graph_service.py`（W3b 归位；main 保留 re-export 兼容面，新代码直接 from 新模块）；task_processor 单例经 `orchestrator.task_processor.get_task_processor`、async_graph 经 `runtime.graph_service` holder（lifespan 注入，**低层禁 `from main import`**）。
- **main.py 是 181 行 composition root（2026-10 W3c 拆解终态，勿再长回去）**：HTTP 端点按族住 `routes/`（task 队列 `task_queue_routes` / analytics 上报读取 `analytics_ingest_routes` / 类目属性佣金 `catalog_routes` / 健康鉴权进度物流 `ops_routes`）；清扫器与周期任务 `runtime/maintenance.py`；启动校验/僵尸恢复 `runtime/startup_checks.py`；lifespan 本体 `runtime/lifespan.py`；app 构造/MCP 挂载/路由注册顺序 `app_factory.py`（⚠️ 路由注册顺序契约：newapi catch-all `/api/*` 必须最后；`python -m src.main -m http` 是 Dockerfile 启动契约）。新端点进对应族文件，禁止写回 main。
- `worker/src/mcp_server.py` 零业务逻辑，工具只回调本进程 REST——**改路由路径必须同步其 `_call`**。
- langgraph 按节点 Input model 过滤 state：**节点/路由要读的字段必须声明进该节点 Input**，否则静默拿不到。
- 类目链、余额判定、重量/尺寸、图片 URL 链路各有「改前必读」注释块（见下方「不变量速查表」），勿凭记忆改。
- **God file 冻结增长（2026-10 W0 起）**：`skill/scripts/cli.py`、`skill/scripts/cloud_probe.py`、worker 的 `assemble_ozon_product_node.py`/`validation_retry_loop.py` **只减不增**——新逻辑进 lib/新域文件/新模块，禁止续写（main.py 已拆解至 181 行，见上条）。

**纪律**
- 功能测试只打本地 Docker，**禁止用生产 `worker.mxou.cn`**；本地 Supabase 未配置 = auth fail-open，验证鉴权用空 token。**v0.75 起有技术闸**：生产库由 deploy/cos-update 写入 `prod_marker` 哨兵，worker 测试 conftest（`scripts/prod_db_guard.py`）探测到即拒跑 exit 2；生产 PG 宿主直连端口是 **15433**（不是 5433——5433 是本地开发惯例端口，撞车曾致测试套件连产 18h，见 `docs/audit/2026-09-11-io-avalanche.md`）。
- **依赖方向立法（W3a，`worker/tests/test_import_direction.py` 硬闸）**：utils↛graphs/api/routes/mcp_server/orchestrator；services↛graphs/routes/mcp_server/main；graphs↛routes/main/mcp_server；`import main` 消费方文件集冻结只减不增（R2 棘轮/R3 冻结，新增消费方即红）。
- **单测默认断网（W3b，`worker/tests/conftest.py` 守卫）**：只许 loopback（本地 PG/本地 Docker 不受影响，`TEST_NET_ALLOWLIST` 可加白）；确需真外呼标 `@pytest.mark.external_network` 且 `RUN_EXTERNAL_TESTS=1` 才运行——套件正确性不得依赖机器网络状态。
- Commit `<type>(<scope>): 中文描述`；工作树常有其他会话的 WIP，**逐文件 `git add`，不用 `-a`/stash**；动手改文件前先看 `git status` + 相关文件 mtime——多会话并行实施同一方案时会撞车（2026-09-09 实录：策略模块被两会话重复实现）。
- **多会话协作（规范 `docs/WORKFLOW.md`）**：非平凡任务**一会话一分支一 worktree**（开工即 `git worktree add ../ozon-worker-<topic> -b <type>/<topic> origin/dev`，主 worktree 只做 Tier B 小改/发版/review）；分支拓扑 main=发布线（**tag 只打 main**）/dev=集成线/`<type>/<topic>`=工作流分支（合后即删）；两级门槛——Tier A（跨子系统/新 API/新表/发版/>3 文件）必须分支+PR（CI 绿才合，self-merge 合法，merge commit 保留流边界），Tier B（≤3 文件 docs/单点 fix）直提 dev 但**当日 push**。
- 发版：VERSION 四源一致 + `CHANGELOG.md` 顶部新块 + 实机 ≥3 单 gate（本文已无版本块，发版不再改 AGENTS.md，除非行为变更影响 agent 操作需同步活文档节）；发版动作 = dev→main PR 合入后**在 main 上打 tag**。**⚠️ VERSION bump 后必跑 `gen_api_docs.py` 重生成并提交**——API-REFERENCE.md 头部嵌版本号，漏跑 = CI 漂移闸红 + cd.yml tag CI 闸拦截。
- 写 Ozon API 调用前先用本机 MCP `mcp__ozon__search_methods`/`describe_method` 核对契约（零凭证只读），禁手 grep swagger。
- `worker/config/*.json` bind mount 热加载，改 prompt 无需重建镜像。
- **Mimosa 安全插件共处纪律（所有会话必读）**：
  ① **DB 代码唯一放行形态 = 静态 SQL + `%s` 占位 + 参数元组经 `exec_driver_sql`**——SQLAlchemy `text()` 命名绑定、ORM `select().where()`/`pg_insert()` 会被其 SQL 规则误报拦截（`# nosemgrep` 无效、加密规则不可改；`category_doc_gate.py`/`declined_disposition.py` 是放行形态范本）。
  ② **生成类步骤绝不与 `git commit` 同一条 Bash 命令**——commit 被 git-gate 拦时整条命令蒸发（gen 单独跑→验证 diff→单独提交）。
  ③ **经 GitHub API（`gh api git/...`）创建的 commit 不触发 push 事件**——**提交一律本地 `git push` 或 PR 合并**。
  ④ 插件加密不可改（hooks/rules AES），可调面只有：根 `.gitignore`（其枚举器读它——命名 venv 与 `.mimosa/` 必须显式收口，否则枚举爆 5000 上限 → coverage partial → `ledger validate` 拒跑）、`mimosa policy init`、`mimosa ledger/validate/backlog`（finding 官方平反通道，前置=coverage complete）。

**先读什么**：集成/端点 → `docs/API-OVERVIEW.md` + `docs/API-REFERENCE.md`；节点流/错误映射 → `docs/ARCHITECTURE/02-worker-graph.md`；
MCP 面 → `docs/MCP-SERVER.md`；操作 skill → `skill/SKILL.md`（agent 硬约束见下方）；建表/改列 → `docs/DB-SCHEMA-AUDIT.md`；
部署 → `docs/DEPLOY.md`；多会话协作/分支拓扑/发版流 → `docs/WORKFLOW.md`；子 Agent 规范 → `docs/SUBAGENT-SPEC.md`；
恢复演练 → `docs/RESTORE-RUNBOOK.md`；架构全景/函数级细节/已知问题清单 → `docs/ARCHITECTURE/`（v0.80 基线 + 09-findings）。

**高频坑**：编译 skill 必须 Python 3.12（ABI）；worker 测试全家桶在 `skill/.venv314`（系统 python 无 pytest）；本地 PG 类目树为空会让类目类测试失败（先 `init_data` 导入）；MXOU 字面 `balance:0` 是哨兵不是欠费；产品图托管在 COS bucket，生命周期规则一删 Ozon 卡片全变无图；`test_webui_e2e` 提交用例在无 boto3 环境被图片镜像闸 422（已知隔离问题）；worker 全量测试须显式 `PGDATABASE_URL=postgresql://postgres:ozon123@localhost:5433/ozon`（漏掉会落 `postgres:5432` 容器主机名→30 分钟假阴性；且 5433 可能被非 compose 的临时 PG 占位——连错库测试照样绿，跑前 `lsof -iTCP:5433 -sTCP:LISTEN` 核实）；PG 集成测试的 skip 守卫勿读 env 判存（`import main` 会向 environ 注入容器风格 URL），用直连探测。⚠️ conftest 的生产库写闸（PR#20 prod_db_guard）只对 pytest 生效——直接 `python tests/xxx.py` 跑集成脚本不经过闸，涉库操作仍靠人工纪律。⚠️ **2026-09-16 安全批两坑**：①`SKIP_FAILED_REVIVE` 语义已翻转——部署重启默认**不**复活 failed 任务（重试走采集箱 resubmit；恢复旧行为显式 `SKIP_FAILED_REVIVE=0`）；②鉴权矩阵已收口——cancel_task/task_statistics/progress/store/health/logistics-quote 无 Bearer 一律 401（statistics 非 admin 恒自身租户、store/health 上游失败 502、logistics/quote 有限流），写集成测试/客户端联调时别按「匿名可读」旧口径来。

## 不变量速查表（改对应链路前：先看这行 + 权威位置；权威在代码注释与测试，不在这里）

| 链路 | 不变量 | 权威位置 |
|---|---|---|
| 定价 | 唯一入口 `compute_price`；三档 price/old_price(≥日常×1.2)/promo_price；利润口径=销售净利率 `profit/price` | `utils/pricing_core.py` + `pricing_estimate.py` |
| 佣金 | 唯一入口 `commission_resolver`（explicit > 缓存表 > segments > 0.10）；provisional band pass 破「档位↔价格」鸡生蛋 | `utils/commission_resolver.py` |
| 标题 | 公式唯一入口；9048 防并卡前缀只用确定性字段（item/supplier/中文标题，**绝不用 LLM 翻译标题**），跟卖 UPDATE 刻意不加 | `utils/title_formula.py` + prepare `_derive_model_name_9048` |
| state 通道 | 节点/路由要读的字段必须声明进 Input；失败出口的 error_code 不进 Output 声明即被静默吞 | `graphs/state.py` 注释（W1 起 CI 检测器） |
| 类目 | 信任序：Skill 权威 > L0（succ≥2 权威）> 文本猜测；R1 敏感子树 veto 覆盖所有层；search_kw 恒非权威 | assemble `_skill_precedence_over_l0`/`_l0_authoritative`/`_r1_veto` |
| 类目 | 低置信/歧义 → LLM 仲裁或阻断入箱，绝不静默 completed；权威命中恢复 match_confidence=0.95；divergent 阶梯看纯函数 | assemble `_divergent_match_verdict` + `utils/blocked_draft_box` |
| 属性 | 匹配唯一入口 `attr_value_matcher`；多候选绝不盲补首值；LLM 消歧三件套（-1 出口/候选索引重查证/abstain） | `utils/attr_value_matcher.py` |
| 属性 | 值数出口闸 `cap_attribute_values`（8229 契约恒 1，cap 取 schema `max_value_count`）——**任何往 Ozon 发 values 的路径必须过闸** | `utils/attr_value_sanitize.py` |
| 属性 | 数值 bounds：静态白名单 > 学习表 > 不夹取；假事实兜底唯一出口 `FACT_NEUTRAL_FREE_TEXT_DEFAULTS`（保质期/储存不编造） | `utils/attr_numeric_sanitize.py` + content_enrich 家族 |
| 图片 | 图来源唯一入口 `image_source`；非 AI 图恒不上卡（`_enforce_payload_image_policy`）；E1 原图兜底默认停用；跟卖竞品图只作生图参考**≠上卡** | `utils/image_source.py` + `image_url_guard.py` |
| 图片 | 收尾卡片图断言（数量 + AI 首图 3:4）mismatch → `CARD_IMAGE_MISMATCH` 拒假成功；主图拒单走 `regen_main_image` 统一过 `_reupload_gate_blocked` | `utils/card_image_assert.py` + upload/status 节点 |
| 重量 | (0,10)g 视同缺失走兜底（不 ×1000）；箱级毛重 reconcile 唯一入口；密度守卫 0.40 g/cm³ **只兜底不拒绝** | normalizer v0.68.1 注释 + `utils/volume_weight_guard.py` |
| 余额 | 真欠费 = MXOU 实查**负数**无哨兵；字面 `balance:0` 是哨兵不是欠费；绝不用 `tokens.remain_quota`（僵尸字段） | `utils/mxou_api.py` `get_mxou_balance` |
| 终态 | completed 必须过 `_has_real_product_evidence`；16 个 failed 出口全带 LOCAL_* 错误码 | `utils/task_processor.py` |
| 店铺同步 | timestamptz 窗口唯一出口 `_fmt_utc`/`_as_utc`（禁裸 strftime）；`/v1/actions` GET-only；`localization_index` 是数组；`_sync_products` 失败**绝不** `_archive_missing` | `services/store_sync_service.py` |
| 租户 | 租户面读写唯一入口 `resolve_tenant` / `api/deps_tenant`（勿回 `_key_user_id`——生产 tenant 是 Supabase user_id） | `api/deps_tenant.py`（phase3 测试锁定） |
| 上传 | 新增 import POST 出口必须接 `preserve_existing_card_attributes`（/v3 全量替换语义，防洗卡）；CREATE 零图硬闸 `LOCAL_IMAGES_MISSING` | prepare/upload 节点注释 |
| retry | 错误消费前先 `_accumulate_decline_errors`；`box_reviewed`（采集箱草稿）禁自主改写 | `graphs/validation_retry_loop.py` 头注释 + `test_box_reviewed_authority_v070` |
| 学习 | approve 是唯一学习成功信号；写 mapping 前过 `_leaf_path_overlap` 语义预检；L0 且 dc 未变 skip upsert | `graphs/nodes/learning_record_node.py` |
| 锚价 | 恒 materialize，绝不可引用化/延迟解析；价差守卫只在 discovery_meta 有锚时生效 | `utils/price_sanity_guard.py` 模块注释 |
| 划线价 | old_price 唯一规则 `enforce_old_price_rule`（<400 差价≥20） | `utils/`（v0.81 收口批） |
| 字典缓存 | 三防（负缓存 60s + TTL 抖动 + 单飞锁 `get_or_fetch` 读穿）；Ozon 拉取失败 raise 专属异常**绝不落负缓存**；三桶策略（global/scoped/ephemeral） | `utils/dict_value_cache.py` 模块注释 |
| SQL | SQLAlchemy `text()` 不识别 `:bind::type` 裸 cast → 显式 `CAST(:bind AS 列型)`；裸 SQL 绑 JSONB 列必须 `json.dumps`（list 会被适配成 text[]） | 记忆 `sqlalchemy-jsonb-cast-trap` |
| 出站 | worker 任何出站抓图/外链 fetch 必须过 `safe_fetch`（调用方宽 except 兜底，fail-closed） | `utils/secure_fetch.py` |
| 安全卫生 | CSV 导出公式中和（worker/webui 同口径）；ILIKE 用户输入一律 `escape_like` | v0.79 安全批 |
| 信封 | `notes` 是运营态，绝不进 payload/extensions；S1 竞品数据用扁平键（勿嵌套 `extensions.competitor.*`） | `docs/CONTRACT-v4.md` |
| skill CDP | 统一 `cdp_client`（禁裸 websocket）；`find_tab` 命中用户 tab 必须 `release`；`new_tab` 默认后台（前台须显式 `background=False`，后台渲染靠 `force_active`） | `lib/cdp_client.py` + `chrome_launcher` 注释 |
| skill CLI | 新命令出口必须带 `👉 NEXT:` 行；提交确认二分法（明确意图→直提，弱意图→展示） | cli.py `_print_next` + `test_next_hints_v079` |
| skill 缓存/锁 | 缓存统一 `cache.py`（namespace+TTL+SHA256），key 必须含语言/ID 维度；版本指纹读 `skill/VERSION` 文件（禁止源码版本常量）；锁唯一实现 `lock_utils.py` | `lib/cache.py` + `lib/lock_utils.py` |
| 删除 | Windows 沙箱删除一律 `safe_unlink`/`safe_rmtree`（fail-open） | `lib/utils.py` |

## 更新联动规则（改 X 必须同步检查 Y）

| 改了什么 | 必须同步检查 | 不用动 |
|---|---|---|
| skill CLI **命令签名/参数** | pounding-mcp `server.py` 参数映射 + `router.py` 意图词表 + SKILL.md 命令表（CI `check_doc_sync` 闸） | worker / webui |
| skill **信封字段结构**（GraphInput 增减字段） | worker `state.py` + `docs/CONTRACT-v4.md` + webui 草稿展示 | pounding-mcp（只传不解析） |
| worker **新增/修改 API 端点或 schema** | **跑 `worker/scripts/gen_api_docs.py`**（CI 漂移即红）+ `docs/API-OVERVIEW.md` 变更记录 + webui 接入 | skill / pounding-mcp |
| worker **定价/标题公式** | `compute_price`/`title_formula` 唯一入口已锁定，面板展示三档价 | skill |
| skill **内部抓取/图搜逻辑** | 什么都不用改（工具签名/产出结构不变） | 全部 |
| **版本发版**（VERSION 四源） | CHANGELOG 顶部新块 | — |
| **契约变更**（CONTRACT-v4） | skill/worker/pounding-mcp 三处同步 + 客户端面板 | — |

## ⚠️ Agent 使用 Skill 时的硬约束

当用户请求涉及 Skill 子项目（1688 抓取、Ozon 跟卖、选品、上架）时：

1. **先读 `skill/SKILL.md`**，不要凭记忆或自己探索项目结构操作
2. **只用 SKILL.md 中的命令**，不要自己写 Python 代码、不要用 requests/urllib 抓取
3. **严格按意图路由选择管线**，不要混用蓝海逻辑和跟卖逻辑
4. **不要修改 Skill 的 Python 代码**，除非用户明确要求改代码
5. **趋势/蓝海选品必须先 web_search**：命令层无 `trend`。流程 = agent 先搜索「{品类} Ozon 热门趋势 蓝海 细分品类」+ LLM 提炼细分关键词 → 再 `discover --keyword <关键词>`；禁止跳过搜索直接猜关键词
6. **每次操作前重新判断用户意图**，不要因为上下文中提过某个概念就默认使用它

违反以上约束会导致：空白 Chrome 窗口泛滥、登录态丢失、管线混乱、数据错误。

## 测试

```bash
# Worker 全量（pytest 全家桶在 skill/.venv314；需本地 Docker PG 5433/ozon123）
cd worker && PGDATABASE_URL="postgresql://postgres:ozon123@localhost:5433/ozon" PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/ -q
# Worker 单文件（纯 mock，无需 PG）
cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/<file>.py -q
# Skill（本机快速）；CI 标准 = Docker python:3.12-slim（无 Chrome 确定性场景 + cp312 ABI 一致）
cd skill && .venv314/bin/python -m pytest tests/ -q
# pounding-mcp（必须自身 .venv——skill/.venv314 无 pounding_mcp 包）
cd pounding-mcp && .venv/bin/python -m pytest tests/ -q
```

- 本地测试前按 zombie 警告清任务表（`DELETE FROM ozon_product_tasks WHERE status IN ('pending','failed','running')`），避免误激活旧任务真实上架。
- worker 全量失败先查类目树是否导入（`init_data`）；本地 Docker PG 端口 5433（非 5432）。
- 功能验证一律本地 Docker（`deploy/docker-compose.yml`）；实机 gate 要求见「发版」。

## 版本与发版

**四源一致**（缺一发版链路就断，2026-08-31 实证）：

| # | 文件 | 谁读它 |
|---|---|---|
| 1 | 根 `VERSION` | worker 镜像 `APP_VERSION`（cd.yml） |
| 2 | `skill/VERSION` | compile.py 打包覆写 dist/SKILL.md frontmatter + cache.py 缓存指纹 |
| 3 | `deploy/skill/VERSION` | skill 部署包/update 校验 |
| 4 | `skill/SKILL.md` frontmatter `version` | agent 侧技能版本 |

**流程（强制顺序）**：① 改四源 → ② CHANGELOG 顶部新块 → ③ 本地 worker+skill+webui 全绿 → ④ **实机 gate（不可跳过）**：本地 Docker 真实跑通 ≥3 单（真实 1688/Ozon 链接），核对 category_match_log / listing_result_log / 终态——mock 全绿≠能发版 → ⑤ dev→main PR 合入后在 **main 上打 tag**（触发 build-skill 4 平台编译 + cd.yml 部署）→ ⑥ 确认 CD 两个 workflow success → ⑦ 服务器 `cos-update.sh`；skill 用户端自动更新。

```bash
# 发版前快速核对（四个输出必须同版本号）
grep -H "" VERSION skill/VERSION deploy/skill/VERSION | sed 's/:$/: /'; grep -m1 '^version:' skill/SKILL.md
```

## 环境与部署要点

- Worker 凭证随请求传（`GraphInput` 的 token/ozon_client_id/ozon_api_key），不是环境变量。平台级 env（`deploy/.env`，模板 `.env.example`）：`PGDATABASE_URL`（必填）、`SUPABASE_URL/KEY`、`GRSAI_API_KEY`、`RATE_LIMIT_PER_MINUTE`（300）、`MAX_CONCURRENT`（30）、`SENTRY_DSN`。完整清单与部署步骤见 `docs/DEPLOY.md`。
- ⚠️ **生产必配 `CREDENTIAL_MASTER_KEY`**（凭证 AES-256-GCM）：`openssl rand -base64 32`；**启用后不可随意更换**（换 key = 存量凭证不可解密），轮换走 `worker/scripts/rotate_master_key.py`。
- skill 侧：`WORKER_URL`（**实机测试必须显式指向本地 `http://localhost:8080`**——`_const.py` 默认生产地址，曾因此误打生产）、`OZON_CLIENT_ID/OZON_API_KEY`。

## 专项速记（细节在专属文档，此处只留指针）

- **COS 图片真相**：AI 生图 URL 由 MXOU 托管在我方 COS bucket `yss-1256275613`，Ozon 卡实时引用不存副本——bucket 生命周期规则**绝不能覆盖图片路径**（一删卡片全变无图，不可恢复）。worker 侧唯一 COS 上传 = `utils/cos_uploader.py`；prepare 的 `_rewrite_payload_images_to_accelerate` 走全球加速域名（改图 URL 链路勿绕过）。
- **属性缓存**：PG JSONB（非 JSON 文件）；机制与三桶策略见 `utils/dict_value_cache.py` 模块注释；预热/导出/COS 灌入手册 `docs/CACHE-WARM-RUNBOOK.md`（预热用 `--export-from-pg`，勿用旧 `--export-only`）。缓存预热 JSON 资产不进 git。
- **日志**：结构化 JSON，四种审计类型（task.lifecycle / node.* / ozon.api / 链路追踪 trace_id）；代码入口 `utils/logger.py`；详见 `docs/LOGGING.md`。
- **CDP 稳定性**：tab 必须在 finally 关闭；消息 ID 原子计数防碰撞；导航等待用事件不用 sleep；致命断连立即退出轮询；Chrome 多 tab SIGTERM 需 5-10s 轮询 + SIGKILL 回退；1688 滑块自动暂停等人过。
- **Windows 兼容**：进程扫描用 `service._list_browser_commands`（wmic/ps 分流）；启动用 `creationflags`（无 start_new_session）；路径提取用 `Path(p).name`；文件锁 `os.replace` 需重试；编译产物为 `.pyd`。细则 `docs/` 与 chrome_launcher 注释。
- **skill 源码保护**：`compile.py` Cython 编译 13 个 lib 模块 + COPY/AUX 明文双清单；改模块归属必须同步 compile.py 三清单 + 跑 `test_compile_lists.py`；分发流程见 compile.py 头注释与 `build-skill.yml`。
- **双 MCP 分工**：云端 worker `/mcp` 管上架/草稿/店铺/查询（改路由必须同步 `mcp_server.py` 的 `_call`）；本地 pounding-mcp 管采集（依赖本机 Chrome 登录态）。`run_store_action`/`submit_*` 是真实写操作，agent 侧需用户确认。

---

**历史与深读**：全部版本演进见根 `CHANGELOG.md`；架构全景 + 已知问题清单 `docs/ARCHITECTURE/`（09-findings）；过时文档在 `archive/docs/legacy/`。
本文 2026-10-03 W0 治理重写（1806 → 169 行）：36 个版本史块删除（CHANGELOG 均有对应节），不变量收敛为上表并逐条指向代码权威位置。
