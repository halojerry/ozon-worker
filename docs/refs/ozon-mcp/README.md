# ozon-mcp（第三方参考）

> 来源：[PCDCK/ozon-mcp](https://github.com/PCDCK/ozon-mcp)（MIT License）
> 克隆时间：2026-08-21 · 本目录是**只读参考**，非我们维护的代码。

## 定位

**开发武器，不暴露给用户。** 用于：
1. 补 worker 端点时查 Ozon API 契约（不用翻 Ozon 官方文档）
2. 抽取 `transport/seller.py` 的 `SellerClient` 进 worker 替换单薄的 `ozon_client.py`
3. `knowledge/` 下的 YAML 是 Ozon API 知识库（分页/限流/错误码/安全/quirks），补端点时查坑

## 内容

| 目录 | 内容 | 价值 |
|---|---|---|
| `data/seller_swagger.json` (2.4MB) | Ozon Seller API 全量 swagger | 420 方法索引源 |
| `data/perf_swagger.json` (242KB) | Ozon Performance API swagger | 46 方法索引源 |
| `data/swagger_meta.json` | swagger 元信息 | 版本/来源 |
| `knowledge/*.yaml` (8 个) | quirks/pagination/errors/rate_limits/safety/examples/workflows/deprecated/subscription | API 行为知识库 |
| `knowledge/loader.py` + `models.py` | 知识库加载器 + 数据模型 | 参考实现 |
| `src/ozon_mcp/transport/` | SellerClient/PerformanceClient/BaseClient + 限流/重试 | transport 层（可抽取进 worker） |
| `src/ozon_mcp/schema/` | catalog/responses/graph 模型 | 响应模型参考 |
| `pyproject.toml` | 依赖清单 | httpx/pydantic/structlog 等 |
| `README.md` | 原项目说明 | 466 方法/15 工具全貌 |

## 不包含

- `tests/`、`scripts/`、`.github/`、`Dockerfile`、`glama.json`、`uv.lock` — 与我们无关

## 使用方式

```bash
# 查某个 Ozon API 方法的契约
python3 -c "import json; d=json.load(open('data/seller_swagger.json')); [print(p) for p in sorted(d.get('paths',{})) if 'product' in p]" | head

# 查分页/限流知识
cat knowledge/pagination_patterns.yaml
cat knowledge/rate_limits.yaml
```

## 抽取 SellerClient 进 worker（待执行）

当前 worker `utils/ozon_client.py` 只有 `ozon_post()` 一个函数 + `ozon_check_quota()`。ozon-mcp 的 `transport/seller.py` 提供了完整的 `SellerClient`（Client-Id + Api-Key 鉴权 + 限流 + 重试 + 分页）。迁移时需把 httpx 异步改回 worker 的同步 requests 风格。
