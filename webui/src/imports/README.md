# Ozon AI 自动化运营 ERP · API 对接包

> 给对接同事的一站式材料。本项目 = **worker（FastAPI 后端） + webui（React 前端）**，
> 运行在 Docker 容器（`deploy-worker-1`，端口 `8080`）。

---

## 📦 包里有什么

| 文件 | 用途 |
|---|---|
| **`API-INTEGRATION-GUIDE.md`** | ⭐ 对接文档（先读这个）：117+ 端点、鉴权、关键模型、错误码、实测验证 |
| **`openapi.json`** | 实时 OpenAPI 3.1 规范快照（98 路径）——用 `openapi-typescript` 可重新生成类型 |
| **`generated.d.ts`** | 从 openapi.json 生成的 TypeScript 类型（7034 行）——前端对接直接用 |

## 🚀 快速开始（三分钟上手）

### 1. 确认服务在跑

```bash
curl http://<worker-host>:8080/health
# → {"status":"ok","db":"connected"}
```

交互式文档（Swagger）：`GET http://<worker-host>:8080/docs`

### 2. 拿 token

- 你的 MXOU API Key 就是 token（`sk-` 前缀可选）
- 验证：`POST /api/v1/auth/verify`，或直接带 `Authorization: Bearer <key>` 请求业务端点

### 3. 调用示例

```bash
# 读取草稿列表（采集箱）
curl -H "Authorization: Bearer <你的token>" http://<worker-host>:8080/api/v1/drafts

# 提交草稿上架
curl -X POST -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"token":"<token>","credential_id":null}' \
  http://<worker-host>:8080/api/v1/drafts/<draft_id>/submit
```

### 4. 前端 TS 对接

```ts
import axios from 'axios'
import type { paths, components } from './generated'

const api = axios.create({ baseURL: '/api/v1' })
api.interceptors.request.use((cfg) => {
  const token = localStorage.getItem('ozon_webui_token')
  if (token) cfg.headers.Authorization = `Bearer ${token}`
  return cfg
})

// 类型安全示例
type Draft = components['schemas']['DraftOut']
const drafts = await api.get<components['schemas']['DraftOut'][]>('/drafts')
```

## 🔐 鉴权速查

| 场景 | 方式 |
|---|---|
| 业务/管理/订单/商品/任务/模板/凭证/草稿 | `Authorization: Bearer <MXOU key>` |
| **免鉴权（仅 5 个）** | `POST /api/v1/mxou/login`、`GET /site/announcements`、`GET /site/banners`、`/health`、`/api/v1/store/health` |
| token 失效 | `401 {"detail":"Invalid token"}` → 重新登录 |
| 速率超限 | `429` → 退避重试 |
| 乐观锁冲突（draft PATCH） | `409` → 重新拉取后带新 version 重试 |

> 本地起服（未配置 Supabase）→ 任意 token 放行返回 `local_dev`；
> Docker 部署（配置了 Supabase）→ 生产鉴权，token 必须真实有效。

## 📚 详细文档

- 全部端点清单、请求/响应模型、错误码 → **`API-INTEGRATION-GUIDE.md`**
- 关键 schema（DraftCreate / DraftPatch / SubmitResponse / OrderOut...）→ 见 `generated.d.ts` 内 `components['schemas']`
- 交互式 Swagger：`/docs`（ReDoc：`/redoc`）

## 🛠 如何重新生成类型（当 openapi.json 更新后）

```bash
npx openapi-typescript openapi.json -o generated.d.ts
```

## 服务信息

- 版本：`v0.56.6`（见项目根 `VERSION`）
- 端口：`8080`（Docker 映射 5000）；前端 dev `5173`、preview `4173`
- 前端代码：`webui/src/api/client.ts`（1456 行，60+ 对接函数，可直接参考调用方式）
- **v0.57 新增**：`GET /stores/{id}/stats`（店铺卡今日统计）、`GET /discovery/runs` 与 `GET /analytics/bestsellers` 全局共享、`GET /task_statistics` KPI 数据源

---

*对接包生成时间：2026-08-19 · 端点以 openapi.json / Swagger 为最新真相源*
