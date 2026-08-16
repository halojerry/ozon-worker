# 系统设置（运营配置中心）PRD — v0.55

> 2026-08-17。架构定调：**用户/充值/订阅走 api.mxou.cn（Supabase，复用 New API），业务数据走 worker 本地 PG**。
> 管理员唯一最高权限（Supabase users.role >= 10，New API RoleAdminUser/RoleRootUser）。
> 前置修复已落地（commit：管理员角色判定修复——Supabase 整数 role 100 兼容 + 查库数据源修正，worker 1105 passed）。

---

## 一、背景与目标

### 1.1 现状
- webui v0.54 照搬 mxou 完成，但**管理员系统设置是死链**（侧边栏 /system-settings/site 无路由）
- worker 有 admin 面板（用户/店铺/任务统计），**无站点运营/商业/引擎配置**
- 用户体系/充值/订阅**已完整存在 api.mxou.cn（New API）**：users.quota（余额）、subscription_plans（套餐，已有「月度套餐」）、topups/redemptions（充值/兑换码）、options（epay 支付配置已配：pay.mxou.cn）

### 1.2 目标
1. **修复系统设置死链** → 真实管理员配置中心
2. **站点运营**：Banner + 通告（worker PG 自建）
3. **商业**：订阅/充值**完全复用 New API**（不重建），webui 接入管理/购买页
4. **引擎配置**：提示词/运费费率/选品库（worker PG + config 自建）
5. **权限**：仅管理员（role>=10）可进系统设置；客户只管自己的店铺/任务/模板

### 1.3 非目标（已决策不做）
- 不重建充值/订阅/支付体系（复用 New API）
- 不做主账号/子账号体系（api.mxou.cn 无此概念，未来代理商再说）
- 不做 6 语言翻译
- auth_node raw REST 收敛（远期项，见 §五）

---

## 二、架构与权限模型

### 2.1 双库分工（已确认）
```
┌─ api.mxou.cn Supabase（用户/商业，零改动）──────────────────────┐
│  users.quota / tokens / subscription_plans / user_subscriptions │
│  topups / redemptions / options（epay 配置）                    │
│  请求：/api/user/*、/api/subscription/*、/api/option/*          │
└──────────────────────────────────────────────────────────────────┘
┌─ worker 本地 PG（业务，自建）───────────────────────────────────┐
│  site_banners / site_announcements（新表）                      │
│  logistics_rates（已有）/ config/*.json（已有）/                │
│  blue_ocean_queries（已有，补导入）                             │
└──────────────────────────────────────────────────────────────────┘
```

### 2.2 权限分层
| 角色 | 判定 | 能做什么 |
|---|---|---|
| 管理员（你，唯一） | Supabase role >= 10 | 全部系统设置（A/B/C）+ admin 面板 |
| 普通用户（客户） | role < 10 | 自己的店铺/任务/模板/余额（已有），**看不到系统设置** |

**鉴权实现**：系统设置路由走 worker `require_admin`（已修复，role>=10）。webui 侧 admin 守卫 `(user.role ?? 0) >= ROLE.ADMIN(10)`（已有）。

---

## 三、功能清单

### A. 站点运营（worker 自建）

**A1. Banner 管理**（新表 `site_banners`）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | int PK | |
| image_url | text | banner 图片 URL |
| link_url | text | 点击跳转（可空） |
| title | varchar(128) | 标题 |
| sort_order | int | 排序（小在前） |
| enabled | bool | 启停 |
| created_at / updated_at | timestamptz | |

API：`GET/POST/PUT/DELETE /api/v1/admin/site/banners`（require_admin）
展示：`GET /api/v1/site/banners`（公开，仅 enabled + 按 sort_order）→ webui 首页/登录页

**A2. 通告管理**（新表 `site_announcements`）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | int PK | |
| title | varchar(128) | 标题 |
| content | text | 内容（支持简单换行） |
| announcement_type | varchar(16) | banner（横幅）/popup（弹窗） |
| enabled | bool | 启停 |
| created_at | timestamptz | |

API：`GET/POST/PUT/DELETE /api/v1/admin/site/announcements`（require_admin）
展示：`GET /api/v1/site/announcements`（公开）→ 登录页横幅 / 首页弹窗

### B. 商业（复用 New API，webui 接入）

**B1. 订阅套餐管理**（复用，零后端开发）
- worker 不动。webui **接回 New API 的 `subscriptions` feature**（v0.54 删除的 `features/subscriptions/` 从 mxou 原版复制回 webui）
- 管理员编辑：`POST/PUT/PATCH /api/subscription/admin/plans`（创建/更新/启停）——走 New API 认证（webui 已登录 cookie）
- 用户购买：`GET /api/subscription/plans` + `POST /api/subscription/balance/pay`（余额购买）/ `epay/pay`（易支付拉起）
- 数据已在：`subscription_plans` 表（「月度套餐」¥99 已存在）

**B2. 充值**（复用，webui 可选接入）
- 管理员：`GET /api/user/topup`（充值记录）+ `POST /api/user/topup/complete`（补单）
- 用户：`GET /api/user/topup/info` + `POST /api/user/pay`（易支付）+ `POST /api/user/topup`（兑换码）
- **优先接 wallet 页**（`features/wallet/` 从 mxou 复制回），充值链路完全复用
- 支付配置（PayAddress/EpayId/EpayKey/PayMethods）：`PUT /api/option/`（New API RootAuth）——如需要可在系统设置加「支付配置」页，**但改动 New API 配置有风险，v0.55 先不做配置页，只接入展示**

**B3. 侧边栏死链修复**
- `use-sidebar-data.ts` admin 组「系统设置」→ 指向新的系统设置页（`/system-settings`，非 mxou 的 `/system-settings/site`）

### C. 引擎配置（worker 自建）

**C1. 提示词编辑**（`worker/config/*.json` 13 个）
- API：`GET /api/v1/admin/config`（列目录）+ `GET /api/v1/admin/config/{name}`（读内容）+ `PUT /api/v1/admin/config/{name}`（写，改前自动备份到 `config/backup/{name}.{ts}.json`，保留最近 5 份）
- 校验：JSON 合法性校验，非法拒绝写入
- 安全：**require_admin + 写前备份 + 返回备份路径（可回滚）**
- webui：系统设置子页「引擎配置」→ JSON 编辑器（textarea + 格式化/校验按钮）+ 回滚列表

**C2. 运费费率管理**（`logistics_rates` 表已有）
- API：`GET /api/v1/admin/logistics/rates`（列表，分页）+ `PUT /api/v1/admin/logistics/rates/{id}`（改单条）+ `POST /api/v1/admin/logistics/rates/import`（CSV 导入）
- 校验：必填字段（scoring_group/service_level/tpl_provider/weight 区间）校验
- webui：系统设置子页「运费费率」→ 表格编辑 + CSV 导入

**C3. 公共选品库**（`blue_ocean_queries` 表已有）
- API：`GET /api/v1/admin/queries`（列表，分页/搜索）+ `POST /api/v1/admin/queries/import`（CSV/JSON 导入，批量 upsert）+ `DELETE /api/v1/admin/queries/{id}`（删除）
- 导入格式：CSV（keyword/category/blue_ocean_score 等）或 JSON 数组
- webui：系统设置子页「选品库」→ 表格 + 导入/导出

---

## 四、worker 后端设计

### 4.1 新表（PG，`storage/database/shared/model.py` 追加）
```python
class SiteBanner(Base):  # __tablename__ = "site_banners"
    id, image_url, link_url, title, sort_order, enabled, created_at, updated_at

class SiteAnnouncement(Base):  # __tablename__ = "site_announcements"
    id, title, content, announcement_type, enabled, created_at
```

### 4.2 新路由（`worker/src/routes/`）
```
admin_site_routes.py   — /admin/site/banners|announcements（require_admin）
site_public_routes.py  — /site/banners|announcements（公开）
admin_config_routes.py — /admin/config/*（require_admin，提示词读写+备份）
admin_logistics_routes.py — /admin/logistics/rates*（require_admin）
admin_queries_routes.py   — /admin/queries*（require_admin）
```

### 4.3 复用现有
- 鉴权：`require_admin`（已修复 role>=10）
- PG：`get_engine()` + 现有 Base
- 日志：现有结构化 logger

---

## 五、测试验证方案

### 5.1 worker 单测（新增 ~20 用例）
| 模块 | 用例 |
|---|---|
| role 判定 | ✅ 已落地 11 用例（commit 前置修复） |
| site banners API | CRUD + 公开端点只返 enabled + require_admin 403 |
| site announcements API | CRUD + 公开端点 + type 校验 |
| config 读写 | 读/写/非法 JSON 拒绝/备份生成/回滚列表 |
| logistics rates | 列表/改单条/CSV 导入校验 |
| queries 导入 | CSV/JSON 导入 + 去重 + 删除 |

### 5.2 webui 验证
- `npm run build` 0 错误 + 系统设置各子页渲染无 JS 错误
- 管理员进入：role=100 → 可见系统设置；role=1 → 403
- 订阅/充值页接回后：plans 列表渲染（「月度套餐」）+ 余额购买流程

### 5.3 回归
- worker 全量 pytest（1105 基线不降）
- 既有 webui 页面不受影响（系统设置为新增独立路由）

---

## 六、审计方案

| 指标 | 当前 | 目标 |
|---|---|---|
| 系统设置死链 | 1 处（/system-settings） | 0（真实页面） |
| 站点运营 API | 0 | 10 端点（banner admin 4 + 公开 1 + 通告 admin 4 + 公开 1） |
| 引擎配置 API | 0 | 3 组（config/logistics/queries） |
| 订阅/充值 | 复用 New API | webui 可管理/购买 |
| worker 测试 | 1105 | ≥ 1125（+20） |
| 权限 | 管理员 403 bug（已修） | role>=10 全通 |

**门禁**：系统设置任何端点 require_admin；提示词写前必备份；公开端点只暴露 enabled 数据。

---

## 七、风险与决策

| 风险 | 缓解 |
|---|---|
| 提示词编辑影响线上 LLM 行为 | 写前备份 + 只允许管理员 + JSON 校验 |
| 接回 subscriptions/wallet feature 有依赖 | 从 mxou 原版完整复制（含依赖），build 验证 |
| 动 New API 配置（option） | v0.55 不做支付配置页，只读展示 |
| auth_node raw REST 双路径 | **远期**：收敛需保降级链，独立 PRD 评审 |

**已决策**：
- 订阅/充值/支付：**完全复用 New API**（零后端开发，webui 接回前端）
- 站点运营/引擎配置：**worker 自建**（业务数据隔离，不动 New API 库）
- 主/子账号：暂缓
- auth_node 收敛：远期

---

## 八、实施计划

| 阶段 | 内容 | 工期 |
|---|---|---|
| P0 | ✅ 前置修复（role 判定 + 查库修正） | 已完成 |
| P1 | A 站点运营：建表 + API + webui 页面 | 1 天 |
| P2 | C1 提示词编辑 + C2 运费费率 + C3 选品库 | 1.5 天 |
| P3 | B 商业：接回 subscriptions/wallet feature + 侧边栏修复 | 1 天 |
| P4 | 全量回归 + 版本 v0.55.0 + CHANGELOG | 0.5 天 |

**验收**：P4 完成时——系统设置 4 子页（站点运营/商业/引擎配置/选品库）全可用，worker ≥1125 passed，webui build 0 错误。
