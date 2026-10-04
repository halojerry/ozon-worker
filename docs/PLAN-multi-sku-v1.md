# PLAN — 多 SKU 合卡能力 v1（variants 通道）：每色一 SKU 合一张卡

> 状态：**草案（待拍板）** · 起草 2026-10-05 · 依据：F 线 8 单实测证据链
> （2026-10-04，5371047）+ 用户需求（3D 耗材「每个颜色都上架然后合并成一个商品卡」）。
> 姊妹篇：PLAN-follow-clone-v1（跟卖克隆）/ PLAN-product-refresh-v1（翻新）。

## 动因

用户点名场景：3D 打印耗材等多规格简单品（颜色×少量规格），每色一 SKU、
合并一张卡——Ozon 站内竞品全是此形态（PLA 卡 4929923490 等）。**当前管线
恒单 SKU 单卡**（F 线 8 单实证）：

1. skill 信封把多色折叠进属性字符串（型号=8色串+每色数量键），`sku_id` 单值；
2. worker `variant_primary_loop` 无 variants 数据直接跳过；import items 恒 1；
3. 9048（型号名称=并卡键）是**防**并卡设计（同货源确定性派生防漂移）——
   与 Ozon 合卡机制（同 dc/tp + 同 9048 + 多 items[]）不同路；
4. CONTRACT-v4 无 variants 通道。

多 SKU 是真实经营需求：耗材/袜类/手套等「颜色即 SKU」品类的在架形态
就是竞品那样的多变体卡；单 SKU 单卡在这些类目没有竞争力（搜索权重/
加购率/卡面完整性全吃亏）。

## Ozon 合卡机制（竞品对照 + F 线证据）

- 同 `description_category_id/type_id` + **同 9048 值** + 多个 items[]
  （每 item 一个 SKU：offer_id 唯一 + 颜色属性差异）→ Ozon 合并为一张卡
  多变体（竞品 PLA 卡：同卡下 20+ 色）；
- 与我们主链「防并卡」（9048=f"{item_id}~{hash}" 确定性唯一）相反——
  **多 SKU 模式刻意同 9048**（跟卖 UPDATE 的 9048 同理，方向已验证）；
- 颜色属性（10096 等）每 item 填该色字典值；主图可共享或每变体一张。

## 核心设计

### 1. 信封 variants 通道（CONTRACT-v4 变更）

```
draft.variants = [
  {"sku_id": "1083073125898_1", "color": "белый", "color_dict_id": 972075676,
   "price_delta_cny": 0, "stock_ref": "…", "ref_image": "<该色 1688 图 URL>"},
  …  # ≤15（Ozon 卡变体上限实测 20+，保守 15）
]
```

- skill 采集腿：1688 SKU 列表已抓（`draft.sku_id` 现取 `_0` 即证据）——
  按「颜色/尺寸」维度展开（1688 颜色 SKU 键识别沿用现有解析）；
- envelope_contract 登记 `draft.variants`（值类型宽松，键存在性闸同现行）；
  gen_contract_docs 重生成。

### 2. worker 展开（prepare）

- `draft.variants` 非空 → 多 SKU 路径：每 variant 一个 import item，
  共享 dc/tp/非颜色属性，**9048 全 items 同值**（合卡键），颜色属性逐
  item 填该 variant 值，price = 主定价 ± delta；
- `variant_primary_loop` 复活：每 variant 主图 = 该色 ref_image 生图
  （现有变体主图链路本就存在，F 线证明只是无数据喂）；
- 上传/校验/学习链对 items[] 多元素已兼容（multi-SKU 分支在 prepare/
  upload 早有——F 线确认 items>1 的组装路径存在，缺的是上游数据）。

### 3. 模式选择（不破坏主链防并卡）

- 缺省：单 SKU（现状，防并卡 9048 派生不变）；
- `--variants`（skill flag）→ `draft.variants` 进信封 → worker 走展开路径
  （9048 同值合卡语义）；
- 跟卖/克隆模式不混用（follow 链维持现状，B1 拓展时再议）。

### 4. 定价与图

- 定价：主 SKU 三档定价（compute_price 唯一入口，per-variant delta 只做
  加减不再走公式）；利润闸按主 SKU；
- 图：每色一张变体主图（生图参考=该色 1688 原图）；共享营销图全变体
  复用（现状 shared_marketing_images 语义）。

## 批次

- **V1 skill**：采集腿 variants 展开（颜色 SKU 识别/键提取）+ `--variants`
  flag + 信封通道 + 预估打印（每 variant 价）；
- **V2 worker**：prepare 展开（items 多元素 + 9048 同值 + 颜色属性分配）
  + variant_primary_loop 喂数据 + 契约登记 + 回归测试
  （合卡键断言：所有 item 9048 相同、颜色属性互异、offer_id 唯一）；
- **V3 实机 gate**：F 线的耗材链接（1083073125898，8 色）+ 一条袜类，
  验收=竞品形态（一张卡 N 变体、每色可选可购），对照 4929923490 结构。

## 明确不做

- 不做跨链接合卡（variants 只来自同一 1688 offer 的 SKU 列表）；
- 不做尺码表（服装鞋帽类 size grid 是另一套 Ozon 机制，品类差异大，
  且用户明说「不同于帽鞋服装」——颜色/简单规格先行）;
- 不动主链单 SKU 缺省行为（9048 防并卡语义不变）。

## 风险

| 风险 | 缓解 |
|---|---|
| 颜色字典匹配失败（1688 中文色名→Ozon 字典值） | attr_value_matcher 既有链（不盲补首值）；无匹配 variant 降级剔除并如实报 |
| 生图成本 ×N 变体 | 变体主图 1 张/色（非 5 槽）；nano-banana-fast 单图秒级 |
| 卡审核变体丢失（Ozon 拆卡） | V3 gate 验收断言变体数；丢失则回查 9048/颜色属性键 |
| 15+ 色截断 | 超限取采购价前 15 色并如实打印剔除清单 |
