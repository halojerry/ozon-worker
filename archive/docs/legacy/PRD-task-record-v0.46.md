# PRD — 上架记录增强（P0-2，对标上品帮 /batchRecord + 毛子 /product/import-history）

> 2026-08-16。竞品调研：`docs/competitor/shangpinbang-full.md` §2.2（上架记录 13 列 + 状态机 + 批量搬家/异常重上/释放帮豆）、`docs/competitor/maozier-backend-full.md` §四（批量失败重上 + 库存添加记录）。
> 现状盘点：Tasks 页已有商品信息/平台/店铺/状态/售价/划线价/货源链接/上架方式（跟卖-选品二分）/创建时间/操作（详情/生图/回采集箱改/重上）+ 多选 + 批量重上 + 状态筛选 + 5s 轮询 + 详情弹窗。
> 本 PRD 聚焦**增量差距**：上架方式细分 + 批量重上来源标记 + CSV 导出。

## 一、背景与目标

### 1.1 现状差距（对标竞品 13 列）
| 竞品列 | 现状 | 差距 |
|---|---|---|
| 上架方式（一键/编辑/手动/搬家/下架重上） | 仅「跟卖/选品」二分 | 缺「编辑更新」（update_product_id）、「重上」（resubmit 来源）标记 |
| 上架状态机（上架中→待校验→完成/异常 + 库存子状态） | 已有 pending/running/completed/failed/rejected + pending_moderation | 基本齐（worker 无"待校验"中间态概念，不强行引入） |
| 批量操作（异常重上/批量删除/释放帮豆） | 已有批量重上 | 释放帮豆=我们无帮豆体系，跳过 |
| **导出 CSV** | ❌ 无 | **新增**（当前筛选结果导出，竞品标配） |
| 售价/划线价/货源链接 | ✅ 已有 | 齐 |

### 1.2 目标
1. **上架方式细分**：TaskListItem 新增 `update_mode`（编辑更新标记）——上架方式列显示「跟卖/编辑更新/选品」三态；`resubmit_count`（重上次数）标记重上来源
2. **CSV 导出**：任务列表工具栏「导出 CSV」——导出**当前筛选结果**（商品标题/货号/店铺/状态/售价/划线价/利润率/货源链接/上架方式/创建时间），UTF-8 BOM 兼容 Excel 中文
3. 保留现有批量重上/筛选/轮询全部行为

### 1.3 非目标
- 上架方式「一键/手动/搬家/下架重上」细分到提交类型枚举（我们的提交路径只有：skill 采集提交 / WebUI 新建 / WebUI 编辑更新 / 跟卖 / 重上——用 update_mode + follow_sell + resubmit_count 三字段即可表达）
- 待校验中间态、帮豆释放、批量删除（删任务记录有风险，P1 再说）

## 二、字段设计（worker 改动最小化）

### 2.1 `_payload_meta` 扩展（task_service.py）
```python
"update_mode": bool(ext.get("update_product_id")),   # 编辑更新（在线商品改后重传）
"follow_sell": bool(ext.get("follow_sell")),          # 已有
"resubmit_count": row.resubmit_count or 0,            # 重上次数（SELECT 加列）
```

### 2.2 上架方式推导（前端）
| update_mode | follow_sell | resubmit_count>0 | 显示 |
|---|---|---|---|
| true | — | — | 编辑更新 |
| false | true | — | 跟卖 |
| false | false | — | 选品上架 |
| 任意 | 任意 | >0 | 重上（第 N 次，N=resubmit_count+1） |

## 三、改动清单

### Worker（2 处小改）
1. `task_service.py` `_payload_meta`：加 `update_mode`；`_SELECT_COLS` 加 `resubmit_count`
2. `schemas.py` `TaskListItem`：加 `update_mode: bool` + `resubmit_count: int`

### WebUI（2 文件）
1. **`client.ts`**：TaskListItem 加 `update_mode?` / `resubmit_count?`
2. **`Tasks.tsx`**：
   - 上架方式列：三态推导 + 重上次数 badge（`重上 ×2`）
   - 工具栏「导出 CSV」按钮（当前筛选结果 → Blob 下载，UTF-8 BOM）
   - 导出函数 `exportCsv(filtered)`：列 = 商品标题/货号/店铺/账号/状态/售价/划线价/利润率/货源链接/上架方式/创建时间

## 四、测试计划

### Worker
- `tests/test_task_service.py`（若存在则加用例；否则新建）：`_payload_meta` 提取 update_mode；list_tasks 返回 resubmit_count
- 全量回归 worker 1008 不破

### WebUI
- build + tokens:validate 绿
- 手动冒烟：任务列表三态上架方式显示正确；导出 CSV 打开含全部列 + 中文不乱码

## 五、验收标准（DoD）
1. 上架方式列：编辑更新/跟卖/选品三态 + 重上次数标记正确
2. 导出 CSV：当前筛选结果导出，Excel 打开中文不乱码（BOM），列完整
3. worker 1008 + skill 493 + webui build 不破

## 六、实施顺序
T0 worker 字段（_payload_meta + schemas + SELECT）→ T1 测试 → T2 webui 上架方式三态 + 重上 badge → T3 CSV 导出 → T4 build + 冒烟 + 版本 0.46.0
