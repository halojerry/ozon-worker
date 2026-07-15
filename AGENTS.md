# AGENTS.md — ozon-worker 工作区

本文件是工作区级导航。两个子项目各有更详细的文档,改动前请先读对应文档(见「深入阅读」)。

## 工作区概述

两段式 Ozon 上架系统，职责严格分离：

| | Skill | Worker |
|---|---|---|
| **角色** | Agent 调用的工具（OpenClaw/Claude Code/Hermes/ZCode 等） | 云端管线，消费信封完成上架 |
| **位置** | 本地 | 云端（Coze 部署） |
| **入口** | `skill/SKILL.md`（Agent 操作手册） | `worker/src/main.py`（FastAPI + CLI） |
| **职责** | 1688 CDP 抓取 → 组装 GraphInput 信封，**不上架** | 接收信封 → 类目→定价→属性→生图→校验→上传→自学习 |
| **接口** | 输出 `GraphInput` JSON（三层结构 `{draft, source, extensions}`） | 输入 `GraphInput`，输出 `GraphOutput` |

接口契约详见 `docs/CONTRACT.md`。Agent 调用指南详见 `skill/SKILL.md`。

## 目录结构

```
ozon-worker/
├── skill/                      # 本地:1688 抓取 + 信封组装 (Python ≥3.9, pip)
│   └── scripts/
│       ├── cli.py              # CLI 入口:search / probe / graph
│       ├── cloud_probe.py      # build_graph_envelope(信封生产者)
│       ├── lib/                # ak_1688_client / ozon_api / config_store / ...
│       └── capabilities/browser_probe/   # Chrome CDP 探针 + 反检测
└── worker/                     # 云端:LangGraph 上架工作流 (Python ≥3.12, uv)
    └── src/
        ├── main.py             # FastAPI + CLI 入口(-m http/flow/node)
        ├── graphs/
        │   ├── graph.py        # main_graph 编排(auth→...→learning_record)
        │   ├── state.py        # GlobalState / GraphInput / GraphOutput
        │   ├── nodes/          # ~28 个节点
        │   └── validation_retry_loop.py   # 校验失败重试子图
        ├── storage/            # database(Supabase/PG) / memory(checkpoint) / s3
        └── utils/              # local_db_manager / task_processor / size_mapper / mxou_api / ...
```

## skill → worker 契约(最重要)

交接载荷是 `GraphInput`,定义在 `worker/src/graphs/state.py:110`:

```
GraphInput = { token, ozon_client_id, ozon_api_key, envelope }
```

`envelope` 采用**三层结构** `{draft, source, extensions}`，与 worker `ingest_node` 优先匹配:

- **`draft`** — 产品数据:
  - 必填: `item_id`、`title`、`images[]`(str URL 数组)、`weight`(克, int)、`dimensions{length,width,height}`(mm, int)
  - 定价相关: `purchase_cost`(CNY, float)、`purchase_url`、`currency`("CNY")
  - 可选: `attributes{}`(dict[中文属性名→值])、`supplier`、`stock`、`shipping{}`、`ozon_category{description_category_id,type_id}`
  - 多SKU: `variants[{sku_id,name,color,model,image,price,original_price,size,stock}]`
  - 单SKU: 顶层 `sku_id`、`price`、`original_price`(均平铺在 draft 下)

- **`source`** — 采购源信息: `{purchase_url, purchase_cost}`(与 draft 中同名字段冗余,供 worker prepare_node 兜底)

- **`extensions`** — 定价配置透传: `{margin_rate, commission_rate, fx_buffer}`(可选,worker 有默认值 0.25/0.10/0.05)

> ⚠️ **关键约定(已代码核实):**
> - **`variant.price` = 1688 SKU 原始采购成本(CNY)**，skill **不做加价**。定价全权由 worker `pricing_node` 在采购成本基础上叠加佣金+汇率缓冲+利润率。
> - **`dimensions` 单位 mm**: 1688 原数据为 cm(页面 JSON 验证: `columnList: [{label:"长(cm)"}]`),skill 自动 cm→mm ×10。worker `pricing_node` 再 /10 转回 cm 定价;`prepare_node` 启发式判 mm(≥50)直接用于 Ozon 上架。改一边必须同步另一边。
> - **`weight` 单位克**: 1688 原数据为 g(页面 JSON 验证: `columnList: [{label:"重量(g)"}]`),直传。
> - **单SKU vs 多SKU**: 多SKU 时 `draft.variants` 为数组,无顶层 sku_id/price/original_price;单SKU 时 variants 数组仍存在但在 draft 顶层平铺 `sku_id`/`price`/`original_price`。worker `ingest_node` 都能处理。

## 架构边界

- **worker 三层**:FastAPI `POST /submit_task`(校验 token、入队)→ Supabase 队列 `ozon_product_tasks`(`SELECT FOR UPDATE SKIP LOCKED`)→ 最多 10 个并发 LangGraph worker(`SupabaseTaskProcessor`,`asyncio.Semaphore(10)`)。
- **skill 是无状态本地抓取**,不调用任何 Ozon 上架 API(那是 worker 的职责)。
- 编辑时不要越界:别给 skill 加上架调用,别给 worker 加 1688 抓取。

## 常用命令

| 子项目 | 命令 |
|---|---|
| skill | `pip install -r requirements.txt && playwright install chromium` |
| skill | `python3 scripts/cli.py search "<词>"` / `probe --url <url>` / `graph --item-id <id> --category-query "<ru>"` |
| worker | `uv sync`(用阿里云镜像,见 worker/pyproject.toml) |
| worker | `bash scripts/local_run.sh -m flow -i '{...}'` 跑全流程 |
| worker | `bash scripts/local_run.sh -m node -n <节点ID> -i '{...}'` 跑单节点 |
| worker | `bash scripts/http_run.sh -p 5000` 启 HTTP 服务 |
| worker | `uv run pytest` 跑测试 |

## 环境与密钥

- **worker 凭证随请求传**(放在 `GraphInput` 里的 `token`/`ozon_client_id`/`ozon_api_key`),**不是环境变量**。
- worker 平台级环境变量:`SUPABASE_URL`、`SUPABASE_KEY`、`PGDATABASE_URL`(PG/检查点/队列)、`COZE_WORKSPACE_PATH`(定位 `assets/`)、`COZE_BUCKET_ENDPOINT_URL`(S3)。Coze 平台会通过 `coze_workload_identity` 自动注入。
- skill 环境变量见 `skill/.env.example`:`ALI_1688_AK`、`OZON_CLIENT_ID`、`OZON_API_KEY`、`MXOU_TOKEN`、`MXOU_API_BASE`。
- ⚠️ worker 代码里硬编码了 sandbox Supabase key 和 `GRSAI_API_KEY` 作为**回退**,生产环境务必用环境变量覆盖。

## 深入阅读(改前先看)

- **`skill/SKILL.md`** —— Agent 调用指南（入参、返回格式、提交 Worker、错误处理）
- **`docs/CONTRACT.md`** —— Skill↔Worker 接口契约（GraphInput/GraphOutput schema、Worker API、错误码）
- **`worker/AGENTS.md`** —— worker 完整文档:节点逐一流程、变更日志、Ozon API 坑(改节点或重试子图前必读)。
- **`skill/README.md`** —— skill CLI 用法、输出 schema、Python API 示例。
- **`worker/config/*.json`** —— 5 个 LLM prompt 配置(category_match / attributes / scene_generation / error_repair / translate_russian),均走 mxou `deepseek-v4-flash`。
- **`worker/assets/`** —— Ozon API 文档 JSON、服装/鞋尺码 CSV、n8n 流程导出 JSON、`workflow_progress.json`。

## 需牢记的约定

- COS 图片 URL **直接传给 Ozon**,不再走 S3 中转(避免内存泄漏);`image_url_processor.py` 仍在但 prepare 路径已绕过。
- 多 SKU 变体绑定属性是 **9048**(不是 8292);`vat="0"`;主图(`primary_image`)与 `images` 分开。
- WARNING 级 Ozon 错误(`erased_attribute_value` / 9782)**过滤不算失败**;`ozon_status` 返回 `pending` 视为软成功(有 30s–2min 导入延迟)。
- 多 SKU 自由文本颜色不合并,必须用 Ozon 字典颜色(dict_id > 0);revalidate 时共享属性(含 9048)要同步到所有变体 items[1+]。
- `GlobalState` 有自定义 reducer:`progress_counter`=max、`error_message`=覆盖、`failed_stage`/`stages`=合并。改状态字段时注意匹配 reducer 语义。
- worker 用 `uv`,默认源是阿里云镜像;PyPI 仅在 `tool.uv.sources` 显式引用时使用(预发布包同步延迟时回退)。
