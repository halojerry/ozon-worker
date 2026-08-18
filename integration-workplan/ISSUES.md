# ISSUES · 已知问题与风险

> 更新 2026-08-18 · 状态：`open`/`closed` · 全部问题经代码/文档/逆向核实，含行号证据

## 已确认问题

### I-1 三套并行 token 系统在打架 [open · 对应 W1]
- **现象**：webui 存在 4 处 token 并存且互相独立：
  - `design-deliverables/design-tokens.json`（新设计稿唯一事实源，权威）
  - `src/styles/theme.css` `:root`（硬编码 OKLCH，活跃但无来源标注）
  - `src/tokens/tokens.json`（Tokens Studio 格式，声称有 sync/validate 脚本）
  - `src/index.css` `:root` + **3519 行业务样式**（`.app-shell`/`.sidebar`/`.card`/`.btn` 等 36+ 选择器，`main.tsx:46` 在 `styles/index.css` 之后加载覆盖 body 样式）——**非纯 legacy**，仅 `.login-page` 之外仍被全站使用
- **根因**：webui 从 NewAPI 模板 fork（package.json 名仍 `newapi-web`），模板自带 token 体系；中途引入 Tailwind v4 + shadcn 未清理；新设计稿又加一本。
- **处理**：W1 收敛——`design-tokens.json` 为唯一事实源 → `theme.css` 生成 + 校验脚本；废弃 `tokens.json`；`index.css` 业务样式**先评估迁移（W3.4），未迁移前保留 import**（不可整体删除）。

### I-2 `tokens:sync`/`tokens:validate` 脚本声称存在但从未实现 [closed · W1]
- **现象**：`src/tokens/tokens.json` 头注释 + `src/index.css:3-5` 声称「Tokens Studio 导出 → 替换 → 跑 npm run tokens:sync 自动重生成，tokens:validate 强校验」，但 `package.json` 只有 gen:route/dev/build/preview。
- **影响**：中。宣称的「唯一真相源」无落地机制，必然漂移。
- **处理**：W1.3 用真实校验脚本替代（读 design-tokens.json 断言 theme.css）。

### I-3 任务进度 `progress.percent` 已知失真 [closed · 前端已处理]
- **现象**：`progress.percent` 可能恒为 0 或失真（历史已知坑）。
- **处理（已实现）**：前端用 `stages_completed.length / 13` 计算百分比（`pages/Tasks.tsx:56` 注释 + `client.ts:600`），不信任 `percent` 字段。

### I-4 在售列表 `/products` 无图/价/库存字段 [closed · 对应 W4.5]
- **现象**：`ProductListItem`（schemas.py:652-663）只有 product_id/offer_id/task_id/draft_id/credential_id/created_at/moderation_status——**无 image/price/stock**。设计稿商品表要缩略图/价格。
- **可选路径**：前端改调 `/products/ozon`（`OzonProductOut.image/price/stock` 实时拉取）或后端补字段。
- **处理**：W4.5 前端改数据源（快）或补字段（慢）。

### I-5 店铺卡无统计字段（今日订单/销售额；无评分数据源）[closed · 对应 W4.6]
- **现象**：`CredentialOut`（schemas.py:307-320）只有 id/masked/shop_name/currency/is_default/status/时间戳——无订单/销售额。设计稿店铺卡片要求这些。
- **数据源**：`store_sync_service` 缓存含 `product_count/total_amount/commission_amount/profit`（store_sync_service.py:130-133），**无评分字段**——卡片不显示评分。
- **处理**：W4.6 新增店铺统计端点（`store_sync_service` 缓存聚合今日订单数/销售额/利润）。

### I-6 采集箱 `source` 默认 `skill`，webui 自建草稿无法区分来源 [closed · 对应 T7b.1]
- **现象**：`draft_service.create_draft`：`source = str(body.get("source") or "skill")`（draft_service.py:101）——webui 建的草稿也标 skill。
- **影响**：设计稿若想显示「来源徽标」无法区分。
- **处理**：T7b.1 webui `createDraft` 传 `source="webui"`（小改）。

### I-7 图片工坊 regen 依赖 params 快照，源 URL 死会降级 [closed · T7b.2 已验证]
- **现象**：`task_generated_images.params` JSONB 快照携带参考图 URL；1688 原图 URL（alicdn/COS）死亡时 regen 降级。
- **处理**：T7b.2 验证（2026-08-18）——worker 端 `image_service.py:207-242` 已用 `check_url_alive` 过滤死 URL 并返回 `images_filtered`（死 URL 列表），更新不失败；webui 暂无 update_images 调用点（商品编辑页属 W6 占位范围），后续接线时消费 `images_filtered` 提示即可。

### I-8 ⭐ aibuy 毒 token 导致静默降级 CDP（用户实测 v0.4x 仍开 1688 页面）[closed · 对应 W5]
- **现象**：`search_by_image_aibuy` 静默返回 `[]` → 降级 CDP → 一个个开 1688 页。用户观察「v0.4x 后仍显式开 1688 匹配」。
- **根因**（探针定位）：
  1. `_fetch_aibuy_cookies_from_chrome` 只查 key 存在不查 value（`ozon_image_search.py:545`）——`_m_h5_tk=""` 也通过
  2. **`_m_h5_tk` 值为空的 4-key dict 仍 truthy** → `_save_aibuy_token` 落盘（`:702-705`，`if not token_cookies:` 只拦全空 dict，拦不住空 value 的 dict）→ 6h 内每次 aibuy 都拿死 token
  3. `_mtop_request` 空 token 直接 `return {}`（`:571-573`）→ `[]` → CDP 接管
  4. 全程 `logger.debug`（`cloud_probe.py:3352` / `ozon_discovery.py:2189`）——日志不可见
- **处理**：W5.1-W5.5（校验 value / 死 token 不落盘 / 等 token 舞步 / 降级出声 / 文案修正）。

### I-9 aibuy 后端可能抓不到 Ozon 图（ir.ozone.ru 可达性）[closed · T7b.3 已验证]
- **现象**：aibuy 路径是 **1688 服务器** fetch 竞品图（`ir.ozone.ru`）；CDP 是**用户浏览器**本地抓图。曾怀疑 Ozon CDN 屏蔽国外 IP 时 aibuy 空而 CDP 成功。
- **处理**：T7b.3 验证（2026-08-18）——本机 curl `ir.ozone.ru` 403 / `ozonstatic.com` fake-IP TLS 失败，但这是**本地网络/代理特性**（Clash fake-IP 拦截），与 aibuy 无关（aibuy 走阿里出口）。**实测 `ir.ozone.ru` 竞品图 URL 可直接 aibuy 图搜成功，无需 COS 转存**。已移除 COS relay 过度设计；遇 aibuy 空结果走既有 CDP 兜底。

### I-10 编译 .so 无版本/特征校验，旧二进制静默降级 [closed · 对应 W5.7]
- **现象**：`ozon_image_search.py` 在 COMPILE_FILES（compile.py:30）；stub 加载器（compile.py:296-355）无版本检查。用户 dist 若为 v0.39 前编译，`import search_by_image_aibuy` 抛 ImportError 被 `except Exception`（cloud_probe.py:3352）吞掉 → 静默 CDP。
- **处理**：W5.7 stub 加特征检查，旧 .so 明确 warning。

### I-11 `/v3/posting/fbs/list` 已废弃 [closed · 对应 W4.4]
- **现象**：Ozon API 文档标注 v3 于 2026-06-01 停用，应迁 `/v4/posting/fbs/list`（游标分页 + price 对象）。worker 仍用 v3（order_service.py:177、store_sync_service.py:68）。
- **处理**：W4.4 随订单图改造一并迁移。

### I-12 webui→skill 采集桥不可行 [open · 架构边界]
- **现象**：skill 客户端本地（NAT 后），worker 只能被 skill 轮询（task_status/mappings/lookup），无回连通道。「webui 点按钮 → 客户机采集」需常驻 daemon 或 webhook 基础设施，**今天不存在**。
- **处理**：不设计进本次适配；若「按需采集」是必须，单独立客户端 agent 工作流。

### I-13 静默直调 seller.ozon.ru 内部端点 = ToS 灰区 [open · 合规]
- **事实**：毛子/上品帮已验证 `what_to_sell/data/v3` 端点本身不校验 Premium（前端 UI gate），绕过 = 改 Vuex store（毛子 inject.js）或拦截 premium/status 响应（上品帮 ozon_min.js）。**我们的 skill CDP 直调端点天然绕过前端 gate，无需任何绕过代码**。
- **风险**：静默直调内部端点属 ToS 擦边。我们是对外 SaaS，建议保留显式模式兜底 + 文档免责。
- **处理**：W5.6 cookie 直调实现 + 保留 CDP 兜底；文档标注合规边界。

### I-14 多用户数据池产品决策 [closed · 对应 W4b]
- **现象**：毛子/上品帮的护城河 = 中心化数据池（A 用户采集 B 用户共享）。我们当前单租户隔离（`contributed_by_token_id` 过滤）。
- **决策（已定）**：订单/商品/草稿/凭证/任务**严格租户隔离**；热销榜（`ozon_bestsellers`）与发现归档（`discovery_runs` GET）**全局共享**（保留贡献者标注）；**蓝海/榜单本次不开放**——`/admin/queries` admin-only、`market_bestsellers` 无读端点（仅 POST main.py:2220），需新读端点（TODO #12）。
- **处理**：W4b 实现 + 隔离测试锁定。

### I-15 误导性日志文案「Chrome 无 1688 会话」[closed · 对应 W5.5]
- **现象**：`ozon_image_search.py:703` 文案「Chrome 无 1688 会话」暗示需登录；实际 `_m_h5_tk` 是**匿名反爬 cookie**，与登录无关。曾误导「需要登录态」的错误认知。
- **处理**：W5.5 改「无 1688 反爬 cookie，aibuy 不可用」。

## 风险

### R-1 generated.d.ts 会过期
- 后端 schema 变更后需重新生成（`npx openapi-typescript openapi.json -o generated.d.ts`）。
- 缓解：T7.3 类型迁移 + 建议 CI 接入自动生成。

### R-2 真实 token 涉及生产数据
- 验证会触碰真实店铺/订单。缓解：先只读端点验证；写操作（submit/ship）用测试店铺。

### R-3 视觉全站生效的大改动面
- 改 `:root` 全站翻转，需完整视觉回归（15 页 + 登录 + 错误页 + 组件）。缓解：W2/W3 逐页对照 proto 截图。

### R-4 静默采集依赖工具 Chrome 常驻 [已缓解 · T7b.4]
- cookie 直调需 Chrome 会话 cookie 热（页面加载过即可，匿名可行）。缓解：`check` 命令做 cookie 就绪检测 + 明确提示（T7b.4，已实现——cli.py:787-800「1688 反爬 cookie」就绪检测 + 预热提示）。

## 观察（非问题）

- `GET /api/v1/store/health` 返回 `{"status":"unknown"}` 属正常——需带 client_id/api_key 才完整。
- `/api/{path}` 是 mxou.cn 代理通配，非真实业务端点。
- 毛子/上品帮均回传数据到自有后端（毛子 AES-CBC+HMAC 上传 `/api.chrome/check_data`）——我们 worker 已有等价的 skill→worker 上传链路（analytics/discovery/drafts），无需新增。
