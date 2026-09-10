# 设计 — B2-β / BL-17：租户过滤集中化（A9 S12-01）

> 状态：待评审 ｜ 依据：A9 §4 Q12.1 / S12-01（P1）｜ 基线 v0.74.0 @ 7a13d7ab
> 范围：worker 读端点与查询层的租户过滤执行方式；不改鉴权（token 语义）、不改租户解析（resolve_tenant）。

## 1. 背景与证据

- **现状模式**：租户解析集中在 `services/tenant_service.py:39-72`（resolve_tenant，60s LRU + fail-closed 503），但**过滤下推全靠各端点手工把 tenant_id 拼进查询**——main.py 中 `resolve_tenant` 18 处调用点各自负责。
- **复发实证**：v0.73 一批修了四处端点租户漂移（error_reports/forensics/categories/attributes 用了 `_key_user_id` 哈希租户，生产 tenant 是 Supabase user_id 整数如 "28"——哈希租户查不到任何行）。**该模式每新增一个读端点就重掷一次骰子**。
- **结构性缺口**：`category_match_log`/`attr_match_log` 无 tenant_id 列（model.py:369-431），读保护完全依赖经 ozon_product_tasks 的 join；任何未来直读审计表的端点（报表/导出）即跨租户泄漏。task_status 老数据无租户列宽容放行（main.py:1879-1896，S12-02）。
- **设计内共享面（guard 必须可豁免）**：category_mapping（W11 全局知识）、category_commission、蓝海/榜单读面（v0.57 拍板全局共享）、attribute/dictionary 缓存（全局）。

## 2. 方案选项

### 选项 A：FastAPI dependency 注入 request.state.tenant + 查询 guard 函数（推荐）

路由声明 `tenant: str = Depends(get_tenant)`，dependency 内解析 token（body 或 header）→ `request.state.tenant`；service 层配一个 guard 入口（如 `tenant_where(table_alias) -> SQL 片段 + params`），所有租户表查询必须经它取 where 条件。
- ✅ 与现有 body-token 鉴权模型兼容（Starlette 缓存 `request._body`，dependency 可安全重复读）；显式、可测、逐端点渐进迁移。
- ❌ 仍是 opt-in——新端点忘用 dependency 就回到现状（需 CI 断言补位，见 §4）。
- ❌ text() 手写 SQL 居多（非 ORM），guard 只能约束「取条件的方式」，不能自动注入。

### 选项 B：SQLAlchemy with_loader_criteria 全局自动过滤

`event.listens_for(Session, "do_orm_execute")` + `with_loader_criteria(TenantModel, ...)` 对带 tenant_id 列的 ORM 实体自动注入过滤。
- ✅ 真·自动，覆盖所有 ORM select。
- ❌ **对本仓库覆盖面是幻觉**：热路径查询（task_processor 认领、取证聚合、日志 join）几乎全是 `text()` 裸 SQL，ORM criteria 完全不生效——上了它反而给人「已全局防护」错觉。
- ❌ 全局共享表（category_mapping 等 45 表中约 1/3 无 tenant_id）需维护 skip 清单，漏列即 500。

### 选项 C：路由装饰器 @tenant_scoped

装饰器包 handler，前置解析 + 后置断言（响应体含他租户字段即拒）。
- ✅ 集中点清晰。
- ❌ 与 FastAPI dependency 机制重复造轮子；响应体断言对 list 端点脆弱（分页/聚合形状各异），易漏。

**取舍结论**：B 自动性最好但对 text() 主场无效，属伪解；C 与 A 能力等价但更绕。选 A，用「CI 断言测试」补 opt-in 的漏装面。

## 3. 推荐方案与分期

**Phase 1（地基，~1 天）**
1. `services/tenant_dependency.py`：`get_tenant(request) -> str` dependency（复用 resolve_tenant 与其 60s 缓存/fail-closed 语义，零重复实现）；token 来源兼容现有两种（body 字段/header），行为与现端点逐字节一致。
2. `utils/tenant_guard.py`：`tenant_eq(column, tenant) -> TextClause`（唯一 where 构造入口）+ 全局共享表白名单常量（取自 A7 §1 归属列「全局/用户/审计」口径）。
3. 新代码规约写入 AGENTS「纪律」：新增租户面读端点必须用 `Depends(get_tenant)` + guard 取 where。

**Phase 2（存量收敛 + 门禁，~2 天）**
4. main.py 18 处 resolve_tenant 调用点逐一迁移到 dependency（行为不变，含 v0.73 已修四处回归锁定）。
5. 两张审计表（category_match_log/attr_match_log）补 tenant_id 列：写入侧同事务带上（listing/attr 写点各一处），历史行按 task join 一次性回填（同 S12-01 建议）。
6. CI 断言测试（仿 test_compile_lists.py 先例）：枚举 openapi 读端点 → 断言租户表端点签名含 get_tenant dependency；grep 断言「SELECT ... FROM category_match_log 无 join ozon_product_tasks 且无 tenant 条件」为零。

**Phase 3（收尾）**
7. task_status 宽容读改 404（S12-02，回填完成后）；forensics/get_decrypted 等 404 防线回归为 guard 产物而非散点手写。

## 4. 验收探针

- **跨租户矩阵测试**：双租户夹具遍历全部 GET 读端点（drafts/forensics/error_reports/orders/...），断言 B 租户对 A 租户资源恒 404/空集——该测试进 CI 即「漂移复发」的永久负反馈。
- **门禁即探针**：Phase 2 的 dependency 枚举断言测试红 = 有端点绕过集中租户面。
- **运行时**：审计 SQL 日志抽样「无 tenant 条件的租户表查询」告警（A9 S12-01 原探针）；跨租户 404 率仪表（应恒为 100% 的拒绝率而非泄漏）。
- **回填核账**：`SELECT count(*) FROM category_match_log WHERE tenant_id IS NULL` 趋零。
