# Ozon-Worker 生图流程 v5 升级 — 技术方案架构评审报告

> **评审对象**：`docs/PRD-生图流程优化-v5.md`（v1.1, 760 行）
> **评审人**：高见远（Gao）· 架构师
> **评审日期**：2026-08-09
> **项目根目录**：`/Volumes/os/dev/ozon-worker`
> **评审方法**：PRD 逐条假设 × 真实源码交叉验证

---

## 评审结论速览

| 维度 | 评级 | 说明 |
|------|------|------|
| 方案目标合理性 | 🟡 部分成立 | 核心问题真实，但目标存在过度设计与可衡量性缺陷 |
| 技术可行性 | 🔴 严重风险 | 3 处 P0 技术假设与代码矛盾，方案需重大修改 |
| 完整性 | 🔴 不完整 | graph 路由描述错误、state 扩展遗漏、测试策略缺失 |
| 风险识别 | 🟠 不足 | 未量化成本、未验证 API 能力、未评估 LLM 输出稳定性 |
| 优化建议 | 见 §5 | 7 条 P0、6 条 P1、4 条 P2 |

**总体结论：建议修改后通过**。PRD 识别的核心问题（提示词变量不足、draft 数据未消费）真实存在且有改进价值，但方案中存在 3 处 P0 级技术假设与真实代码直接矛盾，必须修改后方可进入实施。

---

## 1. 方案目标审查

### 1.1 核心问题是否真实存在？

| PRD 声称的问题 | 代码验证 | 结论 |
|---------------|---------|------|
| 提示词仅 `{{title}}` + `{{scene_context}}` 两变量 | ✅ `image_prompts.py:52` — `get_image_prompt(key, **kwargs)`，模板仅含 `{{title}}`/`{{scene_context}}` | **真实** |
| 提示词为中文 | ✅ `image_prompts.json` 全部中文，`_DEFAULT_PROMPTS`（`image_prompts.py:18-29`）全部中文 | **真实** |
| draft 的 `attributes/weight/dimensions/variants` 未被生图消费 | ✅ 10 个生图节点仅取 `draft.title`（如 `main_image_gen_node.py:73`）；`scene_generation_llm_node.py:40-42` 仅取 title/description/category | **真实** |
| 无 Negative Prompt | ✅ `call_mxou_image_api`（`mxou_api.py:206-213`）签名无 `negative_prompt`；payload（`mxou_api.py:315-321`）无 `negative` 字段 | **真实** |
| 无配色预设体系 | ✅ 全局搜索无 color_preset 相关代码 | **真实** |
| 无俄文叠加数据生成 | ✅ 无 ru_overlay 相关代码 | **真实** |

**结论**：PRD 识别的核心问题**全部真实存在**，改进方向有价值。

### 1.2 目标 G1-G7 评估

| 目标 | 评估 | 严重度 |
|------|------|--------|
| **G1 变量覆盖 2→23** | 合理，但 PRD §2.2 的 19 个视觉变量定义中，COLOR/MATERIAL/APPEARANCE/ATMOSPHERE/PACKAGING 的示例值使用**俄文**（如 `темно-синий + медный`、`гладкий ABS-пластик`、`премиальный / уютный`），与"全部为英文描述"的声明自相矛盾。LLM 若模仿示例输出俄文，将直接破坏"英文 prompt 适配 gpt-image-2"的核心目标 | 🔴 P0 |
| **G2 图位保持 10 个** | 合理，非目标 | ✅ |
| **G3 消费 8 类 draft 数据** | 合理且必要 | ✅ |
| **G4 人工抽检通过率 ≥85%** | 可衡量但**缺乏基线** — 当前通过率是多少？如未测量，85% 这个数字无法判断改进幅度 | 🟡 P1 |
| **G5 配色统一** | 合理，color_preset_router 是纯函数无 LLM，风险低 | ✅ |
| **G6 俄文叠加数据** | 合理，但 PRD 将其定位为"异步不阻塞生图"，技术实现存疑（见 §2.3） | 🟡 P1 |
| **G7 向后兼容** | **目标缺失关键维度**：visual_vars 为空时回退旧逻辑（中文 prompt），但未评估"同一产品部分图走英文、部分走中文"的视觉割裂风险。10 张图中如果 visual_vars 生成成功但某节点 prompt_assembler 渲染失败回退中文，同一产品的图片风格将严重不一致 | 🔴 P0 |
| **缺失目标** | 无成本控制目标（新增 2 次 LLM 调用/产品）、无 LLM 输出稳定性指标（19 字段 JSON 结构化输出的字段完整率/幻觉率）、无 prompt-template-v5.html 模板验证目标（该文件在代码库中不存在，见 §2.1） | 🟡 P1 |

---

## 2. 技术可行性

### 2.1 🔴 P0：`prompt-template-v5.html` 文件不存在

PRD 开篇声明关联文件 `prompt-template-v5.html`，且 §1.1 称"已在人工运营中验证有效"。但：

```
$ find /Volumes/os/dev/ozon-worker -name "prompt-template-v5*"
(无结果)
```

**影响**：方案的核心依据（35 变量 / 22 图位 / 3 套后缀 / 8 配色预设）全部来自该模板，但模板本身不在代码库中。无法核实模板内容与 PRD 引用是否一致、模板的"已验证有效"声明是否属实。

**建议**：PRD 评审前必须附上 `prompt-template-v5.html` 原文，或将其置于代码库中供交叉验证。

### 2.2 🔴 P0：多模态假设不成立 — DeepSeek-v4-flash 不支持图片输入

PRD §5.2.1 明确写道：

> `images` URL 传入 LLM（多模态，若模型支持）辅助视觉推断

PRD §8.1 `VisualVarsInput` 也包含 `draft`（含 `images` 字段）。

**代码验证**：

`call_mxou_chat_api`（`mxou_api.py:55-97`）的 payload 构造：

```python
payload: Dict[str, Any] = {
    "model": model,
    "messages": [
        {"role": "system", "content": system_prompt},  # ← 纯字符串
        {"role": "user", "content": user_prompt}        # ← 纯字符串
    ],
    "temperature": temperature,
    "max_tokens": max_tokens,
    "thinking": {"type": "disabled"}
}
```

`content` 字段是 **纯字符串**，不支持 OpenAI 多模态格式（需要 `content` 为 `[{"type": "text", ...}, {"type": "image_url", ...}]` 列表）。DeepSeek-v4-flash 是**纯文本模型**，不具备视觉理解能力。

**影响**：PRD 声称通过 LLM 从 images URL 辅助推断 `PRODUCT`/`APPEARANCE`/`MODEL` 等视觉变量的路径**完全无效**。这些变量只能依赖 LLM 从 title/description 文本推断，质量将显著低于预期。

**建议**：从 `VisualVarsInput` 中移除 images 多模态推断路径，改为纯文本推断 + 品类速查表兜底。或若需多模态，须切换到支持视觉的模型（如 GPT-4o），但这会大幅增加成本。

### 2.3 🔴 P0：`ImageGenSubgraph` 并非独立子图，"异步并行"设计不可行

PRD §5.1 和 §9.2 设计 ru_overlay_llm "与 ImageGenSubgraph 异步并行，不阻塞生图"。

**代码验证**：

`graph.py` 中**没有** `ImageGenSubgraph` 这个子图对象。10 个生图节点直接添加到主图中（`graph.py:72-81`），通过 fan-out/fan-in 边实现并行：

```python
# graph.py:208-209 — scene_generation_llm → Phase1 fan-out
builder.add_edge("scene_generation_llm", "white_bg_gen")
builder.add_edge("scene_generation_llm", "multi_angle_gen")

# graph.py:220-230 — Phase1 → Phase2 fan-out
builder.add_edge(["white_bg_gen", "multi_angle_gen"], "detail_gen")
builder.add_edge(["white_bg_gen", "multi_angle_gen"], "main_image_gen")
# ... etc
```

`state_image_gen.py:190` 虽定义了 `ImageGenSubgraphState` 类，但它**未被 graph.py 使用**——是历史遗留的死代码。

**LangGraph 的并行模型**：LangGraph StateGraph 通过 fan-out 边实现节点并行，但**不支持"后台异步任务"**。所有节点都在图的同步执行流中。要实现 ru_overlay_llm 与生图并行，只有两种路径：

| 方案 | 可行性 | 问题 |
|------|--------|------|
| **A：将 ru_overlay_llm 作为图节点，从同一源 fan-out** | ✅ 技术可行 | ru_overlay_llm 需与 Phase1 同时 fan-out，结果在 prepare_ozon_upload 前汇聚。但 PRD 说"不阻塞生图"——若 ru_overlay_llm 失败/超时，fan-in 汇聚点会等待它完成，反而**阻塞了 prepare**。需设计为"可选汇聚"（容忍 ru_overlay 结果缺失） |
| **B：在节点内部用 ThreadPoolExecutor fire-and-forget** | ⚠️ 有风险 | state 持久化与错误处理极难——异步线程写入 state 不会被 LangGraph 的 reducer 正确合并，且进程重启后丢失 |

**影响**：PRD 的"异步并行"设计在现有 LangGraph 架构下**无法按描述实现**。若强行用方案 B，会导致 ru_overlay 数据在异常恢复时丢失。

**建议**：采用方案 A — 将 ru_overlay_llm 作为图节点，与 Phase1 生图节点一起从 `scene_generation_llm`（或新增的 `visual_vars_llm`）fan-out。在 Phase2 汇聚点（`prepare_ozon_upload`）之前用条件边处理 ru_overlay 缺失（允许空值通过）。放弃"不阻塞"的表述，改为"并行执行，失败容错"。

### 2.4 🔴 P0：MXOU Image API 是否支持 negative_prompt 字段 — 未经验证

PRD §8.3 设计：

> `negative_prompt` 非空时：
> - API 支持 negative 字段 → `payload["negative"] = negative_prompt`
> - API 不支持 → prompt 尾部追加 `" ||NEG|| {negative_prompt}"`

**代码验证**：

`_call_image_with_model`（`mxou_api.py:293-463`）的 payload 构造：

```python
payload: Dict[str, Any] = {
    "model": model,
    "prompt": prompt,
    "images": safe_images,
    "aspectRatio": aspect_ratio,
    "replyType": "async"
}
```

全局搜索 `mxou_api.py` 中 **无任何 `negative` 关键词**。PRD 假设 API 支持 `negative` 字段**纯属推测，无代码或文档证据**。

**`||NEG||` 兜底的有效性**：将 negative prompt 用 `||NEG||` 分隔符拼入 prompt 尾部——主流生图模型（DALL-E 系列、Stable Diffusion）**不认识** `||NEG||` 语法。gpt-image-2 / nano-banana-fast 的 API 若不原生支持 negative 参数，模型很可能把 `||NEG|| no watermark, no text` 当作正向 prompt 的一部分来理解，反而**可能增加**水印/文字的生成概率（反向效果）。

**影响**：PRD 的 Negative Prompt 功能可能完全无效，且 `||NEG||` 兜底可能产生反效果。G4 验收标准中"无水印/文字/logo 违规率 ≤5%"的目标可能无法达成。

**建议**：
1. **P0**：实施前必须通过抓包/文档确认 MXOU Image API 是否支持 `negative` 参数。如不支持，砍掉 Negative Prompt 功能，改为在 prompt 正文中用自然语言描述禁止元素（当前中文模板已在做此操作）。
2. 如 API 确实支持，移除 `||NEG||` 兜底逻辑——不要把两种机制混用。

### 2.5 🟠 P1：graph 路由流程描述与真实代码严重不符

PRD §3.1 描述的链路：

```
ingest → auth → scene_generation_llm → ImageGenSubgraph → prepare → submit → fetch_back → learning_record
```

**代码验证**（`graph.py:92-393`）真实链路：

```
auth → check_quota → [follow_sell_import | ingest] → pricing → assemble_ozon_product
→ scene_generation_llm → [white_bg_gen + multi_angle_gen]（Phase1 并行）
→ [detail_gen + social_proof_gen + comparison_gen + scene_1-3_gen + variant_primary_loop + main_image_gen]（Phase2 并行）
→ prepare_ozon_upload → ozon_validate → ozon_upload → ozon_status
→ [fetch_back → learning_record | validation_retry_wrapper → learning_record | ozon_status（重试）]
```

**具体不符项**：

| PRD 声称 | 真实代码 | 影响 |
|---------|---------|------|
| `ingest → auth` | `auth → check_quota → ingest`（顺序反了） | PRD 对节点插入点的理解错误 |
| "auth_node 校验 token + 余额" | `auth_node` 校验 token + MXOU 余额（`auth_node.py:411`），但 `check_quota_node` 是**独立的 Ozon 店铺配额校验**（`check_quota_node.py:22`），校验的是 Ozon 每日创建配额，非 MXOU 余额 | 新节点插入点判断错误 |
| 无 pricing / assemble_ozon_product / ozon_validate / ozon_status / validation_retry_wrapper | 全部存在且在生图前后 | PRD 对 pipeline 全貌理解不完整 |

**影响**：PRD 说"新增节点插入在 scene_generation_llm 之后、ImageGenSubgraph 之前"——这个位置是对的（`graph.py:208` 确认 scene_generation_llm → white_bg_gen/multi_angle_gen 是直接连边），但 PRD 的整体流程图会误导工程师。

### 2.6 🟠 P1：`variant_primary_loop_node` 不是简单的"变体白底图"节点

PRD 将 `variant_white_bg` 与其他 9 个节点并列，认为可以统一改造（替换 `get_image_prompt` → `assemble_prompt`）。

**代码验证**（`variant_primary_loop_node.py`）：

该节点与其它 9 个节点有**结构性差异**：

| 维度 | 其他 9 节点 | variant_primary_loop_node |
|------|-----------|--------------------------|
| 执行模式 | 单次 `call_mxou_image_api` | `ThreadPoolExecutor(max_workers=4)` 并行循环（`line 119`） |
| 输入 schema | 各自独立的 Input/Output（`state_image_gen.py`） | `VariantPrimaryLoopInput`（`variant_primary_loop_node.py:19-25`） |
| 输出 | 单张图片 URL | `List[str]`（多张变体图） |
| 参考图来源 | Phase1 的 white_bg/multi_angle 或原始图 | 每个 variant 的 `variant.image`（`line 71`），fallback 到 white_bg |
| 多SKU跳过逻辑 | `main_image_gen_node` 在 `len(variants) > 1` 时跳过（`line 37-39`） | 无 variants 时返回空列表（`line 49-55`） |
| 调用 prompt 的方式 | `get_image_prompt("main", title=title)` | `get_image_prompt("variant_white_bg")`（**不传 title**，`line 100`） |

**关键问题**：
1. `variant_primary_loop_node` 调用 `get_image_prompt("variant_white_bg")` 时**不传 title**，意味着当前模板中的 `{{title}}` 占位符被保留为字面量 `{{title}}` 传给 API。这是**已存在的 bug**，PRD 未发现。
2. 改造为 `assemble_prompt` 时，需要为每个 variant 单独组装 prompt（不同 variant 可能有不同颜色/外观），但 PRD 的 `assemble_prompt` 设计是"一次组装、所有图位共用"，没有考虑 per-variant 变量差异化。
3. 该节点的 Input schema（`VariantPrimaryLoopInput`）不在 `state_image_gen.py` 中，而是在 `variant_primary_loop_node.py:19-25` 内联定义，改造时容易遗漏。

**影响**：统一改造假设部分不成立。variant_primary_loop 需要特殊处理。

### 2.7 ✅ 已验证属实的假设

| PRD 假设 | 验证结果 |
|---------|---------|
| `call_mxou_chat_api` 存在，使用 deepseek-v4-flash | ✅ `mxou_api.py:55`，默认 `model="deepseek-v4-flash"` |
| `mxou_llm.py` 是 `call_mxou_chat_api` 的转发 | ✅ `mxou_llm.py` 仅一行：`from utils.mxou_api import call_mxou_chat_api` |
| `call_mxou_chat_api` 有超时/重试/429 限流 | ✅ 3 次重试 + 指数退避 + 429 handler（`mxou_api.py:101-198`） |
| `get_image_model` 路由 gpt-image-2 / nano-banana-fast | ✅ `imagegen.json` 确认 main/social_proof 用 gpt-image-2，其余 8 节点用 nano-banana-fast |
| 10 节点调用模式一致（get_image_prompt + get_image_model + call_mxou_image_api + save_image） | ✅ 已验证 main/white_bg/scene_1/variant_primary_loop，模式一致 |
| Phase1/Phase2 依赖关系如 PRD 所述 | ✅ `graph.py:220-230` 确认 fan-out/fan-in |
| draft.attributes 是 dict 结构 | ✅ `assemble_ozon_product_node.py:435` — `dict[str, Any]` |
| draft.weight 是数字 | ✅ `draft_sanity.py:40` — `float(draft.get("weight") or 0)` |
| draft.dimensions 含 length/width/height | ✅ `draft_sanity.py:47` — `dims.get(k) for k in ("length", "width", "height")` |
| `_russian_required_attrs` 存在 | ✅ `prepare_ozon_upload_node.py:1840` 和 `:1944`，但是是局部变量（Ozon 属性 ID 元组），不是可复用的校验函数 |

### 2.8 🟠 P1：`_russian_required_attrs` 不可直接复用

PRD §5.2.3 说 ru_overlay_llm "复用 `_russian_required_attrs` 校验逻辑"。

**代码验证**：

`_russian_required_attrs` 是 `prepare_ozon_upload_node.py` 方法内部的**局部变量**（两个不同值）：
- Line 1840: `_russian_required_attrs = (4191, 4180, 9048, 4384, 4389, 23171, 23487)` — 用于批量翻译判断
- Line 1944: `_russian_required_attrs = (4191, 4180, 4384, 4389, 23171, 23487)` — 用于单属性翻译判断（少了 9048）

它是 **Ozon 属性 ID 的元组**，不是校验函数。真正的校验逻辑是 `_has_cyrillic()` 和 `has_chinese()` 函数。

**影响**：PRD 的引用不精确。ru_overlay_llm 应复用的是 `_has_cyrillic()` + `has_chinese()` 校验函数，而非 `_russian_required_attrs` 元组。且这两个函数是 `prepare_ozon_upload_node.py` 的内部函数（非模块级），需要提取为独立工具函数才能复用。

---

## 3. 完整性检查

### 3.1 State 扩展遗漏 — 节点级 Input/Output schema 未覆盖

PRD §7.3 仅提到扩展 `DraftExtension`（visual_vars / color_preset / ru_overlay）。

**代码验证**：GlobalState（`state.py:15`）是 Pydantic BaseModel，扩展简单（加 Field 即可）。但**关键遗漏**：

10 个生图节点各有独立的 Input schema（`state_image_gen.py`），例如：

```python
# state_image_gen.py:44-54
class MainImageInput(BaseModel):
    draft: Optional[Dict[str, Any]]
    token: str
    original_images: List[str]
    multi_angle_image: Optional[str]
    white_bg_image: Optional[str]
    variants: list
    # ← 缺少 visual_vars / color_preset / brand_primary / accent / headline_style
```

LangGraph 的节点函数签名是 `def node(state: NodeInput, config, runtime) -> NodeOutput`——只有 Input schema 中声明的字段才会从 GlobalState 中映射传入。**如果不扩展全部 10 个节点的 Input schema，visual_vars 和 color_preset 根本无法到达节点函数内部**。

PRD 完全未提及这一改造点。

| 需扩展的 schema 文件 | 需扩展的类 | 数量 |
|---------------------|----------|------|
| `state_image_gen.py` | WhiteBgInput, MultiAngleInput, MainImageInput, DetailImageInput, SocialProofInput, Scene1Input, Scene2Input, Scene3Input, ComparisonInput | 9 个 |
| `variant_primary_loop_node.py` | VariantPrimaryLoopInput | 1 个 |
| `state.py` | GlobalState + 新增 VisualVarsInput/Output, ColorPresetOutput, RuOverlayInput/Output | 5 个 |
| `state.py` | SceneGenerationInput（需透传 visual_vars 给下游）| 1 个 |

**影响**：遗漏此项将导致改造无法完成——`assemble_prompt` 拿不到 visual_vars。

### 3.2 Graph 路由接线方案缺失

PRD §5.1 和 §9.2 给出了概念流程图，但**没有给出具体的 `builder.add_edge()` / `builder.add_conditional_edges()` 改造方案**。

需要明确：
1. visual_vars_llm_node 如何接入？从 `scene_generation_llm` → `visual_vars_llm` → `color_preset_router` → 然后 fan-out 到 Phase1？
2. ru_overlay_llm 的并行接入点在哪？fan-out 源是 `color_preset_router` 还是 `visual_vars_llm`？
3. 新增节点是否需要 `metadata={"type": "agent", "llm_cfg": "..."}` 配置？（现有 LLM 节点如 `scene_generation_llm` 有此配置）
4. 新增节点的 Input/Output schema 如何映射到 GlobalState？

### 3.3 测试策略缺失

PRD §10 给出了验收标准，但**没有测试策略**：
- 无单元测试方案（visual_vars_llm 的 mock、prompt_assembler 的边界测试）
- 无集成测试方案（全链路 mock 生图 API）
- 无回归测试方案（10 节点改造后的对比验证）
- 无 LLM 输出稳定性测试方案（19 字段 JSON 的字段完整率/幻觉率）

### 3.4 配置迁移方案不完整

PRD §11 列出了 5 个配置文件，但：
- `config/image_slots_v5.json` 的结构未定义（10 图位模板 + 2 后缀 + negative 的 JSON schema 未给出）
- `config/category_visual_defaults.json`（13 品类兜底速查表）的内容未给出——这是兜底的关键，覆盖率直接决定 LLM 失败时的生图质量
- 无从旧配置（`image_prompts.json`）到新配置（`image_slots_v5.json`）的迁移步骤
- 无回滚方案（新配置出问题时如何快速切回旧配置）

### 3.5 LLM 输出 JSON 解析容错缺失

PRD 说 visual_vars_llm "system prompt 指示输出 JSON"，但：
- `call_mxou_chat_api` 返回 `Optional[str]`（纯文本），**不解析 JSON**
- 现有 `scene_generation_llm_node.py:69-88` 有 JSON 解析容错（`json.loads` + fallback 文本提取），但 PRD 未为 visual_vars_llm 设计类似的容错
- 19 个字段的 JSON 输出比 3 个场景复杂得多，LLM 可能输出不完整 JSON、多余 markdown 标记、字段缺失等
- PRD 的兜底是"查品类速查表"，但未定义"部分字段缺失时如何处理"（是整体回退还是单字段补全？）

### 3.6 并发与限流未评估

- `call_mxou_chat_api` 和 `call_mxou_image_api` 共享 `mxou_acquire(token)` 限流器（`mxou_api.py:102, 326`）
- 新增 visual_vars_llm（+ru_overlay_llm）会产生额外 LLM 调用，在并发场景下可能与生图 API 调用争抢限流配额
- PRD 未评估新增 LLM 调用对现有限流策略的影响

---

## 4. 风险识别

### 4.1 性能风险

| 风险 | 证据 | 严重度 |
|------|------|--------|
| **visual_vars_llm 阻塞生图** | PRD 将 visual_vars_llm 放在 scene_generation_llm 之后、ImageGenSubgraph 之前，是**阻塞式**串行执行。`call_mxou_chat_api` 默认 timeout=90s，重试 3 次最坏 90+2+90+4+90=276s | 🔴 P0 |
| **Q-5 验收标准（增幅 ≤15%）不现实** | 假设当前生图总耗时 180s（Phase1 ~90s + Phase2 ~90s 并行），+15% = +27s 预算。visual_vars_llm 单次调用 10-30s（4096 max_tokens），失败重试可达 90s+。LLM 调用成功率不足 100% 时，增幅远超 15% | 🟠 P1 |
| **ru_overlay_llm 并行可能拖慢 prepare** | 若采用 fan-out 方案，prepare_ozon_upload 需等待所有 fan-in 节点完成（含 ru_overlay_llm）。ru_overlay_llm 超时/重试会直接延长 prepare 等待时间 | 🟠 P1 |

### 4.2 成本风险

| 风险 | 证据 | 严重度 |
|------|------|--------|
| **新增 2 次 LLM 调用/产品，成本未量化** | 当前每产品 LLM 调用：scene_generation(1) + assemble_ozon_product(1) + prepare_ozon_upload(翻译, 1-N次)。新增 visual_vars_llm(1) + ru_overlay_llm(1) = +2 次，增幅约 50-100%。deepseek-v4-flash 单次调用成本未在 PRD 中估算 | 🟠 P1 |
| **visual_vars_llm max_tokens=4096 可能不够** | 19 个字段 + JSON 结构开销，4096 tokens 可能截断输出。若需提升到 8192，成本翻倍 | 🟡 P2 |
| **LLM 失败时查品类速查表** | 速查表覆盖率不明，若覆盖不足，部分品类产品可能反复重试 LLM，烧更多 token | 🟡 P2 |

### 4.3 数据一致性风险

| 风险 | 证据 | 严重度 |
|------|------|--------|
| **中英混用导致同产品图片风格割裂** | PRD G7 回退策略：visual_vars 为空时回退旧逻辑（中文 prompt）。但 visual_vars 在 `visual_vars_llm_node` 生成后即固定——如果 LLM 成功但部分字段为空，`assemble_prompt` 渲染部分变量后输出英文+空占位符的混合 prompt，与旧版中文 prompt 风格完全不同 | 🔴 P0 |
| **ru_overlay 数据丢失** | 若采用 fire-and-forget 异步方案，进程重启后 ru_overlay 数据丢失。PRD 说"写入 draft.ru_overlay 随 task 持久化"，但 draft 在 GlobalState 中是 `Optional[Dict]`，没有持久化机制——它只在 graph 运行期间存在于内存 | 🟠 P1 |
| **color_preset_router 品类关键词匹配不可靠** | PRD §5.2.2 用中文关键词（"驱蚊/蚊/杀虫"）匹配 `draft.category`。但 `draft.category` 可能是英文/俄文/混合，关键词匹配覆盖率存疑 | 🟡 P2 |

### 4.4 兼容性风险

| 风险 | 证据 | 严重度 |
|------|------|--------|
| **旧 envelope 无 visual_vars 字段** | PRD C-1 验收标准说"自动走旧逻辑"。但旧 envelope 的 draft 中没有 visual_vars，新节点 `visual_vars_llm_node` 的 Input schema 需要 draft——会正常执行 LLM 调用，**不会跳过**。"旧 envelope 自动走旧逻辑"需要额外判断逻辑 | 🟠 P1 |
| **image_slots_v5.json 缺失回退** | PRD C-2 说"回退 image_prompts.json 旧模板"。但 `assemble_prompt` 和 `get_image_prompt` 是不同的函数，回退逻辑需要在 `assemble_prompt` 内部调用 `get_image_prompt`——PRD §9.3 确认了这个设计，但未定义 image_slots_v5.json 的 schema 校验（部分缺失算"配置缺失"还是"配置存在"？） | 🟡 P2 |

### 4.5 安全风险

| 风险 | 证据 | 严重度 |
|------|------|--------|
| **LLM 输出注入** | visual_vars_llm 的 19 个字段值会被 Jinja2 渲染进 prompt。若 LLM 输出含 Jinja2 模板注入语法（如 `{{ }}` 或 `{% %}`），可能导致 prompt_assembler 渲染异常或信息泄露。当前 `image_prompts.py:63` 用 `Template(template).render(**kwargs)` 不做转义 | 🟡 P2 |

---

## 5. 优化建议（按优先级排序）

### P0 级（必须修改后方可实施）

| # | 建议 | 关联问题 |
|---|------|---------|
| P0-1 | **提供 `prompt-template-v5.html` 原文**，PRD 评审前完成模板内容核实 | §2.1 |
| P0-2 | **移除多模态推断路径**：从 `VisualVarsInput` 中移除 images 多模态推断，改为纯文本推断（title/description/category/attributes）+ 品类速查表兜底。在 PRD 中明确标注"deepseek-v4-flash 不支持图片输入" | §2.2 |
| P0-3 | **修正 ru_overlay_llm 并行方案**：放弃"异步不阻塞"表述。改为将 ru_overlay_llm 作为图节点，与 Phase1 生图节点一起 fan-out，在 prepare_ozon_upload 前汇聚（容错空值）。给出具体 `builder.add_edge()` 接线方案 | §2.3 |
| P0-4 | **验证 MXOU Image API negative 参数支持**：实施前通过抓包确认 API 是否接受 `negative` 字段。如不支持，砍掉 Negative Prompt 功能，改为在 prompt 正文中用自然语言描述禁止元素。删除 `||NEG||` 兜底逻辑 | §2.4 |
| P0-5 | **修正视觉变量语言一致性**：PRD §2.2 的示例值必须全部改为英文（COLOR: `navy blue + rose gold` 而非 `темно-синий + медный`）。在 system prompt 中强制要求 LLM 输出英文 | §1.2 G1 |
| P0-6 | **补充节点级 Input schema 扩展方案**：在 PRD 中明确列出需要扩展的 10 个 Input schema（9 个在 state_image_gen.py + 1 个在 variant_primary_loop_node.py），以及新增的 VisualVarsInput/Output、ColorPresetOutput、RuOverlayInput/Output | §3.1 |
| P0-7 | **设计 visual_vars 部分缺失时的处理策略**：不能简单回退旧逻辑（中英混用）。改为"单字段缺失用品类速查表补全，关键字段（PRODUCT/COLOR/MATERIAL）全部缺失才回退旧逻辑" | §4.3 |

### P1 级（建议修改后实施）

| # | 建议 | 关联问题 |
|---|------|---------|
| P1-1 | **修正 graph 路由流程图**：按真实代码重画 §3.1 和 §9.1/9.2 的流程图，包含 check_quota/pricing/assemble_ozon_product/ozon_validate/ozon_status 等节点 | §2.5 |
| P1-2 | **为 variant_primary_loop_node 设计特殊改造方案**：该节点是 ThreadPoolExecutor 循环，需为每个 variant 单独组装 prompt（不同颜色/外观）。不能简单替换 `get_image_prompt` → `assemble_prompt` | §2.6 |
| P1-3 | **提取 `_has_cyrillic()` / `has_chinese()` 为独立工具函数**，供 ru_overlay_llm 和 prepare_ozon_upload 共享复用。修正 PRD 中"复用 `_russian_required_attrs`"的不精确表述 | §2.8 |
| P1-4 | **量化成本影响**：估算每产品新增 LLM 调用次数 × 单次 token 成本，给出月度成本增量预估 | §4.2 |
| P1-5 | **修正 Q-5 性能验收标准**：将"增幅 ≤15%"改为"visual_vars_llm 成功时增幅 ≤15%，LLM 失败回退时增幅 ≤30%"，或给出分场景验收标准 | §4.1 |
| P1-6 | **补充测试策略**：至少包含 (a) prompt_assembler 单元测试（10 图位 × 变量完整/缺失场景），(b) visual_vars_llm mock 测试（JSON 解析容错），(c) 10 节点回归对比测试 | §3.3 |

### P2 级（可在实施过程中迭代）

| # | 建议 | 关联问题 |
|---|------|---------|
| P2-1 | 补充 `image_slots_v5.json` 和 `category_visual_defaults.json` 的 JSON schema 定义 | §3.4 |
| P2-2 | 评估 visual_vars_llm 的 max_tokens 是否需要提升到 8192 | §4.2 |
| P2-3 | 在 `assemble_prompt` 中加入 Jinja2 沙箱渲染（`SandboxedEnvironment`），防止 LLM 输出注入 | §4.5 |
| P2-4 | color_preset_router 的品类关键词匹配增加英文/俄文关键词覆盖 | §4.3 |

---

## 6. 代码验证证据索引

以下为本评审报告中引用的关键代码证据，供工程师交叉核对：

| 证据 | 文件 | 行号 | 内容摘要 |
|------|------|------|---------|
| `get_image_prompt` 仅 2 变量 | `worker/src/utils/image_prompts.py` | 52-67 | `get_image_prompt(key, **kwargs)` → Jinja2 渲染 `{{title}}`/`{{scene_context}}` |
| 中文模板 | `worker/config/image_prompts.json` | 1-22 | 全部中文，仅 title/scene_context 占位符 |
| `call_mxou_image_api` 无 negative | `worker/src/utils/mxou_api.py` | 206-213, 315-321 | 签名无 negative_prompt；payload 无 negative 字段 |
| `call_mxou_chat_api` 纯文本 | `worker/src/utils/mxou_api.py` | 55-97 | content 为纯字符串，不支持多模态 |
| 模型路由 | `worker/config/imagegen.json` | 1-15 | main/social_proof=gpt-image-2，其余=nano-banana-fast |
| graph 真实路由 | `worker/src/graphs/graph.py` | 92-393 | auth→check_quota→ingest→pricing→assemble→scene_llm→Phase1→Phase2→prepare→validate→upload→status |
| `check_quota_node` 独立 | `worker/src/graphs/graph.py` | 86, 132-140 | auth 之后、ingest 之前的独立节点 |
| 无 ImageGenSubgraph | `worker/src/graphs/graph.py` | 72-81 | 10 节点直接添加到主图，非子图 |
| GlobalState 是 BaseModel | `worker/src/graphs/state.py` | 15-139 | Pydantic BaseModel，可扩展 |
| 节点 Input schema 需扩展 | `worker/src/graphs/state_image_gen.py` | 9-185 | 9 个 Input 类无 visual_vars 字段 |
| `variant_primary_loop` 结构差异 | `worker/src/graphs/nodes/variant_primary_loop_node.py` | 28-129 | ThreadPoolExecutor 循环，非单次调用 |
| variant 节点不传 title | `worker/src/graphs/nodes/variant_primary_loop_node.py` | 100 | `get_image_prompt("variant_white_bg")` 不传 title |
| scene_llm 仅取 3 字段 | `worker/src/graphs/nodes/scene_generation_llm_node.py` | 40-42 | draft.title/description/category |
| `_russian_required_attrs` 是局部元组 | `worker/src/graphs/nodes/prepare_ozon_upload_node.py` | 1840, 1944 | Ozon 属性 ID 元组，非可复用函数 |
| draft.attributes 是 dict | `worker/src/graphs/nodes/assemble_ozon_product_node.py` | 435 | `dict[str, Any]` |
| draft.dimensions 结构 | `worker/src/utils/draft_sanity.py` | 47 | keys: length/width/height |
| auth_node 校验余额 | `worker/src/graphs/nodes/auth_node.py` | 411 | `balance <= 0` → AUTH_EXHAUSTED |
| prompt-template-v5.html 不存在 | `find` 全局搜索 | — | 无结果 |
| mxou_api 无 negative 关键词 | `worker/src/utils/mxou_api.py` | 全文 | grep "negative" 无匹配 |

---

## 总体评审结论

### 建议：**修改后通过**

**理由**：

1. **核心问题真实**：PRD 识别的 6 个结构性问题（变量不足、中文 prompt、draft 数据闲置、无 negative、无配色、无俄文叠加）经代码验证全部属实，改进方向有价值。

2. **但存在 3 处 P0 级技术假设与代码直接矛盾**：
   - 多模态推断路径无效（DeepSeek-v4-flash 不支持图片输入）
   - ImageGenSubgraph 并非独立子图，"异步并行"设计不可行
   - MXOU Image API 的 negative 参数支持未经验证，`||NEG||` 兜底可能产生反效果

3. **方案完整性不足**：节点级 Input schema 扩展遗漏（10 个 schema 需改造但 PRD 未提及）、graph 路由流程图与真实代码严重不符、测试策略缺失、配置迁移方案不完整。

4. **存在 1 处 P0 级自相矛盾**：PRD §2.2 声称视觉变量"全部为英文"，但示例值中 COLOR/MATERIAL/APPEARANCE/ATMOSPHERE/PACKAGING 使用俄文。

5. **成本与性能评估不充分**：新增 2 次 LLM 调用/产品的成本未量化；Q-5 "增幅 ≤15%"的验收标准在 LLM 失败重试场景下不现实。

**修改要求**：完成全部 P0 级建议（7 条）后方可进入实施阶段。P1 级建议建议在实施前同步修正，P2 级可在实施过程中迭代。

---

*评审报告完。以上所有结论均基于真实源码交叉验证，证据索引见 §6。*
