# Ozon变体商品实施计划

## 一、API查询成果总结

### 1. 关键发现：变体绑定机制

**核心属性：8292（合并到一个PDP）**

- **功能**：将相同1688商品的多个SKU绑定到同一Ozon商品卡片
- **规则**：属性8292的值为1688商品ID（item_id），所有SKU使用相同的值
- **示例**：
  ```json
  {
    "id": 8292,
    "values": [{"value": "1688商品ID_123456"}]
  }
  ```

**变体属性**：
- 属性10096（颜色）：每个SKU可以不同
- 属性4295（尺码）：每个SKU可以不同

---

### 2. 必需字段dictionary_value_id查询结果

**颜色字典（属性10096，651个选项）**：
```json
{
  "white": 61571,
  "black": 61574,
  "grey": 61576,
  "yellow": 61578,
  "red": 61579,
  "pink": 61580,
  "blue": 61581
}
```

**尺码字典（属性4295，400个选项）**：
```json
{
  "44": 35428,
  "46": 35429,
  "48": 35430,
  "50": 35431,
  "52": 35432,
  "54": 35433,
  "universal": 35646
}
```

**性别字典（属性9163，4个选项）**：
```json
{
  "female": 22881,
  "male": 22880,
  "girls": 22882,
  "boys": 22883
}
```

**国家字典（属性4389，267个选项）**：
```json
{
  "china": 90296,
  "russia": 90295,
  "not_specified": 90297
}
```

**类型字典（属性8229，根据type_id查询）**：
```json
{
  "Long Sleeve": 93148,
  "Rashguard": 93149
}
```

---

### 3. 尺码映射表（Supabase size_mapping表）

**女性尺码表（23行）**：
- RU尺码：36-80
- INT尺码：3XS-7XL
- 胸围/腰围/臀围范围（cm）

**数据已导入**：女性23行数据已成功导入到Supabase

**待导入**：男性、儿童、鞋子尺码表（SQL脚本已生成）

---

## 二、draft结构设计

### 原始draft（单SKU）
```python
draft = {
    "title": "连衣裙",
    "category": "连衣裙",
    "cost_cny": 50.0,
    "weight": 200,
    "depth": 30, "width": 40, "height": 60,  # cm单位
    "sku_id": "sku_001",
    "image_urls": ["主图URL"],
    "attributes": [...],
    "selling_points": [...]
}
```

### 新draft（支持变体）
```python
draft = {
    "title": "连衣裙",
    "category": "连衣裙",
    "cost_cny": 50.0,
    "weight": 200,
    "depth": 30, "width": 40, "height": 60,
    "item_id": "123456",  # 🆕 1688商品ID（用于属性8292绑定）
    
    # 🆕 变体数据
    "variants": [
        {
            "sku_id": "sku_white_44",
            "color": "白色",
            "size": "44",  # 俄罗斯尺码
            "int_size": "S",  # 国际尺码（用于映射）
            "image": "白色S码图片URL",
            "price": 50.0,
            "gender": "female"
        },
        {
            "sku_id": "sku_black_46",
            "color": "黑色",
            "size": "46",
            "int_size": "M",
            "image": "黑色M码图片URL",
            "price": 60.0,
            "gender": "female"
        }
    ]
}
```

---

## 三、代码修改计划

### 步骤1：修改state.py

**新增字段**：
```python
class GraphInput(BaseModel):
    # 已有字段...
    item_id: str = Field(..., description="1688商品ID（用于变体绑定）")
    
class GraphOutput(BaseModel):
    # 已有字段...
    variant_group_id: str = Field(default="", description="变体组ID（实际是item_id）")
    product_ids: List[str] = Field(default=[], description="多个SKU的product_id列表")
```

---

### 步骤2：修改pricing_node

**核心逻辑**：
```python
def pricing_node(state, config, runtime):
    variants = state.draft.get("variants", [])
    
    if len(variants) <= 1:
        # 单SKU：常规定价
        base_price = calculate_price(state.cost_cny, ...)
        return PricingOutput(base_price=base_price, ...)
    else:
        # 多SKU：为每个variant定价
        variants_pricing = []
        for v in variants:
            v_price = calculate_price(v["price"], ...)
            variants_pricing.append({
                "sku_id": v["sku_id"],
                "price": v_price,
                "profit_estimation": {...}
            })
        
        return PricingOutput(
            base_price=variants_pricing[0]["price"],  # 主SKU价格
            variants_pricing=variants_pricing
        )
```

---

### 步骤3：修改prepare_ozon_upload_node

**核心逻辑**：
```python
def prepare_ozon_upload_node(state, config, runtime):
    variants = state.draft.get("variants", [])
    item_id = state.draft.get("item_id", "")
    
    if len(variants) <= 1:
        # 单SKU：常规payload
        items = [组装单个SKU的payload]
    else:
        # 多SKU：变体payload
        items = []
        
        for v in variants:
            # 查询颜色dictionary_value_id
            color_dict_id = query_color_dictionary(v["color"])
            
            # 查询尺码dictionary_value_id
            size_dict_id = query_size_dictionary(v["size"])
            
            # 查询性别dictionary_value_id
            gender_dict_id = query_gender_dictionary(v["gender"])
            
            # 组装单个SKU payload
            items.append({
                "name": translate_to_russian(f"{state.title} {v['color']} {v['int_size']}"),
                "offer_id": v['sku_id'],
                "price": str(v['price']),
                "images": [v['image']],
                "attributes": [
                    # 🔑 合并卡片属性（绑定到同一1688商品）
                    {"complex_id": 0, "id": 8292, "values": [{"value": item_id}]},
                    
                    # 颜色属性
                    {"complex_id": 0, "id": 10096, "values": [
                        {"dictionary_value_id": color_dict_id, "value": translate_to_russian(v["color"])}
                    ]},
                    
                    # 尺码属性
                    {"complex_id": 0, "id": 4295, "values": [
                        {"dictionary_value_id": size_dict_id, "value": f"{v['size']} ({v['int_size']})"}
                    ]},
                    
                    # 性别属性
                    {"complex_id": 0, "id": 9163, "values": [
                        {"dictionary_value_id": gender_dict_id}
                    ]},
                    
                    # 其他共同属性（品牌、产地、商品简介）
                    {"complex_id": 0, "id": 31, "values": [
                        {"dictionary_value_id": 126745801, "value": "Нет бренда"}
                    ]},
                    {"complex_id": 0, "id": 4389, "values": [
                        {"dictionary_value_id": 90296, "value": "China"}
                    ]},
                    {"complex_id": 0, "id": 4191, "values": [
                        {"value": generate_description(state)}
                    ]}
                ],
                # 其他基础字段（vat、weight、dimension等）
                "vat": "0",
                "currency_code": state.currency_code,
                "weight": state.weight,
                "weight_unit": "g",
                "depth": state.depth * 10,  # cm → mm
                "width": state.width * 10,
                "height": state.height * 10,
                "dimension_unit": "mm",
                "description_category_id": state.description_category_id,
                "type_id": state.type_id
            })
    
    return PrepareOzonUploadOutput(
        ozon_payload={"items": items},
        variant_group_id=item_id
    )
```

---

### 步骤4：修改ozon_upload_node

**核心逻辑**：
```python
def ozon_upload_node(state, config, runtime):
    response = requests.post(
        "https://api-seller.ozon.ru/v2/product/import",
        headers={
            "Client-Id": state.ozon_client_id,
            "Api-Key": state.ozon_api_key
        },
        json=state.ozon_payload
    )
    
    # 解析响应，获取多个product_id
    result = response.json()["result"]
    task_ids = result.get("task_id", "")
    
    # 如果是变体商品，需要查询每个SKU的product_id
    product_ids = []
    for item in state.ozon_payload["items"]:
        # 查询product_id（需要调用/v2/product/info/list）
        product_id = query_product_id(item["offer_id"])
        product_ids.append(product_id)
    
    return OzonUploadOutput(
        product_id=product_ids[0] if product_ids else None,
        product_ids=product_ids,
        variant_group_id=state.variant_group_id
    )
```

---

## 四、辅助函数实现

### 1. 颜色字典查询函数

```python
def query_color_dictionary(color_name: str) -> int:
    """查询颜色的dictionary_value_id"""
    # 加载颜色字典（从/tmp/ozon_color_dictionary.json）
    with open('/tmp/ozon_color_dictionary.json', 'r') as f:
        color_dict = json.load(f)
    
    # 查找匹配的颜色
    for color in color_dict["result"]:
        if color["value"].lower() == color_name.lower():
            return color["id"]
    
    # 未找到：返回默认值或抛出异常
    raise ValueError(f"颜色'{color_name}'未找到对应的dictionary_value_id")
```

---

### 2. 尺码字典查询函数

```python
def query_size_dictionary(size: str, gender: str) -> int:
    """查询尺码的dictionary_value_id"""
    # 1. 从Supabase查询俄罗斯尺码映射
    # 使用int_size（如S、M、L）查询对应的RU尺码（如44、46、48）
    
    # 2. 从尺码字典查询dictionary_value_id
    with open('/tmp/ozon_size_dictionary.json', 'r') as f:
        size_dict = json.load(f)
    
    # 查找匹配的尺码
    for size_item in size_dict["result"]:
        if size_item["value"] == str(size):
            return size_item["id"]
    
    raise ValueError(f"尺码'{size}'未找到对应的dictionary_value_id")
```

---

### 3. 俄语翻译函数

```python
def translate_to_russian(text: str) -> str:
    """将文本翻译成俄语"""
    # 使用大语言模型翻译
    llm_response = call_llm(f"Translate to Russian: {text}")
    return llm_response.content
```

---

## 五、测试验证流程

### 测试场景1：单SKU商品

**输入数据**：
```python
draft = {
    "title": "连衣裙",
    "item_id": "123456",
    "variants": []  # 单SKU，无变体数据
}
```

**预期输出**：
- payload中只有1个item
- 属性8292仍存在，但只有1个SKU

---

### 测试场景2：多SKU变体商品（2个颜色）

**输入数据**：
```python
draft = {
    "title": "连衣裙",
    "item_id": "123456",
    "variants": [
        {"sku_id": "sku_white_44", "color": "白色", "size": "44", "int_size": "S", "price": 50.0},
        {"sku_id": "sku_black_46", "color": "黑色", "size": "46", "int_size": "M", "price": 60.0}
    ]
}
```

**预期输出**：
- payload中有2个items
- 两个SKU的属性8292值相同（都是"123456"）
- 颜色、尺码属性不同
- Ozon返回2个product_id，绑定到同一卡片

---

## 六、关键注意事项

### 1. dictionary_value_id查询规则

**强制性**：
- 所有dictionary_value_id必须来自API查询或JSON文件
- **禁止凭记忆或推理填写**
- 违反会导致Ozon返回`error_attribute_values_out_of_range`错误

**实现方式**：
```python
# 加载预查询的字典JSON文件
color_dict = load_json("/tmp/ozon_color_dictionary.json")
size_dict = load_json("/tmp/ozon_size_dictionary.json")

# 查询匹配的dictionary_value_id
color_id = find_dictionary_value(color_dict, "white")
size_id = find_dictionary_value(size_dict, "44")
```

---

### 2. 标题俄语翻译

**强制性**：
- 标题必须俄语翻译
- 使用LLM或专业翻译API

**示例**：
```python
# 英文标题
"USB Mini Fan White S"

# 俄语翻译（使用LLM）
"USB迷你风扇 Белый S"
```

---

### 3. 单位转换

**规则**：
- 尺寸：1688是厘米，Ozon是毫米（乘以10）
- 重量：1688是克，Ozon也是克（不变）

**示例**：
```python
draft = {
    "depth": 30,  # cm
    "width": 40,  # cm
    "height": 60  # cm
}

# Ozon payload
{
    "depth": 300,  # mm (30 * 10)
    "width": 400,  # mm (40 * 10)
    "height": 600  # mm (60 * 10)
}
```

---

## 七、后续优化建议

### 1. 尺码映射自动化

**目标**：从1688的INT尺码（S、M、L）自动映射到俄罗斯尺码（44、46、48）

**实现**：
```python
def auto_map_size(int_size: str, gender: str) -> int:
    """自动映射尺码"""
    # 1. 查询Supabase size_mapping表
    # 2. 找到gender和int_size匹配的ru_size
    # 3. 返回俄罗斯尺码
    
    sql = f"""
    SELECT ru_size FROM size_mapping 
    WHERE gender = '{gender}' AND int_size = '{int_size}'
    LIMIT 1
    """
    
    result = execute_sql(sql)
    return result[0]["ru_size"]
```

---

### 2. 批量dictionary_value查询

**优化**：一次性查询所有需要的dictionary_value_id，避免重复查询

```python
def batch_query_dictionaries(variants: List[Dict]) -> Dict:
    """批量查询所有dictionary_value_id"""
    result = {
        "colors": {},
        "sizes": {},
        "gender": None
    }
    
    # 批量查询颜色
    unique_colors = set([v["color"] for v in variants])
    for color in unique_colors:
        result["colors"][color] = query_color_dictionary(color)
    
    # 批量查询尺码
    unique_sizes = set([v["size"] for v in variants])
    for size in unique_sizes:
        result["sizes"][size] = query_size_dictionary(size)
    
    return result
```

---

### 3. 错误处理机制

**场景**：dictionary_value_id查询失败

**处理**：
```python
try:
    color_id = query_color_dictionary(v["color"])
except ValueError as e:
    # 降级处理：使用默认值或跳过
    logger.warning(f"颜色'{v['color']}'未找到，使用默认值")
    color_id = 61571  # 默认值：white
```

---

## 八、时间表与里程碑

| 步骤 | 任务 | 预计时间 | 状态 |
|------|------|----------|------|
| 1 | 修改state.py（添加variants字段） | 10分钟 | ⏳ 待执行 |
| 2 | 修改pricing_node（支持多SKU定价） | 20分钟 | ⏳ 待执行 |
| 3 | 修改prepare_ozon_upload_node（实现变体逻辑） | 30分钟 | ⏳ 待执行 |
| 4 | 实现辅助函数（颜色、尺码查询） | 15分钟 | ⏳ 待执行 |
| 5 | 导入完整尺码表数据到Supabase | 10分钟 | ⏳ 待执行 |
| 6 | 测试单SKU场景 | 15分钟 | ⏳ 待执行 |
| 7 | 测试多SKU变体场景 | 20分钟 | ⏳ 待执行 |

---

## 九、总结

**核心成果**：
1. ✅ 查询到Ozon所有关键API数据（类目、属性、字典值）
2. ✅ 发现变体绑定机制（属性8292）
3. ✅ 生成完整的属性匹配对照表和payload规范文档
4. ✅ 导入女性尺码表数据到Supabase
5. ✅ 提供完整的实施计划和代码修改方案

**关键约束**：
- dictionary_value_id必须查询，禁止凭记忆填写
- 标题必须俄语翻译
- 单位转换：厘米→毫米（乘以10）

**下一步**：按照实施计划逐步修改代码，实现变体商品上架功能。