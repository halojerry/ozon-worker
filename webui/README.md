# webui — Ozon 上架助手 WebUI（React SPA）

Vite + React 18 + TypeScript 脚手架（`docs/PLAN-webui-v1.md` §1.4 / T4）。

静态托管在 worker 域名 `/app` 下（FastAPI `_mount_webui_static`，见 `worker/src/main.py`），
同域零 CORS。登录鉴权走现有 `POST /api/v1/auth/verify`。

## 目录结构

```
webui/
├── src/
│   ├── api/client.ts        # Axios 实例（baseURL=/api/v1 + Bearer 拦截器 + 类型占位）
│   │                         # ⚠️ 类型占位待 openapi-typescript 从 worker openapi.json 生成（单一真相源）
│   ├── pages/               # Login + 5 个占位页（T10 采集箱 / T10b 商品编辑 / T11 店铺 / T12 任务 / T13 生图）
│   ├── components/          # Layout（左侧导航壳）/ PagePlaceholder
│   ├── stores/auth.ts       # token 状态（localStorage 持久化 + useSyncExternalStore）
│   ├── App.tsx              # 路由表（/login + 受保护布局）
│   └── index.css            # 设计 token（颜色/间距/圆角/阴影/字号，T10-T13 统一消费）
├── vite.config.ts           # base='/app/'；dev 代理 /api → localhost:8080
└── package.json
```

## 开发

```bash
cd webui
npm install
npm run dev          # http://localhost:5173/app/ ；/api 代理到本地 worker（VITE_API_PROXY_TARGET 可改）
```

## 构建 + worker 静态托管

```bash
npm run build        # 产出 dist/（assets 引用 /app/ 前缀）
```

worker 侧（`worker/src/main.py` `_mount_webui_static`）：

- 托管目录默认 `webui/dist`（从 `worker/src/` 向上两级），env `WEBUI_DIST` 可覆盖；
- SPA fallback：非静态文件路径回 `index.html`，前端路由（`/app/collect-box` 等）直连/刷新不 404；
- `webui/dist` 不存在时挂载跳过并打 warning，**不阻断 worker**；
- 路径穿越防护：只服务 `dist` 目录内的文件。

### Docker 部署（deploy/Dockerfile）

打包阶段把构建产物复制进镜像并保留默认路径即可：

```dockerfile
COPY webui/dist/ ./webui/dist/
```

（部署脚本接入见 T16，届时 `deploy.sh` 构建 webui 后再打镜像。）

## 验收

- `npm run build` exit 0 且产出 `dist/`；
- `npm run dev` 可跑，浏览器打开 `/app/` 见登录页；
- worker 启动后 `GET /app/` 返回 SPA，`GET /app/collect-box` 同样返回 SPA（fallback）；
- 登录：有效 MXOU token → `POST /api/v1/auth/verify` → localStorage 持久化 → 跳 `/`；
  无效 → 页面内错误提示（token_invalid / balance_insufficient / account_inactive）；
  401 响应拦截器自动清 token 回登录页。
