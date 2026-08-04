# Worker 日志系统文档

> 版本: v1.0 | 日期: 2026-07-18

## 架构概览

```
Skill (客户本地)                    Worker (云端 Docker)
    │                                   │
    ├─ AuditLogger (JSONL per-task)     ├─ 结构化 JSON stdout
    │  → data/logs/{task_id}.jsonl      │  → docker compose logs
    │                                   │
    └─ submit_envelope() ─────────────► │  trace_id 自动生成
                                        │
                                        ├─ log_task_event("submitted")
                                        ├─ log_task_event("started")
                                        │
                                        ├─ node.pricing  (▶ 开始 → ✅ 完成/❌ 失败)
                                        ├─ node.prepare  (▶ 开始 → ✅ 完成/❌ 失败)
                                        ├─ node.upload   (▶ 开始 → ✅ 完成/❌ 失败)
                                        │
                                        ├─ ozon.api (POST /v3/product/import → 200)
                                        │
                                        └─ log_task_event("completed")
```

## 四种审计日志

| 类型 | logger 名 | 触发时机 | 关键字段 |
|------|-----------|----------|----------|
| **任务生命周期** | `task.lifecycle` | 任务状态变更 | `event`, `task_id`, `user_id` |
| **节点执行** | `node.{name}` | 节点开始/结束 | `node_name`, `duration_ms`, `output` |
| **Ozon API** | `ozon.api` | 每次 Ozon API 调用 | `endpoint`, `status_code`, `duration_ms` |
| **链路追踪** | 所有日志自动携带 | — | `trace_id`, `task_id`, `user_id` |

## 日志格式

### JSON 格式（生产环境，`LOG_FORMAT=json`）

```json
{
  "ts": "2026-07-18T10:00:05.123456+00:00",
  "level": "INFO",
  "logger": "task.lifecycle",
  "msg": "📋 任务 submitted: 550e8400-e29b-41d4-a716-446655440000",
  "trace_id": "a1b2c3d4e5f6",
  "task_id": "550e8400-e29b-41d4-a716-446655440000",
  "user_id": "123",
  "data": {
    "event": "submitted",
    "priority": 0,
    "timeout_seconds": 1800
  }
}
```

### 可读格式（本地开发，`LOG_FORMAT` 为空）

```
10:00:05 [INFO] task.lifecycle: [a1b2c3d4/550e8400] 📋 任务 submitted: 550e8400
```

## 字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| `ts` | string | ISO 8601 时间戳（UTC） |
| `level` | string | DEBUG / INFO / WARNING / ERROR |
| `logger` | string | 日志来源模块 |
| `msg` | string | 人类可读消息 |
| `trace_id` | string | 请求链路 ID（12 位 hex，贯穿整个请求生命周期） |
| `task_id` | string | 任务 UUID |
| `user_id` | string | 用户 ID（从 token 解析） |
| `node_name` | string | 当前执行的管线节点名 |
| `data` | object | 附加结构化数据（因日志类型而异） |

## 任务生命周期事件

| event | 含义 | 级别 | 附加字段 |
|-------|------|------|----------|
| `submitted` | 任务入队 | INFO | `priority`, `timeout_seconds` |
| `started` | 开始执行 | INFO | `priority` |
| `completed` | 执行成功 | INFO | — |
| `failed` | 执行失败 | ERROR | `error_message`, `error_type`, `permanent` |
| `retried` | 自动重试 | WARNING | `retry_count`, `max_retries`, `error_message` |
| `cancelled` | 被取消 | INFO | — |

## 节点执行日志

```json
// 节点开始
{"level":"INFO","logger":"node.pricing","msg":"▶ 节点开始: pricing","node_name":"pricing"}

// 节点完成
{"level":"INFO","logger":"node.pricing","msg":"✅ 节点完成: pricing","node_name":"pricing",
 "data":{"duration_ms":2341.5,"output":{"pricing_info":{"price_rub":350}}}}

// 节点失败
{"level":"ERROR","logger":"node.pricing","msg":"❌ 节点失败: pricing: timeout","node_name":"pricing",
 "data":{"duration_ms":30000.0,"error_type":"TimeoutError"},
 "exception":{"type":"TimeoutError","msg":"timeout","traceback":["..."]}}
```

## Ozon API 调用日志

```json
// 成功
{"level":"INFO","logger":"ozon.api","msg":"Ozon API POST /v3/product/import → 200",
 "data":{"method":"POST","endpoint":"/v3/product/import","status_code":200,"duration_ms":1234.0,
         "request":{"items_count":3,"offer_ids":["sku_001","sku_002"]},
         "response":{"task_id":172549793}}}

// 失败
{"level":"WARNING","logger":"ozon.api","msg":"Ozon API POST /v3/product/import → 400",
 "data":{"method":"POST","endpoint":"/v3/product/import","status_code":400,"duration_ms":890.0,
         "request":{"items_count":1},"error":"{\"code\":\"INVALID_ATTRIBUTE_VALUE\",...}"}}
```

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `LOG_FORMAT` | 空 | `json` = JSON 格式（生产），空 = 可读格式（本地） |
| `LOG_LEVEL` | `INFO` | 日志级别：DEBUG / INFO / WARNING / ERROR |
| `LOG_FILE` | 空 | 可选：同时写文件路径（如 `/app/logs/worker.log`） |

### Docker 配置示例

```yaml
# deploy/docker-compose.yml
environment:
  - LOG_FORMAT=json
  - LOG_LEVEL=INFO
```

## 常用查看命令

### 基础查看

```bash
# 查看最近 50 条日志
docker compose logs worker --tail=50

# 实时跟踪
docker compose logs worker -f

# 查看指定时间之后的日志
docker compose logs worker --since 2026-07-18T10:00:00
```

### 按 trace_id 过滤（追踪单次请求全链路）

```bash
# 找到某个任务的 trace_id（从提交响应中获取）
# 然后过滤该 trace_id 的所有日志
docker compose logs worker | grep "a1b2c3d4e5f6"
```

### 按日志类型过滤

```bash
# 只看任务生命周期
docker compose logs worker | grep '"logger":"task.lifecycle"'

# 只看 Ozon API 调用
docker compose logs worker | grep '"logger":"ozon.api"'

# 只看某个节点（如 pricing）
docker compose logs worker | grep '"logger":"node.pricing"'

# 只看错误
docker compose logs worker | grep '"level":"ERROR"'

# 只看警告
docker compose logs worker | grep '"level":"WARNING"'
```

### 按内容过滤

```bash
# 查找特定任务的所有日志
docker compose logs worker | grep "550e8400"

# 查找特定用户的所有任务
docker compose logs worker | grep '"user_id":"123"'

# 查找失败的任务
docker compose logs worker | grep '"event":"failed"'

# 查找重试的任务
docker compose logs worker | grep '"event":"retried"'

# 查找 Ozon API 4xx/5xx 错误
docker compose logs worker | grep '"logger":"ozon.api"' | grep -E '"status_code":[45]'
```

### JSON 解析（需要 jq）

```bash
# 格式化输出所有 JSON 日志
docker compose logs worker --tail=100 | jq '.'

# 只看任务生命周期事件
docker compose logs worker | jq 'select(.logger == "task.lifecycle")'

# 只看耗时超过 5 秒的节点
docker compose logs worker | jq 'select(.data.duration_ms > 5000)'

# 统计每种事件的数量
docker compose logs worker | jq -r '.logger' | sort | uniq -c | sort -rn

# 找出所有失败的任务 ID
docker compose logs worker | jq 'select(.data.event == "failed") | .task_id'

# 查看某个 trace_id 的完整链路
docker compose logs worker | jq 'select(.trace_id == "a1b2c3d4e5f6")'
```

### 性能分析

```bash
# 找出最慢的 10 个节点执行
docker compose logs worker | jq 'select(.data.duration_ms != null) | {logger, msg, duration_ms: .data.duration_ms}' | jq -s 'sort_by(-.duration_ms) | .[:10]'

# 统计 Ozon API 各端点的平均耗时
docker compose logs worker | jq 'select(.logger == "ozon.api") | {endpoint: .data.endpoint, ms: .data.duration_ms}' | jq -s 'group_by(.endpoint) | map({endpoint: .[0].endpoint, avg_ms: (map(.ms) | add / length), count: length})'
```

## 代码中使用日志

### 获取 logger

```python
from utils.logger import get_logger

logger = get_logger(__name__)
logger.info("处理开始")
```

### 设置链路上下文

```python
from utils.logger import set_trace_context

# 在请求入口设置（自动注入到后续所有日志）
set_trace_context(trace_id="abc123", task_id="uuid", user_id="user1")
```

### 记录任务生命周期

```python
from utils.logger import log_task_event

log_task_event("submitted", task_id=task_id, user_id=user_id, priority=0)
log_task_event("completed", task_id=task_id)
log_task_event("failed", task_id=task_id, error_message="timeout", permanent=True)
```

### 记录 Ozon API 调用

```python
from utils.logger import log_ozon_api_call

log_ozon_api_call(
    method="POST",
    endpoint="/v3/product/import",
    status_code=200,
    duration_ms=1234.5,
    request_summary={"items_count": 3},
    response_summary={"task_id": 12345},
)
```

### 使用 audit_node 装饰器

```python
from utils.logger import audit_node

@audit_node("pricing")
async def pricing_node(state):
    # 自动记录：▶ 节点开始 → ✅ 节点完成 / ❌ 节点失败
    # 自动记录：耗时(ms)、输出摘要、异常信息
    ...
```

## 日志文件管理

- **stdout 日志**: Docker 管理，通过 `docker compose logs` 查看
- **文件日志**（如果配置了 `LOG_FILE`）: RotatingFileHandler，50MB × 5 个备份
- **Skill AuditLogger**: `data/logs/{task_id}.jsonl`，7 天自动清理

## Sentry 错误监测（v0.23）

Worker 侧接入 Sentry（`utils/sentry_setup.py`），上报任务执行异常与超时，自动带
`task_id` / `tenant_id` tag 与 extra，方便按用户/任务定位问题。

```bash
# deploy/.env 配置（SENTRY_DSN 为空则完全禁用）
SENTRY_DSN=https://...@o<org>.ingest.us.sentry.io/<project>
SENTRY_ENV=production        # 环境标签（本地测试建议 local）
SENTRY_TRACES_SAMPLE_RATE=0.1
```

覆盖范围：
- FastAPI 端点异常（SDK 自动集成，HTTP 500/未捕获异常）
- LangGraph 任务执行异常与超时（`task_processor.py` 异常分支 `capture_task_error`）
- ERROR 级日志自动捕获（sentry-sdk logging 集成；测试进程自动跳过，避免测试噪音）

验证：
```bash
docker compose exec worker python -c "from utils.sentry_setup import init_sentry; print(init_sentry())"
# True = 已启用；容器日志出现「Sentry 监测已启用」
```

## 故障排查流程

```
1. 用户反馈上架失败
   ↓
2. 查找任务 ID（从用户或 Skill 日志）
   ↓
3. docker compose logs worker | grep "<task_id>"
   ↓
4. 找到 trace_id
   ↓
5. docker compose logs worker | grep "<trace_id>"
   → 看到完整链路：提交 → 各节点执行 → Ozon API 调用 → 结果
   ↓
6. 找到失败节点或 Ozon API 错误
   ↓
7. 根据 error_message 和 exception.traceback 定位根因
```
