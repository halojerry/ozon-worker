# PRD — 管理员面板（平台运营后台）

> 2026-08-16。竞品调研：上品帮「子账号管理/设备管理/VIP服务」、毛子ERP「VIP/充值/毛豆明细/设备管理」——但那些是**卖家视角的子账号**。我们的「管理员面板」是**平台运营者视角**：管理所有注册用户的余额、店铺、任务、密钥、用量。
> 现状基础：tokens 表鉴权、`_check_mxou_balance`（MXOU 真实余额判定）、`task_statistics`（全租户统计）、credentials 表（店铺，租户隔离）、task_processor。

## 一、背景与目标

### 1.1 问题
作为 SaaS 平台，运营者（管理员）目前**没有统一视图**查看：
- 有多少用户 / 各自余额 / 是否欠费
- 每个用户的店铺、上架任务量、成功率
- 平台整体运行状况（任务总量、成功率、失败分布）

目前这些数据散落在 PG 表（tokens/users/credentials/ozon_product_tasks）+ Supabase，无管理界面，只能 SQL 查。

### 1.2 目标（管理员面板 P0）
1. **平台概览**：用户数 / 店铺数 / 任务总数 / 成功率 / 今日任务（复用 task_statistics + 各表 COUNT）
2. **用户列表**：所有用户（id / 用户名 / 余额 / 状态 / 店铺数 / 任务数 / 最近活跃），可搜索
3. **用户详情**：单个用户的店铺列表 + 任务统计 + 密钥状态
4. **店铺列表**：所有店铺（跨用户，含归属用户 / 货币 / 校验状态）
5. **任务统计**：全租户成功率 / 失败分布（复用 task_statistics）

### 1.3 非目标（P1+）
- 用户管理操作（禁用/充值/改余额——需 Supabase 写权限，谨慎，下一期）
- 平台级配置（费率/类目映射维护）
- 数据大屏（P3）

## 二、设计

### 2.1 管理员鉴权
- **角色判定**：`tokens` 表加 `is_admin` 列（或 users 表 role='admin'），管理员 token 才能访问 `/api/v1/admin/*`
- 现有 `_authenticate_token` 返回 tenant_id；管理员端点在此基础上**额外校验 is_admin**
- 部署：`scripts/init_data.py` 或 SQL 手动标记首个管理员 token

### 2.2 API（`routes/admin_routes.py` + `services/admin_service.py`）
```
GET  /api/v1/admin/overview      → 平台概览（用户数/店铺数/任务数/成功率/今日）
GET  /api/v1/admin/users         → 用户列表（搜索/分页；含余额/店铺数/任务数）
GET  /api/v1/admin/users/{id}    → 用户详情（店铺 + 任务统计 + 密钥）
GET  /api/v1/admin/stores        → 店铺列表（跨用户，含归属）
GET  /api/v1/admin/tasks         → 任务统计（全租户）
```
- 全部走 Supabase `tokens`/`users`（用户信息）+ PG `credentials`/`ozon_product_tasks`（业务数据）
- 余额：对每个用户查 `_check_mxou_balance`（或批量缓存——MXOU API 有频率限制，按需查）
- 数据拼装：users 来自 Supabase，店铺/任务来自 PG，按 user_id 关联

### 2.3 WebUI
- 新页面 `pages/Admin.tsx`，路由 `/admin`
- **概览卡片**：用户数/店铺数/任务总数/成功率/今日任务
- **Tab 切换**：用户列表 / 店铺列表 / 任务统计
- 用户列表行：用户名/id/余额/状态/店铺数/任务数/最近活跃 + 「详情」弹窗（该用户店铺 + 任务统计）
- Layout 菜单「管理后台」（仅管理员可见——登录响应带 is_admin）

### 2.4 schemas
`AdminOverview` / `AdminUserOut` / `AdminUserDetail` / `AdminStoreOut` / `AdminTasksOut`

## 三、测试计划

### Worker
- `test_admin_service.py`：overview 聚合、用户列表拼装（mock Supabase + PG）、店铺跨用户、非管理员 403
- `test_admin_api.py`：鉴权（无 token 401 / 非管理员 403 / 管理员 200）

### WebUI
- build + tokens:validate

## 四、验收标准（DoD）
1. 管理员 token 可访问 /admin 全部端点；非管理员 403
2. 概览/用户/店铺/任务统计数据正确（真实 PG + Supabase）
3. WebUI 管理后台页可用（管理员登录后菜单可见）
4. worker 全量回归不破

## 五、实施顺序
T0 admin_service（overview/users/stores/tasks 聚合）→ T1 管理员鉴权（is_admin 列 + 校验）→ T2 admin_routes + schemas → T3 worker 测试 → T4 WebUI Admin.tsx + 路由 + 菜单 → T5 版本 0.51.0 + 回归 + 提交
