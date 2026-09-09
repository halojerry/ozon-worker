# session-sync — Ozon 卖家会话代管（worker 端加密存储 + 服务端直调）

> v0.70 批次 C 新增。对标竞品 bindShopCookie：把本机 Chrome 的 seller.ozon.ru
> 登录会话交给 worker 代管（AES-256-GCM 加密落库），worker 服务端可用该会话
> cookie 直调 Ozon 卖家内部接口（what_to_sell 运营数据等），不再依赖本机 Chrome 在线。

## 命令

```bash
# 收割并上传（核心场景）
python3 scripts/cli.py session-sync --credential-id <uuid>

# 只查 worker 侧会话状态（脱敏：只回 cookie 名单 + 状态 + harvested_at）
python3 scripts/cli.py session-sync --credential-id <uuid> --status

# 本地 worker 调试
WORKER_URL=http://localhost:8080 python3 scripts/cli.py session-sync --credential-id <uuid>
```

`--credential-id` 是 worker 店铺凭证 ID（`worker /api/v1/credentials` 列表返回的
UUID，或 WebUI 店铺管理页可见）。**cookie 明文只出现在上传请求里**；命令输出与
worker 端任何接口都只回名单与状态，绝不回显 cookie 值。

## 何时执行（使用时机）

1. **worker 侧会话过期提示**：worker `/api/v1/analytics/what-to-sell` 返回
   `409 session_expired`，或任务/报告提示「请在 skill 重新执行 session-sync」。
2. **本机直调判废提示**：`queries` / 跟卖富化等静默 cookie 直调反复 401/DataDome，
   且 `check` 显示 seller.ozon.ru 登录态刚恢复过——先 `session-sync` 把新会话
   推给 worker。
3. **首次启用会话代管**：给某店铺开通服务端运营数据查询前，同步一次会话。
4. 换机器 / 重装 Chrome / 重新登录 seller 后台之后，建议重同步。

## 失效判定与重同步闭环（worker 侧联动，勿凭记忆改）

- worker 服务端直调遇 **401 / 403 / 登录 302** → 自动把该会话标记 `expired`
  并返回 `409 session_expired`；此后该会话 fast-fail，不再烧直调。
- **换 CREDENTIAL_MASTER_KEY 后旧会话解密失败是预期行为**：worker 同样标
  `expired` 返回 None，不会返回垃圾数据。
- 恢复方式只有一条：在本机 Chrome 打开一次 seller.ozon.ru 卖家后台（确保登录），
  然后重跑 `session-sync --credential-id <uuid>`。上传成功即回到 active。

## 红线

- 缺 `sc_company_id`（核心 cookie）时命令 fail-fast exit 2，**不上传半截会话**。
- 不把 cookie 值写进日志、报告、对话回复；汇报用户时只说「N 条 cookie、状态 active」。
- `--status` 只读；删除/撤销会话走 `DELETE /api/v1/credentials/{id}/session`（204）。
