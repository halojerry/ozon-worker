# Ozon Worker · API 对接包

> 给对接同事/集成方的入口。本项目 = **worker（FastAPI 后端） + webui（React 前端）**，
> 运行在 Docker 容器（`deploy-worker-1`，端口 `8080`）。
> **端点清单、鉴权、错误码的权威文档在 `docs/`，本目录只放机器可读产物。**

## 阅读顺序

1. **`docs/API-OVERVIEW.md`** — 对外约定：Base URL 双环境、**双鉴权矩阵**（请求体 `token` vs `Authorization: Bearer`）、限流、错误信封双形态、14 个错误码、分页、任务生命周期、版本策略、API 变更记录
2. **`docs/API-REFERENCE.md`** — 全部端点参考（方法/路径/参数/请求响应示例/状态码），由 `worker/scripts/gen_api_docs.py` 从 OpenAPI 自动生成
3. **`docs/MCP-SERVER.md`** — 远程 MCP 面（`/mcp`，17 工具，agent 对接用）
4. **`docs/CONTRACT-v4.md`** — skill↔worker 信封契约（提交上架任务必读）
5. 交互式：`GET /docs`（Swagger）、`GET /redoc`

## 本目录文件

| 文件 | 用途 | 如何刷新 |
|---|---|---|
| `openapi.json` | OpenAPI 3.1 快照（与 `webui/src/imports/openapi.json` 逐字节相同） | `python worker/scripts/gen_api_docs.py` |
| `generated.d.ts` | 从 openapi.json 生成的 TypeScript 类型 | `cd webui && npx openapi-typescript src/imports/openapi.json -o src/imports/generated.d.ts && cp src/imports/generated.d.ts ../api-integration/` |

CI（`scripts/ci.sh` Step 5d + GitHub `test-worker` job）会校验快照与代码一致，改了 API 忘记刷新会红。

## 三分钟上手

```bash
curl http://<worker-host>:8080/health          # {"status":"ok",...}

# Bearer 面（REST 业务端点）
curl -H "Authorization: Bearer <MXOU key>" http://<worker-host>:8080/api/v1/drafts

# 请求体 token 面（提交上架任务；完整信封见 docs/CONTRACT-v4.md）
curl -X POST -H "Content-Type: application/json" \
  -d '{"token":"<MXOU key>","ozon_client_id":"...","ozon_api_key":"...","envelope":{...}}' \
  http://<worker-host>:8080/api/v1/submit_task
```

前端 TS：

```ts
import type { paths, components } from './generated'
type Draft = components['schemas']['DraftOut']
```

> 鉴权细节（哪些端点走 Bearer、哪些走 body token、租户如何解析、本地未配 Supabase 时的回退语义）
> **以 `docs/API-OVERVIEW.md` 为准**，本文件不再重复。旧版对接指南（描述 M2 前「key 派生租户」
> 模型，已与代码不符）已归档至 `archive/docs/legacy/API-INTEGRATION-GUIDE-legacy-key-derived-auth.md`。
