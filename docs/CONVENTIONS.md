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
