# PLAN — n8n 前代残留清理（purge-n8n-legacy-v1）

> 2026-09-24 立项。动因：生产双事故（1688 原图上卡 + 库存自动 100）取证定案为
> **前代 `pounding-ozon-hybrid` 云端（n8n/windmill 工作流集群）未退场**——跑在 workbuddy
> 平台实例上（124 任务，tag `pounding-ozon`），持店铺 api_key 直调 Ozon，绕过 worker
> 全部闸。生产 worker.mxou.cn 上的 n8n（/webhook、/rest/workflows）已 404 下线。
> 本计划清理**仓库内全部 n8n 时代死代码/死资产**，并登记生产侧处置。

## 取证结论（2026-09-24，只读）

- 店铺 4718259 全扫 230 卡：09-19 后新建 37 张中 30 张为旁路（offer `{1688id}_0`，
  fbs=100，秒级连发批次）；另 5 张裸数字 offer（what-to-sell 源 id）**零图上卡** + 100 库存。
- 我方管线 09-19 后仅 1 单（发版 gate，测试店）；26 张我方卡被灌 fbs=100（我方代码零库存调用）。
- workbuddy `ozon-imgfix.app.workbuddy.host/{product_id}__{n}_orig.jpg`：对已上卡改挂原图（3 张实锤）。
- 仓库内残留触点全部核verified为死代码/死资产（见批次 1 清单，均 grep 零活调用方）。

## 批次 0 — 生产止血（用户/同事侧，无代码）

| # | 动作 | 说明 |
|---|---|---|
| U1 | workbuddy 后台停 windmill/n8n 内 tag `pounding-ozon` 的 124 任务 + `ozon-imgfix` app | 断旁路上架/改图源 |
| U2 | Ozon 后台轮换 4718259 api_key，新 key 只配进 worker `POST /credentials` | 总闸：新旧系统同时断粮 |
| U3 | 轮换 `ozon_ro` 只读账号密码 + PG 5432 公网收防火墙白名单 | 凭证曾在对话明文出现 |

## 批次 1 — 仓库死代码清理（本批，零行为变更）

### skill/scripts/cloud_probe.py

| 删除项 | 行号（v0.79.0） | 依据 |
|---|---|---|
| `_load_path_registry()` + 顶层 `_paths` | ~100-197 | 只服务 deprecated 路径；`path_registry.json` 文件不存在，文件分支永不走 |
| `_refresh_from__discovery_api()`（n8n workflow discovery） | ~134-190 | 惰性触发仅服务 deprecated submit_task；生产 /rest 404 |
| 惰性 discovery 触发块 | ~204-217 | 同上 |
| 8 个 webhook PATH 常量（PIPELINE/INGEST/FOLLOW_SELL/REFRESH/IMAGE_GEN/ATTR_LEARN/TASK_STATUS/CAT_LOOKUP） | 197-202, 391-392 | 除 PIPELINE（仅 deprecated submit_task 用）与 CAT_LOOKUP（仅降级分支用）外全部零消费 |
| `submit_task()`（deprecated webhook POST） | 605-625 | docstring 自标 DEPRECATED；grep 全仓零调用方 |
| `_cloud_post()` | 250-? | 仅两个死调用方（submit_task、cat-lookup 降级） |
| `lookup_category_webhook()` 的 n8n 降级段 | ~420-444 | 函数本身活（L3746 调用方），只删降级分支——生产 webhook 404 永不生效 |
| `refresh_product` 历史注释 | ~3526 | 函数已不存在，注释残留 |

### worker/assets/

| 动作 | 依据 |
|---|---|
| `processor.json`（pounding-ozon-processor n8n export）→ `archive/assets/` | grep worker 零引用；纯前代解剖标本 |

### 文档

- `skill/README.md`「与 pounding-ozon-hybrid 的关系」节：改写为历史说明（hybrid/n8n 云端已退役，正路唯一入口 worker `/submit_task`）。
- 本 PLAN + CHANGELOG 登记。

### 明确不动

- `deploy/skill/`（旧快照副本）：cd.yml 打包 `--exclude='deploy/skill'`，不进部署产物、不被执行，仅 VERSION 四源宿主——同步清理无收益反增 diff。
- `archive/docs/legacy/`：历史归档按惯例不动。

### 验收

- skill 全量测试绿（基线 1519）；CI 口径 lint `ruff check scripts/ --select F` 绿。
- `grep -rn "n8n" skill/scripts/ worker/src/` 仅剩历史注释/文档措辞，无路径常量、无 webhook 调用。
- worker 测试不受影响（worker 源码零改动，仅 assets 挪动——跑 worker 快速子集确认无引用）。

## 批次 2 — extensions.stock / warehouse_id 死键处置（待拍板，行为变更）

现状：`template_service` 校验/存储 + skill `_INJECTABLE_EXT_KEYS` 透传 +
`test_template_profile.py` 活测试锁定下发链，但 **worker graphs 零消费**（信封到了也不会设库存）。

| 选项 | 内容 | 影响 |
|---|---|---|
| A（建议） | 三处删除（worker 白名单/校验、skill 注入键、测试改写）+ 旧模板数据兼容（存量 config.stock 静默忽略） | 口径收敛为「我方管线永不设库存」——与本次事故教训一致；模板编辑器/UI 同步删键 |
| B | 实现消费：rFBS 上架后经 `/v1/product/import/stocks` 设库存 | 需定义库存来源（模板/信封/人工）；与「自动库存=超卖风险」教训相反，不建议 |

**不与批次 1 同车**：涉及 API schema（模板 config 键删除）+ 测试改写 + webui 表单，需独立 PR。

## 批次 3 — 生产存量清理（写操作，待用户定范围后执行）

dry-run → 确认 → 执行，全部走店铺 API：

1. 5 张零图卡（offer 裸数字，present=100）：**归档**（最优先，裸奔风险）。
2. 30 张 n8n 原图卡 + 3 张 imgfix 卡：present→0；归档或重推 AI 图（逐卡拍板）。
3. 26 张我方卡：假库存 present→0（真实库存由卖家人工恢复）。
4. 2 张 fbo 卡：fbo 库存清零（fbs 是否保留人工定）。
5. 1 张 alicdn 存量卡 `6392319163`：重推 AI 图（v0.77.1 欠账）。

## 批次 4 — 防御批（随下版）

1. **库存断言告警**：ozon_status 收尾断言（复用 v0.77.1 卡片图断言通道）——我方完成的卡
   `present>0` 且非我方设置（我方永不设库存，恒成立条件）→ error_reports + 通知通道。
2. **卡片图被外部替换探测**：收尾断言复查窗口内首图 URL 变化（imgfix 类通道指纹：
   非 `ir-*.ozone.ru`/本方 COS 域名即告警留痕）。
3. CI-gate docs-only 盲区（gate 回退最近有 CI 的祖先提交求值）——v0.79 发版实录登记的 defer。

## 执行记录

- [x] 2026-09-24 批次 1 施工（本 PR）
- [ ] 批次 0 用户侧（U1/U2/U3）
- [ ] 批次 2 拍板
- [ ] 批次 3 范围确认
- [ ] 批次 4 随下版
