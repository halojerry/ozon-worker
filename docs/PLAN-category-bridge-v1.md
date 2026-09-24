# PLAN category-bridge-v1 — follow 类目毒 ID 根治 + EN 确定性桥 + 错误分流

> 分支 `fix/category-bridge-v1`（2026-09-24 开工）。动因：2026-09-24 真机 gate follow ×5 全灭
> （`invalid Request.Items.TypeId: value must be greater than 0`），根因四环闭环取证在本文 §1。
> **改类目链前先读完 §1-§2**；B2/A2 对照实验（§1.3）是本方案的行为依据。

## 1. 取证（全部 2026-09-24 实测，可复现）

### 1.1 事故链（终版——2026-09-24 深挖反转，防线日志与复制卡实证）

**初版结论（已修正）**：「worker 见 isdigit 就信、毒值直达 import」——**错**。防线
日志实证 worker 三道闸全部正常工作：

1. `ozon_scraper.py` 把前台 Web ID 同值双塞 dc/tp（毒源，v0.11 遗留）；
2. worker `_verify_category_schema`（schema API 自校验）对毒 ID **正确 400 拒绝**
   （实测 14762/38578 → `category ... is not found`）；
3. 数字直查树无果 → **正确置空**（v0.20「绝不保留原始值」）；
4. 门控仲裁 EN 面包屑无候选 → 置空；
5. hand 类目解析失败 → **降级 api import-by-sku 复制竞品 → 成功**（复制卡
   6443818882 实测在架，created 07:49:40）；
6. **真雷**：`/v3/product/import` 官方契约 `items[].required =
   [description_category_id, price, type_id]`——**UPDATE 项同样必填**。prepare
   按「UPDATE 空=省略字段」组装 → proto 默认 0 → 网关 400
   `invalid Request.Items.TypeId: value must be greater than 0`；
7. 400 被误分类「审核拒绝」进 error_repair_llm 循环（LLM 修不了类目）→ 原样重炸
   → 终态**假 failed（卡真在架）**——今早 5 单全部这个形态（测试店现存 5 张复制卡）。

B2/A2 对照实验（§1.3）依然成立且解释升级：B2 成功不是因为「skill 信封换了值」，
而是合法 dc/tp 让 hand 走 CREATE 不降级；A2 死于属性中文（独立缺陷不变）。

### 1.2 两套 ID 体系（决定性证据）

- 前台 storefront 与 Seller API 是**平行 ID 空间**，无官方映射端点（mcp ozon 全方法
  search 确认：tree + attribute×3 + import，无任何 web-id → seller-id 端点）。
- 同类目实例：前台 `House & Garden > Storage > Organizers and dividers > Cases`（web
  14762，父链 14500/14759）= Seller 树 `dc=17027937 / tp=95483`。web 三 ID 在 Seller 树
  16552 节点（本地全量）零命中；`Дом и сад` 前台=14500 / Seller 树=17027494。
- 竞品卡反查不通：`/v3/product/info/list` 对他人 product_id 返回 items:0（自家限定）。

### 1.3 B2/A2 对照实验（方案行为依据）

同款 f1 信封（竞品 3465392291 / 货源 1030281440854），仅改 dc/tp：

| 变体 | dc/tp | 结果 |
|---|---|---|
| B2 | 17027937/95483（合法） | **completed + approved**，OzonID 6443821910，卡上类目逐字命中，4 图全 AI 链，零库存（rFBS/sds，stocks 空）——毒 dc/tp 是唯一死因，隔离验证成立 |
| A2（f2 信封） | 置空（剥毒） | 类目关通过：EN 面包屑 jieba 无候选 → 降级 → retry 整卡重配 → **R2b 采纳「住宅和花园>食物贮藏>冰箱收纳箱」conf 0.7（语义正确）**；死在独立缺陷（follow 变体属性中文清洗漏网） |

### 1.4 三语同 ID（用户情报 + 实测确认）

`/v1/description-category/tree` 的 `language` 参数（DEFAULT/RU/EN/TR/ZH_HANS）只换名字
不换 ID：本地树已有 RU 7992 行 + ZH_HANS 7992 行（同 dc/tp 双行）；live 拉 EN 树确认
`17027937 = Storage / 95483 = "Storage Case"`——**EN 叶子名与前台面包屑/竞品标题一字
不差**（前台展示名与 Seller 树 EN 名同源）。⇒ EN 面包屑对 EN 树做文本匹配 = 确定性桥，
不经 LLM。

### 1.5 官方契约要点（mcp describe + context7 /websites/ozon_ru_api_seller）

- import 官方错误码含 `description_category_invalid`（类目不存在）、
  `description_category_has_no_description_type`（type 不属于该类目）——毒 dc/tp 语义
  上必中其一；当前被模板文案掩盖。错误分流按 code 走类目分支。
- `/v4/product/info/attributes`：按 product_id 反查**自家**商品全量已填属性（含
  dictionary_value_id + attributes_with_defaults）——「同叶子自家 approved 卡属性模板
  继承」通道（二期属性批用）。
- GitHub 开源（gam6itko/ozon-seller PHP、diphantxm/ozon-api-client Go、
  ozon-api-client PyPI）全是 API client，**无前台→Seller 类目桥先例**——本方案为自研。

## 2. 设计口径

### 红线（不变）

- dc/tp 仅接受 Seller 树 ID；信封携带的其它一切 ID 都是线索不是真相。
- skill 不做类目匹配决策（纯采集）；匹配归 worker 类目链。
- 宁阻断不错挂：桥解不出 → 走既有学习表/门控仲裁 → 仍无 → 入箱。

### 2.1 skill 侧（采集层，只出真相不出猜测）

`ozon_scraper.py` 面包屑段重写：

```python
# 旧（删除）：
#   result["description_category_id"] = best.get("category_id", "")
#   result["type_id"] = best.get("category_id", "")   # ← 一鱼两吃，v0.11 遗留事故源
# 新：
result["web_category_id"] = best.get("category_id", "")  # 前台 ID，仅排查线索，永不进 dc/tp
result["category_path"] = category_path                    # 文本路径（EN/RU/ZH 原样）+ breadcrumb_language
# description_category_id / type_id 键不再产出（缺省语义=未解析，与 _discover_page_truth 对齐）
```

follow 信封构建（cloud_probe）：`draft.ozon_category` 只带
`{category_path, breadcrumb_language, web_category_id, source:"page"}`；dc/tp 字段省略。
存量兼容：老信封带毒 dc/tp 的，由 worker 侧校验门拦（2.2），不依赖 skill 升级顺序。

### 2.2 worker 侧（边界强制 + 确定性桥）

1. **复制卡反查回填（核心修复，已实施）**：import-by-sku 确认点（product_id 到手）
   立即 `/v3/product/info/list` 反查复制卡 `description_category_id/type_id` 回填
   state（官方复制带出的真实类目——实测 6443818882 → 17027933/970742618，比文本
   仲裁准，含品牌子类目等 Seller 树外叶子）。类目解析段优先消费该值（覆盖信封值）。
2. **UPDATE 硬闸（已实施）**：import_by_sku_ok 且 dc/tp 仍空 → 二次反查兜底 →
   仍无 → 显式 failed「UPDATE 项缺类目」拒绝空类目进 prepare（那里会按空=省略
   组装必 400）。product_id 仍透传（卡已建成事实不丢，可人工恢复）。
3. **EN 树导入（零代码，运维命令）**：现成 `refresh_category_tree.py` 已语言参数化
   （API 直拉全量 upsert）——部署后跑一次
   `python scripts/refresh_category_tree.py --languages EN` 即落 EN 行（7992 同 ID
   节点）。新机初始化走 init_data 时后续按需补 assets JSON。
4. **EN 面包屑匹配腿（拆二期，随属性填满批）**：涉及 ozon_category_query 搜索行为
   （类目链红线区）+ 测试面大；且止血核心（复制卡反查回填）已根治降级 UPDATE 场景，
   EN 腿剩余价值=hand CREATE 定稿率提升，非紧急。二期与属性批同车。
5. **错误分流**：import 400 且响应文本含 `description_category_invalid` /
   `description_category_has_no_description_type`（容错大小写与下划线变体）→ 走
   `_try_recategorize_card` 通道（R4 既有），**不进 error_repair_llm**；终态文案如实
   「类目无效（信封或匹配层产出非法 ID）」。

### 2.3 明确不做（本期）

- 属性填满三层策略（自家模板继承 / 消歧不 skip / 布尔默认）——行为变更大户，独立 PR
  （PLAN-attribute-fill-v1，二期）。
- follow 变体属性中文清洗漏网——随二期属性批（A2 死因，非类目链）。
- 前台 web-id ↔ seller-id 映射表——不做，无官方端点、易过期；EN 文本桥足够。

## 3. 测试

- skill：`test_ozon_scraper*.py` 改断言（不再产出 dc/tp；web_category_id 保留）；
  信封快照更新（毒值消失）。
- worker：
  - `test_follow_envelope_dc_tp_guard.py`（新）：树外 ID / dc=tp 同值 / 合法 ID 三态——
    前两态必须置空走兜底且日志留痕，第三态直通。
  - EN 匹配：EN 树行单测（本地库导入后）+ 「Storage Case」面包屑 → 17027937/95483
    回归。
  - 错误分流：`description_category_invalid` 400 → recategorize 通道断言（不调 LLM）。
- 全量：worker（PG 5433 本地惯例端口 → 实测 15433）+ skill + 双侧 CI 口径 lint。

## 4. Gate（实机，本地 Docker catbridge-gate 镜像 + 测试店铺 5381204，2026-09-24 通过）

复跑今早失败单同竞品 **4225157851**（task 9be5e9df）——修复链全程日志留痕：

1. 毒 14761 schema 验证拒 → 数字直查失败 → 门控无候选置空（三道闸照常）✅
2. hand 降级 api import-by-sku 复制成功（6443824939）✅
3. **复制卡真实类目反查回填 dc=17027937/tp=97369 ✅（新代码）**
4. **采用复制卡真实类目（覆盖解析值 -/-）✅（新代码）**
5. 终态 **completed + approved**，留存行「住宅和花园 > 收纳 > 储物盒」，
   Ozon 卡 dc/tp 逐字命中，4 图，stocks=False，sources=sds（零库存）✅

对照：同竞品修复前必死（TypeId 400 → LLM 循环 → 假 failed）。

坑单回归（毒信封直提 14762）：由 worker 硬闸回归测试锁定（单测层），
实机层与 gate 1 同链路（防线在 gate 1 已实证拦截）。

存量模板/采集箱兼容：无 schema 改动，零 422 面。

## 4.1 遗留清理（待用户拍板范围）

- 测试店 **5 张假 failed 复制卡**（6443818882 等，2026-09-24 07:49 时段）待归档
  ——与生产 68 卡清理同车。
- 二期（随属性填满批）：EN 匹配腿、属性中文清洗（A2 死因）、/v4 自家模板
  属性继承、颜色视觉属性链修通。

## 5. 交付物

- 本文件 + CHANGELOG 条目
- skill/worker 代码 + 测试
- 实机 gate 结果回填本文 §4
