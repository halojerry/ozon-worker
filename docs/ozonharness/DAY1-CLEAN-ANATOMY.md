# DAY1-Clean（Boujoy Harness）解剖报告

> 深度调查时间：2026-08-19（两路源码级：网关 1626 行 + 前端 3291 行 + Swift 壳 + vault 全读）
> 用途：作为「电商版 Harness」的改造底座，本文件是后续所有改造的真相源。
> 结论先行：**DAY1-Clean 是完整可跑的产品，我们的电商能力全部以「改造 + 挂载」方式叠加，不复刻 UI 层。**

---

## 1. 三层架构

```
macOS App (BoujoyHarness.swift, WKWebView)
 ├─ 拉起 dsh: dsh web --host 127.0.0.1 --port 3080 (知识模式, cwd=vault) / 3081 (纯净模式, cwd=~)
 ├─ 拉起网关: boujoy_server.py --port 8766 (静态站 + /api/* + 反代 dsh)
 └─ 浏览器只加载 127.0.0.1:8766 (同源, 无 CORS)
       └─ web/ = index.html + app.js(3291行) + app.css(97KB) ← 全部 UI 逻辑
```

- 端口：8766 网关（0.0.0.0，手机可访问码接入）/ 3080 dsh 知识 / 3081 dsh 纯净
- 数据流：前端 `rpc(method)` → `POST /api/harness/{mode}/{method}` → 网关原样反代 dsh `/api/{method}`；事件走 WS 双向字节桥（`events.mux` 会话/审批/提问 + `events.host` 主机）
- **网关是唯一读盘层**：vault / records / news 全在网关读；会话状态全在 dsh
- 会话绑定工作区（cwd）；删除会话 = 网关移到 `~/.Trash` + 记 deleted-sessions.json

## 2. 双 profile 机制

- 两个实例是**同一个 web profile**（bundles: `dsh-base` + `dsh-web-app`），差异只在 `DSH_HOME`：
  - knowledge（3080，`home/`）：挂 vault 工作区、有 settings.yaml（默认 deepseek-v4-flash）+ `.credentials.yaml`
  - clean（3081，`clean-home/`）：无 vault、首次从 knowledge 复制凭证
- 模型配置：dsh 自读 `$DSH_HOME/.credentials.yaml`（`DEEPSEEK_API_KEY`，0600）+ 环境变量 `DEEPSEEK_BASE_URL`
- dsh 版本：**0.1.0-rc.6**（我们的插件按 rc.7 验证 → 改造时需升级 runtime）

## 3. 六大板块机制

| 板块 | 机制 | 数据源 |
|---|---|---|
| **AGENT** 执行现场 | dsh 会话 + WS mux 帧（审批/提问/投影/jobs）+ 权限命令 `commands/execute` | dsh 3080/3081 |
| **知识库** 第二大脑 | vault 全量索引 + CJK 检索 + 视频媒体 + 焦点卡（读 Active-Context.md）| `/api/knowledge/*` |
| **专家** 可调用的方法 | 卡片 = `vault/05-Prompts/Boujoy-Harness/Experts/*.md`；派发 = **纯 prompt 注入** | `/api/records?kind=expert` |
| **风格** VOICE/FORM/RHYTHM | 与专家同构；叠加 = 派发对话框里拼进 prompt | `/api/records?kind=style` |
| **监控** USAGE/TRAJECTORY | **无独立 API**，全来自 dsh 会话投影（tokenUsage/contextPressure/缓存命中/推理强度）+ 历史折叠 | dsh projection |
| **新闻** AI FEED | RSS 爬近 3 天 AI 新闻+工具各 10 条，6h 缓存，刷新重爬 | `/api/news` |

**关键**：专家/风格 = **纯 prompt 注入**，非 dsh skill 注册——`confirmDispatch()` 把专家/风格指令拼进输入框文本。

## 4. Vault 知识库机制（自动捕获）

- `AGENTS.md`（168 行）定义 agent 角色 = 本地 Markdown 记忆库管理员；dsh 以 cwd=vault 启动时自动读取
- 启动默认读：`AGENTS.md` + `DASHBOARD.md` + `00-System/Boot.md` + `Hot-Index` / `Memory-Index` / `Active-Context`
- 捕获管道：Value-Filter（0-3 不存 / 4-5 队列 / 6-8 知识卡 / 9-10 卡+热索引）→ 去重（bigram Jaccard 0.62）→ 原子写
- 落盘闭环：前端「沉淀」→ 让模型打分压缩 → **模型用 HTTP 工具 `POST /api/knowledge/capture`** → 网关只守安全（路径白名单 02-projects~06-business、防符号链接、原子写）
- 知识卡格式：YAML frontmatter（type/role/status/updated/source/tags）+ 正文（一句话结论/场景/内容/决策/行动/关联）

## 5. 安全基线（叠加电商时保持同一套）

- CORS 白名单（8766 壳 / 3080 / 3081 / null）；访问码（非 loopback 必须，60s 内 10 次限流）
- Origin 校验用对端 socket 地址（防 DNS-rebinding）；写操作先过校验
- 路径逃逸 403 / 符号链接防护 / 凭证 0600 / deleted-sessions 记废纸篓恢复

## 6. 启动链（macOS Swift 壳）

`applicationDidFinishLaunching` → loadConfig（vault/dshRoot/knowledgeHome/cleanHome/python）→ ensureHarness(3080, cwd=vault, DSH_HOME=home) → ensureProductServer(8766, 6 位访问码写桌面提示) → 轮询 heartbeat → 加载 8766。退出 terminate 全部子进程 + 网关父进程 watch 防孤儿。

## 7. ⚠️ 两个推翻旧假设的事实

1. **仓库没有任何 Windows 启动器**（无 .ps1/.bat）——纯 macOS（arm64）产品。Windows 必须靠 Electron 壳补 → **坐实「DAY1-Clean + Electron 壳」路线**。
2. **设计语言 = 暗色霓虹朋克风**（acid 荧光黄绿 `#d2ff00`、电光蓝 `#2439ff`、硬阴影、斜切多边形 clip-path、像素字体 FusionPixel），与我们「暖白极简黑白红 SaaS」完全不同 → **MVP 决策：先复用，后换肤**。

---

## 8. 电商版改造范围（MVP，决策已锁定）

### 保留不动
- 6 板块：AGENT / 知识库 / 专家 / 风格 / 监控 / 新闻
- 网关反代 + WS 桥、vault 捕获、双 profile、安全基线、**设计语言（MVP 复用）**

### 改造 / 新增
| 项 | 做法 |
|---|---|
| dsh runtime | rc.6 → rc.7（挂载我们插件的硬前置）|
| profile（knowledge）| 挂 pounding-guard + mcp-pounding（patch 层）；vault 换/并入我们的 vault 布局 |
| 专家板块 | 对接 skill 能力卡（采集/图搜/选品/上架/类目/配置 = 专家卡）|
| 新闻板块 | AI 通用新闻 → 电商爆品情报（热销/热搜/汇率/政策，复用 skill queries/bestsellers）|
| 采集箱（新增）| worker `/api/v1/drafts` 商品卡片（图片+采购价+运费+利润）|
| 任务中心（新增）| worker `/task_status` 采集+上架任务 |
| 计算器（新增）| 跨境定价器（worker compute_price 公式，前端直算）|
| 网关扩展 | 加 worker/skill 桥（复用 pounding_mcp http_server 8901 逻辑）|
| 壳 | Swift → Electron（跨平台；网关/vault/web 不动）|

### 后 MVP
- 换肤 app.css → 我们的 design-tokens（暖白/黑/红），配合设计交付包统一视觉

## 9. 文档与源码索引（后续改造时快速定位）

- 前端：`web/app.js` rpc(245)/showPage(716)/sendPrompt(1867)/captureCurrentConversation(2502)/openDispatch(2621)/renderMonitor(3098)/loadNews(3269)/init(3063)
- 网关：`web/boujoy_server.py` HARNESS_ORIGINS(44)/_proxy(563)/_parse_record(829)/_save_record(858)/_capture_write(995)/do_GET(1108)/_ws_upgrade(1380)/do_POST(1419)
- 壳：`src/Boujoy-Harness-App/macos/BoujoyHarness.swift` ensureHarness(83)/ensureProductServer(99)
- 配置：`src/Boujoy-Harness-App/boujoy-config.template.json`
- 数据落点：`~/Library/Application Support/Boujoy/BoujoyHarness/`（news/缩略图/deleted-sessions）+ `$DSH_HOME`（会话/凭证）
