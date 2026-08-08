# 实施计划 — E2E 修复 + Sentry 观测增强（v0.32 候选）

> 状态：**草案（依赖 PRD §7 六个拍板项）** — 对应 PRD：`docs/PRD-e2e-fixes-20260808.md`。
> 所有改动遵循 TDD（先写失败测试 → 最小实现 → 回归）。每 wave 独立提交。
> 已通过 codegraph 验证的代码路径见各 wave「已验证代码点」。

---

## 前置：6 个拍板项（推荐默认值标注 ✅）

| # | 拍板项 | 推荐默认 | 若选其他 |
|---|---|---|---|
| 1 | P0-1 属性合并顺序 | ✅ 竞品优先 → 1688 补缺 → 兜底垫底 | 1688 优先（属性更全但可能不匹配 Ozon 类目） |
| 2 | P0-2 8229 标题交叉验证 | ✅ 接受误伤风险（标题无类目词则跳过 8229） | 不做交叉验证（仅黑名单+竞品优先） |
| 3 | P3 语言检查降 warning | ✅ 降级 + fingerprint 聚合 | 保持 error（噪音不解决） |
| 4 | mxou 401 排查 | ✅ 本次一并排查（疑似 API key 过期/限额） | 单开任务 |
| 5 | S4 默认店铺重置 | ✅ 重置为主店铺 4718259 | 保持现状 |
| 6 | 失败任务重跑验证 | ✅ 修复后本地重跑 3 个失败跟卖 | 不上架验证（仅单测） |

> ⚠️ 拍板前不写任何生产代码。本文档仅作执行蓝图。

---

## Wave 1 — P0-1 跟卖属性合并（worker）

**已验证代码点**：`worker/src/graphs/nodes/assemble_ozon_product_node.py:269-304`
- L269 `follow_attrs_raw = state.final_attributes`（仅竞品属性）
- L294 `if not attrs_for_payload: _build_hardcoded_attributes`（兜底）
- **问题**：完全不读 `draft.attributes`（1688 中文）与 `draft.ozon_attributes`（竞品俄语）

**改动**：
1. `_assemble_follow_sell` 内新增 1688 属性合并：
   - 读 `draft.get("attributes")`（中文 1688）+ `draft.get("ozon_attributes")`（俄语竞品）
   - 俄语竞品属性直接进 `attrs_for_payload`（已是 Ozon 格式或可按 schema 转换）
   - 中文 1688 属性走现有 `_match_product_attr`（L1600）+ 语言路由（v0.29）→ 映射到 schema
   - 合并去重（attribute_id 优先竞品），再拼兜底
2. **回归测试**：新增 `worker/tests/test_follow_attr_merge.py`（断言：竞品 5 属性 + 1688 可映射属性全部进入 final_attributes；无 schema 时走兜底）

**验证**：单测绿 + 本地 Docker 重跑落地扇类 follow（属性数 ≥8）

---

## Wave 2 — P0-2 8229 类型匹配修复（worker）

**已验证代码点**（codegraph 实证）：
- `worker/src/graphs/nodes/assemble_ozon_product_node.py:1600` `_match_product_attr` —— 1688 属性→schema 映射唯一入口（`_build_items_deterministically` L1564 调用）
- `assemble_ozon_product_node.py:1723` `product_value = _match_product_attr(attr_name_cn)` —— 用 **schema 属性名（attr_name_cn）** 去匹配 1688 属性；8229「类型」会命中 1688 的「专利类型」（日志实证）
- `assemble_ozon_product_node.py:1725-1741` 字典属性匹配 + `_clean_dict_value` 中文置空逻辑（v0.29）
- **修复点明确**：`_match_product_attr` 内（或调用处 L1723）对 `attr_id=8229` 特判——排除「专利类型/光源类型/开关类型/风扇类型」等干扰键；竞品俄语属性（`ozon_attributes`）优先映射 8229

**改动**：
1. `_match_product_attr` 或 L1723 增加 8229 干扰属性名黑名单（`专利类型/光源类型/开关类型/风扇类型/造型类型` 等）
2. 竞品 `ozon_attributes`（俄语 `Тип вентилятора` 等）优先填 8229（走 RU 字典值匹配，v0.29 语言路由）
3. 标题交叉验证：type_id 匹配结果与标题关键词（桌面/手持/挂脖/落地/风扇灯）一致性检查，不一致跳过
4. 仍不匹配 → 跳过 + `attr_id=8229` 结构化日志（配合 Wave 5 Sentry）
5. **回归测试**：`test_attribute_fill_v016.py` 增加 8229 干扰属性用例（断言「专利类型」不映射到 8229）

**验证**：单测绿 + 重跑无线迷你风扇 follow（不再 DESCRIPTION_DECLINE attr=8229）

---

## Wave 3 — P0-3 + P1 9782 保留 + retry 快照（worker）

**已验证代码点**：
- `worker/src/utils/attribute_utils.py:99` `get_safe_hazard_default`
- `worker/src/graphs/nodes/prepare_ozon_upload_node.py:1888-1894` 9782 安全守卫
- `worker/src/graphs/validation_retry_loop.py:1771` `revalidate_node`（重建属性）
- `worker/src/utils/task_processor.py:305-324` `_is_failed` 分支（上传失败只写 DB 不上报 Sentry）

**改动**：
1. **P0-3**：prepare 对 9782 —— `state.final_attributes` 中已有有效 dict_id（Не опасен/970661099）→ **保留**；空/非安全值才重走 `get_safe_hazard_default`；仍无 → 跳过
2. **P1**：`ozon_upload_node` 上传成功后快照 `uploaded_attributes`（Ozon 格式）到 state；`revalidate_node` 重建时优先用快照，已存在有效 dict_id 的字典属性不重新 LLM 匹配
3. **回归测试**：`test_hazard_attr_fallback.py` 增加「重试保留」用例；`test_learning_record_gate.py` 增加快照重建用例

**验证**：单测绿 + 重跑驱蚊棒/驱蚊水 follow（9782 不再 error_attribute_values_empty）

---

## Wave 4 — P2 skill 工具链（skill）

**已验证代码点**：
- S1：`skill/scripts/batch_test.py:89-107` `parse_urls_file`（正则 `offer/(\d+)` 不认 m 站）
- S2/S3：`skill/scripts/batch_test.py:308-318`（凭证读 env、worker-url 读 `MXOU_API_BASE`）
- S10：`skill/scripts/batch_test.py:266,277`（finally 内 return）
- **S5 实证**：`apply_analytics_to_candidate`（`skill/scripts/lib/ozon_seller_analytics.py:424`）在 `ozon_discovery.py` 有调用，但 **`ozon_fission.py`（`run_fission` L147）未调用** → 裂变候选无运营数据。修复点：`run_fission` 内对候选调用 `apply_analytics_to_candidate(candidate, metrics)` + `fetch_sales_analytics(cdp, skus)`
- **S6 实证**：`FissionState.should_visit_*`（ozon_fission.py:82-92）只按 visited 去重，**无类目一致性过滤**；`category_consistency` 是 `calculate_blue_ocean_score` 的评分因子（v0.31）但不拦截扩散。修复点：BFS 展开候选时按种子类目（`source_category`/category_id）过滤明显跨类目项

**改动**：
1. **S1**：`parse_urls_file` 兼容 `offerId=(\d+)` query 参数 + `dj.1688.com` 302 解析（S8 合并）
2. **S2**：未传 `--client-id/--api-key` 时自动调 `get_ozon_credentials(store_id or "")`（与 graph 一致）
3. **S3**：`--worker-url` 默认改 `WORKER_URL` 优先，回退 `MXOU_API_BASE`
4. **S10**：重构 finally 内 return（改 try/except 外层返回）
5. **S5**：`run_fission` 候选接入 `fetch_sales_analytics` + `apply_analytics_to_candidate` 富化
6. **S6**：BFS 展开时按种子类目过滤跨类目候选
7. **S7**：fission 的 `fetch_product_info` 复用 `_ensure_ozon_tab` 存活校验（stale tab 修复）
8. **S9**：`cli.py cmd_discover` 导出合并 `match_selected` 结果
9. **回归测试**：新增 `test_batch_test_url_parse.py`（m 站 URL/凭证回退/worker-url 优先级）；`test_fission_e2e_mock.py` 增加「候选有运营数据 + 跨类目过滤」用例

**验证**：`batch_test --urls-file <m站URL文件> --dry-run` 一步通过；discover 裂变候选有运营数据且不过度偏题

---

## Wave 5 — P3 Sentry 观测增强（worker）

**已验证代码点**：
- `worker/src/utils/sentry_setup.py:73-106` `capture_task_event`（tag: task_event/task_id/tenant_id + extras）
- `worker/src/utils/sentry_setup.py:109-136` `capture_task_error`（仅 task_id/tenant_id）
- `worker/src/utils/task_processor.py:305-324` `_is_failed` 分支（**无 capture**）
- `worker/src/utils/task_processor.py:344-362` TimeoutError/Exception（有 capture，但无 failed_stage/error_code）
- `worker/src/utils/logger.py:35-73` trace_id ContextVar（未入 Sentry）

**改动**：
1. `capture_task_error`/`capture_task_event` 增加 kwargs：`failed_stage`/`error_code`/`category_id`/`attr_id`（set_tag）+ `notice`/`upload_status`/`product_id`（set_extra）
2. `task_processor.py:305-324` `_is_failed` 分支补 `capture_task_error`（业务失败上报，带 error_code/failed_stage）
3. `sentry_setup.init` 加 `before_send` hook：统一 `trace_id` 从 logger ContextVar 注入 scope tag
4. fingerprint 策略：`capture_task_event` 语言检查类降 `level="warning"` + fingerprint 按 `task_event + error_code` 聚合
5. FastAPI 全局 `@app.exception_handler(Exception)`：未捕获端点异常 → capture + WorkerErrorCode 映射
6. `mxou_api.call_mxou_chat_api` 加 span（与 image_gen 对齐）
7. 节点流转 `add_breadcrumb`（ProgressCallback 内，节点级）
8. **回归测试**：`test_sentry_setup.py` 同步更新（新 kwargs 签名）

**验证**：
- 单测绿
- 本地 DSN 验证：语言检查类错误聚合为 1 issue + level=warning；任务失败带 error_code/failed_stage 可过滤
- mxou 401 排查结论（拍板项 4）：确认根因（key 过期/限额/配置）并修复

---

## Wave 6 — 端到端回归（全部修复后）

1. worker 全量单测：`cd worker && PYTHONPATH=src python3 -m pytest tests/ -v`
2. skill 相关单测
3. 本地 Docker 重跑 3 个失败跟卖（无线风扇/驱蚊棒/驱蚊水）→ 全部成功 + 属性数 ≥8
4. 类目属性匹配重点验证：8229/9782 不再缺失，属性填充数提升
5. 清理测试痕迹（本地任务表、临时文件）

---

## 提交规范

- 每 wave 一个 commit：`fix(worker): <P0-x 描述>` / `feat(skill): ...` / `feat(obs): Sentry 观测增强`
- 遵循仓库 `docs/CONVENTIONS.md` + `.githooks` pre-commit
- 不 push，等用户确认后统一推送

---

*实施计划 v0.1 — 待 PRD §7 拍板后执行。*
