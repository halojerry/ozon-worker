# PRD — 订单操作（P1-1，对标上品帮/毛子ERP 订单处理操作）

> 2026-08-16。竞品调研：`docs/competitor/shangpinbang-full.md` §4.1（批量备货/打印面单+拣货单/物流查询/催护照/催取货/索好评/颜色标签）、`docs/competitor/maozier-backend-full.md` §七（取消货件选原因/保存货源信息/采购信息/买家标记）。
> 现状：v0.47 订单列表（P0-4）已能看订单；缺「处理」能力。
> 本 PRD 聚焦 P1 订单操作：**货源/采购信息标注（本地元数据）+ 面单 PDF 下载**。备货/取消/催评等 Ozon 写入操作单独一期（P1-2，见 §六）。

## 一、背景与目标

### 1.1 问题
订单列表只能看不能操作。竞品两家订单页都支持：记录货源/采购信息（毛子：货源地址/价格/备注 + 采购单号/快递/单号）、打印面单（两家）、催评（两家）。

### 1.2 目标（P1-1 范围）
1. **订单货源/采购信息标注**（本地元数据，`order_notes` 表）：每单可记录货源地址/货源价格/货源备注 + 采购单号/采购快递/采购单号——对标毛子，是「出单后采购跟进」的核心
2. **面单 PDF 下载**（`/v2/posting/fbs/package-label`）：一键下载当前订单面单

### 1.3 非目标（P1-2 后续）
- 备货发货（`/v4/posting/fbs/ship`，对真实订单写入，需谨慎 + 真实店铺验证）
- 取消订单（`/v2/posting/fbs/cancel`，选原因）
- 催护照/催取货/索好评（`/v1/posting/fbs/message` 或站内信，需消息模板体系——复用竞品 autoMsg 设计）
- 批量备货/批量面单（P1-3）

## 二、设计

### 2.1 `order_notes` 表（本地元数据，不对 Ozon 写入）
```sql
CREATE TABLE IF NOT EXISTS order_notes (
    posting_number  TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL,
    source_url      TEXT NOT NULL DEFAULT '',   -- 货源地址
    source_cost     NUMERIC,                    -- 货源价格
    source_remark   TEXT NOT NULL DEFAULT '',   -- 货源备注
    purchase_no     TEXT NOT NULL DEFAULT '',   -- 采购单号
    purchase_carrier TEXT NOT NULL DEFAULT '',  -- 采购快递
    purchase_tracking TEXT NOT NULL DEFAULT '', -- 采购快递单号
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_order_notes_tenant ON order_notes(tenant_id);
```
- 租户隔离：所有读写带 tenant_id；posting_number 全局唯一（Ozon 单号跨店铺唯一）
- 不建外键（订单数据实时拉取，notes 独立持久化）

### 2.2 API（`routes/orders_routes.py` 扩展 + `order_service.py`）
```
GET    /api/v1/orders/{posting_number}/notes   → OrderNoteOut | {source_url:"", ...}
PUT    /api/v1/orders/{posting_number}/notes   → upsert（source_url/cost/remark + purchase_*）
GET    /api/v1/orders/{posting_number}/label   → 面单 PDF（/v2/posting/fbs/package-label 代理）
```
- notes：本地读写，租户隔离，posting_number 不存在也允许 upsert（先标注后同步订单）
- label：`get_decrypted` → `ozon_post /v2/posting/fbs/package-label {posting_number}` → 返回 PDF base64 或文件流（`label_url` 或直接 bytes）；失败 502

### 2.3 schemas
`OrderNoteOut`（含全部 6 个字段）/ `OrderNoteUpsert`

### 2.4 WebUI 订单页（Orders.tsx）
- 行操作新增：「备注」（弹窗：货源地址/价格/备注 + 采购单号/快递/单号，保存调 PUT notes）「面单」（调 GET label → 下载 PDF）
- 列表「备注」列：有 notes 显示 📋 图标/货源摘要，否则 —
- 详情弹窗追加货源/采购信息区块

## 三、测试计划

### Worker
- `test_order_notes.py`：notes upsert/get + 租户隔离（A 看不到 B 的 notes）+ label 代理（mock ozon_post 返回 PDF，成功/失败 502）
- 全量回归 worker 1023 不破

### WebUI
- build + tokens:validate 绿
- 手动冒烟：订单行「备注」弹窗保存 → 刷新显示；「面单」在有真实店铺时下载

## 四、验收标准（DoD）
1. notes upsert/get 租户隔离通过测试
2. label 代理成功返回 PDF / 失败 502
3. WebUI 备注弹窗 + 面单下载 + 列表标记
4. worker 全量回归不破

## 五、实施顺序
T0 order_notes 表 + notes 读写 → T1 label 代理 → T2 worker 测试 → T3 WebUI 备注弹窗 + 面单 + 列表标记 → T4 版本 0.48.0 + 回归 + 提交

## 六、P1-2（下一期）订单写入操作
- 备货发货 `/v4/posting/fbs/ship`（勾选待发运订单 → 批量备货确认）
- 取消订单 `/v2/posting/fbs/cancel`（选原因，复用 cancel-reason 列表）
- 催护照/催取货/索好评：消息模板体系（对标 autoMsg，3 种内置模板 + 占位符）
- 批量面单（多选 → 合并 PDF）
