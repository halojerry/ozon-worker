# PRD — 采集箱批量上架（P0-3，对标上品帮 goodsCollect 批量上架）

> 2026-08-16。竞品调研：`docs/competitor/shangpinbang-full.md` §2.1（采集箱表格批量上架：勾选 → 选店铺 → 批量提交 → 上架记录）。
> 复用 v0.44 上架配置模板（P0-1）：批量上架统一应用模板参数。
> 本 PRD 为「自有 WebUI 复刻路线」P0 第三项：采集箱批量上架。

## 一、背景与目标

### 1.1 问题
采集箱（/collect-box）已有：多选、批量删除、清空、单草稿编辑上架（Products.tsx 提交栏）。缺**批量上架**：
- 用户一次勾选 N 个草稿 → 统一选店铺 + 上架配置模板 → 批量提交
- 逐个进编辑页提交是重复劳动（竞品上品帮/毛子均支持勾选批量上架）

### 1.2 目标
1. 采集箱工具栏新增「批量上架」按钮（勾选 ≥1 草稿后可用）
2. 弹窗：选目标店铺（credential_id，默认店铺）+ 选上架配置模板（template_id，复用 v0.44）+ 显示数量/总成本/预估
3. 循环调用现有 `submitDraft`（**完全复用已验证逻辑**：模板注入/409 重复校验/跨店确认/入队）
4. **失败隔离**：单个草稿失败不中断其余；结果汇总「成功 N / 失败 M」+ 每项原因
5. 成功后跳转任务页或刷新列表显示新提交状态

### 1.3 非目标（P0 范围外）
- Worker 批量端点（前端循环即可满足；批量端点留 P1 优化——减少请求数/一次鉴权）
- 定时批量上架（scheduled_at 已透传，前端弹窗可加时间选择，本 PRD 不做）
- 批量编辑（先编辑后上架的复杂流，后续单独 PRD）

## 二、交互设计

### 2.1 入口
采集箱工具栏「批量上架」按钮（`selected.size > 0` 可用，在「批量删除」旁）：

```
[批量删除] [清空采集箱] [批量上架(primary)]  已选 N 项    ... [刷新]
```

### 2.2 批量上架弹窗（BatchSubmitModal）
| 区块 | 字段 | 说明 |
|---|---|---|
| 目标店铺 | select | 复用 listCredentials（默认选中 is_default 店铺） |
| 上架配置 | select | 复用 listTemplates（默认选中 is_default 模板；可「不使用」） |
| 汇总 | 文本 | `共 N 个草稿 · 采购总成本 ¥X`（遍历 draft.payload.draft.purchase_cost 求和） |
| 提示 | 文本 | 「将逐条提交上架；某条失败不中断其余，完成后显示结果」 |
| 操作 | 取消 / 批量上架 | 提交后按钮转 loading「提交中 i/N…」 |

### 2.3 提交逻辑（前端循环，失败隔离）
```ts
for (const draftId of selectedIds) {
  try {
    await submitDraft(draftId, credentialId, templateId)
    ok.push(draftId)
  } catch (e) {
    // 409 重复 → reason="目标店铺已存在相同商品"；其余 → errText
    fail.push({ draftId, reason })
  }
}
```

### 2.4 结果呈现（BatchResult 状态）
提交完成后弹窗内切换为结果视图：
- 成功列表（N 条，task_id 可点 → /tasks?task_id=xxx）
- 失败列表（M 条，每条显示草稿标题 + 原因；可「查看」进编辑页修正）
- 关闭 → 刷新采集箱（新提交状态可见）

### 2.5 跨店确认处理
循环中若某条返回 `confirm_required=true`（该草稿已上架到其他店铺）——**不弹逐个确认**（批量场景打扰），直接放行继续提交（提交本身不硬拦，服务端 confirm_required 仅提示）。在结果里标注「已跨店」可选项，本 PRD 简化为直接放行 + 结果中注明。

## 三、改动清单

### WebUI（全部改动，Worker 零改动）
1. **`webui/src/pages/CollectBox.tsx`**：
   - 工具栏加「批量上架」按钮
   - 新增 `BatchSubmitModal` 组件（店铺/模板下拉 + 汇总 + 提交循环 + 结果视图）
   - 状态：`batchOpen: boolean`、`batchResult: {ok: BatchOk[], fail: BatchFail[]} | null`
   - 提交中逐条 `submitDraft(draftId, credentialId || undefined, templateId || undefined)`
   - 结果视图：成功项 task_id 链接 /tasks；失败项草稿标题 + 原因 + 「去编辑」链接 /products/{id}
2. **样式**：复用现有 modal/field/btn/toolbar token（index.css 无需新增或极少）

### 无 Worker / API 改动
- `submitDraft`（client.ts）v0.44 已支持 template_id——批量直接复用
- 无需新端点

## 四、测试计划

### WebUI
- `npm run build` + `npm run tokens:validate` 绿
- 手动冒烟（本地 worker 8080 + 测试账号）：
  1. 勾选 2+ 草稿 → 批量上架 → 选店铺/模板 → 提交 → 结果「成功 N」
  2. 混入一个已上架草稿（409）→ 结果「成功 N-1 / 失败 1 + 原因」
  3. 批量提交后采集箱刷新 → 新提交状态列更新
  4. 未勾选时按钮禁用

## 五、验收标准（DoD）
1. 批量上架按钮随勾选状态启用/禁用
2. 弹窗可选店铺（默认店铺默认选中）+ 可选模板（默认模板默认选中）+ 汇总数量/成本
3. 提交循环失败隔离：单个 409/错误不中断其余
4. 结果视图成功/失败分组，失败项可去编辑页，成功项 task_id 可跳任务页
5. 全量回归 worker 1008 + skill 493 + webui build 不破

## 六、实施顺序
T0 采集箱批量上架按钮 + BatchSubmitModal 组件（弹窗 UI）→ T1 提交循环 + 失败隔离 → T2 结果视图 + 刷新 → T3 build + tokens:validate + 手动冒烟
