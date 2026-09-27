# 04 · 特征属性链：填充顺序 / 出口闸 / 字典三桶 / 学习

> 范围：`prepare_ozon_upload_node.py`（下称 prepare）、`assemble_ozon_product_node.py`（assemble）、
> `validation_retry_loop.py`（retry）、`utils/attr_*`。
> 信任序（代码验证一致）：**本商品证据(1688) > 标题证据词(A1) > vision 推断 > 模板继承 > 类目默认(A2) > schema-LLM(A5) > 竞品复制卡(A6)**。
> 两条细化：①A6 有意排在 A4 数值语义闸**之后**——竞品已过审真实值豁免禁填规则，只受值数闸约束（prepare:4045-4046 注释）；②每步只填 `existing` 没有的 id，先到先得，无任何步骤改写前序值。

## 1. 一个属性值从候选到落卡的生命周期

```
[信封] draft.attributes(1688 三源) / draft.ozon_attributes(竞品全表) / title / supplier / variants
  │
  ▼ A. assemble 构建期（信任序第 1：本商品证据）
  match_attr_name（精确→包含→jieba→同义词组）→ _find_dict_value → unique_or_none（唯一才填，多候选留 prepare）
  + v0.71 /values/search 只认精确等值（assemble:3272-3287）
  + 8229 主动填（dict id==type_id 优先，fetch_ru_dict_value 补 RU，assemble:3309）
  + _validate_and_enrich_items：品牌 85/31/5076 强制 Нет бренда、4389=Китай、hashtag 23171（assemble:3623-3678）
  │ → state.final_attributes（flat）
  ▼ B. prepare 主转换循环（prepare:2659-3112）
  dict_id>0 直接落（中文值置空）；dict_id=0 → 缓存【精确压倒包含】→ 学习表 ozon_attribute_mappings → /values/search 首个合法（⚠️top-1）→ 全失败跳过
  + 9048=item_id / 8962 兜底"1"+sanitize / 4958 搜索兜底 / 7578 等自由文本默认
  ▼ C. prepare 补齐链（prepare:3978-4062，整段 try，异常 warning 跳过）
  ① _fill_missing_required_dict_attrs(:663) 必填字典默认（竞品→1688→live→通用→专属默认；8292"Нет"/4295 One size/性别中性/颜色）
  ② A1 augment_draft_with_title_evidence（extras:170）标题证据词→伪 draft.attributes（不覆盖真实 1688 键）
  ③ _fill_optional_dict_attrs(:1061) 同义词链 + 中文直搜旁路（共享≥1 中文字符 且 非 quantity 箱规键）
  ④ _infer_attrs_from_vision(:1354) vision 推断（_INFER_KW 白名单，图≤3）
  ⑤ _inherit_attrs_from_template(:1703) 同 (dc,tp) approved 最近 3 张模板继承（_TEMPLATE_SKIP_ATTR_IDS 排除 9048/品牌族/海关族）
  ⑥ A2/A3 apply_class_defaults_and_numerics（extras:307）类目默认+标题数值派生
  ⑦ A4 sanitize_numeric_semantics（extras:453）恒生效：22390 禁填、8513/11650/23249 首值>200 剥
  ⑧ A5 schema-LLM（prepare:4020-4044，kill-switch LLM_SCHEMA_FILL=0，**默认开**）→ apply_llm_schema_fill 逐条确定性验证才落卡
  ⑨ A6 merge_copied_card_attributes（prepare:1661，调用 4060）复制卡/现卡特征（已填恒不覆盖，7 个我方必赢 id 不抄）
  ▼ D. 出口闸（恒生效）
  cap_attribute_values 逐 item（prepare:4069）→ 必填缺失重算 → 结构校验
  ▼ E. upload/retry 出口
  upload: 防洗卡 → /v3/product/import
  retry: 翻译+flat 合并(cap) → attributes/update(cap) / product_import UPDATE(A6) / full-import-create(A6)
  ▼ F. 事后
  fetch_back /v4 回读 diff → learning 消费 outcome；拒单 → learn_bounds_from_decline 回灌 attr_bounds_learned（retry:868-886）
```

## 2. 出口闸全集

| 闸 | 实现 | 规则 |
|---|---|---|
| 值数闸 | `attr_value_sanitize.py:76 cap_attribute_values`（唯一入口） | cap 取 schema `max_value_count`；**8229 恒 1**（:50，防旧缓存缺字段）；is_collection 无声明不设限；先去重再截断。接线四处：prepare ③ 内/prepare 载荷出口/retry flat 合并(:1551)/retry attributes-update 重发前(:2786) |
| 数值语义闸 | `attr_fill_extras.py:453 sanitize_numeric_semantics` | 22390 恒禁填；8513/11650/23249 首值>200 剥除；**恒生效无 kill-switch**；A5 内部同款拦截+11650 源头不提案 |
| 数值边界 | `attr_numeric_sanitize.py` | 优先级：静态白名单(8962∈[1,10000]) **恒赢** > 学习表 attr_bounds_learned > 不夹取；RU 逗号归一/多数字取首/Integer 去尾巴；`limit_error_attr_needs_drop`（VALUE_MAX 且无界 → retry 丢弃可选属性） |
| 品牌族 | assemble:3623-3640 | 85/31/5076 无条件覆盖/缺失补 `Нет бренда`(126745801)；模板继承/A5/A6/attr_gap 全排除品牌族 |
| 5379？ | **不存在**——全 worker/src 无属性 5379；最接近 9379/22232（海关族恒宁缺，prepare:1532-1533）。疑为笔误，见 09 | — |

## 3. 值匹配层（utils/attr_value_matcher.py「三处统一」）

- `match_attr_name:98` / `match_dict_value:138`（返回全部候选）/ `unique_or_none:165`（危险品只挑安全默认→is_aspect 跳过→唯一命中→多候选 LLM 消歧→0 候选 skipped）。
- 调用点：assemble 构建期（2940/2953）、prepare 补全期（1197/1303/1474）、retry 修复期（retry:566/594-630）。
- LLM 消歧安全三件套：-1 出口 / dict_id 从候选列表重查证 / 解析失败 abstain 不降级取首值；`MxouOutOfQuotaError` 上抛。
- attr_synonyms.json 14 组（material/season/use/style/color/type/quantity[zh_exclude 箱规]/packaging/gender/origin/shape/pattern/composition/power）。
- **top-1 盲采残留三处**（与「绝不盲补首值」红线不一致，09-#3）：assemble 可选补齐 `_results[0]`(:3743)、retry `_search_dictionary_values_chain` `result[0]`(:575，且写回 final_attributes 重发)、prepare 主循环 search 首个合法(:3018)。

## 4. 字典链三桶（utils/dict_value_cache.py）

| 桶 | 判定 | 键 | 行为 |
|---|---|---|---|
| global | `category_dependent=false` | 哨兵 `(attr,0,0,language)` 一份 | 零 DDL |
| scoped | true / **缺字段默认保守 scoped** | (attr,dc,tp,language) | 现状键 |
| ephemeral | 首页即 has_next / 值数>2000（品牌 85 级） | 不物化 | 运行时 /values/search |

- TTL 30d + ±10% 抖动防雪崩；负缓存 60s（进程内 + PG sentinel 行 `[]`）；`routed_get` 三态语义（非空=命中 / `[]`=确认空勿回源 / None=回源）；**单飞 `get_or_fetch:353`**（读穿一站式：负缓存短路→routed_get→miss 单飞→锁内双检→fetch 抛异常透传给全部等待者、**只有返回空列表才落负缓存**）。
- `fetch_ru_dict_value`（ozon_dict_values.py:95）5 页 has_next 循环——⚠️ 仍 `limit=5000`（:124，官方钳 2000，全库口径不一致，09）。
- retry RU 强刷（:1743-1800）绕缓存直连 + routed_set 按桶 + 与 assemble 共用单飞键空间。
- schema 懒加载 `category_schema_service.py`：PG 缓存→未命中按租户凭证拉一次 `/v1/description-category/attribute`→回写 30d→失败降级不抛；字典值 `?attr_id=` 按需（webui 同链）。
- ⚠️ **prepare 直搜链未接 `get_or_fetch`**（多处理绕缓存直连 Ozon，大字典×5 页×多属性放大请求数，09-#11-属性）。

## 5. 必填兜底（23487/8229/22390）

- 23487（Производитель）= supplier，三处一致（assemble:3473 / prepare:724 / retry `_KNOWN_DEFAULTS_RETRY:1847`）；supplier 缺失→`Нет бренда`；中文→LLM 翻译→仍中文→«Китайская компания»。
- 22390 = itemId（prepare:719）是**唯一写入口**；A4 恒禁自动填 + A5 禁提案。
- 8229（Тип）：assemble P0 主动填 + prepare 两处 RU 补查 + retry 不变式 `_ensure_type_attr_8229`(:2781)；值数恒 cap1。
- 9782 危险品只走 `get_safe_hazard_default`；自由文本必填无默认→跳过交 retry。

## 6. 拒单学习（attr_bounds_learned）

- 写侧唯一入口 `learn_bounds_from_decline`（attr_numeric_sanitize.py:172-216）：parse 拒单原文（`символ` 段排除/同段多 attr 不学/须同时定位 attr+界）→ Python 侧 `_tighten_bounds` 收紧并集（min 取 max / max 取 min）→ upsert → 失效缓存；异常静默 0。
- 回灌点：retry parse_error 消费前逐条 error 尝试（retry:868-886）。
- 读侧唯一入口 `_resolve_effective_bounds:219`。
- ⚠️ **只紧不松**：Ozon 放宽限额时旧学习值永久过夹，无过期/重置机制（:166 注释认账，09）。

## 7. attr_match_log 审计打点

writer `utils/attr_match_log.py:24`（task_id 空跳过、DB 失败非致命）。打点**只在 prepare 链**：matched/synonym（同义词+旁路）、skipped_no_value/skipped_multi_candidate、no_infer/vision、filled/template_inherit(0.6)、matched/class_default+title_numeric+dims_string、matched/llm_schema(0.8)。**assemble 构建期与 retry 修复期零打点**——缺口榜高估 prepare 层缺口（09）。

## 8. 疑点（并入 09-findings §属性）

1. 「5379 宁缺毋滥」无对应代码（9379/22232 才是海关族）——口径笔误待澄清。
2. top-1 盲采残留三处（§3）。
3. `fetch_ru_dict_value` limit=5000 vs 官方 2000（首页有效数据比声称少）。
4. retry `_get_attribute_schema` 回写 TTL=1d（:723）vs 全库 30d——重试场景反复回源。
5. A6 豁免 A4 有风险窗口：竞品卡若自带箱规数量类值（8513>200）原样照抄，只有「对方已过审」隐式担保。
6. A6 UPDATE 就地读回在 prepare + 三出口各读一次 /v4，跨调用无去重缓存。
7. `merge_copied_card_attributes` 只对带 product_id 的 item 生效——CREATE 场景 C-⑨ 恒空转（日志「合并+N」误导）。
8. 中文直搜旁路触发宽（共享任意 ≥1 中文字符）——多打 /values/search。
9. vision 推断命中白名单但 LLM 失败/多候选放弃无审计打点。
10. `LLM_SCHEMA_FILL` 默认开——新环境未设变量 A5 自动生效（不是默认关的白名单开关）。
