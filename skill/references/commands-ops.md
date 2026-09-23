# 运维类命令（check / query / report / session-sync / import-cookies / cleanup / update / 配置）

> v0.79 拆分自 command-reference.md（PLAN-agent-ergonomics-v1 D3）。选管线见 routing.md；
> 上架类见 commands-listing.md；选品类见 commands-discovery.md。

## 目录
- [环境检查（check）](#环境检查check)
- [任务查询（query）](#任务查询query)
- [问题上报（report）](#问题上报report)
- [卖家会话代管（session-sync）](#卖家会话代管session-sync)
- [跨浏览器 cookie 导入（import-cookies / probe-win-cookies）](#跨浏览器-cookie-导入import-cookies--probe-win-cookies)
- [凭证配置（set_store / set_token / set_ak / list_stores / get_ak）](#凭证配置set_store--set_token--set_ak--list_stores--get_ak)
- [自动更新（update）](#自动更新update)
- [磁盘清理（cleanup）](#磁盘清理cleanup)
- [调试探针（probe）](#调试探针probe)
- [settings.json 可调参数](#settingsjson-可调参数)

## 环境检查（check）

**触发**：首次使用 / 排错 / 查运行轨迹。

```bash
python3 scripts/cli.py check
python3 scripts/cli.py check --logs              # 列最近 5 个日志文件
python3 scripts/cli.py check --logs <task_id>    # 打印该任务 JSONL 事件轨迹（零 Chrome/网络）
```

- 全量诊断：Chrome / 凭证 / Worker / Ozon API / seller 会话；输出末行 `👉 NEXT:`（v0.79）——失败给修复命令，成功给首条业务命令
- `check --logs <task_id>` 零副作用，适合取证

## 任务查询（query）

**触发**：用户问"任务/上架进度"、"完成了吗"、追问 `graph`/`follow`/`batch_test` 提交后返回的 task_id 状态。

```bash
# 单次查询（非终态返回当前进度，终态打印明细）
python3 scripts/cli.py query 550e8400-e29b-41d4-a716-446655440000

# 轮询直到终态（每 10s 查一次，打印进度中间态；--timeout 默认 900s 超时）
python3 scripts/cli.py query 550e8400-... --watch

# 长任务（生图/审核慢）调大轮询上限
python3 scripts/cli.py query 550e8400-... --watch --timeout 1800
```

- **输入**：`<task_id>`（位置参数；`batch_test --wait` 另走批量轮询）
- **参数**：`--watch`（轮询到终态，每 10s）、`--timeout`（watch 超时秒，默认 900）
- **输出**（非 JSON，人读格式）：`任务 {id}: {status}` + 开始/完成时间 + 重试次数；终态成功 → 产品明细行（OzonID | 售价 | 净利润率 | 审核状态 | 备注 + 采购链接/采购价/运费/类目）；失败 → `❌ 错误: {error_message}`；各终态/中间态出口均带 `👉 NEXT:`（v0.79）
- **status 取值**：`completed`/`failed`/`rejected`/`cancelled`（终态，`rejected`=Ozon 审核被拒）/ `pending`/`running`（非终态）/ `not_found`/`worker_unreachable`/`query_error`
- **执行后验证**：① 终态 `completed` → 确认 `moderate_status` 后再向用户报成功；② `rejected`/`failed` → 按 error-codes.md 引导（看 Ozon 后台拒绝原因 / 可重提）；③ `pending`/`running` 非终态 → 告知预计 10-20 分钟，建议 `--watch` 或稍后重查
- **⚠️ rejected/failed 重提（v0.38 N2）**：终态不占用 SKU 去重名额，可重新提交。重提方式：调 Worker `POST /api/v1/resubmit_task/{task_id}`（请求体带 `token`，复制原载荷 + 重生成图片重新入队）。CLI 暂未内置 resubmit 命令；重提后返回新 task_id，用 `query <新id> --watch` 跟踪。

## 问题上报（report）

**触发**：任务 failed 重试无解 / Ozon 拒审反复 / 未知错误码 / 假成功。

```bash
python3 scripts/cli.py report --title "VALUE_MAX_LIMIT 反复拒单" \
    --severity high --category listing --step upload \
    --task-ids 550e8400-... --error-codes VALUE_MAX_LIMIT
```

- 模板与纪律见 `error-report.md`；上报后把 report_id 回给用户
- MCP 侧等价工具 `report_issue`

## 卖家会话代管（session-sync）

**触发**：worker 提示 `409 session_expired` / check 提示会话过期 / 首次启用会话代管。

```bash
python3 scripts/cli.py session-sync --credential-id 123
python3 scripts/cli.py session-sync --credential-id 123 --status   # 只查状态
```

- CDP 收割 seller cookie 上传 worker 加密代管（AES-GCM，不回显值）
- 无 sc_company_id 拒传 exit 2；细则见 `session-sync.md`

## 跨浏览器 cookie 导入（import-cookies / probe-win-cookies）

**触发**：1688/Ozon seller 未登录，想免手动登录。

```bash
python3 scripts/cli.py import-cookies
python3 scripts/cli.py import-cookies --sources chrome,firefox   # 指定源
python3 scripts/cli.py import-cookies --browser-profile "Profile 1"  # Windows 接管指定源 profile
python3 scripts/cli.py import-cookies --paste                    # 手动粘贴 Cookie 头（跨平台兜底；--site 1688|ozon-seller 显式落域）

# Windows 排障：只读探针（源浏览器/加密形态/通道判定矩阵，不解密）
python3 scripts/cli.py probe-win-cookies --takeover-test
```

- 扫描本机 Chrome/Edge/Brave/Firefox 的 1688/Ozon 登录 cookie → 注入工具 Chrome → 验证；readiness 未登录时也会自动兜底（冷却 1h）
- Windows 三层通道：Firefox 直读 / Chrome/Edge/Brave 副本接管（零解密，失败降级提示 `--paste`）/ `--paste` 手动兜底；排障先跑 `probe-win-cookies`
- `SKILL_DISABLE_TAKEOVER=1` 关闭接管通道；细则见 `env-setup.md`

## 凭证配置（set_store / set_token / set_ak / list_stores / get_ak）

```bash
python3 scripts/cli.py set_store --name "主店铺" --client-id 4718259 --api-key "..."
python3 scripts/cli.py set_token --token "sk-..."
python3 scripts/cli.py set_ak --ak "..."
python3 scripts/cli.py list_stores

# 浏览器自动获取 1688 AK（登录后自动复制）
python3 scripts/cli.py get_ak --timeout 300
```

- 环境准备类操作，自动执行无需确认；多店铺规则见 routing.md「多店铺」

## 自动更新（update）

**触发**：提示"发现新版本 vX.Y.Z"或用户要求升级 Skill。

```bash
python3 scripts/cli.py update
```

- **行为**：从 COS manifest 下载最新包 → minisign 验签（v0.76 起）→ sha256 校验 → 备份当前 → 覆盖 `scripts/`/文档 → **保留 `data/`** → 失败自动回滚
- **⚠️ 跨进程锁**：并发 CLI 同时 auto-update 会互相破坏备份/覆盖，有文件锁防竞态
- **Agent 决策**：版本升级由用户主导，不自动触发

## 磁盘清理（cleanup）

**触发**：用户问"占空间太大/清理一下"、磁盘满、Chrome profile 膨胀。

```bash
# 预演（只打印将删除内容，不实际删）
python3 scripts/cli.py cleanup --all --dry-run

# 清理全部：Chrome profile 可再生缓存 + 磁盘缓存 + 孤儿 .json.tmp + 过期结果（--days 默认 30）
python3 scripts/cli.py cleanup --all

# 只清过期结果/日志文件（保留最近 7 天）
python3 scripts/cli.py cleanup --old-results --days 7
```

- **参数**：`--profile-cache`（Chrome profile 可再生缓存目录白名单，**登录态绝不动**）、`--cache`、`--temp`、`--old-results`（配合 `--days`）、`--dry-run`、`--all`
- **⚠️ profile-cache 需 Chrome 关闭**：Chrome 进程运行时跳过（缓存文件被锁，硬删会损坏），返回 `skipped_chrome_running` warning
- **安全**：删除走 `safe_rmtree`（fail-open），登录态文件绝不在清理名单

## 调试探针（probe）

```bash
python3 scripts/cli.py probe --url "https://detail.1688.com/offer/xxx.html" --timeout 30
```

- CDP 探针抓取单个 1688 商品（调试用，零副作用）

## settings.json 可调参数

> 位于 `data/config/settings.json`（与凭证同目录），高级用户/维护者按需调整；普通 agent 操作不涉及。

| key | 默认 | 作用 |
|-----|------|------|
| `probe_interval_seconds` | 2.5 | 1688 CDP 探针页内操作间隔秒数（调大更稳防验证码） |
| `match_min_conf` | 0.3 | 图搜匹配主护栏置信度下限 |
| `match_badge_eff_min` | 0.5 | 图搜匹配 badge 有效性下限 |
| `visual_review` | false | 全局开启 discover/follow 人工评审暂停（等价每次加 `--review`） |
| `fx_rate` | 0.075 | RUB→CNY 全局汇率兜底（CLI `--fx-rate` > 店铺 stores.json > 此值） |
| `sentry_dsn` | 内置默认 | Sentry 错误上报 DSN 覆盖（可选） |

修改后即时生效（读取时加载，无需重启）。
