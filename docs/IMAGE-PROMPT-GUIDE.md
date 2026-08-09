# 生图模板占位符说明（v0.32）

> 维护者参考文档。当前模板：`worker/config/image_prompts.json`（热加载，改文件即生效）。
> 视觉变量来源：`worker/src/utils/prompt_assembler.py`（确定性 extract）+ `visual_vars_llm_node`（LLM 19 变量）。

## 当前全部占位符（10 个，v0.32 color 已移除）

### 数据来源占位符（4 个，来自信封 draft，确定性提取）

| 占位符 | 说明 | 数据来源 | 示例渲染 |
|---|---|---|---|
| `{{title}}` | 产品标题（清洗后）——prompt 首句，生图主体识别 | `draft.title` 经 `clean_title_for_image_prompt`（去平台词/营销词/标点）| 「产品：便携风扇 白色。」|
| `{{material}}` | 材质——影响产品质感表现 | `draft.attributes[材质/材料/material]` 经清洗（逗号串取首项 / 30 字符截断 / 空白清理，`_clean_attr_value`）| 「材质为ABS塑料」|
| `{{size}}` | 尺寸——影响构图比例感知 | `draft.dimensions`（length×width×height mm，缺任一维度为空）| 「尺寸99×25×72 mm」|
| `{{weight}}` | 重量——补充规格信息 | `draft.weight`（克）| 「重量67 г」|
| `{{category}}` | 类目——决定场景/风格倾向 | `draft.category` | 「属于风扇品类产品」|

### LLM 生成占位符（5 个，来自 visual_vars_llm，Jinja2 {% if %} 守卫）

| 占位符 | 说明 | 作用 | 缺失时 |
|---|---|---|---|
| `{{lighting}}` | 光线风格（英文值，如 "dramatic beauty lighting"）| 控制光影氛围，影响质感/高级感 | 整句省略 |
| `{{background}}` | 背景氛围（英文值）| 控制背景环境，影响场景感 | 整句省略 |
| `{{effects}}` | 特效/光效（英文值）| 控制光效粒子，用于 detail 特写 | 整句省略 |
| `{{atmosphere}}` | 整体气质（英文值，如 "premium and cozy"）| 控制视觉调性，同产品多图风格统一 | 整句省略 |

### 场景占位符（1 个，来自 scene_generation_llm）

| 占位符 | 说明 | 作用 | 缺失时 |
|---|---|---|---|
| `{{scene_context}}` | 场景描述（中文，如「家庭冰箱速冻」）| 仅 scene_1/2/3——产品在什么场景使用；三张场景图各有不同 scene_context（防同场景，F6）| 场景图退化为纯产品图 |

## 已移除的占位符（v0.32 决策）

| 占位符 | 移除原因 |
|---|---|
| `{{color}}` | 参考图已含真实颜色，prompt 写「主色调为X」反而误导生图（尤其 1688 多选逗号串脏值「黑色,白色,绿色」）|

## 各图位占位符组合 + 模型路由

| 图位 | 占位符组合 | 模型 |
|---|---|---|
| `main`（主图）| title + material + size + weight + category + lighting + background + atmosphere | **gpt-image-2** |
| `white_bg`（白底）| title + material + size + lighting | nano-banana-fast |
| `multi_angle`（多角度）| title + material + lighting | nano-banana-fast |
| `scene_1/2/3`（场景）| title + scene_context + category + material + atmosphere | nano-banana-fast |
| `comparison`（对比）| title + category + material + background + lighting | nano-banana-fast |
| `detail`（细节）| title + material + size + lighting + effects | nano-banana-fast |
| `social_proof`（好评）| title + category + atmosphere + background | **gpt-image-2** |
| `variant_white_bg`（变体白底）| title + material + size + lighting | nano-banana-fast |

模型路由配置：`worker/config/imagegen.json`（main/social_proof 用 gpt-image-2 质量优先；其余 8 槽位 nano-banana-fast 速度优先，banana 成功率高于 image2）。

## 关键设计（对抗团队定案）

1. **color 不注入**：参考图承担产品颜色（v0.32 用户决策）
2. **GIFT/INSET/ICONS 禁编造**：visual_vars_llm system prompt 明确「无赠品则空串，绝不虚构」（v0.32）
3. **属性值清洗**：`_clean_attr_value` 逗号串取首项 + 30 字符截断（实测「X13桌面迷你风扇-黑色,...」多选串污染）
4. **LLM 变量英文值 + 中文模板句**：v0.13 英文 prompt 实验失败已回退（AGENTS.md 明令「勿改回英文版」）
5. **场景差异化**：scene_1/2/3 各传独立 scene_context + slot_scene_context（防三图同场景）
6. **变量缺失容错**：无占位符的 extra 变量静默忽略；LLM 失败回退确定性 extract + 品类默认；绝不阻断生图
7. **数据流第三路线**：节点内即时计算（`merge_visual_vars`），零状态写入，规避 schema 扩展 + 竞态 + 契约污染
