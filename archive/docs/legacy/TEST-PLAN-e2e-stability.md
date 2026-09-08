# 端到端稳定性测试计划（webui + worker + skill 三板块联动）

> 创建日期：2026-08-19
> 测试店铺：Client ID `5423887`（`db981a3c-c820-4933-8346-a80aac0f0315`）
> 状态：**待开测**（环境准备中）

---

## 0. 决策记录（已与用户确认）

| 决策 | 结论 |
|---|---|
| D1 测试环境 | **本地 Docker**（`localhost:8080`，PG `5433`），**禁用生产 `worker.mxou.cn`**（AGENTS.md 红线） |
| D2 上架边界 | **混合测试**：采集类（discover/follow 抓取/image_search/queries）真跑；上架/类目属性提交先 `--to-box` 进采集箱验证链路，最后挑 2-3 个商品真上架验证全流程 |
| D3 MXOU 预算 | **¥100**（真上架会触发 AI 生图，消耗余额，需精打细算） |
| G1-G4 已知 gap | **已确认存在，测试中根据实际情况验证影响面，边测边记，按优先级修复** |
| 问题收集 | **本地文档** `docs/TEST-ISSUES-2026-08.md` + 重要问题同步 **GitHub issue** |

---

## 1. 测试目标

1. **稳定性**：三板块在真实店铺下的端到端链路不崩、不断、数据不串。
2. **联动性**：验证 `skill 采集 → worker 管线 → webui 展示` 的完整数据流闭环。
3. **已知 gap 量化**：G1-G4 每个 gap 的实际影响面 + 复现路径，为修复排优先级。
4. **shopbang 对齐**：对比竞品六阶段漏斗，找出我们筛选/并发/利润核算的差距。

---

## 2. 测试环境与前置准备

### 2.1 环境拓扑

```
本地 Docker（deploy/docker-compose.yml）
├── postgres:16  → 127.0.0.1:5433
├── worker       → 0.0.0.0:8080（鉴权走云 Supabase）
└── webui        → ../webui/dist 挂载，/app 路径
skill（本地 CLI）→ WORKER_URL=http://localhost:8080
```

> ⚠️ **关键**：本地 worker 鉴权走**云 Supabase**（`kekmppsuiiokdckdeolv.supabase.co`），所以 skill 的 `mxou_token` 必须是云 Supabase tokens 表里有效的 token（status==1 + 余额 > 0）。店铺凭证（5423887）已配在 `skill/data/config/stores.json`。

### 2.2 前置检查清单

- [ ] `cd deploy && docker compose up -d --build`（worker + PG）
- [ ] `curl http://localhost:8080/api/v1/health` 返回 200
- [ ] 清空任务表防误激活：`DELETE FROM ozon_product_tasks WHERE status IN ('pending','failed','running')`
- [ ] webui 构建：`cd webui && npm run build`（dist 已存在则跳过）
- [ ] skill `WORKER_URL=http://localhost:8080 python3.12 scripts/cli.py check`（环境 + 5423887 鉴权 + Supabase token 有效性）
- [ ] Chrome CDP 就绪（9222）+ 1688/Ozon 登录态（`check` 会验证）

---

## 3. 测试用例清单

> 每条用例格式：`[ID] 名称 | 输入 | 预期 | 实际 | 状态`

### 3.1 Skill 侧（采集层，安全，真跑）

| ID | 用例 | 输入 | 预期结果 |
|---|---|---|---|
| S1 | `discover` 蓝海选品 | 关键词 或 Ozon 搜索页 URL | 蓝海评分表 + CSV + `discovery_runs` 上报成功 |
| S2 | `discover --export csv` | 关键词 | 本地 CSV 落盘 `data/discovery/*.csv`（utf-8-sig，Excel 可开） |
| S3 | `discover --to-box` | 关键词 | 候选信封进采集箱（`POST /api/v1/drafts`）→ webui 采集箱可见 |
| S4 | `follow` 跟卖 | Ozon 商品 URL | 竞品抓取 + 1688 图搜 + 信封组装（不提交） |
| S5 | `graph` 1688 选品 | 1688 商品 URL | 信封 + 预估售价（worker 公式 + 真实运费） |
| S6 | `image_search` 图搜 | 图片 URL/路径 | 1688 同款候选（aibuy 通道） |
| S7 | `search` 关键词 | 1688 关键词 | 利润估算表 + CSV |
| S8 | `batch_test` 批量 | 混合 URLs 文件 | 批量信封 + 结果 JSON（**观察并发：当前串行+delay，对比 shopbang max 3 队列**） |
| S9 | `queries` 榜单 | 无（读 seller.ozon.ru） | 榜单 CSV + analytics 上报 |

### 3.2 Worker 侧（管线层，dry-run 优先）

| ID | 用例 | 输入 | 预期结果 |
|---|---|---|---|
| W1 | 蓝海归档 | skill discover 触发 | `POST /discovery/runs` 按 token 隔离写入；`GET` 全局共享可读 |
| W2 | 类目映射查询 | `GET /api/v1/mappings/lookup?keyword=` | 返回 `{found, mappings}` |
| W3 | 货源匹配（跟卖） | follow 信封 | `follow_sell_import` api/hand 双模式 + 类目匹配 |
| W4 | **类目属性特征提交** | graph 信封（真上架时） | assemble→prepare→retry 三处 `attr_value_matcher` 一致，字典属性正确，中文零容忍 |
| W5 | 采集箱 | `--to-box` 信封 | `product_drafts` 租户隔离 + `draft_submissions` 记录 |
| W6 | 任务队列 | `POST /submit_task` | 信封校验→余额→配额→sku 去重→入队→终态写回 |
| W7 | 物流报价 | `POST /api/v1/logistics/quote` | 返回 `logistics_cost_cny` |

### 3.3 WebUI 侧（展示层，浏览器）

| ID | 用例 | 操作 | 预期结果 |
|---|---|---|---|
| U1 | 店铺管理 | `/stores` 添加 5423887 凭证 + 设默认 | 凭证落库 + 今日统计 |
| U2 | 采集箱 | `/collect-box` 查看 skill `--to-box` 草稿 | 草稿列表可见（**验证 G1：discover CSV 是否在此可见**） |
| U3 | 上架工作台 | `/on-sale` 选草稿 → AI 填标题 → 一键上架 | `POST /drafts/{id}/submit` 入队成功 |
| U4 | 任务中心 | `/tasks` 观察任务状态流转 | 状态/取消/重试正常 |
| U5 | 热销榜 | `/bestsellers` | 榜单数据展示 |
| U6 | 模板 | `/templates` 设加价率/佣金 | 参数注入 submit 时 extensions |

### 3.4 三板块联动（核心闭环）

| ID | 链路 | 验证点 |
|---|---|---|
| L1 | **discover → 采集箱 → webui** | skill 选品信封 `--to-box` → webui `/collect-box` 可见 → 上架 |
| L2 | **follow → worker → 任务中心** | skill 跟卖信封 → worker 管线 → webui `/tasks` 状态 |
| L3 | **graph → 类目属性 → 真上架** | 1688 信封 → assemble/prepare 属性 → 真上架 Ozon（挑 2-3 个） |
| L4 | **queries → analytics → 热销榜** | skill 榜单 → worker analytics → webui `/bestsellers` |

---

## 4. 已知 GAP 清单（测试中验证 + 排优先级）

| Gap | 现状 | 测试验证方式 | 影响面 |
|---|---|---|---|
| G1 | discover CSV 只落本地，`discovery_runs` 进 PG 但 webui 无页面消费 | S1+S3+U2 对照 | 选品结果 webui 看不到，只能看采集箱草稿 |
| G2 | webui 无蓝海/货源/类目属性独立 UI | U 全量 + 功能对照 | 三块功能只能 skill+worker API 测 |
| G3 | 商品「编辑」按钮未接线；skill 无编辑命令 | 商品管理页点「编辑」 | 「编辑上架」半成品 |
| G4 | skill 批量并发弱（batch_test 串行 + delay） | S8 观察耗时 | 对比 shopbang max3 队列 + max8 任务并行 |

---

## 5. shopbang 学习点对比（测试后出改造方案）

| shopbang | 我们现状 | 改造方向 |
|---|---|---|
| 六阶段漏斗（粗筛→1688→精筛） | discover 单层过滤 | 两段式：低成本字段粗筛 → 再花图搜配额 |
| 18 项 BASE + 4 项 DETAIL 精筛 | 粗筛字段较少 | 补跟卖人数/广告份额/促销天数/成交率 |
| 销量阶梯门槛（价格分档） | 无 | 蓝海评分加价格×销量阶梯 |
| 并发队列 max3 + 任务并行 max8 | batch_test 串行 | 补任务队列 + 并发窗口（G4） |
| 利润核算云端 | ✅ 已对齐 | 无需改 |

---

## 6. 需用户准备的产品链接

1. **1688 商品链接** × 3-5（测 `graph` + 类目属性 W4）
2. **Ozon 商品链接** × 3-5（测 `follow` + 货源匹配 W3）
3. **Ozon 搜索/类目页 URL 或 关键词** × 2-3（测 `discover` S1）
4. **混合 URLs 文件**（1688+Ozon，测 `batch_test` S8）
5. **图片** × 2-3（测 `image_search` S6）
6. 确认 seller.ozon.ru 已登录（测 `queries` S9）

---

## 7. 执行顺序建议

1. **环境准备**（第 2 节前置检查全绿）
2. **采集类真跑**（S1-S9，安全无副作用）→ 记录问题
3. **dry-run 上架**（L1/L2 走 `--to-box`，验证采集箱链路）→ 记录问题
4. **真上架**（L3，挑 2-3 个，消耗 MXOU 预算）→ 验证类目属性 W4
5. **webui 全量过一遍**（U1-U6）
6. **汇总问题** → 按 P0/P1/P2 排优先级 → 出优化方案
