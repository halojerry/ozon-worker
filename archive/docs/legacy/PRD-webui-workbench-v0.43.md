# PRD — WebUI 运营工作台 v0.43：MXOU 登录 + 商品编辑板块（v1.0，2026-08-16）

> 状态：**v1.0 待批准** — 基于 2026-08-16 用户方向确认（MXOU 登录鉴权 + 商品编辑板块全量重传 + 生图学习）。
> 前置：`docs/PLAN-webui-v1.md`（T10b 编辑页）、`docs/PLAN-webui-workbench-v1.1.md`（M0-M2 数据脊柱/失败闭环，v0.42 已交付）。
> 实施执行：plan agent 输出 T0-Tn 拆分（见 `.omo/plans/`）。

---

## 一、背景与问题定义

v0.41-v0.42 交付了 WebUI（采集箱/商品编辑/店铺/任务/在售货架/生图工作台 6 页 + 数据脊柱）。两个核心缺口待 v0.43 补齐：

### 1.1 登录体验差（用户痛点）

现状 WebUI 登录 = **手动粘贴 MXOU API Key**（`Login.tsx` → `/api/v1/auth/verify`）。用户需要：
- 先去 api.mxou.cn 平台登录 → 创建 API Key → 复制 → 粘贴到我们 WebUI
- 登录后**看不到账户余额**（`auth_verify` 不返回 balance 数字，`_check_mxou_balance` 计算后被丢弃）
- 想拿新 Key / 看 Key 列表 → 只能再回 MXOU 平台操作

**用户提出**：直接用 MXOU 账号（api.mxou.cn 的 newapi 平台账号）登录我们 WebUI，登录后能看到余额、管理密钥——参考 newapi 的登录方式。

**可行性已验证**（2026-08-16 实测）：
- `POST https://api.mxou.cn/api/user/login` → 200（newapi 标准登录端点存在）
- `GET https://api.mxou.cn/api/user/self` → 401（登录后可查用户信息+余额）
- `GET https://api.mxou.cn/api/user/token` → 401（「获取秘钥」接口存在）
- `GET /api/status` → 返回 `HeaderNavModules`/`SidebarModulesAdmin` = **newapi 架构确认**

### 1.2 商品编辑板块不完整（对标 shopbang.cn/erp 的 editGoods）

用户参考 shopbang ERP 的 `editGoods?id=-1`（新建商品模式），指出我们编辑板块缺口：

| 能力 | shopbang editGoods | WebUI v0.42 现状 |
|---|---|---|
| 从零新建 | ✅ `?id=-1` | ❌ 无入口（后端 `POST /drafts` 已有但前端未接线） |
| 在线商品编辑 | ✅ 改完全量重传 | ⚠️ 仅改图（`POST /products/{id}/update_images`），改字段无入口 |
| 生图学习 | ✅ 套图/编辑联动 | ⚠️ 生图是独立页（ImageStudio），编辑页只有「AI商品套图」跳转按钮 |
| 图片上传/替换 | ✅ | ❌ 图片只读缩略图（改图是 URL 文本输入） |

**用户拍板方向**：**不做逐个字段的增量编辑，全部走「全量重传」**——对应 product 一样就是更新对应商品（`/v3/product/import` 是 upsert 语义，同 product_id 即更新）。**生图板块的能力要学习过来**（编辑页内嵌生图联动，不只是跳转按钮）。

## 二、需求定义

### F1. MXOU 账号登录（P0）

**F1.1** WebUI 登录页改为「MXOU 账号 + 密码」登录（同时保留 API Key 直登模式作为兜底——已发 Key 的存量用户、脚本用户）。

**F1.2** Worker 新增代理端点 `POST /api/v1/mxou/login`（避免前端直连跨域 + 复用限流/错误映射）：
1. 调 `POST https://api.mxou.cn/api/user/login`（username + password）→ 拿 session token
2. 调 `GET https://api.mxou.cn/api/user/self`（Bearer session）→ 用户信息 + quota（余额）
3. 调 `GET https://api.mxou.cn/api/user/token`（Bearer session）→ 该账号下 API Key 列表
4. 返回 `{ username, balance, keys: [{name, sk-前缀, created}], session_expires_at }`

**F1.3** WebUI 登录成功后：展示余额（侧边栏/顶栏常驻）+ 密钥列表（可复制/新建/吊销）——**用户不再需要离开 WebUI 去 MXOU 平台**。

**F1.4** 登录态与现有鉴权打通：登录拿到的 Key 自动写入本地存储（`ozon_webui_token`），后续请求走现有 `_authenticate_token` 链路，**不改 worker 鉴权核心**。

### F2. 商品编辑板块：全量重传更新在线商品（P0）

**F2.1** 泛化在线商品更新端点：`POST /products/{product_id}/update_images`（仅改图）→ **`PUT /api/v1/products/{product_id}`（全量重传）**：
- 请求体：完整 envelope 字段（title/images/attributes/variants/...）+ credential_id
- 复用现有机制：`product_task_index` 定位（task_id + credential_id + offer_id）→ `/v3/product/import` 全量重传 → 审核状态回填
- 响应：`{ ok, product_id, import_task_id, status, re_under_review }`

**F2.2** 在售货架（OnSale）行操作加「编辑商品」→ 复用 `EditDraft` 表单框架（mode=online）：
- 加载在线商品当前数据（product_task_index + 任务 payload）作为表单初值
- 编辑 → 保存 = 直接全量重传（不落 draft 表，除非用户「存为草稿」）
- 类目/品牌只读（架构上 Worker 职责，同 draft 模式）

**F2.3** 从零新建：`/products/new`（对标 shopbang `?id=-1`）→ 空白表单 → `POST /api/v1/drafts`（后端已有）创建草稿 → 可继续编辑/提交。类目选择 UI（description_category_id/type_id 可手填或从已有草稿复制）。

### F3. 商品编辑板块：生图学习（P1）

**F3.1** 编辑页内嵌「商品套图」区块（非跳转）：
- 复用 ImageStudio 的组件能力：原图选择 / 卖点快照 / 图配置（slot 计划）/ 生成进度 / 新旧对比
- 从编辑页上下文初始化（draft images + title + attributes 作为卖点输入）
- 生成结果实时回填编辑页图片列表（全量重传时带上新图）

**F3.2** ImageStudio 组件重构为可复用（抽 `ImageStudioEmbed`），独立页保持（路由 `/image-studio` 不变）。

### F4. 非功能性

- **安全**：MXOU 密码只在登录请求中出现一次，worker 不落库不日志；session token 仅内存短暂持有（TTL 60s）或加密落地；密钥列表脱敏（只显示 `sk-` 前 6 位 + 尾 4 位）
- **租户隔离**：新端点全部走 `_authenticate`（现有链路），跨租户 404
- **兼容**：`POST /products/{id}/update_images` 保留（OnSale 改图弹窗继续用）；API Key 直登保留
- **不新增基础设施**：无独立前端服务、无新表（若需 session 存储用内存 + 现有 PG）

## 三、验收标准（DoD）

### F1 验收
- [ ] WebUI 登录页有「账号密码登录」tab + 「API Key 登录」tab
- [ ] 账号密码登录成功 → 自动进工作台，侧边栏显示余额（¥ 或 $ 按 MXOU 返回）
- [ ] 密钥管理区列出账号下所有 Key（脱敏），可复制完整 Key / 新建 / 吊销
- [ ] 登录态与现有 token 鉴权打通（后续 API 请求带 `Bearer` 通过 `_authenticate_token`）
- [ ] 错误映射：密码错误 → 明确报错；网络失败 → 可重试提示；MXOU 平台不可达 → 降级提示
- [ ] 单测：mxou login 代理端点（mock MXOU API）+ WebUI 登录流程（组件测试）
- [ ] 存量 API Key 直登仍可用（回归）

### F2 验收
- [ ] `PUT /api/v1/products/{id}` 全量重传：改 title/images/attributes 后重传成功，`status` 正确回填（approved / pending_moderation）
- [ ] OnSale 行「编辑商品」→ 复用编辑页表单，加载在线数据，保存 = 全量重传
- [ ] `/products/new` 从零新建：填表单 → 创建草稿 → 采集箱可见 → 可编辑/提交
- [ ] 单测：全量重传端点（mock ozon_post）+ 新建草稿端点（现有 create_draft 回归）
- [ ] 回归：`update_images` 改图弹窗仍可用

### F3 验收
- [ ] 编辑页内嵌生图区块：从草稿上下文初始化、生成进度、结果回填图片列表
- [ ] `/image-studio` 独立页仍可用（回归）
- [ ] 单测：ImageStudioEmbed 组件渲染/上下文传递（组件测试或 smoke）

### 全局
- [ ] worker 全量测试 ≥894 passed / skill ≥493 passed / webui build + tokens:validate 绿
- [ ] 版本四源 0.43.0 + CHANGELOG

## 四、边界与不做（Out of Scope）

- ❌ 不做 Ozon 商品增量字段编辑（只做全量重传——用户拍板）
- ❌ 不做定时上架真实调度器（v0.42 仍是 stub，后续版本）
- ❌ 不做类目选择器（新建时仅手填 ID 或从已有草稿复制；类目匹配仍是 Worker 职责）
- ❌ 不做图片文件上传端点（改图仍走 URL 输入；COS 上传链路不在本版）
- ❌ 不改 worker 鉴权核心（`_authenticate_token` / Supabase tokens 表不动，MXOU 登录只是「登录入口」扩展）
- ❌ 不做多账号切换 / 记住密码 / 注册（MXOU 平台负责注册）

## 五、风险与对策

| 风险 | 对策 |
|---|---|
| MXOU login/self/token 响应结构与 newapi 标准有差异 | 实施第一步先用真实账号探测（用户提供测试账号），按实测结构开发；结构不匹配时降级「仅 API Key 直登」 |
| session token 有效期短（newapi 默认 7 天） | 登录时一次换取，前端每次启动时静默 re-login（账号密码存 localStorage 加密？不存——改为 session 过期后要求重新登录） |
| 全量重传把字段改坏（如误改属性） | 重传前展示 diff 确认；响应带 `images_filtered` 类字段提示被过滤项 |
| 新建草稿缺必填字段（weight/dimensions/purchase_cost） | 表单必填校验 + draft_sanity 入队防线兜底（现有） |
| 编辑页内嵌生图增加页面复杂度 | ImageStudioEmbed 按需懒加载；失败不影响编辑功能 |

## 六、决策记录

| # | 决策 | 依据 |
|---|---|---|
| D1 | 在线商品编辑 = **全量重传**（不做增量字段编辑） | 用户拍板；`/v3/product/import` 是 upsert 语义，同 product_id 即更新 |
| D2 | MXOU 登录 = worker 代理端点（前端不直连） | 避免跨域 + 复用限流/错误映射/日志 |
| D3 | 登录拿到的 Key 复用现有 `_authenticate_token` 链路 | 不新增鉴权体系，存量逻辑零改动 |
| D4 | API Key 直登保留（双登录模式） | 存量用户/脚本用户兼容 |
| D5 | 类目/品牌编辑只读 | 架构边界：类目匹配/品牌是 Worker 职责（v0.21+ 约定） |
| D6 | 版本 0.43.0（继续 0.4x 迭代） | 用户拍板 |

## 七、版本与里程碑

| 里程碑 | 内容 | 验收 |
|---|---|---|
| M1（P0） | F1 MXOU 登录 + 余额/密钥展示 | F1 验收全过 |
| M2（P0） | F2 全量重传端点 + OnSale 编辑 + /products/new | F2 验收全过 |
| M3（P1） | F3 生图内嵌 + ImageStudio 重构 | F3 验收全过 |
| M4 | 全量回归 + 版本 0.43.0 四源 + CHANGELOG + 冒烟 | 全局验收全过 |
