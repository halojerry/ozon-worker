# 端到端稳定性测试 · 问题收集台账

> 创建日期：2026-08-19
> 关联计划：`docs/TEST-PLAN-e2e-stability.md`
> 店铺：5423887 · 环境：本地 Docker · MXOU 预算 ¥100

---

## 问题字段说明

每条问题记录以下字段：

```
- ID：ISSUE-XXX（顺序编号）
- 板块：skill / worker / webui / 联动
- 类型：Gap / 缺陷 / 性能 / 体验
- 严重级：P0（阻断，必须修）/ P1（影响核心链路）/ P2（优化项）
- 用例：关联测试用例 ID（如 S1 / W4 / L3）
- 复现：命令/操作 + 输入
- 预期：应该怎样
- 实际：发生了什么
- 日志：报错/关键日志摘录
- 状态：open / investigating / fixed / wontfix
- 备注：
```

---

## 已知 GAP（预登记，测试中验证影响面）

### ISSUE-G1 · discover CSV 不进 webui
- **板块**：联动（skill → webui）
- **类型**：Gap
- **严重级**：P1
- **现状**：discover 的 CSV 只落本地 `data/discovery/*.csv`；`discovery_runs` 上报进了 PG 但 webui 无页面消费它。只有 `--to-box` 提交成 envelope 才能在采集箱看到。
- **状态**：open
- **验证方式**：S1+S3+U2 对照，确认「收集到的表格如何在 webui 看到」的断点。

### ISSUE-G2 · webui 无蓝海/货源/类目属性独立 UI
- **板块**：webui
- **类型**：Gap
- **严重级**：P2
- **现状**：三块功能都在 skill+worker，webui 无独立页面（蓝海只能看热销榜，货源/属性结果不展示）。
- **状态**：open

### ISSUE-G3 · 「编辑上架」半成品
- **板块**：webui + skill
- **类型**：Gap
- **严重级**：P1
- **现状**：商品管理「编辑」按钮未接线；后端 `GET /products/{id}/edit` 存在但 webui 未调用；skill 无编辑命令。
- **状态**：open
- **验证方式**：U 商品管理页点「编辑」，确认无响应。

### ISSUE-G4 · skill 批量并发弱
- **板块**：skill
- **类型**：性能
- **严重级**：P2
- **现状**：`batch_test` 串行 + `--delay`；`discover auto-submit` 仅 2 线程。对比 shopbang 的 max 3 队列 + max 8 任务并行。
- **状态**：open
- **验证方式**：S8 记录 batch_test 处理 N 个 URL 的耗时，对比并发上限。

---

## 测试中发现的问题（动态追加）

### ISSUE-001 · aibuy mtop 图搜参数错误（从未真正跑通）
- **板块**：skill
- **类型**：缺陷
- **严重级**：P1（静默图搜主通道失效，每次降级 CDP）
- **用例**：S6 / L2
- **复现**：`follow --ozon-url <任意Ozon链接>`，观察日志「aibuy image search 返回空，降级 CDP」
- **预期**：aibuy mtop 直调秒级返回结构化结果（免浏览器）
- **实际**：① `saved_at`(float) 混入 cookie dict → requests 调 str.startswith() 崩溃；② `image.upload` 传 `imageUrl` 但 API 要 `imageBase64`；③ 换 `imageBase64` 后 GET 414（243KB base64 塞 query string 超长）
- **日志**：`'float' object has no attribute 'startswith'` / `FAIL_SYS_BIZPARAM_MISSED::缺少业务参数imageBase64` / `HTTP 414`
- **状态**：**fixed**
- **修复**（2026-08-19）：
  1. `_read_aibuy_token` 返回前剥离 `saved_at`（float 不再混入 cookie dict）
  2. `_mtop_request` 新增 `method="POST"`，data 走 form body（签名不变）
  3. `_aibuy_image_upload` 改为「下载图 → base64 → POST `imageBase64`」→ 返回 1688 托管 imageUrl
  4. `search_by_image_aibuy` 用上传后的 imageUrl 搜（阿里服务器直接抓，更稳）
  - **验证**：实测 aibuy 图搜返回 20 个精准结果（园林工具收纳架同款），`follow` 端到端 `envelope_built:true` 入采集箱；34 单测全过。

### ISSUE-002 · CDP 图搜 badge 全 0 + 标题相关性护栏过严 → no_relevant_match
- **板块**：skill
- **类型**：缺陷（匹配质量）
- **严重级**：P1（跟卖核心链路拦截，即使有相关货源也拒绝）
- **用例**：S4（follow 跟卖）
- **复现**：`follow --ozon-url "https://www.ozon.ru/product/derzhatel-sadovogo-instrumenta-25-sm-1-sht-4505492370/"`，17 个结果中「铁质园林工具展示架壁挂式收纳架」「花园庭院工具挂钩」等明显相关，但 `_pick_best_match` 标题相关性 conf<0.3 全部拒绝
- **预期**：相关货源（园林工具挂架）应通过护栏，组装信封
- **实际**：`no_relevant_match: true`，`blocked_reason: no_relevant_match`
- **日志**：`图搜结果与竞品标题相关性过低，拒绝匹配（不组装信封）`；17 结果 badge 全 0（未登录态图搜无徽标信号）
- **状态**：open
- **备注**：俄语标题「Держатель садового инструмента」翻译后与「园林工具展示架」词重叠度不足。可选方向：① badge 全 0 时改用 LLM 语义判定（`_llm_semantic_match` 已有）② 降低无 badge 场景的 conf 阈值 ③ 参考 shopbang「无护栏取第一个非广告」但加 LLM 兜底。

### ISSUE-003 · CDP 图搜已改后台 tab（静默）✅
- **板块**：skill
- **类型**：优化（已完成）
- **严重级**：P2
- **用例**：S6 / L2
- **现状**：`search_by_image_cdp` 原用 `conn.new_tab()` 弹可见 tab（用户看到 1688 窗口）；已改 `new_tab(background=True)` 走 `Target.createTarget(background:true)` 后台 tab，对齐 shopbang `show:false` 隐藏窗口体验
- **状态**：fixed
- **备注**：`cdp_client.py` 的 `CdpConnection.new_tab` 新增 `background` 参数。

### ISSUE-004 · seller.ozon.ru analytics 401（6/6 品类全挂）
- **板块**：skill
- **类型**：缺陷（时序，已修复）
- **严重级**：P1
- **用例**：S4（follow 跟卖）
- **复现**：`follow --ozon-url`，seller tab 刚开/复用时 `sc_company_id` cookie 未就绪 → 401
- **根因**：`fetch_sales_analytics` 读 `sc_company_id` 一次性读取，无等待；seller tab 刚开 cookie 未加载 → 读到空 → 401
- **修复**：读 `sc_company_id` 改为轮询等待 ≤8s（仿 aibuy token 舞步），读不到才降级
- **验证**：重跑 follow `1/1 SKUs have data`，权威类目正常返回
- **状态**：fixed

### ISSUE-005 · aibuy 通道误打「badge 评分仅 0」告警
- **板块**：skill
- **类型**：体验（误导性告警，已修复）
- **严重级**：P2
- **复现**：aibuy 图搜命中后，日志打「最佳匹配 badge 评分仅 0」
- **根因**：badge 是 CDP 通道的 DOM 信号，aibuy/AK 通道无 badge，告警未按通道区分
- **修复**：告警条件加 `search_method == "cdp"`（badge 仅 CDP 通道可选参考）
- **状态**：fixed

### ISSUE-002 · CDP 图搜 badge 全 0 + 护栏过严 → no_relevant_match
- **板块**：skill
- **类型**：缺陷（LLM prompt 偏保守，已修复）
- **严重级**：P1（已随 aibuy 修复降级为兜底路径）
- **根因**：CDP 无 badge 时 `_llm_semantic_match` 兜底，但 system prompt 用「可代工」作 YES 标准，对「挂架 vs 展示架」近义同款判 NO
- **修复**：LLM prompt 改为「核心功能+物理形态相同即 YES，近义词（挂架=展示架=收纳架）算同款」
- **验证**：挂架/展示架判同品、停车阻挡器判不同品，3/3 正确
- **状态**：fixed（注：aibuy 已为主通道，CDP 仅兜底，此修复提升兜底准确率）

### ISSUE-005 · aibuy 通道误打「badge 评分仅 0」告警
- **板块**：skill
- **类型**：体验（误导性告警）
- **严重级**：P2
- **用例**：S4（follow 跟卖，aibuy 命中时）
- **复现**：aibuy 图搜命中后，日志仍打 `⚠️ 最佳匹配 badge 评分仅 0，图搜可能不准确，建议人工核实`
- **预期**：aibuy 通道无 badge（靠官方排序+norm），不该打 badge 告警
- **实际**：6/6 品类都出现误导性告警
- **状态**：open
- **备注**：该告警是给 CDP 通道的（badge 是 CDP 解析的 DOM 信号），aibuy 通道应跳过或改用 norm 提示。

### ISSUE-006 · CDP 借道「No such target id」偶发 Handshake 500
- **板块**：skill
- **类型**：缺陷（偶发，有降级，已随 S1 反查移除连带解决）
- **严重级**：P2
- **用例**：S4（follow 跟卖）
- **复现**：`fetch_product_info` 借道时，目标 tab 已被关闭 → `No such target id: xxx` → Handshake 500
- **根因**：触发源是 S1 反查（`_reverse_lookup_ozon_competitor` 逐个 `fetch_product_info`），该段已移除（ISSUE-010）。剩余 `fetch_product_info` 调用方（discover）用 `force_new_tab=True` 不复用用户 tab，无 stale tab 问题
- **状态**：fixed（连带解决）

### ISSUE-007 · all-queries 静默直调 403（降级 CDP 借道可拿）
- **板块**：skill
- **类型**：缺陷（Ozon 反爬限制，非代码 bug）
- **严重级**：P2
- **用例**：S9（queries --type all-queries）
- **复现**：`queries --type all-queries --keyword "电风扇"`，`_seller_direct_post` 直调 403
- **根因**：403 响应含 `challengeURL`（`challenge.html?challenge=...`，`fab_chlg` 反爬挑战）——seller.ozon.ru 的 `searchteam` 端点对无浏览器指纹的 requests 直调触发反爬挑战；浏览器内同源 fetch（CDP 借道）能通过。`what_to_sell/data/v3` 端点反爬宽松（直调 200）。
- **结论**：直调路径对 `searchteam` 端点天然被挑战，伪造浏览器指纹复杂且易失效；CDP 借道兜底已工作（返回「电风扇」18 搜索/31 卖家）。功能不受阻。
- **状态**：wontfix（已知限制，CDP 借道为正解）

### ISSUE-008 · badge 应作为可选参考指标（非硬指标）
- **板块**：skill
- **类型**：优化（已随 ISSUE-005 一起修复）
- **严重级**：P2
- **现状**：badge 有时有（登录态+徽标渲染）有时无（未登录/未渲染），`_pick_best_match` 里 badge 本就是加分信号（`badge_eff * 20`）非硬指标；aibuy 通道无 badge 靠官方排序+norm 放行。告警已加 `search_method == "cdp"` 区分（ISSUE-005）
- **状态**：fixed

### ISSUE-009 · 竞品尺寸/重量漏抓（Ozon 商品页 attributes 就有）✅
- **板块**：skill
- **类型**：缺陷（已修复）
- **严重级**：P1
- **用例**：S4（follow 跟卖）
- **根因**：Ozon 商品页 attributes 的尺寸/重量字段有 3 种形态，但 `parse_ru_dims` 只匹配 `x/х/×`、`parse_ru_weight` 只匹配「数值+单位」、follow 兜底只找 `габарит/размер упаковки`，大量漏抓：① `Размеры, мм = 190*64*230`（`*` 不匹配）② `Длина/Ширина/Высота, см`（三独立键）③ `Вес товара, г = 270`（纯数字）
- **修复**：`parse_ru_dims` 加 `*` 分隔符；新增 `extract_weight_dims_from_attrs`；follow 兜底段改用新函数
- **验证**：USB风扇重量 270g+尺寸 190×64×230；园艺挂架三键 cm→250×70×100mm；单测 7 passed
- **状态**：fixed

### ISSUE-010 · S1 关键词反查（1688 标题→Ozon 搜索）无效且多余 ✅
- **板块**：skill
- **类型**：优化（已移除）
- **严重级**：P2
- **根因**：`_reverse_lookup_ozon_competitor` 用 1688 中文标题反查 Ozon，但 Ozon PC 端无图搜、中文标题搜俄语商品 + 词典窄 → 反查不到；follow 已有竞品完整数据，graph 的反查多余 + 慢
- **修复**：移除 graph 6.6 反查调用段
- **状态**：fixed

---

## 汇总统计

| 状态 | 数量 |
|---|---|
| open | 4（G1-G4） |
| investigating | 0 |
| fixed | 9（ISSUE-001 + ISSUE-002 + ISSUE-003 + ISSUE-004 + ISSUE-005 + ISSUE-006 + ISSUE-008 + ISSUE-009 + ISSUE-010） |
| wontfix | 1（ISSUE-007） |
