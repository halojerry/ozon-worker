# Ozon变体商品规范文档

**生成时间**: 2025-01-09
**测试店铺**: Ozon ID=4718259
**类目**: 服装类目（ID=200000933，类型=Long Sleeve ID=93148）

---

## 📊 **属性匹配对照表**

### **关键属性列表**

| 属性ID | 属性名 | 是否必填 | dictionary_id | 1688对应值 | 匹配的字典值 | dictionary_value_id | 值来源 | 说明 |
|--------|--------|----------|---------------|------------|--------------|---------------------|--------|------|
| **8292** | Merge to one PDP | **是** | 0 | 1688商品ID | - | - | 固定值 | **变体绑定**：值为1688的item_id，相同item_id的SKU合并到同一卡片 |
| **10096** | Color | **是** | 1494 | 白色/黑色/蓝色等 | white/black/blue等 | 61571/61574/61581 | API查询 | **变体属性**：每个SKU可以不同颜色，dictionary_value_id必须从API查询 |
| **4295** | Russian size | **是** | 835 | S/M/L等 | 44/46/48等 | 35428/35429/35430 | API查询 | **变体属性**：每个SKU可以不同尺码，需要使用俄罗斯尺码系统 |
| **9163** | Gender | **是** | 320 | 女装/男装/儿童 | Female/Male/Girls/Boys | 22881/22880/22882/22883 | API查询 | 性别属性，用于区分目标人群 |
| **8229** | Type | **是** | 1960 | 连衣裙/T恤等 | Dress/T-Shirt等 | API查询 | API查询 | 商品类型，不同类目有不同的类型选项 |
| **31** | Brand | **是** | 28732849 | 无品牌 | Нет бренда | **126745801** | 固定值 | **固定值**：Ozon的无品牌选项dictionary_value_id=126745801 |
| **4191** | Description | **是** | 0 | 商品描述 | 动态俄语生成 | - | 动态生成 | **商品简介**：必须填写俄语描述（根据1688标题、属性、材质生成） |
| **4389** | Production country | 否 | 1935 | 中国 | China | **90296** | API查询 | 生产国家，中国=90296 |
| **9048** | Vendor code | 否 | 0 | - | 随机数字 | - | 随机生成 | 货号，同1688商品使用相同随机值 |
| **-** | Title | **是** | - | 商品标题 | 俄语翻译 | - | 翻译 | **标题必须俄语翻译** |
| **-** | Offer ID | **是** | - | 1688 SKU_ID | - | - | 固定值 | 商家SKU编码，使用1688的SKU_ID |
| **-** | Price | **是** | - | 成本价格 | 计算后的价格 | - | 计算 | 根据成本、汇率、利润率计算 |
| **-** | Vat | **是** | - | - | "0" | - | 固定值 | 增值税固定为字符串"0" |
| **-** | Weight | **是** | - | 重量（克） | - | - | 单位转换 | 1688重量单位g，直接使用 |
| **-** | Dimensions | **是** | - | 尺寸（厘米） | mm单位 | - | 单位转换 | dimension_unit="mm"，1688尺寸（cm）需要乘以10 |
| **-** | Currency code | **是** | - | - | RUB/CNY | - | 固定值 | 货币代码，根据店铺类型选择 |
| **-** | Images | **是** | - | 图片URL列表 | URL字符串数组 | - | 翻译+生成 | 图片是URL字符串数组，每个SKU必须有独立主图 |

---

## 🎯 **变体绑定机制详解**

### **属性8292（Merge to one PDP）核心机制**

**工作原理**：
1. 同一个1688商品（相同`item_id`）有多个SKU（不同颜色、尺码）
2. 每个SKU在Ozon payload中设置`attributes`中的`id=8292`，`value=1688的item_id`
3. Ozon系统会自动将相同`item_id`的多个SKU合并到一个商品卡片（PDP）
4. 在PDP页面显示颜色/尺码选择器，每个选项对应不同的SKU

**示例**：
```json
// SKU 1：白色 S码
{
    "offer_id": "sku_white_s",
    "attributes": [
        {"id": 8292, "values": [{"value": "123456"}]},  // 1688商品ID
        {"id": 10096, "values": [{"dictionary_value_id": 61571, "value": "white"}]},  // 白色
        {"id": 4295, "values": [{"dictionary_value_id": 35428, "value": "44"}]}  // S码
    ]
}

// SKU 2：黑色 M码
{
    "offer_id": "sku_black_m",
    "attributes": [
        {"id": 8292, "values": [{"value": "123456"}]},  // 相同的1688商品ID → 合并到同一卡片
        {"id": 10096, "values": [{"dictionary_value_id": 61574, "value": "black"}]},  // 黑色
        {"id": 4295, "values": [{"dictionary_value_id": 35429, "value": "46"}]}  // M码
    ]
}
```

---

## 📝 **dictionary_value_id查询规则（CRITICAL）**

### **禁止凭记忆填写！必须通过API查询**

**规则**：
1. 所有属性中`dictionary_id > 0`的属性，其`dictionary_value_id`必须从Ozon API查询
2. 违反此规则会导致Ozon返回`error_attribute_values_out_of_range`错误
3. **禁止凭记忆、推理、硬编码填写任何dictionary_value_id**

**查询API**：
```python
POST https://api-seller.ozon.ru/v1/description-category/attribute/values
Headers: Client-Id, Api-Key
Body: {
    "attribute_id": 属性ID,
    "description_category_id": 类目ID,
    "type_id": 类型ID,
    "language": "EN",
    "limit": 1000
}
```

**示例：查询颜色字典值**
```python
# 查询属性ID=10096（颜色）
body = {
    "attribute_id": 10096,
    "description_category_id": 200000933,
    "type_id": 93148,
    "language": "EN",
    "limit": 1000
}

# 返回结果（部分）
{
    "result": [
        {"id": 61571, "value": "white"},
        {"id": 61574, "value": "black"},
        {"id": 61576, "value": "grey"},
        {"id": 61578, "value": "yellow"},
        {"id": 61579, "value": "red"},
        {"id": 61580, "value": "pink"},
        {"id": 61581, "value": "blue"},
        ...
    ]
}
```

---

## 🎨 **完整颜色字典对照表（部分）**

| 中文颜色 | 英文颜色 | dictionary_value_id | 俄语翻译 |
|---------|---------|---------------------|---------|
| 白色 | white | **61571** | белый |
| 黑色 | black | **61574** | черный |
| 灰色 | grey | **61576** | серый |
| 黄色 | yellow | **61578** | желтый |
| 红色 | red | **61579** | красный |
| 粉色 | pink | **61580** | розовый |
| 蓝色 | blue | **61581** | синий |
| 绿色 | green | **61583** | зеленый |
| 浅蓝 | light blue | **61584** | голубой |
| 橙色 | orange | **61585** | оранжевый |
| 紫色 | violet | **61586** | фиолетовый |
| 米色 | beige | **61573** | бежевый |
| 布朗 | brown | **61575** | коричневый |
| 金色 | gold | **61582** | золотой |
| 银色 | metallic grey | **61577** | серебристый |

**完整颜色字典**: 共651个颜色选项（已保存到`/tmp/ozon_color_dictionary.json`）

---

## 📏 **俄罗斯尺码对照表（部分）**

| 国际尺码 | 俄罗斯尺码 | dictionary_value_id | 适用人群 |
|---------|-----------|---------------------|---------|
| XS/S | 44 | **35428** | 女性 |
| S | 46 | **35429** | 女性 |
| M | 48 | **35430** | 女性 |
| L | 50 | **35431** | 女性 |
| XL | 52 | **35432** | 女性 |
| XXL | 54 | **35433** | 女性 |
| 通用 | universal | **35646** | 所有人群 |

**完整尺码字典**: 共400个尺码选项（已保存到`/tmp/ozon_size_dictionary.json`）

---

## 📦 **完整Ozon Payload结构示例（变体商品）**

```json
{
    "items": [
        // SKU 1：白色 S码（女装）
        {
            "name": "Длинное платье белое S (连衣裙 白色 S码)",  // 标题必须俄语翻译
            "offer_id": "sku_white_s_123456",  // 1688 SKU_ID
            "description_category_id": 200000933,  // 服装类目ID
            "type_id": 93148,  // Long Sleeve类型ID
            
            "price": "100",  // 计算后的价格（字符串）
            "old_price": "130",  // 促销价（可选）
            "vat": "0",  // 增值税固定为"0"
            "currency_code": "RUB",  // 货币代码
            
            "weight": 200,  // 重量（g）
            "weight_unit": "g",  // 重量单位固定为"g"
            "depth": 300,  // 深度（mm）
            "width": 400,  // 宽度（mm）
            "height": 600,  // 高度（mm）
            "dimension_unit": "mm",  // 尺寸单位固定为"mm"
            
            "images": [
                "https://cdn.example.com/white_s_main.jpg",  // 主图（白色S码图片）
                "https://cdn.example.com/white_s_detail.jpg"
            ],
            "primary_image": "https://cdn.example.com/white_s_main.jpg",  // 主图索引
            
            "attributes": [
                // 🔑 变体绑定属性（必须）
                {
                    "complex_id": 0,
                    "id": 8292,  // Merge to one PDP
                    "values": [{"value": "123456"}]  // 1688商品ID（用于合并）
                },
                
                // 颜色属性（变体属性）
                {
                    "complex_id": 0,
                    "id": 10096,  // Color
                    "values": [
                        {
                            "dictionary_value_id": 61571,  // 白色的dictionary_value_id（必须从API查询）
                            "value": "белый"  // 俄语颜色值
                        }
                    ]
                },
                
                // 尺码属性（变体属性）
                {
                    "complex_id": 0,
                    "id": 4295,  // Russian size
                    "values": [
                        {
                            "dictionary_value_id": 35428,  // 44码（S码）的dictionary_value_id
                            "value": "44"
                        }
                    ]
                },
                
                // 性别属性
                {
                    "complex_id": 0,
                    "id": 9163,  // Gender
                    "values": [
                        {
                            "dictionary_value_id": 22881,  // Female
                            "value": "Female"
                        }
                    ]
                },
                
                // 商品类型属性
                {
                    "complex_id": 0,
                    "id": 8229,  // Type
                    "values": [
                        {
                            "dictionary_value_id": 93148,  // Long Sleeve（需要查询）
                            "value": "Long Sleeve"
                        }
                    ]
                },
                
                // 品牌属性（固定值）
                {
                    "complex_id": 0,
                    "id": 31,  // Brand
                    "values": [
                        {
                            "dictionary_value_id": 126745801,  // 无品牌固定值
                            "value": "Нет бренда"
                        }
                    ]
                },
                
                // 商品简介（必须填写）
                {
                    "complex_id": 0,
                    "id": 4191,  // Description
                    "values": [
                        {
                            "value": "Длинное платье из хлопка, подходит для повседневной носки. Мягкий и комфортный материал. Идеально для весны и лета."  // 俄语商品描述
                        }
                    ]
                },
                
                // 生产国家（可选）
                {
                    "complex_id": 0,
                    "id": 4389,  // Production country
                    "values": [
                        {
                            "dictionary_value_id": 90296,  // China
                            "value": "China"
                        }
                    ]
                },
                
                // 货号（可选）
                {
                    "complex_id": 0,
                    "id": 9048,  // Vendor code
                    "values": [{"value": "12345678"}]  // 随机生成的数字
                }
            ]
        },
        
        // SKU 2：黑色 M码（女装）
        {
            "name": "Длинное платье черное M",
            "offer_id": "sku_black_m_123456",
            "description_category_id": 200000933,
            "type_id": 93148,
            
            "price": "110",
            "vat": "0",
            "currency_code": "RUB",
            
            "weight": 200,
            "weight_unit": "g",
            "depth": 300,
            "width": 400,
            "height": 600,
            "dimension_unit": "mm",
            
            "images": [
                "https://cdn.example.com/black_m_main.jpg",  // 黑色M码图片（独立图片）
                "https://cdn.example.com/black_m_detail.jpg"
            ],
            "primary_image": "https://cdn.example.com/black_m_main.jpg",
            
            "attributes": [
                // 🔑 相同的1688商品ID → 合并到同一卡片
                {
                    "complex_id": 0,
                    "id": 8292,
                    "values": [{"value": "123456"}]  // 相同的item_id
                },
                
                // 不同颜色
                {
                    "complex_id": 0,
                    "id": 10096,
                    "values": [
                        {
                            "dictionary_value_id": 61574,  // 黑色
                            "value": "черный"
                        }
                    ]
                },
                
                // 不同尺码
                {
                    "complex_id": 0,
                    "id": 4295,
                    "values": [
                        {
                            "dictionary_value_id": 35429,  // 46码（M码）
                            "value": "46"
                        }
                    ]
                },
                
                // 其他属性相同
                {"complex_id": 0, "id": 9163, "values": [{"dictionary_value_id": 22881, "value": "Female"}]},
                {"complex_id": 0, "id": 8229, "values": [{"dictionary_value_id": 93148, "value": "Long Sleeve"}]},
                {"complex_id": 0, "id": 31, "values": [{"dictionary_value_id": 126745801, "value": "Нет бренда"}]},
                {"complex_id": 0, "id": 4191, "values": [{"value": "Длинное платье из хлопка..."}]},
                {"complex_id": 0, "id": 4389, "values": [{"dictionary_value_id": 90296, "value": "China"}]},
                {"complex_id": 0, "id": 9048, "values": [{"value": "12345678"}]}  // 相同货号
            ]
        }
    ]
}
```

---

## ⚙️ **变体商品组装逻辑**

### **prepare_ozon_upload_node实现要点**

```python
def prepare_ozon_upload_node(state):
    # 提取变体数据
    variants = state.draft.get("variants", [])
    item_id = state.draft.get("item_id", "")  # 1688商品ID
    
    if len(variants) <= 1:
        # 单SKU：常规payload
        items = [assemble_single_sku(state)]
    else:
        # 多SKU：变体payload
        items = []
        
        for v in variants:
            # 查询颜色dictionary_value_id（必须从API查询）
            color_dict_id = query_color_dictionary(
                ozon_client_id=state.ozon_client_id,
                ozon_api_key=state.ozon_api_key,
                category_id=state.category_id,
                type_id=state.type_id,
                color_value=v["color"]  # 如"白色"
            )
            
            # 查询尺码dictionary_value_id（必须从API查询）
            size_dict_id = query_size_dictionary(
                gender=v["gender"],  # 如"Female"
                size_value=v["size"]  # 如"S"
            )
            
            # 组装SKU payload
            items.append({
                "name": translate_to_russian(f"{state.title} {v['color']} {v['size']}"),
                "offer_id": v['sku_id'],
                "price": str(int(v['price'])),
                "images": [v['image']],  # 每个SKU独立图片
                
                "attributes": [
                    # 🔑 变体绑定属性
                    {"complex_id": 0, "id": 8292, "values": [{"value": item_id}]},
                    
                    # 颜色属性（变体）
                    {"complex_id": 0, "id": 10096, "values": [
                        {"dictionary_value_id": color_dict_id, "value": translate_to_russian(v["color"])}
                    ]},
                    
                    # 尺码属性（变体）
                    {"complex_id": 0, "id": 4295, "values": [
                        {"dictionary_value_id": size_dict_id, "value": f"{size_value}"}
                    ]},
                    
                    # 其他共同属性...
                    {"complex_id": 0, "id": 31, "values": [
                        {"dictionary_value_id": 126745801, "value": "Нет бренда"}
                    ]},
                    {"complex_id": 0, "id": 4191, "values": [
                        {"value": generate_description(state)}
                    ]},
                ]
            })
    
    return PrepareOzonUploadOutput(ozon_payload={"items": items})
```

---

## 🔍 **图片规则（CRITICAL）**

### **每个SKU必须有独立主图**

**规则**：
1. **禁止共用主图**：每个变体SKU必须有自己的主图URL
2. **AI图像比对**：Ozon使用AI比对图片相似度，相似度>85%会触发"图片重复"警告
3. **图片翻译**：图片URL不需要翻译，但图片内容最好翻译成俄语文字

**示例**：
```json
// ❌ 错误：共用主图（会导致图片重复警告）
{
    "images": ["https://cdn.example.com/common_image.jpg"]  // 所有SKU使用相同图片
}

// ✅ 正确：独立主图
{
    "images": [
        "https://cdn.example.com/white_s_main.jpg",  // 白色S码专用图片
        "https://cdn.example.com/white_s_detail.jpg"
    ]
}
```

---

## 🚫 **常见错误与解决方案**

| 错误类型 | 错误原因 | 解决方案 |
|---------|---------|---------|
| error_attribute_values_out_of_range | dictionary_value_id不在字典中 | 必须通过API查询dictionary_value_id，禁止凭记忆填写 |
| 图片重复警告 | 多个SKU使用相同主图 | 每个SKU必须有独立的主图URL |
| 缺少必填属性 | 必填属性未填写 | 确认所有必填属性（is_required=true）都已填写 |
| 标题未翻译 | 标题不是俄语 | 标题必须翻译成俄语 |
| 尺寸单位错误 | dimension_unit不是"mm" | dimension_unit固定为"mm"，1688尺寸（cm）需要乘以10 |
| vat值错误 | vat不是字符串"0" | vat固定为字符串"0" |
| 品牌值错误 | 使用"Без бренда" | Ozon的无品牌值是"Нет бренда"，dictionary_value_id=126745801 |

---

## 📚 **API查询数据文件列表**

所有查询结果已保存到`/tmp`目录：

1. `ozon_category_tree_en.json` - 完整类目树（12387行）
2. `ozon_clothing_categories.json` - 服装类目列表
3. `ozon_clothing_types.json` - 服装类型列表（78个类型）
4. `ozon_attributes_clothing.json` - 服装类目属性列表（所有必填属性）
5. `ozon_color_dictionary.json` - 完整颜色字典（651个颜色）
6. `ozon_size_dictionary.json` - 完整尺码字典（400个尺码）
7. `ozon_gender_dictionary.json` - 性别字典（4个选项）
8. `ozon_country_dictionary.json` - 国家字典（267个国家）
9. `ozon_brand_dictionary.json` - 品牌字典（前100个）

---

## 🎯 **总结**

### **核心要点**

1. **变体机制**：通过属性8292绑定相同item_id的SKU到同一卡片
2. **dictionary_value_id约束**：必须通过API查询，禁止凭记忆填写
3. **标题翻译**：必须翻译成俄语
4. **图片独立**：每个SKU必须有独立主图
5. **单位转换**：dimension_unit="mm"，1688尺寸（cm）需要乘以10
6. **vat固定值**：vat="0"（字符串）
7. **品牌固定值**：Нет бренда，dictionary_value_id=126745801
8. **商品简介必填**：属性4191必须填写俄语描述

### **下一步实施**

1. ✅ 已完成Ozon API查询（类目树、属性、字典值）
2. 🔄 待导入尺码表到Supabase
3. 🔄 待实现prepare_ozon_upload_node修改（支持变体）
4. 🔄 待实现dictionary_value_id查询工具
5. 🔄 待测试变体商品上传

---

**文档生成完成！**