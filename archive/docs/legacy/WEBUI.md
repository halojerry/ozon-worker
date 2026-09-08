# WebUI 使用说明（v0.41.0）

> WebUI 是 worker 域内的浏览器管理面：采集箱、商品编辑、店铺管理、任务进度、生图工作台五页，
> 与 skill 双向互通。设计依据 `docs/PLAN-webui-v1.md`（C1-C7 冻结契约），端点契约见
> `docs/CONTRACT-v4.md` Part 1b。

---

## 1. 是什么

WebUI 让用户不敲命令也能操作和查看整个上架流程。它和 skill 是同一套 worker 的两个门面：

```
skill（客户本地 CLI）──►  worker（云端 Docker + PG）◄──  WebUI（浏览器 /app）
       采集/组信封               执行/仲裁/存储              审阅/编辑/跟踪
```

**单向权威 + 双向可见**：skill 是采集权威（CDP 抓 1688/Ozon），WebUI 是审阅/编辑权威（改字段、选店铺、提交上架），worker 是执行权威（状态机仲裁）。三者不直接通信，只读写 worker 的状态与数据，最终一致，WebUI 2-5s 轮询刷新。

**三段式流程**：采集箱（skill `--to-box` 或 WebUI 创建）→ 商品编辑页（手动 + AI 双模式，选店铺）→ 上架至 OZON（submit 入队，任务进度页跟踪）。

---

## 2. 部署

WebUI 是纯静态 React SPA（Vite 构建），由 worker 的 FastAPI 直接托管在 `/app`，零 CORS、无独立服务。

### 2.1 构建前端

```bash
cd webui
npm install
npm run build        # 产出 webui/dist/
```

开发调试：`npm run dev` 启动 Vite dev server（默认 http://localhost:5173，`/api` 代理到 `http://localhost:8080`，可用 `VITE_API_PROXY_TARGET` 覆盖目标）。

### 2.2 worker 静态托管

worker 启动时 `_mount_webui_static`（`worker/src/main.py`）自动挂载：

- dist 路径 = 环境变量 `WEBUI_DIST`（未设置时默认仓库内 `webui/dist`）。
- 未构建（dist/index.html 不存在）→ 跳过挂载，**不阻断 worker 启动**，日志提示先 `npm run build`。
- SPA fallback：非静态文件路径回 `index.html`（前端路由直连/刷新不 404），仅允许 dist 目录内文件（防路径穿越）。

### 2.3 生产部署（Docker）

**v0.41 起 CI 自动构建分发，服务器零前端操作：**

```text
git tag v0.41.0 → GitHub Actions（cd.yml cos-deploy）
  → ① setup-node + npm ci + npm run build（产出 webui/dist）
  → ② tar 打包 deploy/ + worker/ + webui/（含 dist，排除 node_modules）
  → ③ 传 COS /ozon-worker/ + manifest.json
  → 服务器 bash deploy/update.sh（cos-update.sh）
  → ④ 解压覆盖（webui/dist 就位，脚本校验 index.html 存在）
  → ⑤ docker compose build + up（compose 已配 ../webui/dist 挂载 + WEBUI_DIST）
```

compose 挂载（`deploy/docker-compose.yml`，已提交）：
```yaml
volumes:
  - ../webui/dist:/app/webui/dist:ro
environment:
  - WEBUI_DIST=/app/webui/dist
```

访问 `http://<worker-host>:8080/app/` 即进入 WebUI 登录页。

> ⚠️ 手动/非 tag 部署（如本地 Docker）：先 `cd webui && npm ci && npm run build` 产出 dist 再 `docker compose up -d --build`。dist 未构建时 worker 正常启动，但 `/app/` 不挂前端（日志 warning）。

### 2.4 凭证加密密钥

新增环境变量 `CREDENTIAL_MASTER_KEY`（32 字节主密钥，AES-256-GCM 列级加密 Ozon API Key）。
**未配置时凭证功能不可用**（创建凭证报错，其余页面不受影响）。示例：

```bash
CREDENTIAL_MASTER_KEY=$(openssl rand -base64 32)
# 写入 deploy/.env（docker-compose.yml 自动读取）；⚠️ 更换密钥后历史凭证无法解密
```

---

## 3. 登录

WebUI 用 **MXOU token**（就是 skill 里 `set_token` 配的那个 `sk-...`）登录：

1. 打开 `/app/` → 输入 token → 「登录」。
2. 前端把 token 存 localStorage，之后所有请求带 `Authorization: Bearer sk-...`。
3. token 无效/被禁用/余额不足 → 登录页提示对应原因（与 `auth/verify` 同口径）。

> ⚠️ token 即凭证，谁拿到 token 谁就能操作该账号的草稿、店铺、任务。不要在公共浏览器保存登录态。

---

## 4. 五个页面

### 4.1 采集箱（CollectBox）

skill `--to-box` 入箱的草稿都在这里，也支持 WebUI 直接创建。

- 列：☐ | 图片 | 产品名称 | 采集价格（variants 区间价 ¥0.1-¥3.4）| SKU 数量 | 采集来源（skill/webui）| 备注 | 上架状态（来自 draft_submissions）| 创建/更新时间 | 操作
- 工具栏：批量删除 / 清空采集箱（级联删 submissions）。
- `[编辑上架]` → 跳商品编辑页（毛子模型：采集箱不选店铺，店铺在编辑页选）。
- 上架状态列反映该草稿各次提交：未上架=无行 / 已上架=published / 失败=failed。

### 4.2 商品编辑页（Products）

对标上品帮 editGoods，三区块锚点导航：**主要信息 / 产品属性 / 变体设置**。

- **主要信息**：上架店铺下拉（credentials 列表，掩码显示）· 产品类目 · 品牌（无品牌）· 标题（3 个 AI 按钮 + 加号）· 包装重量克🤖 · 包装长宽高 mm×3🤖。
- **产品属性**：型号名称（合并提示）· 简介（1688 属性自动拼接）· 主题标签 · JSON 富内容 · 填写更多属性。
- **变体设置**：变体表格（图片/视频/货号一键生成/我的售价/划线价/最低价/长度/颜色/颜色名称/宽度/类目属性 + 同首行按钮）；工具栏自动颜色样本/批量水印/批量翻译/批量删除/添加变体。
- **其它**：货源地址 · 货源备注 · 选择仓库（`extensions.warehouse_id`）· 库存数量（每 SKU，`extensions.stock`）。
- **底部**：保留采集数据 / AI 填写产品信息 / AI 商品套图（→ 生图工作台）/ 引用模板 / 关闭 / 定时上架（v1 stub，持久化 `scheduled_at` 无调度器）/ **立即上架**。
- 保存走 PATCH（version 乐观锁，并发编辑冲突返回 409 提示刷新）。
- 立即上架收到**跨店 confirm 标记**（C5 v1）→ 弹确认框「该商品已上架到店铺X，确认继续上架到店铺Y？」→ 确认后二次提交。

### 4.3 店铺管理页（Stores）

毛子绑定弹窗式管理 Ozon 店铺凭证，三层防御（掩码 + 加密 + 轮换）。

- 弹窗字段：shop_name（店铺名称）/ currency（默认 CNY）/ is_default radio（「默认上传产品的店铺」）。
- 列表**仅显示掩码** `****abcd`（API key 永不完整回显）。
- 操作：添加 / 轮换（旧行 revoked + 新行 active）/ 吊销 / 立即校验（解密 → Ozon probe → valid/reason 实时展示）。
- 设置 `is_default` 会清旧默认（第二默认被 `uq_credentials_default` 唯一索引拒绝 → 409）。
- **轮换提醒 banner**：`last_rotated_at` 过久未轮换提示。

### 4.4 任务进度页（Tasks）

上架记录视图。

- 筛选：平台/账号/店铺/状态/货号/时间范围/方式/竞品代码。
- 列：☐ | 商品信息 | 平台,店铺,账号 | 上架状态 | 库存状态 | 最低价状态 | 售价 | 划线价 | 货源信息 | 竞品代码 | 上架方式 | 操作时间 | 上架时间 | 操作。
- 工具栏：查询 / **异常重上**（failed/rejected → `resubmit_task` 新任务行）/ 释放帮豆（v2 入口）/ 批量搬家（v2 入口）。
- 今日上架数量 N。

### 4.5 生图工作台（ImageStudio）

AI 商品套图。

1. **商品原图**：添加原图，最多 3 张。
2. **商品卖点&要求**：AI 帮写（1 次调用）+ textbox（名称/卖点/人群/场景/参数）。
3. **商品图配置**：已选 N 张（最少 2 张）；现有 slot 类型 ±1（白底/场景/卖点/细节/对比/社交证明/多角度）→ 生成 `image_gen_plan`（type→count）；材质/尺寸图 v1 置灰（无现成节点）。⚠️ plan 必须含 Phase1（白底图或多角度图），仅选 Phase2 类型会被拒绝。
4. **富内容配置**：16 种，v2，v1 置灰。
5. **一键生成**：生成前显示 MXOU 余额 + 预计消耗（每图 1 次调用，N 张 = N 次）+ 确认弹窗。**余额 ≤ 0 阻止生成**。
6. **效果预览**：分类型结果展示（商品图下载；富内容/富内容图文 v2）。

单 slot 重生成：任务进度/商品详情里的 `regen` 按钮 → 强制重生成 `version++` 新 URL，**不静默命中旧缓存、不重复烧额度**（`image_parent_task_id` 保证 resubmit 复用父图）。

---

## 5. 与 skill 双向互通

| 方向 | 动作 | 说明 |
|------|------|------|
| skill → WebUI | `graph --url <1688 URL> --to-box` / `follow --ozon-url <URL> --to-box` | 替代直接上架：信封 POST `/api/v1/drafts` 入采集箱，打印 `draft_id` + 「已入采集箱，请到 WebUI 认领」 |
| WebUI → 上架 | 商品编辑页「立即上架」 | 凭证注入（所选店铺/默认店铺）→ submit → 入队 → 任务进度页跟踪 |
| WebUI 操作闭环 | 生图工作台 regen / 任务页异常重上 / 商品页更新在线商品 | 全部落 worker 状态机，skill 侧 `query <task_id>` 同样能看到结果 |

- **冷启动兼容**：老 skill 无 `--to-box` → 直接 submit（行为不变）；WebUI 首页横幅提示「检测到旧版 skill 直接上架，升级后可用采集箱」。
- **同源数据**：一个草稿可提交到多个店铺 → 多个 `draft_submissions` 行，各自独立上架状态，`draft.id` 永不变；跨店提交触发确认提示（C5 v1，不硬拦）。
- **查任务**：WebUI 任务进度页与 skill `query <task_id>` 读同一张 `ozon_product_tasks`，状态/进度一致。

---

## 6. 相关链接

- 执行计划：`docs/PLAN-webui-v1.md`（§2 C1-C7 契约、§4 并行执行图、§5 T1-T16 任务详情）
- 端点契约：`docs/CONTRACT-v4.md` Part 1b
- 前端代码：`webui/`（Vite React TS，`src/api/client.ts` 由 worker openapi.json 生成）
- 后端分层：`worker/src/routes/`（薄）+ `worker/src/services/`（厚）+ `api/schemas.py`（Pydantic 契约）
