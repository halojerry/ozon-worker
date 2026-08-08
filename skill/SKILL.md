---
name: pounding-ozon-probe
version: "0.30.0"
agent_created: true
description: >
  Ozon 跨境电商上架工具。此技能在以下场景触发：用户发送 1688 商品链接时
  直接上架到 Ozon；用户发送 Ozon 商品链接时跟卖；用户发送图片时以图搜款找
  1688 同款；用户请求选品（找蓝海/热卖/趋势商品）时搜索 Ozon 中国站并匹配
  1688 货源；用户发送多个链接时批量处理。覆盖选品、跟卖、上架、以图搜款、
  趋势选品全流程。
---

# pounding-ozon-probe — 工具手册

## 0. 定位 Skill 目录

所有命令在 skill 根目录下执行。Skill 根目录是包含 `scripts/cli.py` 的目录。

确定方式（按优先级）：
1. 若环境变量 `SKILL_DIR` 已设置，使用 `$SKILL_DIR`
2. 若当前目录存在 `scripts/cli.py`，使用当前目录
3. 否则，向上查找上级目录直到找到 `scripts/cli.py`

Python 要求 ≥ 3.12。使用环境中可用的 `python3`（或 `python3.12`）。

## 1. 意图路由

先判断用户意图，再选管线。每次操作前重新判断，不因上下文而惯性选择。

### 决策表（按优先级从上到下）

```
用户输入
 ├─ ① 有 URL？ → 先判 URL 类型：
 │    商品页 detail.1688.com/offer/…  → 【管线 A】1688 直接上架
 │    商品页 ozon.ru/product/…        → 【管线 B】Ozon 跟卖
 │    搜索页/类目页（ozon.ru/search 或 /category/）→ 【管线 C】discover --url 采集该页
 │    多 URL / "批量处理这些"         → 【管线 F】batch_test 批量
 ├─ ② 有图片（无 URL）？             → 【管线 D1】image_search 以图搜款
 │    图搜出候选 → 展示让用户确认哪一款 → 再 graph 上架（不允许图搜后直接上架）
 ├─ ③ 无 URL → 按意图词优先级：
  │    "趋势/热卖/新品风向/爆款" + 品类 → 趋势选品：agent 先 web_search + LLM 提炼
  │      细分关键词 → discover --keyword <细分关键词>（见「关键规则」）
 │    "跟卖/找能跟卖的"              → 【管线 C】discover 跟卖选品
 │    "上架/上货/上点/上产品/整一批"  → 【管线 D】discover 选品上架
 │    "蓝海" → 默认 【管线 C】（蓝海评分体系在 C）；用户明确说"蓝海趋势/市场分析"才走趋势选品（agent 分析 + discover）
 │    "选品/选 N 个" 无修饰          → 追问（跟卖 or 上架 or 趋势选品）+ 品类
 │    "有什么好卖的/卖得动"          → 追问（趋势选品 or 跟卖推荐）+ 品类
 ├─ ③a 无对象：上架/选品 但无 URL/图/关键词 → 先追问：发链接或图片(→A/D1)，
 │    还是给品类让我选品(D)。禁止直接中国站兜底采集
 ├─ ④ 问店铺商品状态/被拒原因        → 引导用户在 Ozon 卖家后台查看（工具不直接查询）
 └─ ⑤ 指代不清 / 数量不符 / 重上已上商品
      （"类似的""这个""它"；声称 5 个只发 2 个；"昨天那个再上一遍"）
      → 必须追问核对 + 检查是否已上过（防重复提交），禁止猜测
```

### 关键规则

- **有 URL 时先判类型**：搜索页/类目页 URL 走 C（discover --url），**绝不去 B 跟卖单商品**（解析会失败）
- 无 URL = 按意图词优先级选 C / D / E
- **蓝海评分只在管线 C 中使用**；「蓝海」默认路由 C，避免与 E 的「趋势」触发词冲突
- 管线 C（跟卖选品）和管线 D（选品上架）命令相同（discover），仅用途叙述与是否 `--auto-submit` 的差别；discover **无 follow_type 参数**，跟卖标记由 follow 内部注入
- ⚠️ **趋势选品 = agent 自主分析 + discover 执行**：命令层已无 `trend`，趋势选品流程为——
  1. agent 用自带 web_search 搜索 `"{品类} Ozon 热门趋势 蓝海 细分品类 2025"`（可加俄语
     `Ozon тренды 2025`、平台变体 `ozon.ru trends` 多角度搜），收集 3-5 条结果；
  2. agent 用自带 LLM 从结果提炼 3-5 个细分关键词（如「宠物用品」→ 驱蚊项圈/宠物驱蚊喷雾）；
  3. agent 用 `discover --keyword <细分关键词>` 执行选品（管线 C 流程，含蓝海评分）。
  跳过 web_search 直接猜关键词 = 选品质量明显下降，除非用户明确表示不用搜索。
- ⚠️ **管线 B 降级语义变化**：Ozon 页面禁止复制时降级走管线 A（直采重建）——这是
  **重建直采卡，不是跟卖复制**（offer_id/定价都会变），必须向用户说明
- **复合意图消歧**：输入同时含趋势词（爆款/热卖）与上架词（上架/整一批）时，追问：「要趋势出款（agent 分析 + discover）还是按词选品直接上（D）？」；批量上架需链接列表走 batch，选品数量不是批量参数
- **数量词**：「选 N 个/来 N 个」→ 追问意图（跟卖/上架/趋势选品）+ 确认 N 是挑选规模还是硬性要求；discover 是展示候选→挑选，不直接产 N 个结果
- **截图处理**：收到的图片需先保存为本地路径或转 URL 供 `image_search --image` 使用；**截图即目标商品时**，可先向用户索要该商品 1688 链接走 A（省图搜配额），图搜用于「找同款」场景
- **URL + 弱化意图词**：用户说"看看/评估/能不能上"时，先用 `graph --no-submit` 展示再等确认，不直接提交

## 2. 命令速查表

> 完整参数与示例见 `references/command-reference.md`（或 `--help`）。

| 命令 | 用途 | 关键参数 | 副作用 | 适用场景 |
|---|---|---|---|---|
| `check` | 验证环境（v0.30: 全量诊断，缺依赖/无浏览器也继续探测 Worker/MXOU/凭证） | 无 | 无 | 首次使用 / 排错 |
| `set_store` | 配置 Ozon 店铺 | `--name --client-id --api-key` | 写 `data/config/` | 首次配置 |
| `set_token` | 配置 MXOU_TOKEN | `--token` | 写 `data/config/` | 首次配置 |
| `set_ak` | 配置 1688 AK | `--ak` | 写 `data/config/` | 首次配置 / AK 过期 |
| `update` | 检查并应用自动更新（v0.30: 跨进程锁，并发命令不会竞态破坏安装） | 无 | **覆盖 skill 文件**（备份 + 保留 data/） | 版本升级 |
| `migrate_profile` | 迁移 Chrome profile 到统一路径（旧 `data/browser/profile` → `profiles/1688/default`） | `[--apply] [--check]` | 复制 profile（默认 dry-run 不写） | v0.30 升级后首次使用（登录态迁移） |
| `query` | 查询 Worker 任务状态 | `<任务ID>`（位置参数，submit/follow 返回的 UUID） | 只读，无副作用 | 提交后查进度/成败/产品明细 |
| `seller` | 卖家店铺全产品运营分析 | `--seller-id [--max-products] [--max-skus]` | 查 seller.ozon.ru 运营指标（逐 SKU，受限速） | 跟卖前20名卖家 → 店铺选品（v0.29.x 新增） |
| `get_ak` | 浏览器自动获取 1688 AK | `--timeout` | 无 | AK 过期时刷新 |
| `list_stores` | 列出已配置店铺 | 无 | 无 | 查看配置 |
| `graph` | 1688 上架 | `--url/--item-id --store [--no-submit] [--category-query] [--ozon-ref-url]` | 提交 Worker（除非 `--no-submit`） | 用户发 1688 商品链接（`--ozon-ref-url` 传 Ozon 竞品参考链接, 同类目属性复用） |
| `follow` | Ozon 跟卖 | `--ozon-url --store [--auto-submit]` | 提交 Worker（加 `--auto-submit`） | 用户发 Ozon 商品链接 |
| `image_search` | 以图搜款 | `--image [--source cdp] [--sort] [--limit]` | 耗 1688 图搜配额 | 用户发图片 / 找同款 |
| `discover` | Ozon 选品 | `--keyword/--url [--rules] [--export] [--auto-submit] [--fission] [--max-depth 2] [--non-interactive]` | 查 seller.ozon.ru 运营指标；`--auto-submit` 提交 Worker；`--fission` 裂变选品（v3） | 找蓝海 / 跟卖选品 / 趋势选品执行 / 裂变选品 |
| `search` | 1688 关键词搜索 | `query [--page-size]` | 耗 1688 搜索配额 | 按词找货 |
| `probe` | CDP 探针抓取单个 1688 商品 | `--url [--timeout]` | 无 | 调试单个商品 |
| `batch_test.py` | 批量处理 URL 列表 | `--urls-file [--submit] [--wait] [--dry-run]` | 提交 Worker（加 `--submit`） | 批量上架 / 回归 |

## 3. 决策边界

| 操作 | 策略 | 说明 |
|------|------|------|
| `check`、`pip install`、`set_store`、`set_token`、`set_ak` | 自动执行 | 环境准备类操作，无需确认 |
| `graph`、`follow`（含 `--auto-submit`） | 自动执行 | 用户给了明确 URL，直接上架 |
| `discover` 选品后的最终提交 | 必须确认 | 展示候选列表，等用户说"提交" |
| 批量处理 | 必须确认 | 影响面大，需用户明确确认 |
| 利润率高低、候选产品优劣 | 展示不表态 | 陈列数据，不替用户判断 |

## 4. 常见越界行为

| 错误行为 | 后果 | 正确做法 |
|---------|------|---------|
| 自己写 Python 代码调 API | 逻辑不完整、缺错误处理 | 用 `cli.py` 命令 |
| 自己探索项目目录结构 | 浪费时间、可能改错文件 | 看本文档 |
| 给 Ozon URL 还去算蓝海评分 | 逻辑混乱 | 有 URL 直接处理 |
| 给 1688 URL 还去 Ozon 搜索 | 多余操作 | 有 URL 直接处理 |
| 替用户决定"这个利润太低不上了" | 用户失去控制权 | 展示数据让用户决定 |
| 在用户没说"提交"时就提交 Worker | 用户没确认就上架 | 等用户明确说"提交" |
| 对话长了就忘记意图路由规则 | 管线混乱 | 每次操作前重读 §1 |
| 把蓝海逻辑混入跟卖流程 | 数据错误 | 蓝海只在管线 C |

## 5. 参考文件索引

| 文件 | 何时读取 | 内容概要 |
|------|----------|----------|
| `references/command-reference.md` | 选定管线后、执行命令前 → 查完整参数和示例 | 各管线完整参数、输入输出、示例 |
| `references/error-codes.md` | 命令执行出错、或用户问进度时 → 查错误码和回复模板 | Worker 错误码表 + 进度查询口径 + CLI 错误处理 + 错误恢复决策 |
| `references/output-schema.md` | 命令执行成功、需要向用户汇报结果时 → 查字段解析和汇报模板 | submit_result / check_task_status / product_summary 字段解析 + JSON 示例 + 汇报模板 |
| `references/env-setup.md` | 首次使用、check 失败、或用户问凭证配置时 → 查环境准备和故障排查 | 环境准备 + 凭证 + check 故障排查 + data/ 目录语义 |
| `envelope_example.json` | 需要确认信封字段结构时 → 查示例。读取路径：`_单SKU选品.envelope`（直采）或 `_跟卖示例.envelope`（跟卖）；`_说明`/`_关键约定` 是文档说明非数据字段 | 完整信封结构示例（单 SKU + 跟卖两种模式） |
| `field_mapping.md` | 需要确认 1688/Ozon 字段如何映射到信封时 → 查映射规则 | 1688/Ozon 字段 → 信封字段的映射规则 |

## 6. 更新与旧包升级

**自动更新（v0.18.0 起，默认开启）**：每次运行命令时，若 COS 上有新版本，
自动备份旧文件 → 覆盖升级 → 失败自动回滚（`data/` 凭证/登录态/缓存全程保留），
升级成功后提示重启终端。v0.30.0 起 updater 有跨进程锁：并发命令同时触发更新时
只一个执行，其余静默跳过（不会破坏安装）。

- 关闭自动更新：`export SKILL_AUTO_UPDATE=0`，退回「提示 + 手动 `skill update`」模式。
- 手动更新：`python3 scripts/cli.py update`

**v0.30.0 升级注意（Chrome profile 统一）**：profile 路径已统一为
`data/browser/profiles/1688/default`（旧 `data/browser/profile` 弃用）。
**旧版本升级后首次运行前**执行 profile 迁移（保留 1688/Ozon 登录态，只复制不删除）：

```bash
python3 scripts/migrate_profile.py --check   # dry-run 预览
python3 scripts/migrate_profile.py --apply   # 实际迁移
```

**环境前置检查（v0.30.0 起）**：所有命令入口自动探测 Python ≥3.12 +
`requests`/`websocket-client`/`Pillow`，缺依赖时立即给出
`pip install -r requirements.txt` 指引并退出（不再链路深处炸 traceback）。
`check` 命令现在是无浏览器也继续探测 Worker/MXOU/凭证的全量诊断。

**Runtime 自动发现（v0.31.0 起，PR-A/PR-B）**：命令入口自动检测可用 Python：
- 当前解释器非 3.12 → 扫描 PATH 找 `python3.12` 并 os.execve 无感切换（agent
  无需关心用哪个 python 跑，`python3 scripts/cli.py <cmd>` 照旧）
- 当前 3.12 但缺依赖 → 自动创建 `data/.venv`（独立 venv，**不碰 agent/系统环境**）
  并安装依赖后继续（首次约 30-60s，进度实时回显；`.ready` 标记短路后续零开销）
- 逃生舱：`SKILL_NO_VENV=1` 关闭自动 venv（回退手动 pip install 指引）；
  `SKILL_PYTHON=/path/to/python3.12` 显式指定解释器
- ⚠️ .so ABI 绑定 3.12：3.13/3.14 会被拒绝（cpython tag 不匹配）

**旧包升级（v0.12.0 之前的包没有 updater，不会自动提示）**：

```bash
# 1. 从最新 GitHub Release 下载 bootstrap_update.py 到 skill 包目录
#    https://github.com/halojerry/ozon-worker/releases
# 2. 运行（会下载最新包 → sha256 校验 → 覆盖升级 → 失败回滚）
python3 bootstrap_update.py
```

如果运行 `graph`/`follow` 提示「缺少依赖模块」或「未找到 scripts.cloud_probe」，
前者按提示 `pip install -r requirements.txt`，后者按上面 bootstrap 升级即可。
手动确认当前版本：`python3 scripts/cli.py update`
（显示「已是最新」即正常）。
