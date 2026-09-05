# v0.64.0 视觉模型切换 + 编排改进 — 完整执行计划

> 日期: 2026-09-01
> 范围: worker 类目匹配/属性填充/生图链路（vision 模型）+ v0.64.0 发版
> 依据: 类目错放 + 属性填充不准深度分析（pg_trgm 字符级 + LLM 无视觉）+ 用户拍板
>       「deepseek-v4-flash 改成 deepseek-v4-flash-vision-exp 多模态」
> 状态: 实施已完成（Steps 1-8 全落地、单测绿），本文档为剩余**发版执行**计划

---

## 1. 已完成实施（不需要再动的部分）

### Step 1 — `call_mxou_chat_api` 视觉支持
- **文件**: `worker/src/utils/mxou_api.py`
- 新增 `image_urls: Optional[list[str]]` 参数；有图时 user content 按 OpenAI Vision array
  格式组装（`[{"type":"text"...},{"type":"image_url"...}]`，上限 4 张），无图时保持纯字符串
  （旧行为逐字不变，向后兼容）。默认模型改为 `deepseek-v4-flash-vision-exp`。
- system_prompt 恒为字符串；余额 pre-check、重试退避、reasoning fallback 逻辑不变。

### Step 2 — 模型名全量切换
- **9 个 config JSON**（`worker/config/*.json`）：`deepseek-v4-flash` → `deepseek-v4-flash-vision-exp`
- **8 个 Python 源码**硬编码 fallback 同步切换（mxou_api/attr_value_matcher/title_sanitizer/
  validation_retry_loop/scene_generation_llm_node/visual_vars_llm_node/follow_sell_import_node/
  prepare_ozon_upload_node/assemble_ozon_product_node）
- 已 grep 确认 `worker/src` + `worker/config` **无残留旧模型名**；`worker/assets/pipeline-full.json`
  是 legacy n8n 导出，刻意不动。
- `attr_disambiguation_cfg.json` 的 "up" prompt 增加「请同时参考上方产品图片判断属性值」视觉指引。

### Step 3 — 视觉辅助类目匹配（解决类目错放）
- **文件**: `worker/src/graphs/nodes/assemble_ozon_product_node.py`
- `_llm_rank_categories`（L2952）：prompt 增加视觉指引行 + `image_urls=draft.images[:3]` 传图。
  LLM 现在能看到「圆盘状饮水器」图片 → 选「宠物饮水器」而非「饮水器」。
- `_llm_match_category`（L1843）是死代码（grep 确认无调用），只同步模型名未接线——不扩大本次范围。

### Step 4 — LLM 属性消歧接线（解决属性填充不准，复活死代码）
- **文件**: `worker/src/utils/attr_value_matcher.py` + `prepare_ozon_upload_node.py`
- `disambiguate_candidates`（安全三件套：-1 出口/候选索引/abstain）新增 `image_urls` 透传参数。
- `prepare._fill_optional_dict_attrs` 多候选分支：**精确命中优先 → 否则 LLM 消歧（带图）→
  消歧失败 abstain 跳过（绝不取第一个）**。
- `assemble._find_dict_value` 从 `hits[0]` 改为 `unique_or_none`（多候选不再盲补首值，
  留给 prepare gap-fill 消歧）——消除「套娃」级隐患，三处一致。

### Step 5 — 视觉属性推断（新增 `_infer_attrs_from_vision`）
- **文件**: `prepare_ozon_upload_node.py`（`_append_spec_table` 之前新增函数，主节点 L3259 接线）
- 对仍未填充的视觉属性（名称关键词匹配：цвет/материал/стиль/узор/пол/форма/сезон/рисунок…，
  **非硬编码属性 ID**，跨类目适配），用 vision 模型从产品图片推断：
  字典属性走 `search_dictionary_values` + `unique_or_none` 链解析 dict_id；自由文本直接用
  LLM 回答（含中文跳过）。图片缺/无 token 时静默跳过（零开销降级）。

### Step 6 — 编排重新审视 + 场景/视觉变量传图
- **结论**: 图拓扑不变（auth→check_quota→[…full]→ingest→pricing→assemble→scene_gen→
  visual_vars→[10 生图节点]→prepare→validate→upload→status→[fetch_back→learning_record |
  validation_retry_wrapper]）。改进全部在节点内部，无结构性重构需求。
- **关键收益**: 类目匹配 + 属性填充前置到 `assemble`（生图**之前**）→ 错误类目在 GPU 浪费前被拦截。
- `scene_generation_llm_node.py` / `visual_vars_llm_node.py`：调用传 `image_urls=draft.images[:3]`，
  移除「F8 实证 deepseek-v4-flash 无视觉」注释。

### Step 7 — Commit Review 修复
- **P2 已修**: `main.py` lifespan 关闭 asyncio 默认线程池（`_graph_executor.shutdown`）；
  `sentry_setup._before_send` 含 Python traceback 的事件跳过噪音聚合（防真实 bug 被 fingerprint
  折叠遮蔽，新增 `_has_python_traceback` 守卫 + 2 单测）。
- **P1 不改（记录决策）**: 超时 → `permanent=True` 维持。理由: 同步节点线程不可取消，
  超时后重试 = 双次执行 + 双次 MXOU 计费；终态语义符合「任务超时即放弃」设计，
  比改可重试引入的重复计费风险更小。已在代码注释记录行为意图。

### Step 8 — 测试更新
- 新增 `test_mxou_vision_format.py`（6 断言: 无图字符串/有图 array/4 张上限/空列表==None/
  system 恒字符串/默认模型）。
- 更新 `test_visual_vars_llm_node.py`（断言 image_urls 传图 ≤3）、`test_sentry_setup.py`
  （traceback 透传）、`test_draft_ai_*`/`test_attr_llm_disambiguate`/`test_config_service`/
  `test_mxou_balance_precheck` mock 签名与模型名。

---

## 2. 剩余执行步骤（发版 v0.64.0）

> ⚠️ 当前版本四源均为 **0.63.0**（v0.63.0 已发版）。本次 bump 到 **0.64.0**。

### Step 9 — 提交实施代码

```bash
cd /Volumes/os/dev/ozon-worker
git add worker/config/ worker/src/ worker/tests/
git commit -m "feat(worker): 视觉模型切换——类目/属性/生图链路全面接入 deepseek-v4-flash-vision-exp

- call_mxou_chat_api 新增 image_urls（OpenAI Vision array，≤4 张，无图纯字符串向后兼容）
- 类目 LLM 匹配传产品图（assemble._llm_rank_categories）→ 缓解 pg_trgm 字符级错配
- 属性消歧接线（prepare 多候选 LLM 消歧带图 + assemble _find_dict_value 改 unique_or_none
  不再盲补首值）→ 复活死代码 disambiguate_candidates
- 新增 _infer_attrs_from_vision 视觉属性推断（颜色/材质/风格等，非硬编码 ID）
- scene_gen/visual_vars 传产品图提升生图质量；9 config + 8 py 全量换模型名
- commit review: asyncio 线程池 shutdown + Sentry traceback 事件跳过噪音聚合
- 测试: test_mxou_vision_format 6 断言 + 5 文件 mock 签名/模型名同步"
```

### Step 10 — 版本四源 bump 0.63.0 → 0.64.0

```bash
# 四个文件必须全部一致
echo "0.64.0" > VERSION
echo "0.64.0" > skill/VERSION
echo "0.64.0" > deploy/skill/VERSION
# skill/SKILL.md frontmatter: version: "0.63.0" → "0.64.0"
# 核对:
grep -H "" VERSION skill/VERSION deploy/skill/VERSION | sed 's/:$/: /'
grep -m1 '^version:' skill/SKILL.md
```

### Step 11 — CHANGELOG.md + AGENTS.md 更新

- **CHANGELOG.md** 顶部新增 `## [0.64.0] - 2026-09-01` 块（内容 = 本计划 Step 1-8 摘要 +
  测试基线: 单测 155 passed 定向 / 全量 1247 passed + 287 skipped）。
- **AGENTS.md** 顶部「最近更新」块新增 v0.64.0 段（在 v0.62.0 段上方），含: 视觉模型切换
  四要点（类目传图/消歧接线/视觉推断/生图传图）+ 新关键约定（vision 内容格式、image_urls 参数、
  `_infer_attrs_from_vision` 关键词匹配不硬编码 ID、disambiguate 已接线不再是死代码）。

### Step 12 — 本地验收全绿（发版前最后确认）

| # | 检查 | 命令 |
|---|------|------|
| 12a | worker 定向回归（本次改动 14 文件） | `cd worker && PGDATABASE_URL=... ../skill/.venv314/bin/python -m pytest tests/test_mxou_vision_format.py tests/test_visual_vars_llm_node.py tests/test_attr_llm_disambiguate.py tests/test_sentry_setup.py tests/test_sentry_noise.py tests/test_mxou_balance_precheck.py tests/test_draft_ai_endpoint.py tests/test_draft_ai_surface.py tests/test_config_service.py tests/test_attr_defaults_wave1.py tests/test_language_routing.py tests/test_model_name_9048.py tests/test_attribute_fill_v016.py tests/test_audit_a_fixes.py -q` |
| 12b | worker 全量（需本地 PG） | `cd worker && PGDATABASE_URL="postgresql://postgres:ozon123@localhost:5433/ozon" PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/ -q` |
| 12c | 无残留旧模型名 | `grep -rn '"deepseek-v4-flash"' worker/src worker/config`（应空） |
| 12d | 前端构建不回归 | `cd webui && bun run build`（本次未动前端，跑通即可） |

> ⚠️ **本地 PG 注意**（2026-09-01 实测）: 当前 `deploy-postgres-1` 容器实际密码是
> `ozon123`（非 AGENTS.md 写的 `localdev123`，docker inspect 实证）。`category_tree_nodes`
> 已有 7992 行（无需重新导入），但 **`logistics_rates` 为空**（0 行）——全量跑前先
> `python scripts/import_logistics.py` 或 init_data 导入，否则费率相关用例会走兜底费率失败。
> 另: 任务表仅 1 条 `pending_moderation`（非 pending/failed/running），无 zombie 误激活风险。

### Step 13 — git tag + push（触发 CD 双链路）

```bash
git add VERSION skill/VERSION deploy/skill/VERSION skill/SKILL.md CHANGELOG.md AGENTS.md
git commit -m "chore(release): v0.64.0 视觉模型切换发版（四源同步）"
git tag v0.64.0 && git push origin dev && git push origin v0.64.0
```

### Step 14 — 确认 CD 两个 workflow success

- **build-skill.yml**: 4 平台 × 14 模块二进制编译（20-30 分钟）
- **cd.yml**: Docker 镜像 + Release + COS 部署包 + skill 二进制包
- 确认 `dist/SKILL.md` frontmatter == v0.64.0（skill/VERSION 覆写链路）

### Step 15 — 服务器升级

```bash
# 服务器上
cd deploy && bash cos-update.sh   # worker 升级（cos-update.sh 不覆盖 .env）
# skill 用户端由 updater 自动更新
```

### Step 16 — 上线后观测（1-3 天）

| 观测项 | 方式 |
|---|---|
| 类目错放是否下降 | Sentry `category_match` 相关 issue + 上架后 Ozon 后台类目抽查 |
| 属性填充率 | `attr_match_log.attempted_fill_rate`（即时回归）+ fetch-back verified_fill_rate（月度） |
| 新模型是否报错 | Sentry 搜 `vision-exp` / model 不存在 / 格式错误 |
| MXOU 计费 | 单任务 LLM 调用成本对比（vision 模型单价/图片 token 消耗） |
| 消歧/推断是否收敛 | `_infer_attrs_from_vision`/disambiguate 命中率日志（logger.info 已埋点） |

---

## 3. 风险与回滚

| 风险 | 等级 | 缓解 |
|---|---|---|
| vision 模型在 MXOU 侧不可用/报 model 不存在 | 中 | `call_mxou_chat_api` 模型名是函数默认值 + config 可配，回滚只需 config 改回 `deepseek-v4-flash`（config 热加载，无需重建镜像） |
| 图片 URL 对 MXOU 不可达（需 Ozon 外网可达或 COS 加速） | 中 | `_infer_attrs_from_vision`/`_llm_rank_categories` 传图失败静默降级文本路径（有图时启用，无图/异常时原逻辑兜底）；`_rewrite_payload_images_to_accelerate` 已做 COS 加速域重写 |
| 视觉推断填错值（幻觉） | 中 | 字典属性走 `search_dictionary_values + unique_or_none` 确定性链（LLM 只出候选值不直接写 dict_id）；自由文本含中文跳过；属性仍过 validate 节点校验 |
| LLM 调用成本上升（多图 token） | 低 | 图片上限 4 张（类目/消歧/推断均 ≤3），仅未填充属性才触发推断 |
| 消歧延迟拖慢任务 | 低 | 超时 30s + abstain 跳过；仅多候选且带图才触发，命中率不足可关 `enabled` |
| 全量回滚 | — | `git revert v0.64.0` 或服务器 `cos-update.sh` 回滚上一部署包（v0.63.0，含 webui） |

---

## 4. 执行清单（checklist）

- [ ] Step 9: commit 实施代码（28 文件: 11 src + 9 config + 8 tests，git status 已核）
- [ ] Step 10: 四源 bump 0.64.0 + grep 核对 4 输出一致
- [ ] Step 11: CHANGELOG + AGENTS.md 顶部更新
- [ ] Step 12a: worker 定向回归 155 passed（3 个已知失败为 test_mxou_balance_precheck 孤立跑全过的
      test-ordering 污染，非本次改动）
- [ ] Step 12b: worker 全量（先 import_logistics 补 logistics_rates）≥ 1200 passed
- [ ] Step 12c: grep 无残留旧模型名
- [ ] Step 12d: webui build 通过
- [ ] Step 13: tag v0.64.0 push
- [ ] Step 14: build-skill + cd 两 workflow success
- [ ] Step 15: 服务器 cos-update.sh
- [ ] Step 16: 上线后 Sentry/attr_match_log/计费观测
