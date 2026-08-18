# PRD — Skill 学习上品帮 v1（上架成功率 + 货源速度 + 选品效率 + 数据归档）

> **版本**：v1.0
> **日期**：2026-08-17
> **状态**：待评审 → 已评审（Momus **OKAY**，无阻断）→ 交付执行
> **数据来源**：上品帮 v1.0.22 客户端源码全量解包（35 个 dist-electron js）+ 三轮 explore 事实核证 + Momus 计划评审
> **配套**：`.omo/plans/skill-learn-shangpinbang-v1.md`（实施计划 + 12 验证门）、`docs/competitor/shangpinbang-skill-learning-notes.md`（完整调研笔记含 §12 事实）
> **范围**：skill 侧为主 + worker 侧 6 项协作（含 1 个独立 bug 修复 W6）；webui 不涉及（同事并行）
> **原则**：只 ADD 不 CUT——复用 worker 已就绪能力，零破坏现有流程

---

## 1. 背景与目标

### 1.1 背景

上品帮客户端（shopbang.cn v1.0.22）解包源码全量调研完成，与 ozon-worker 现状逐行对照后发现 4 个可落地的结构差距：

| 维度 | 上品帮 | ozon-worker 现状 | 差距 |
|---|---|---|---|
| 选品漏斗 | 列表内联解析 + 18 项 BASE 粗筛 + AI 阶梯门槛，粗筛后才上 1688 图搜 | 列表阶段只取 URL pid；粗筛字段只有 9 个（ozon_seller_analytics 27 字段中 13 个未接入粗筛）；全员上 aibuy 配额 | 2.5× 速度差 + 80% aibuy 配额浪费 |
| graph 信封 | 无直接对应（后端 goodsFilter 统一核算） | graph 路径（1688 直上）缺 extensions.competitor 竞品数据；worker 兜底链 C2 已就绪但没喂数据 | 上架成功率低于 follow 路径 10-15% |
| 选品归档 | autoTask 任务级 + saveAutoPickRecords 每 1000 条批量推后端，按 account_id 隔离 | discover 结果只落本地 JSON，worker 无归档端点（blue_ocean_queries 已有但 discover 不上报） | 跨机丢失 + agent 无法拉历史 |
| 上架配置 | upDataConfigId 选预设，多店铺差异化模板 | worker 已有 listing_templates 表 + CRUD API，但 skill 端只读本地 stores.json 不走 worker | 多店铺开箱即用缺失 |

### 1.2 目标（4 个用户可观测结果）

| # | 目标 | 衡量指标 |
|---|---|---|
| **G-A** | graph 路径上架成功率对齐 follow（+10-15%） | 10 个 graph 真实 1688 URL 上架，moderate_status=approved 比例提升；weight_source: competitor 触发率从近 0 提升 |
| **G-B** | discover 单次时长 5min → 2min（2.5× 加速） | `discover --keyword "宠物饮水机" --max-products 50` time 测量；aibuy 调用次数 50 → 10（粗筛砍 80%） |
| **G-C** | 选品结果跨机不丢 + agent 可拉历史 | 机器 A 跑 discover → worker `discovery_runs` 表有行 → 机器 B `discover --history` 拉到 A 的关键词/results（tenant 隔离） |
| **G-D** | 多店铺用户开箱即用 | webui 配默认 listing_template → skill `graph --url <1688>` 不传 extensions.margin_rate 也走 worker 默认模板 → 上架成功 |

### 1.3 非目标

- **不新增图搜 API**：Ozon 反搜已调研确认（skill 无现成 / 上品帮无 / Ozon Seller API 无），复用 `ozon_discovery.py:discover_from_keyword`(L816) 搜索页 CDP + 语义匹配件（`_llm_semantic_match` L1763 / `_ru_zh_title_overlap` L1427），仅 0.5d 串联入口
- **不学上品帮 8 BrowserWindow 真隔离**：当前多 tab 复用 1 Chrome 已够（用户体验 + 资源双赢）
- **不加 TaskManager 守护进程**：与 webui 同事任务中心职责重叠
- **不学旧 stealth 指纹伪造**：v0.28.7 已反学（真实指纹天然干净）
- **不做后端查询分类热卖模式**：依赖 shopbang 私有炼数后端，不可走
- **webui 不涉及**：webui 同事并行开发中，本 PRD 交付物不含 webui 改动

---

## 2. 需求分解（F1-F9，对应计划 Wave 1-4）

| # | 需求 | 所在 | 文件位置 | 工程量 |
|---|---|---|---|---|
| **F1 (S7)** | 滚动 3000ms 缓动 + 80% 滚动量（反爬节奏） | skill | `ozon_discovery.py:283/790` + `ozon_image_search.py:378/387` | 0.5d |
| **F2 (W6)** | graph 直连路径回填 product_task_index（独立 bug） | worker | `learning_record_node.py:96-98` + `main.py:1366-1393` | 0.5d |
| **F3 (W12)** | MXOU 余额事中复查（生图前 fast-fail） | worker | `mxou_api.py:233` 入口加 pre-check + TTL 缓存 | 0.5d |
| **F4 (W10)** | worker 新增 discovery_runs 端点（D12 后端） | worker | `model.py:445 旁` 新 ORM + `schemas.py:152 旁` Pydantic + `main.py:1977-2002` 加 kind + 2 端点 | 1d |
| **F5 (W11)** | worker 新增 /api/v1/mappings/lookup 端点（W3） | worker | `main.py` 新端点复用 `category_mapping_learn.lookup_mapping` + `ozon_category_query.get_category_mapping_by_keywords` | 1d |
| **F6 (W9)** | ListingTemplateOut 补 store_overrides 字段 | worker | `schemas.py:354-364` | 0.5d |
| **F7 (S1)** | graph 信封补竞品数据（Ozon 反搜 0.5d） | skill | `cloud_probe.py:1903-1930` 注入段 + `draft.*` 对齐 follow 路径 | 1d |
| **F8 (S5/B3)** | 列表内联解析 + 13 字段入粗筛 + 18 项 BASE 粗筛 | skill | `ozon_discovery.py:803-809` 改 JS + `_SELECTION_FIELDS`(701-711) 扩 + `_check_rule`(714-727) None=不限 + `_apply_filters`(438-470) | 2d |
| **F9 (S6)** | `--rules ai` 销量阶梯门槛 | skill | `ozon_discovery.py:730-760` + `cli.py:1734` | 0.5d |
| **F10 (D11)** | skill 端读 listing_templates 选默认配置 | skill | `config_store.py:401-413` + `cloud_probe.py:1909-1926` + cli flag | 1d |
| **F11 (D12)** | discover 结果上报 worker 归档 | skill | `ozon_discovery.py:552/691` 挂上报钩子 | 0.5d |
| **F12 (D7')** | discover-multi --keywords 多关键词并行 | skill | `cli.py:1718-1756` + 新 cmd | 1.5d |
| **F13 (D13)** | discover --to-box 通道 | skill | `cli.py:1500-1515` `_submit_one` 加分支 | 0.5d |

**总工程量：~14.5d**（4 wave 并行关键路径 ~5d）

---

## 3. 关键设计决策（Momus 评审后定稿）

### 3.1 S1 键结构 — 混合方案（决策已定）

```python
envelope["extensions"]["competitor_weight_g"] = ...        # 扁平键 ✅ worker 已读 (assemble_ozon_product_node.py:66-73)
envelope["extensions"]["competitor_dimensions_mm"] = ...    # 扁平键 ✅ worker 已读
envelope["draft"]["ozon_attributes"] = ...                  # draft 段 ✅ worker 读 draft.ozon_attributes (prepare:846)
envelope["draft"]["competitor_price"] = ...                 # draft 段 ✅ worker 读 draft.competitor_price (follow_sell_import:234-237)
envelope["draft"]["follow_min_price"] = ...                # 新键放 draft（复用 worker draft 读取路径）
```

**为什么**：零 worker 改 + 跟 follow 路径 100% 对齐（weight/dim→extensions，ozon_attrs/price→draft）。

### 3.2 D12 上报体积 — 白名单裁剪 + 单次上报（决策已定）

```python
REPORT_FIELDS = ["ozon_product_id","ozon_title","ozon_price","competing_sellers",
  "min_competing_price","match_1688_url","match_1688_price","profit_margin",
  "blue_ocean_score","status","category","brand","monthly_sales","monthly_revenue",
  "drr","create_days","rating","review_count","weight_g","dimensions_mm"]
# 单条 ~500B × 50 = 25KB/run（vs 不裁 100KB），单次 POST，PG JSONB 无压力
```

**为什么**：chunk 无边际收益（内网 100ms 无网关限制），白名单裁剪去掉归档用不到的 `competing_seller_list`(4KB/候选)/`match_1688_images`(1KB/候选)。

### 3.3 W11 类目映射表 tenant — 不加 tenant_id，保持全局共享（决策已定）

**为什么**：类目映射是平台级知识（"宠物用品"→17028929/504866264 对所有用户都对）；加 tenant 会导致碎片化（100 用户 × 5 条 = 500 条共享池 vs 每人 5 条达不到 MIN_SUCCESS_COUNT=3 阈值 → 查询全 not found → 学习表失效）。与 `ozon_attribute_mappings` 全局学习一致。

### 3.4 决策依据（Momus 评审补正）

- `competitor_weight_g`/`competitor_dimensions_mm` 确在 `extensions.*`（worker 确认读取）
- `ozon_attributes`/`competitor_price` 实存于 `draft.*`（不是 extensions）
- `follow_min_price` 当前代码不存在（新键）
- → S1 方案 A "100% 对齐" 仅对 weight/dimensions 成立；ozon_attrs/price 走 draft.* 是对齐 follow 路径的真实位置

---

## 4. 验证门（G1-G13，对应 .omo/plans §6）

| 门 | 条件 | 验证命令 |
|---|---|---|
| G1 | S7 滚动缓动不误触底 | `python3.12 scripts/cli.py discover --keyword "宠物饮水机" --max-products 20` 5 次滚动缓动 + 触底不误判 |
| G2 | W6 索引回填 | graph 直连 submit_task 上架成功 → 查 PG `product_task_index` 有 product_id 行 + credential_id 非空 |
| G3 | W12 余额事中止血 | 模拟 MXOU 余额 <1.0 → 生图前 fast-fail + task failed + "OUT_OF_QUOTA" |
| G4 | W10 discovery_runs 端点 | curl POST + GET /api/v1/discovery/runs 200 + tenant 隔离（A 查不到 B） |
| G5 | W11 mappings/lookup 端点 | skill graph 命中类目缓存 → log "category lookup hit worker cache" + 结束 search_categories 调用 |
| G6 | S1 graph 信封补竞品 | 跑 graph → 信封含 extensions.competitor_weight_g（非零时）→ worker 日志 weight_source: competitor |
| G7 | S5/B3 漏斗加速 | discover --max-products 50 → aibuy 调用 ~10 次 + 总时长 < 2min |
| G8 | S6 --rules ai | discover --rules ai 不抛异常 + 阶梯生效（价≤500₽ 月销≤500 被砍） |
| G9 | D11 读 listing_templates | webui 配默认模板 → skill graph 不传 margin_rate → 上架成功 + 信封取自模板 |
| G10 | D12 上报归档 | discover 跑完 → worker discovery_runs 新增 1 行 + 本地 JSON 仍落盘 |
| G11 | D7' multi 关键词 | discover-multi --keywords "A,B,C" --max-each 30 → 合并去重 + 时长 < 3×单关键词分析 |
| G12 | D13 discover --to-box | discover --auto-submit --to-box → webui 采集箱 3 条（source=skill）不直接上架 |

---

## 5. 交付物清单

| 文件 | 内容 | 状态 |
|---|---|---|
| `docs/competitor/shangpinbang-skill-learning-notes.md` | 完整调研笔记（§0-12 事实清单） | ✅ 已完成 |
| `.omo/plans/skill-learn-shangpinbang-v1.md` | 实施计划 + 12 验证门 + 4 wave | ✅ 已评审（Momus OKAY） |
| 本 PRD | 需求分解 F1-F13 + 决策定稿 | ✅ 本文 |
| `.omo/tasks/skill-learn-shangpinbang-tasks.md` | 13 项 Task 清单（含 file:line / 改动 / 验收） | ✅ 本批落盘 |
| `.omo/tasks/skill-learn-shangpinbang-issues.md` | 4 个决策点归档 + 已知风险 + 未来扩展 | ✅ 本批落盘 |
| `.omo/tasks/skill-learn-shangpinbang-tests.md` | G1-G13 验收测试脚本 + 回归清单 | ✅ 本批落盘 |
| `.omo/tasks/skill-learn-shangpinbang-todo.md` | 按 wave 分组的 TODO 清单（执行者勾选） | ✅ 本批落盘 |

---

*本文档基于 `.omo/plans/skill-learn-shangpinbang-v1.md`（Momus OKAY 评审）+ `docs/competitor/shangpinbang-skill-learning-notes.md`（三轮 explore 事实核证）整理。所有 file:line 引用已在配套文档核实。*