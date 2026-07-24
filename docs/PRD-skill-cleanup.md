# PRD: Skill 代码库清理 + 架构优化

## 背景

Skill 代码库经过多轮快速迭代，积累了以下技术债务：
- cloud_probe.py 膨胀到 3155 行，22% 是死代码
- 命名冲突（ConfigError/AuthError 各定义两次）
- 引用不存在的模块（scripts.lib.pipeline）
- 14 个辅助文件未编译为二进制，业务逻辑暴露
- 环境变量 fallback 路径与新 config 系统混用

Agent 在使用 Skill 时频繁遇到 bug，根本原因是代码质量问题。

## 目标

1. **消除死代码**：删除所有未调用的函数，减少 22% 的 cloud_probe.py 体积
2. **统一命名**：消除 ConfigError/AuthError 命名冲突
3. **修复 import**：移除不存在的模块引用
4. **扩大编译范围**：核心业务逻辑全部编译为二进制
5. **清理 API 一致性**：统一使用新 config API，移除旧 fallback

## 非目标

- 不拆分 cloud_probe.py 为多个模块（P2，后续 PR）
- 不模板化生图节点（P2，后续 PR）
- 不修改 Worker 端代码

## 执行计划

### Phase 1：删除死代码（P0）

**cloud_probe.py 删除 16 个死函数（-700 行）**：

| 函数 | 行号 | 行数 | 原因 |
|------|------|------|------|
| `_cloud_get` | 288 | 25 | 从未调用 |
| `_extract_variant_label` | 876 | 55 | 从未调用 |
| `_refresh_browser_session` | 1528 | 40 | 从未调用 |
| `publish_graph_envelope` | 1689 | 80 | 从未调用 |
| `_graph_envelope_to_ctx` | 1614 | 70 | 仅被 publish_graph_envelope 调用 |
| `_verify_product_health` | 2580 | 130 | 从未调用 |
| `_fix_price_to_target` | 2710 | 20 | 从未调用 |
| `_fix_dimensions` | 2733 | 30 | 仅被 _verify_product_health 调用 |
| `_fix_attribute_value` | 2758 | 35 | 仅被 _verify_product_health 调用 |
| `_fix_description_and_tags` | 2789 | 25 | 仅被 _verify_product_health 调用 |
| `check_all_tasks` | 2884 | 3 | 从未调用 |
| `poll_pipeline_task` | 2889 | 55 | 从未调用 |
| `refresh_product` | 1887 | 25 | 从未调用（旧 n8n 管线） |
| `find_supply` | 1921 | 20 | 从未调用（旧 n8n 管线） |
| `publish_variant_product` | 2052 | 12 | 从未调用 |
| `analyze_ozon_product` | 2079 | 100 | 从未调用 |
| `verify_product_quality` | 2190 | 40 | 从未调用 |

**其他文件清理**：
- `_const.py`：删除 `SKILL_NAME`、`DEFAULT_OZON_CURRENCY`（未使用）
- `_errors.py`：删除 `ERR_MISSING_CONFIG`（未使用）
- `config_store.py`：删除 `remove_store`、`set_default_store`、`remove_setting`（未使用）
- `reference_images.py`：删除 `select_reference_images`、`merge_followup_reference_images`（未使用）
- `compile.py` AUX_FILES：移除 `scripts/lib/update.py`（文件不存在）

### Phase 2：统一命名（P0）

**ConfigError 统一**：
- 保留 `_errors.py` 的 `ConfigError(SkillError)` 作为标准定义
- `ak_1688_client.py` 的 `ConfigError(Exception)` 改名为 `AkConfigError(Exception)`
- 更新 ak_1688_client.py 中所有 `raise ConfigError` 和 `except ConfigError`

**AuthError 统一**：
- 保留 `config_store.py` 的 `AuthError(Exception)` 作为标准定义
- `ak_1688_client.py` 的 `AuthError(ApiError)` 改名为 `AkAuthError(ApiError)`
- 更新 ak_1688_client.py 中所有 `raise AuthError` 和 `except AuthError`

### Phase 3：修复 import（P0）

- `cloud_probe.py`：删除 `from scripts.lib.pipeline import ...` 的 try/except 块（3 处）
- `cloud_probe.py`：删除 `from scripts.lib.update import ...` 的 try/except 块（已做）
- `cloud_probe.py`：删除未使用的 `LOGS_DIR` import
- `cloud_probe.py`：删除未使用的 `_read_task_log` import
- `batch_test.py`：删除 `load_env_file` import 和调用（no-op）

### Phase 4：扩大编译范围（P1）

**新增到 COMPILE_FILES（4 个文件）**：
- `capabilities/browser_probe/service.py` — CDP 探针、反检测（最高优先级）
- `lib/ak_callback.py` — AK OAuth 流程（stdlib only，零风险）
- `lib/reference_images.py` — 1688 CDN 过滤规则（stdlib only，零风险）
- `lib/image_preprocessor.py` — 图片预处理（Pillow，低风险）

**更新 compile.py**：
- COMPILE_FILES 从 5 个增加到 9 个
- AUX_FILES 移除 update.py
- 添加 stealth.py 到 COMPILE_FILES（反检测 JS）

### Phase 5：清理 API 一致性（P1）

- `ak_1688_client.py` `_signature_headers()`：改用 `get_ali_1688_ak()` 替代 `load_config()`
- `cloud_probe.py`：删除 `_get_token()` 冗余函数，统一用 `_get_mxou_token`
- `ak_callback.py`：移除 `.env` 写入逻辑，改用 `set_ali_1688_ak()`
- `batch_test.py`：移除 `OZON_CLIENT_ID`/`OZON_API_KEY` 环境变量 fallback

## 验收标准

1. `python3 -c "import ast; [ast.parse(open(f).read()) for f in ...]"` 全部通过
2. `python3 scripts/cli.py check` 正常运行
3. `python3 scripts/cli.py list_stores` 正常运行
4. `python3 compile.py` 编译成功（9 个 .so/.pyd）
5. CI 构建通过（macOS arm64/x86_64 + Windows，Python 3.12/3.13）
6. 无命名冲突（grep ConfigError/AuthError 只出现一次定义）

## 风险

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| 删除的函数被外部调用 | 运行时崩溃 | 每个函数已用 grep 确认无调用 |
| 编译后 Playwright/CDP 异常 | CDP 功能失效 | 编译后完整测试 check 命令 |
| API 改名导致下游 break | import 错误 | 全局搜索替换，不留遗漏 |

## 回滚方案

所有改动在 `refactor/skill-cleanup` 分支，合入 main 前需 PR review。
如需回滚：`git revert <commit>` 或直接不 merge PR。

## 执行顺序

1. Phase 1（删除死代码）→ 提交
2. Phase 2（统一命名）→ 提交
3. Phase 3（修复 import）→ 提交
4. Phase 4（扩大编译）→ 提交 + CI 验证
5. Phase 5（清理 API）→ 提交 + CI 验证
6. 创建 PR → Review → Merge to main
