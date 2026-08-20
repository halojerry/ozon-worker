# 多 SKU 变体上传优化方案（深度调研版）

> 生成：2026-08-20 · ULW-RESEARCH 深度调研（6 workers + 官方/竞品/代码三域交叉验证）· 日志：`.omo/ulw-research/20260820-034021/`
> 前置调研：`docs/OZON-MULTI-SKU-QUOTA.md`（v0.59 已落地：9048 绑定合并、数量变体独立产品）
> 本方案**仅调研 + 建议**，执行另议（用户已明确「先说执行的事情」）

---

## 零、TL;DR

| 问题 | 结论 |
|---|---|
| 官方 API 有 variants 数组吗？ | **没有**。变体 = `items[]` 独立元素 + 属性 9048 同值合并 |
| 配额按卡还是按变体？ | **按卡**。合并 N 变体到一卡 → 配额返还 N-1（官方奖励机制） |
| 我方缺什么？ | ① merge 开关（合并 vs 每 SKU 独立）② 变体上限校验（500 硬顶/100 请求）③ 两处数据源不一致（9048 值源 + pricing/prepare variants 源）④ 合并成功无读回验证 |
| 数量变体必须拆卡吗？ | **官方无此强制**——数量是可合并的区别特征；拆卡是我方设计决策 |
| 竞品怎么做？ | 毛子 WB 版有 merge Switch（默认合并）；上品帮 80 上限；两者 WB 平台都 30 |
| 推荐方案（oracle 评审修正） | **Phase 1 = A6 配额预检 + A5 读回验证异步化**（即时价值，修复现有 VARIANT_NOT_MERGED 误报）；**Phase 2 = A1-A4 与 Option B 合并立项**（多变体信封透传 + merge 开关 + 上限校验 + 数据源修正） |

---

## 一、Ozon 官方事实（权威依据，带来源）

### 1.1 API 模型：无 variants 数组

**官方 OpenAPI 规范 v2.1**（docs.ozon.ru/api/seller，420 端点，全文 "variant" 出现 **0 次**）：

> "В одном запросе можно передать до 100 товаров. Каждый товар — это отдельный элемент в массиве `items`."
> "Чтобы объединить две карточки, для каждой передайте `9048` в массиве `attributes`. Все атрибуты в этих карточках, кроме размера или цвета, должны совпадать."
> （每请求最多 100 个商品，每个商品是 items[] 独立元素。合并两张卡：每张卡 attributes 传 9048，除尺寸/颜色外所有属性必须一致）

**4 个主流开源客户端交叉验证**（Python a-ulianov/OzonAPI、Go diPhantxm/ozon-api-client、TS googlesheets-ru/OzonFromGAS、PHP gam6itko/ozon-seller）：所有 `CreateOrUpdateProduct*` / `V3ImportProductsRequestItem` 结构**只有 `items[].attributes`，无 variants 字段**。

**对我方的影响**：信封 `draft.variants` 是**内部抽象**，worker 上传时需展开为多个 `items`（我方 `prepare_ozon_upload_node.py` 已这样做，方向正确）。

### 1.2 合并规则（属性 9048 = 型号名称）

| 维度 | 官方事实 | 来源 |
|---|---|---|
| 合并键 | 9048 属性（自由文本，非字典），值 = 型号名称/item_id | 官方规范 + 官方帮助 |
| 合并前提 | **同品牌 + 同类目** + 除尺寸/颜色外属性全一致 | 官方帮助「将多个商品整合至单一商品卡」 |
| 值一致性 | `"Sun-100"` vs `"Sun-100_2"` → **拆成两张卡**（值必须完全一致含大小写空格） | 官方错误文章 + OSS 实测 |
| 生效时间 | 上传后约 **24 小时**自动合并（审核后 24h 出现在网站） | 官方帮助 |
| 合并后 | 评价合并至一卡；model_id 标识合并卡 | 官方帮助 + /v3/product/info/list |
| 拆分 | 更改 9048/型号名称 特征值即可从卡中删除 | 官方帮助 |
| 可合并示例 | 不同颜色手机壳 / 不同容量洗发水 / **不同数量（含 "2 шт"）** | 官方帮助 + 官方文章 |
| 不可合并 | 不同手机型号的手机壳 / 不同设计的灯具 / 不同用途洗发水 | 官方帮助 |

⚠️ **数量变体修正**：官方把「每个包装中的商品单位数量」列为**区别特征之一，可用于合并**（如 3 包薯片 vs 5 包薯片合并一卡）。「数量变体拆独立产品」是我方 `variant_type="quantity"` 的**设计决策**，非官方强制——若拆，配额按 N 计（不返还）。

### 1.3 配额机制（1 卡 = 1 配额，官方确认）

**官方限额文章「商品上传与编辑限额」**（Ozon Global 版，中文镜像）：

| 规则 | 值 | 备注 |
|---|---|---|
| 总限额默认 | **500 张商品卡** | 无论是否过审都扣减 |
| 合并返还 | **合并 N 变体 → 配额 + (N-1)** | 官方示例：10 件不同颜色衬衫合并 → 当天 +9 单位 |
| 销售增长 | 每 +10 万卢布销售额 → +500 卡；150 万卢布 → 取消限制 | |
| 每日创建 | ≤2000 单位 | UTC 00:00 重置 |
| 每日编辑 | 默认 2000 张，质量高可升 5000 | |
| 归档释放 | 归档商品 → 配额重新释放 | |
| API 查询 | `GET /v4/product/info/limit` → `total/daily_create/daily_update` 各 `{limit, usage, reset_at}`，**-1 = 不限** | 官方 URL: docs.ozon.ru/api/seller/#operation/ProductAPI_GetUploadQuota |
| ⚠️ 2026 新变化 | **额外卡片槽位可付费购买 45-65 卢布/卡**（引 Oborot.ru） | callplex.ru 2026-07-22，单源需追踪 |

**版本差异**：俄本土账户（2022-12-22 后注册）社区口径 20,000 卡总/1,500 卡日（selsup，未官方验证）vs Ozon Global 500 卡——方案落地时以 `GET /v4/product/info/limit` 实查为准。

### 1.4 硬限制与错误

| 限制 | 值 |
|---|---|
| 单请求 items | ≤ **100**（硬上限） |
| offer_id | ≤ 50 字符 |
| name | ≤ 500 字符 |
| images | ≤ 30（v2.1 规范；旧镜像写 15 有出入） |
| 单卡变体 | 官方帮助「合并商品」文章：**最大 500**；官方推荐 **10-15**；服装类目 UI 上限 30 色/15 码 |
| 手动合并 | ≤ 100 张卡/次；>100 张须用 XLS 模板 |

**常见错误**：
- `item_limit_exceeded` — 超配额
- 429 — 限流，头 `Item-Retry-After`（分钟，日限重置莫斯科 03:00）/ `Item-Rate-Limit-Remaining`
- `/v1/product/import/info` 返回逐商品 `errors[]`（`code/message/state/level/description/field/attribute_id/attribute_name`）
- 合并失败：9048 值不一致、变体特征未填/填错、颜色字段缺失（填了「颜色名称」没填「商品颜色」）

### 1.5 变体读回（合并成功验证）

- `GET /v3/product/info/list` → 每 item 的 `model_info {model_id, count}` —— **唯一官方合并验证通道**
- `GET /v4/product/info/attributes` 也返回 model_id+count，但**有 100 条截断 bug**（TP 官方确认，2022-07 Q&A）
- 分析报表可按 `spu`（合并卡标识）分组
- ❌ `GET /v4/product/info/variants` **官方不存在**（0 命中）

---

## 二、竞品基准（毛子 / 上品帮）

> 全部原文摘录 + 出处见调研日志 wave-1-digest.md 与 `docs/competitor/*-full.md`。⚠️ 修正了 `OZON-MULTI-SKU-QUOTA.md` 的两处引用偏差（毛子 merge 开关/30 上限原文在 **WB 版**，非通用）。

| 维度 | 毛子ERP | 上品帮 | 我方（现状） |
|---|---|---|---|
| **合并机制** | OZON：`model_id` 型号名称字段（随机 `mz-{15位}`，空=默认合并）；WB：`merge` Switch | 变体特性（至少一个特征不同）+ 型号名称 | 9048=item_id 绑定（确定性、可溯源）✅ |
| **merge 开关** | ✅ WB 版显式 Switch（默认 1=合并，0=每变体单独上架）；OZON 版无开关（model_id 空即合并） | ❌ 无显式开关（变体特性隐式决定） | ❌ **无开关（恒合并）** |
| **变体上限** | OZON 无显式；WB **30**（平台约束） | OZON **80**；WB **30**（平台约束） | ❌ **无（Ozon items≤100 隐含）** |
| **数量变体** | 无专门证据（每行独立 SKU） | ✅ 「一个商品中的件数」列（同首行批量） | ✅ `variant_type="quantity"` → 独立产品 N 配额 |
| **每变体价格/成本** | 原售价 + 我的售价 + 划线价；批量=原售价倍数(默认0.95)/固定金额 | 售价/划线价/最低价 + 同首行批量；货号一键生成 | 单 SKU 平铺；多 SKU variants 每项独立 price |
| **变体属性** | ✅ is_aspect 设置/取消（后台 SKU 表） | ✅ 动态变体属性列 + 自动颜色样本 | 颜色属性 10096/10097 等每变体独立，共享属性移除颜色 |
| **配额/合并状态展示** | ✅ 在线商品展开「已合并(model.count)」 | 无直接展示 | ❌ 无 |
| **批量上架** | 单次 300 SKU / 3 分钟 / 单日 5000 | 单次 ≤1000 SKU | 无批量 SKU 上传入口（信封 variants ≤1） |

**竞品结论**：毛子的「merge Switch（默认合并）」是配额保护的关键 UI——用户可显式选择「每变体单独上架」（配额 N 份）或「合并一卡」（配额 1 份）。上品帮 80 上限防 500 硬顶。**我方两个都缺**。

---

## 三、我方现状（file:line 引用）

### 3.1 Skill 侧（client 抓取/信封组装）

| 位置 | 现状 |
|---|---|
| `cloud_probe.py:780-845` `_collapse_variants_to_single` | **无条件折叠**：数量变体→筛"1只装"中位数；无1只装→最低价；纯颜色/尺寸→中位数。恒返回 1 元素 |
| `cloud_probe.py:1905-1914` | v0.14 P0-4 起**无条件调用**折叠（注释明确兼容 0/1/N） |
| `cloud_probe.py:1970-1991` | `is_multi = len(variants) > 1` 分支是**死代码**（折叠后恒 False）；信封 `draft.variants` **恒不出现**（1 个也平铺到顶层 sku_id/price） |
| `cloud_probe.py:1696` | `effective_max_skus = max_skus or 15` — 采样上限默认 15 |
| 各入口 max_skus | `graph`=15（唯一保留多变体采样）；`discover`(2102)/`follow`(3435)/`batch_test`(209) 恒 **1** |
| `cloud_probe.py:2387-2470` `build_variant_envelope` | 遗留多变体信封构建器（merge_group/variant_key 模式），**无调用方**（n8n 旧管线时代） |
| `ozon_discovery.py:121-197` `ProductCandidate` | **无 variants 字段**——发现候选是「1 Ozon ↔ 1 1688」单产品模型 |
| `ozon_scraper.py:633-646` | Ozon 侧只抓 `aspects` 变体名（slice 20），**无变体价格/SKU 结构** |

### 3.2 Worker 侧（云端上传管线）

| 位置 | 现状 |
|---|---|
| `state.py:127-130` GlobalState.variants | `List[Dict[str, Any]]` **无 Pydantic 约束、无上限**；「≤1 元素」纯属 Skill 约定，worker 零强制 |
| `prepare_ozon_upload_node.py:2720-2728` | 变体类型路由：首个 variant 的 `variant_type=="quantity"` → 数量拆分 |
| `prepare_ozon_upload_node.py:2731-2799` | **数量拆分已实现**：每 SKU 独立产品（N 配额），offer_id=`{var_sku_id}_{suffix}`，标题追加 `, {qty_num} шт.`，剔除属性 8292 |
| `prepare_ozon_upload_node.py:2804-3044` | **9048 绑定合并已实现**：N 变体 items，颜色动态检测(10096-10099)、共享属性剔除颜色、每变体 offer_id/价格/主图/尺寸 |
| `prepare_ozon_upload_node.py:2273-2293` | 9048 = `item_id`（强制覆盖/追加） |
| `assemble_ozon_product_node.py:2590-2597` | 9048 兜底用 `offer_id` ⚠️ **与 prepare 值源不一致**（item_id vs offer_id） |
| `pricing_node.py:311-343` | `variant_prices`：读 **draft.variants**；每变体 cost=SKU 价+物流+包装 → CNY/RUB 定价公式 |
| ⚠️ 数据源不一致 | pricing 读 `draft.variants`(311)，prepare 读 `state.variants`(1551，由 ingest 提取)；`variant_prices` 按**索引 i** 对齐（无 sku_id 匹配）→ 中间节点改写 state.variants 会错位 |
| `draft_sanity.py` | **无 variants 校验**（只有 weight/尺寸/信封结构） |
| `graph.py:231-245` | `variant_primary_loop`（多 SKU）与 `main_image_gen`（单 SKU）并行，各按 variants 数量自跳过 |
| `variant_primary_loop_node.py:54-152` | 每变体生成一张主图（ThreadPool 4，fallback 白底/多角度，缓存 `variant_{idx}`） |
| `ozon_status_node.py:413-459` | `model_info` 检查：`len(model_ids)>1` 或全部 `count<=1` → `VARIANT_NOT_MERGED` 失败 ✅（已有合并验证雏形） |
| `validation_retry_loop.py:193/2160-2209` | `VARIANT_NOT_MERGED → repair_prepare`；revalidate 同步共享属性（含 9048）到 items[1+] 保留变体颜色/尺寸；9048 防重译 |
| `follow_sell_import_node.py:302/342` | 跟卖恒 `variants=[]`（单产品），9048 = `ozon_product_id` |

**现状总结**：worker 侧**已具备**数量拆分 + 9048 绑定合并 + 合并验证 + retry 修复的完整变体管线，且**无任何变体数量上限**——理论上已支持任意 N 变体（实测过 39 变体）。瓶颈全在 Skill 侧折叠（信封恒 ≤1）+ 两个数据源不一致 bug + 缺 merge 开关。

---

## 四、差距分析

| # | 差距 | 影响 | 竞品对照 |
|---|---|---|---|
| G1 | **无 merge 开关**（恒合并） | 无法选择「每 SKU 单独上架」；SKU 数量>类目容量的商品（如 1688 多色多码 SKU）无法拆分上架 | 毛子 WB 有 Switch；上品帮变体特性隐式 |
| G2 | **无变体上限校验** | 超 500 硬顶 / 超 100 请求上限 → 上传失败 | 上品帮 80 / 毛子 WB 30 |
| G3 | **9048 值源不一致**（prepare item_id vs assemble offer_id） | item_id 缺失时两处生成不同 9048 → 合并失败（拆卡） | — |
| G4 | **pricing/prepare variants 源不一致 + 索引对齐** | 中间节点改写 state.variants 会错价 | — |
| G5 | **skill 折叠策略不可配置** | 无法透传多变体；无法选择代表变体策略 | 毛子/上品帮均有变体表 |
| G6 | **无合并成功读回验证闭环** | 仅 ozon_status 被动检查，无主动 model_info.count 确认 | 毛子后台「已合并(model.count)」展示 |
| G7 | **2026 付费槽位**未纳入成本模型 | 配额不足时的成本未知 | — |

---

## 五、方案选项

### Option A — Worker 侧最小改动（Phase 1 即时价值 / Phase 2 随 B 合并）

> ⚠️ oracle 评审（2026-08-20）重排：**A6+A5 先行**（即时价值 + 修误报源）；**A1-A4 与 Option B 合并立项**（死代码不单独排期）。改动表按新优先级排列。

**目标（Phase 1）**：配额预检 + 合并验证异步化，不依赖 skill 变化即可落地。

| 改动 | 位置 | 内容 |
|---|---|---|
| **A6 配额预检**（先行） | submit/prepare | 调 `/v4/product/info/limit` 预检 daily_create 余量；`item_limit_exceeded` 从「上传后失败」变「上传前拦截」。⚠️ merge=true 时 Ozon 可能先按 N 暂扣、合并后返 N-1（R3 未明说）——按 1 预检但容忍临时 N 暂扣；merge=false 按 N 预检 |
| **A5 读回验证·异步化**（先行，oracle 修正） | 新异步 job + product_summary | **同步路径降级为「只记录不判定」**：现有 `ozon_status_node.py:413-459` 的 `model_info` 检查在 24h 合并完成前会误报 `VARIANT_NOT_MERGED`（count=1 时）或误放（model_info 空时走 else）→ 移除失败判定，仅写日志/遥测。合并验证迁到**独立后台 job（cron/队列，24-48h 后回查 `/v3/product/info/list` 的 `model_info.count`）**，结果回写 task 记录；`VARIANT_NOT_MERGED` 重定义为「24h 后仍未合并」才触发，且**不自动 `repair_prepare` 重传**（task 30min 超时物理上无法内联 24h 验证） |
| A1 merge 开关 | `state.py` extensions + `prepare_ozon_upload_node.py` | extensions 加 `merge_variants: bool`（默认 true）；false 时每变体独立卡（N 配额）。⚠️ oracle 补充两个子项：① **必须从共享属性主动剥离 9048**（assemble 已注入 `9048=item_id` 到基础属性，不剥则独立卡仍同 9048 → Ozon 照样合并，开关失效）；② **offer_id 改确定性**——现用 `f"{var_sku_id}_{int(time.time())%1000000}"`（prepare:2260/2741/3017），重试时后缀变 → offer_id 变 → `repair_prepare` 重传建**重复卡**；改 `var_sku_id` 直用 + 缺失时 `{sku_id}_{i}` 索引兜底。参考 quantity 分支（2731-2799）但不直接复用（它剔 8292 且不做颜色/尺寸处理；颜色/尺寸独立卡需保留区分属性 + 标题消歧追加颜色词防同名审核） |
| A2 变体上限校验（三层） | `draft_sanity.py`（入队前）+ prepare（兜底） | **拆三个维度，非一个常量**：① 软上限 `MAX_VARIANTS=30`（业务默认，extensions/config 可配置；对齐上品帮 80 保守值/服装 UI 30）；② 单请求 ≤100 items（传输硬限，>100 拆多个 /v3/product/import 调用）；③ 单卡 ≤500（平台硬顶）。**超限行为必须确定**：入队前硬拒 INVALID_REQUEST（省生图/翻译成本）或显式分批，**绝不静默截断丢 SKU** |
| A3 9048 值源统一 | `assemble_ozon_product_node.py:2590` | 兜底改读 `item_id`（与 prepare 一致）。⚠️ oracle 校正：prepare 在 `if item_id` 时强制覆盖 9048，**仅 item_id 缺失才暴露不一致**——属边缘 bug，随 A1-A4 顺手修，不单独排期 |
| A4 variants 源对齐 | `pricing_node.py:311` + prepare | pricing 改读 `state.variants`（与 prepare 一致，防中间节点改写 state.variants 后漂移）；`variant_prices` 改按 `sku_id` 匹配（现按索引 `i` 对齐 prepare:2748/2949，重试重排即错价） |

**成本**：A5+A6 先行 = Medium(1-2d)；A1-A4 随 B 立项 = Medium-Large(2d+)。**风险**：低（不动现有合并路径默认行为；A5 移除的是误报源而非功能）。
**验证**：worker 全量测试 + 真实店铺 5423887 多 SKU 信封直传验证（本地 Docker）+ 24h 后异步验证 job 回查。

### Option B — Skill 多变体信封透传（后续演进）

**目标**：让 discover/follow/batch 真正支持多变体上传（对齐竞品变体表）。

| 改动 | 位置 | 内容 |
|---|---|---|
| B1 折叠可配置化 | `cloud_probe.py:1905-1914` | `_collapse_variants_to_single` 加 `max_skus` 参数（默认 1 保持现状）；>1 时改为「采样截断不折叠」 |
| B2 信封透传 | `cloud_probe.py:1970-1991` | 激活 `is_multi` 死代码分支：`draft["variants"] = variants`（≤N，对齐 A2 上限） |
| B3 各入口放开 | `discover(2102)/follow(3435)/batch_test(209)` | `max_skus` 从 1 → N（默认 3-5 保守） |
| B4 候选数据结构 | `ozon_discovery.py:121` ProductCandidate | 加 `variants` 字段（或复用 build_graph_envelope 的 option_groups 笛卡尔积重建） |
| B5 变体表 UI | 无（CLI 场景）；后续 webui 若做变体编辑再加 | — |
| B6 复用遗留实现 | `build_variant_envelope:2387` | 视需要启用/重写（merge_group/variant_key 模式可参考） |

**成本**：skill 4-5 处 + 信封契约（CONTRACT-v4）版本号更新 + 测试面大。**风险**：中（信封结构变更影响 skill/worker 契约；变体数量上限校验需 worker 侧配合）。

### Option C — 数量变体策略按类目检测（独立小项，可与 A/B 并行）

- 现状：`variant_type="quantity"` 一刀切拆独立产品（N 配额）。
- 优化：若类目 schema 有数量属性且 `is_aspect=true` → 数量可作为区别特征**合并**（省配额，官方支持）；否则拆卡。
- 位置：`prepare_ozon_upload_node.py:2720-2728` 路由前查类目属性 schema。
- **注意**：合并后「2 只装」与「5 只装」价格/库存需每变体独立（已有 variant_prices），且要防合并后单 SKU 无货影响整卡。

---

## 六、推荐（oracle 评审修正版）

**分两阶段**：Phase 1 = A6 + A5（即时价值，独立落地）；Phase 2 = A1-A4 与 Option B **合并立项**（「多变体上传」项目）。

理由（oracle 评审结论）：
1. **A1/A2/A3/A4 全是 `draft.variants>1` 才触发的路径，而 skill 恒折叠（信封 ≤1）——在 B 落地前是死代码**。分开排期会「交付了却无人能触发」；合并进 B 统一排期才产生价值。
2. **A5/A6 是即时收益**：A6 配额预检让 `item_limit_exceeded` 上传前拦截；A5 移除现有 `VARIANT_NOT_MERGED` 同步误报源（24h 合并未完成时 count=1 会误触发 retry 重传）。
3. **B 是「真正多变体上传」**，价值大但需信封契约变更（CONTRACT-v4 版本号）+ skill/worker 全链路测试。C（数量变体按类目）优先级最低，仅在配额真成瓶颈时再做（需额外维护每变体价格/库存同步 + 断货影响整卡，复杂度不值）。

## 七、风险与未决项（oracle 评审补充）

| # | 项 | 处置 |
|---|---|---|
| R1 | 2026 付费槽位（45-65 卢布/卡）为单源（callplex 引 Oborot.ru） | 落地前追 Oborot.ru 官方公告确认价格表 |
| R2 | 俄本土配额 20,000/1,500（selsup 社区口径）vs Global 500 | 以 `GET /v4/product/info/limit` 实查为准 |
| R3 | 数量变体合并后配额是否按 N-1 返还，官方未明说 | A6 落地后真实店铺实测验证 |
| R4 | 9048 值 maxLength（官方未给，需查类目属性 schema） | B 实施时经 `/v1/description-category/attribute` 确认 |
| R5 | 单请求 100 上限是否含已合并变体（官方未明说） | A2 分批逻辑按 100/请求保守处理 |
| R6 | **合并验证 24h 时效（合并是异步的）** | **A5 已改异步 job（oracle 修正）：同步轮询只记录不判定，24-48h 后台回查；VARIANT_NOT_MERGED 重定义为「24h 后仍未合并」且不自动重传** |
| R7 | **merge=false 独立卡的 9048 剥离**（oracle 新增） | A1 必须主动剥离共享属性 9048，否则开关失效 |
| R8 | **时间戳 offer_id 破坏重试幂等**（oracle 新增） | A1 改确定性 offer_id，防 repair_prepare 重传建重复卡 |

---

## 八、来源清单

**官方**：docs.ozon.ru/api/seller（OpenAPI v2.1 规范，ozon-mcp 镜像逐字一致）· seller-edu.ozon.ru 限额/合并/错误文章（307 反爬，经中文镜像逐字核对：chwang.com/guide/186965910725、186966202919 · ozon.menglar.com/article/operate/20421 · eluosilvshi Ozon-help 翻译）· seller.ozon.ru 官方文章（Web Archive 20250122112105）

**竞品**：docs/competitor/maozier-plugin-full.md（§3.2/§3.4/§3.7/§8.2）· maozier-backend-full.md（§4.1.3/§4.3.1/§4.3.3/§4.7）· shangpinbang-full.md（§1/§2.1/§3.3/§3.4/§22.1）· 修正了 OZON-MULTI-SKU-QUOTA.md 的两处引用偏差（merge 开关/30 上限仅 WB 版）

**开源实操**：a-ulianov/OzonAPI · diPhantxm/ozon-api-client · googlesheets-ru/OzonFromGAS · gam6itko/ozon-seller · DragonSigh/ozon-seller-api-docs · Habr Q&A（attributes 100 条截断 bug）· Moysklad 官方博客（4 类合并区分）

**社区**：reg.ru · secrets.tbank.ru · veroliki.ru · sellermarket.ru · callplex.ru（2026 付费槽位）· lianlianpay（配额旧制）· selsup（俄本土限额，未验证）

**代码**：worker/src/graphs/{state.py, nodes/prepare_ozon_upload_node.py, nodes/assemble_ozon_product_node.py, nodes/pricing_node.py, nodes/ozon_status_node.py, validation_retry_loop.py, draft_sanity.py} · skill/scripts/cloud_probe.py · skill/scripts/lib/ozon_discovery.py

---

## 九、实测验证记录（2026-08-20，真实店铺 5423887 + 本地 Docker worker）

> 用 1688 产品 1047702285425（4 变体：小粉马/小青蛙 × opp袋/彩盒，真实价 6.6/11 CNY）构造 4 变体信封直传本地 worker，验证配额/合并/单请求行为。

### 9.1 已验证事实

| 验证项 | 结果 | 证据 |
|---|---|---|
| 单请求上传 | ✅ 4 变体一次请求成功，`moderate=approved` | product_id 6038432275，4 变体独立 sku_id/price |
| 配额占用 | ⚠️ **按 item 扣**：4 变体 = 4 配额（非 1） | daily_create 1→9（2 次×4）、total 199→207 |
| 合并 | ❌ 未合并（count=1，单独一卡） | model_id 6188124276 count=1 |
| 24h 自动合并 | ❌ 不会发生——非时序问题，是**数据问题**（变体特性无法匹配） | `double_without_merger_offer` 错误 |

### 9.2 实测发现 3 个 bug

**Bug 1（已修复）— `main_img` 未定义崩溃（P0）**
`prepare_ozon_upload_node.py:2563`：`main_img` 只在 `if has_variant_images:` 块内定义，但多 SKU 分支（variants>1 且变体主图全失败）引用它 → `UnboundLocalError` → retry 死循环（每次重跑生图烧 MXOU 费用）。修复：提升定义到块外。**已修复并重建容器验证**。

**Bug 2（待修）— 时间戳 offer_id 破坏合并 + 幂等（P0）**
`prepare_ozon_upload_node.py:2260`：`offer_id_suffix = int(time.time())%1000000`。两次 prepare（retry 触发）生成 `_198560`/`_199038` 两批不同 offer_id → Ozon 视为 8 个独立商品 → **变体永远无法合并** + 重试建重复卡。需改确定性 offer_id（`{var_sku_id}` 直用 / `{sku_id}_{i}` 索引兜底）——对应 A1 第二子项。

**Bug 3（待修）— 首次上传 9048 缺失（P0）**
assemble 日志 `必填文本属性9048 无默认值，跳过写入空值` → 首次上传 4 变体无 9048 → 变体特性缺失 → 无法合并。prepare 的 9048 兜底（2273-2293）只在 `item_id` 非空时补——多 SKU 场景需在 prepare 多 SKU 分支强制补 9048。

### 9.3 其他实测注意点

1. **颜色映射**：`小粉马→白色` 走 FALLBACK_COLORS 兜底（中文色名不匹配），4 变体颜色=白/透明/米/黑（OK 但语义失真）；首次上传颜色值竟是中文被 ozon_validate 拦截（`属性10096含中文字符`）——retry 修复后才转俄语
2. **FBP 条形码**：确认可不填（barcodes=[] 也 approved）
3. **生图失败链路**：首次生图全失败 → 无营销图 → `has_variant_images=False` 仍走多 SKU 分支（Bug 1 修复后不崩，但商品无图 → `image_absent_with_shipment` warning）
4. **daily_create 上限 100**：该店铺配额紧张，多 SKU 大量上传会撞

### 9.4 修复后重测预期

修 Bug 2/3 后重新上传：offer_id 确定性 → 同 item_id 变体 9048 一致 → 24h 内合并一卡 + Ozon 返还 N-1 配额 → `model_info.count=4`。重测命令：本地 Docker + `test_multisku.py`（信封 4 变体直传）。
