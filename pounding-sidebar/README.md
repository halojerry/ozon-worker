# pounding-sidebar

Pounding 电商客户端侧边栏插件（dsh-better-sidebar 消费插件）。

侧边栏 **8 板块**中，`Agent`（对话）为 dsh 原生，本插件注册其余 **7 个业务板块**：

| 板块 | tab id | 数据源（PRD §2.3 能力归属） |
|---|---|---|
| 采集箱 | `pounding:collect` | worker `/api/v1/drafts`（本地网关注入 Bearer）|
| 任务中心 | `pounding:tasks` | worker `/task_status`、`/tasks` |
| 专家 | `pounding:experts` | skill 能力（本地 pounding-mcp HTTP 网关 8901）|
| 知识库 | `pounding:vault` | vault 落盘（better-sidebar `/sidebar/api/fs.*`）|
| 爆品新闻 | `pounding:buzz` | skill `queries`/`bestsellers` + 汇率 + 外部源 |
| 计算器 | `pounding:calculator` | 纯脚本直算（worker `compute_price` 公式前端化）|
| 用量 | `pounding:usage` | dsh token-meter + worker 配额/余额 + 远程通道状态 |

另注册一个 **CSV 文件预览器**（`pounding:csv`）：vault 产出的选品/采集导出 csv 在侧栏直接预览（演示 `registerFileViewer` 机制）。

---

## 验证结论（2026-08-19，基于 dsh-better-sidebar v0.13.1 源码）

API 契约已从仓库 `omdsh-dev/DSH-better-sidebar` 源码核实，关键事实：

1. `ctx.betterSidebar` 是 **client half 服务**（仅浏览器侧）；host 半读 better-sidebar 数据走 `/sidebar/api/*` HTTP 路由。
2. 消费插件三步：
   - `import type {} from 'dsh-better-sidebar'` —— 触发 `Context` 类型合并（编译期擦除，无运行时依赖，不触发构建纯度门）；
   - `export const inject = ['betterSidebar']` —— 服务就绪后才激活；
   - `ctx.effect(() => ctx.betterSidebar.registerTab({...}))` —— **必须包 effect**，fiber 卸载（HMR/禁用）时自动撤销注册，否则下次激活报 `already registered`。
3. `registerTab` / `registerFileViewer` 返回 disposer；内置 tab id（editor/explorer/git/subagent/terminal/browser/diff）不可重复，我们统一 `pounding:*` 前缀。
4. 数据访问：组件收到 `TabComponentProps = { ctx, store, scope: { sessionId, cwd? }, tab, visible }`；`visible===false` 时应暂停轮询。
5. `registerFileViewer` 匹配算法：priority 降序 → detect → exts；`fetchStrategy: 'fsRead'` 走 `/sidebar/api/fs.read`，component 收到 `content` 文本。
6. 服务还提供 `openTab` / `activateTab` / `updateTab` / `openFile` / `badge` / `onOpen|onActivate|onClose` / 声明式设置等（v0.12+，`features` 能力探测）。

**类型校验与构建均已通过**：`tsc --noEmit` 零错误；`npm run build` 产出 host ESM（lib/index.js）+ client CJS bundle（lib/client.js，经 `window.__ModuleLoader__.load({ id: "pounding-sidebar", ... })` 注册，7 tab + 1 viewer）。

---

## 安装 / 挂载

前置：先装 `dsh-better-sidebar`（本插件依赖它的服务；未装时本插件照常加载，注册静默跳过）。

```bash
# 1. 装 better-sidebar
dsh plugin --profile web add dsh-better-sidebar

# 2. 装 pounding-sidebar（GitHub 或 npm，取决于发布形态）
dsh plugin --profile web add github:<组织>/pounding-sidebar
# 或手动：把 cordis.patch.yml 的 insert 追加到 ~/.dsh/profiles/web/cordis.patch.yml

# 3. 硬刷新浏览器（Cmd/Ctrl+Shift+R）；client 改动热加载，无需重启 dsh web
```

> 注意：`NODE_ENV=production` 时 `npm install` 会跳过 devDependencies；本地开发用 `env -u NODE_ENV npm install`。

---

## 开发

```bash
env -u NODE_ENV npm install
npm run typecheck   # tsc --noEmit
npm run build       # tsc 发类型 + tsdown 打两个产物
```

结构：

```
src/
├── index.ts           # host half（占位，无 Node 逻辑）
└── client/
    └── index.tsx      # 7 业务板块 tab + CSV viewer（经 ctx.betterSidebar 注册）
```

构建参照社区标准消费者插件 `dsh-sentinel` 的模板：client 半 externals 恰为平台 seed 模块，其余 inline；跨插件协作只走 `ctx.betterSidebar` 服务方法，**禁止 value-import `dsh-better-sidebar`**（构建纯度门会拒）。

---

## 待办（骨架 → 实装）

- [ ] 采集箱：接 worker `/api/v1/drafts`，渲染商品卡片（图片 + 采购价 + 运费 + 利润率）
- [ ] 任务中心：任务列表/进度 → worker；agent 也可经 pounding-mcp 查询
- [ ] 专家：按钮 → 本地 pounding-mcp HTTP 网关（8901）触发 skill
- [ ] 知识库：vault 目录树 + markdown 预览（内置 viewer）
- [ ] 爆品新闻：热销榜/热搜词/汇率/政策订阅
- [ ] 计算器：完整跨境定价表单 + worker `compute_price` 公式
- [ ] 用量：token-meter + worker 配额 + 远程通道状态
- [ ] 挂载到 `pounding-harness-shell`（fork 的 dsh-desktop）一起打包
