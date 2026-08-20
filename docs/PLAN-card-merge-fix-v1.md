# Ozon 并卡（误跟卖）修复方案 v1

> 日期: 2026-08-21
> 范围: worker 上传管线 + skill 提交链路（Q4 答复）
> 依据: Ozon 并卡机制调研 + 本仓库代码审计（file:line 已核实）
> 状态: 方案文档，只写不实现

---

## 1. 问题定义

### 1.1 用户痛点

产品上架后**跑到别人的商品卡下，变成跟卖**。这不是我们自己的多 SKU 变体合并逻辑问题（那套走 `variants` 绑定，是自家卡内合并），而是**跨卖家并卡**：我们的商品被 Ozon 判定与另一卖家的商品是同一型号/相似商品，自动合到一起，展示为对方卡下的货源。

### 1.2 Ozon 并卡机制调研

Ozon 把两张卡判定为同一商品并合并的依据有四类：

| # | 依据 | 说明 |
|---|---|---|
| ① | **EAN / 厂商货号一致** | 上传时 barcodes/EAN 相同 → 同商品 |
| ② | **属性 9048（型号名称）一致** | 同品牌 + 同类目 + 除颜色/尺寸外属性全一致 → 合并（官方帮助 + OpenAPI 规范，见 `docs/PLAN-multi-sku-optimization.md` §1.2） |
| ③ | **「合并至一张卡片」属性** | 商家后台手动合并操作 |
| ④ | **相似商品自动识别** | 标题/图片高相似触发；`SPU_ALREADY_EXISTS_IN_ANOTHER_ACCOUNT` / `DUPLICATES` 错误码在实测中踩过 |

### 1.3 我方根因

| 根因 | 代码位置 | 说明 |
|---|---|---|
| **9048 = 裸 1688 item_id 原样写入** | `worker/src/graphs/nodes/prepare_ozon_upload_node.py:2270-2290` | `model_name_9048 = item_id.strip()`（L2274），且 L2276 注释「无论 9048 是否已存在，都强制覆盖为 item_id」。**同货源竞品也从同一 1688 item 上货 → 写下同一个 item_id → 9048 相同 → 同型号判定并卡** |
| 标题从 1688 直译 | worker prepare `_translate_to_russian_llm`（`config/translate_russian_cfg.json`） | 与竞品（同样从该 1688 直译）高度相似 → 触发 ④ 相似识别 |
| 生图失败回退 1688 原图 | `variant_primary_loop_node.py:54-152` 等 fallback 分支 | 回退原图 → 与竞品同图 → 触发同图识别 |
| 跟卖模式保留竞品标题 | `skill/scripts/cloud_probe.py:3614-3616` | follow 场景 `draft["title"] = ozon_title`（保留竞品 SEO 标题），此时并卡表面可预期，但 hand 模式（`follow_type="hand"`，L3595-3598）是 **CREATE 重建**，本不该并入竞品卡 |

**核心结论：并卡的直接触发器是「9048 型号名在跨卖家间撞值」+「标题/图片与竞品相似」。9048 撞值是确定性的（同 item_id 必撞），标题/图片相似是概率性的。修复应先消掉确定性根因。**

---

## 2. 现状（file:line 引用）

| 项 | 位置 | 现状 |
|---|---|---|
| 9048 值源 | `prepare_ozon_upload_node.py:2270-2290` | `model_name_9048 = item_id.strip()`，强制覆盖/追加；L2271 注释明示「同一 item_id 的多个 SKU 使用相同 9048 → 自动合并为一个商品卡片」 |
| 9048 兜底另一值源 | `assemble_ozon_product_node.py:2590-2597` | 用 `offer_id` 兜底，与 prepare 的 item_id 不一致（见 multi-sku 方案 G3，边缘 bug） |
| 标题生成 | worker prepare（LLM 直译）+ `attributes_llm_node.py` | **无真正 SEO**：无 Ozon 关键词数据（what-to-sell 流量词未接入标题链路） |
| follow 标题 | `cloud_probe.py:3614-3616` | 竞品俄语标题覆盖 1688 中文标题 |
| 生图失败回退 | `variant_primary_loop_node.py`（生成失败用 1688 变体原图 fallback）；Phase2 8 节点已不回退（R4 修复） | 回退点未对「同图并卡」风险告警 |
| 合并验证 | `ozon_status_node.py:413-459` | `model_info` 检查：仅判断「变体是否合并」（`len(model_ids)>1` / `count<=1`），**不区分「并入自家卡」还是「并入他人卡」** |
| retry 保护 | `validation_retry_loop.py:193/2160-2209` | revalidate 同步共享属性（含 9048）到 items[1+]；9048 防重译 |
| follow api 模式 | `follow_sell_import_node.py:302/342` | 恒 `variants=[]`，9048 = `ozon_product_id`，走 import-by-sku 复制路径，**不经 prepare 2270 段** |

---

## 3. 方案选项

### 3.1 ① 9048 前缀方案（核心，推荐）

**改动**：`prepare_ozon_upload_node.py:2274` 改为确定性派生值：

```
9048 = f"{item_id}~{hash}"
hash = sha1(normalize(supplier) + "|" + normalize(source_title))[:8]
```

**设计要点（决定成败，必须遵守）**：

- **hash 输入必须是信封中已有的确定性字段**（`draft.supplier` + 1688 中文原始标题），**绝不能是 LLM 翻译后的俄语标题**。理由：prepare 内标题翻译每次结果可能不同，retry/repair 后 hash 漂移 → 自家变体拆卡。中文原始标题在 prepare 阶段仍保留（翻译发生在 prepare 内部），可取到。
- **计算位置唯一**：`prepare_ozon_upload_node.py` 2270 段抽一个纯函数 `_derive_model_name_9048(item_id, supplier, source_title)`，单 SKU 分支与多 SKU 分支（L2804-3044 的 9048 绑定）同源调用，防止两处漂移（对应 multi-sku 方案 G3 的值源不一致教训）。
- **normalize 规则锁死**：strip + 去内部空白 + 全角转半角；`supplier` 为空时退化为只 hash `source_title`；两者都空时退化为裸 `item_id`（与现状等价，兜底不崩）。
- **值长度**：item_id（约 13-15 位）+ `~` + 8 位 hex ≈ 25 字符，远低于 Ozon 限制；9048 是自由文本属性（非字典），无 dict_id 约束。

**影响分析**：

| 场景 | 行为 | 结论 |
|---|---|---|
| 自家多 SKU 变体合并 | 同 item_id + 同 supplier + 同 source_title → 同 hash → 9048 一致 → 变体仍并入**自家卡** | ✅ 不受影响 |
| 跨卖家同货源（竞品同 item_id） | supplier 或标题不同 → hash 不同 → 9048 不同 → **不并入竞品卡** | ✅ 目标达成 |
| 跨卖家同货源但 supplier/标题完全一致 | 极端情况 hash 撞车 → 仍并卡 | ⚠️ 概率极低，且与现状等价，接受 |
| 已上架的历史卡 | 9048 值变更 → Ozon 视为**新型号** → 建新卡，旧卡残留 | ⚠️ 需配合 ⑤ 归档策略 |
| follow hand 模式（重建） | 本来就要 CREATE 新卡，前缀方案与 hand 策略一致 | ✅ 无冲突 |
| follow api 模式（import-by-sku） | 走 `follow_sell_import_node`，9048 = ozon_product_id，不经本段 | ✅ 无影响 |

### 3.2 ② 标题差异化（配合 ①，防相似识别）

- **真 SEO（推荐目标）**：接 what-to-sell 流量词 → 标题生成。流量词数据已在 `ozon_discovery.py` 的 what_to_sell 链路可获取，与 `docs/PLAN-conversation-entry-v1.md` 同批改造（同一批 what-to-sell 数据消费）。
- **保底（短期）**：在 source_title 上做确定性差异化（加商品核心特征词/使用场景词），保证与竞品标题不同，**先防并卡再谈 SEO**。
- **平衡**：Ozon 标题是搜索排名因素，差异化不能盲目随机打乱词序，须基于关键词体系内调整。标题生成改的是 worker prepare + `config/*.json`（提示词热加载），不涉及 Cython 编译模块。

### 3.3 ③ 生图失败告警

- 回退 1688 原图的位置加 **warning 日志**：「图片为 1688 原图，与竞品可能同图，存在被相似识别并卡风险」。
- 位置：`variant_primary_loop_node.py` fallback 分支 + 其余仍回退原图的点（Phase2 已不回退，无需改）。
- 非致命，只提示，进日志/遥测，不阻断。

### 3.4 ④ 上架前检测（用户决策点）

- **位置**：skill 提交前（`cloud_probe.py` 的 `publish_product_new` / follow 提交路径），或 worker submit 前。
- **逻辑**：用标题核心词搜 Ozon（`/v1/product/list` + `/v1/product/info` 或 `search_categories_validated` 相似通道），高相似命中 → 返回警告「可能并卡到竞品」→ 用户二选一：「**差异化重上**」（改标题 + 走 ① 新 9048）或「**确认跟卖**」（保留现状，接受并卡）。
- **价值**：把「并卡」从不可见的被动结果变成用户可控的决策点；同时给正品同源跟卖（用户本来就打算跟卖到某卡）留了出口，保留选择权。

### 3.5 ⑤ 已并卡补救（worker 侧）

- **检测**：`ozon_status_node.py:413-459` 的 `model_info` 检查扩展为**区分两种并卡**：
  - 并入自家卡：`model_info.count > 1` 且 model 下产品都是我们这次上传的变体 → 正常合并，放行。
  - 并入他人卡：model 下出现**非本次上传**的陌生 product → 标记「误并他人卡」。
- **补救流程**：检测到误并 → 归档该卡（释放配额）→ 走 ① 新 9048 + ② 差异化标题重上。删卡是写操作，需人工/半自动确认（对齐「提交前必须用户确认」纪律）。

---

## 4. 推荐组合

**组合执行，分三阶段**：

| 阶段 | 内容 | 位置 | 成本 |
|---|---|---|---|
| **Phase 1（P0 最小防御）** | ① 9048 前缀 + ③ 生图失败告警 | worker（独立发版） | 1-2 天 |
| **Phase 2** | ④ 上架前检测 + ⑤ 已并卡补救 | skill 提交链路 + worker | 2-3 天 |
| **Phase 3** | ② 标题差异化（真 SEO） | worker + config + what-to-sell 数据消费 | 与对话入口同批 |

理由：
1. ① 消掉确定性根因（9048 跨卖家撞值），是修复的主干；③ 是它的廉价配套。
2. ④ 是用户可控的兜底网（相似识别不可完全消除，检测给了决策权）；⑤ 是事后止血。
3. ② 的 SEO 部分依赖 what-to-sell 数据消费，与 `PLAN-conversation-entry-v1.md` 同批改造，避免两套标题生成逻辑漂移。

---

## 5. 实施步骤

| 步骤 | 内容 | 验收 |
|---|---|---|
| P1-1 | `prepare_ozon_upload_node.py` 新增 `_derive_model_name_9048(item_id, supplier, source_title)` 纯函数；单 SKU（L2274）与多 SKU 绑定分支同源调用 | 单测：同输入恒同值；supplier 空/标题空退化路径正确 |
| P1-2 | 改标题生成前保留 source_title 快照（供 hash 用，防翻译后取不到原始标题） | prepare 日志输出 hash 输入摘要 |
| P1-3 | 生图失败回退原图位置加 warning | 日志含「可能同图并卡」提示 |
| P2-1 | `ozon_status_node.py` model_info 检查区分自家/他人并卡 | 单测：陌生 product 混入 → 标记误并 |
| P2-2 | 误并补救流程：归档 + 差异化重上（人工确认） | 流程可走通，归档释放配额 |
| P2-3 | skill 提交前检测：标题核心词搜 Ozon → 高相似警告 → 用户选差异化重上或确认跟卖 | 命中时返回选择，不静默提交 |
| P3-1 | what-to-sell 流量词 → 标题生成（与对话入口同批） | 标题含流量词 + 与竞品不同 |

---

## 6. 风险

| 风险 | 影响 | 缓解 |
|---|---|---|
| **9048 改动对已上架卡的影响** | 前缀方案只对**新上架**生效，已上架卡 9048 仍为裸 item_id，继续存在并卡可能；若存量也改则旧卡变新卡 | 上线前统计存量 `9048 = item_id` 的卡；**只对新商品启用前缀**，存量按需配合 ⑤ 归档重上；灰度发布 |
| **Ozon 相似识别不可控** | 标题/图片只能降低并卡概率，无法根除（④ 是平台行为） | 组合防线（①+②+③ 降低触发）+ ④ 检测给用户决策权，接受残余概率 |
| **hash 确定性被破坏** | 供应商名/标题在采集链路上多变（尾缀空格、全半角、同一工厂多种写法）→ 重试后 hash 变 → 自家拆卡 | normalize 规则锁死 + 单测；hash 只用信封确定性字段；**绝不用翻译后标题** |
| **与 SEO 的平衡** | 差异化过度伤搜索排名 | 差异化在关键词体系内做（② 保底先保不并卡，真 SEO 阶段再优化）；④ 让用户参与决策 |
| **跨卖家并卡不总是坏事** | 正品同源跟卖用户可能**想**并到已有卡 | ④ 保留「确认跟卖」选项，不强制差异化 |
| 9048 值长度/类目限制 | 超长或类目 schema 约束变化 | 值约 25 字符远低于限制；落地前经 `/v1/description-category/attribute` 确认 9048 maxLength（对应 multi-sku R4） |

---

## 7. 验收标准

1. **跨卖家不并卡**：同一 1688 item、不同供应商的信封 → 生成的 9048 值不同；真实店铺实测上传后不并入彼此卡。
2. **自家多 SKU 不变**：自家多变体信封 → 9048 一致；24h 后 `model_info.count == N`（自家卡合并行为不变，对齐 `docs/PLAN-multi-sku-optimization.md` 实测基线）。
3. **hash 确定性**：retry / repair_prepare 触发后 9048 值不变（同一信封重跑 N 次值一致）。
4. **告警**：生图失败回退 1688 原图时日志出现并卡风险 warning。
5. **上架前检测**：标题高相似命中时返回「差异化重上 / 确认跟卖」选择，不静默提交。
6. **已并卡补救**：误并入他人卡被检出 → 归档 → 差异化重上流程可走通。
7. 回归：worker 全量测试通过（含 `test_*` 现有 1252 用例不破）+ skill 测试通过；9048 相关单测（前缀派生/退化路径/确定性）新增覆盖。
