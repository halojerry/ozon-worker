# Ozon 属性/类目 API 参考（长期维护，开发直接查这里）

> 来源: `docs/ozon-api-docs-2026-07-05 (3).json`(官方文档抓取) + 真实 API 实测(2026-08-07, 店铺 5371047)。
> 用途: 上架时「尽可能填满属性特征」的实现依据。海关编码(ТН ВЭД)属性**不填**——平台自动关联。

## 0. 通用约定

- **鉴权**: 所有接口带 header `Client-Id` + `Api-Key` + `Content-Type: application/json`
- **language 参数**: 三个枚举接口(tree/attribute/values)支持 `DEFAULT|RU|EN|TR|ZH_HANS`,
  默认 RU。**language 决定返回文本的语言**(属性名/字典值文本)。
- **`/values/search` 无 language 参数**(按 value 文本模糊搜索, 语言无关)
- **dictionary_value_id 跨语言通用**: 同一个 id 在 RU 下显示俄语文本、ZH_HANS 下显示中文文本。
  匹配拿到 id 后, 上传时 value 文本建议填 RU(或由双语言字典提供)。
- **字典属性判定**: schema 里 `dictionary_id != 0` → 字典属性, 上传必须 `dictionary_value_id`。
  `dictionary_id == 0` → 自由文本/数值/布尔, 只传 `value`。
- **value 文本非空**: 官方示例字典属性总是 `dictionary_value_id` + 非空 `value`(RU)。

---

## 1. 类目树 — `POST /v1/description-category/tree`

获取商品类别和类型树形图。**只有末级类别(type 节点)可以创建商品**。
类别不会根据用户请求创建, 慎重选类目(不同类目佣金不同)。

**请求体**:
```json
{ "language": "DEFAULT" }   // DEFAULT|RU|EN|TR|ZH_HANS, 默认 RU
```

**响应** `result[]`:
```
{
  "description_category_id": 17028747,   // 类目 ID
  "category_name": "日化",
  "children": [ ... ],                    // 子节点
  "type_name": "...",                     // type 节点名(末级)
  "type_id": 99385,                       // type ID(末级, 可创建商品)
  "disabled": false
}
```

**实测**(5371047): 中文树 `{"result":[...]}` dict 格式, 俄语树是 `[...]` 直接 list。
worker `category_tree_nodes` 表已缓存双语, 类目匹配用 pg_trgm(不用 LLM)。

---

## 2. 类别特征列表 — `POST /v1/description-category/attribute`

获取指定类别+类型的商品特征(schema)。**这是「要填什么属性」的权威来源**。

**请求体**:
```json
{
  "description_category_id": 17028747,   // 必填, tree 获取
  "type_id": 99385,                       // 必填(实测必需, 部分类目可不传?)
  "language": "ZH_HANS",                  // 回复语言; 建议 ZH_HANS(匹配 1688 中文属性名)
  "limit": 1000                           // 可选, 实测缺省也能返回全部(28/31/40 个)
}
```

**响应** `result[]`(每个属性):
```
{
  "id": 8229,              // 属性 ID
  "name": "Тип товара",    // 属性名(按 language)
  "description": "...",
  "type": "String",        // String|Int|Double|Boolean|Dictionary...
  "is_collection": false,
  "is_required": true,     // ⚠️ 必填标记(缺失 → Ozon 拒 MISSING_REQUIRED_ATTRIBUTE)
  "group_name": "...",
  "dictionary_id": 1960,   // ⚠️ !=0 → 字典属性(有嵌套值指南); ==0 → 无指南
  "is_aspect": false,
  "attribute_type": "AttributeValue"
}
```

**实测**(5371047, dc=17028963/type=822555160):
- 返回 28 个属性, 必填 3 个: `8229 Тип(dict=1960)`, `85 Бренд(dict=28732849)`, `9048 型号(dict=0 自由文本)`
- `language=ZH_HANS` 时 name 是中文(如「类型」「品牌」), RU 时俄语

**关键属性 ID(跨类目常见)**:
| ID | 名称 | 类型 | 处理 |
|----|------|------|------|
| 8229 | Тип товара/类型 | 字典 | 用类目末级俄语名搜 values/search |
| 85/31/5076 | Бренд/品牌 | 字典 | 强制 `Нет бренда`(126745801) |
| 9048 | 型号(合并卡片) | 自由文本 | item_id 填充 |
| 9163 | Пол/性别 | 字典 | 无来源 → Унисекс |
| 9782 | 危险等级 | 字典 | 只填「非危险」(970661099, Не опасен) |
| 4389 | Страна/原产国 | 字典 | Китай(90296) |
| 22604 | ТН ВЭД/海关编码 | - | **跳过不填** |
| 23536 | 标记码 | - | **跳过**(Ozon 自动) |

---

## 3. 特征值指南 — `POST /v1/description-category/attribute/values`

拉取**字典属性的全部可选值**(分页)。

**请求体**:
```json
{
  "attribute_id": 9782,
  "description_category_id": 17028747,
  "type_id": 99385,
  "language": "RU",        // 建议 RU(值文本用俄语); ZH_HANS 也行(id 通用)
  "limit": 100,            // ≤2000
  "last_value_id": 0       // 游标分页, 响应 has_next=true 时用最后一个 id 继续
}
```

**响应**:
```
{
  "result": [
    { "id": 970593901, "value": "Класс 1. Взрывчатые материалы", "info": "..." }
  ],
  "has_next": false,
  "total_count": 10
}
```

**实测**(5371047, 9782 + 杀虫剂类目): 10 条危险等级值, `get_safe_hazard_default`
正确挑出 `(970661099, "Не опасен")`。

---

## 4. 参考值搜索 — `POST /v1/description-category/attribute/values/search`

按文本模糊搜索字典值, 拿 `id`。**无 language 参数**(语言无关, 中文/俄语都能搜)。

**请求体**(官方定义, 全部 required):
```json
{
  "attribute_id": 8229,                  // required: 属性 ID
  "description_category_id": 17028747,   // required: 类目 ID
  "type_id": 99385,                      // required: 类型 ID
  "value": "Эпоксидная смола",           // required: 搜索词, 最少 2 字符
  "limit": 5                             // required: 1-100
}
```

**响应**: `{ "result": [ { "id": 822555160, "value": "Эпоксидная смола для творчества" } ] }`

**实测**(5371047, 8229 + 杀虫剂类目 dc=17028747/type=99385):
- 搜中文「杀虫剂」→ `id=99385 value=杀虫剂`(语言无关, 三种调用都命中)
- 搜俄语「Эпоксидная смола для творчества」→ `id=822555160`(与类目 type_id 一致)

**语言路由规则(worker 已实现, test_language_routing.py 锁定)**:
- 1688 中文属性值/标题 → 直接传中文搜(不需要指定语言, values/search 语言无关)
- Ozon 类目名(俄语 type_name)→ 传俄语搜
- schema/values 全量接口按需指定 language(决定返回文本语言)

---

## 5. 更新商品特征 — `POST /v1/product/attributes/update`

**增量更新**: 只改请求里指定的属性; 已填属性**无法删除**。
用于上架后补漏(必填缺失/值错误), 不重跑全管线。

**请求体**:
```json
{
  "items": [
    {
      "product_id": 5856512203,
      "attributes": [
        { "complex_id": 0, "id": 8229,
          "values": [ { "dictionary_value_id": 822555160, "value": "Эпоксидная смола для творчества" } ] }
      ]
    }
  ]
}
```

**限流**: 每分钟 + 每天限制, 超限 429, 响应头:
- `Item-Retry-After` — 距离限制重置分钟数(每日 = 莫斯科时间 03:00)
- `Item-Rate-Limit-Remaining` — 剩余操作数
- 配额查询: `POST /v4/product/info/limit`

**用法**: worker `validation_retry_loop` 对属性类错误走此接口靶向修复(~3s, 无需重新审核)。

---

## 6. 其他相关接口

| 接口 | 用途 |
|------|------|
| `POST /v3/products/info/attributes` | 批量查商品已填属性(旧版) |
| `POST /v4/product/info/attributes` | 新版查商品特征描述 |
| `POST /v4/product/info/limit` | 查询每分钟/每天操作限额 |
| `POST /v3/product/import` | 完整创建/更新商品(重传全部字段, 可删属性) |

---

## 7. 「属性特征填满」实现策略(worker 现状 + 优化方向)

### 现状(2026-08-07 实测, 3 个 1688 产品完整链路)

- schema 28-40 个属性, 上传 15-16 个, **必填 0 缺失**(assemble 匹配 + prepare 602 兜底)
- 兜底已补: 品牌/国家/型号/件数(8962)/保质期(7578)/温度(10350/10351)/存储(8787)/材质(8050)
- **可选属性覆盖率低**(~16/28): 1688 15 个属性只匹配到 6 个 Ozon 属性

### 填满算法(目标: 必填 100% + 可选最大化)

```
1. 类目: tree(缓存 PG) → pg_trgm 匹配 1688 类目名 → dc/type
2. schema: attribute(dc/type, ZH_HANS) → 必填清单 + 字典属性清单(dictionary_id>0)
3. 字典值: 对每个字典属性 values 拉全量(RU+ZH_HANS 双语言, 分页 last_value_id)
   → 缓存 PG dictionary_value_cache(att_id+dc+type, 两种语言都写)
4. 1688 属性匹配: 中文名 → schema 中文名精确/包含匹配 → 中文值 → values/search 拿 id
5. 必填补全(prepare _fill_missing_required_dict_attrs):
   语义默认(attr_defaults): 品牌=Нет бренда / 性别=Унисекс / 尺码=RU / 8292=0
   标题搜索 → 属性名搜索 → 安全兜底(pick_dict_fallback_value, 唯一值才填)
6. 可选补全(_fill_optional_dict_attrs): 唯一字典值 + 竞品属性兜底(优化方向)
7. 上传后: attributes/update 增量补漏(限流友好)
```

### 优化方向(待实施)

1. **1688 属性名 → Ozon schema 属性映射表扩充**(当前只靠名字精确/包含匹配, 覆盖率低)
2. **Ozon 竞品属性复用**: 同类目竞品属性值(俄语)填 1688 缺的可选属性(follow 已有 ozon_attributes, 直采待接)
3. **双语言字典缓存**: values 拉取同时写 RU + ZH_HANS, 上传 value 用 RU 文本
4. **8229 重复**: assemble 1954(类目名)与 2041(属性名)两条补填路径去重
5. **10096/10097 颜色**: dict_id 有但 value 空 → 补 RU 文本; 中文值翻译失败 → 竞品值/跳过
