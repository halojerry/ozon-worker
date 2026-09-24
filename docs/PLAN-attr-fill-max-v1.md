# PLAN attr-fill-max-v1 — 三期：特征属性尽可能填满

> 分支 `feat/attr-fill-max-v1`（2026-09-24）。二期（attribute-fill-en-v1）之后第三批属性工程。
> 用户指令：「特征属性要尽可能填满的呀」。

## 一、动因：多类目 fill 对账（2026-09-24，本地 Docker + 测试店 5381204）

13 张自有卡 / 11 个 (dc,tp) 类目 / 3 大域（厨房收纳、厨房餐具、个护、服饰）做
schema N vs 卡上实填 M 对账（/v4/product/info/attributes 反查）：

- 全 schema 口径填满率 24-46%（分母含 ~8-10 个/类目纯噪音：臭氧视频×4/PDF×2/JSON 富内容）。
- **剥噪后有效特征属性口径：每类目 10-14 个，填 4-7 个（~40-55%），缺口 3-7 个/类目。**
- 必填实际 100%（审计里「必缺:类型」是 /v4 不回传 8229 系统派生值的测量伪影）。

缺口高度重合，分三类：

| 缺口 | 覆盖类目 | 性质 |
|---|---|---|
| 材料（含主要容器材料/处理材料） | 6/9 | **证据就在 1688 标题里**（不锈钢/塑料/纳米玻璃），没接进字典链 |
| 保证（10400） | 9/9 | 类目级常识默认，模板继承救不了（源头卡自己也空） |
| 目标受众/性别/包装 | 3-4/9 | 同上，保守默认可填 |
| 颜色（10096） | 5/9 | vision 已覆盖一角，标题词是补充 |
| 类目特有（盖子直径/炊具特点/申请方法…） | 各 1-2 | 证据驱动，宁缺毋滥维持 |

## 二、设计：三箭，全部确定性，证据链信任序不变

```
本商品证据(1688 attrs) > 标题证据词(A1) > vision > 模板继承 > 类目默认(A2)
```

### A1 标题证据词 → 伪 draft.attributes（utils/attr_fill_extras.py）

- 扫描 draft.title（+draft.category_path）：材料/颜色/形状/性别四类高置信词表
  （多字词优先、单字词 `\b` 兜底、`黑科技`/`双色` 等伪命中先剥/放弃）。
- 合成伪属性（键=材质/颜色/形状/性别，值=canonical 中文），**不覆盖真实 1688 键**。
- 伪属性走**既有同义词安全链**（缓存精确 → raw 中文直搜 → RU 映射兜底 →
  多候选无精确则 LLM 消歧或弃），零新匹配逻辑。
- attr_synonyms.json：material 补 竹/铁/木/塑胶；color 组 value_map 从空补 16 词
  RU 兜底；shape 组补 5 词（v0.65.1「留空」设计修订——闭域确定性翻译，同 material 待遇）。

### A2 类目级保守默认（config/attr_class_defaults.json，热加载）

- 白名单三件：保证→`нет гарантии`、目标受众→`Универсальный`、性别→`Унисекс`
  （性别用 exact_names 精确名匹配，防 `пол` 误击 `полотенце`）。
- **字典精确命中才填**（search + find_dict_value_id 全等），命中不了静默跳过。
- `skip_if_title_has`：标题有人群信号（女/男）时默认让位证据链。
- **布尔/个体事实类属性（包括盖子/可洗碗机清洗等）禁入本表**——默认值会撒谎。

### A3 数值派生 + 尺寸串

- 标题容量正则（`5000 ml`/`2L保鲜盒`→毫升，50..50000 守卫）→ 体积/容量类属性。
- 标题个数正则（`2 pcs`/`6件套`→1..100 守卫）→ 每包数量/原厂包装数量类属性。
- item 自身 depth/width/height → 「尺寸，毫米」串属性。
- 体积**不**从包装尺寸推导（包装≠容积，v0.68 交叉校验拒单教训）。

### 接线（prepare_ozon_upload_node）

```
_fill_missing_required_dict_attrs
→ augment_draft_with_title_evidence(draft) → _fill_optional_dict_attrs(伪draft)
→ _infer_attrs_from_vision
→ _inherit_attrs_from_template
→ apply_class_defaults_and_numerics   ← 新（默认+数值+尺寸串）
→ v0.71 值数闸（新填点全部过闸）
```

审计：成功填点写 attr_match_log，match_layer ∈ {title_evidence(经 synonym 层), class_default, title_numeric, dims_string}。

## 三、测试

`worker/tests/test_attr_fill_max_v083.py` 12 用例（纯 mock）：
伪属性抽取/不覆盖真实键/颜色放弃与伪命中剥除/默认精确命中与跳过/性别让位与
exact_names 保护/数值守卫/不重复填/伪属性贯通同义词链。
`test_attr_synonyms_extended_v0651` shape/color value_map 断言按新设计修订。

## 四、实机 Gate

（待跑：本地 Docker + 测试店，1-2 单——标题含材料/容量词的产品，
对账填满率对比 40-55% 基线；结果回填于此。）

## 五、Defer

- 包装（盒装/袋装语义不明）、交货形式、包括盖子等布尔——证据出现前不填。
- 类目特有特征属性（盖子直径/炊具特点等）LLM 字典约束兜底——观察本批缺口余量再立项。
