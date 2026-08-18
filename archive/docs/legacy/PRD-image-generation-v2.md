# PRD: v2 商品图片生成策略

> 基于 Ozon 平台规则 + GitHub 社区最佳实践 + gpt-image-2 模型特性

---

## 1. 背景和问题

### 1.1 当前问题
- MXOU 生图含 `кэшбэк`、`розыгрыш`、配送信息、链接等促销文字，导致 Ozon 拒绝（attr 4195 DESCRIPTION_DECLINE）
- Prompt 一刀切"禁止任何文字"，但电商图完全没有文字反而不自然
- 各节点 prompt 不统一，有的俄语有的中文有的英文
- 参考图直接传 1688 原图（含水印、中文标签），影响生图质量

### 1.2 调研结论
| 来源 | 关键发现 |
|------|---------|
| Ozon 官方 | 允许信息图，主图带信息图+0.95% 转化率；拒绝水印/促销文字/非俄语/联系方式 |
| GitHub ecom-details-image | 25 场景 JSON 模板，统一 prompt 结构：主体→场景→灯光→构图→约束 |
| gpt-image-2 特性 | 文字渲染 99% 准确，支持参考图（最多7张），英文 prompt 最佳 |
| 俄罗斯电商社区 | 9 图标准结构，场景图提前激发购买欲，信息图建立信任 |

---

## 2. 总体策略

### 2.1 Prompt 语言：统一英文
- 英文 prompt 模型理解最稳定
- 模型会根据 "Russian marketplace" 上下文自动生成俄语文字和俄罗斯风格场景
- 不硬性禁止俄语文字（Ozon 允许），只禁止促销/联系方式类文字

### 2.2 参考图策略
```
1688原图 → white_bg（只传这张：纯净白底，无中文/水印干扰）
white_bg → 主图/场景/详情/社证/多角度/对比（全部以白底图为唯一定妆照）
```
好处：产品外观一致，杜绝 1688 水印和中文标签传递到下游生图。

### 2.3 统一约束尾巴
每张图的 prompt 末尾追加：
```
Do NOT include: watermarks, logos, prices, discounts, phone numbers,
email, website URLs, QR codes, promotional badges.
```

### 2.4 [product] 变量
传入 `clean_title_for_image_prompt()` 清洗后的标题。该函数已过滤：
- 80+ 平台名（Amazon/Temu/Shopee 等）
- 营销黑话（Best/Super/Hot）
- 跨境套话（cross-border/wholesale/dropshipping）
- 电商水词（2024/New/Free shipping）

---

## 3. 每张图的 Prompt

### 3.1 主图 (main_image)
**目的**：搜索结果中第一眼看到，决定点击率
**背景**：纯色干净，非纯白（跟 white_bg 区分）
**文字**：允许俄语关键卖点标注（模型自己决定标注什么）
```
Professional e-commerce hero shot of [product]. Best showcase angle,
clean solid background, studio lighting, sharp focus.
Russian marketplace main image standard.
```

### 3.2-3.4 场景图 ×3 (scene_1, scene_2, scene_3)
**目的**：让买家想象产品在生活中的样子，激发购买欲
**背景**：LLM 根据产品特征自动生成场景描述，prompt 只定方向框架
**文字**：允许自然的产品名/俄语语境标签
```
Lifestyle photograph of [product] in a natural usage setting.
[LLM-generated scene description]. Authentic natural lighting,
real-life feel, matching Russian consumer daily aesthetics.
Russian marketplace lifestyle image standard.
```

### 3.5 详情图 (detail)
**目的**：展示材质纹理和做工品质，建立信任
**背景**：模糊背景，聚焦产品局部
**文字**：不允许
```
Macro close-up of [product]. Focus on material texture and build quality.
Side lighting to reveal surface details, shallow depth of field.
Russian marketplace detail image standard.
```

### 3.6 对比图 (comparison)
**目的**：直观展示为什么选这个产品
**文字**：允许俄语简短标注
```
Visual comparison: [product] versus a generic alternative. Highlight key
advantages. Clean layout, minimal text labels acceptable.
Russian marketplace infographic style.
```

### 3.7 社交证明 (social_proof)
**目的**：模拟真实用户认可，降低决策疑虑
**文字**：允许自然的用户评价风格文字，不允许价格/链接
```
Smartphone snapshot of [product] in real use. Natural indoor lighting,
slightly grainy, warm tone, candid unposed feel. Russian UGC style.
```

### 3.8 多角度 (multi_angle)
**目的**：让买家看清各个面
**背景**：纯白统一
**文字**：不允许
```
[product] shown from front, side, and back angles. Clean white
background, consistent lighting. Russian marketplace images standard.
```

### 3.9 白底图 (white_bg)
**目的**：平台合规要求的纯白底图
**背景**：#FFFFFF 纯白，无阴影
**文字**：不允许
**参考**：1688 原图（唯一传原图的节点）
```
Professional packshot of [product] on pure white background #FFFFFF.
Front view, even diffused lighting, product occupying 85%+ of frame.
Russian marketplace standard packshot.
```

---

## 4. 图片顺序

```
现状: 主图 → 详情 → 场景1 → 场景2 → 场景3 → 对比 → 社证 → 多角度 → 白底

优化: 主图 → 场景1 → 场景2 → 详情 → 对比 → 社证 → 场景3 → 多角度 → 白底
```

| 位置 | 用户心理 | 图片 | 作用 |
|------|---------|------|------|
| 1 | 看到 | 主图 | 抓住眼球，搜索结果点击 |
| 2-3 | 想要 | 场景1、场景2 | 想象拥有产品的生活 |
| 4-6 | 信任 | 详情、对比、社证 | 品质证据+决策信心 |
| 7-9 | 确认 | 场景3、多角度、白底 | 补充视角+平台合规 |

---

## 5. 实施计划

### 5.1 修改文件

| 文件 | 改动 |
|------|------|
| `utils/image_gen_factory.py` `make_prompt()` | 改英文 prompt + 统一约束 |
| `utils/image_gen_factory.py` `build_phase1_refs()` | white_bg 参考 1688 原图 |
| `utils/image_gen_factory.py` `build_phase2_refs()` | 主图/场景/详情/社证/多角度/对比 参考 white_bg |
| `nodes/main_image_gen_node.py` | prompt 改英文 |
| `nodes/white_bg_gen_node.py` | prompt 改英文 |
| `nodes/detail_gen_node.py` | prompt 改英文 |
| `nodes/social_proof_gen_node.py` | prompt 改英文 |
| `nodes/comparison_gen_node.py` | prompt 改英文 |
| `nodes/multi_angle_gen_node.py` | prompt 改英文 |
| `nodes/scene_1/2/3_gen_node.py` | 走 factory make_prompt，无需改动 |
| `nodes/prepare_ozon_upload_node.py` | IMG_ORDER 调整 |

**不改的**：
- `scene_generation_llm_node.py` — LLM 生成场景描述的逻辑不变
- 图节点调用结构 — 只改 prompt 字符串和参考图逻辑

### 5.2 验证方式
1. 提交一个产品，获取生图 URL
2. 肉眼检查图片是否：无水印/无促销文字/有俄语自然标注/场景符合俄罗斯审美
3. 提交到 Ozon 确认不被 attr 4195 拒绝

### 5.3 预期效果
- 消除因生图带促销文字导致的 DESCRIPTION_DECLINE（4/7 个 declined 产品由此造成）
- 产品外观一致性提升（统一以白底图为参考源）
- Prompt 统一为英文，维护性提升

---

## 6. 附录：Ozon 图片规则速查

| ✅ 允许 | ❌ 禁止 |
|---------|--------|
| 俄语信息图（1-2张附加图） | 水印、logo 超 5% 面积 |
| 品牌 logo ≤5% 面积 | 价格、折扣、"bestseller" |
| 产品本身认证标签 | 电话、邮箱、链接、二维码 |
| 俄语功能标注 | 非俄语/非拉丁字符 |
| AI 生成图 | 虚假 UI 按钮/播放键 |
| 手机拍照风格图 | 黑白照片、模糊低质 |
