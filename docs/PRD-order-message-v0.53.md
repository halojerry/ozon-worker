# PRD — 消息催评自动化（P2c，对标上品帮 autoMsg 催护照/催取货/索好评）

> 2026-08-16。竞品调研：上品帮「OZON消息」消息模板（催护照/催取货/索好评 3 种内置模板 + 占位符 + 发送记录）、毛子消息模板（俄语原文 + 中文译文）。
> Ozon 端点实测（2026-08-16）：
>   - `/v1/chat/start` {posting_number} → {result.chat_id}（按订单建立聊天）
>   - `/v1/chat/send/message` {chat_id, message}（message 1-1000 字符，俄语）
>   - `/v3/chat/list`（已有聊天列表）

## 一、目标

订单列表行「发消息」→ 弹窗选模板（催护照/催取货/索好评，内置俄语文案 + 占位符替换）→ chat/start + send/message → 发送记录本地存。

## 二、设计

### 2.1 内置模板（俄语 + 中文说明，对标毛子原文）
| key | 名称 | 文案（俄语，占位符 [货件编号]/[商品名称]） |
|---|---|---|
| passport | 催护照 | Здравствуйте! Товар, который вы покупаете: [货件编号] ([商品名称]), Вы еще не заполнили паспорт... |
| pickup | 催取货 | Здравствуйте! Ваш товар [货件编号] прибыл в пункт выдачи... |
| review | 索好评 | Здравствуйте! Вы получили товар [货件编号]? Если довольны, оставьте отзыв... |

占位符替换：`[货件编号]`→posting_number，`[商品名称]`→products[0].name（截断）。

### 2.2 Worker `order_service` 新增
```python
def send_order_message(tenant_id, posting_number, message, credential_id=None) -> dict:
    """1. /v1/chat/start {posting_number} → chat_id
       2. /v1/chat/send/message {chat_id, message}
       返回 {ok, chat_id, message}"""

def get_message_templates() -> list[dict]:
    """内置模板列表（静态，纯读）：[{key, name, text}]"""
```
- 消息长度校验（1-1000 字符，超长截断）
- 失败：无默认店铺 400 / Ozon 502

### 2.3 发送记录（本地 `order_messages` 表）
```sql
CREATE TABLE order_messages (
    id UUID PK, tenant_id, posting_number, template_key, message,
    chat_id, status(pending/sent/failed), error, created_at
)
```
- 记录每次发送（含失败），WebUI 可查

### 2.4 路由
```
GET  /api/v1/orders/message-templates          → [{key, name, text}]（内置）
POST /api/v1/orders/{posting_number}/message   → send_order_message（记录）
GET  /api/v1/orders/messages                   → 发送记录列表（租户隔离）
```

### 2.5 WebUI（Orders.tsx）
- 行操作「发消息」→ 弹窗：模板下拉（3 种内置）+ 文案预览（可编辑）+ 发送
- 发送成功/失败提示 + 刷新
- 「消息记录」入口：查看本账号发送记录（时间/订单/模板/状态）

## 三、测试
- `test_order_messages.py`：chat/start + send 两步断言、模板列表、消息长度校验、记录 upsert、无默认 400/502

## 四、DoD
1. 发送闭环 worker 单测通过（chat/start → send/message 请求体断言）
2. WebUI 发消息弹窗（模板 + 预览编辑）+ 发送记录
3. worker 全量回归不破

## 五、实施
T0 order_messages 表 + send_order_message + 模板 → T1 路由 + 记录 → T2 worker 测试 → T3 WebUI 弹窗 + 记录 → T4 版本 + 提交
