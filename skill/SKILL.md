---
name: pounding-ozon-probe
version: "0.78.0"
agent_created: true
compatibility: Requires Python >=3.12, Google Chrome (auto-launched via CDP), network access to 1688/Ozon/Worker
license: Proprietary
description: >
  Ozon 跨境电商上架与选品工具。触发词：1688、detail.1688.com、Ozon、ozon.ru、上架、上货、
  跟卖、选品、蓝海、爆款、热卖、趋势、以图搜款、找同款、自动采集、无人值守、批量上架、
  任务进度。用户发 1688/Ozon 商品链接、商品图片或选品关键词，或要求上架、跟卖、找货源、
  查任务进度时使用本技能。
---

# pounding-ozon-probe — 工具手册

## 0. 定位 Skill 目录

所有命令在 skill 根目录（含 `scripts/cli.py` 的目录）下执行。确定方式按优先级：
`$SKILL_DIR`（宿主环境已注入）→ 当前目录含 `scripts/cli.py` → 向上级目录查找。Python ≥ 3.12。
**确定一次即可，勿反复探测环境。**

## 0.5 最短路径（三条 recipe，照抄跑）

> **① 1688 链接→上架**（用户明确要上架时）：
> ```bash
> python3 scripts/cli.py graph --url <1688商品URL> --wait
> ```
> 终态打一行（✅ task_id/product_id 或 ❌ 原因），出口带 `👉 NEXT:` 指引。
> 用户只是"看看/评估" → 加 `--no-submit` 展示信封+预估（确认口径见 §3）。

> **② Ozon 链接→跟卖**（用户明确要跟卖时）：
> ```bash
> python3 scripts/cli.py follow --ozon-url <Ozon商品URL> --auto-submit --wait
> ```
> 提交前打 💰 预估（`--min-margin 20` 可拦）。弱意图（多少钱/评估）→ 去掉 `--auto-submit` 即展示候选。

> **③ 关键词→选品入箱**（无人值守，可自动执行）：
> ```bash
> python3 scripts/cli.py discover --keyword <关键词> --to-box --non-interactive
> ```
> 要「凑足 N 个达标」用 discover-task `--target-count N`（用户没说数量先问）。

## 1. 意图路由（话术 → 命令）

**先查下表落位；表没有的再走 `references/routing.md` 决策树。每次操作前重新判断意图。**

| 用户话术 | 命令（照抄，替换尖括号） |
|---|---|
| 发 1688 链接 + 上架/整一批 | `graph --url <URL> --wait` |
| 发 1688 链接 + 看看/能不能上/多少钱 | `graph --url <URL> --no-submit` |
| 发 Ozon 链接 + 跟卖/复制 | `follow --ozon-url <URL> --auto-submit --wait` |
| 发 Ozon 链接 + 弱意图 | `follow --ozon-url <URL>`（缺省展示候选） |
| 发图片找 1688 同款 | `image_search --image <图片路径/URL>` |
| 多个链接/批量处理 | `batch_test.py --urls-file <文件> --submit --wait` |
| 关键词选品/蓝海/跟卖选品 | `discover --keyword <词>` |
| 自动采集/无人值守/跑 N 个 | `discover-task --keyword <词> --target-count <N> --to-box` |
| 查任务进度/完成了吗 | `query <task_id> --watch` |
| 环境报错/首次使用 | `check` |

### 关键规则（压缩版，细则全在 references/）

1. **URL 先判类型**：1688 商品页→A / Ozon 商品页→B / 搜索类目页→C discover --url / 多 URL→batch；截图先转 URL 供 image_search。
2. **提交确认二分法**（详见 §3）：明确上架意图→直提；弱意图→展示等确认；**选品类双出口**（`--to-box` 可自动 / `--auto-submit` 须确认）用户没说走哪条就先问。
3. **指代不清 / 数量不符 / 重上** → 必须追问核对，禁止猜测；"选 N 个"先问要多少个达标的。
4. **长任务后台纪律**：MCP 调 discover/discover-task/follow/seller/graph 一律 `background=true` → `job_status` 看进度（带 `next_poll_s`，按它轮询勿秒查）→ 完成后 `job_result`；会话关闭任务照跑，重开会话 `job_list` 找回；需终止用 `job_cancel`。
5. **重命令串行闸**：六命令跨进程互斥，闸被占 **exit 4** → 加 `--wait` 排队；`--force` 仅用户明确要求时用。
6. **趋势选品**命令层无 trend：先 web_search + LLM 提炼再 discover（`references/trend-selection.md`）。
7. **免登录**：readiness 自动从本机浏览器导入 cookie（每小时最多一次）；手动 `import-cookies`，失败走人工登录。
8. **任务 failed 无解 / 未知错误码 / 假成功** → `report` 上报（`references/error-report.md`），把 report_id 回给用户。

## 2. 命令速查表

> **所有命令都是黑盒**：不确定参数时跑 `--help`（recipe 命令直接跑，不必先 --help），**不要读 `cli.py` 源码**。
> 完整参数/示例按域查：上架类 `references/commands-listing.md` · 选品类 `references/commands-discovery.md` · 运维类 `references/commands-ops.md`。
> 自由度：**[照抄]**=用 §0.5/§1 给的模板勿改 flag；**[可调]**=分析类可按需组合。

| 命令 | 用途 | 自由度 |
|---|---|---|
| `graph` | 1688 上架 | [照抄] |
| `follow` | Ozon 跟卖 | [照抄] |
| `image_search` | 以图搜款 | [照抄] |
| `batch_test.py` | 批量处理 URL 列表 | [照抄] |
| `discover` | Ozon 选品（采集→表格→货源） | [可调] |
| `discover-task` | 任务式全自动选品（干跑缺省） | [可调] |
| `discover-multi` | 多关键词批量选品 | [可调] |
| `search` | 1688 关键词搜索 | [可调] |
| `seller` | 卖家店铺全产品分析 | [可调] |
| `queries` | what-to-sell 蓝海/榜单查询 | [可调] |
| `category` | Ozon 类目查询（只读） | [可调] |
| `query` | 查 Worker 任务状态 | [照抄] |
| `check` | 环境诊断 / `--logs` 看运行轨迹 | [可调] |
| `report` | 上报问题到 worker | [照抄] |
| `session-sync` | 收割 seller 会话上传 worker | [照抄] |
| `import-cookies` / `probe-win-cookies` | cookie 导入 / Windows 排障探针 | [可调] |
| `set_store` / `set_token` / `set_ak` / `list_stores` / `get_ak` | 凭证配置 | [照抄] |
| `update` / `migrate_profile` / `cleanup` | 升级 / profile 迁移 / 磁盘清理 | [照抄] |
| `probe` | CDP 调试探针（单个商品） | [可调] |

### 全局 flag 与出口信号（v0.78+）

- **`--wait`**（graph/follow/discover 族）：不放弃等待——闸被占排队（30s 心跳）+ 提交后轮询到终态再退出。终态前无需再手工 `query`。
- **`--min-margin`**：graph/follow=提交前预估利润率拦截（exit 3）；discover 族=匹配期筛选门槛。**语义不同，勿混用**。
- **出口 `👉 NEXT:` 行**（v0.79）：每条命令出口末行给下一步建议（汇报/查询/修复/结束）——照它执行，不自己发明动作。
- **运行日志**：每条命令打 `📋 运行日志: data/logs/run_*.log`；discover 族另写 `data/logs/report_*.json` 运行报告并打 `📄 运行报告: <path>`。

## 3. 决策边界（提交确认二分法）

| 意图强度 | 判定 | 动作 |
|---|---|---|
| **明确上架意图** | 发链接 + "上架/跟卖/整一批/发布"，或会话中已确认过提交 | `graph` / `follow --auto-submit` 直接提交，带 `--wait` 到终态，不追问 |
| **弱意图** | "看看 / 能不能上 / 多少钱 / 评估一下" | `graph --no-submit` / `follow`（不带 --auto-submit）展示信封+预估，等用户说提交 |
| **选品类双出口** | discover/search/discover-task | `--to-box` 入箱**可自动执行**；`--auto-submit` 直上**必须确认**；没说走哪条先问 |
| 环境准备类 | check / pip install / set_* | 自动执行，无需确认 |
| 数据展示类 | 候选/利润率/优劣 | 陈列数据，不替用户判断 |

## 4. Red Flags（绕圈念头自查）

| 借口 | 现实 |
|---|---|
| "先读一遍 cli.py 源码搞清楚" | 黑盒纪律——跑 `--help` 或查 references，读源码浪费上下文 |
| "$SKILL_DIR 没设，我探测一下环境" | 宿主已注入；真缺就 cd 到含 scripts/cli.py 的目录。**确定一次，勿反复探测** |
| "任务在跑，我多查几次快点" | `job_status` 返回 `next_poll_s`（20s）——分钟级任务按节奏查，秒级轮询纯浪费 |
| "连续失败了，再重试一次" | 同一命令连续失败 **2 次即停**，按 NEXT 行修复或 `report` 上报 |
| "上次会话用户确认过，这次直接提交" | 每次会话重新按 §3 判意图；确认不复用跨会话 |
| "选品结果不错，直接 --auto-submit 了" | 双出口纪律——没确认不直提 |
| "我并行跑几个 discover 快一点" | 串行闸会 exit 4；批量场景用一次调用或 `--wait` 排队 |
| "命令输出里没写下一步，我猜一个" | 出口末行有 `👉 NEXT:`——照它执行；没有 NEXT 才需要自己判断 |

## 5. 参考文件索引

按需读取，不预先加载：
- `routing.md` — 意图路由决策树 + 并发限制 + 多店铺 + 双出口对比（选管线不确定时查）
- `commands-listing.md` — graph/follow/image_search/search/batch_test 完整参数与示例
- `commands-discovery.md` — discover 族/seller/queries/category 完整参数与示例
- `commands-ops.md` — check/query/report/session-sync/cookie/凭证/清理/升级
- `error-codes.md` — 错误码表 + 回复模板 + 进度口径（出错/问进度时查）
- `error-report.md` — 出错上报模板与纪律（任务 failed/未知错误/用户抱怨时查）
- `session-sync.md` — 卖家会话代管细则（worker 提示会话过期时查）
- `output-schema.md` — 输出字段解析 + 汇报模板（成功汇报时查）
- `env-setup.md` — 凭证/环境/check 排查（首次使用查）
- `trend-selection.md` / `discover-fission.md` — 趋势/裂变细则（对应场景查）
- `anti-patterns.md` — 越界行为对照（每次操作前自查）
- `envelope_example.json` — 信封结构示例；`field_mapping.md` — 字段映射规则

## 6. 常见问题与升级

- 缺依赖 → `pip install -r requirements.txt`；`graph`/`follow` 提示缺模块 → bootstrap 升级
  （`python3.12 bootstrap_update.py` 或重新下载最新包）
- 升级后登录态丢失（老版本 profile 在 `data/browser/profile`）→ 老路径迁移先
  `python3 scripts/migrate_profile.py --check` 预览，确认无误再 `--apply` 执行
  （只复制不删除；细则见 references/env-setup.md）
