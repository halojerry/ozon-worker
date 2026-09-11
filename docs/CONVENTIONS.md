---
title: 开发规范
purpose: 分支命名、commit 规范、发版流程等工程纪律
applies-version: ">=v0.2.0"
last-updated: 2026-09-11
owner: docs-gov
depends: []
status: active
---

# 开发规范

## 分支命名

| 前缀 | 用途 | 示例 |
|------|------|------|
| `feat/` | 新功能 | `feat/worker-hardening` |
| `fix/` | Bug 修复 | `fix/variant-merge` |
| `refactor/` | 重构 | `refactor/dependency-cleanup` |
| `docs/` | 文档 | `docs/logging-guide` |
| `hotfix/` | 紧急修复 | `hotfix/auth-bypass` |

**规则**:
- 分支名用小写英文 + 短横线
- 从 `main` 创建，合并回 `main`
- 合并后删除分支

## Commit Message 规范

格式: `<type>(<scope>): <中文描述>`

| type | 用途 |
|------|------|
| `feat` | 新功能 |
| `fix` | Bug 修复 |
| `refactor` | 重构（不改功能） |
| `docs` | 文档 |
| `chore` | 构建/工具/配置 |
| `test` | 测试 |
| `style` | 格式化（不影响逻辑） |

**scope**（可选）: `worker` / `skill` / `deploy` / `api` / `docs`

**示例**:
```
feat(worker): 结构化 JSON 日志 + trace_id 链路追踪
fix(skill): check_task_status 指向 Worker 而非 n8n
refactor(worker): 依赖清理 27→15，移除 coze-coding-utils
docs: CONTRACT.md v3.0 与实际代码对齐
chore(deploy): 添加 .dockerignore 和 HEALTHCHECK
```

**规则**:
- 中文描述，动词开头
- 不超过 72 字符
- 一个 commit 做一件事
- 不要出现 "fix bug"、"update code" 等无意义描述

## 版本号规范

语义化版本 `MAJOR.MINOR.PATCH`:

| 变更类型 | 版本变化 | 示例 |
|---------|---------|------|
| 不兼容的 API 变更 | MAJOR +1 | 1.0.0 → 2.0.0 |
| 新功能（向后兼容） | MINOR +1 | 0.2.0 → 0.3.0 |
| Bug 修复 | PATCH +1 | 0.2.0 → 0.2.1 |

版本文件: `VERSION`（根目录）
变更记录: `CHANGELOG.md`

## 发版流程

```bash
# 1. 更新 VERSION 文件
echo "0.3.0" > VERSION

# 2. 更新 CHANGELOG.md

# 3. 提交
git add VERSION CHANGELOG.md
git commit -m "chore: release v0.3.0"

# 4. 打 tag
git tag v0.3.0

# 5. 构建镜像
VERSION=0.3.0 docker compose -f deploy/docker-compose.yml build

# 6. 部署
VERSION=0.3.0 bash deploy/deploy.sh
```

## 代码风格

- Python: PEP 8，行宽 120
- Lint: `ruff check src/ --select E,F,W --ignore E501`
- 中文注释和日志消息
- 类型注解（Pydantic model 优先）

## 密钥纪律（2026-09-11 起强制）

用户政策：**密钥绝不进源码库**。仓库 PUBLIC，任何可用凭证（Ozon api_key、MXOU token、
1688 cookie/token、GRSAI key、Supabase service_role JWT 等）不得出现在源码、测试、
文档、注释及 JSON/YAML 资产中——「只是测试值」「只是样例」同样违反。

1. **绝不入库（含测试夹具）**：假值用非关键词常量名 + 占位形态（防 gitleaks
   generic-api-key 邻接匹配）。先例：`test_param_mapping_a6`、
   `skill/tests/test_aibuy_search.py` 的 `_SIGN_TOK`（32 位假 hex，签名公式测试）；
   模板文件用 `$VAR` 占位（先例 `worker/assets/error-handler.json` 的
   `$SENTRY_KEY`/`$SENTRY_URL`）。
2. **运行时一律 env / 参数 / 凭证库传入**（credentials 表 AES-GCM、settings.json、
   环境变量），代码不落字面量。
3. **CI 双闸**（`.github/workflows/ci.yml` secret-scan job）：①gitleaks-action
   PR 增量扫描；②全树 `gitleaks detect --no-git --redact -v --exit-code=2`
   （拦存量——增量扫不到的历史密钥在此拦截）。测试级第三闸
   `worker/tests/test_leak_guard_in_tree.py`：前缀指纹扫描 `git ls-files`
   全清单，锁定放行登记只减不增（ratchet）。
4. **轮换 SOP**：发现入库 → 立即平台侧轮换（各控制台作废旧 key）→
   `git filter-repo` 清历史（破坏性操作，需 owner 拍板 + 协作者重新 clone）→
   GitHub support 清 PR/fork 缓存。
5. **.gitignore 现状**：`skill/data/`、`.env`/`.env.*`（覆盖 `deploy/.env`）、
   `deploy/backups/` 已覆盖。⚠️ 例外欠账：`deploy/skill/data/config/settings.json`
   （运行时配置，含真实 mxou_token）仍被 git 跟踪，待 `git rm --cached` + 补
   ignore 规则后失效本地副本。
6. **放行登记只减不增**：现存已知残留登记于 `.gitleaks.toml` `[allowlist].paths`
   + `worker/tests/test_leak_guard_in_tree.py` 的 `KNOWN_REMAINING`（两处均受
   该测试锁定，清欠一批删一条，禁止新增）。审计文档（`docs/audit/`）允许
   ≤8 字符级指纹引用（事件存证需要），完整密钥仍绝不允许。
