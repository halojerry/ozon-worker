# PLAN — 跟卖模式 v1（follow_clone）：竞品卡零 LLM / 零生图上架

> 状态：**草案（待拍板）** · 起草 2026-10-03 · 前置探针 B0 因网络阻塞待跑
> （SG 出口被 Ozon/WB 拉黑，切节点后重跑）。
> 姊妹篇 `PLAN-product-refresh-v1.md`（存量卡翻新）——共享主图作钥匙 /
> 模式作用域图片 lane / 前 20 均值锚 / 货源标注四件，实施可交叉复用。

## 动因

- 主链（graph/follow）每单必走生图 5 槽 + 4191 撰写链 + vision，API 成本高、
  链路长；竞品已在售卡自带：俄语标题/描述/属性/dc-tp/原图——**内容生产环节
  对跟卖场景是纯浪费**。
- 选品面已有现成资产：discover 漏斗（`DEFAULT_MAX_COMPETITORS=50`、
  `competing_sellers`、`sales_growth`、`min_competing_price`）+ follow 的
  aibuy 图搜源匹配（1688 货源）+ `pricing_core` 锚价链。
- 用户拍板（2026-10-03）：
  1. 能复制商品卡 → 直接复制，重新匹配货源算价上架；
  2. 不能复制 → 克隆竞品原图/信息/特征属性上架（**原图 URL 直传，不落 COS**）；
  3. 不生图、不 LLM、零 API 消耗；
  4. 选品：近 7/30 天大增长 + 跟卖数 ≤50 + 利润闸过关；
  5. 定价：**跟卖列表前 20 报价的均值**做锚（不足 20 取全部），出单后用户自改；
  6. 货源标注：1688 直上与跟卖两条上架路径统一落库，webui 可见货源。

## 与现有资产的对齐（复用 vs 新建）

| 环节 | 复用 | 新建 |
|---|---|---|
| 选品 | discover 漏斗 + what_to_sell 增长数据 | 7d/30d 增长筛选参数化 |
| 源匹配 | follow 的 aibuy 图搜 → 1688 货源 | 无（原样） |
| 锚价 | fetch_competing_sellers 的 sellers[]（含价格） | 前 20 均值计算 + **选品时物化**（price_sanity 红线：禁引用化） |
| 卡内容 | 竞品卡自带 dc/tp/俄语标题/属性/图 | 零写作零翻译——**全量回显** |
| 定价 | pricing_core（锚价 + 利润闸） | follow_clone margin profile（默认 factor 1.0 卡均值） |
| 图片 | Ozon CDN 原图 URL 直传 import（C 实录：接受并挂图） | 模式作用域放行 external |
| 上传 | import 链 / UPSERT_BY_OFFER / min_price 补送 | follow_clone 模式标记贯通 |

## 核心设计

### 1. 单分支：克隆独立卡（B0 实录收敛，原「绑定优先」判死）

```
选定竞品卡（选品漏斗出）
   │ CDP 读卡（A 实录：API 读不到竞品——webCharacteristics DOM 链）
   ↓
克隆载荷：竞品 dc/tp + attributes 全量回显（id 键 + snake_case dict id，D 实录）
         + images = 竞品 Ozon CDN 原尺寸 URL 直传（C 实录：import 接受并挂图）
         + 新 offer_id + 均值锚价
   ↓
import → 独立新卡（B 实录：三轮全部 INDEPENDENT，无自动并卡）
   ↓
48h 审核观察（重复内容拒审率 = B5 gate 核心观测项）
```

- 原「绑定分支（offer 挂竞品卡，images=[] 白嫖）」**判死**：Ozon 对跨店
  逐字克隆不并卡（B 实录三轮），显式 привязка 无 API 通道——文档留此判决
  防后续再议；
- 跟卖卡 images=[] 锁语义保留（那是对**本店已有卡** UPDATE 的豁免，与克隆无关）。

### 2. 零 LLM / 零生图 lane（worker 侧短路）

信封 `extensions.follow_clone = true`（新键，envelope_contract 登记）：
- 类目：**跳过整条匹配链**（L0/Skill/LLM 仲裁全免）——竞品卡 dc/tp 直接采
  （权威来源 = 竞品在售事实，比 page 面包屑更硬）；
- 内容：跳过 4191 撰写链 / vision / 数字锚定闸 / rich content 生成——
  竞品属性全量回显（含 4191/11254 若有）；
- 生图：整段子图跳过（IMAGE_GEN 禁入口）；
- 图片出口闸：`_enforce_payload_image_policy` / `enforce_upload_policy` 对
  follow_clone 克隆分支放行 `external`（Ozon 原尺寸 CDN URL，复用
  `filter_reference_images` 的原尺寸过滤——缩略/.webp 恒拒不变）；
  **全局闸不松，只开模式作用域口**（v0.78 批E 事故的反向决策，用户拍板留痕）；
- 跟卖对本店已有卡的 images=[] 锁语义不变（那是 UPDATE 豁免，与克隆无关）。

### 3. 定价：前 20 均值锚

- 数据源：`fetch_competing_sellers` 的 sellers[]（价格升序）；
- 计算：`anchor = mean(前 20 个报价)`，不足 20 取全部；**选品时物化进信封**
  （`pricing_info.anchor_price` 语义对齐 price_sanity 红线——绝不引用化）；
- 上架价缺省 `= anchor × factor`（factor 默认 **1.0**，env/信封可调；
  涨幅策略留给用户出单后自改——三档 old_price/promo 价逻辑沿用主链）；
- 利润闸：`compute_pricing_core` 常规链（成本 = 1688 货价 + RETS_Economy
  物流 + 包装）；**均值算出低于成本线 → 拒**（宁缺毋滥，与
  commission_fallback_not_profitable 同语义）。

### 4. 货源标注（两条上架路径统一）

- 采集侧：信封 `source.url / source.item_id`（已有，graph 直上与 follow 同形）；
- 落库：`listing_result_log` 补 `source_url` / `source_item_id` 两列
  （graph_result 优先、信封回落，沿用留存表取值优先级注释纪律）；
- 露出：任务查询 / listing 相关端点带出两字段（API 文档重生成）；
- webui：卡片/列表显示货源链接（1688 URL 直点）。

### 5. webui 预览 URL 直链

- 草稿态：`DRAFT_IMAGE_MIRROR` 已默认停（v0.77.1），原始 URL 直链预览
  ——克隆草稿用 Ozon CDN URL，**零 COS 存储零生命周期风险**；
- 在架卡：Ozon 自己托管，与源 URL 无关。

### 6. 租户×店铺数据边界（与翻新篇 §4 同口径）

- 信封随店凭证提交（每店各自的 client_id/api_key，SKU/价格/锚价互不相通）；
- 货源标注落 `listing_result_log`（租户列已有）；均值锚物化在**本任务信封**内，
  不跨任务跨店引用；
- 类目学习表/字典缓存/sku_metrics_pool 保持 **W11 全局共享**（一店经验全网复用），
  跟卖来源写入时 follow_type 置信压制既有纪律不变；
- 新增端点一律走 `api/deps_tenant`（v0.75 租户口径）。

## 批次划分

### B0 探针批（✅ 已完成 2026-10-03，实录）

`worker/scripts/probe_clone_card.py`（读/写分凭证 = 跨店跟卖一比一模拟：
4718259 卡主凭证读卡 → 5381204 卖方凭证提交）。四轮结论：

**A. Seller API 读不到竞品卡**——`/v3/product/info/list` 对他人 pid 回空集、
`/v4/product/info/attributes` 404（product info 域只覆盖本店）。
→ **竞品卡数据获取必须走 CDP 链**（v0.80 A7 webCharacteristics DOM 链已在役，
follow 同款）。

**B. 跨店逐字克隆不并卡——「绑定分支」不存在于 API 通道**。三轮递进实测
（弱克隆 / 全属性克隆 / 全属性+图克隆）全部 INDEPENDENT 新 pid：
- 弱克隆（属性无 id）→ 新 pid 6515844689（已归档清理）；
- 全属性克隆（12 attrs 含 9048 + 重量尺寸）→ 新 pid 6515855894（已归档）；
- 全属性 + `primary_image` CDN 主图 → 新 pid 6515871405（**保留 48h 审核
  观察样本**，测试店 5381204）。
→ **克隆独立卡是唯一 API 跟卖路径**；「挂 offer 到竞品卡」Ozon 不给 API
通道（UI 手动 привязка 存在但非 API）。**两分支判定收敛为单分支：克隆。**

**C. Ozon 自家 CDN 图 URL 直传 import 实锤**——第 4 轮克隆载荷 images=
`primary_image` 的 ir-20.ozone.ru URL，import 后验证读新卡 images_n=1。
→ 竞品图零下载零 COS 直上卡机械可行。

**D. 回读通道形状实录（B2 构造器产品化直用）**：
- v3 `/product/info/list`：body `{"product_id": [字符串]}`（int 或带空
  offer_id/sku 数组 → 空集）；响应**顶层 `items` 无 result 包装**，卡主键 `id`；
- `images` 键对这些卡**恒空**，主图真身在 **`primary_image`**（CDN URL 列表）；
  其余图可从 11254 rich JSON 的 src 提取；
- v4 `/product/info/attributes`：属性主键是 **`id`**（非 attribute_id），
  `dictionary_value_id` snake_case（card_echo 注释的 camelCase 口径按此修正）；
- 新建卡 v4 立即读 404（索引延迟），验证读走 v3。

**E. 克隆属性必须用生产形状（2026-10-04 留观卡六错→清零实测，第 5/6 轮）**：
- 属性对象 `{"complex_id": 0, "id": N, "values": [...]}`（= prepare 生产形状）——
  用 `attribute_id` 键且无 `complex_id` 的形状，**optional 属性能绑上、required
  属性被校验路径丢弃 → `error_attribute_values_empty`**（10096/4295/9163/8292
  四个 required 实锤；换生产形状后四属性全绑、六错全清）；
- 值对象**两键恒发**（`dictionary_value_id` + `value` 同发）——单发 dict_id
  同样被判空；
- **v3 回显无 weight/dims 字段**（`missing_dimension` 根因）——重量从 4497
  （Вес товара）属性取或 CDP/信封 weight_g；尺寸同理走属性或 CDP；
- 留观卡 6515871405 经生产形状修复后 **errors 清零**，审核观察在有效样本上
  继续（此前样本带错不可售，观察无意义）。
- **教训（B2 评审自责）**：clone_card_builder 初版发明了与生产 prepare 平行的
  属性形状——「零 LLM 逐字回显」的正确姿势是**形状也逐字对齐生产管线**，
  不是只对齐值。此教训已进模块 docstring + 测试断言。

### B1 skill 侧：选品 + 均值锚 + 信封（Tier A worktree）

- `follow --clone`（复用 follow 源匹配链）+ discover 挑选腿出 clone 出口；
- 筛选参数：`--growth-7d/--growth-30d`、`--max-sellers`（默认 50，已有常量对齐）；
- **增长窗字段现实（2026-10-03 核实）**：
  - 候选面现仅有 `sales_growth`（=analytics `sales_dynamics` **月动态单窗**），
    且依赖 analytics face——v0.74 token 寿命问题下实测常拿不到（30 单 gate
    has_analytics=false 为主）；
  - **双窗机制可行**：`what_to_sell/data/v3` 有 `filter.period`（现硬编码
    weekly）——period=weekly + period=monthly 两拉按 pid join 可算增速
    （如 (周销×4.33−月销)/月销 加速度），代价是 analytics face 调用翻倍；
  - v1 口径：**有 analytics face 时双窗 join，face 降级时回落月动态单窗，
    两者都没有 → 该筛选项「不限」如实放行**（不编造增长值）。
- competing_sellers 前 20 均值计算 + 信封物化（存档候选的
  `competing_seller_list` 实测为空——**均值必须选品时现算**，不可依赖存档行）；
- 信封 `extensions.follow_clone` 组装；预估打印（预估↔上架价一致性口径）。

### B2 worker 侧：follow_clone lane（Tier A worktree）

- ingest 识别 follow_clone → 类目/内容/生图三段短路（§2）；
- 克隆构造器：竞品卡回显 → import 载荷（`id` 属性主键 + snake_case
  dictionary_value_id + `primary_image` 兜底 + 重量尺寸透传，B0-D 实录形状
  探针脚本产品化）；
- 图片出口闸模式作用域放行 + 测试锁定（全局闸不松的回归用例）；
- envelope_contract 新键登记 + `gen_contract_docs` 重生成。

### B3 定价接线（可与 B2 同批）

- follow_clone margin profile（factor 默认 1.0）+ 利润闸 fail-closed；
- 三档价沿用主链（old_price 规则/enforce_old_price_rule 不豁免）。

### B4 货源标注 + webui（Tier A worktree）

- listing_result_log 两列（init_data 幂等迁移）+ 端点露出 + gen_api_docs；
- webui 货源链接 + 草稿 URL 直链预览。

### B5 实机 gate

- 测试店 ≥5 单全走克隆路径；已有观察样本 6515871405（B0 第 4 轮，
  48h 审核结论回填本节）；
- 克隆卡 48h 审核观察（重复内容拒审率）；
- 零 LLM/零生图账面自证：mxou_call_ledger 该任务 model 调用数 = 0。

## 明确不做

- **不做翻译**（竞品俄语内容原样回显——零 LLM 的本意）；
- **不动 seller-actions 族**（自建促销不受本次 v2 迁移影响，已另行卫生批）；
- **不放松全局图片闸**（只开 follow_clone 模式口）;
- **不做跨店竞品监控**（选品面够用，别长成爬虫项目）；
- 克隆分支若 B0 实证必然并卡 → **不做克隆分支**（绑定已覆盖）。

## 风险登记

| 风险 | 缓解 |
|---|---|
| 克隆卡被 Ozon 判重复内容拒审 | B5 48h 观察；拒审 → 克隆分支降级/下线，绑定分支不受影响 |
| 绑定挂卡机制与预期不符（无显式绑定 API） | B0 实证；最坏走克隆分支并卡路径（同一机制） |
| 均值锚被异常高价拉偏 | 前 20 升序取值天然抗单个离群；利润闸兜底 |
| 竞品卡 API 读不全（部分类目属性受限） | B0 实录 A；CDP webCharacteristics 链兜底（v0.80 A7 已证） |
| 跟卖定价高于竞品最低价曝光吃亏 | 用户拍板口径（出单后自改）；factor 可调 |
