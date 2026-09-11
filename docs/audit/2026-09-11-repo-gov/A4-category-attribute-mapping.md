# A4 — Ozon 类目-属性-特征映射规则与校验体系审计

> 审计日期 2026-09-11 ｜ 基线 v0.74.0（ozon-worker-gov）｜ 数据源：源码静态审计 + 本地 PG（5433）实查 + `mcp__ozon__*` 契约核对（零凭证只读）
> 关联：A1 文档治理 / A2 竞品对齐 / A3 数据质量。本文是该目录下唯一新增文件。

## 0. 结论摘要（先读）

1. **审计前提修正（重要）**：经 `ozon_describe_method(DescriptionCategoryAPI_GetAttributes)` 权威核对，Ozon attribute schema **不存在** `min_length/max_length/pattern/unit` 字段。平台侧的数值边界（VALUE_MAX_LIMIT/MIN_LIMIT）与长度限制**不随 schema 下发，只在拒单错误里反馈**。真正的未消费契约字段是：schema 行的 `description`（特征填写说明）、`group_id/group_name`（特征分组）、`attribute_complex_id/complex_is_collection`（复杂特征），以及字典值行的 `info/picture`。
2. **「特征」语义考证**：Ozon 官方分节名 «Атрибуты и характеристики»，GetAttributes 返回的每个条目就是 характеристика（特征）。**「属性/特征」在 Ozon 语义里是同一概念**；「特征值」= 字典值（dictionary value，`/values` 返回 `{id,value,info,picture}`）。三层模型成立：**类目 (dc,tp) → 特征（schema 行，attr_id）→ 特征值（dictionary_value_id）**。
3. **「按类目维度的必填/可选全量地图」当前不存在也不可静态导出**：本地 PG `attribute_cache` 实查 **0 行**（生产 v0.71 前曾 12 行 vs 7992 个 (dc,tp) 类目对），schema 是按需懒加载的。要达成用户诉求 3（可填字段完整准确），须先做 **schema 资产化**（预热→导出→版本化快照），再叠加本文 §6 校验器。
4. **自动化校验雏形已三处但规则互不同源**：`offline_validate.py`（离线试填）、`ozon_validate_node.py`（在线预检）、`attr_value_sanitize.py`（出口闸）各持一套正则与规则；本文 §6 给出统一「类目合规校验器」设计，可直接落 `worker/scripts/`。
5. 消费矩阵：13 个文档化字段中 **8 个已消费、5 个未消费**（§2）；`type` 字段实证存在但 swagger 未文档化（§2 P1 风险）。

## 1. 用户四诉求 → 本文对应章节

| 诉求 | 回应 |
|---|---|
| 1. 类目维度必填/可选/平台规则梳理 | §2 消费矩阵 + §4 三层模型 + §5 抽样表；平台规则权威= schema 字段 + 拒单错误码反馈（§6 规则表） |
| 2. 差异化填写规则/校验逻辑/取值规范 | §5（按类目 schema 差异化）+ §6 规范框架（按特征类型五分类） |
| 3. 类目-属性-特征映射表，可填字段完整准确 | §4.4 映射表数据结构现状 + 差距分析（fill 率机制 / ephemeral 巨字典 / 宁缺毋滥） |
| 4. 自动化校验 | §6 校验器设计（函数签名 + 规则表 + 错误码映射），雏形 = offline_validate.py |

## 2. Schema 字段消费矩阵（核心发现）

契约权威：`DescriptionCategoryAPI_GetAttributes`（POST /v1/description-category/attribute，MCP describe 2026-09 核对）。响应 `result[]` 每行字段全集如下。

### 2.1 schema 行字段（13 文档化 + 1 实证）

| 字段 | 类型 | Ozon 语义 | 消费 | 代码位置 / 说明 |
|---|---|---|---|---|
| `id` | int64 | 特征 ID | ✅ | `attr_value_sanitize.build_schema_index:26`；全链主键 |
| `name` | str | 特征名（随 language） | ✅ | `attr_value_matcher.match_attr_name:89`（精确→包含→jieba→同义词） |
| `description` | str | **特征填写说明** | ❌ | 全库零消费。机会：注入 LLM 填充/消歧 prompt（`attr_disambiguation_cfg`）补语义 |
| `dictionary_id` | int64 | 0=无字典 | ✅ | `prepare_ozon_upload_node:1089`、`ozon_validate_node:104-115`、`offline_validate:161` |
| `group_id` | int64 | 特征组 ID | ❌ | 零消费。机会：webui 表单分组、高价值特征优先填充 |
| `group_name` | str | 特征组名 | ❌ | 同上 |
| `is_aspect` | bool | 方面特征（创建/出仓后不可改） | ✅ | `attribute_utils.is_aspect_attr:78`——schema 显式标志优先，**名称关键词兜底过宽**（见 F-P1-2） |
| `is_collection` | bool | 多值集合 | ✅ | `resolve_value_cap:62`、`prepare:1076`（v0.71 值数闸） |
| `is_required` | bool | 必填 | ✅ | `prepare:665`（必填兜底）、`ozon_validate_node:128`（预检）、`attr_gap`（分母） |
| `category_dependent` | bool | 字典按类目隔离 | ✅ | `dict_value_cache.is_category_dependent:48`（v0.72 三桶路由） |
| `max_value_count` | int64 | 值数上限 | ✅ | `resolve_value_cap:57`；**8229(Тип) 硬编码 cap=1**（`:50`，防旧缓存缺字段） |
| `attribute_complex_id` | int64 | 复杂特征 ID | ❌ | 零消费——复杂特征类目（服装尺码表等）形态可能填错 |
| `complex_is_collection` | bool | 复杂特征集合标志 | ❌ | 零消费 |
| `type`（实证，swagger 未列） | str | Integer/Decimal/Number… | ✅ | `attr_numeric_sanitize.is_numeric_attr_type:40`（大小写不敏感六变体）；v0.26 P1-2 生产实证生效 |

### 2.2 字典值行字段（GetAttributeValues → result[]）

| 字段 | 消费 | 说明 |
|---|---|---|
| `id`（=dictionary_value_id） | ✅ | 匹配链权威（`match_dict_value:129`） |
| `value` | ✅ | 展示文本；字典属性含中文时清零（`clean_dict_value:82`，dict_id 权威） |
| `info`（补充说明） | ❌ | `category_schema_service._normalize_values:33-40` 只保留 `{id,value}`，**直接丢弃** |
| `picture`（值图片） | ❌ | 同上。机会：webui 颜色/图案下拉带图，降低选值错误 |

### 2.3 平台侧校验字段的真实情况（回应「未消费的校验字段」）

- **长度/正则/单位不下发**：契约无此字段；标题/描述长度、值长度限制由平台拒单（DESCRIPTION_DECLINE 等）事后反馈。
- **数值边界不下发**：VALUE_MAX_LIMIT/VALUE_MIN_LIMIT 只在拒单中出现。我方现状 = 硬编码白名单 `NUMERIC_ATTR_BOUNDS`（`attr_numeric_sanitize.py:32`）**仅 8962 一条**（(1,10000)）。
- **推论**：平台规则的完整性只能靠「schema 字段全消费 + 拒单错误自动回流学习」双通道逼近，不可能一次性静态获得（F-P1-1）。

## 3. 数据源与实查记录

| 数据源 | 实查结果 |
|---|---|
| 本地 PG `attribute_cache` | **0 行**（表结构：dc+tp+language UNIQUE，`attributes_schema jsonb`，30d TTL） |
| 本地 PG `category_tree_nodes` | **18256 行**（AGENTS.md 记 16552，口径漂移，见 F-P3-1） |
| 本地 PG `dictionary_value_cache` | **168 行**（attr,dc,tp,language UNIQUE，`values_data jsonb`） |
| `assets/category_tree.json` | 顶层 dict `result[]` 26 个一级类目；嵌套计 568 类目节点 + **7424 个 type 叶**；节点字段 `description_category_id/category_name/type_name/type_id/disabled/children` |
| Ozon MCP 契约 | GetAttributes / GetAttributeValues / SearchAttributeValues / GetTree 四方法 describe 全文核对 |
| 凭证 | 仓库无 stores.json（未入库），未发起真实 Ozon API 调用；§5 属性层为静态推断口径 |

## 4. 「类目-属性-特征」三层模型现状

### 4.1 类目层

- **存储**：PG `category_tree_nodes`（`model.py:236`）：`description_category_id`+`type_id`（叶非空）+`node_name/node_type(category|type)/parent_description_category_id/full_path/top_level_category_name/depth/disabled/language`。JSON 资产 `category_tree.json`（嵌套，ZH_HANS）经 `init_data.import_category_tree` 导入。
- **规模**：本地 18256 行 / JSON 7424 type 叶。获取通道：`refresh_category_tree.py`（全量重建）、`ozon_category_query`（jieba+ILIKE 搜索，v0.65.1 修饰词剥离/敏感词防护）。
- **关键耦合**：类目选择（assemble 多层仲裁 L0/L1/R2b/Skill）决定 schema，schema 决定属性面——类目错 = 整卡属性面全错，这是 R4 换类目闭环存在的原因。

### 4.2 属性层（特征 schema）

- **存储**：`attribute_cache`（dc,tp,language）UNIQUE，`attributes_schema jsonb` **原样存整个响应 result[]**，30d TTL（`local_db_manager.set_attribute_cache:258`）。
- **四条获取通道**（全部回写同一表）：① 交互懒加载 `category_schema_service.get_attributes_with_lazy_fetch:43`（webui/远程 MCP）；② 管线懒加载 assemble `:951/:2045` + retry 回写；③ 批量预热 `scripts/warm_category_cache.py --coverage`（死节点 `warm_dead_nodes` 永久跳过）；④ 字典拉取时顺带回写（`category_schema_service:163`）。
- **覆盖现状**：本地 0 行；生产 v0.71 前 12 行/7992 类目对（99.8% 未预热，v0.71 红线修订的动因）。**覆盖率可量化通道已有**（`--coverage`），但**没有 per-类目规则产物**（必填清单/可选清单导出）——用户诉求 1 的载体缺失。

### 4.3 特征值层（字典）

- **三桶策略**（`dict_value_cache.py` 唯一入口，v0.72）：`global`（cat_dep=false → 哨兵键 (attr,0,0,language) 一份）/ `scoped`（cat_dep=true → (attr,dc,tp)）/ `ephemeral`（首页 2000 即 has_next 的巨字典如品牌 85 → **不物化**，运行时 `/values/search`）。
- **语义后果（对诉求 3 的硬约束）**：ephemeral 类特征值**永远没有本地全集**，「可填字段取值全量准确」对品牌/巨字典类只能定义为「值搜索精确命中」而非「枚举闭合」。
- **匹配纪律**：`attr_value_matcher` L1 纯函数（精确→包含→唯一值；多候选 llm_eligible 默认 skipped，绝不盲补首值）；`ozon_dict_values.py` 运行时搜索通道。

### 4.4 映射表数据结构（现状汇总）

```
类目 (dc,tp) ──1:N──> 特征 schema 行 (attribute_cache.attributes_schema[])
                        │  id/name/description?/dictionary_id/group_id?/is_required/
                        │  is_collection/max_value_count/is_aspect/category_dependent/type?
                        └──dictionary_id>0──1:N──> 特征值 (dictionary_value_cache.values_data[]
                                                    或运行时 /values/search)
商品 payload: item.attributes = [{id: attr_id, values: [{value, dictionary_value_id}]}]
```

## 5. 五个真实类目抽样映射表

dc/tp 实查自 `category_tree.json`（Python 遍历 2026-09-11）；**属性列为静态推断口径**（本地 attribute_cache 空、仓库无凭证，未拉真实 schema——补全方法见 F-P0-1 探针）。已知全局特征 attr_id 引自代码硬编码事实：8229 Тип（必填、字典 1960、恒单值）、85/31/5076 品牌（强制 Нет бренда=126745801）、4380 原产国（Китай）、9782 危险等级（安全默认）、22604 ТН ВЭД（跳过）、4180 名称/4191 简介/9048 型号/23171 hashtag（系统生成）。

| # | 类目路径（ZH_HANS 树实查） | dc | tp | 必填（推断） | 字典型（推断） | 备注 |
|---|---|---|---|---|---|---|
| 1 | 服装/服装/无袖连衣裙 | 200000933 | 93211 | 8229 Тип、4180、862314 品牌等必填集 | 8229、颜色(10096 邻域)、尺码（**aspect**） | 服装类多 aspect（颜色/尺码创建后不可改）；复杂特征风险区（F-P2-2） |
| 2 | 家居/家居/……（待 schema） | — | — | — | — | 见下方样本替换说明 |
| 3 | 运动与休闲/滑板和滑板车/滑板车手机座 | 17028701 | 971298965 | 8229、4180 | 8229、材质邻域 | 电子配件类数值属性多（如 8962 件数） |
| 4 | 儿童用品/积木玩具套装/积木 | 62573858 | 92952 | 8229、4180 | 8229、材料、主题 | 玩具类证书/年龄限制特征常为必填字典 |
| 5 | 美容和卫生/美容设备/面膜制作机 | 59968946 | 97753 | 8229、4180 | 8229、功率/电压邻域 | 数值+单位类特征密集，VALUE_MUST_BE_* 高发 |

> 诚实说明：#2「家居」一级类目在树中为「家居与花园」分支，抽样脚本关键词命中了运动类目（钓鱼收纳箱 dc=77119630/tp=95489 可作备选）。**本表的价值在结构而非数值**——它演示了诉求 1/3 要求的 per-类目映射表形态；把推断列变成实测列的唯一路径是 F-P0-1（schema 资产化探针），一次性 5 类目拉取即可回填本表全部列。

**每类目应产出的完整列规范**（映射表模板）：`attr_id | name | description | group_name | is_required | dictionary_id | is_collection | max_value_count | is_aspect | category_dependent | type | 取值规范（字典枚举/数值 bounds/自由文本语言约束） | 我方填充通道 | 校验规则 ID`。

## 6. 填写规则规范框架与自动化校验器设计

### 6.1 按特征类型的取值规范 × 现有校验 × 缺口

Ozon `type` 实证枚举有限（Integer/Decimal/Number…），**是否字典由 `dictionary_id>0` 独立判定**，二者正交。五分类：

| 特征类型 | 取值规范 | 现有校验 | 缺失校验 |
|---|---|---|---|
| 字典单选 | dictionary_value_id>0 且值在字典内；value 文本中文清零 | dvid≤0 拦截（ozon_validate_node:307-321）；宁缺毋滥匹配链 | **dvid 不在当前类目字典集合**（盲 id 无校验）；ephemeral 字典无法闭合校验（降级为 /values/search 验真） |
| 字典多选 | is_collection 且 max_value_count 截断 | `cap_attribute_values`（v0.71，三出口：prepare/retry flat/retry 重发） | 同上 + 多值间语义冲突无校验 |
| 自由文本（dictionary_id=0, type 缺省） | 俄语；禁止 CJK（`has_chinese` 含假名+扩展A）；dvid=0 | 中文检测（ozon_validate_node:402-416 + retry 负检） | **长度无校验**（平台不下发，只能拒单学习）；URL/联系方式无检测 |
| 数值（type∈Integer/Decimal/Number） | 可解析数字；RU 逗号归一；bounds 夹取 | `sanitize_numeric_attr_value` 唯一入口 + validate 预检 | bounds 白名单仅 8962 一条（F-P1-1） |
| 布尔/其他未知 type | 契约未文档化 | 无 | swagger 缺口，实证驱动（F-P1-3） |

横切规则（已有，应纳入统一校验器）：必填缺失（is_required 对照）、8229 恒单值、9782 只填安全默认、22604 跳过、is_aspect retry 场景不可改、标题-类目西里尔词面一致性（LOCAL_TITLE_CATEGORY_MISMATCH 先例）、品牌强制 Нет бренда / 原产国 Китай（attr_defaults + attr_gap 豁免表 `_SYSTEM_GENERATED_IDS`）。

### 6.2 「除必填外可填字段全部完整准确」差距分析（诉求 3/4 核心）

- **fill 率机制**：`attr_gap.compute_gap` → `attempted_fill_rate = filled/should_fill`（`attr_gap.py:145-149`），分母剔除系统生成/海关/品牌/原产国等豁免集。缺口明细带 `source_hint`（from_1688_attr/from_title/from_variant/from_ozon_attrs/no_source）。**缺口**：指标是任务级的，无 per-类目基线，无「可填未填」与「不可填」的合同级定义（见下条）。
- **「完整」的物理上限**：① ephemeral 巨字典无全集；② 未预热类目 schema 拉不到（懒加载失败降级 found=False）；③ `is_aspect` 名称关键词兜底（тип/вид/размер/цвет…）过宽导致部分可选特征被跳过（F-P1-2）。建议在契约层定义「完整性 = f(类目 schema 覆盖 × 字典物化状态)」并随 gap_report 一起输出。
- **「准确」的现存风险**：包含匹配双向子串（`match_dict_value:151`）可能错配（黑→黑板）；LLM 消歧三件套默认关。校验器可在填后跑「dvid 验真 + 值数 + 类型」闭环，准确率由「拒单回流」负反馈补齐。

### 6.3 类目合规校验器设计（扩展 offline_validate.py）

输入商品属性集 + 类目，输出违规清单；与现有三处校验共用唯一入口（避免第四套正则）：

```python
# worker/scripts/category_compliance.py（建议落点；offline_validate.py 的 validate_items 升级迁移于此）
@dataclass
class Violation:
    rule_type: str          # 见规则表
    attr_id: int
    attr_name: str
    param: dict             # 规则参数快照（cap/bounds/dict_size/type…）
    message: str
    severity: str           # "block"（必拦，对齐本地拦截先例）| "warn"（降级 warning）
    mapped_code: str        # Ozon 原始码或 LOCAL_* 码

def validate_category_compliance(
    schema: list[dict],                    # attribute_cache.attributes_schema（无则先懒加载）
    dict_index: dict[int, list[dict]],     # attr_id → 字典值（dict_value_cache.routed_get；ephemeral→None）
    item_attrs: list[dict],                # [{"id", "values":[{"value","dictionary_value_id"}]}]
    *, item_name: str = "", dc: int = 0, tp: int = 0,
    mode: str = "preflight",               # preflight(上传前) | offline(离线试填) | retry(重发前, is_aspect 生效)
) -> list[Violation]: ...
```

规则表（rule_type × param × 错误码映射；worker `api/errors.py` 14 码是平台/任务级码，**属性细节码走 Ozon 原始码透传 + LOCAL_ 前缀本地码**，先例 = `LOCAL_TITLE_CATEGORY_MISMATCH`）：

| rule_type | param 来源 | 现有码 / 需新增 |
|---|---|---|
| required_missing | schema.is_required | Ozon MISSING_REQUIRED_ATTRIBUTE（映射 repair 见 validation_retry_loop:295-348） |
| value_count_exceeded | max_value_count/is_collection + 8229→1 | Ozon ATTRIBUTE_VALUE_COUNT_EXCEEDED（已有本地拦截语义） |
| dict_value_invalid | dictionary_id>0 且 dvid≤0 | Ozon INVALID_ATTRIBUTE_VALUE |
| dict_value_unknown | dvid ∉ 字典集（scoped/global 可判；ephemeral 跳过转 /values/search 验真） | **新增 LOCAL_DICT_VALUE_UNKNOWN（warn）** |
| type_mismatch | type=Integer/Decimal 不可解析 | Ozon VALUE_MUST_BE_INTEGER / VALUE_MUST_BE_DECIMAL |
| numeric_out_of_bounds | NUMERIC_ATTR_BOUNDS（学习型白名单） | Ozon VALUE_MAX_LIMIT / VALUE_MIN_LIMIT |
| cjk_in_value | has_chinese（attribute_utils 唯一正则） | Ozon DESCRIPTION_DECLINE |
| aspect_immutable | is_aspect 且 mode=retry | 本地拦截（已有先例 validation_retry_loop:2961） |
| customs_skip / system_generated | attr_gap 豁免集 | —（不计违规，计 skip） |
| title_category_mismatch | common_cyr_words(RU path) | LOCAL_TITLE_CATEGORY_MISMATCH（v0.73 已有） |

接线点：prepare 出口闸之后（preflight）、offline_validate（offline）、retry `_fix_via_attributes_update` 之前（retry）。规则全部从 schema/字典实数据推导，零硬编码 attr_id（8229 等契约特例集中一处注释块）。

## 7. 发现清单

### P0

**F-P0-1 属性层无资产化产物，「类目维度规则地图」不可产出**
- 证据：本地 PG `attribute_cache` 0 行（实查 2026-09-11）；v0.71 备忘 7992 (dc,tp) vs 12 行；`warm_category_cache.py --coverage` 只输出统计不落规则产物。
- 影响：用户诉求 1/3（per-类目必填/可选/取值规范表）没有权威载体；新类目上架前无法离线评估属性面风险。
- 建议：warm 后新增 `--export-schema-manifest`：导出 per-类目 schema 快照（含 §5 模板全列）到版本化 JSON 资产；5 类目试点回填本文 §5。
- 探针：`PGDATABASE_URL=... python -c "from utils.local_db_manager import LocalDBManager; LocalDBManager().get_attribute_schema(200000933, 93211)"` 核对形状；有凭证时 `scripts/offline_validate.py --dc 200000933 --type 93211 --attrs '{}'` 直拉。

### P1

**F-P1-1 数值边界无学习闭环，白名单仅 1 条**
- 证据：`attr_numeric_sanitize.py:32-34`（仅 8962）；production VALUE_MAX_LIMIT/MIN_LIMIT 拒单真实存在（v0.69 三店扫描 42 例）。平台不下发 bounds（§2.3）。
- 影响：白名单外数值属性越界 → 整单被拒 → 烧 retry。
- 建议：从 `listing_result_log.moderation_texts`/decline 流自动抽取 (attr_id, min, max) 入 `NUMERIC_ATTR_BOUNDS` 学习表（decline 驱动 upsert，人工复核阈值）。
- 探针：grep 生产 decline 原文中 VALUE_MAX_LIMIT 行的 attr 上下文，验证可解析性。

**F-P1-2 is_aspect 名称关键词兜底过宽，抑制可选特征填充**
- 证据：`attribute_utils.py:28-31`（тип/вид/модель/размер/цвет…），schema 缺 is_aspect 字段的旧缓存行全部落入关键词兜底；`match_attr_name` 匹配层把含这些词的**任意**属性判为 aspect。
- 影响：retry 场景跳过（安全方向正确），但构建期同样按 aspect_skipped 漏填合法可选特征（fill 端损失）；名称含 «вид деятельности» 之类非 aspect 属性误伤。
- 建议：兜底仅在 schema 行**确缺** is_aspect 字段时启用，且匹配后校验 dictionary_id>0；把 schema 字段优先级写死进 `is_aspect_attr` 单测。
- 探针：对 attribute_cache 真实行统计 `is_aspect` 字段存在率（`SELECT count(*) FILTER (WHERE attributes_schema::text LIKE '%"is_aspect"%') …`）。

**F-P1-3 `type` 字段 swagger 未文档化，枚举靠实证**
- 证据：MCP describe GetAttributes 响应无 `type`；`attr_numeric_sanitize.py:16-27` 自证仅见过 Integer/Decimal，六变体大小写不敏感防御。
- 影响：新类型（布尔/受限字符串）出现时无契约告警，靠拒单发现。
- 建议：在 schema 资产导出（F-P0-1）里带 `type` 分布统计；发现新枚举即补 `_NUMERIC_TYPE_NAMES` / 新规则。
- 探针：`SELECT DISTINCT jsonb_array_elements(attributes_schema)->>'type' FROM attribute_cache` （生产跑）。

### P2

**F-P2-1 description/group_name（schema）与 info/picture（字典值）零消费**
- 证据：§2.1/§2.2 矩阵；`_normalize_values` 丢弃 info/picture（category_schema_service.py:33-40）。
- 影响：LLM 填充/消歧缺语义（描述文本是平台给的第二权威）；webui 表单无分组、无值图片，选值错误率偏高。
- 建议：① `build_disambiguation_prompt` 注入 `description`；② schema 资产带 group_name；③ 字典缓存列扩展或旁路存 info/picture（注意 values_data 形状兼容）。
- 探针：单类目 describe 后 diff schema 行字段集合 vs 本文矩阵。

**F-P2-2 attribute_complex_id/complex_is_collection 零消费**
- 证据：§2.1；全库 grep 无引用。
- 影响：复杂特征类目（尺码表、多成分声明）按普通特征填可能形态错误被拒。
- 建议：短期在校验器对 `attribute_complex_id>0` 的特征打 warn 跳过；长期按契约实现复杂载荷。
- 探针：生产 attribute_cache 统计 `attribute_complex_id>0` 的类目分布。

**F-P2-3 校验规则三处非同源**
- 证据：offline_validate.validate_items:156-208 与 ozon_validate_node 各持中文检测/类型检测；`attr_value_matcher.py:67` 与 `attribute_utils.py:164` has_chinese 正则不一致（后者含假名+扩展A，v0.73 注释自证）。
- 影响：离线绿 ≠ 预检绿；修复漂移成本随规则数线性涨。
- 建议：§6.3 校验器落地时三处全部改为调用它；正则以 attribute_utils 为唯一事实源（注释已声明方向）。

### P3

**F-P3-1 文档口径漂移**：AGENTS.md 记类目树 16552 节点，本地实查 18256 行（不同批次导入/语言差异未标注）。建议在 init_data 导入时把行数+语言分布写进导出资产 manifest。

**F-P3-2 8229 硬编码特例扩散风险**：`attr_value_sanitize.py:50` cap=1 特例正确但孤例；同类契约特例（4380/85 等默认值）散在 attr_gap/attr_defaults。建议集中为 `contract_attr_specials` 常量块并注释互引。

**F-P3-3 offline_validate 分页参数违约旧痕**：`fetch_dict_values:89` 用 limit=200（合法但非契约上限 2000），与主链 2000 口径不一致，仅影响离线工具效率，无违约风险。

## 8. 速查：本文引用的关键文件（gov 仓库绝对路径）

- `/Volumes/os/dev/ozon-worker-gov/worker/src/services/category_schema_service.py`（懒加载+三桶回写）
- `/Volumes/os/dev/ozon-worker-gov/worker/src/utils/attr_value_sanitize.py`（值数出口闸）
- `/Volumes/os/dev/ozon-worker-gov/worker/src/utils/attr_value_matcher.py`（L1 匹配纯函数）
- `/Volumes/os/dev/ozon-worker-gov/worker/src/utils/attr_numeric_sanitize.py`（数值清洗+bounds 白名单）
- `/Volumes/os/dev/ozon-worker-gov/worker/src/utils/dict_value_cache.py`（三桶唯一入口）
- `/Volumes/os/dev/ozon-worker-gov/worker/src/utils/attribute_utils.py`（aspect/hazard/customs/中文检测）
- `/Volumes/os/dev/ozon-worker-gov/worker/src/utils/attr_gap.py`（fill 率量化）
- `/Volumes/os/dev/ozon-worker-gov/worker/scripts/offline_validate.py`（离线校验雏形）
- `/Volumes/os/dev/ozon-worker-gov/worker/src/graphs/nodes/ozon_validate_node.py`（在线预检）
- `/Volumes/os/dev/ozon-worker-gov/worker/src/graphs/nodes/prepare_ozon_upload_node.py`（四填充函数）
- `/Volumes/os/dev/ozon-worker-gov/worker/src/storage/database/shared/model.py`（三张表模型）
- `/Volumes/os/dev/ozon-worker-gov/worker/assets/category_tree.json`（类目树资产）
