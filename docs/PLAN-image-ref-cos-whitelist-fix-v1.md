# PLAN-image-ref-cos-whitelist-fix-v1 — 生图参考白名单认领本方 COS 镜像图（「原图上卡」事故修复）

> 2026-09-16 立项。取证结论与修复方案均已在会话中与用户对齐（用户拍板：**识图参考允许 1688 原图**——
> 白名单语义不变，补的是「本方 COS 镜像 = 货源原图 1:1 副本」的等价关系）。
> 分支名：`fix/image-ref-cos-whitelist-v1`（自 origin/dev 拉出，一会话一分支一 worktree）。

---

## 0. TL;DR

**事故**：2026-09-16 16:00–17:00 采集箱批量提交 65 单，31 单 completed 卡片全部上了 1688 原图（用户观感：
「生图了但上卡还是原图」）。生图 API 一次都没被调用（worker 台账 + 网关计费双源互证，15:59:03 后归零）。

**根因**（三环相扣，每环单独看都"正确"）：
1. `draft_image_mirror`（M5b）把草稿 `draft.images` 前 5 张镜像到 COS 并**回写草稿 payload** →
   信封里只剩 COS URL（`draft-images/{md5}.jpg`），无任何 alicdn 原图；
2. `image_url_guard.is_product_image_candidate` 白名单只认 alicdn/1688/taobaocdn/pdd →
   **本方 COS 域被拒** → 五个生图节点「无合格参考图」全部跳过；
3. prepare 空画廊（E1 salvage 过同一白名单，同样为空）→ assemble `_validate_and_fill_items`
   用**未过滤**的 `draft.images`（=镜像原图）补位上卡 → Ozon 抓 COS 成功 → 过审。

**修复核心**：`is_product_image_candidate` 放行本方 COS 托管图（`is_cos_url()`）——镜像图本就是原图副本，
生图参考语义完全等价。一处改动，129 个已污染草稿**自愈**（无需清数据）。

**gpt-image-2.5 无辜**：channel 6 模型表 `gpt-image-2 / nano-banana-fast / nano-banana-2-lite / gpt-image-2.5`
四模型并存，事故当天 gpt-image-2 计费记录全天连续、零错误。模型切换与本案无关（批3 独立可选）。

---

## 1. 事故取证存档（结论可复核）

| 证据 | 数值 | 来源 |
|---|---|---|
| 失败窗口 | 2026-09-16 16:00–17:00（9/15 15:56 镜像生效后首 个整批） | ozon_product_tasks 按小时聚合 |
| 批次构成 | 65 任务 = 31 completed + 26 failed + 8 rejected，全部 draft_submissions | draft_submissions 按小时计数 |
| 信封污染面 | 60/65 信封 `draft.images` **零 alicdn/1688**，全是 `yss-1256275613.cos.../draft-images/` | payload jsonb 域名统计 |
| 生图调用断流 | worker `mxou_call_ledger` image_gen:* 最后一条 15:59:03；网关 image 模型计费同刻断流 | 双台账互证 |
| 草稿污染面 | 近 3 天 129 草稿 `image_mirror_state=mirrored`，payload 首图 key 前缀全部 `draft-images/` | product_drafts |
| 卡图=原图（像素级） | 5 个 product 抽样：卡图 800×800/1500×1500/310×310 方形，与信封镜像图尺寸逐一相等、pHash 0.43–0.72 | 下载比对 |
| 健康路径对照 | 有生图记录的任务卡图 896×1200（3:4，与生成图 1086×1448 同比例）；原图全是方图 | 下载比对 |
| 网关模型表 | channel 6 (OpenAI, status=1)：gpt-image-2 / nano-banana-fast / nano-banana-2-lite / gpt-image-2.5 | Supabase channels |

**图源判别口径（写进运维常识）**：COS key 前缀 `draft-images/` = 镜像原图；`ozon-1688/salvage/` = E1 兜底转存原图；
`file/images/`（3:4）= AI 生成图。判「这单有没有真生图」看 `task_generated_images` 有无行。

---

## 2. 设计口径（用户拍板）

1. **识图（生图参考）允许参考 1688/阿里系原图**——白名单的 alicdn/1688/taobaocdn/pdd 面保持不变，不放宽不收紧。
2. **本方 COS 镜像图 = 货源原图的一比一副本**（mirror 下载原 URL 字节后原样转存），参考语义等价 → 放行。
3. 上卡优先级不变：**AI 生成图 > E1/补位镜像原图 > 空图诚实失败**。原图兜底上卡是 E1 的既有设计
   （v0.28.5），本次不废除，只把「谁能进兜底」收口成同一白名单口径。

---

## 3. 修复方案

### 批1（P0 hotfix）：白名单放行本方 COS

**改动 1 — `worker/src/utils/image_url_guard.py`**
- 新增 `is_cos_url()` 的**唯一实现**（`.myqcloud.com` / `cos.` 子串判定，语义沿用 `cos_uploader.is_cos_url` 现文）。
  放这里是为了断 import 环：`cos_uploader` 已经 import 本模块，反向 import 会成环。
- `is_product_image_candidate` 增加放行分支，**顺序敏感**：

  ```python
  # 顺序：先 str + http(s) 前缀（is_cos_url 对非 str 恒 True 的既有契约不能带进这里），
  # 再 本方COS OR 货源图床白名单，最后缩略/.webp 恒拒（对 COS 镜像同样生效）。
  if is_cos_url(lowered):
      pass  # 本方 COS 托管（draft 镜像 / E1 salvage / AI 生成产物）→ 合格候选
  elif not any(dom in lowered for dom in (...现有白名单...)):
      return False
  ```

- 模块 docstring 补一段事故注记（2026-09-16 原图上卡事故 + 镜像×白名单冲突链），落实「改前必读」。

**改动 2 — `worker/src/utils/cos_uploader.py`**
- 删除本地 `is_cos_url` 实现，改 `from utils.image_url_guard import is_cos_url`（re-export 保持
  `from utils.cos_uploader import is_cos_url` 的既有 3 个消费方零改动：draft_service 镜像闸 /
  ozon_validate_node 全外链硬拦 / validation_retry_loop pictures 取图）。
- 模块头注释同步「is_cos_url 唯一实现已迁 image_url_guard」。

**不做的事**：
- 不做精确 bucket 域匹配（`COS_BUCKET` env 耦合换不来安全增益——信封图的写入方只有 mirror/salvage/gen 三者，
  全是本方桶）；已知限制：若未来配自定义 `COS_PUBLIC_DOMAIN`（非 myqcloud 域），is_cos_url 不识别，
  届时随该需求一起扩。
- 不清 129 个已镜像草稿——放行后它们天然自愈。

### 批2（P1 hardening）：补位收口 + E1 直通优化

**改动 3 — `worker/src/graphs/nodes/assemble_ozon_product_node.py`（3113–3116）**
- `_validate_and_fill_items` 的无图补位改为**只补 `is_cos_url` 成立的图**（镜像/E1 产物），
  裸 alicdn 外链不再进 payload（Ozon 抓不到 alicdn，塞进去只会换一种方式 IMAGE_ERROR——
  与 prepare 3124「不使用 alicdn 原图」纪律对齐，消除两节点口径矛盾）。
- 补位时 log 带来源前缀（`draft-images/` / `salvage/`），出事可一眼定位。

**改动 4（可选优化）— `worker/src/utils/cos_uploader.py` `salvage_original_images`**
- 输入已是本方 COS 的 URL 直接原样收下（免下载-再上传的冗余转存；P0 生效后镜像草稿走 E1 会触发此路径）。

### 批3（独立可选，与本案根因无关）：gpt-image-2.5 切换

- `worker/config/imagegen.json`：`main` / `social_proof` 两键 `gpt-image-2` → `gpt-image-2.5`
  （bind mount 热加载，无需重建镜像；`image_models.py` 的 DEFAULT 兜底不动）。
- 切换前先拿 1–2 单实测质量/耗时/计费（网关计费口径 gpt-image-2=75000/张，2.5 待确认），再全量。
- `mxou_api.PRIMARY_IMAGE_MODEL` 同步与否取决于是否要改代码默认值——config 热加载已覆盖生产，
  代码默认值随下一次发版顺手对齐。

---

## 4. 任务拆解（SDD）

| # | 任务 | 文件 | 验收 |
|---|---|---|---|
| T1 | is_cos_url 迁移 + re-export | `image_url_guard.py` / `cos_uploader.py` | 既有 3 个消费方 import 零改动；无 import 环 |
| T2 | 白名单放行分支 + docstring 事故注记 | `image_url_guard.py` | 见 T5 用例全绿 |
| T3 | assemble 补位只吃 COS 图 | `assemble_ozon_product_node.py` | 裸 alicdn 不再进 payload；COS 镜像可补位 |
| T4 | E1 对已托管图直通（可选） | `cos_uploader.py` | 镜像输入零二次转存 |
| T5 | 单测扩展 | `worker/tests/test_image_url_guard.py` 等 | 见 §5 |
| T6 | 发版物料 | VERSION 四源 / CHANGELOG / AGENTS.md 最近更新块 | 四源一致 + 实机 gate 过 |

纪律：动手前 `git status` + 相关文件 mtime 检查（多会话撞车防范）；逐文件 `git add`；
worker 全量测试须带 `PGDATABASE_URL=postgresql://postgres:localdev123@localhost:5433/ozon` 且先核实 5433 监听者。

---

## 5. 测试计划

**`test_image_url_guard.py` 新增用例**（先红后绿）：
1. `https://yss-1256275613.cos.ap-guangzhou.myqcloud.com/draft-images/{md5}.jpg` → True（本方 COS 镜像）；
2. `.../file/images/xxx.jpg` → True（AI 生成图，重提场景作参考）；
3. `.../ozon-1688/salvage/{md5}.jpg` → True（E1 产物回流场景）；
4. `https://evil.example.com/draft-images/x.jpg` → False（非 COS 非白名单域仍拒）；
5. `cos.accelerate.myqcloud.com` 全球加速形态 → True；
6. 非字符串 / 空串 / `ftp://` → False（is_cos_url 的 True 契约不外溢）；
7. COS 域 + `_310x310` 后缀 → False（缩略恒拒对 COS 同样生效）；
8. 既有 alicdn/1688/taobaocdn/pdd 与 .webp 用例回归零变化。

**`assemble` 补位用例**（mock 层）：
9. item 无图 + draft.images 全 COS → 补位成功且 primary=首张 COS；
10. item 无图 + draft.images 全裸 alicdn → 不补位（诚实空图，走既有 IMAGE_ERROR 语义）；
11. 混合（COS + alicdn）→ 只补 COS 子集。

**回归锁定用例**（防再次回归的核心一环）：
12. 模拟镜像后信封（draft.images=5×COS）→ `filter_product_images` 返回 5 张（即生图节点 ref 非空）
    ——本事故的直接回归闸。

**全量**：worker 全量 pytest（PGDATABASE_URL 按纪律带 5433 本地库）；预期基线 2639+ 新增用例全绿。

---

## 6. 实机 Gate（发版门槛，≥3 单）

1. 取 3 个 `image_mirror_state=mirrored` 的**已污染草稿** resubmit → 每单验证：
   `task_generated_images` 有行（≥5 slot）、卡主图 3:4（AI 生成图）、信封/卡无 `draft-images/` 直上；
2. 新采集 1 单全链路（discover → box → submit）→ 同上；
3. 对照：1 个 gen 失败场景（或 mock）→ E1/补位图上卡且来源前缀可辨；
4. 生图调用恢复确认：发版后 `mxou_call_ledger` image_gen:* 恢复增长、网关计费连续。

---

## 7. 风险与回滚

| 风险 | 评估 | 缓解 |
|---|---|---|
| 放行面过大（任意 myqcloud 域） | 低——信封图写入方仅 mirror/salvage/gen，全本方桶 | 批1 先 is_cos_url 全量放行；若未来接外部 COS 再收桶域 |
| `is_cos_url(非str)=True` 既有契约外溢 | 中——直搬会放行 None/数字 | T2 顺序强制 str+http 前置，用例 6 锁死 |
| 内容方缩略（如 310×310 原图被镜像，key 无尺寸后缀） | 低——内容小图过闸，参考质量下降但不串图 | `evaluate_image_quality` 既有质量分兜底；观测后再议 |
| 批2 收口后裸 alicdn 草稿 gen 全挂时 0 图 | 与现状等价（alicdn 本就抓不到） | E1 salvage 仍是主兜底路径 |
| 回滚 | 单 commit revert 即可；无 schema/config/数据变更 | — |

---

## 8. 流程

- Tier A（跨节点行为变更 + 发版）：worktree 分支 `fix/image-ref-cos-whitelist-v1`，PR 自 origin/dev，
  CI 绿 self-merge（merge commit 保留流边界）；批3 若同车，config 变更单独 commit 便于独立回滚。
- 发版按纪律：VERSION 四源一致 → CHANGELOG → AGENTS.md 顶部「最近更新」块 → 实机 gate → dev→main PR → main 打 tag。
- 事故复盘沉淀：本计划 §1 取证表即复盘底稿；AGENTS.md「高频坑」追加一行「草稿图镜像后信封里是本方 COS 域，
  白名单/图源判别必须认它」。
