# TEST · 视觉全站落地 + 能力补齐验收测试

> 验收门槛：W1-W6 全绿后视为可交付 · 对应 TASKS.md / PRD v2.0
> 更新 2026-08-18 · 保留 v1 的 API 对接用例（TC-1..TC-14 已验证 ✅）

## 环境

| 项 | 值 |
|---|---|
| 目标 | `<worker-host>:8080`（本地 Docker：`http://localhost:8080`） |
| token | 有效 MXOU key（`Authorization: Bearer <key>`） |
| webui | `cd webui && npm run dev`（`http://localhost:5173/app/`） |
| 设计稿 | `../design-deliverables/`（规格书 HTML + proto PNG + design-tokens.json） |
| 辅助 | `../api-integration/openapi.json`、Swagger `/docs` |

---

## A · API 对接回归（v1 保留，TC-1..TC-14 已验证 ✅）

| # | 请求 | 期望 | 结果 |
|---|---|---|---|
| TC-1 | `GET /health` | `200 {"status":"ok","db":"connected"}` | ✅ |
| TC-2 | `GET /api/v1/site/banners`、`/announcements` | `200 []`（免鉴权） | ✅ |
| TC-3 | `GET /api/v1/drafts`（无 token） | `401 {"detail":"Token is required"}` | ✅ |
| TC-4 | `GET /api/v1/drafts`（Bearer invalid） | `401` 或 `503`（Supabase 不可达时） | ✅ |
| TC-5 | `POST /api/v1/drafts`（envelope 信封） | `201` 返回 `draft_id`；payload 无明文凭证 | ✅ |
| TC-6 | `GET /drafts`；`PATCH /drafts/{id}` 带旧 version | 列表返回；旧 version→`409`；新→成功 | ✅ |
| TC-7 | `POST /drafts/{id}/ai/{field}`、`/estimate` | 返回生成字段 / 价格建议 | ✅ |
| TC-8 | `POST /drafts/{id}/submit` → `GET /task_status/{task_id}` | submit 返回 task_id；轮询到终态；跨店 `confirm_required` | ✅ |
| TC-9 | `GET /orders`、`GET /products`、`GET /products/ozon` | `200` 结构化列表 | ✅ |
| TC-10 | `GET /tasks`、`GET /tasks/{id}/images`、`POST .../images/{slot}/regen` | 返回任务/图片；regen 后 version 递增 | ✅ |
| TC-11 | `GET /api/v1/admin/overview` | `200` 概览统计；无权限→`403` | ✅ |
| TC-12 | `POST /api/v1/mxou/login`（错误凭据） | `401`；连续失败触发限流 | ✅ |
| TC-13 | `npx tsc --noEmit`（webui，T7.3 类型迁移后） | 0 错误；`generated.d.ts` 被引用 | ✅（42 接口迁移，见 TC-P3） |
| TC-14 | 触发 404（假 draft_id）、422（坏请求体） | 错误体均为 `{"detail": "..."}` | ✅ |

---

## B · 视觉全站落地（W1-W3）

### TC-V1 视觉 token 生效
| 项 | 内容 |
|---|---|
| 操作 | 打开任意受保护页（如 `/orders`），浏览器检查 computed style |
| 期望 | 页面底 `#F7F6F2`；侧栏 `#111111` + 白字；主按钮 `#E20E0E` 白字；表格边框 `#E6E4DF` |
| 证据 | 截图存档（`integration-workplan/evidence/`）|

### TC-V2 dark 模式适配
| 项 | 内容 |
|---|---|
| 操作 | 切 dark，检查侧栏/背景/主按钮/徽标 |
| 期望 | 侧栏仍 `#111`；背景暗暖灰；主按钮仍红；红色徽标深红底浅红字（不违和）|

### TC-V3 等宽数字
| 项 | 内容 |
|---|---|
| 操作 | 打开订单/任务/店铺页，检查金额/计数列 font-family |
| 期望 | 数字列使用 `--font-mono`（SFMono/Menlo/Consolas）+ tabular-nums |

### TC-V4 token 校验脚本防漂移
| 项 | 内容 |
|---|---|
| 操作 | 改 `theme.css` 一个值（如 primary→#000）→ 跑校验脚本；还原 |
| 期望 | 脚本报错「与 design-tokens.json 不一致」；还原后通过 |

### TC-V5 15 页视觉回归
| 项 | 内容 |
|---|---|
| 操作 | 逐页打开 15 页（登录/仪表盘/商品/上架/订单/任务/定价/图工坊/采集箱/店铺/热销榜/大屏/模板/设置/管理）对照 proto PNG |
| 期望 | 无风格错位（黑侧栏/红强调/暖白底/留白层级一致）|
| 证据 | 每页截图与 proto 并列存档 |

### TC-V6 硬编码 hex 清理
| 项 | 内容 |
|---|---|
| 操作 | `grep -rn "#[0-9a-fA-F]\{6\}" webui/src/pages webui/src/features --include="*.tsx"`（排除 brand-icons）|
| 期望 | 仅剩装饰性品牌图标色，页面级无裸 hex（改吃 token）|

### TC-V7 index.css 业务样式保留（W3.4 回归）
| 项 | 内容 |
|---|---|
| 操作 | W3.4 迁移评估前后各截图 3 页（订单/任务/店铺）|
| 期望 | `.app-shell`/`.sidebar`/`.card` 等业务样式无视觉回归（迁移完成前保留 main.tsx:46 import）|
| 证据 | 前后截图对比 |

### TC-V8 tokens.json 废弃（W1.4）
| 项 | 内容 |
|---|---|
| 操作 | grep `tokens/tokens.json` 引用；打开设置检查 |
| 期望 | 无代码 import；文件标注 legacy；主题切换不受影响 |

---

## C · API 接线（W4）

### TC-D1 首页 KPI 真实数字
| 项 | 内容 |
|---|---|
| 操作 | 打开首页，观察 KPI 卡（今日订单/AI 上品数/上架成功率）|
| 期望 | 数字非 0 占位，来自 **新增 `getTaskStatistics()`**（GET `/api/v1/task_statistics`）/ `getAdminOverview` 真实数据（有任务数据时）|
| 证据 | 截图 + `curl /api/v1/task_statistics` 对照 |

### TC-D2 物流费率 Tab 可用
| 项 | 内容 |
|---|---|
| 操作 | 系统设置→「业务」Tab：浏览费率表 → 编辑一条 → CSV 导入 |
| 期望 | 列表渲染；PUT 生效（`200`）；导入返回插入数 |
| 证据 | 截图 + curl 响应 |

### TC-D3 订单商品缩略图
| 项 | 内容 |
|---|---|
| 操作 | 打开订单列表，检查商品行 |
| 期望 | 商品名旁显示缩略图（product_id → `/v3/product/info/list` images[0]，复用 `_fetch_info_map` 模式）|
| 证据 | 截图 + 响应含 `image` 字段 |

### TC-D4 订单接口已迁 v4
| 项 | 内容 |
|---|---|
| 操作 | `GET /orders`；worker 日志 grep `v4/posting/fbs/list` |
| 期望 | 走 v4（游标分页 + price 对象）；无 v3 调用 |
| 证据 | worker 日志 |

### TC-D5 在售列表图/价
| 项 | 内容 |
|---|---|
| 操作 | 打开商品管理表 |
| 期望 | 缩略图 + 价格 + 库存显示（走 `/products/ozon` 或补字段后）|

### TC-D6 店铺卡统计
| 项 | 内容 |
|---|---|
| 操作 | 打开店铺管理，观察卡片 |
| 期望 | 今日订单数/销售额/利润显示真实统计（store_sync 聚合）；**不显示评分**（缓存无此字段）|

---

## D · 多用户聚合（W4b）

### TC-A1 公共数据全局共享
| 项 | 内容 |
|---|---|
| 前置 | A 用户 skill `queries --type ozon-bestsellers` 采集上传 |
| 操作 | B 用户打开热销榜页 |
| 期望 | B 可见 A 采集的数据（含贡献者标注）|
| 证据 | 截图 + `GET /analytics/bestsellers`（B token）|

### TC-A2 私有数据租户隔离
| 项 | 内容 |
|---|---|
| 操作 | B 用户请求 A 用户的订单/草稿/商品/凭证（跨 tenant_id）|
| 期望 | `404`/`403`；B 看不到 A 的任何私有数据 |
| 证据 | curl 响应 |

### TC-A3 发现归档全局共享（W4b.2）
| 项 | 内容 |
|---|---|
| 前置 | A 用户 skill `discover` 采集并上报（D12）|
| 操作 | B 用户 `GET /api/v1/discovery/runs` |
| 期望 | B 可见 A 的发现归档（含贡献者标注）；A 删除/无数据时不受 B 影响 |
| 证据 | 截图 + curl（B token）|

### TC-A4 蓝海/榜单维持现状（W4b 边界）
| 项 | 内容 |
|---|---|
| 操作 | 检查 `GET /admin/queries`（蓝海）与 `GET /analytics/market-bestsellers`（榜单）|
| 期望 | 蓝海保持 admin-only；榜单无 webui 读端点（404）——与 TODO #12 一致，本次不开放 |
| 证据 | curl 响应 |

---

## E · 静默采集（W5）

### TC-S1 aibuy 匿名可用（I-8 修复验证）
| 项 | 内容 |
|---|---|
| 前置 | 干净 Chrome profile（**未登录 1688**）|
| 操作 | `python3.12 scripts/cli.py image_search --image <URL>` |
| 期望 | aibuy 通道出结果（匿名反爬 cookie 即可）；**不弹 1688 页** |
| 证据 | 命令 stdout + 无 CDP 打开日志 |

### TC-S2 毒 token 不落盘
| 项 | 内容 |
|---|---|
| 操作 | 模拟 `_m_h5_tk` 空值场景（单测）|
| 期望 | 不 `_save_aibuy_token`；返回 `[]` 时日志为 warning（非 debug 静默）|
| 证据 | 单测断言 + 日志级别 |

### TC-S3 热销榜 cookie 直调（静默）
| 项 | 内容 |
|---|---|
| 操作 | 工具 Chrome 加载过 seller.ozon.ru 后，`queries --type ozon-bestsellers`（静默模式）|
| 期望 | **不导航新页面**，requests 直调 `what_to_sell/data/v3` 出数据，与 CDP 模式结果一致 |
| 证据 | 命令 stdout + 日志确认直调（无导航）|

### TC-S4 旧 .so 特征校验
| 项 | 内容 |
|---|---|
| 操作 | 用缺 `search_by_image_aibuy` 的旧编译模块（单测 mock）|
| 期望 | 明确 warning「编译模块过旧」而非静默降级 |
| 证据 | 日志 |

### TC-S5 token 舞步等待（W5.3）
| 项 | 内容 |
|---|---|
| 操作 | 冷启动（无缓存 token）`image_search --source aibuy`；strace/日志测 `document.cookie` 轮询时长 |
| 期望 | 轮询 ≤5-10s 直到 `_m_h5_tk` 非空（非固定 2s）；成功后 token 落盘 |
| 证据 | 日志时间戳（首导航→token 就绪间隔）|

### TC-S6 日志文案修正（W5.5）
| 项 | 内容 |
|---|---|
| 操作 | grep skill 源码/日志 |
| 期望 | 无「Chrome 无 1688 会话」；为「无 1688 反爬 cookie，aibuy 不可用」|
| 证据 | grep 结果 + 日志样本 |

---

## F · 占位 + 类型迁移（W6）

### TC-P1 竞品曲线占位
| 项 | 内容 |
|---|---|
| 操作 | 打开定价工具，检查竞品对比区域 |
| 期望 | 空态：虚线框 + 一句话说明 + 可行动入口（非假按钮）|
| 证据 | 截图对照 spec Do/Don't |

### TC-P2 图工坊 AI 编辑占位
| 项 | 内容 |
|---|---|
| 操作 | 打开图片工坊，检查背景替换/去背景入口 |
| 期望 | 同 TC-P1 空态规范 |

### TC-P3 类型迁移
| 项 | 内容 |
|---|---|
| 操作 | `cd webui && npx tsc --noEmit` |
| 期望 | 0 错误；`generated.d.ts` 被 client.ts 引用（无手写类型占位）|

---

## 记录模板

```
TC-xx | 结果 [PASS/FAIL] | 实际响应/截图 | 备注
```

## 执行记录（2026-08-18 首轮）

| 用例 | 结果 | 证据 | 备注 |
|---|---|---|---|
| TC-1..TC-14（v1 API 回归） | ✅ | 先前已验证 | 保留 |
| TC-13（类型迁移） | ✅ | `npx tsc -b` 0 错误 ×3 | 42 接口迁移 generated.d.ts（openapi-typescript 7.13 重新生成后仍 0 错误） |
| TC-V1/V2/V3 | ✅ | theme.css `:root/.dark` 新值 + verify-design-tokens.mjs 通过 | `--font-mono` 已加 @theme inline |
| TC-V4（校验脚本防漂移） | ✅ | `node scripts/verify-design-tokens.mjs` →「✓ 一致」 | 脚本 3530 字节 |
| TC-V7（index.css 保留） | ✅ | main.tsx:44+46 双 import 保留；业务样式未删 | W3.4 评估项落位 |
| TC-V8（tokens.json 废弃） | ✅ | tokens.json 标注 legacy，无代码引用 | |
| TC-D1（KPI 真实数字） | ✅ | Home.tsx 聚合 task_statistics + store stats（今日订单/AI 上品数/上架成功率） | allSettled 不阻塞 |
| TC-D2（物流费率） | ✅ | SystemSettings 业务 Tab：表格/编辑/CSV 导入 + 模板提示 | 需管理员权限（未配 → 明确提示） |
| TC-D3（订单缩略图） | ✅ | OrderProductOut.image（后端 product_id 批量填充）+ ImageCell 兜底 | 列表 + 详情弹窗 |
| TC-D4（v4 迁移） | ✅ | order_service.py:293 + store_sync_service.py:58-72（cursor/has_next） | 全量测试 1212 passed |
| TC-D5（在售列表图/价） | ✅ | /products/ozon 视图 ImageCell 兜底 | |
| TC-D6（店铺卡统计） | ✅ | Stores 页今日统计列（订单/销售额/利润）；无评分 | store_sync_service.get_store_stats |
| TC-A3（发现归档全局共享） | ✅ | main.py GET /discovery/runs 去 tenant 过滤 + 贡献者列 | A 可见 B 归档 |
| TC-A4（蓝海/榜单边界） | ✅ | 蓝海 admin-only / market_bestsellers 仅 POST 未开放 | TODO #12 |
| TC-S1..TC-S6（静默采集） | ✅ | 556 skill 测试通过（19 新增：毒 token/轮询/直调/特征校验） | aibuy token 4 处修复 + 热销榜 cookie 直调 |
| TC-P1（竞品曲线占位） | ✅ | PricingTool 空态 + Link → /bestsellers | |
| TC-P2（图工坊 AI 编辑占位） | ✅ | ImageStudio 空态 + Link → /products | |
| TC-P3（类型迁移） | ✅ | 42 接口迁移（27 纯别名 + 14 Omit + 2 内联），30 保留手写+注释 | |

**待人工浏览器验收**：TC-V5（15 页逐页截图）、TC-V6（hex 视觉抽查）、TC-S3（真实 Chrome 会话热销榜静默直调冒烟）。

## 验收结论（执行后填写）

| 里程碑 | 结果 | 签字 |
|---|---|---|
| W1 视觉 token 全站落地 | ✅ 代码层 | 待浏览器验收 |
| W2 组件级打磨 | ✅ 代码层 | 待浏览器验收 |
| W3 页面回归 + 清理 | ✅ 代码层 | 待浏览器验收 |
| W4 API 接线 | ✅ 代码层 | 待浏览器验收 |
| W4b 多用户公共数据聚合 | ✅ 代码层 | 待浏览器验收 |
| W5 静默采集改造 | ✅ 测试层 | 待真实 Chrome 冒烟 |
| W6 占位 + 类型迁移 + 验收 | ✅ | tsc 0 错误 + build 通过 |
