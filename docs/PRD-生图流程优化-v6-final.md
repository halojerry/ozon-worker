# PRD：生图流程优化 v6 — 对抗验证后最终方案（2026-08-09）

> **状态**：定稿待批准执行（Wave 0/1 基础已落地，Wave 1-C/2 阻塞待用户确认）
> **前置**：`docs/PRD-生图流程优化-v5.md`（被评审对象）、`docs/REVIEW-生图流程优化-v5.md`（Gao 评审，已被本方案部分推翻）
> **方法**：HYPERPLAN 对抗团队（5 成员 × 3 轮交叉攻击）+ plan agent 实施编排（provenance: ses_01d63f2e1ffepO1siEg1hg4GuZ）
> **已落地**：Wave 0（commit e4fc7fb）+ Wave 1 基础（commit b674763）

---

## 一、对抗定案（推翻/修正 Gao REVIEW 的结论）

### 1.1 关键实证（全部代码验证）

| # | 事实 | 证据 | 对 Gao REVIEW 的影响 |
|---|---|---|---|
| F1 | **7/10 节点不传 title**（非仅 variant 1 个）| comparison/detail/social_proof 零参数、scene_1/2/3 只传 scene_context（各节点 L51-53/52）、variant L100 | 推翻 P1-2「保留字面量」——Jinja2 lenient Undefined 渲染为空串（实测）|
| F2 | **全部 10 节点 Input schema 已带 draft** | state_image_gen.py:9-172 | 推翻 P0-6「扩展 10 schema」的机制误读；真坑是 PRD §5.2.4 vs §7.3 自相矛盾 |
| F3 | LangGraph 节点输入被 schema 强转，未声明字段被剥掉 | graph.py:312-315 | P0-6 可达性担忧实质成立（保守正确）|
| F4 | **8/10 图位跑 nano-banana-fast**（速度优先）| imagegen.json | 「英文更好」收益主要落在 2 个 gpt-image-2 槽位 |
| F5 | **GraphOutput 无 ru_overlay 字段** | state.py:154-191 | G6/Q-4 数据通道断链（kill-shot，Gao 未发现）|
| F6 | **19 变量只有 1 个 scene 字段**，服务不了 3 个 scene 槽位 | PRD §2.2 vs scene_generation_llm_node.py:90-93 | 三张场景图会变同一场景（Gao 完全漏掉）|
| F7 | draft 载体：CONTRACT-v4 契约污染 + last-write-wins 双写竞态 | state.py:44 | draft 载体永久否决 |
| F8 | mxou chat API 纯文本，deepseek-v4-flash 无视觉 | mxou_api.py:55-97 | 多模态推断无效（Gao P0-2 对）|
| F9 | `_has_cyrillic` 模块级（prepare:130）、`has_chinese` 已共享（attribute_utils:134）| 实证 | Gao P1-3 前提错误，提取降 P2 卫生项 |
| F10 | v0.13 英文 prompt 实验失败已回退 | AGENTS.md 明令「勿改回英文版」| PRD「英文更好」是零证据断言 |
| F11 | Jinja2 kwargs 值不被二次渲染 | image_prompts.py:63 | SandboxedEnvironment 无意义（Gao P2-3 伪需求）|
| F12 | 「允许俄文」=PS 合成层、「negative 禁字」=AI 渲染层，互补非矛盾 | PRD §1.3 非目标 | Gao C4 分层误读；13 品类速查表 vs 8 配色预设是两张表 |

### 1.2 砍掉清单（对抗收敛）

| 项 | 处置 |
|---|---|
| negative_prompt / `\|\|NEG\|\|` | **整体砍掉**（双方同罪：未验证断言；后缀内嵌禁令保留）|
| ru_overlay 进主路径 | **移出**（GraphOutput 无字段，需扩展+回写；Q-4 验收删除）|
| 多模态推断 | **移除**（纯文本模型）|
| Jinja2 沙箱 | **移除**（错误威胁模型）|
| Wave 1 的 10 schema 扩展 | **否决** → 第三路线（节点内即时计算、零状态写入）|
| draft 作数据载体 | **永久否决**（契约污染+竞态）|
| 英文模板作默认前提 | **降级为 A/B 实验**（v0.13 历史 + 零基线）|
| prompt-template-v5.html 作引用源 | **降级**：「不在仓库」，需人工核对 PRD 引用 |

### 1.3 验收标准重写

| 原 AC | 重写后 |
|---|---|
| AC-1「19 变量全非空英文」| **「8 必填非空 + 11 可选默认化」**（可选默认走模板内置/纯提取，不要求 LLM 全量填充）|
| G4「通过率 ≥85%」| **须先测基线**（无基线不作改进幅度判断）；85% 是目标非假设 |
| Q-4 ru_overlay 字段完整 | **删除**（移出主路径）|
| Q-5「增幅 ≤15%」| **分场景**：LLM 成功 ≤15% / 失败回退 ≤30% |

---

## 二、实施编排（plan agent 输出）

```
Wave 0: 修 7/10 节点空 title bug（纯 bug 修复）                ✅ 已提交 e4fc7fb
Wave 1: prompt_assembler + 中文模板变量增强（零状态写入）      ⏳ 基础已提交 b674763
        ├─ 1-A/B: assembler + extract 函数（纯新增）          ✅ 已完成
        ├─ 1-C: image_prompts.json 加 {{material/color/size/weight/category}} 占位符  ⛔ 阻塞
        ├─ 1-D: 10 节点迁移到 assemble_prompt                 ⛔ 阻塞（依赖 1-C）
        └─ 1-E/F: 基线测量 + 回归（真实生图成本）             ⛔ 阻塞（A/B 预算）
Wave 2: visual_vars_llm + color_preset + slots（A/B 后）       ⛔ 阻塞（依赖 A/B）
独立后续: ru_overlay（GraphOutput 扩展）                      ⛔ 阻塞（独立任务）
```

---

## 三、执行状态（2026-08-09）

### 已落地

**Wave 0**（e4fc7fb）：7/10 节点 title 注入
- RED 7 failed → GREEN 10/10（test_image_gen_title_injection.py）
- 回归 test_image_prompts_config 12/12 不破坏
- 改动 +21/-7（仅补 title 参数，未碰模板/config/graph）

**Wave 1 基础**（b674763）：prompt_assembler.py（112 LOC）+ test_prompt_assembler.py（281 LOC, 17 用例）
- RED → GREEN 39 passed；回归 12/12 + 10/10
- `assemble_prompt`（场景优先级 slot > global > 模板默认；失败回退中文模板）
- `extract_visual_vars_from_draft`（material/color/size/weight/category 确定性提取，无 LLM）
- **纯新增**，未触碰现有文件；`**extra` 预留 Wave 2

### 阻塞项（需用户确认）

| # | 确认点 | 推荐默认 |
|---|---|---|
| ① | 基线样本来源 | 本地 PG 真实信封（脱敏）|
| ② | A/B 预算 | 100 次生图/实验（2 arm × 5 产品 × 10 槽位，可降 3 产品）|
| ③ | 模板改动范围 | 现有中文模板加 5 个占位符（不做英文重写）|
| ④ | 两个已落地 commit 是否推送 | 推送（与 E2E 批次一致）|
| ⑤ | 云端学习缓存排查 | 需用户提供生产 DB 访问方式 |

---

## 四、Wave 1-C/2 详细方案（待确认后执行）

### Wave 1-C 模板增强
- `worker/config/image_prompts.json`：现有中文模板加 `{{material}}`/`{{color}}`/`{{size}}`/`{{weight}}`/`{{category}}` 占位符
- 同步更新 `image_prompts.py` 的 `_DEFAULT_PROMPTS`（drift 测试约束）
- 测试：`test_config_file_matches_defaults` + `test_prompt_assembler` 扩展

### Wave 1-D 节点迁移
- 10 节点 `get_image_prompt(key, ...)` → `assemble_prompt(key, **extract_visual_vars_from_draft(draft), slot_scene_context=scene_context_N)`
- scene_1/2/3 传 slot_scene_context（防同场景化，F6）

### Wave 2 LLM 层（A/B 后）
- `visual_vars_llm_node`：纯文本 LLM，19 变量，容错 JSON 解析（镜像 scene_llm:69-88 范式），失败回退 extract + 品类默认
- `color_preset_router`：纯函数，8 预设 × 品类映射（无 LLM）
- 单字段 `visual_vars` 进 GlobalState + 10 Input schema（Wave 2 才扩展，F3）
- graph 接线：scene_generation_llm → visual_vars_llm → color_preset_router → Phase1

### 测试基建
- `test_visual_vars_llm_node.py`（mock chat：合法/坏 JSON/部分字段/API 失败）
- `test_color_preset_router.py`（13 品类映射 + 默认）
- 复用 `test_image_gen_title_injection.py`（mock 节点模式已验证）

---

## 五、风险注册

| Wave | 风险 | 缓解 |
|---|---|---|
| W1 | 8/10 弱模型天花板 | 分槽位指标；门禁=无回归非硬目标 |
| W1 | 无基线 → 改进不可证 | 基线先测（Gao G4 批评适用于 W1 自身）|
| W1 | 模板漂移 | drift 测试约束 |
| W2 | 19 字段 LLM 稳定性 | 容错解析 + per-field 默认 + 单次重试 |
| W2 | LLM 延迟/成本 | A/B 预算封顶；visual_vars 按 task_id 缓存 |
| 所有 | zombie 真实上架 | 本地 Docker + 清任务表（红线）|

---

*PRD v6 — 2026-08-09，对抗验证后定稿。Wave 0/1 基础已落地，Wave 1-C/2 待用户确认执行。*
