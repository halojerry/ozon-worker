# 06 · 图片链：来源分类 / 生图 / 出口硬闸 / 收尾断言

> 红线：**原图不上卡**。参考≠上卡。唯一图来源判定 `utils/image_source.py`，唯一白名单 `utils/image_url_guard.py`（`is_cos_url` 唯一实现在此，cos_uploader 仅 re-export）。

## 1. 图来源五分类（image_source.py:72-96）

| 分类 | 判定 | 可上卡 |
|---|---|---|
| `ai` | 本方 COS 且 key ∈ {`file/images/`, `mxou-b64/`} | ✅ |
| `salvage` | 本方 COS 且 key `ozon-1688/salvage/`（E1 原图转存） | 仅逃生门 `IMAGE_SALVAGE_FALLBACK=1` |
| `mirror_draft` | 本方 COS 且 key `draft-images/`（草稿镜像） | ❌（v0.78 批A 收窄） |
| `external` | 非本方 COS 外链；**及本方 COS 但 key 不属已知通道**（保守） | ❌ |
| `invalid` | 非 str/空/非 http(s) | ❌ |

- 三个消费 API：`has_generated_images:99`（任一 ai，b64 兜底计入）、`enforce_upload_policy:106`（全 ai[+salvage] 才放行）、`salvage_fallback_enabled:124`。
- `ImageGenAllFailedError`（:55）**故意非永久**——整任务自动重试一轮，重试仍全败才终态 failed。
- ✅ `is_cos_url` 域判定已收紧为 **hostname 感知**（2026-09-26 handover 批）：urlparse 取 host → myqcloud 后缀 / `cos` 完整域标签 / `COS_PUBLIC_DOMAIN` env 域三者其一；路径/查询串不参与判定——`mycos.evil.com/file/images/x.jpg` 类伪装域不再误判本方 COS（上卡闸的 key 前缀仍是最后防线）。

## 2. 参考图入炉

- skill 侧：`reference_images.py`（alicdn 白名单+bad-token+<200px 拒，白底优先 limit 10）→ `draft.images`；跟卖竞品图 → `extensions.competitor_ref_images`。
- worker 入口：`filter_reference_images`（image_url_guard.py:134-149）——`extensions.follow_sell` 时额外放行 **Ozon 竞品 CDN 原尺寸图**（缩略/`.webp` 恒拒）；竞品图在 image_source 仍是 external，**enforce_upload_policy 恒拒出 payload**（参考≠上卡）。
- 消费点：white_bg:57 / multi_angle:46 / main_image:77。

## 3. 生图计划（utils/image_gen_plan.py）

- `ALL_SLOTS` 10 个；`DEFAULT_PLAN` = white_bg/multi_angle/main_image/detail/scene_1 + variant_primary_loop（**social_proof/comparison/scene_2/scene_3 默认关**，slot 保留可覆盖重开）。
- 覆盖链：`config.configurable.image_gen_plan` → `state.image_gen_plan` → DEFAULT_PLAN；⚠️ **GlobalState 无 image_gen_plan 字段、队列路径不注入 → 生产恒 DEFAULT_PLAN**（预留接口非活通道，09）。
- ⚠️ `validate_plan`（:68）全仓零调用——纯 Phase2 plan 不会报错，只会静默连锁跳过全部生图。

## 4. 生图节点矩阵（10 节点）

统一流程：plan 闸 → 参考白名单 → 任务缓存 task_generated_images((task,slot,version)，命中不重烧) → `prompt_assembler.assemble_prompt`（确定性 extract 不被 LLM 覆盖；`is_adult_product` 命中清空标题/品类/俄文文案变量防 violation）→ `call_mxou_image_api`。

| 节点 | 参考图策略 | 特殊语义 |
|---|---|---|
| white_bg / multi_angle | 原图过滤后前 2（≥5 张智能质量评估） | 无合格参考跳过 |
| main_image | **只用白底图**（防色差）→缺则 multi_angle→原图前 2 | 节点内二次降级循环 fast→2-lite；全配置错→空图不推败 |
| detail / comparison | Phase1 优先 | 常规 |
| scene_1/2/3 | **仅 Phase1 图**（Phase1 失败→跳过不回退原图防广告内容） | scene_context 差异化 |
| social_proof | Phase1 | ⚠️ 节点内降级循环**缺 MxouModelConfigError break**（重复烧坏模型，09-#2-图片） |
| variant_primary_loop | variant.image 单参考 | ThreadPool(4)；⚠️ **失败→1688 原图兜底**（:148-158）——与出口硬闸冲突毒化整单（09-#1） |

**API 层模型链**（mxou_api.py:27-33）：节点配置模型（imagegen.json 现读）→ `nano-banana-fast` → `nano-banana-2-lite`（降级级 120s）。失败分类：

| 异常 | 行为 |
|---|---|
| `ImagePollTimeoutError` | 轮询超时≠失败（已计费）→ **不重试不降级**防双倍扣费 |
| `MxouOutOfQuotaError` | 余额 pre-check（<1.0，30s 缓存+0.0 二次直查）→ 直接失败，不降级不 E1；任务判永久 |
| `MxouContentViolationError` | violation 状态/关键词 → 不重试不降级防重复烧额度；任务判永久。⚠️ 关键词表含宽泛 `"content"` 子串（误判面，09-#5-图片） |
| `MxouModelConfigError` | 未配价/model_not_found → 每模型 1 POST 快停 + Sentry 告警（token+模型 1h 去重）→ 链内快跳下一模型 |
| 普通 failed | 有界重试（主模型 N 次，降级级 1 次，1s 退避） |

b64 产物 → `_b64_to_cos_url`（稳定 key `mxou-b64/{tid}_{digest}.png` 幂等）→ 公网 URL；台账 `mxou_call_ledger` 每次计费生成一行。

## 5. 上卡组装

- **assemble**：跟卖 items `images=[]` 锁（:1067，AI 图由 prepare 注入）；无图补位收窄（:3183-3203）——补位子集只保留 `classify=="ai"`，draft 图全外链→诚实不补。
- **prepare**：`_IMG_ORDER:1884` = main → **social_proof → detail** → scene×3 → comparison → multi_angle → white_bg（主图第一、white_bg 恒最后）。⚠️ docs/WORKER-TOPOLOGY.md:239 的顺序与此不一致（文档漂移）。
  - 多 SKU 主图优先级：main_image → variant_primary_images[0] → white_bg → multi_angle → scene（⚠️ 代码与注释矛盾：注释称变体图优先，09-#7-图片）；变体 items = [变体主图] + 共享营销图[:15]。
  - 跟卖：AI 图 <3 只告警不补竞品图；绝不把 ir.ozone.ru 图放进上传数组。
  - COS 加速域名改写 `_to_ozon_image_url`（区域→cos.accelerate.myqcloud.com，幂等）。
- **出口硬闸 `_enforce_payload_image_policy`**（prepare:2026，唯一调用点 :4136）：单/多 SKU 各分支收口后最后一道——全部 primary+images 过 `enforce_upload_policy(allow_salvage=逃生门)`；违规 → IMAGE_GEN_ALL_FAILED + 违规清单。

## 6. 出口闸链（validate → upload → retry → 收尾）

| 闸 | 位置 | 行为 |
|---|---|---|
| 空图硬失败 | ozon_validate:285-288 | item_errors「缺失」→ critical → retry |
| 抽样可达性 | validate:584-631 | safe_fetch；网络故障降 warning；HTTP≥400 全败→critical |
| 全外链镜像闸 | validate:633-646 | 零 COS 图→critical |
| `LOCAL_IMAGES_MISSING` | ozon_upload:286-310 | 位置在 offer upsert **之后** import POST **之前**；只拦无 product_id 的 CREATE（UPDATE/跟卖天然豁免 0 图=不动卡图；upsert 注入死卡不误伤） |
| 重传闸 `_reupload_gate_blocked` | validation_retry_loop:3824-3865 | 三条 POST 出口（CREATE 全量/UPDATE/pictures 替换）统一过 enforce_upload_policy；违规不 POST |
| AI 图优先防覆盖 | retry:3485-3536 | `_prefer_generated_payload_images`（AI>原图）；`_restore_draft_images_to_payload` 拒覆盖已有 AI 图（只救空载荷） |
| E1 salvage | prepare:1986-2023 | 全 AI 图失败：默认抛 ImageGenAllFailedError（诚实失败）；逃生门转存 `ozon-1688/salvage/` 补位；已托管图 passthrough 零二次转存 |

**retry 主图重生成**（4194/4195 + 有 AI 图 + 未重生成过 `regen_main_image_done` 防循环）：`regen_main_image_node:3631`——写死合规 prompt（白底/居中/无文字水印）；产物**必须判 ai 否则拒入**；旧主图移除、新图插首位；失败→warn-and-pass 绝不抛死任务。⚠️ regen 产物不写任务缓存（09）。

## 7. 收尾断言（utils/card_image_assert.py + ozon_status_node.py:523-632）

- 四态：ok / mismatch / unverified（卡图未填充）/ skipped（载荷无图=跟卖 UPDATE）。
- **数量恒校验**：卡图数 < 载荷图数 → mismatch；**AI 载荷 3:4 校验**：`is_all_ai_images`（逐张走 image_source）→ 下载卡首图（域白名单）手写 JPEG/PNG 头解析宽高 → 比值 ∈[0.70,0.80]，不符 →「卡片被原图覆盖」。
- mismatch/unverified → **3×20s 复查**（CDN 就绪时序，env 可覆写）；仍 mismatch → `CARD_IMAGE_MISMATCH` failed **拒假成功**（moderation_status 仍记 approved）；仍 unverified → 放行但 logger.error + Sentry 大声留痕。
- ⚠️ 覆盖面：只校验 items[0] vs 卡 info_items[0]——多 SKU 变体卡图不校验；混合载荷只查数量不查比例（09）。

## 8. 疑点（并入 09-findings §图片）

1. **🔴 variant 原图兜底毒化整单**（variant_primary_loop:148-158 1688 原图 → 出口闸判 external → 整单 IMAGE_GEN_ALL_FAILED，即使其余 5 张 AI 图全成功；v0.78 批A 后行为变质，注释过时未收口）。
2. social_proof 降级循环缺 MxouModelConfigError break。
3. main 节点双层降级链可能重复 POST 同一坏模型（API 层从 fast 下一级起步、节点层又从 fast 开始）。
4. `is_cos_url` 的 `"cos." in host` 子串过松（外链可伪装本方 COS）。
5. `_CONTENT_VIOLATION_KEYWORDS` 含宽泛 `"content"` 子串（普通错误误判永久违规）。
6. validate_plan 死代码（纯 Phase2 plan 静默连锁全跳过）。
7. 多 SKU 主图优先级注释与代码矛盾。
8. LOCAL_IMAGES_MISSING/CARD_IMAGE_MISMATCH/VARIANT_NOT_MERGED 是自由字符串码，未收进 errors.py 枚举。
9. card_image_assert 只查 items[0]；混合载荷不查比例。
10. regen 产物不落任务缓存（整任务重试时主图二次烧额度面）。
11. validate 空图 critical 判定靠中文文案子串匹配（文案改动即静默失效）。
12. 跟卖 regen 参考退化（filter_product_images 不放行竞品图，与批I 口径不完全一致）。
