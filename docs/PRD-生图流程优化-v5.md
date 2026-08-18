# PRD：生图流程 v5 升级 — 基于 prompt-template-v5 的字段化提示词体系

> 版本：v1.1 · 日期：2026-08-09 · 状态：待评审
> 关联文件：`prompt-template-v5.html`（35 变量 / 10 图位采纳 / 2 套后缀）

---

## 1. 背景与目标

### 1.1 背景

当前 worker 生图链路（v0.29.x）存在以下结构性问题：

| 维度 | 现状 | 影响 |
|------|------|------|
| 提示词变量 | 仅 `{{title}}` + `{{scene_context}}` 两个占位符 | AI 无法获知材质/颜色/场景/光线/模特等关键视觉信息，出图随机性大、与产品相关性弱 |
| 提示词语言 | 中文 | 主流生图模型（gpt-image-2 / nano-banana）对英文 prompt 理解更精准，中文 prompt 导致风格控制力下降 |
| 图位覆盖 | 10 个生图节点（white_bg / multi_angle / main / detail / social_proof / scene_1-3 / comparison / variant_white_bg） | 图位数量已满足，但提示词质量不足（变量/后缀/negative 缺失） |
| draft 数据利用 | 仅取 `title`，scene_generation_llm 取 `title/description/category` | `attributes`（1688 属性：材质/颜色/尺寸）、`weight`、`dimensions`、`variants` 等丰富数据未被生图消费 |
| 配色体系 | 无 | 无法按品类统一视觉调性，同产品 10 张图风格割裂 |
| Negative Prompt | 无 | 模型可能生成水印/文字/logo，需后期人工排查 |
| 俄文叠加层 | 无数据生成与存储 | 后期 PS 叠加俄文时需人工逐张填写，无法批量化 |

`prompt-template-v5.html` 定义了一套成熟的字段化提示词体系（35 变量 + 22 图位 + 3 套后缀 + 8 配色预设），已在人工运营中验证有效。本次升级**仅采用其中与现有 10 个生图节点相关的变量、后缀（A/B）和配色预设**，不新增图位。

### 1.2 目标

| 目标 | 衡量指标 |
|------|---------|
| **G1 变量覆盖** | 生图 prompt 可用变量从 2 个 → 23 个（19 视觉 + 4 系统），覆盖模板 v5 全部 AI 生图变量 |
| **G2 图位优化** | 保持现有 10 个生图节点，聚焦提示词质量提升（不新增图位） |
| **G3 数据消费** | draft 中 `attributes/weight/dimensions/variants/category` 等 8 类数据被生图流程消费 |
| **G4 提示词质量** | prompt 切换为英文 + 后缀拼接 + Negative Prompt，出图与产品相关性提升（人工抽检通过率 ≥85%） |
| **G5 配色统一** | 按品类自动路由 8 种 COLOR_PRESET，同产品图位风格一致性提升 |
| **G6 俄文叠加数据** | 生成并持久化 13 组俄文叠加变量，供后期 PS/Canva 批量叠加 |
| **G7 向后兼容** | 升级不破坏现有 10 节点行为；visual_vars 为空时回退旧逻辑 |

### 1.3 非目标

- **不新增图位**：模板 v5 中的图位 11-22（step_by_step / flow_chart / packaging / scene_matrix / operation / attribute_bar / triple_data / packaging_hero / ugc_review / important_notice / variants_picker / detail_triptych）本次不实现
- 不涉及俄文文字的 AI 直接渲染（模板明确：俄文由后期 PS/Canva 叠加，AI 只留白）
- 不替换生图模型（gpt-image-2 / nano-banana-fast 路由不变）
- 不改动 Ozon 上传/审核链路（ozon_upload_node / fetch_back_node 不变）

---

## 2. prompt-template-v5.html 模板结构分析

### 2.1 整体结构

```
prompt-template-v5.html
├── 变量字段定义（35 个）
│   ├── 🎨 视觉变量（19 个，AI 生图用，英文）
│   ├── ⚙️ 系统/风格变量（4 个）
│   └── 📝 俄文叠加变量（13 组，后期 PS 用）
├── 8 种品类配色预设（COLOR_PRESET）
├── 6 种标题修辞风格（HEADLINE_STYLE）
├── 通用后缀（3 套，本次仅采用 A/B）
├── 22 种图片模板（图位，本次仅采用其中 10 种）
├── 俄文文字叠加规范总表
├── 反向推导 Prompt 示例（20 张参考图）
└── 完整工作流（6 步）
```

### 2.2 字段定义 — 视觉变量（19 个，AI 生图用）

> 橙色组。这些是注入 AI 生图 prompt 的核心变量，全部为**英文描述**（适配 gpt-image-2 / nano-banana）。

| # | 变量名 | 含义 | 数据类型 | 必填 | 数据来源 | 示例（IPL 脱毛仪） |
|---|--------|------|----------|------|----------|-------------------|
| 1 | `{PRODUCT}` | 产品名称+外观描述 | string | ✅ | LLM 从 title+images 推断 | premium blue and rose-gold IPL photo epilator, sleek ergonomic body, ice-cooling head |
| 2 | `{COLOR}` | 产品主色调 | string | ✅ | LLM 从 attributes/图片推断 | темно-синий + медный |
| 3 | `{MATERIAL}` | 材质 | string | ✅ | draft.attributes（材质属性） | гладкий ABS-пластик |
| 4 | `{APPEARANCE}` | 外观形态 | string | ✅ | LLM 从 title+images 推断 | компактная ручка с LED-вспышкой |
| 5 | `{SIZE}` | 尺寸/容量 | string | ✅ | draft.dimensions / draft.weight | 15×5×3 см |
| 6 | `{MODEL}` | 画面主体/模特 | string | ⬜ | LLM 按品类推断 | young blonde woman with glowing skin |
| 7 | `{ACTION}` | 与产品的交互动作 | string | ⬜ | LLM 按品类推断 | gliding the epilator on her forearm with bright flash of light |
| 8 | `{SCENE}` | 拍摄场景 | string | ⬜ | scene_generation_llm（已有） | modern bathroom vanity with soft bokeh |
| 9 | `{BACKGROUND}` | 背景色调 | string | ⬜ | LLM 按 COLOR_PRESET 推断 | deep navy blue with subtle bokeh highlights |
| 10 | `{LIGHTING}` | 光线风格 | string | ✅ | LLM 按 COLOR_PRESET 推断 | dramatic beauty lighting with cool blue rim light and warm skin tones |
| 11 | `{EFFECTS}` | 特效/光效/粒子 | string | ✅ | LLM 按品类推断 | bright white flash from epilator window, subtle lens flare, soft glow |
| 12 | `{TEXT_AREAS}` | 文字留白区域布局 | string | ✅ | 按图位固定模板 | top-left headline band + upper right circular badges + bottom-left gift capsule |
| 13 | `{ICONS}` | 底部图标行 | string | ⬜ | LLM 按品类推断 | three circular icons: cooling mode, unlimited flashes, wireless |
| 14 | `{INSET}` | 小插图内容 | string | ⬜ | LLM 按品类推断 | small inset showing razor and sunglasses gift accessories |
| 15 | `{GIFT}` | 赠品/配件/礼包 | string | ⬜ | LLM 从 attributes/title 推断 | bonus accessories: safety razor and protective glasses in corner |
| 16 | `{ATMOSPHERE}` | 氛围关键词 | string | ⬜ | LLM 按 COLOR_PRESET 推断 | премиальный / уютный |
| 17 | `{PACKAGING}` | 产品包装描述 | string | ⬜ | LLM 推断 | чёрно-синяя коробка с золотым логотипом |
| 18 | `{PROBLEM_SCENE}` | 问题场景（对比图用） | string | ⬜ | LLM 按品类推断 | left side: irritated skin with visible stubble |
| 19 | `{COMPARISON}` | 对比/解决方案描述 | string | ⬜ | LLM 按品类推断 | right side: smooth skin after IPL treatment |

**必填说明**：`PRODUCT/COLOR/MATERIAL/APPEARANCE/SIZE` 为产品基础属性，必须从 draft 提取或 LLM 推断；`LIGHTING/EFFECTS/TEXT_AREAS` 是 v5 高阶技巧明确标注的「prompt 质量核心」，直接影响出图氛围感和留白位置。

### 2.3 字段定义 — 系统/风格变量（4 个）

> 绿色组。控制整体视觉调性。

| # | 变量名 | 含义 | 数据类型 | 必填 | 取值范围 | 路由逻辑 |
|---|--------|------|----------|------|----------|----------|
| 20 | `{COLOR_PRESET}` | 配色预设代号 | enum | ✅ | TOY_KIDS / PET_FUN / INDUSTRIAL / GARDEN / TECH_BLUE / BEAUTY_PINK / WARM_SLEEP / HOME_LIFESTYLE | 按 draft.category 映射（见 §5.2.2） |
| 21 | `{BRAND_PRIMARY}` | 品牌主色 HEX | string | ⬜ | HEX 色值如 `#1E3A5F` | COLOR_PRESET 默认色，可被 attributes 覆盖 |
| 22 | `{ACCENT}` | 强调色 HEX | string | ⬜ | HEX 色值如 `#F59E0B` | COLOR_PRESET 默认色，可被 attributes 覆盖 |
| 23 | `{HEADLINE_STYLE}` | 标题修辞风格 | enum | ⬜ | EXCLAIM / PROMISE / NUMBER / CONTRAST / QUESTION / TWO_LINE_TWO_COLOR | LLM 按品类推断 |

### 2.4 字段定义 — 俄文叠加变量（13 组，后期 PS 用）

> 蓝色组。**不注入 AI 生图 prompt**，仅生成并存储，供后期 PS/Canva 叠加。

| # | 变量名 | 含义 | 数据类型 | 数据来源 |
|---|--------|------|----------|----------|
| 24 | `{PRODUCT_RU}` | 俄文产品名 | string | LLM 翻译 title |
| 25 | `{CTA_RU}` | 俄文行动号召/大标题 | string | LLM 生成 |
| 26 | `{SELLING_POINTS_RU}` | 俄文卖点（分号分隔） | string | LLM 从 attributes 生成 |
| 27 | `{EFFECT_DATA_RU}` | 俄文效果数据 | string | LLM 从 attributes/weight 生成 |
| 28 | `{TARGET_RU}` | 俄文目标对象 | string | LLM 按品类推断 |
| 29 | `{BONUS_RU}` | 俄文赠品/促销 | string | LLM 推断 |
| 30 | `{ATTRIBUTE_1/2/3}` | 属性胶囊 1/2/3 | string[3] | LLM 从 attributes 提取 top3 |
| 31 | `{DATA_1_VALUE/UNIT}` | 性能数据 1 值/单位 | string[2] | LLM 从 attributes 提取 |
| 32 | `{DATA_2_VALUE/UNIT}` | 性能数据 2 值/单位 | string[2] | LLM 从 attributes 提取 |
| 33 | `{DATA_3_VALUE/UNIT}` | 性能数据 3 值/单位 | string[2] | LLM 从 attributes 提取 |
| 34 | `{REVIEW_NAME/STARS/TEXT}` | 用户评论 名/星/文 | string[3] | LLM 生成（模拟真实评论） |
| 35 | `{VARIANT_A/B}` | 多 SKU 名称 | string[2] | draft.variants 提取 |

### 2.5 本次采用的 10 种图片模板（图位）

> 模板 v5 共定义 22 种图位，本次仅采用与现有 worker 10 个生图节点对应的图位（1-10）。图位 11-22（step_by_step / flow_chart / packaging / scene_matrix / operation / attribute_bar / triple_data / packaging_hero / ugc_review / important_notice / variants_picker / detail_triptych）不在本次范围内。

| # | 图位 key | 中文名 | 文字策略 | 后缀 | 当前 worker |
|---|---------|--------|---------|------|------------|
| 1 | `main` | 场景主图（首图） | 允许俄文 | A | ✅ 已有 |
| 2 | `white_bg` | 纯白底产品图 | 禁止文字 | B | ✅ 已有 |
| 3 | `multi_angle` | 多角度展示 | 禁止文字 | B | ✅ 已有 |
| 4 | `scene_1` | 场景图1（生活化） | 允许俄文 | A | ✅ 已有 |
| 5 | `scene_2` | 场景图2（特殊场景） | 允许俄文 | A | ✅ 已有 |
| 6 | `scene_3` | 场景图3（对比/差异化） | 允许俄文 | A | ✅ 已有 |
| 7 | `comparison` | 对比图 | 允许俄文 | A | ✅ 已有 |
| 8 | `detail` | 细节/材质特写 | 允许俄文 | A | ✅ 已有 |
| 9 | `social_proof` | 真人好评/UGC | 允许俄文 | A | ✅ 已有 |
| 10 | `variant_white_bg` | 变体白底图 | 禁止文字 | B | ✅ 已有 |

### 2.6 本次采用的 2 套通用后缀

> 模板 v5 共定义 3 套后缀（A/B/C），后缀 C（编号教学型）仅服务于 step_by_step / flow_chart 图位，本次不采用。

| 后缀 | 适用图位 | 内容 | Negative Prompt |
|------|---------|------|----------------|
| **A 允许文字型** | main / scene_1-3 / comparison / detail / social_proof（7 种） | `vertical 3:4 aspect ratio, professional Ozon-style e-commerce product photography, realistic skin and material texture, soft natural light with subtle rim light, shallow depth of field, ultra detailed, 8k, photorealistic, clean negative space for Russian text overlay, commercial advertising composition, high contrast colors, color scheme based on {COLOR_PRESET}` | no English/Chinese/Russian text, no watermark, logo, barcode, price, QR code, no harsh shadows, oversaturation, distorted hands, ugly fingers, extra limbs |
| **B 纯白底型** | white_bg / multi_angle / variant_white_bg（3 种） | `pure white background #FFFFFF, professional e-commerce product photography, centered composition, even soft studio lighting, ultra detailed material texture, sharp focus, 8k, photorealistic, no environmental context` | 严格：no text of any kind, no Russian/English/Chinese text, no watermark, logo, label, barcode, number, price, QR code, promotional graphics, infographic elements, no shadows on background |

---

## 3. 当前 worker 数据流转分析

### 3.1 现有链路

```
skill 层发起请求
  └─ envelope（draft + source + extensions）
     └─ ingest_node 解析 envelope → 提取 draft
        └─ auth_node 校验 token + 余额
           └─ scene_generation_llm_node（LLM 生成 scene_context_1/2/3）
              └─ ImageGenSubgraph（10 个生图节点并行/串行）
                 ├─ Phase1: white_bg → multi_angle（参考图基础）
                 └─ Phase2: main / detail / social_proof / scene_1-3 / comparison（消费 Phase1 参考图）
                    └─ 每个节点:
                       1. get_image_prompt(key, title=..., scene_context=...)  ← Jinja2 渲染（仅 2 变量）
                       2. get_image_model(key)  ← gpt-image-2 / nano-banana-fast
                       3. call_mxou_image_api(model, prompt, ref_images, aspect_ratio="3:4")
                       4. save_image(task_id, key, url)  ← 缓存
```

### 3.2 draft 可用字段（已存在但未被生图消费）

| 字段 | 类型 | 当前生图消费 | 模板 v5 可映射变量 |
|------|------|------------|------------------|
| `title` | string | ✅ `{{title}}` | `{PRODUCT}` / `{PRODUCT_RU}` |
| `description` | string | ✅ scene_llm 用 | `{PRODUCT}` / `{APPEARANCE}` |
| `category` | string | ✅ scene_llm 用 | `{COLOR_PRESET}` 路由依据 |
| `attributes` | dict | ❌ 未用 | `{COLOR}` / `{MATERIAL}` / `{SIZE}` / `{ATTRIBUTE_1/2/3}` / `{DATA_1/2/3}` |
| `weight` | number | ❌ 未用 | `{SIZE}`（容量/重量） |
| `dimensions` | dict{depth,width,height} | ❌ 未用 | `{SIZE}`（尺寸） |
| `variants` | list[dict] | ❌ 未用 | `{VARIANT_A/B}` |
| `images` | list[string] | ✅ 参考图 | `{PRODUCT}`（视觉推断辅助） |
| `ozon_attributes` | dict | ❌ 未用 | `{COLOR}` / `{MATERIAL}`（Ozon 俄文属性值） |

### 3.3 现有提示词示例（image_prompts.json → main）

```
产品：{{title}}。这是电商营销主图，适合俄罗斯消费者的审美偏好。
画面以产品为视觉中心，占比不低于60%。背景是自然融入的场景环境，
柔和自然光打在产品上，清晰呈现材质纹理与细节。整体调性简洁高级。
画面中不得出现任何水印、logo、品牌标识、价格标签...
```

**问题**：中文 + 仅 title + 无材质/颜色/光线/场景等视觉变量 + 无后缀拼接 + 无 Negative Prompt。

### 3.4 模板 v5 期望的 main prompt（变量注入后）

```
{MODEL}, {ACTION}, looking directly at camera with big expressive eyes.
{PRODUCT} prominently displayed in center foreground. {SCENE} in soft focus
background, {LIGHTING}, {BACKGROUND}, {EFFECTS}. Vertical 3:4 composition
with upper third clean for bold headline text, {TEXT_AREAS}, {INSET} in
corner, {GIFT}. {COLOR_PRESET} color scheme, {ATMOSPHERE} atmosphere.
+ 后缀 A: vertical 3:4 aspect ratio, professional Ozon-style e-commerce...
+ Negative: no English text, no Chinese text, no Russian text, no watermark...
```

---

## 4. 字段缺失与数据不匹配问题

### 4.1 问题清单

| # | 问题 | 严重度 | 根因 | 影响 |
|---|------|--------|------|------|
| P1 | **变量覆盖严重不足**：模板需 23 个 AI 变量，worker 仅提供 2 个 | 🔴 P0 | `get_image_prompt()` 只渲染 `{{title}}`/`{{scene_context}}` | 出图与产品相关性弱，随机性大 |
| P2 | **提示词语言错位**：模板为英文，worker 为中文 | 🔴 P0 | `image_prompts.json` 全部中文模板 | 模型对中文 prompt 风格控制力差 |
| P3 | **draft 数据未消费**：attributes/weight/dimensions/variants 4 类数据闲置 | 🔴 P0 | 生图节点只取 `draft.title` | 材质/颜色/尺寸等关键信息丢失 |
| P4 | **缺视觉变量生成 LLM**：只有 scene_context 生成，无 19 视觉变量生成 | 🔴 P0 | 无 `visual_vars_llm_node` | MODEL/ACTION/LIGHTING/EFFECTS 等核心变量缺失 |
| P5 | **缺配色预设路由**：无 COLOR_PRESET 按品类映射 | 🟠 P1 | 无 `color_preset_router` | 同产品图位风格割裂 |
| P6 | **缺后缀拼接逻辑**：模板有 2 套后缀（A/B），worker 无拼接 | 🟠 P1 | `get_image_prompt()` 直接返回模板原文 | 缺少比例/质量/Negative 描述 |
| P7 | **缺 Negative Prompt**：模板有详细 negative，worker 无 | 🟠 P1 | API 调用未传 negative_prompt | 模型可能生成水印/文字/logo |
| P8 | **缺俄文叠加数据生成**：13 组俄文变量无生成与存储 | 🟡 P2 | 无 `ru_overlay_llm_node` | 后期 PS 需人工逐张填写 |
| P9 | **场景生成 LLM 输出粗糙**：只输出中文 scene_context，不输出结构化视觉变量 | 🟡 P2 | `scene_generation_llm_node` 输出 schema 简单 | SCENE 变量质量不足 |

### 4.2 数据流转断点图

```
draft.title ────────────────────► {{title}} ✅
draft.description ───(scene_llm)─► {{scene_context}} ⚠️(仅场景，非完整视觉变量)
draft.category ──────────────────► ✗ 未路由到 {COLOR_PRESET}
draft.attributes ────────────────► ✗ 未提取 {COLOR}/{MATERIAL}/{SIZE}
draft.weight ────────────────────► ✗ 未映射 {SIZE}
draft.dimensions ────────────────► ✗ 未映射 {SIZE}
draft.variants ──────────────────► ✗ 未映射 {VARIANT_A/B}
draft.ozon_attributes ───────────► ✗ 未提取俄文属性值

缺失节点:
  ✗ visual_vars_llm_node（生成 19 视觉变量）
  ✗ color_preset_router（品类→COLOR_PRESET 映射）
  ✗ ru_overlay_llm_node（生成 13 组俄文叠加变量）
  ✗ prompt_assembler（变量注入 + 后缀拼接 + Negative）
```

---

## 5. 改进后的生图流程设计方案

### 5.1 总体架构

升级后的生图链路新增 3 个预处理节点 + 1 个提示词组装器，位于 scene_generation_llm 之后、ImageGenSubgraph 之前。**不新增生图节点，仅优化现有 10 个节点的提示词质量**：

```
ingest → auth → scene_generation_llm → 【新增】visual_vars_llm → 【新增】color_preset_router
                                                                    ↓
                                                          【新增】prompt_context（聚合）
                                                                    ↓
                                              【新增】ru_overlay_llm（异步，不阻塞生图）
                                                                    ↓
                          ImageGenSubgraph（10 节点，每个节点调 prompt_assembler 组装最终 prompt）
```

### 5.2 新增节点设计

#### 5.2.1 visual_vars_llm_node（视觉变量生成 LLM）

**职责**：消费 draft 全量数据，生成 19 个视觉变量（英文）。

**输入**：`draft`（title/description/category/attributes/weight/dimensions/variants/images）

**输出**（VisualVarsOutput）：
```python
{
  "product": "premium blue and rose-gold IPL photo epilator, sleek ergonomic body",
  "color": "navy blue + rose gold",
  "material": "smooth ABS plastic",
  "appearance": "compact handle with LED flash window",
  "size": "15×5×3 cm",
  "model": "young blonde woman with glowing skin",
  "action": "gliding the epilator on her forearm with bright flash of light",
  "scene": "modern bathroom vanity with soft bokeh",
  "background": "deep navy blue with subtle bokeh highlights",
  "lighting": "dramatic beauty lighting with cool blue rim light",
  "effects": "bright white flash, subtle lens flare, soft glow",
  "text_areas": "top-left headline band + upper right circular badges",
  "icons": "three circular icons: cooling mode, unlimited flashes, wireless",
  "inset": "small inset showing razor and sunglasses gift accessories",
  "gift": "bonus accessories: safety razor and protective glasses",
  "atmosphere": "premium / cozy",
  "packaging": "black-blue box with gold logo",
  "problem_scene": "left side: irritated skin with visible stubble",
  "comparison": "right side: smooth skin after IPL treatment"
}
```

**实现要点**：
- 复用 `call_mxou_chat_api`（deepseek-v4-flash），system prompt 指示输出 JSON
- `attributes` 中的材质/颜色/尺寸字段直接提取，减少 LLM 幻觉
- `images` URL 传入 LLM（多模态，若模型支持）辅助视觉推断
- 失败兜底：按 category 查品类速查表（模板已提供 13 类预设）

#### 5.2.2 color_preset_router（配色预设路由）

**职责**：按 draft.category 映射到 8 种 COLOR_PRESET 之一，并输出对应 BRAND_PRIMARY / ACCENT。

**映射规则**（基于模板「已验证品类速查表」）：

| 品类关键词（category 含） | COLOR_PRESET | BRAND_PRIMARY | ACCENT |
|--------------------------|--------------|---------------|--------|
| 驱蚊/蚊/杀虫 | GARDEN | #16A34A | #A16207 |
| 宠物/猫/狗/pet | PET_FUN | #3B82F6 | #F97316 |
| 美妆/护肤/美容 | BEAUTY_PINK | #1E3A5F | #F59E0B |
| 母婴/婴儿/儿童 | TOY_KIDS | #22C55E | #F97316 |
| 家居/收纳/家纺 | HOME_LIFESTYLE | #A16207 | #1E40AF |
| 电子/数码/3C/充电 | TECH_BLUE | #1E40AF | #06B6D4 |
| 清洁/化工/工业 | INDUSTRIAL | #000000 | #DC2626 |
| 睡眠/夜灯/卧室 | WARM_SLEEP | #1E293B | #F59E0B |
| （默认） | HOME_LIFESTYLE | #A16207 | #FED7AA |

**实现**：纯函数，无 LLM 调用，配置外置 `config/color_presets.json`（热加载）。

#### 5.2.3 ru_overlay_llm_node（俄文叠加变量生成）

**职责**：生成 13 组俄文叠加变量，存入 draft 供后期 PS 使用。**异步执行，不阻塞生图**。

**输出**（RuOverlayOutput）：
```python
{
  "product_ru": "ФОТОЭПИЛЯТОР",
  "cta_ru": "Безлимит вспышек",
  "selling_points_ru": "3 в 1; режим охлаждения; безлимит вспышек; беспроводной",
  "effect_data_ru": "3В1; ∞ вспышек; беспроводной",
  "target_ru": "волосы; кожа",
  "bonus_ru": "Подарки в наборе",
  "attribute_1": "3 в 1", "attribute_2": "БЕСПРОВОДНОЙ", "attribute_3": "БЕСШУМНЫЙ",
  "data_1_value": "8800", "data_1_unit": "ударов/мин",
  "data_2_value": "600", "data_2_unit": "Нм",
  "data_3_value": "7200", "data_3_unit": "об/мин",
  "review_name": "Сергей П.", "review_stars": "★★★★★", "review_text": "...",
  "variant_a": "мышка", "variant_b": "дразнилка"
}
```

**中文零容忍**：所有输出必须西里尔字符，禁止中文（复用 `_russian_required_attrs` 校验逻辑）。

#### 5.2.4 prompt_assembler（提示词组装器）

**职责**：将 10 图位模板 + 23 变量 + 2 套后缀（A/B） + Negative Prompt 组装成最终英文 prompt。

**组装流程**：
```
final_prompt = 图位模板(变量注入) + " " + 后缀(后缀变量注入) + " ||NEG|| " + negative_prompt
```

**调用方式**（在每个生图节点内）：
```python
from utils.prompt_assembler import assemble_prompt

prompt, negative = assemble_prompt(
    slot_key="main",              # 图位
    visual_vars=state.visual_vars, # 19 视觉变量
    color_preset=state.color_preset,  # COLOR_PRESET + BRAND_PRIMARY + ACCENT
    headline_style=state.visual_vars.get("headline_style", "EXCLAIM"),
)
# prompt → call_mxou_image_api(prompt=prompt, ...)
# negative → 传给 API（若支持）或拼入 prompt 尾部
```

**配置外置**：`config/image_slots_v5.json`（10 图位模板 + 2 后缀 + negative，热加载）。

### 5.3 生图节点改造（现有 10 节点，向后兼容）

每个节点将 `get_image_prompt(key, title=..., scene_context=...)` 替换为 `assemble_prompt(slot_key, visual_vars, color_preset)`。

**兼容策略**：若 `visual_vars` 为空（LLM 失败兜底），回退到旧版 `get_image_prompt`（中文模板），保证不阻断生图。

**影响文件**：main / white_bg / multi_angle / detail / social_proof / scene_1-3 / comparison / variant_white_bg 共 10 个 node。

### 5.4 升级后数据流转

```
draft
  ├─ title/description ──► visual_vars_llm ──► {PRODUCT}{APPEARANCE}{MODEL}{ACTION}...
  ├─ attributes ─────────► visual_vars_llm ──► {COLOR}{MATERIAL}{SIZE}{ATTRIBUTE_*}{DATA_*}
  ├─ weight/dimensions ──► visual_vars_llm ──► {SIZE}
  ├─ variants ───────────► ru_overlay_llm ───► {VARIANT_A/B}
  ├─ category ───────────► color_preset_router ► {COLOR_PRESET}{BRAND_PRIMARY}{ACCENT}
  ├─ ozon_attributes ────► ru_overlay_llm ───► {COLOR_RU}{MATERIAL_RU}（俄文属性复用）
  └─ images ─────────────► 参考图（Phase1 white_bg/multi_angle）

       visual_vars(19) + color_preset(3) + headline_style(1)
                          ↓
                   prompt_assembler
                          ↓
     图位模板 + 变量注入 + 后缀(A/B) + Negative Prompt
                          ↓
              call_mxou_image_api(model, prompt, negative)
                          ↓
                   save_image(task_id, slot, url)

       ru_overlay(13组) ──► draft.ru_overlay ──► 持久化 ──► 后期 PS/Canva
```

---

## 6. 功能需求

### FR-1 视觉变量生成 LLM 节点

| 项 | 内容 |
|----|------|
| **触发** | scene_generation_llm_node 之后自动执行 |
| **输入** | draft（title/description/category/attributes/weight/dimensions/variants/images） |
| **输出** | 19 个视觉变量（英文），JSON 结构 |
| **模型** | deepseek-v4-flash（复用 mxou LLM） |
| **兜底** | LLM 失败 → 按 category 查品类速查表（config/category_visual_defaults.json） |
| **缓存** | 按 task_id 缓存 visual_vars，重跑不重烧 |

### FR-2 配色预设路由

| 项 | 内容 |
|----|------|
| **触发** | visual_vars_llm 之后，纯函数无 LLM |
| **输入** | draft.category |
| **输出** | {color_preset, brand_primary, accent} |
| **配置** | config/color_presets.json（热加载） |

### FR-3 俄文叠加变量生成 LLM 节点

| 项 | 内容 |
|----|------|
| **触发** | 与 ImageGenSubgraph 并行（异步，不阻塞生图） |
| **输入** | draft + visual_vars |
| **输出** | 13 组俄文变量 |
| **模型** | deepseek-v4-flash |
| **校验** | 中文零容忍（西里尔校验，复用 `_russian_required_attrs`） |
| **存储** | 写入 draft.ru_overlay，随 task 持久化 |

### FR-4 提示词组装器

| 项 | 内容 |
|----|------|
| **输入** | slot_key + visual_vars + color_preset + headline_style |
| **输出** | (prompt: str, negative_prompt: str) |
| **配置** | config/image_slots_v5.json（10 图位模板 + 2 后缀 + negative） |
| **兜底** | 配置缺失/渲染失败 → 回退旧版 get_image_prompt（中文） |

### FR-5 现有 10 节点改造

| 项 | 内容 |
|----|------|
| **改动** | 每节点 `get_image_prompt()` → `assemble_prompt()` |
| **兼容** | visual_vars 为空时回退旧逻辑 |
| **影响文件** | main/white_bg/multi_angle/detail/social_proof/scene_1-3/comparison/variant_white_bg 共 10 个 node |

### FR-6 API 传参增强

| 项 | 内容 |
|----|------|
| **改动** | call_mxou_image_api 增加 negative_prompt 参数 |
| **兼容** | API 不支持 negative 时拼入 prompt 尾部 `||NEG|| ...` |

---

## 7. 字段数据规范

### 7.1 PromptContext（生图上下文，聚合传递）

```typescript
interface PromptContext {
  // 视觉变量（19，英文）
  visual_vars: {
    product: string        // 必填，产品名称+外观描述
    color: string          // 必填，主色调
    material: string       // 必填，材质
    appearance: string     // 必填，外观形态
    size: string           // 必填，尺寸/容量
    model: string          // 可选，画面主体/模特
    action: string         // 可选，交互动作
    scene: string          // 可选，拍摄场景（复用 scene_context）
    background: string     // 可选，背景色调
    lighting: string       // 必填，光线风格
    effects: string        // 必填，特效/光效
    text_areas: string     // 必填，文字留白布局
    icons: string          // 可选，底部图标行
    inset: string          // 可选，小插图
    gift: string           // 可选，赠品
    atmosphere: string     // 可选，氛围关键词
    packaging: string      // 可选，包装描述
    problem_scene: string  // 可选，问题场景
    comparison: string     // 可选，对比描述
  }
  // 系统变量（4）
  color_preset: string       // 必填，enum 8 选 1
  brand_primary: string      // 可选，HEX
  accent: string             // 可选，HEX
  headline_style: string     // 可选，enum 6 选 1
}
```

### 7.2 RuOverlay（俄文叠加变量，13 组）

```typescript
interface RuOverlay {
  product_ru: string         // 俄文产品名
  cta_ru: string             // 俄文行动号召
  selling_points_ru: string  // 俄文卖点（分号分隔）
  effect_data_ru: string     // 俄文效果数据
  target_ru: string          // 俄文目标对象
  bonus_ru: string           // 俄文赠品
  attribute_1: string        // 属性胶囊1
  attribute_2: string        // 属性胶囊2
  attribute_3: string        // 属性胶囊3
  data_1_value: string       // 性能数据1值
  data_1_unit: string        // 性能数据1单位
  data_2_value: string       // 性能数据2值
  data_2_unit: string        // 性能数据2单位
  data_3_value: string       // 性能数据3值
  data_3_unit: string        // 性能数据3单位
  review_name: string        // 评论用户名
  review_stars: string       // 评论星级
  review_text: string        // 评论文本
  variant_a: string          // SKU变体A名
  variant_b: string          // SKU变体B名
}
```

### 7.3 draft 扩展字段

```typescript
interface DraftExtension {
  // v5 新增
  visual_vars?: VisualVars      // 19 视觉变量
  color_preset?: string         // 配色预设代号
  brand_primary?: string        // 品牌主色
  accent?: string               // 强调色
  ru_overlay?: RuOverlay        // 13 组俄文叠加变量
}
```

---

## 8. 接口定义

### 8.1 visual_vars_llm_node

```python
# 输入
class VisualVarsInput(BaseModel):
    draft: Optional[Dict[str, Any]] = Field(default=None)
    token: str = Field(default="")
    scene_context_1: str = Field(default="")  # 复用 scene_generation_llm 输出
    scene_context_2: str = Field(default="")
    scene_context_3: str = Field(default="")

# 输出
class VisualVarsOutput(BaseModel):
    visual_vars: Dict[str, str] = Field(default_factory=dict)  # 19 视觉变量
    color_preset: str = Field(default="HOME_LIFESTYLE")
    brand_primary: str = Field(default="")
    accent: str = Field(default="")
```

### 8.2 prompt_assembler

```python
def assemble_prompt(
    slot_key: str,                          # 图位 key，如 "main"
    visual_vars: Dict[str, str],            # 19 视觉变量
    color_preset: str = "HOME_LIFESTYLE",   # 配色预设
    brand_primary: str = "",                # 品牌主色 HEX
    accent: str = "",                       # 强调色 HEX
    headline_style: str = "EXCLAIM",        # 标题风格
) -> tuple[str, str]:
    """
    返回 (prompt, negative_prompt)。
    prompt = 图位模板(变量注入) + " " + 后缀(变量注入)
    negative_prompt = 对应后缀的 negative prompt
    失败回退旧版 get_image_prompt()，negative 返回空串。
    """
```

### 8.3 call_mxou_image_api 增强

```python
def call_mxou_image_api(
    token: str,
    prompt: str,
    negative_prompt: str = "",               # 🆕 v5 新增
    ref_images: Optional[List[str]] = None,
    aspect_ratio: str = "3:4",
    timeout: int = 180,
    max_retries: int = 1,
    model: str = PRIMARY_IMAGE_MODEL,
) -> Optional[str]:
    """
    negative_prompt 非空时：
    - API 支持 negative 字段 → payload["negative"] = negative_prompt
    - API 不支持 → prompt 尾部追加 " ||NEG|| {negative_prompt}"
    """
```

### 8.4 ru_overlay_llm_node

```python
class RuOverlayInput(BaseModel):
    draft: Optional[Dict[str, Any]] = Field(default=None)
    token: str = Field(default="")
    visual_vars: Dict[str, str] = Field(default_factory=dict)

class RuOverlayOutput(BaseModel):
    ru_overlay: Dict[str, str] = Field(default_factory=dict)  # 13 组俄文变量
```

---

## 9. 流程图说明

### 9.1 升级前流程

```
ingest → auth → scene_generation_llm ──► ImageGenSubgraph（10节点）
                    ↓                          ↓
              scene_context_1/2/3     get_image_prompt(title, scene_context)
                                              ↓
                                      call_mxou_image_api(prompt)
                                              ↓
                                      save_image(task_id, slot, url)
```

### 9.2 升级后流程

```
ingest → auth → scene_generation_llm
                    ↓
          ┌─ visual_vars_llm ──► 19 视觉变量（英文）
          │       ↓
          │  color_preset_router ──► COLOR_PRESET + BRAND_PRIMARY + ACCENT
          │       ↓
          │  prompt_context（聚合 23 变量）
          │       ↓                    ↘（异步）
          │  ImageGenSubgraph          ru_overlay_llm ──► 13 组俄文 → draft.ru_overlay
          │  (10 节点)                              ↓
          │       ↓                          （不阻塞生图）
          │  每节点: assemble_prompt(slot, vars, preset)
          │       ↓
          │  call_mxou_image_api(prompt, negative_prompt)
          │       ↓
          │  save_image(task_id, slot, url)
          │
          └─► prepare → submit → fetch_back → learning_record
```

### 9.3 prompt_assembler 内部流程

```
输入: slot_key + visual_vars + color_preset
  ↓
1. 加载 config/image_slots_v5.json
  ↓
2. 取图位模板（如 "main"）→ Jinja2 渲染（注入 19 视觉变量 + 4 系统变量）
  ↓
3. 取后缀（A/B 按图位 text_policy 决定）→ Jinja2 渲染（注入 COLOR_PRESET 等）
  ↓
4. 取 negative_prompt（对应后缀的 negative）
  ↓
5. prompt = 渲染后图位模板 + " " + 渲染后后缀
   失败 → 回退 get_image_prompt(key, title=visual_vars["product"])
  ↓
输出: (prompt, negative_prompt)
```

---

## 10. 验收标准

### 10.1 功能验收

| 编号 | 验收项 | 验收方法 | 通过标准 |
|------|--------|---------|---------|
| AC-1 | visual_vars_llm 生成 19 变量 | 单元测试：mock draft 输入 | 输出含全部 19 key，值为非空英文 |
| AC-2 | color_preset_router 品类映射 | 13 类品类各测 1 例 | 映射结果符合 §5.2.2 规则 |
| AC-3 | ru_overlay_llm 生成 13 组俄文 | 单元测试 + 中文零容忍校验 | 全部西里尔字符，无中文 |
| AC-4 | prompt_assembler 组装 main 图位 | 集成测试：注入测试变量 | prompt 含全部变量值 + 后缀 A + negative 非空 |
| AC-5 | prompt_assembler 组装 white_bg | 集成测试 | prompt 含后缀 B + negative 含 "no text of any kind" |
| AC-6 | 现有 10 节点向后兼容 | visual_vars 为空时运行 | 回退旧版中文 prompt，不报错 |
| AC-7 | call_mxou_image_api 传 negative | 抓包检查 payload | payload 含 negative 字段或 prompt 尾部 ||NEG|| |
| AC-8 | 热加载生效 | 改 image_slots_v5.json 不重启 | 下一次生图用新模板 |
| AC-9 | 重跑不重烧 | 同 task_id 重跑 | 命中 task_image_cache，不调 API |

### 10.2 质量验收

| 编号 | 验收项 | 通过标准 |
|------|--------|---------|
| Q-1 | 出图与产品相关性 | 人工抽检 50 张，相关率 ≥85% |
| Q-2 | 无水印/文字/logo | Negative 生效，违规率 ≤5% |
| Q-3 | 同产品风格一致性 | 同产品 10 张图 COLOR_PRESET 一致，风格统一 |
| Q-4 | 俄文叠加数据可用 | ru_overlay 字段完整，可直接导入 PS 批量叠加 |
| Q-5 | 性能不退化 | 单产品生图总耗时增幅 ≤15%（visual_vars_llm 约 +10s） |

### 10.3 兼容性验收

| 编号 | 验收项 | 通过标准 |
|------|--------|---------|
| C-1 | 旧 envelope 不含 visual_vars | 自动走旧逻辑，生图正常 |
| C-2 | image_slots_v5.json 缺失 | 回退 image_prompts.json 旧模板 |
| C-3 | visual_vars_llm 失败 | 查品类速查表兜底，不阻断 |
| C-4 | ru_overlay_llm 失败 | 生图正常（异步），ru_overlay 为空 |

---

## 11. 配置文件清单

| 文件 | 用途 | 热加载 |
|------|------|--------|
| `config/image_slots_v5.json` | 10 图位模板 + 2 后缀（A/B） + negative prompt | ✅ |
| `config/color_presets.json` | 8 配色预设 + 品类映射规则 | ✅ |
| `config/category_visual_defaults.json` | 13 品类视觉变量兜底速查表 | ✅ |
| `config/image_prompts.json` | 旧版中文模板（兜底） | ✅ 保留 |
| `config/imagegen.json` | 节点模型路由（不变） | ✅ |

---

## 12. 实施计划（建议）

| 阶段 | 内容 | 优先级 |
|------|------|--------|
| Phase 1 | prompt_assembler + image_slots_v5.json + 现有 10 节点改造 | P0 |
| Phase 2 | visual_vars_llm_node + color_preset_router | P0 |
| Phase 3 | call_mxou_image_api 增加 negative_prompt | P1 |
| Phase 4 | ru_overlay_llm_node + draft.ru_overlay 持久化 | P1 |
| Phase 5 | 品类速查表 + 兜底逻辑完善 | P2 |

---

## 附录 A：10 图位 → 后缀 → 文字策略 映射表

| 图位 | 后缀 | 文字策略 | 必填变量 |
|------|------|---------|---------|
| main | A | 允许俄文 | PRODUCT, MODEL, ACTION, SCENE, LIGHTING, BACKGROUND, EFFECTS, TEXT_AREAS, COLOR_PRESET |
| white_bg | B | 禁止文字 | PRODUCT, COLOR, MATERIAL, APPEARANCE, SIZE |
| multi_angle | B | 禁止文字 | PRODUCT, COLOR, MATERIAL, APPEARANCE |
| scene_1 | A | 允许俄文 | MODEL, ACTION, SCENE, LIGHTING, ATMOSPHERE, COLOR_PRESET |
| scene_2 | A | 允许俄文 | PRODUCT, MODEL, ACTION, SCENE, BACKGROUND, LIGHTING, COLOR_PRESET |
| scene_3 | A | 允许俄文 | PRODUCT, MODEL, ACTION, BACKGROUND, LIGHTING, COLOR_PRESET |
| comparison | A | 允许俄文 | PROBLEM_SCENE, PRODUCT, COMPARISON, MODEL, COLOR_PRESET, BRAND_PRIMARY, ACCENT |
| detail | A | 允许俄文 | PRODUCT, MATERIAL, COLOR, APPEARANCE, BACKGROUND, LIGHTING, COLOR_PRESET |
| social_proof | A | 允许俄文 | MODEL, PRODUCT, BACKGROUND, LIGHTING, COLOR_PRESET, ACCENT |
| variant_white_bg | B | 禁止文字 | PRODUCT, COLOR, APPEARANCE |

## 附录 B：8 配色预设色值表

| COLOR_PRESET | 色值组 | 适用品类 |
|-------------|--------|---------|
| TOY_KIDS | #22C55E / #F97316 / #FFFFFF | 儿童玩具、亲子 |
| PET_FUN | #3B82F6 / #EC4899 / #A855F7 / #F97316 | 宠物玩具 |
| INDUSTRIAL | #000000 / #DC2626 / #F59E0B | 工业、清洁剂 |
| GARDEN | #16A34A / #A16207 / #92400E / #FEF3C7 | 园艺、户外 |
| TECH_BLUE | #1E40AF / #06B6D4 / #FFFFFF / #0F172A | 电子、科技 |
| BEAUTY_PINK | #FBCFE8 / #1E3A5F / #F59E0B | 美容、护肤 |
| WARM_SLEEP | #1E293B / #F59E0B / #FCD34D | 卧室、睡眠 |
| HOME_LIFESTYLE | #A16207 / #FED7AA / #1E40AF | 家居、家纺 |
