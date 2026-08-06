# Skill 优化完整计划

> 基于发布版 COS v0.27.0（sha256 校验通过）与开发仓库全量对账。审计范围：SKILL.md × cli.py(14命令) × batch_test.py × compile.py 打包清单 × README.md × envelope_example.json × field_mapping.md × cloud_probe.py × updater.py。

---

## 一、诊断总结

### 1.1 发布包核对

| 项 | 发布版 v0.27.0 | 开发仓库 | 状态 |
|---|---|---|---|
| SKILL.md | 14,644 B（08-06 打包） | 14,989 B（08-05） | ⚠️ 发布版落后仓库（trend --market-info 多角度说明被简化） |
| cli.py | 53,943 B | 53,943 B | ✅ 同步 |
| cloud_probe.py | 130,477 B | 130,477 B | ✅ 同步 |
| VERSION | **0.25.0** | 0.27.0 | 🔴 P0 bug |
| envelope_example.json | ✅ 含 _单SKU选品 + _跟卖示例 | 一致 | ✅ |
| field_mapping.md | ✅ 58 行 | 一致 | ✅ |
| audit_products.py | **不在包内** | 171 行（08-05） | ✅ 正确（开发工具不进包） |
| README.md / SKILL-DEV.md | 不在包内 | 有 | ✅ 正确 |

### 1.2 命令覆盖率审计（cli.py 14 命令）

| 命令 | SKILL.md 状态 | 缺失内容 |
|---|---|---|
| `check` | ✅ 完整 | — |
| `set_store` | ✅ 完整 | — |
| `set_token` | ✅ 完整 | — |
| `set_ak` | ✅ 完整 | — |
| `update` | ✅ 完整 | — |
| `graph` | △ 参数不全 | 缺 `--no-submit` `--category-query` `--item-id` `--retries` |
| `follow` | △ 参数不全 | 缺降级机制详细说明（DataDome → Widget API → 管线A） |
| `image_search` | △ 参数不全 | 缺 `--source cdp`（比默认 ak 更准）`--sort` `--limit` |
| `discover` | △ 参数不全 | 缺 `--export` `--output` `--brand-filter` `--rules` 字段说明 |
| `trend` | △ 参数不全 | 缺 `--export` `--output` `--with-skus` |
| `batch_test` | △ 参数不全 | 缺 `--wait` `--dry-run` `--start` `--limit` `--delay` `--wait-timeout` `--type-filter` |
| `search` | ✗ 完全未入册 | 1688 关键词搜索，输出商品列表 |
| `probe` | ✗ 完全未入册 | CDP 探针抓取单个 1688 商品（调试用） |
| `list_stores` | ✗ 完全未入册 | 列出已配置的 Ozon 店铺 |
| `get_ak` | ✗ 完全未入册 | 浏览器自动获取 1688 AK |

**覆盖率：6/14 完整（43%），7/14 参数不全，4/14 完全未入册。**

### 1.3 内部矛盾

| 矛盾点 | SKILL.md 声称 | 代码事实 | 影响 |
|---|---|---|---|
| §5.3 进度查询 | "CLI 不提供实时进度查询" | `batch_test.py:51` 实现了 `_poll_task_status()` 轮询；`cloud_probe.py:2445` 有 `check_task_status()` | agent 误以为不能查进度，用户体验差 |
| §5.1 完成通知 | "流程完成后我会通知你" | 无推送机制，agent 无法知道任务何时完成 | 虚假承诺 |
| §9 参考文件 | 只列了 2 个文件 | 实际包内还有 VERSION、requirements.txt | 轻微，不影响操作 |

### 1.4 意图路由缺口

当前路由图（§3）有 5 个分支：1688 URL / Ozon URL / "有什么好跟卖的" / "帮我选品上架" / "找蓝海趋势"。

**缺失入口：**
1. **用户发来一张图片**（应走 D1 `image_search`）——路由图无此分支
2. **用户问"我店铺里商品什么状态/为什么被拒"** —— 当前正确做法是告知用户用 Ozon 卖家后台查看（audit_products 是开发工具不进包），但路由图没说"不处理"导致 agent 可能乱猜

---

## 二、优化计划（3 阶段）

### Phase 0 — 紧急修复（P0，先于文档）

#### 0.1 修复 VERSION 死循环

**问题**：COS manifest 发布为 v0.27.0，但包内 `VERSION` 文件是 `0.25.0`。updater 逻辑（`updater.py:93`）比较 `manifest版本 > 本地版本` → 判定有更新 → 下载覆盖 → VERSION 仍是 0.25.0 → 永不收敛。**线上所有用户每次执行任意命令都在重复下载 10MB 包。**

**修复方案**：

- **CI 层面**：GitHub Actions 打包步骤中，用发布 tag 覆写 VERSION 文件再打包：
  ```yaml
  - name: Write VERSION from tag
    run: echo "${{ github.ref_name }}" > skill/VERSION
  ```
  或在 `compile.py` 的 `pack()` 函数里，打包前用 `git describe --tags` 写入 VERSION。

- **校验**：CI 打包后加一步 `grep -q "$(cat manifest.json | jq -r .version)" dist/VERSION` 断言，不一致则 fail。

- **仓库**：`skill/VERSION` 改为跟随 skill 版本线（当前是 0.27.0，已对齐）。注意 AGENTS.md 里 worker 到 v0.27、skill 之前停在 0.25，两条版本线应分开管理。

- **重发**：修复 CI 后重新触发 v0.27.0 发布，确保新包内 VERSION = 0.27.0。

- **验证**：更新后跑 `python3.12 scripts/cli.py check`，确认不再提示"发现新版本"。

**涉及文件**：`.github/workflows/`（CI yaml）、`skill/compile.py`（可选，打包时写 VERSION）
**验收标准**：包内 VERSION = manifest version；连续两次执行任意命令不触发下载。

#### 0.2 删除 deploy/skill.zip 旧包

**问题**：`deploy/skill.zip`（07-25 打包，SKILL.md 仅 8,199 B，cli.py 35,067 B，v0.4.0 时代）还躺在仓库里，和 COS 自动更新机制并存，有误用风险。

**修复**：删除该文件，或改名加 `_DEPRECATED` 后缀。

**涉及文件**：`deploy/skill.zip`
**验收标准**：仓库内不存在 v0.4.0 时代的旧包。

---

### Phase 1 — 文档重组（P1，核心改进）

#### 1.1 新增：全命令速查表（§4 开头）

在 §4 命令参考最前面加一张表，覆盖全部 14 命令 + batch_test.py：

| 命令 | 用途 | 关键参数 | 副作用 | 适用场景 |
|---|---|---|---|---|
| `check` | 验证环境 | 无 | 无 | 首次使用 / 排错 |
| `set_store` | 配置 Ozon 店铺 | `--name --client-id --api-key` | 写 data/config/ | 首次配置 |
| `set_token` | 配置 MXOU_TOKEN | `--token` | 写 data/config/ | 首次配置 |
| `set_ak` | 配置 1688 AK | `--ak` | 写 data/config/ | 首次配置 |
| `update` | 检查/执行更新 | 无 | 覆盖 skill 文件 | 版本升级 |
| `graph` | 1688 上架 | `--url/--item-id --store [--no-submit] [--category-query] [--retries]` | 提交 Worker（除非 --no-submit） | 用户发 1688 链接 |
| `follow` | Ozon 跟卖 | `--ozon-url --store [--auto-submit]` | 提交 Worker（加 --auto-submit） | 用户发 Ozon 链接 |
| `image_search` | 以图搜款 | `--image [--source cdp] [--sort] [--limit]` | 耗 1688 配额 | 用户发图片 / 找同款 |
| `discover` | Ozon 选品 | `--keyword/--url/--max-products [--rules] [--export] [--auto-submit]` | 查 seller.ozon.ru | 找蓝海产品 |
| `trend` | 趋势选品 | `--category [--market-info] [--max-price] [--export]` | 耗 1688 配额 | 品类趋势选品 |
| `search` | 1688 关键词搜索 | `query [--page-size]` | 耗 1688 配额 | 按词找货 |
| `probe` | CDP 探针抓取 | `--url [--timeout]` | 无 | 调试单个商品 |
| `list_stores` | 列出已配置店铺 | 无 | 无 | 查看配置 |
| `get_ak` | 自动获取 1688 AK | `[--timeout]` | 无 | AK 过期时刷新 |
| `batch_test.py` | 批量处理 | `--urls-file [--submit] [--wait] [--dry-run]` | 提交 Worker（加 --submit） | 批量上架 |

**涉及文件**：`skill/SKILL.md` §4 开头
**验收标准**：14 命令 + batch_test 全部出现在表中，每行含副作用标注。

#### 1.2 补全命令参数（§4 各管线）

**管线 A（graph）补：**
```bash
# 只组装信封不提交（调试/确认场景）
python3.12 scripts/cli.py graph --url "https://..." --store "主店铺" --no-submit

# 用商品 ID + 指定 Ozon 类目
python3.12 scripts/cli.py graph --item-id "980815374096" --category-query "поилка" --store "主店铺"
```
补参数说明：`--item-id`（与 --url 二选一）、`--category-query`（Ozon 类目俄语关键词）、`--retries`（CDP 重试，默认 3）、`--no-submit`（只组装不提交）。

**管线 D（image_search）补：**
```bash
# 用 CDP 图搜（比默认 ak 更准，准确率~100%）
python3.12 scripts/cli.py image_search --image "https://..." --source cdp --sort price_asc --limit 5
```
补参数说明：`--source`（ak 默认 / cdp 更准）、`--sort`（price_asc/price_desc/sold_desc/yx_desc）、`--limit`（默认 10）。

**管线 C（discover）补：**
补 `--rules` 字段列表（monthly_sales / gmv / drr / seller_count / margin / price / create_days / sales_growth / rating）、`--export`（csv/json/both）、`--output`（导出路径）、`--brand-filter`（nobrand/known/all，默认 nobrand）。

**管线 E（trend）补：**
补 `--with-skus`（CDP 拉 SKU 明细）、`--export`（json/csv/both）、`--output`（导出路径）。

**批量处理（batch_test）补：**
```bash
# 提交并轮询结果（展示产品明细）
python3.12 scripts/batch_test.py --urls-file urls.txt --submit --wait

# 只组装不提交（验证信封）
python3.12 scripts/batch_test.py --urls-file urls.txt --dry-run

# 从第 5 个开始处理 10 个，间隔 5 秒
python3.12 scripts/batch_test.py --urls-file urls.txt --submit --start 5 --limit 10 --delay 5
```
补全部参数：`--wait` `--dry-run` `--start` `--limit` `--delay` `--wait-timeout` `--type-filter`。

**新增命令示例：**
```bash
# search：1688 关键词搜索
python3.12 scripts/cli.py search "宠物饮水机" --page-size 5

# list_stores：查看已配置店铺
python3.12 scripts/cli.py list_stores

# get_ak：自动获取 1688 AK
python3.12 scripts/cli.py get_ak --timeout 300

# probe：CDP 探针抓取单个商品（调试用）
python3.12 scripts/cli.py probe --url "https://detail.1688.com/offer/xxx.html" --timeout 30
```

**涉及文件**：`skill/SKILL.md` §4 各管线
**验收标准**：cli.py 每个命令的每个 `add_argument` 在 SKILL.md 中都有对应说明或出现在速查表。

#### 1.3 修正 §5.3 进度查询矛盾

**当前**：
> CLI 工具不提供实时进度查询…不要频繁调用 Worker API 轮询状态

**实际**：
- `batch_test.py --wait` 实现了轮询，每 5s 调 `/task_status/{task_id}`，超时 900s
- `cloud_probe.py:check_task_status()` 单次查询 Worker 的 `GET /task_status/{task_id}`
- `batch_test.py:_print_product_summary()` 打印 1688链接/利润率/售价/采购价/运费预估/净利润率/OzonID

**改为**：

> ### 5.3 查询进度
>
> **批量提交**：用 `batch_test.py --wait` 自动轮询，完成后打印每个产品的明细（1688链接/利润率/售价/采购价/运费预估/净利润率/OzonID）。
>
> **单任务查询**：当前 CLI 未暴露单任务查询子命令（`check_task_status` 函数存在于 cloud_probe.py 但未注册为 cli 命令）。如用户追问进度：
> 1. 告知任务正在云端处理中（类目匹配 → AI 生图 → Ozon 上传 → 审核），预计 10–20 分钟
> 2. 建议用户等待后用 `batch_test.py --wait` 查看结果，或在 Ozon 卖家后台查看商品状态
> 3. 不要自行调用 Worker API 轮询
>
> **§5.1 承诺修正**：将"流程完成后我会通知你"改为"任务已提交到云端处理，预计 10–20 分钟完成。你可以在 Ozon 卖家后台查看上架结果，或稍后用 `batch_test --wait` 查询。"

**涉及文件**：`skill/SKILL.md` §5.1 + §5.3
**验收标准**：文档不再声称"不提供进度查询"；§5.1 不做虚假承诺。

#### 1.4 意图路由补 2 个分支

§3 路由图改为：

```
用户输入
  ├─ 有 1688 URL？              → 【管线 A】1688 直接上架
  ├─ 有 Ozon URL？              → 【管线 B】Ozon 跟卖
  ├─ 有图片（无 URL）？          → 【管线 D1】image_search 以图搜款
  ├─ "有什么好跟卖的"？无 URL    → 【管线 C】Ozon 中国站发现 → 跟卖
  ├─ "帮我选品上架"？无 URL      → 【管线 D】1688 搜索/图搜 → 直接上架
  ├─ "找蓝海/热卖/趋势选品" + 品类 → 【管线 E】趋势选品
  └─ 问店铺商品状态/被拒原因     → 引导用户查看 Ozon 卖家后台（工具不直接查询）
```

**涉及文件**：`skill/SKILL.md` §3
**验收标准**：路由图覆盖所有用户输入类型，包括图片和店铺状态查询。

#### 1.5 新增输出解析规范（§4.1）

在 §4 各管线之后、§5 之前，新增一节：

> ### 输出字段解析
>
> 所有业务命令（graph / follow / batch_test）输出 JSON，关键字段：
>
> | 字段 | 类型 | 含义 | agent 取值方式 |
> |---|---|---|---|
> | `summary` | dict | 商品摘要 | 提取标题、价格、重量、图片数 → 汇报给用户 |
> | `envelope` | dict | 完整 GraphInput 信封 | 不需解析，内部数据 |
> | `submit_result.ok` | bool | 提交是否成功 | true → 按 §5.1 回复；false → 按 §5.2 回复 |
> | `submit_result.task_id` | str | 任务 ID | 提取后告知用户，用于后续查询 |
> | `submit_result.error_code` | str | 错误码 | 按 §5.2 错误码表回复 |
> | `product_summary[]` | array | 产品明细（--wait 轮询后） | 提取 1688链接/利润率/售价/采购价/运费/净利润率/OzonID → 表格展示 |
>
> **成败判定**：`submit_result.ok == true` → 成功；否则按 `error_code` 查 §5.2 表。
>
> **汇报模板**：
> - 成功："✅ 任务已提交，任务 ID: `{task_id}`。预计 10–20 分钟完成。"
> - 失败：按 §5.2 错误码表回复。
> - 轮询完成："✅ 任务完成。产品明细：[表格]"

**涉及文件**：`skill/SKILL.md` 新增 §4.1
**验收标准**：agent 拿到 JSON 输出后能按表取字段、判成败、按模板汇报。

---

### Phase 2 — 精修（P2，仓库卫生）

#### 2.1 README.md 加横幅 + 清理过时内容

在文件顶部加：
```markdown
> ⚠️ 本文件为维护者文档。AI Agent 请遵循 `SKILL.md`。
```

删除以下过时内容：
- `playwright install chromium` → 改为"Chrome 自动启动，无需安装 Playwright"
- `cp .env.example .env` → 改为"凭证存储在 `data/config/`，用 `set_token/set_ak/set_store` 配置"
- Python API 示例 `build_graph_envelope_with_retry(...)` → 删除（与 SKILL.md §8 "不自己写代码调 API" 矛盾）
- "只做数据采集和信封组装，**不上架**" → 改为"覆盖选品到上架 Ozon 全流程"

**涉及文件**：`skill/README.md`
**验收标准**：README 不再有与 SKILL.md 矛盾的内容。

#### 2.2 SKILL.md frontmatter 加版本号

```yaml
---
name: pounding-ozon-probe
version: "0.27.0"
description: >
  ...
---
```

发版 checklist 加一条：新命令必须同步"速查表 → 意图路由 → 错误表"。

**涉及文件**：`skill/SKILL.md` frontmatter、`docs/SKILL-PACKAGE.md` checklist
**验收标准**：SKILL.md frontmatter 含 version 字段，与 VERSION 文件一致。

#### 2.3 check 故障排查表

§2.3 `check` 后补一张故障表：

| ❌ 项 | 原因 | 修复 |
|---|---|---|
| Chrome 未安装 | 系统无 Chrome | 安装 Google Chrome |
| Chrome 版本过旧 | Chrome < 100 | 升级 Chrome |
| 1688 AK 无效 | AK 过期或未配置 | `python3.12 scripts/cli.py get_ak` 或手动 `set_ak` |
| 1688 未登录 | Chrome 中 1688 未登录 | 在 Chrome 中打开 1688.com 登录 |
| Ozon 店铺未配置 | data/config/stores.json 无店铺 | `python3.12 scripts/cli.py set_store ...` |
| MXOU_TOKEN 无效 | token 过期或未配置 | 向用户索取新 token，`python3.12 scripts/cli.py set_token` |
| Worker 不可达 | 网络问题或 Worker 宕机 | 检查网络；`curl -s https://worker.mxou.cn/health` |

**涉及文件**：`skill/SKILL.md` §2.3
**验收标准**：check 失败时 agent 能按表引导用户修复。

#### 2.4 data/ 目录语义说明

新增一节说明 `data/` 目录结构：

| 路径 | 用途 | 可否删除 |
|---|---|---|
| `data/config/` | 凭证（stores.json / token / ak） | ❌ 绝对不能删 |
| `data/discovery/` | discover 选品结果落盘 | 可以清理旧文件 |
| `data/logs/` | 运行日志 | 可以清理旧文件 |
| `data/cache/` | 磁盘缓存（TTL 自动过期） | 可以清理 |
| `wave*.txt` / `urls_*.txt` | 测试遗留文件 | 可以删除 |

**涉及文件**：`skill/SKILL.md` 新增小节
**验收标准**：agent 知道哪些文件能动、哪些不能动。

#### 2.5 docs/SKILL-PACKAGE.md 补注

加一条：`audit_products.py` 为开发排查工具，不进 dist 包。

**涉及文件**：`docs/SKILL-PACKAGE.md`
**验收标准**：打包清单文档明确标注 audit_products 不进包。

---

## 三、优先级总览

| 阶段 | 编号 | 动作 | 涉及文件 | 影响 |
|---|---|---|---|---|
| **Phase 0** | 0.1 | CI 写入 VERSION = tag | .github/workflows/ | 🔴 所有用户每次操作重复下载 |
| | 0.2 | 删 deploy/skill.zip | deploy/ | 误用风险 |
| **Phase 1** | 1.1 | 全命令速查表 | SKILL.md §4 | agent 出错率最高 |
| | 1.2 | 补全命令参数 | SKILL.md §4 | 4命令未入册 + 6命令参数不全 |
| | 1.3 | 修正 §5.3 矛盾 | SKILL.md §5 | 虚假承诺 + 矛盾口径 |
| | 1.4 | 意图路由补 2 分支 | SKILL.md §3 | 图片/店铺状态无入口 |
| | 1.5 | 输出解析规范 | SKILL.md §4.1 | agent 拿到 JSON 不会用 |
| **Phase 2** | 2.1 | README 清理 | README.md | 维护者文档卫生 |
| | 2.2 | frontmatter 版本号 | SKILL.md + SKILL-PACKAGE.md | 旧手册配新代码无法发现 |
| | 2.3 | check 故障表 | SKILL.md §2.3 | 首次使用卡点 |
| | 2.4 | data/ 目录语义 | SKILL.md | 防 agent 误删文件 |
| | 2.5 | audit_products 标注 | SKILL-PACKAGE.md | 防维护者困惑 |

---

## 四、不做的事

- ❌ 把 worker 内部细节搬进 SKILL.md（SKILL-DEV.md 已承担，保持分离）
- ❌ 在 SKILL.md 放任何 Python API 示例（与 §8 冲突）
- ❌ 把 audit_products.py 打包进发布版（它是开发工具）
- ❌ 给每个参数写长篇说明（速查表优先，细节留给 `--help`）
- ❌ 暴露 check_task_status 为 cli 子命令（保持 agent 用 batch_test --wait，不自行轮询）
