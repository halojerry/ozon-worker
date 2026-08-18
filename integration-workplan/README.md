# Ozon Worker · API 对接工作包

> 面向接手对接的同事：一份可执行的工作计划与验收清单。
> 配套**材料包**在 `../api-integration/`（对接文档 + OpenAPI 规范 + TS 类型）。

## 📁 本文件夹内容

| 文件 | 内容 | 负责人视角 |
|---|---|---|
| `PRD.md` | 产品需求：对接目标、范围、用户故事、验收标准 | 产品/需求 |
| `PLAN.md` | 执行计划：阶段、里程碑、排期建议 | 项目经理 |
| `TASKS.md` | 任务分解：可勾选的执行清单 | 开发 |
| `TODO.md` | 当前待办：谁、何时、卡在哪 | 全员 |
| `ISSUES.md` | 已知问题与风险：对接中会踩的坑 | 开发/测试 |
| `TEST.md` | 测试计划与用例：验收怎么测 | 测试 |

## 🔗 关联文件（务必一起看）

| 关联文件 | 路径 | 用途 |
|---|---|---|
| **设计交付包** | `../design-deliverables/README.md` | 设计规范 HTML + tokens + 15 页原型图（UI 落地唯一事实源） |
| API 对接文档 | `../api-integration/API-INTEGRATION-GUIDE.md` | 端点/鉴权/模型/错误码 |
| 对接包入口 | `../api-integration/README.md` | 三步上手 |
| OpenAPI 规范 | `../api-integration/openapi.json` | 最新真相源（97 路径） |
| TS 类型 | `../api-integration/generated.d.ts` | 前端类型安全 |
| 前端现有对接 | `../webui/src/api/client.ts` | 1456 行，60+ 函数可参考 |
| 前端包清单 | `../webui/package.json` | 依赖（Bun + React 19 + TanStack） |
| 后端主应用 | `../worker/src/main.py` | 路由挂载与鉴权实现 |
| 后端路由模块 | `../worker/src/routes/*_routes.py` | 各资源端点真相源 |
| 后端模型 | `../worker/src/api/schemas.py` | 全部 Pydantic 模型 |

## 🏃 给接手的同事

```bash
# 1. 先读
open ../api-integration/README.md          # 三步上手
open PRD.md                                # 要交付什么

# 2. 服务就绪确认
curl http://<host>:8080/health             # 应返回 {"status":"ok","db":"connected"}

# 3. 对着 TASKS.md 逐项勾选
# 4. 做完用 TEST.md 验收
# 5. 卡住看 ISSUES.md
```

## 📌 当前状态速览（v2.0 已执行 ✅ 2026-08-18）

- 版本：`v0.57`（规划 v0.56.6 起）
- 端点：110+（OpenAPI **98 路径**，新增 `/stores/{id}/stats`）
- 鉴权：Bearer token（MXOU key），仅 5 个免鉴权端点
- **执行状态**：W1-W6 + W4b + T7b 全部落地——
  - 视觉 v2.0 全站（theme.css token 映射 + 校验脚本 + KPI 卡/表格 mono/空态 + hex 清理 + 登录页迁移）
  - API 接线（KPI 真实数字 / logistics Tab / 订单图 product_id 批量 / v4 迁移 / 在售图价 / 店铺统计）
  - 多用户聚合（热销榜 + 发现归档全局共享；蓝海/榜单不开放）
  - 静默采集（aibuy 毒 token 4 处修复 + 热销榜 cookie 直调 + .so 特征校验）
  - 类型迁移：client.ts **42 接口** → `generated.d.ts`（T7.3）
- 测试：worker **1212 passed** / skill **556 passed** / webui `tsc -b` 0 错误 + build 通过
- 遗留 TODO：15 页浏览器逐页截图验收（TC-V5） + 真实 Chrome 热销榜静默直调冒烟（TC-S3）

---

*工作包生成：2026-08-17 · 对应项目 v0.56.6*
