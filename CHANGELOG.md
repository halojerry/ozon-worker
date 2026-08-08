# Changelog

## [0.30.0] - 2026-08-08

> hyperplan 对抗规划落地：worker 属性匹配修复（retry 止血 + fetch-back 回读闭环 + 学习 provenance）+ skill runtime 稳定化（顶层 preflight + CDP 统一）。规划文件：`.omo/plans/attribute-matching-runtime-stability.md`。

### Fixed(worker retry 止血 — 上品成功率核心)

- **删 retry 盲补字典首值**（`validation_retry_loop.py` Step 2.5）：字典属性语义解析+API 搜索未命中 → 直接跳过，绝不取 `_dict_vals[0]`。首值=语义随机：8229 可能取同大类其他小类（「套娃」错配实证）、9782 可能取「Класс 1 爆炸物」→ BR_hazard_class1。与 assemble/prepare 已统一的「宁缺毋滥」纪律对齐。
- **revalidate 加危险品守卫**（对称 prepare_ozon_upload_node.py:1877）：9782 在 retry 阶段只放行「非危险」安全值，其余跳过重传。
- **revalidate 加 is_aspect 守卫**（`attribute_utils.is_aspect_attr` 新增，A4 首次落地）：schema `is_aspect=true` 属性（交付出仓后不可改）retry 跳过，避免 attributes/update 被拒白烧一轮。
- **recheck 对 rejected_unfixable 早退**：不再带旧 task_id 空轮询（省 API 调用）。
- **`/values` limit 5000→2000**（官方 max）；**`/values/search` ≥2 字符守卫**（官方 value 最少 2 字符）。
- **prepare post-fill 中文清零**（L952 必填 + L1083 可选）：主转换循环（L1982-1990）之后 append 的字典属性 value 含中文直接置空（dict_id 权威），堵住「双保险」的第三处漏修。
- **8292 移出 `_KNOWN_DEFAULTS_RETRY`** → 统一走 `attr_defaults.resolve_merge_card_default` 字典解析路径。
- **stale 注释清理**：9 处 `/v1/product/prices/update` → `/v1/product/import/prices`（旧端点不存在）。
- **assemble 8229 type_id 匹配中文清零（Sentry POUDING_OZON-AA/9Y/9T 等实证）**：`_validate_and_enrich_items` 的 8229 type_id 分支从 ZH_HANS dict_lookup 取值未过 `_clean_dict_value` → 「垂钓诱饵/桑拿香薰/儿童泡泡机」等中文 value 直达 payload（ozon_validate 拦截上报）。修复：type_id 命中时 value 含中文置空（dict_id 权威），与 prepare post-fill 一致。
- **zombie recovery 安全开关（Sentry 实证触发真实上架）**：启动清理会复活 `retry_count < max_retries` 的 failed 任务——本地/测试环境会用真实凭证重新上架旧任务（实测 4 个任务被激活真实上传）。新增 `SKIP_ZOMBIE_RECOVERY=1` 环境变量跳过全部复活；本地测试必开（`deploy/.env` 已加，容器内验证「任务保持 failed 不复活」）。

### Fixed(worker 学习闭环 — 切断 Goodhart 棘轮)

- **fetch-back 回读闭环（P0）**：`fetch_back_node` 在 approved 后调 `/v4/product/info/attributes` 回读 Ozon 真实存储值 → diff 发送 vs 存储 → 检出 dict_id 漂移/被擦除/Ozon 自动填默认（`attributes_with_defaults`）→ `attr.outcome` 结构化遥测。graph 路由：`成功 → fetch_back → learning_record`。
- **学习门收紧（R6）**：被 Ozon 擦除 + 自动填默认的属性不写入学习（「Ozon 没查这个字段」不再被学习成「这个值是对的」）。
- **9782 erase 事件入遥测（R7）**：IGNORABLE_CODES 保留（防 retry 死循环）但擦除不再静默。
- **学习 provenance（PR-6）**：`ozon_attribute_mappings.source` 列（learned_approved/default_fallback/retry_recovered/fetch_back_corrected）+ 幂等迁移；default_fallback 复用不增长 success_count（切棘轮）；prepare 按置信消费（retry_recovered 隔离、fabricated `[{name}]` 跳过）；`scripts/backfill_mapping_source.py` 历史回填（dry-run 默认）。

### Fixed(skill runtime 稳定化 — 链路体验)

- **顶层 `_preflight_runtime`**：Python≥3.12 + requests/websocket/PIL 探测，缺依赖立即精确提示 + return 1（不再链路深处炸 traceback）。
- **4 处 ModuleNotFoundError 精确归因**：缺依赖 → pip 指引；缺模块 → 升级指引（不再误导「版本过旧」）。
- **cmd_check 不 early return**：无浏览器也继续探测 Worker/MXOU/凭证，一次性全环境诊断。
- **graph/follow/image_search(cdp) 入口前置 ensure_chrome_cdp**：缺 Chrome 立即报错（不再等 enrich 60s）。
- **updater 跨进程锁**（fcntl/msvcrt on data/.update.lock）：并发 CLI auto-update 不再竞态破坏安装。
- **RATE_LIMIT_PER_MINUTE 默认 10→300**：与 AGENTS.md/.env.example 对齐（批量提交不再 429）。
- **CDP 统一**：profile 双轨消除（全部 `profiles/1688/default`）、删 legacy 无锁 Popen、find_tab 复用用户 tab 后立即 release（不再误关用户标签页）、`_check_1688_login_live` cookie 优先零 tab 检测（降级导航 5 分钟缓存）、`scripts/migrate_profile.py` 迁移脚本（dry-run 默认）。
- **follow 信封透传 `ozon_attributes_category`**（与 graph --ozon-ref-url 一致）：worker 类目一致性校验防跨类目属性错配。

### Refactored

- **retry schema 查询 PG 缓存优先**（`_get_attribute_schema` 先查 attribute_cache 再 Ozon 直查，与主路径共享缓存）；`/values/search` body 去掉 language 字段（官方无此参数，语言仅控制 fallback 链）。

### 测试

- worker 257 passed（新增 15：retry 盲填删除/hazard/aspect 守卫/unfixable 早退/8292/retry_count/fetch_back 7 用例）；skill 87 passed（新增 6：preflight 4 + updater 锁 2）。
- 本地 Docker 实测：graph 26 节点编译（含 fetch_back）、PR-1 守卫源码断言、fetch_back diff 端到端（mock /v4）、provenance 消费门全部通过。
- 遗留 14 个环境失败（PGDATABASE_URL/uvicorn 缺失）与本次改动无关（git stash 验证）。

### 已知边界（显式 scope-out）

- PR-2 assemble 大函数物理移动 → `docs/TODO-attribute-util-extraction.md`
- PR-5 attr_defaults 扩消费（9554/18270/9160 无实证不盲加）→ 计划文件
- D1 类目错路径 category-repair 节点 → `docs/TODO-category-repair.md`
- zombie recovery 复活 failed 任务在本地环境会误激活旧任务（真实上架）→ 本地测试需先清空任务表

## [0.29.3] - 2026-08-07

> 用户电脑 CDP 全崩根因修复(REALISTIC_UA 混装) + follow 前置 ensure Chrome + MXOU 余额本地直查 + 工具 Chrome 常驻。

### Fixed(REALISTIC_UA 混装, 今天所有抓取故障源头)

- **stealth.py 极简版删了 REALISTIC_UA, service.py 4 处裸引用未删**(1619/1800/1836/1923):
  一调即崩 `cannot import name 'REALISTIC_UA'` → CDP 图搜/探针/graph 抓取全挂 →
  降级 API → 匹配垃圾结果/无货源。修复: 4 处引用删除(真实 Chrome 用真实 UA,
  伪造 UA 反而增加检测风险, 与 v0.28.7 极简设计一致), 仅保留 STEALTH_JS 注入。
- **follow 未前置 ensure Chrome**: 命令出口关闭 Chrome 后, 下次 follow 抓 Ozon
  连不上 9222 → 空数据 → 图搜用错图/文字搜索兜底(错配货源根因之一)。
  修复: `follow_sell_cloud` Step 2 前 `ensure_chrome_cdp(port=9222)`。

### Changed(工具 Chrome 生命周期, 用户反馈体验一致)

- **独立 profile + 常驻**(去掉命令出口 close_tool_chrome):
  v0.28.6「用完即关」被反馈体验不一致——每次命令 Chrome 被关, 用户手动开的
  浏览器却能保持。常驻后登录态复用, 下次命令 CDP 可用即直接用;
  v0.28.4 常驻的坑(撞用户 Chrome 默认 profile 单实例锁)已被独立 profile 消除。
  close_tool_chrome() 保留但不自动调用。

### Feat

- **MXOU 余额本地直查**(config_store.fetch_mxou_balance): check 命令显示余额/
  欠费标红预警, 与 Worker 鉴权同源(欠费 key 提前暴露, 不再白跑生图)。

## [0.29.2] - 2026-08-07

> 描述拉丁字符修复(Sentry POUDING_OZON-60 实证) + Sentry 任务重跑监控 + 生图提示词线上配置入库。

### Fixed(描述含拉丁字符, Sentry 实证三层根因)

- **validate 误报主因**: `ozon_validate_node` 的 `[a-zA-Z]` 直接检测整个 description,
  规格表 HTML `<table class="ozon-spec"><caption>` 标签本身是拉丁 → 凡有规格表的
  描述必报「描述含拉丁字母」。修复: 检测前剔除 `<[^>]+>` 标签, 只查正文。
- **富文本描述(4191)漏清拉丁**: `_sanitize_rich_description` 注释说清拉丁但代码
  漏了(HTML 标签含拉丁, 当初跳过) → 补 `[a-zA-Z]{2,}` 正文清理。
- **规格表属性值污染**: `_append_spec_table` 在净化后追加, 属性值(Black/USB/One Size)
  不经净化直接进描述 → 追加前清理, 清空跳过该行。

### Added(监控)

- **Sentry 任务重跑监控**: `capture_task_event` 通用事件上报; 三监控点——
  启动 zombie 恢复(`zombie_reset`) / 超时 stale 重置(`stale_running_reset`) /
  重跑任务 `retry_count>0`(`task_rerun`)。Sentry 搜 `task_event:*` 可查
  「是否偷偷跑任务/异常重跑」。

### Changed

- **生图提示词以线上配置为准入库**: main/scene_1/scene_2 无「均禁止」后缀(按线上),
  scene_2/3 修「任何、水印」笔误; JSON + `_DEFAULT_PROMPTS` 同步(漂移测试过)。
- Sentry MCP OAuth token 已配入本机 `~/.reasonix/config.toml`(1h 过期, 需刷新)。

## [0.29.1] - 2026-08-07

> 服务器 v0.29.0 部署实测 3 修复 + gha 构建缓存 + tests 进部署包。

### Fixed(服务器实测)

- **① deploy.sh latest 标签**: build 后显式 `docker tag v{VERSION} latest` + `up -d` 带
  `VERSION` —— 原 up 无 VERSION 落到旧 latest 镜像(服务器跑旧 0.27.0)。
- **② 多阶段镜像 PYTHONPATH**: compose 的 `PYTHONPATH=/app/src` 覆盖镜像
  `/opt/venv/...` 导致 uvicorn/依赖找不到容器重启 → 改为
  `/opt/venv/lib/python3.12/site-packages:/app/src`。
- **③ 部署包含 tests**: cd.yml 打包不再排除 `worker/tests`(服务器可跑 pytest)。
- **④ cos-deploy manifest url 空字段**: manifest 生成 step 补 `COS_BUCKET/REGION` env。

### Changed

- **worker 镜像 gha 构建缓存**: ci.yml/cd.yml `docker build` → `build-push-action` +
  `cache-from/to: type=gha`(pip/基础镜像层跨发版复用, 5-10min → 1-2min)。
- 宝塔环境 `stop_grace_period` 若被面板丢弃属环境限制, 原生 Docker 生效
  (Docker 28 + compose 2.32 实测支持, 见 DEPLOY-CHECKLIST.md)。

## [0.29.0] - 2026-08-07

> CI/CD 规范化 + Worker 稳定性(PRD-cicd-stability 第一批 7 项)+ cos-update.sh 一键升级。

### Worker 稳定性

- **P0 并发竞态修复**: `_current_task_id` 模块级 global → `contextvars.ContextVar`
  (asyncio 多任务并发不再串号; 新增 4 断言并发测试)。
- **优雅关闭**: `SHUTDOWN_FLAG` + lifespan drain(轮询 PG running 数, 最多 5 分钟)
  + docker-compose `stop_grace_period: 5m` —— `--force-recreate` 不再强杀运行中任务。
- **Dockerfile 多阶段构建**: builder(编译链) → runtime(libpq5+curl),
  镜像 ~800MB → ~400MB。

### CI/CD

- **CI 合并**: 单一 ci.yml(worker-ci.yml 删除, push 不再双跑); 去 `|| true` —
  worker ruff 全规则阻断(修 1 错), skill ruff `--select F --ignore F821` 阻断
  (ruff --fix 修 skill 160 处 + F841 下划线)。
- **gitleaks 密钥扫描**(近 3 个月, 避开历史已泄露基线)。
- **pip-audit 依赖漏洞**(警告模式)。
- **cd.yml cos-deploy**: tag push 自动打包 deploy/+worker/ 源码 →
  COS `/ozon-worker/` + manifest.json(sha256; tar 排除 .env/旧包/凭证)。
- **deploy/cos-update.sh 一键升级**: 读 manifest → sha256 校验 → 备份 →
  覆盖(保留 .env) → 优雅重建 → 健康检查失败自动回滚。

## [0.28.6] - 2026-08-07

> Chrome 独立 profile 根治「无限重启」+ 登录态持久化 + env-setup 文档更新。

### Fixed

- **Chrome 无限重启根治**(用户电脑 Windows/mac x86 实测复现):
  - 根因: check/discover 直接 ensure 用**默认 profile**(用户 Chrome), 用户 Chrome
    无 CDP 占用 profile → 杀不掉(只杀带 --remote-debugging-port 的实例)→ 启动
    新实例撞单实例锁 → 反复杀/重启失败; service 探针(graph/follow)早已用独立
    profile, 两条路径不一致。
  - 修复: `_default_profile_dir` → skill `data/browser/profile`(独立, 不碰用户
    Chrome, 避开 macOS TCC; data/ 随更新保留登录态)。**用户 Chrome 永不被杀/重启**。
  - 进程活着检查改 `Popen.poll()`(不依赖 ps/PowerShell 命令行解析, Windows/mac
    命令行截断/沙箱禁 ps 都不再误判)。
  - cli.py/batch_test 出口 finally `close_tool_chrome`(用完即关, 不常驻不累积;
    v0.28.4 常驻方案在「用户 Chrome 常开」场景死锁)。
  - 启动命令加 `--disable-crash-reporter`/`--disable-breakpad`(Crashpad 固定写
    用户 Chrome 目录, 权限受限环境导致启动崩溃)。
  - **登录态持久化**: cookies 存磁盘 profile, 关浏览器不丢, 重启自动恢复(已实测)。
- chrome_launcher 移回明文(编译态无法热迭代, cloud_probe 同款先例)。
- B1 补提交: 8 辅助生图节点 max_retries 2→1(a128249 遗漏, v0.28.5)。

## [0.28.5] - 2026-08-07

> Worker 八项优化(基于 Ozon 三店审计 317 商品/65 问题/20.5% 问题率, 本地 wave5 实测 5/5 上架成功)。

### Added

- **A1 错误码映射补全**: REPAIR_STRATEGY 补 9 个未映射错误码(审计 50 次错误不再 LLM 盲修):
  - `VALUE_MUST_BE_INTEGER`/`VALUE_MUST_BE_DECIMAL`/`ATTRIBUTE_VALUE_COUNT_EXCEEDED` → repair_prepare
  - `EMPTY_REQUIRED_AFTER_WARNING_DELETING`/`warning_attribute_values_empty`/`erased_attribute_value`/`CONDITIONAL_ATTRIBUTE_ERROR` → error_repair_llm
  - `SPU_ALREADY_EXISTS_IN_ANOTHER_ACCOUNT`/`all_image_failed` → unfixable(不浪费重试)
  - `marking_auto_corrected` 明确化 → error_repair_llm
- **C1 skill query 命令**: `cli.py query <任务ID>` 查状态/耗时/产品明细(agent 不再盲等)
- **C2 失败中文 notice**: ERROR_NOTICE_MAP 17 码 → 中文可读失败说明, task_status error_message 直接可读
- **E1 原始图转存 COS**: `utils/cos_uploader.py`(boto3 S3 兼容 COS), AI 生图全失败时下载原始图转存 COS 补位(未配置 COS 优雅降级)

### Changed

- **B1 生图重试 2→1**: 8 个辅助生图节点(comparison/detail/multi_angle/scene×3/social_proof/white_bg)max_retries 2→1, 主图保留 2(单商品省 8 次潜在 API 调用)
- **B2 提示词物流排除**: 10 条生图提示词统一加「物流信息/退换货说明/地址/电话/联系方式/店铺名称」排除(JSON + 模块默认同步, 热加载)
- **D1 draft_sanity**: 入队防线补 weight<=0/尺寸缺失或含 0 拦截(防 0 重量打爆定价)
- **A3 回归测试**: 核实 prepare 主路径已有标题/描述/属性值净化链(sanitize_title + 中文清除), 补 6 断言确认零拉丁/中文

### 部署注意

- deploy/.env 新增 COS 凭证启用 E1: `COS_SECRET_ID`/`COS_SECRET_KEY`/`COS_BUCKET`/`COS_REGION`(可选 `COS_PUBLIC_DOMAIN`)
- 新增测试: test_repair_strategy_mapping(7) / test_notice_build(7) / test_title_sanitizer(6) / test_cos_uploader(6)

## [0.28.4] - 2026-08-06

> Chrome 跨平台重复开启修复 + 常驻复用 + 文档增强(0.28.2 后 4 个 commit 合并发布)。

### Fixed

- **Chrome 跨平台重复开启**(用户反馈"一直重复开启浏览器", Windows/Mac):
  - 根因1: ensure_chrome_cdp 持锁复查依赖 ps/PowerShell 命令行解析判
    --remote-allow-origins, 解析失败/截断 → 误判缺参 → 杀 Chrome → 重启 → 循环。
    → 删除两处"缺 allow-origins 杀重启", CDP 可用即信任(连接 403 由调用方报错, 绝不杀浏览器)。
  - 根因2: 工具 Chrome 用完不关, 用户手动关后下次又新开。
    → v0.28.3 PID 追踪用完即关 → **v0.28.4 改为常驻复用**(更优):
    check 启动后引导登录 1688 + Ozon Seller, 浏览器保持常驻, 后续命令 CDP 可用即复用,
    不再每次弹新窗口; 用户手动关闭后下次命令自动重启。
  - check 新增登录引导: 未登录 1688 自动打开 login.1688.com + seller.ozon.ru
    (discover 运营指标需要卖家登录), 交互等待登录后 Enter; 非交互提示重跑。
- 用户反馈 6 项: env-setup pip3.12 修正 / output-schema 补 product_summary 字段 /
  error-codes check_task_status 同步提示 / C-D 澄清(--auto-submit 显式参数)。

### Added

- command-reference 并发限制表(Chrome 单实例串行/1688 配额/batch_test 内置 delay)+ 批量操作规则。
- error-codes 错误恢复决策表(每错误码下一步 + 自动重试最多 1 次)。
- output-schema submit_result/check_task_status JSON 示例 + status 取值/终态判定。
- SKILL.md 管线 E 市场信息三级降级(web_search → SEARXNG_URL → 问用户手动提供/退回 C)。

### Tests

- skill 81 passed; 行号引用(5 处)与代码逐一核对无误。


## [0.28.0] - 2026-08-06

> Skill 架构重构版(执行 PRD Phase A-F + 两轮 agent 实测修复)。Worker 代码零改动。

### Added

- **references/ 渐进式披露**:SKILL.md 455→150 行骨架(意图路由/速查表/边界/越界/索引/更新),
  拆 4 个专题文件:command-reference/error-codes/output-schema/env-setup;compile.py DOC_FILES
  含 references/(复制支持子目录)。
- **frontmatter 合规**:agent_created + description 第三人称 + version(与 VERSION 一致)。
- **口语化意图路由**:真实用户表达全覆盖——「帮我上产品/选50个/上点/有什么好卖的/昨天那个再上一遍/
  整一批夏季爆款」;口语动词归一、无对象追问(③a)、数量词规则、复合意图消歧、重上已上商品防重复、
  图搜→上架确认衔接、URL+弱化词先 --no-submit。
- **跨 Agent 兼容**:python3.12→python3(38处)+ §0 定位 Skill 目录(SKILL_DIR/当前/上级)。

### Fixed

- 蓝海触发词冲突(command-reference vs 决策表相反)→ 统一「蓝海→C,蓝海趋势/市场分析→E」。
- 「C/D 区别只在 follow_type」不实声明删除(discover 无此参数,跟卖标记内部注入)。
- frontmatter description 与决策表语义不一致;batch_test 凭证来源说明;声称数量与链接数不符追问;
  check 故障表;审核被拒 vs 提交失败区分;管线 E 无搜索兜底。

### Tests

- agent 实测两轮:工程化 9 场景(修复 9 项)+ 真实口语 10 场景(修复后 10/10 可路由)。
- skill 全量 81 passed;deploy/skill 同步一致。


## [0.27.0] - 2026-08-06

> 合并 v0.26(生图额度/队列/帽类属性)+ v0.27(信封治理/类目修复/出参审核状态)。
> 本地 Docker 真实上架验证(挂脖风扇/风扇帽/蚊香盘 3 个 approved),wave4 实测三修复。

### Added

- **信封治理标准(ENVELOPE-STANDARD.md)**:skill→agent→worker 三方数据边界——🅰 判定层
  (运营数据)不进信封、🅱 worker 执行层必须全消费、🅳 出参标准;逐字段核实(删除前
  验证 worker 实际消费)。
- **出参审核状态(v0.27)**:product_summary 补 `ozon_status`(approved/pending/declined)+
  `ozon_error`;OzonStatusInput + GraphOutput 补 moderation_status(output_schema 过滤
  致终态丢审核状态的根因)。
- **学习表治理(v0.27)**:删污染(甩脂机品牌ID 101029485/手串错配,66% 污染率实证)、
  写入前 dc/type 树存在性校验、查询门槛 success_count≥3。
- **方案B 直采类目(v0.27)**:skill poll_category=True + search_categories 语言自动选择
  (中文→ZH_HANS);worker `_resolve_skill_category` 校验采用 Seller 空间类目(跳过 pg_trgm 猜)。
- **性别双值兜底通用化(v0.27)**:9163 或属性名含 пол/性别/gender 的必填属性一律
  「中性词→男+女双值」兜底;dictionary_id=0 也强制进 required_dict(帽子类实证)。
- **品牌 31 补全**:BRAND_ATTRIBUTE_IDS=[85,5076] 缺 31(服装和鞋类品牌,帽子类必填)。
- **v0.26**:生图额度暴烧修复(P0-1 图内 pending 死循环/P0-2 队列无限重跑有界化/
  P0-3 生图失败分类不重POST/P0-4 task_generated_images 生图幂等)、帽类 9163 男+女双值、
  数字属性类型校验(8205/11650/4497/7444)、字典属性全量填满、工具链(offline_validate/
  analyze_category_mismatch/audit_products/Sentry 全局监控)、wave1/2 实测修复
  (seller analytics 借道/premium 解锁/退货率/图搜徽标降级/空壳跟卖拦截/定价统一公式/
  假成功透出/尺寸方向反转)。

### Fixed

- wave4 实测三修复(本地 Docker 真实上架):9163 dict_id=0 被筛出 required_dict →
  性别兜底失效 → 缺 9163 被拒;品牌 31 缺失;GraphOutput 缺 moderation_status →
  ozon_status 出参恒空。
- test_mxou_polling 同步 v0.26 超时抛异常(v0.25 断言过期)。

### 信封清理(v0.27)

- 删除无用字段:stock×5、max_skus/dropped_skus/drop_reason/filtered_skus;
- 10 个竞品运营字段移出信封(属判定层,skill 采集/展示保留);
- 保留 worker 兜底物理字段:competitor_weight_g/competitor_dimensions_mm;
- shipping/price/original_price 标记废弃保留(展示/折叠内部用)。

### Tests

- 新增 test_skill_category_direct(5 用例)/test_ozon_status_mapping(5 断言)/
  test_gender_attr_with_dict_id_zero(1 用例);全量 211 passed。


## [0.25.0] - 2026-08-05

> wave3/wave4 本地真机测试 + 修复：跟卖/直采 12 个产品 11 个 approved（浴刷已救活），
> 全链路根因修复 + 尺码表入库 + COS 全球加速抓图。

### Added

- **尺码表入库（F1a）**：儿童/男性/女性服装 + 鞋子四张 CSV 随镜像分发，
  `init_data.py` 部署时自动导入 PG `size_mappings`（children 127 / female 104 /
  male 88 / shoes 34 行实证），`size_mapper` 查询优先（PG → 本地兜底）。
- **必填字典属性语义解析（F1b/F1c）**：`attr_defaults` 品牌/性别/尺码/8292/型号
  语义默认值，prepare/retry 统一接入；无颜色来源 → 中性默认色
  （прозрачный→белый）；性别无来源 → Унисекс/Универсальный（列表模式关键词兜底）。
- **1688→Ozon 类目映射学习闭环（T1）**：skill 信封补 `source_category_id`，
  worker follow/直采优先查学习表，approved 后回写。
- **必填字典属性 live search 兜底（T2/T3）**：`/values/search` 公共封装，
  解析失败必走 live search；非必填字典属性 attr_synonyms 跨语言同义词填满。
- **商品描述规格参数表（T4）**：4191 富文本追加 Характеристики 表格。
- **AK 搜索结构化字段 + 趋势选品（S1/S2/S3）**：moq/48H发货率/销量/发货地/标签 +
  筛选参数；信封补 1688 类目数字 ID；`trend` 命令（web 趋势→AI 细分关键词→
  AK 搜索满 3 停）。
- **Ozon 竞品属性优先填充（v0.25 wave3）**：follow 信封透传 `ozon_attributes`，
  worker 按竞品俄语值搜索 → 1688 推断 → 兜底；8229 短词搜索；标题颜色推断；
  裤袜/女性专属品类词；One-size 尺码兜底；8292 自由文本「Нет」最后兜底。
- **生图模型路由（v0.25）**：main/social_proof 用 gpt-image-2，其余 banana；
  grsai 轮询前 30s 不轮询 + 每 5s 一次；提示词 v0.25 修订热加载。
- **Sentry 错误监测（v0.23）**：SENTRY_DSN 可配，任务异常/超时自动上报。

### Fixed

- **跟卖上传禁竞品图（wave4 浴刷 0 图下架根因）**：AI 图不足 10 张不再用
  竞品 ir.ozone.ru 补位（Ozon 抓竞品 CDN 图失败 → 整卡 0 图被下架）。
- **审核通过后路由死循环（wave4 实证）**：条件边收到 OzonStatusInput 强转 state，
  moderation_status 被剥 → approved 永远看不到 → 每秒重跑 ozon_status；
  兜底 imported+success+product_id → 成功。
- **ozon_status 404 回退**：import/info 任务不存在（product_id 当 task_id 轮询）
  → 回退 info/list 查真实状态，不误判失败；404 回退查无商品立即失败。
- **OzonStatusInput 补 ozon_task_id（d897efd）**：修复 import/info 阶段被入参过滤
  剥掉导致的 product-id 误用；校验失败返回真实 product_id。
- **制造商 23487 中文零容忍**：中文供应商名必须俄语化（LLM 翻译，失败兜底
  「Китайская компания」）——中文供应商整单被 Ozon 拒（BR_chinese_hieroglyphs）。
- **COS 全球加速域名抓图**：payload 统一改写 `cos.accelerate.myqcloud.com`
  （Ozon 跨境抓图更稳，wave4 图抓取失败频发实证）。
- **offer_id 统一裸竞品 ID**：import-by-sku/assemble/prepare 三处一致，
  防 api 复制模式双卡；import-by-sku 超时不 fallback CREATE（P2a）。
- **店铺路由（F4）**：`_get_ozon_credentials` 显式 OZON_CLIENT_ID/API_KEY 环境
  变量优先——batch --client-id 被 stores.json 默认店覆盖问题。
- **竞品兜底安全（P3）**：int 安全转换 + 部分尺寸缺失兜底；repair_pricing 无定价
  阻断（删 999 兜底，P2b）；api 兜底信封校验放宽（P1）。
- **Docker pytest-asyncio**：容器全量回归可运行（170+ 测试全绿）。

## [0.22.0] - 2026-08-04

> 经营数据闭环：worker 拒绝原因可行动化 + 完成结果返回产品经营明细，
> skill 提交后可选轮询展示（--wait）。

### Added

- **完成结果产品明细（v0.22）**：worker 任务完成后，`task_status` 的 result 增加
  `product_summary` 数组，每个产品一条：1688 采购链接、利润率（margin_rate）、
  售价、采购价、运费预估（logistics_cost）、净利润率、Ozon 商品ID；
  多 SKU 变体每变体一条。GraphOutput 契约同步新增 `product_summary` 字段。
- **提交拒绝原因可行动化**：skill `submit_envelope` 不再吞服务端错误——
  解析统一错误格式（error_code/message/detail）与 FastAPI detail，
  返回结构化原因，agent 可直接看到「token 无效/余额不足/配额不足/信封异常」及解法。
- **batch_test --wait**：批量提交后轮询任务终态，逐产品打印
  1688链接/利润率/售价/采购价/运费预估；新增 `--wait-timeout`。
- **worker 自修复升级（经验固化）**：
  - repair_prepare 尺寸单位改 cm/mm 交叉判定（旧"密度<1.0 无差别/10"会把
    修车躺板 1100mm 错砍成 110mm）；
  - `price_out_of_range` 映射到 repair_pricing（原走 LLM）+ 取价修正
    （pricing_info 实际键是 price，旧代码取 final_price 永远落空）；
  - 字典值搜索语言链 ZH_HANS→RU→EN（Ozon 字典值是俄语，旧 ZH→EN 搜不到
    8229「вентилятор→Hand Fan」）。
- **Windows 体验**：chrome_launcher 进程检测 wmic→PowerShell（Win11 弃用 wmic
  导致频繁启动新实例）+ profile 目录自动创建（缺失会开全新浏览器无登录态）。
- **图搜**：输入框选择器兼容新版页面 `.ali-search-input`（旧 `#alisearch-input`
  不存在 → 输入失败 → 空输入点搜索误触上传图片按钮）；点击前校验 URL 已填入；
  3 次分段滚动合并候选；RU→ZH 产品词映射扩充（套筒/撬棍/水平仪/风扇/套装等）
  + 标题相关性多词加权；无徽章降级阈值 conf 0.4→0.3。
- **discover 品牌过滤**（参考 maozi 插件 brand_option）：用 Ozon widget API
  （product id 查询）返回的 brand 字段（英/俄文）布尔判断——`без бренда`/空 =
  无品牌，其它（含白牌）算品牌；`--brand-filter` 三档：nobrand=只要无品牌/白牌
  （默认，规避品牌侵权）、known=只过滤知名品牌黑名单（Nike/Apple/博世 等 60+）、
  all=不过滤。命中直接 filtered 跳过，不浪费 1688 匹配/图搜/生图资源。
- **image_search --source cdp**：CLI 支持 CDP 网页版图搜（默认 ak=1688 AK API；
  需要 Chrome 登录 1688 时用 cdp，准确率更高）。
- **seller.ozon.ru 登录态检查**：check 命令区分两个登录态——www.ozon.ru
  （选品/DataDome）与 seller.ozon.ru（卖家后台，运营数据依赖）；seller 未登录
  时明确提示登录卖家后台；discover 运营数据全部缺失时同样提示
  （agent 需要月销量/销售额/增长率判断选品）。
- **token 引导**：set_token 输出提示访问 https://api.mxou.cn 注册获取。
- **竞品数据闭环（参考 maozi）**：follow 跟卖时借道 seller.ozon.ru
  what_to_sell 获取竞品**重量(4497)/尺寸(9454/9455/9456)/月销/GMV/上架天数**
  透传信封；worker assemble 在 1688 重量缺失或尺寸全 0 时用竞品值兜底
  （`apply_competitor_fallback`），降低 INCORRECT_DIMENSION/价格失真。
- **跟卖双模式（参考 maozi follow_type）**：`extensions.follow_type` 二选一——
  `hand` 防侵权跟卖（**默认**）：跳过 import-by-sku 1:1 复制，走 CREATE 重建
  （我们管线重做类目/属性/生图，天然防同款/侵权检测）；`api` 强制跟卖：
  import-by-sku 复制竞品卡片（快但可能报错/被下架）。
  **触发规则**：有 1688 货源匹配 → hand 重建（默认）；图搜无匹配（无货源）
  → skill 自动组装 api 信封（import-by-sku 复制竞品，不丢单，result 标记
  api_fallback 供 agent 知晓）；worker 兜底——hand 信封缺货源数据
  （无 purchase_url/purchase_cost）自动降级 api。
- **跟卖防双卡（offer_id 统一）**：import-by-sku 的 offer_id 从 `竞品ID` 改为
  `follow_{竞品ID}`，与后续 upload 一致——旧代码两者不一致导致 api 模式
  import-by-sku 建一张卡、ozon_upload 又 CREATE 一张（双卡 bug）；import-by-sku
  轮询 30s→60s 降超时 fallback 双卡概率。
- **余额判定根因修复**：`_check_mxou_balance` 统一查**用户级 users.quota**——
  unlimited 分支不再返回 key 级 tokens.remain_quota（僵尸字段，同账户两 key
  一正一负 +4.4亿/-5808万误导）；users 查询失败/无记录不再降级 remain_quota
  （负数会误报「有余额却余额不足」），unlimited 放行、非 unlimited 拒绝（数据
  异常暴露，宁缺毋滥）。测试 10/10。

## [0.21.0] - 2026-08-04

> 48 商品端到端实测暴露的三类根因修复：类目错配（13/16 declined）、
> 假成功/学习缓存固化（declined 被当 success 写进 category_mapping）、
> 9782 危险品等级被兜底填成"爆炸物 Category 1"（BR_hazard_class1）。

### Fixed

- **尺寸/重量根因修复（P2，2026-08-04 实证）**：1688 抓取尺寸单位误判 +
  density 兜底放大，导致挂脖风扇 300g→30.4kg、工具套装 400g→364kg、
  修车躺板 5200g→82.5kg，价格分别炸到 2134/25290/5837 CNY。
  - skill probe fallback 容器补 `module-od-product-attributes` /
    `module-od-product-description`，body 行正则补「规格/体积/外观尺寸/包装体积」，
    带单位候选（如「规格 8.5*6.5*11cm」）优先于无单位值；
  - `cloud_probe` 新增 `extract_dimensions_from_texts`（带单位优先、mm 不乘 10、
    前缀/后缀单位都认、单边 >5m 拒绝）与 `resolve_packaging_dimensions`
    （cm/mm 交叉密度判定：按 cm 密度 <0.1 且按 mm 在合理区间 → 切 mm）；
  - density 兜底：商家已提供真实重量时**不再用体积×0.5 覆盖**；
    无商家重量才估算且封顶 30kg。
- **worker 入队防线（P2）**：新增 `utils/draft_sanity.py`，weight>50kg 或
  单边>5m 的信封在 submit 直接 INVALID_REQUEST；pricing_node 对超限重量打
  `weight_suspect` 标记并告警，防脏数据再打爆定价。
- **跟卖类目缺失可观测（P3）**：import-by-sku 成功但类目解析失败时不再静默，
  返回 `category_missing=true` 标记（不阻断，类目由官方复制带出）；
  follow_sell_v5 测试同步到 v0.14/v0.19.1 行为（competitor_price 字段、
  import 失败才报类目错误）。

- **成功判据收紧（P0-1）**：learning_record 只认 `moderate_status=="approved"`；
  删除 `upload_status=="success"` / imported / active / processed 强制成功分支；
  retry 循环"imported 即 success"、"pending+product_id 视为成功"、"不可修复标 success"
  三处假成功路径改为 pending_moderation / rejected_unfixable；
  新增 `scripts/clean_category_mapping.py` 一键清理旧污染学习缓存。
- **9782 危险品等级安全兜底（P0-2）**：删除必填字典属性"取第一个字典值"兜底；
  危险属性只挑「非危险」安全默认（get_safe_hazard_default），取不到则跳过；
  普通属性仅字典值唯一时才兜底；prepare 层加防御纵深。
- **类目匹配修复（P0-3）**：外置同义词表 `config/category_synonyms.json`
  （震动棒→振动器、后视镜→摩托车后视镜、折叠椅→户外折叠椅 等）；
  末级类目词（含同义词）命中节点名 +0.5 打破 tie；
  L0 学习缓存命中必须与 L1 top5 候选一致（防旧脏数据固化）；
  jieba top1 全泛化词命中返回空触发 L3；
  skill 信封改传完整 1688 类目路径（不再截断末两级）。
- **skill 信封数据完整性（P1-1）**：尺寸缺失时标记 `dimensions_estimated`，
  worker assemble 显式告警（不再静默估算硬传）。
- **生图禁文字（P1-2）**：10 个生图 prompt 统一追加"严禁任何文字/水印/价格/促销字样"，
  默认提示词同步（防 4195 图片含配送信息被拒）。
- **batch 429 退避（P1-3）**：batch_test 提交遇到 429 指数退避重试 3 次（30/60/120s）。

### Tests

- 新增 `test_learning_record_gate.py`（5 用例）、`test_hazard_attr_fallback.py`（7 用例）、
  `test_category_match_v021.py`（5 用例）、`skill/tests/test_envelope_fields.py`（2 用例）。

## [0.20.0] - 2026-08-04

> 跟卖 0 图根因修复（A）：真实测试发现甩脂机类商品「图生成成功但卡片 0 图」——
> 根因是类目类型无效（品牌页被当类目）导致 Ozon 整包拒绝 import，图片根本没机会应用。

### Fixed

- **Skill 类目路径净化**：`category_path` 只拼 `/category/` 类目 crumb（排除品牌页
  Luxhommè 类），worker 的 pg_trgm 提示词不再取到品牌段
- **Worker 跟卖类目全链路修复**：
  - `follow_sell_import_node`：类目解析失败**绝不保留原始值**（品牌页 ID 不再被当有效类目）
  - `assemble_ozon_product_node`：数字类目必须通过类目树校验才采用；类目不可用时
    **直接走跟卖组装（省略类目，UPDATE 由 Ozon 保留原卡片类目）**，不再掉进 1688
    类目匹配（曾匹配出无效 dc/type 对 17028706/971301594 被整包拒）
  - `prepare_ozon_upload_node`：跟卖 UPDATE 类目为空时**省略字段**（不传 0）
- 单测：`test_ozon_category_fix.py` 新增品牌排除/路径净化用例（7/7 通过）

### Pending（后续版本）

- B：`warning_all_image_failed` 自动重传一次（直上偶发拉图失败自愈）
- C：ozon_status 用真实 import task_id 轮询 + pending 超时上限
- D：Ozon 风控限速 + 跟卖 import-by-sku 真假成功判定

## [0.19.2] - 2026-08-03

### Fixed

- **task_statistics v1 路由恒 0（Worker）**：`/api/v1/task_statistics` 声明了
  `TaskStatisticsResponse` 响应模型却把 `{status, statistics}` 整个返回，字段对不上被
  Pydantic 填默认值 → 统计接口全 0（旧路径 `/task_statistics` 正常、v0.19.0 的字段
  映射修复被这个解包 bug 挡在路由层）。v1 改为解包 `statistics` 后返回。
- **COS 上传无限挂死（CI）**：coscli 无请求超时，跨境上传 TCP 黑洞会无限阻塞
  （v0.19.1 的 build-skill 在 Upload 步骤挂 8 小时被手动取消）。build-skill 与
  skill-distribute 上传均加单次 600s GNU timeout，3 次重试有界。

## [0.19.1] - 2026-08-03

> 真实测试暴露的跟卖断链修复（P0+P1）：竞品类目缺失/错取导致跟卖失败；
> 参考上品帮/maozi 插件逆向结论，修类目解析 + 复用 1688 来源类目兜底 +
> 竞品信息透传。P2（销量/上架时间，卖家后台接口）探针验证中，随 v0.20。

### Fixed

- **Skill 类目解析 Bug1（品牌页当类目）**：面包屑挑类目改为只认链接含 `/category/` 的 crumb，
  品牌页（`/brand/`，crumbType 同为 CRUMB_TYPE_FULL_LINK）一律排除——甩脂机此前把
  品牌 Luxhommè(101029485) 当类目，现在正确取 Мини-тренажеры
- **Skill 类目解析 Bug2（breadCrumbs 缺失零兜底）**：entrypoint API 改为**纯数字 ID 优先**请求
  （插件实证稳定），缺失时自动回退 slug 版本；评分/评论/卖家/提问/跟卖信息一并解析（P1）
- **Worker 掐死官方通道（Bug3）**：`follow_sell_import_node` 中 import-by-sku 成功（拿到
  product_id）→ 不再强制要求类目（Ozon 官方复制自动带出）；Fallback CREATE 才需要类目，
  缺失时用 1688 来源类目/标题 pg_trgm 兜底（复用 direct 管线引擎）——本地实测棘轮扳手
  「棘轮扳手」→ Ozon 类目 Трещотка(17028653/92147) 成功过类目关

### Changed

- 竞品信息透传进信封 extensions（可选字段，契约兼容）：跟卖数/最低价/评分/评论数/提问数/卖家

### Pending（v0.20）

- P2 销量/上架时间：seller.ozon.ru 卖家后台接口（search-variant-model 等）实探为 403/404，
  需从插件请求报文反向精确报文后接入（seller 登录态已确认可用）

## [0.19.0] - 2026-08-03

> 真实上架 E2E 测试（2026-08-03，7 链接）暴露的问题修复：1688 直接上架 4/4 成功；
> Ozon 跟卖 0/3 全部被图搜护栏误拒（根因：matchBadgeFull 徽标静态文本为空 + 只取前 5 张卡
> + 五金词映射缺失）。本版一并修复生图频繁降级 banana 与上架统计不可用。

### Fixed

- **图搜护栏误拒（Skill）**：全匹配徽标 `matchBadgeFull` 静态 `textContent` 为空（hover 才显示属性级原因）→ 改为按 class 识别为「全部符合」（最高分 100/1.0，直接放行，不再被标题相关性否决）；`page_size` 5→20 + 结果页多段滚动（实测 60 张卡此前只取前 5 张，1/3、2/3、FULL 卡全被忽略）；无徽标（未登录/未渲染）时按标题相关性降级（conf≥0.4 放行，用户确认可接受牺牲准确度）；补 RU→ZH 词映射（棘轮/扳手/活动头/两用/梅花/螺丝刀/钳/锤/电钻 + 甩脂机/抖抖机/减脂/音乐）；CDP 图搜空结果原地重试 1 次再降级 AK
- **生图频繁降级 banana（Worker）**：主模型 `gpt-image-2` 超时 90s→180s（9 个生图节点统一），主模型重试 4 次→3 次；主模型真失败才降级 `nano-banana-fast`；每次生图记录 model + 耗时日志（可审计降级率）
- **上架统计接口恒返回 0（Worker）**：`get_task_statistics` 字段名（`total_tasks` 等）与 `TaskStatisticsResponse`（`total` 等）不匹配，Pydantic 全填默认值 → 新增 `statistics_payload` 统一映射，`avg_duration_seconds`（上架耗时）恢复可查
- **task_status progress 陈旧（Worker）**：completed/failed 终态优先返回 100%，不再显示内存残留的中间阶段（如 0%/social_proof_gen）

### Changed

- Ozon 商品页 CDP 抓取补多段滚动（触发图片画廊/描述/评价懒加载）

## [0.18.0] - 2026-08-03

### Changed

- **Skill 自动更新升级为默认自动应用**：每次命令检测到新版本即自动备份 → 覆盖 → 失败回滚（`data/` 全程保留）；`SKILL_AUTO_UPDATE=0` 可退回「提示 + 手动 `skill update`」；源码开发目录（存在 compile.py）仍拒绝自动更新
- **分发链路重构（build-skill 直传 COS）**：打 tag 后 build-skill 产包 → 直传 COS + manifest → sha256/公网一致性校验，不再依赖 release 事件与 40 分钟轮询（实测旧链路 4 次运行全失败、release 事件零运行记录）；skill-distribute.yml 降级为手动兜底（`gh workflow run skill-distribute.yml -f tag=<ver>`）
- **仓库治理**：从 git 跟踪中移除运行时数据（skill/data 297 个文件）、部署包（4 个 tar.gz/zip）、Cython 中间产物（`*.c`）；补 `.gitignore` + pre-commit 阻塞规则 + CI repo-hygiene 检查防回归

### Added

- **旧包一键升级 bootstrap**：`scripts/bootstrap_update.py`（随包分发 + Release 附加资产），解决 v0.12.0 之前旧包无 updater、永远收不到更新的问题；cli 在缺 `scripts.cloud_probe`（旧包）时给出明确升级提示
- **updater 单测**：`skill/tests/test_updater.py`（11 断言，mock 网络 + 临时目录，无 pytest 环境也可独立运行）

### Fixed

- v0.17.0 COS 分发失败（上传 15 分钟超时）导致 v0.13~v0.17 修复未触达用户——v0.17.0 已补发到 COS，本版本起发布自动完成

## [0.17.0] - 2026-08-03

> v0.12.0 之后首个 skill 统一发版：补发 v0.12.0 遗漏的 skill 修复，并验证「tag → build-skill → release → COS 分发 → 自动更新」全链路。worker 侧 v0.13~v0.16 改动均已含在本次 tag（详见下方各自条目）。

### Skill 修复（v0.12.0 后累积，本次随包发布）

- **E4 裸 CDP 统一封装**（`b78fe64`）：4 处手写 websocket/CDP 全部收敛到 `cdp_client.py`，后续不再允许裸 `websocket.create_connection`
- **图搜弹窗双保险**（`400ce69`/`8231639`）：Chrome 启动加 `--disable-popup-blocking` + JS 层 `window.open` 覆盖，1688 图搜不再需要手动放行弹窗；另加多重新搜机制
- **图搜标题相关性护栏**（`93ddd1a`）：badge/标题相关性弱匹配不再组装信封（防不同产品跟卖错款）
- **COS 分发竞态修复**（`b9a0310`）：skill-distribute 轮询等待 build-skill 包就位，tag 推送不再出现「Release 已发但包没传上去」

### 发版链路（本次验证）

- 端到端验证自动更新：v0.12.0 老包 → `skill update` → v0.17.0（COS manifest 指向最新包，sha256 校验）

## [0.16.0] - 2026-08-03

> 属性填充增强：类目属性尽可能填掉 + 中文零容忍（标准俄语）+ 海关编码跳过。随 v0.15.0（生图提示词外置）一并部署。

### 属性填满

- **必填自由文本无默认值 → 跳过不写空串**（assemble `_validate_and_enrich_items`）：空串上传触发 `error_attribute_values_empty`，宁缺毋滥交给 retry 靶向修
- **可选字典属性补充增强**：多值属性不再一律跳过——① 本地产品标题词对 ZH_HANS 字典值包含匹配（仅唯一命中才补）② Ozon `/values/search`（RU）官方匹配兜底；匹配不到仍跳过（v0.13 关闭盲补首值的原则保留，避免"属性值不正确"）
- 海关编码属性（ТН ВЭД 等）从可选补充排除

### 中文零容忍（标准俄语）

- **`_russian_required_attrs` 翻译结果校验**（prepare L1241）：4191/4180/9048/4384/4389/23171 俄语翻译结果必须含西里尔且无中文，否则跳过该属性——修复拉丁值翻译失败仍直接上传的泄漏路径（「请用俄文填写该字段」）
- **9024(SKU) 不再豁免中文检查**：只豁免拉丁/数字直传，含中文一律翻译/跳过
- **`_generate_rich_description_fallback`**：1688 中文属性名原样拼 HTML 的泄漏（且结果不过 sanitize）→ 属性名/值含中文跳过该 `<li>`

### 海关编码（ТН ВЭД）跳过

- 新建 `worker/src/utils/attribute_utils.py`：`is_customs_attr(attr_id, attr_name)`（ID=22604 + 名称关键词 RU/ZH/EN）
- assemble 三处：1688 匹配不填 / 必填补全跳过（绝不标题搜索乱填 HS code）/ 可选补充排除
- prepare：`_skip_attrs` 按 ID 防御纵深
- validation_retry_loop：`SKIP_ATTR_IDS` 并入海关 ID（revalidate 重传也跳过）

## [0.15.0] - 2026-08-03

> 生图提示词外置配置 + 热加载：调提示词不再需要重新部署 Worker，只改配置文件即可。

### 生图提示词配置文件化（热加载）

- **新增 `worker/config/image_prompts.json`**：10 个生图节点（main/white_bg/multi_angle/scene×3/comparison/detail/social_proof/variant_white_bg）的中文提示词全部外置，与 v0.14 硬编码**逐字一致**（保持中文版，不换英文）
- **新增 `worker/src/utils/image_prompts.py`**：`get_image_prompt(key, **kwargs)` — 每次现读磁盘（无缓存）→ 改文件下一次生图即生效；文件缺失/JSON 损坏/渲染失败 → 回退模块级默认提示词，绝不抛异常阻断生图节点
- **10 个生图节点改造**：删硬编码 prompt 字符串，改调 `get_image_prompt`（Jinja2 模板，占位符 `{{title}}`/`{{scene_context}}`），其余逻辑零改动
- **`deploy/docker-compose.yml`**：worker 服务新增 `../worker/config:/app/config:ro` bind mount → 宿主机改任何 config JSON（含 LLM cfg）无需重建镜像/重启容器，下一次调用自动生效

### 运维方式变化

- **调生图提示词**：`vim ../worker/config/image_prompts.json` → 保存即生效（无需任何操作）
- 注：config 目录 bind mount 后，`docker compose build` 的 `COPY config/` 层不再影响运行时（宿主机文件覆盖）

## [0.14.0] - 2026-08-03

> 8·26 审计遗留修复四批全量实施（PRD: `docs/PRD-audit-fixes-20260803.md`，31 项验证 27 属实 + 4 部分属实）。

### 批次 A：上架正确性（P0/P1，每单必现）

- **P0-2 跟卖属性链路接通**：`_assemble_follow_sell` 消费 follow_sell_import 输出的 final_attributes（统一 attribute_id 键）；删除硬编码 `{"id": 126745801}`（字典值ID被当属性ID）假条目；兜底最小属性集。修复跟卖商品属性全部丢失、品牌/原产国不生效
- **P0-4 单SKU/跟卖/发现漏运费**：`cloud_probe.py` 删 `if len(variants)>1` 守卫，`_collapse_variants_to_single` 无条件调用（内部兼容 0/1/N），单SKU 采购成本含国内运费 freightCny
- **P0-3 GlobalState 补字段**：加 `dictionary_values` / `match_confidence`（动态字典选色 + 类目低置信度阻断复活）
- **P0-6 竞品价链路**：Skill 抓 Ozon 竞品售价（scraper price 字段）→ `draft.competitor_price` → worker `follow_sell_import` 优先读（不再误用 1688 采购价当竞品价）
- **P1-1 quantity 变体定价**：改用 `pricing_info.variant_prices`（含利润/佣金/物流），不再用 1688 裸采购价上架
- **P1-4 定价失败阻断**：pricing 异常返回 `[PRICING_FAILED]` 标记；删 assemble ¥1000/¥1500 兜底；graph 加 `route_after_pricing` 条件边阻断
- **P1-5 parse_error 合并读 validation_errors**：本地校验错误转结构化错误 + 关键词分类，不再退化为 UNKNOWN 通用 LLM 修复
- **P1-6 登录预检条件化**：service.py `_check_1688_login_live` 加 `login_detected` 守卫
- **P1-7 佣金死代码删除**：移除用 1688 item_id 查 Ozon `/v5/product/info/prices` 的恒空块

### 批次 B：成本优化（LLM / 生图）

- **B1 属性合并批量翻译**：含中文/拉丁属性值一次 LLM 调用批量翻译（分隔符拆回，失败逐条兜底），省 40-60% LLM 调用
- **B2 call_mxou_chat_api 重试退避**：4xx（除429）不重试、429 指数退避、5xx/timeout 退避重试 2 次
- **B3 MXOU 限流接入**：`mxou_rate_limiter`（450 RPM 滑窗 + 429 退避）接入 chat/image 两入口（原死代码）
- **B4 变体主图并发生成**：`variant_primary_loop` ThreadPoolExecutor(4) 并行（配 B3 限流），39 变体小时级→分钟级
- **B5 空参考图跳过生图**：7 个 Phase2 节点空 ref 直接返回 None（detail/scene×3/comparison/social/main 连原始图都没有时）

### 批次 C：性能

- **E1 进度写 PG 节流**：每任务 2s 合并窗口（旧每节点异步写一次）
- **E2 cloud_probe import 惰性**：discovery 网络请求移出模块顶层（旧每次命令 +10s），惰性 + 进程级缓存
- **E3 cache 原子写**：临时文件 + os.replace（并发 CLI 不再写坏 JSON）
- **C1 类目树 TTL 缓存**：ozon_api `_query_category_tree` 24h 命名空间缓存（旧每次搜索重拉整树 ~2-5s）
- **E6 discover CDP 复用**：`search_by_image_cdp` 加 `conn` 参数；`match_selected` 批量图搜共享单连接（旧每候选新建）
- **E9 并发上限**：num_workers 联动 `MAX_CONCURRENT`（默认 **30**，4核4G I/O 密集安全值；旧硬编码 10）
- **C4 ProgressLogger 去重读**：进度配置模块级缓存只读一次 + `config_path` 参数生效 + 修复重复 getenv

### 批次 D：健壮性 / 代码质量

- **D2 ozon_post 共享 session**：改用 `utils.http_session` 连接池（旧裸 requests.post 每次新建 TCP）
- **D3 config 错位对齐**：graph.py metadata llm_cfg 对齐节点实际读取（assemble→category_match_v2_cfg、prepare→attributes_llm_cfg）
- **E8 cfg 键名统一**：`scene_generation_llm_cfg.json` `max_completion_tokens` → `max_tokens`（旧键被忽略，改 cfg 不生效）
- **D5 chrome_launcher 端口过滤**：仅杀带 `--remote-debugging-port` 的实例，不误杀用户日常 Chrome
- **E7 batch_test**：finally `follow_result/matches` 用 `locals().get()` 防 NameError 掩盖原异常；进度文件每 5 条增量写 + 循环后全量写（旧 O(n²)）
- **D4 死代码清理**：删除 6 个废弃节点（category_lookup/attributes_fetch/attributes_llm/attributes_learning/error_handler/multi_info_gen）+ `loop_graph.py` + `image_gen_factory.py`
- **D4 死代码清理**：删除 6 个废弃节点（category_lookup/attributes_fetch/attributes_llm/attributes_learning/error_handler/multi_info_gen）+ `loop_graph.py` + `image_gen_factory.py`
- **C4 NODE_ORDER 同步**：progress_logger 节点顺序字典同步真实图节点集
- **E4 裸 CDP 统一封装**：4 处手写 websocket/CDP → `cdp_client`（`scrape_ozon_product_via_cdp` 全量重构 / `cli.py check` 1688+Ozon 检查 / `batch_test.py` 前置检查）；`CdpTab.close(close_remote=)` + `CdpConnection.release()` 新增（复用用户已有 tab 不误关远程）
- **E5 follow_sell_cloud 连接共享**：Step2（抓 Ozon）+ Step3a（1688 图搜）共享一个 `CdpConnection`（省 2-3 个冗余 WS）；envelope 链路（probe_1688_page 会话引导）保持独立更安全
- **图搜弹窗拦截修复（真实冒烟发现）**：1688 图搜点按钮后 `window.open` 弹窗被 Chrome 拦截 → 注入覆盖为当前 tab 延迟导航 + 结果页未打开自动重试 1 次
- **图搜多重新搜机制**：badge 评分 ≤ 1 时自动重新图搜（`force_refresh` 绕过缓存）最多 2 次取最佳——1688 算法偶发匹配差，实测 badge 0→2（符合 2/3 条件）
- **Chrome 启动禁用弹窗拦截**：`chrome_launcher` 加 `--disable-popup-blocking`（专用抓取实例，不影响用户日常 Chrome）——1688 图搜/登录跳转的 `window.open` 弹窗无需手动放行站点，与 JS 层覆盖双保险
- **图搜标题相关性护栏（follow 管线）**：旧逻辑只按 badge 排序取第一，图搜误匹配不同产品也组装信封 → 复用 discover 的 `_pick_best_match`（badge "符合0/N" 跳过 + RU→ZH 标题重叠打分）；增强：badge 轻微匹配（<0.5 如"符合1/3"）但标题相关性弱（conf<0.3）也拒绝（实测"水龙头"被误标符合1/3 的教训）。拒绝时 `no_relevant_match` 不组装信封，宁缺毋滥

### 验证

- py_compile 全量 ✅；`test_attribute_fill_v013.py` 8/8 ✅；`test_audit_a_fixes.py` 5/5 ✅（P1-4 阻断路由 + P0-2 跟卖属性消费/兜底）；集成验证 ✅；mock 全流程 13/13 ✅；graph 模块导入 ✅（删死代码无残留）

## [0.13.0] - 2026-08-03

### Fixed
- **字典属性手填文本兜底移除（Ozon 上传报错根因）**：字典值未匹配时不再写 `dictionary_value_id=0 + 中文文本`（Ozon 只接受列表中的 dict_id，手填触发「属性值不正确，请从列表中选择一个属性值」——用途/商品颜色/风格报错来源）。三处统一为「未匹配 → 跳过属性，由 `/values/search` 修正或补默认字典值」：
  - `assemble_ozon_product_node.py`：`_build_items_deterministically` 字典未匹配跳过 + `_validate_and_enrich_items` 校验跳过
  - `prepare_ozon_upload_node.py`：字典属性无有效 dict_id → 跳过，绝不文本兜底
  - `validation_retry_loop.py`：`error_repair_llm` 字典修复改走「取字典第一个有效 dict_id」，绝不塞文本默认值
- **可选字典属性盲补移除**（assemble）：不再「取字典第一个值」盲补（语义随机 → 填错值被拒）；仅当字典**唯一值**时才补充，多值一律跳过
- **自由文本属性中文翻译失败防上传**：LLM 翻译失败/仍含中文 → **跳过该属性**，不再回退中文原文或写空值（修复「颜色名称 - 请用俄文填写该字段」）
- **retry 重传防御**（validation_retry_loop `revalidate_node`）：字典属性 + dict_id=0 的文本值不再重传（防死循环）；非翻译名单属性的中文值翻译失败 → 跳过重传
- **品牌属性 dict_id 保留**（prepare，集成测试发现）：品牌 85/5076 强制标记为字典属性，`"Нет бренда"(126745801)` 不再因 schema 缺失被当自由文本归零（否则 Ozon 报「请从列表中选择」）
- **生图提示词回退中文版**：main/scene/comparison/detail/social_proof/white_bg/multi_angle 恢复为 v2 英文 prompt 之前的中文版本（英文版出图质量问题，后续再调）

### Changed
- 颜色属性字典匹配强化：字典值分页拉全（limit 5000），避免大字典（颜色 1494 条）截断导致匹配不到 → 文本兜底 → 报错

## [0.12.0] - 2026-08-01

### Added
- **Discover v2 四阶段重构**（Skill）：先全量采集 → 表格分析 → 挑完再找货源（1688 配额只花在选中产品）；`--rules` 自动筛选、`--min-price/--max-price` 价格区间、无关键词中国站懒加载
- **蓝海评分增强**：sales_growth（需求上升）+ drr 广告占比（低竞争）因子
- **seller.ozon.ru 运营指标借道**（月销量/增长率/广告占比/上架天数，未登录自动降级）
- **Skill 自动更新机制**：COS manifest 检测 → `skill update` 下载/sha256 校验/备份/回滚/保留 data/；每次命令静默检查
- **CDP 图搜匹配修复**：badge 过滤 + RU-ZH 产品词映射 + 相关性护栏 + 重试机制（37/37 匹配率实测）

### Fixed
- 属性缓存预热崩溃根因：全量内存 OOM + 单事务卡死 PG + 429 无限递归 → 逐节点小事务 + 指数退避
- chrome_launcher 误杀 Electron 进程（裸 chrome 匹配）
- Chrome 130+ 默认 profile 禁止远程调试 → 独立 profile
- 采集选择器 :is() 拼接 bug、widget webPrice/评分 key 错误、缓存污染
- compile.py 遗漏 ozon_seller_analytics、__pycache__ 污染 dist
- CI：PR 到 dev 不触发（pull_request 只匹配 main）

### Changed
- service.py 移回明文（探针改动最频繁，需快速迭代）；stealth.py 保留编译
- 统一包机制：一个包全平台（_native/{darwin-arm64,darwin-x86_64,linux,win32}）

## [0.2.0] - 2026-07-18

### Added
- API v1 router (`/api/v1/`) with OpenAPI auto-docs
- Unified error codes (12 `WorkerErrorCode` values)
- Pydantic request/response schemas
- Structured JSON logging with trace_id chain tracing
- Node execution audit (duration, output, errors)
- Ozon API call logging
- Task lifecycle audit (submitted/started/completed/failed/retried)
- Deployment package (`deploy/`): docker-compose, deploy.sh, update.sh
- Auto-init DB on first deploy (category tree + logistics rates)
- CI script (`scripts/ci.sh`)
- API rate limiting (10 req/min/token)
- `MAX_CONCURRENT` env var for concurrency control
- `WORKER_URL` env var for skill-to-worker connection
- `.dockerignore` for smaller images
- Pre-commit hooks (ruff lint)
- LOGGING.md documentation
- CONTRACT.md v3.0

### Fixed
- Multi-SKU variant merge: 9048 now uses `item_id` (deterministic, traceable)
- `double_without_merger_offer` now auto-repairable (appends suffix)
- Variant image fallback uses marketing main_image instead of 1688 alicdn URL
- Deep copy base_item for variant items (prevent shared reference mutation)
- Token prefix handling unified (`replace("sk-", "", 1)`)
- Removed hardcoded Supabase service_role key from 3 files

### Changed
- Dependencies: 27 → 15 (removed opencv, Pillow, langsmith, coze-*, etc.)
- `langchain` → `langchain-core` (only RunnableConfig used)
- `coze-coding-utils` → local `runtime/context.py` stubs
- `memory_saver.py`: psycopg/PostgresSaver lazy-loaded
- Skill `check_task_status()` now queries Worker directly (not n8n)
- Skill `submit_envelope()` now POSTs to Worker directly
- Auth: balance check kept, no deduction (MXOU handles billing)
- Dockerfile: clean pyproject.toml install, HEALTHCHECK on /api/v1/health

## [0.1.0] - 2026-07-01

### Added
- Initial release
- LangGraph 22-node pipeline
- 1688 CDP data extraction
- Ozon product upload
- Multi-SKU variant support
- Self-repair retry loop
- Category/attribute learning
