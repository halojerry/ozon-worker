# dsh-pounding-guard

dsh 侧的三级安全门控插件（「老板眼皮底下 vs 黑盒」）。订阅 `tools/pre-execute`，对 `mcp__pounding__*` 工具按 read/write/destructive 分级审批。

> 依据 dsh 官方契约（0.1.0-rc.7）：`PreToolDecision = {kind:'allow'} | {kind:'deny',reason} | {kind:'ask',reason?}`，`ask` 由 harness 的 approval seam 解析。

## 三级安全

| 级 | 行为 | 工具 |
|---|---|---|
| `read` | 黑盒直跑（next 放行）| check/list_stores/search/probe/image_search/category/follow/discover/discover_multi/seller/queries/query |
| `write` | 返回 ask → 审批 | set_store/set_token/set_ak/get_ak/graph/update |
| `destructive` | 返回 ask → 审批 | cleanup |

动态升级：`read` 工具带 `auto_submit`/`to_box`/`submit` flag 时 → `write`。

## 挂载（3 段缺一不可）

见 `cordis.patch.yml`：
1. `@deepseek-ai/dsh-user-approval`（审批机制，否则 ask fail-closed）
2. `@dsh-external/dsh-pounding-guard`（本插件）
3. `@deepseek-ai/dsh-mcp-client`（pounding-mcp 工具，被门控对象）

> 注意：approval 还需要一个 answerer（ACP bridge / host adapter），否则 `unavailable` → 自动拒绝。

## 构建

```bash
pnpm install
pnpm run build   # tsc → lib/index.js
```

## 对应文档

- 工具清单 + 安全分级：`../docs/ozonharness/MCP-TOOLS.md`
- 审批机制契约：`../docs/ozonharness/REFERENCES.md`（interception-extension-points / approval-seam）
