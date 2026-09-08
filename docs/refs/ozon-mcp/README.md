# ozon-mcp（第三方参考）

> 来源：[PCDCK/ozon-mcp](https://github.com/PCDCK/ozon-mcp)（MIT License）
> 克隆时间：2026-08-21 · 本目录是**只读参考**，非我们维护的代码。
> ⭐ **活安装已就位（2026-09-08）**：查契约优先用本机 MCP 工具 `mcp__ozon__*`，见下
> 「活安装」节。本目录只是仓库内数据底稿——裁剪时 server 壳被丢弃，**不能**直接启动。

## 定位

**开发武器，不暴露给用户。** 用于：
1. 补 worker 端点时查 Ozon API 契约（不用翻 Ozon 官方文档）
2. 抽取 `transport/seller.py` 的 `SellerClient` 进 worker 替换 `ozon_client.py`（按需，见末节）
3. `knowledge/` 下的 YAML 是 Ozon API 知识库（分页/限流/错误码/安全/quirks），补端点时查坑

## 内容

| 目录 | 内容 | 价值 |
|---|---|---|
| `data/seller_swagger.json` (2.4MB) | Ozon Seller API 全量 swagger | 420 方法索引源 |
| `data/perf_swagger.json` (242KB) | Ozon Performance API swagger | 46 方法索引源 |
| `data/swagger_meta.json` | swagger 元信息 | 版本/来源 |
| `knowledge/*.yaml` (9 个) | quirks/pagination/errors/rate_limits/safety/examples/workflows/deprecated/subscription | API 行为知识库 |
| `knowledge/loader.py` + `models.py` | 知识库加载器 + 数据模型 | 参考实现 |
| `transport/` | SellerClient/PerformanceClient/BaseClient + 限流/重试 | transport 层（可抽取进 worker） |
| `schema/` | catalog/responses/graph/extractor/resolver/search | 响应模型与检索层参考 |
| `pyproject.toml` | 依赖清单 | httpx/pydantic/structlog 等 |
| `README.orig.md` | 原项目英文说明 | 466 方法/15 工具全貌 |

**不包含**：上游 `src/ozon_mcp/` 的 server 壳（`__main__.py`/`server.py`/15 个 MCP 工具层）以及
`tests/`、`scripts/`、`.github/`、`Dockerfile`、`uv.lock`——所以本副本只是库文件，起不来 MCP server。

## 活安装（开发期 MCP server，2026-09-08 装好）

- **安装位置**：`/Volumes/os/dev/ozon-mcp`（上游完整 clone，`uv sync --python 3.12` 建 venv）
- **注册位置**：`~/.zcode/cli/config.json` → `mcp.servers.ozon`（stdio，`uv --directory /Volumes/os/dev/ozon-mcp run ozon-mcp`，command 用 uv 绝对路径防 GUI PATH 缺失）
- **零凭证纯查询模式（刻意不配 OZON_* env）**：server 对无凭证场景只暴露 **12 个查询工具**
  （search_methods / describe_method / get_examples / get_rate_limits / get_error_catalog /
  list_sections / get_section / list_workflows / get_workflow / get_related_methods /
  get_swagger_meta / list_methods_for_subscription）；`ozon_call_method`/`ozon_fetch_all`
  **根本不注册**——纯契约查询器，物理上不可能误触线上店铺。以后要 `fetch_all` 实调再加测试店凭证。
- **本地补丁（1 行，上游 bug）**：`src/ozon_mcp/server.py` 的 `_configure_logging` 里
  structlog `configure()` 没设 `logger_factory`，默认 PrintLoggerFactory 把启动日志打进
  **stdout** 污染 stdio JSONRPC 流（注释声称 logs MUST go to stderr 但没做到）。已补
  `logger_factory=structlog.WriteLoggerFactory(file=sys.stderr)`。上游停更（2026-04 后无
  commit），若日后 re-sync 上游需重打此补丁。
- ⚠️ **PyPI 同名包陷阱**：`pip install ozon-mcp` / `uvx ozon-mcp` 装到的是无关项目
  （oychao1988，playwright 爬虫）。只能 clone + 本地装。

## 查询纪律（治「查不准」，2026-09-08 起）

**写任何 Ozon API 调用 / 补 worker 端点前，必须先查 `mcp__ozon__*`，禁止凭记忆或手 grep swagger：**

1. `ozon_search_methods` 按关键词找方法（RU/EN BM25，返回 path + safety 标注）
2. `ozon_describe_method` 看解析好的请求/响应 schema——**重点核对响应顶层形状**（`result` 还是
   `items[]`？）和**分页字段**（`has_next`/cursor/last_id 哪种）
3. 有疑义再看 `ozon_get_examples` / `ozon_get_error_catalog` / `ozon_get_rate_limits`

历史教训全是跳过这步造成的：v0.59 把 `/v5/product/info/prices` 顶层当 `result` 读佣金（实际是
`items[0].commissions`，P0）、空 `offer_id:[]` 过滤查不到数据、v0.62 字典接口单次 5000 不翻页、
prices 缺 commissions 块被当 0 回填污染缓存。

MCP 不可用时才手工兜底：

```bash
# 查某个 Ozon API 方法的契约
python3 -c "import json; d=json.load(open('data/seller_swagger.json')); [print(p) for p in sorted(d.get('paths',{})) if 'product' in p]" | head

# 查分页/限流知识
cat knowledge/pagination_patterns.yaml
cat knowledge/rate_limits.yaml
```

## 抽取 SellerClient 进 worker（按需，不排期）

worker `utils/ozon_client.py` 现状（v0.65/v0.69 后）已不是裸 `ozon_post`：tenacity 3 次重试 +
令牌桶限流（`ozon_rate_limiter.py`，参数源自本库 `knowledge/rate_limits.yaml`）+ Retry-After 解析 +
7 类 typed errors（`ozon_errors.py`，ported from 本库）。与 ozon-mcp transport 哲学一致
（唯 httpx 异步 vs 我们同步 requests）。**只在补缺口域**（财务报表 /v1/finance/transaction/list、
FBO 发货 /v3|v4/posting/fbo/*）**时参考其实现，不整包迁移**。
