# 竞态 + 能力统一复用深度审计——设计与实施计划（待用户审批）

> 本文档 Part 1 为设计（spec），Part 2 为可执行实施计划（Task 1-11）。执行停在 Task 11 用户拍板关口，修复代码属后续波次 plan。

# Part 1：设计文档

- 日期：2026-09-09
- 状态：**待用户审批**
- 范围：`skill/` + `worker/` + `webui/` 全量功能与管线 + skill 引导内容（SKILL.md / references/）
- 方法：方案 A（两线并行）——静态全扫先行 → 动态证实 → 分波修复
- 测试边界：默认纪律（mock/本地复现 → 证实后才上本地 Docker 测试店真单；绝不碰生产 `worker.mxou.cn`；真单分波、波间汇报）

## 1. 背景与目标

用户要求深度排查两类系统性问题，并对发现的问题先定位、真测、再修复：

1. **竞争态（race condition）**：并发、迟到回调、重复提交、缓存竞争导致的状态错乱。
   历史上已修过同类 bug（后台任务终态粘性 4c42dcfb、学习表竞态 N5b、store_sync 无退避风暴、
   余额缓存 0 污染、`or 1.0` 吞 0.0 置信度、字典缓存 NULL type_id 裂行 0727e7dc）——本审计
   复查这些区域是否仍有同类模式，并覆盖此前未审过的写点。
2. **能力重复（缺统一复用）**：同一能力多套实现且语义分叉。已点名例子：1688 货源匹配
   （follow 用 aibuy→CDP→AK 三级降级，discover 用自己的漏斗 v2 匹配管线，trusted 语义/
   有效性守卫/类目键归一各自实现）。
3. **引导内容漂移**：SKILL.md / references/ 命令表与实际 CLI 行为的一致性。

**终态交付**：一份每条带 `file:line` 证据、经动态复现证实或证伪的《发现清单》+ 按风险分波的
修复（每波 TDD + 全量回归 + 波间用户确认）。修复实施以发现清单获批为前提（符合
「先调查再出方案」既定纪律）。

## 2. 已知地形（2026-09-09 摸底，非深度结论）

### 2.1 skill 侧能力重复迹象

- **1688 找货源/图搜多通道并存**：`follow_sell_cloud`（cloud_probe.py:3750）内部
  aibuy mtop 直调 → CDP 网页版 → AK API 三级降级（cloud_probe.py:3955-4017）；
  `discover` 走 ozon_discovery.py 自己的匹配管线（`_search_1688_source`/`_process_match`/
  `_attach_match_meta`:2191，漏斗 v2，自带置信度与货源有效性守卫 :938）。两条链的
  trusted 判定（matchBadgeFull vs method=="aibuy"，cloud_probe.py:2457-2485）、类目键
  归一（aibuy cate_level2 优先 vs AK 单层 cateId，ozon_discovery.py:155-157）、
  有效性门槛各自实现。
- **重量估算**：v0.58 已做同源统一（`DEFAULT_WEIGHT_G`:48 + `estimate_shipping_cny`:57，
  cloud_probe.py:3424-3428 消费），但抓取层守卫 `_sanitize_weight_g`（cloud_probe.py:1708）
  与 discover 侧清洗规则是否逐字一致未验证。
- **命令面**：cli.py 实际注册 22+ 子命令（cli.py:2349-2670，含 set_store/list_stores/
  set_token/set_ak/probe/discover-task/discover-multi/query/report/seller/queries/cleanup/
  import-cookies），AGENTS.md/SKILL.md 命令表只覆盖其中一部分——引导漂移嫌疑。

### 2.2 worker 侧能力重复迹象

- **绕过 `ozon_client.py` 直接调 Ozon API 的文件多达 10 个**：main.py、pricing_node、
  ozon_upload_node、ozon_status_node、fetch_back_node、auth_node、validation_retry_loop、
  ozon_dict_values、logistics_quote、ozon_client 本体。各处自管 transport/重试/限流的
  程度不一，需逐个甄别「合理分工」还是「重复实现」。
- 唯一入口（compute_price / title_formula / commission_resolver /
  cap_attribute_values / attr_value_matcher）的调用面需核查是否有漏网路径。

### 2.3 竞态高危区（按历史同类 bug 推导）

任务终态全部写点（取任务/终态/取消/重跑/stale 恢复/60s 清理器/进度回写）、MCP 后台任务
回调面、草稿状态机（并发编辑/重复提交/resubmit）、共享表 upsert（字典三桶/属性回写/
佣金回填/学习表/selection_insights）、余额缓存、store_sync 调度器重叠执行。

### 2.4 用户实证靶点（2026-09-09 用户报告，P0 优先审计对象）

**靶点一：重复获取 Ozon cookie / 1688 AK / 1688 登录态**（缓存了为什么还反复拿）。
摸底已锁定三套获取机制各自的重复触发点，待动态证实哪个是用户实况：

- **AK 双存储读写错位**（高嫌疑）：写侧 `ak_callback._save_ak`（ak_callback.py:37-78）
  写 `SKILL_ROOT/.1688-AK/.ak_store.json` + config_store；读侧 `get_ak_from_file`
  （ak_1688_client.py:171-184）读 **CWD 相对路径**（`Path(".1688-AK/...")`）与
  `~/.openclaw/workspace/...` 等四个位置——**SKILL_ROOT 存放位不在读列表**，且任一
  旧文件存在即短路返回（旧 AK 遮蔽新 AK）→ 401/403 → `_try_refresh_ak`
  （ak_1688_client.py:79-90）开浏览器重取 → 仍读旧文件 → 循环。复现方法：
  非 skill 目录 CWD 跑 `search`/AK 调用 + 故意放一个过期 `.ak_store.json` 在读路径。
- **aibuy mtop token 6h TTL**（ozon_image_search.py:46）：过期/毒 token 即触发
  Chrome 导航刷新（舞步等待 ≤8s）；1688 登录态失效时每次图搜都重导航 = 用户可见的
  「重复获取 1688 登录态」。导入 cookie（import-cookies，cli.py:3314）注入的是工具
  Chrome profile，浏览器 profile 重建/未启动即失效，无独立磁盘兜底。
- **Ozon seller cookie 无磁盘缓存**：`_fetch_seller_session_cookies`
  （ozon_seller_analytics.py:936）每次从活 Chrome 会话现读；直调失败即
  `wait_for_seller_login` 自动开 seller 页（ozon_discovery.py `_enrich_with_seller_metrics`
  回退段）——每次 discover 都可能重演开页。

审计动作：域 A/B 内逐条核实三个机制的全部调用方与 TTL/失效语义；动态证实 AK 遮蔽
循环；修复方向 = AK 单一存储 + 读侧同一解析函数 + 刷新后清旧文件；token/cookie
统一磁盘缓存层（cache.py）+ 失效降级不导航。

**靶点二：类目/货源匹配置信度 0.11~0.38 → worker 不自动上**。摸底结论：

- 用户看到的置信度 = `_title_conf`（ozon_discovery.py:2181）——**Ozon 标题 vs 1688
  候选标题的纯文本相关性**（RU→词对映射 `_ru_zh_title_overlap`，ZH→`verify_1688_match`
  关键词重叠），与类目维度无关；aibuy 视觉信号 normalizationScore 只微调 `score`
  不进 confidence（ozon_discovery.py:2322-2333）。守卫阈值 `_min_conf=0.3`
  （:2277）、`badge_less_conf_weak`/`guardrail_blocked`（:2464-2481）产出弱档/阻断。
- worker 类目判定层**已**建真值优先信任序（Skill page/what_to_sell 权威 >
  L0 学习表（1688 叶子 cid）> jieba 文本兜底；信封带 Ozon 页面真值与 1688 cid，
  v0.66~0.72 批次），但 L0 冷启动（新叶子前 1-2 单不权威）+ search_kw 信封非权威
  + 上游 conf 本身低 → 终判落文本链 → 低置信**有意**入箱不自动上
  （v0.67/0.68 拍板：宁入箱不上错）。即：**决策层改了真值优先，评分信号没改**——
  conf 仍是标题文本，类目一致性（1688 cate_level2 ↔ Ozon 树）与图搜官方相似度
  不参与评分。这是「改了判定方式但置信度还是低」的直接答案。
- 审计动作：域 A 核实 `_title_conf` 全部调用方与低分分布；动态对照（同批候选：
  纯文本 conf vs 加类目一致性/图搜信号的 conf 分布）；修复方向（P0 波）=
  评分信号换轨——类目一致性与 aibuy 官方信号为主、标题文本为辅，弱档阈值同步
  重校；配套核实 match_evidence→worker 压 0.6 链（learning_record_node.py:629-636）
  在新信号下的语义。

## 3. 审计方法：三阶段

### Phase 1 —— 静态审计（只读，7 域并行）

| 域 | 范围 | 产出 |
|---|---|---|
| A | skill 能力矩阵：能力 × 命令，每格实现入口 file:line | 重复能力候选清单 |
| B | skill 并发/缓存/信封组装路径一致性（graph/follow/discover/batch_test 四条组装路径字段填充对比） | 竞态+分叉候选 |
| C | worker 任务生命周期竞态写点清单（每个状态写点的并发防护有无） | 竞态候选 |
| D | worker MCP 后台任务 + 草稿状态机 + 提交/resubmit/批量提交链 | 竞态候选 |
| E | worker 缓存与共享表 upsert 并发语义（字典三桶/属性回写/佣金/学习表/insights/余额缓存/store_sync 调度器） | 竞态候选 |
| F | worker 能力重复（Ozon transport 收敛面 + 唯一入口漏网核查 + 错误码/通知构造） | 重复候选 |
| G | webui 交互竞态表面 + skill 引导内容漂移（SKILL.md/references vs 实际 CLI） | 漂移+竞态候选 |

**发现条目格式**（写入 `docs/audit/2026-09-09-race-duplication/findings.md`）：

```
### F-<域字母><序号>  <一句话标题>
- 类型：race | duplication | drift
- 严重度：高（已有实证或窗口明显）| 中（可疑模式）| 低（薄封装可接受）
- 证据：<file:line> + 3 行以内代码推演
- 复现思路：<怎么动态证实>
- 修复方向：<一句话草案，仅记录不实施>
```

### Phase 2 —— 动态证实（发现清单内每条高/中嫌疑必须过此关）

- **race**：并发 repro（asyncio/线程脚本、双 REST 并发、迟到回调模拟、mock 单测）。
  证实标准 = 本地稳定复现，或代码推演 + 运行日志双证据。
- **duplication**：行为对照测试——同一输入走两条实现，diff 输出差异并截图/留存。
- **E2E**：仅对「用户可见最终效果」类证实问题上本地 Docker（测试店 5381204）真单，
  wave 分批、波间汇报。前置：清任务表 zombie、空 token 验 auth 场景、测试图必须 COS 图。

### Phase 3 —— 分波修复（发现清单经用户拍板后启动）

- **P0 波**：用户可见错误 / 资损 / 合规（假成功、错上架、多值拒单残余、终态翻盘残余、**§2.4 两靶点：凭证反复获取 + 评分信号换轨**）。
- **P1 波**：数据一致性 / 缓存裂行 / 学习表污染。
- **P2 波**：统一入口重构（1688 匹配统一层、Ozon transport 收敛、重量清洗归一、
  引导内容对齐）——重构前先锁行为对照测试，保证行为保持。
- 每波：TDD、全量回归绿（worker 基线 2113 / skill 基线 810+）、CHANGELOG、波间用户确认。
- 具体修复任务在发现清单获批后按波另写详细 plan（写作时 findings 未定，无法提前写实代码——
  这是刻意的两段式规划，非占位符）。

## 4. 红线与约束

1. 禁止打生产 `worker.mxou.cn`；功能测试只打本地 Docker（http://localhost:8080）。
2. 共享工作树有其他会话 WIP（当前：cloud_probe.py / ozon_discovery.py / draft_service.py /
   DiscoveryPanel.tsx / CONTRACT-v4.md 及配套测试）——Phase 1 只读安全；Phase 3 修复前
   必须 `git status` 复核，逐文件 `git add`，禁 `-a`/stash；与 WIP 撞车的发现只记录不顺手改。
3. 唯一入口纪律：收敛后禁止内联复制再现；改 API 必跑 `gen_api_docs.py`；契约字段三处同步。
4. langgraph Input model 纪律：节点要读的字段必须声明进节点 Input。
5. 执行主体：审计任务优先 subagent 分域并行（2026-09-09 试调度遇 provider 认证失败，
   执行时重试；仍失败则主会话按域顺序内联执行，域间落盘 findings 防上下文丢失）。

## 5. 产出物与验收标准

1. 设计文档（本文）→ 用户审批。
2. 实施计划——本文档 Part 2。
3. `docs/audit/2026-09-09-race-duplication/findings.md`——发现清单（Phase 1 产出，
   Phase 2 逐条标注 confirmed / refuted / need-more-evidence）。
4. 修复波次规划（Phase 2 结束时呈用户拍板）+ 每波修复的详细 plan / 测试 / 回归证据。

**验收**：
- 每条高/中严重度发现都有 repro 或对照测试佐证（confirmed/refuted 二态，无悬置）。
- 修复波全量回归绿且基线数不降。
- 统一入口落地后旧路径删除或薄委托，全库 grep 无第三份拷贝。
- 引导内容抽查零漂移（SKILL.md 命令表逐条对 CLI 实测）。

---

# Part 2：实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 对 skill/worker/webui 做静态全扫 + 动态证实，产出每条带 file:line 证据的《竞态与能力重复发现清单》，并规划分波修复（修复本身待清单获批后另写波次 plan）。

**Architecture:** 两线并行——Phase 1 七个只读审计域（A-G）产出结构化发现条目；Phase 2 对高/中嫌疑逐条写并发 repro / 行为对照测试证实或证伪；Phase 3 为分波修复关口（P0/P1/P2），具体修复代码在清单获批后按波另写详细 plan。

**Tech Stack:** Python 3.12/3.14（skill/.venv314 全家桶）、pytest、FastAPI+LangGraph（worker）、本地 Docker Compose（PG 5433）、React/bun（webui，只读面）。

**Spec:** 本文档 Part 1

## Global Constraints

- 禁止访问生产 `worker.mxou.cn`；功能测试只打本地 Docker `http://localhost:8080`。
- 共享工作树有其他会话 WIP——动手前必须 `git status` 复核；逐文件 `git add`，禁 `-a`/stash。当前已知 WIP：`skill/scripts/cloud_probe.py`、`skill/scripts/lib/ozon_discovery.py`、`worker/src/services/draft_service.py`、`webui/src/components/DiscoveryPanel.tsx`、`docs/CONTRACT-v4.md` 及配套测试。
- Phase 1/2 全程只读源码 + 新增测试/审计文档；不修改任何业务源码（修复属 Phase 3，另立 plan）。
- worker 回归基线命令：`cd worker && PGDATABASE_URL="postgresql://postgres:localdev123@localhost:5433/ozon" PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/ -q`（基线 2113 绿）。
- skill 回归基线命令：`cd skill && .venv314/bin/python -m pytest tests/ -q`（基线 810+ 绿）。
- 发现条目格式与严重度定义见 spec §3 Phase 1；每条高/中发现必须过 Phase 2 证实关（confirmed/refuted 二态）。
- E2E 真单前置：`DELETE FROM ozon_product_tasks WHERE status IN ('pending','failed','running')` 清 zombie；测试图必须 COS 图；auth 短路验证用空 token。
- subagent 调度失败（2026-09-09 曾遇 provider 认证错误）时回退为主会话按域内联执行，每域完成即落盘 findings。

---

### Task 1: 建立审计工作区（findings 骨架）

**Files:**
- Create: `docs/audit/2026-09-09-race-duplication/findings.md`

**Interfaces:**
- Produces: findings 文件骨架，后续所有域任务向它追加条目；条目 ID 规则 `F-<域字母><序号>`（如 F-C03 = worker 任务生命周期域第 3 条）。

- [ ] **Step 1: 写 findings 骨架**

文件内容：标题 + 七个域的空小节（A-G）+ 状态汇总表（列：ID/类型/严重度/一句话/状态(待验证|confirmed|refuted)）+ spec §3 的条目格式模板原文。

- [ ] **Step 2: 提交**

```bash
git add docs/audit/2026-09-09-race-duplication/findings.md
git commit -m "docs(audit): 竞态与能力重复审计——findings 骨架"
```

---

### Task 2: 域 A —— skill 能力矩阵与重复扫描

**Files:**
- Read: `skill/scripts/cli.py`（子命令注册 :2349-2670）、`skill/scripts/cloud_probe.py`、`skill/scripts/batch_test.py`、`skill/scripts/lib/ozon_discovery.py`、`skill/scripts/lib/ozon_image_search.py`、`skill/scripts/lib/ak_1688_client.py`
- Modify: findings 文件（追加域 A 小节）

**Interfaces:**
- Produces: 「能力 × 命令」矩阵表 + 域 A 发现条目（F-A*）。

- [ ] **Step 1: 枚举命令入口**

```bash
grep -n "add_parser" skill/scripts/cli.py
```

对照 SKILL.md 命令表记录漂移候选（已知：实际 22+ 子命令 vs 文档 10 个左右）。

- [ ] **Step 2: 对每个命令追其主要能力调用链**

重点锚点：信封组装 `build_graph_envelope`（cloud_probe.py:1734）、`follow_sell_cloud`（:3750）及其图搜三级降级链（:3955-4017 aibuy→CDP→AK）、discover 匹配管线（ozon_discovery.py `_search_1688_source`/`_process_match`/`_attach_match_meta`:2191/`rank_match_pool`:1382）、重量链 `_sanitize_weight_g`（cloud_probe.py:1708）与 `DEFAULT_WEIGHT_G`/`estimate_shipping_cny`（ozon_discovery.py:48/57）、batch_test 货源复用 `_find_discover_source`/`_need_cdp`。逐命令记录：图搜通道、类目猜测路径、重量/运费/利润估算入口、信封组装函数、缓存使用。

- [ ] **Step 3: 写域 A 矩阵与发现条目**

矩阵每格：`能力 | 命令 | 实现入口 file:line | 备注(与其他命令一致/分叉)`。分叉处出条目，重点核对：①follow 三级降级 vs discover 管线的 trusted/置信度/类目键归一是否同一套语义；②`_sanitize_weight_g` 与 discover 侧重量清洗规则逐字对比；③利润估算 `_calculate_profit` 调用方是否同源；④四条信封组装路径（graph/follow/discover/batch_test 复用）字段填充差异。

- [ ] **Step 4: 提交**

```bash
git add docs/audit/2026-09-09-race-duplication/findings.md
git commit -m "docs(audit): 域A skill 能力矩阵与重复候选"
```

---

### Task 3: 域 B —— skill 并发/缓存/组装路径一致性

**Files:**
- Read: `skill/scripts/lib/cache.py`、`skill/scripts/batch_test.py`、`skill/scripts/lib/chrome_launcher.py`、`skill/scripts/lib/cdp_client.py`、`skill/scripts/lib/config_store.py`、cloud_probe.py 信封组装段
- Modify: findings 文件（追加域 B 小节）

- [ ] **Step 1: 扫并发点**

```bash
grep -rn "ThreadPool\|threading\|asyncio\|concurrent\|multiprocessing" skill/scripts --include="*.py" | grep -v test
```

记录每处并发（batch_test 是否并行、discover workers>1 旧路径 ozon_discovery.py:119）的共享状态与防护。

- [ ] **Step 2: 扫缓存一致面**

`cache.py` 命名空间/TTL 清单；同一数据（图搜结果/商品详情/费率）是否多处缓存且 TTL 不一致。

- [ ] **Step 3: 四条组装路径字段对照**

graph（build_graph_envelope）/ follow（follow_sell_cloud）/ discover（build_graph_envelope_from_discovery 类路径）/ batch_test（复用 discover 货源）各抽一条真实字段序列，对比 `draft.*`/`extensions.*` 填充差异（discovery_meta、match_evidence、competitor_ref_images、commission_segments 的有无与语义）。

- [ ] **Step 4: 写域 B 条目 + 提交**

```bash
git add docs/audit/2026-09-09-race-duplication/findings.md
git commit -m "docs(audit): 域B skill 并发/缓存/组装一致性"
```

---

### Task 4: 域 C —— worker 任务生命周期竞态写点清单

**Files:**
- Read: `worker/src/utils/task_processor.py`、`worker/src/main.py`（提交/取消/重跑/进度持久化/定期清理）、`worker/src/storage/database/`（队列消费 SQL）、`worker/src/graphs/graph.py`
- Modify: findings 文件（追加域 C 小节）

- [ ] **Step 1: 枚举任务状态全部写点**

```bash
grep -rn "UPDATE ozon_product_tasks\|status\s*=\s*['\"]\(completed\|failed\|running\|cancelled\|pending\)" worker/src --include="*.py"
grep -rn "FOR UPDATE SKIP LOCKED\|STALE_RUNNING\|ZOMBIE\|task_rerun\|cancel_task" worker/src --include="*.py"
```

- [ ] **Step 2: 逐写点判并发防护**

每个写点记录：是否有条件更新（`WHERE status IN (...)` 乐观锁）或行锁；「读-改-写」是否跨 await 窗口；与 4c42dcfb 终态粘性同类的「迟到写翻盘」残余（取消/重跑/stale 恢复/60s 清理器互相竞争）；进度回写 PG（2s 节流）与终态写的先后。

- [ ] **Step 3: 写域 C 条目（每写点一行 + 疑似严重度）+ 提交**

```bash
git add docs/audit/2026-09-09-race-duplication/findings.md
git commit -m "docs(audit): 域C 任务生命周期竞态写点清单"
```

---

### Task 5: 域 D —— worker 后台任务 + 草稿状态机 + 提交链

**Files:**
- Read: `worker/src/mcp_server.py`、`worker/src/services/draft_service.py`（⚠️ 他会话 WIP，只读）、草稿路由（drafts/submit/resubmit/batch-submit/assemble）、后台任务 job_* 实现
- Modify: findings 文件（追加域 D 小节）

- [ ] **Step 1: 后台任务面**

追 job_* 工具的提交/回调/收割路径，核对 4c42dcfb 终态粘性的覆盖范围：粘性保护是否覆盖**所有**可变字段（进度/结果载荷），还是仅 status；重复回调、进程重启恢复路径。

- [ ] **Step 2: 草稿状态机**

并发 PATCH 编辑、box_reviewed 语义、submit 与 resubmit/batch-submit 并发、draft_submissions 与任务表关联（重复提交同一草稿、提交后草稿又被编辑）。WIP 文件内发现照常记录，标注「该文件有未提交 WIP，修复需等落地」。

- [ ] **Step 3: 写域 D 条目 + 提交**

```bash
git add docs/audit/2026-09-09-race-duplication/findings.md
git commit -m "docs(audit): 域D 后台任务与草稿状态机竞态"
```

---

### Task 6: 域 E —— worker 缓存与共享表并发语义

**Files:**
- Read: `worker/src/utils/dict_value_cache.py`（模块注释必读）、`worker/src/services/category_schema_service.py`、`worker/src/utils/commission_resolver.py`、learning_record/backfill 节点、`worker/src/services/store_sync_service.py` 及调度器、main.py `_check_balance_cached`、selection_insight_service
- Modify: findings 文件（追加域 E 小节）

- [ ] **Step 1: 逐表逐缓存判 upsert 语义**

字典三桶（全局哨兵键 (attr,0,0,language)/scoped/ephemeral）、attribute_cache 回写、category_commission 回填、category_mapping 原子 upsert、selection_insights、余额缓存 token 指纹绑定——每处记录：真 upsert（ON CONFLICT）还是先查后插；并发写同一键的裂行/覆盖面（0727e7dc NULL type_id 同类残余）。

- [ ] **Step 2: 调度器重叠执行**

store_sync 调度器、metrics_aggregation、定期清理、预热任务——有无「上一轮未完下一轮又起」防护、退避、单实例锁。

- [ ] **Step 3: 写域 E 条目 + 提交**

```bash
git add docs/audit/2026-09-09-race-duplication/findings.md
git commit -m "docs(audit): 域E 缓存与共享表并发语义"
```

---

### Task 7: 域 F —— worker 能力重复（transport 收敛面 + 唯一入口漏网）

**Files:**
- Read: `worker/src/utils/ozon_client.py` 及 10 个直连 Ozon 的文件（main.py、pricing_node、ozon_upload_node、ozon_status_node、fetch_back_node、auth_node、validation_retry_loop、utils/ozon_dict_values.py、utils/logistics_quote.py）、`worker/src/utils/pricing_estimate.py`、`worker/src/utils/title_formula.py`、`worker/src/utils/attr_value_sanitize.py`、`worker/src/api/errors.py`
- Modify: findings 文件（追加域 F 小节）

- [ ] **Step 1: Ozon transport 直连清单甄别**

```bash
grep -rln "api-seller.ozon.ru" worker/src --include="*.py"
```

已知 10 文件。逐个记录：用途、是否有自己的重试/限流/超时、为何绕过 ozon_client（合理分工如 dict_values 专用分页 vs 重复实现）。判定标准：transport 层能力（鉴权头、重试、限流、错误分类）在两处重复 = 高严重度重复候选。

- [ ] **Step 2: 唯一入口漏网核查**

```bash
grep -rn "compute_price\|build_title_formula\|resolve_commission_rate\|cap_attribute_values" worker/src --include="*.py" | grep -v "utils/pricing_estimate\|utils/title_formula\|utils/commission_resolver\|utils/attr_value_sanitize"
```

对每个调用面确认走唯一入口；再反查有没有「手写公式绕过入口」的残余（内联 ×1.15/×1.2、手拼 commission 0.10 默认、发 values 前未过闸的路径——重点扫 prepare/retry/assemble 之外新出现的 values 出口）。

- [ ] **Step 3: 写域 F 条目 + 提交**

```bash
git add docs/audit/2026-09-09-race-duplication/findings.md
git commit -m "docs(audit): 域F worker 能力重复与唯一入口漏网"
```

---

### Task 8: 域 G —— webui 竞态表面 + 引导内容漂移

**Files:**
- Read: `webui/src/components/`（任务/草稿/店铺相关组件：轮询、提交按钮防抖、编辑抽屉状态）、`skill/SKILL.md`、`skill/references/*.md`、`pounding-mcp/pounding_mcp/server.py`（工具签名 vs CLI 实参）
- Modify: findings 文件（追加域 G 小节）

- [ ] **Step 1: webui 竞态表面清单**

只列组件级嫌疑：轮询定时器未清理、重复点击提交无禁用、编辑抽屉保存与轮询刷新互踩、乐观更新回滚缺失。每条 file:line + 一句话，标注「前端单方证据弱，E2E 才可证实」。

- [ ] **Step 2: 引导内容漂移**

SKILL.md 命令表逐条对 `cli.py --help` 实测（`python3.12 scripts/cli.py <cmd> --help`）；references/ 文件清单与错误码文档对 `worker/src/api/errors.py` 实数（14 个）；pounding-mcp server.py 工具参数映射抽查 5 个对 CLI 签名。

- [ ] **Step 3: 写域 G 条目 + 提交**

```bash
git add docs/audit/2026-09-09-race-duplication/findings.md
git commit -m "docs(audit): 域G webui 表面与引导漂移"
```

---

### Task 9: Phase 2 —— 动态证实（高/中嫌疑逐条 repro）

**Files:**
- Create: `worker/tests/test_audit_repro_*.py` / `skill/tests/test_audit_repro_*.py`（每条 confirmed 候选一个可复跑 repro；race 类也可用 `docs/audit/2026-09-09-race-duplication/repro/` 下独立脚本）
- Modify: findings 文件（状态列翻 confirmed/refuted + 证据链接）

**Interfaces:**
- Consumes: Task 2-8 产出的全部高/中严重度条目。
- Produces: 每条目一个可复跑证据（测试名或脚本路径 + 运行输出结论）。

- [ ] **Step 1: 按域排序嫌疑清单，逐条设计 repro**

race 类 repro 模板（并发双写 + 迟到写翻盘断言）：

```python
import asyncio

async test_late_write_cannot_flip_terminal_state():
    # 1) 造任务至终态 completed
    # 2) 模拟迟到回调/旧 checkpoint 以旧 status 写回
    # 3) 断言：终态未被翻盘（预期现行实现已防=证伪该条；被翻盘=confirmed）
    ...
```

duplication 类：行为对照——同一输入分别喂两条实现（如 follow 三级降级 vs discover 管线的同一货源候选），diff trusted/置信度/类目键输出。

- [ ] **Step 2: 逐条运行并回填结论**

race 跑法（worker 侧纯 mock 优先，需 PG 的用 5433）：`cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_audit_repro_<id>.py -q`；skill 侧：`cd skill && .venv314/bin/python -m pytest tests/test_audit_repro_<id>.py -q`。每条回填：confirmed（复现输出摘要）/ refuted（防护点 file:line）/ need-more-evidence（升级到 Task 10 真单）。

- [ ] **Step 3: 回归确认 repro 不破坏基线**

```bash
cd worker && PGDATABASE_URL="postgresql://postgres:localdev123@localhost:5433/ozon" PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/ -q
cd skill && .venv314/bin/python -m pytest tests/ -q
```

Expected: 全绿（新增 repro 测试本身应全过——它们验证现状行为，不修码）。

- [ ] **Step 4: 提交**

```bash
git add worker/tests/ skill/tests/ docs/audit/2026-09-09-race-duplication/
git commit -m "test(audit): Phase2 动态证实——repro 与结论回填"
```

---

### Task 10: 本地 Docker E2E（仅限 need-more-evidence / 用户可见效果类）

**Files:**
- Create: `docs/audit/2026-09-09-race-duplication/e2e-wave-log.md`

- [ ] **Step 1: 环境准备**

```bash
cd deploy && docker compose up -d --build
docker compose exec worker python scripts/init_data.py   # 类目树空才需要
docker exec deploy-worker-1 psql -U postgres -d ozon -c "DELETE FROM ozon_product_tasks WHERE status IN ('pending','failed','running')"
curl http://localhost:8080/api/v1/health
```

- [ ] **Step 2: 分波真单（测试店）**

仅对 Task 9 标记 need-more-evidence、或结论依赖「Ozon 真实审核回包」的条目提交真单（COS 图）；每波 ≤3 单，跑完回查终态/留存表/审计表后向用户汇报再进下一波。空 token auth 短路用例同波验证。

- [ ] **Step 3: 记录 wave log + 提交**

```bash
git add docs/audit/2026-09-09-race-duplication/e2e-wave-log.md
git commit -m "docs(audit): E2E wave 记录"
```

---

### Task 11: 发现清单定稿 + 修复波规划（用户拍板关口）

**Files:**
- Modify: findings 文件（定稿：排序 = 严重度 × 用户影响；每条含修复方向与预估波次）
- Create: 修复波规划小节（P0/P1/P2 分波 + 每波入口条件）

- [ ] **Step 1: 清单定稿**

全部条目二态闭环（confirmed/refuted）；confirmed 按 P0（用户可见/资损/合规）、P1（数据一致性）、P2（统一入口重构/引导对齐）归波；跨域重构（1688 匹配统一层、Ozon transport 收敛）标明依赖顺序。

- [ ] **Step 2: 向用户呈交清单与波规划，等待拍板**

**此处停——修复属 Phase 3，须用户批准后按波另写详细 plan（TDD 任务级），不在本 plan 范围内。**

---

## Self-Review 记录

- Spec 覆盖：spec §3 七域 ↔ Task 2-8 一一对应；Phase 2 ↔ Task 9-10；Phase 3 关口 ↔ Task 11。spec §2 摸底锚点已内嵌到对应任务的 Step 中。
- 占位符：Task 9/10 的具体条目依赖 Task 2-8 产出，属刻意的两段式规划（spec §5 已声明），非占位符；所有「怎么做」步骤均给了可执行命令或模板。
- 类型一致性：条目 ID 规则 `F-<域字母><序号>` 在 Task 1 定义、Task 2-8/9 沿用。
