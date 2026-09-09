# 竞态与能力重复审计——发现清单（findings）

- 审计计划：`docs/PLAN-race-duplication-audit-v1.md`（方法 A：静态全扫→动态证实→分波修复）
- 日期：2026-09-09
- 状态：**进行中**——两个用户实证靶点（§2.4）已修复并单测锁定；域 A-G 全量扫描待执行
- 条目 ID 规则：`F-<域字母><序号>`；状态机：待验证 → confirmed（已复现/已修复）| refuted | need-more-evidence

## 状态汇总

| ID | 类型 | 严重度 | 一句话 | 状态 |
|---|---|---|---|---|
| F-A01 | duplication | 高 | AK 存储读写位不相交 + 掩码毒化 + 刷新无冷却 + ak_exp 不预判 → 凭证反复弹浏览器 | confirmed→已修复 12b6c27f |
| F-A02 | duplication | 高 | aibuy token 导航刷新无冷却无记忆 → 1688 登录态失效后每候选导航一次 | confirmed→已修复 093658b7 |
| F-A03 | duplication | 高 | 货源匹配 confidence 纯标题文本，类目/视觉信号不参与 → 0.11~0.38 误低分 | confirmed→已修复（模块+接线，待全量数） |
| F-A04 | duplication | 中 | Ozon seller cookie 无磁盘缓存，直调失败即开 seller 页等登录 | confirmed→已修复（30min 快照兜底） |

## 域 A —— skill 能力矩阵与重复扫描

### F-A01  AK 凭证反复获取（四层根因）
- 类型：duplication；严重度：高；状态：confirmed → **已修复**（commit 12b6c27f）
- 证据：
  1. 写侧 `ak_callback._save_ak` 落 `SKILL_ROOT/.1688-AK`（旧实现 `_resolve_ak_store_path`:41-55），读侧 `ak_1688_client.get_ak_from_file`（旧 :171-198）四个 CWD 相对/老 workspace 位与其**完全不相交**；
  2. 掩码毒化：回调处理器 `ak_callback.py:186` result 只回 display 掩码（`code[:4]+"****"+code[-4:]`），`_try_refresh_ak`（旧 :79-91）拿掩码 `set_ali_1688_ak` → settings.json 真值被覆盖成 `eFhV****MDA=`（用户实机逆向证实）；`preflight_check` 非空即 ✅ 假健康；
  3. 刷新无冷却 + 每 CLI 命令独立进程 → AK 一失效每命令各弹一次浏览器；
  4. `ak_exp`（AK 尾部 14 位到期标识，用户逆向 pyd 证实）存了不用，只走「请求报错→被动刷新」。
- 修复：config_store 统一存储入口（resolve/read/write_through 写穿已存在旧位）+ `get_active_ak` 掩码免疫 + `.refresh_claim.json` O_EXCL 跨进程冷却 600s + `_ensure_ak_fresh` 到期预判主动刷新；`check` 命令 preflight 有效性感知。
- 测试：`skill/tests/test_ak_store_unification.py` 15 用例（全绿）。

### F-A02  aibuy token 刷新风暴
- 类型：duplication；严重度：高；状态：confirmed → **已修复**（commit 093658b7）
- 证据：`search_by_image_aibuy`（ozon_image_search.py:827-836 旧）token 失效即 `_fetch_aibuy_cookies_from_chrome` 导航 www.1688.com + 轮询 ≤8s；discover 批量 N 候选 → N 次导航；跨进程无记忆。
- 修复：静默读先行（`_read_1688_cookies_silent` 免导航）+ 导航刷新跨进程 600s 冷却（settings 键 `aibuy_refresh_claim`），冷却内零导航直接降级 CDP/AK。
- 测试：`skill/tests/test_aibuy_refresh_cooldown.py` 6 用例 + `test_aibuy_search.py` 3 存量用例隔离补丁（全绿）。

### F-A03  匹配置信度纯文本评分（靶点二）
- 类型：duplication；严重度：高；状态：confirmed → **已修复**（模块 2831a564 + 接线本批）
- 证据：`_title_conf`（ozon_discovery.py:2181）纯标题文本相关性独占 confidence；aibuy normalizationScore 只微调排序分（:2322-2333）不进 confidence；类目一致性零参与。低分（<0.3 守卫 :2277）→ 弱档/阻断 → worker 有意入箱不自动上（v0.67/0.68 拍板）。worker 类目判定层已真值优先（L0/页面真值），但评分信号未换轨——「决策层改了、打分没改」。
- 修复：新模块 `skill/scripts/lib/match_scoring.py`——复合评分 = 类目一致性 0.45 + 图搜官方信号 0.35 + 标题文本 0.20；**按在场信号分量归一**（零信号 CDP 候选 conf==title 逐字保旧口径；类目零重叠——RU 面包屑 vs ZH 类目名跨语言——不加分也不稀释）；badge 直通 trusted。接线：`_pick_best_match`/`_search_1688_source` 增 `ozon_category_path` 参数（默认空=行为不变），discover 主循环传 `candidate.page_category_path`（v0.72 页面真值）；AK `similarity_score` 0-100 归一并入视觉分量。已登记 compile.py AUX_FILES。
- 测试：`skill/tests/test_match_scoring.py` 18 用例（含 2 接线级：官方信号救回弱标题候选、同语种类目加分）。
- 后续（非阻塞）：RU↔ZH 类目词典可再抬跨语言类目分量；同批候选新旧 conf 分布对照待真单 wave。

### F-A04  Ozon seller cookie 无磁盘缓存
- 类型：duplication；严重度：中；状态：confirmed → **已修复**（本批）
- 证据：`_fetch_seller_session_cookies`（ozon_seller_analytics.py:936）每次活 Chrome 现读；直调失败即 `wait_for_seller_login` 开 seller 页（ozon_discovery `_enrich_with_seller_metrics` 回退段）。
- 修复：新统一入口 `get_seller_session_cookies`——活读成功写穿 30min 磁盘缓存（cache.py ns=`seller_session_cookies`）→ 活读失败回落含 sc_company_id 的快照（Chrome 已关也能直调）→ 双缺 {}。接线三点：discover 富化（ozon_discovery）、follow 富化（cloud_probe）、queries 命令（cli.py）。
- 测试：`skill/tests/test_seller_cookie_cache.py` 4 用例。

## 域 B/C/D/E/F/G —— 待扫描

域 B（skill 并发/组装一致性）、域 C（worker 任务生命周期竞态写点）、域 D（后台任务+草稿状态机）、域 E（缓存/共享表并发语义）、域 F（worker transport 收敛面：10 文件绕过 ozon_client 直连）、域 G（webui 表面+引导漂移：cli 22+ 子命令 vs 文档 10 个）按计划 Task 3-8 执行后回填。

---

## 域 C —— worker 任务生命周期竞态写点（2026-09-09 第二波扫描，核验 12 写点）

前提：队列为单容器单进程，认领 `FOR UPDATE SKIP LOCKED`（task_processor.py:459）保护良好。以下为「迟到写翻盘/检查-提交跨 await/乐观锁非原子」残余。

### F-C01  终态写点无条件更新，stale/zombie 重置可致同 task 双跑 + 迟到写翻盘
- 类型：race；严重度：**高**；状态：**已修复 78d3ba5d**（终态守卫入口 `_write_terminal_status` AND status='running' + 认领刷 updated_at；5 单测）
- 证据：task_processor.py:697-700（completed）/:631-635（failed）/:667-670（rejected）均为 `UPDATE ... WHERE id=:task_id` 无 `AND status='running'`；main.py:1069-1082 清理器与 :511-528 zombie_reset 可把仍被旧 run 持有的行翻成 pending 被新 run 认领 → 旧 run 迟到终态写翻盘第二 run，双跑重复烧额度。
- 复现：双 run 同 task_id，断言旧 run 终态能覆盖新 run running（当前能→confirmed）。
- 修复方向：终态 UPDATE 一律加 `AND status='running'`。

### F-C02  心跳与图节点共享线程池，心跳饿死→清理器误判 stale→双跑
- 状态更新（2026-09-09 P1 波）：已修复 ad880ef9（认领刷 updated_at + 心跳独立 2 线程执行器；线程池全拆分留 P2）
- 类型：race；严重度：中；状态：待验证
- 证据：main.py:483-488 默认 executor 扩容后图节点与心跳（task_processor.py:878-885 asyncio.to_thread）共享 128 线程；认领时不刷 updated_at（:473-477）；外部 API 抖动占满线程池→心跳饿死→清理器误判 stale 重置→双跑（历史「超时×100/failed×120」实证）。
- 修复方向：认领时同步刷 updated_at；心跳与图节点拆线程池；stale 重置前 FOR UPDATE 复核。

### F-C03  进度 PG 回写无 status 守卫，重跑窗口旧 run 迟到 persist 覆写新 run 进度
- 状态更新（2026-09-09 P1 波）：已修复 ad880ef9（进度回写 AND status='running'）
- 类型：race；严重度：低；状态：待验证
- 证据：main.py:121-125 UPDATE progress WHERE id 无 status 谓词；仅展示层抖动。
- 修复方向：进度写加 `AND status='running'`。

### F-C04  zombie_reset 复活「永久性错误」failed 任务（OUT_OF_QUOTA 等不改 retry_count）→ 重启后用失效凭证重跑
- 状态更新（2026-09-09 P1 波）：已修复 ad880ef9（permanent 落库 retry_count=max_retries；2 单测）
- 类型：race；严重度：中；状态：待验证
- 证据：main.py:526-528 复活 `failed AND retry_count<max`；`_is_permanent_task_error`（task_processor.py:36-49）落 failed 时不动 retry_count（:840-850）→ 永久错误可被复活。云端靠 SKIP_FAILED_REVIVE=1 规避。
- 修复方向：永久错误落库时 retry_count=max_retries。

### F-C05  优雅关闭 drain 超时 5min 直接退出，running 任务无差别 zombie 重跑
- 状态更新（2026-09-09 P1 波）：已修复 ad880ef9（排空超时→running 置 failed+推满 retry，不自动重跑）
- 类型：race；严重度：中；状态：待验证
- 证据：main.py:605-622 drain 超时仅 log 即退出，未标记任务；部署窗口上传中断→重启重跑（同 SKU 双倍成本）。
- 修复方向：drain 超时先把 running 置 failed 或打 lease。

### F-C06  submit_task SKU 去重 SELECT-后-INSERT 非原子，并发重复第二个 IntegrityError→500
- 类型：race；严重度：低；状态：**已修复 14b7ea7b**（撞唯一索引 IntegrityError→409 DUPLICATE_SUBMIT）
- 证据：main.py:1771-1785 去重 SELECT + INSERT；唯一索引兜底（model.py:69-76）但路由无冲突映射（:1811-813 回 500 而非 409）。
- 修复方向：ON CONFLICT 或 FOR UPDATE，冲突映射 409。

## 域 D —— MCP 后台任务 + 草稿状态机 + 提交链（核验 12 接口）

### F-D01  patch_draft 乐观锁非原子：SELECT version 无行锁、UPDATE 无 version 谓词 → 409 形同虚设
- 类型：race；严重度：**高**；状态：**已修复 78d3ba5d**（UPDATE 加 AND version=:expected，rowcount=0→409；并发测试锁定恰一胜）
- 证据：draft_service.py:557-583——SELECT version（:558-561 无 FOR UPDATE）→409 校验→UPDATE `version=version+1` WHERE id（:570-583 无 version 条件）→ 并发双 PATCH 双双成功、后写覆盖先写，stale-409 永不触发。
- 复现：asyncio 并发两个同 version PATCH，断言双 200（当前即如此→confirmed）。
- 修复方向：UPDATE 加 `AND version=:client_version`，rowcount=0 判 409。

### F-D02  assemble_draft 服务端 LWW 整包回写，LLM await 长窗口覆盖并发 PATCH
- 类型：race；严重度：中；状态：待验证
- 证据：draft_service.py:603-620 get_draft→await LLM→UPDATE payload WHERE id 无 version 谓词；窗口内 PATCH 全丢。
- 修复方向：写回沿用 version 谓词 + rowcount 判定。**已修复 78d3ba5d**（读时快照 base_version 守卫写回，窗口被改→409 请重试）。

### F-D03  submit/resubmit/batch「检查+入队」跨 await 非原子；offer_id 空时无唯一索引兜底真双跑
- 状态更新（2026-09-09 P1 波）：已修复 ad880ef9（空 offer→draft_id 占位 sku_key 让唯一索引兜底 + IntegrityError→409）
- 类型：race；严重度：中；状态：待验证
- 证据：drafts_routes.py:119-136/:146-147/:166-168 检查后跨镜像/远程查店 await 再入队；uq 唯一索引仅覆盖 sku_key 非空（model.py:69-76），`_resolve_offer_id` 空→sku_key=""→NULL 无兜底（draft_service.py:200-209 + task_processor.py:405）；第二个撞索引抛 500 无映射。
- 修复方向：(draft_id, credential_id) 原子占位（ON CONFLICT）或事务内 FOR UPDATE；空 sku_key 补占位键。

### F-D04  任务终态 commit 与 draft_submissions 写回不同事务，崩溃窗口 submission 卡 pending 永久 409 且无对账
- 状态更新（2026-09-09 P1 波）：已修复 ad880ef9（60s 清理循环加幂等对账：任务终态而 submission 活跃→按任务终态归位）
- 类型：race；严重度：中；状态：待验证
- 证据：task_processor.py:650/:717 先 commit 终态再独立连接 `_writeback_status`（draft_status_writeback.py:39-46）；崩溃→submission 卡 pending→`has_active_submission` 恒真→重提永久 409；无对账扫描。
- 修复方向：同事务或后台 reconcile 归位。

### F-D05  pounding-mcp 同步路径 run_and_record 绕过 _finish 终态粘性，cancel 可被翻盘
- 状态更新（2026-09-09 P1 波）：已修复 7d4ece6c（run_and_record 收敛 _finish；2 单测）
- 类型：race；严重度：中；状态：待验证
- 证据：pounding-mcp/pounding_mcp/tasks.py:197-223 直接写 status，未走 _finish 的 _TERMINAL_STATUSES 粘性（:479-483）；cancel（:494-513）竞争→cancelled 被 completed 翻盘。4c42dcfb 只补了后台面。
- 修复方向：run_and_record 收敛到 _finish。

### F-D06  pounding 重任务单飞闸跨进程失效（list() 只读本进程内存）
- 类型：race；严重度：低；状态：待验证
- 证据：tasks.py:239-245 + :555-561；stdio 与 8902 两进程互不见。
- 修复方向：单飞判定查共享落盘或 advisory lock。

### F-D07  schedule_listing ON CONFLICT 重置已处理行为 pending（不清 task_id）→ 二次上架窗口
- 类型：race；严重度：低；状态：待验证
- 证据：draft_service.py:71-87 + :99-127。
- 修复方向：ON CONFLICT 对已 submitted/published 行拒绝或仅改期。

### F-D08  webui 提交/保存面并发窗口（前端单方证据弱）
- 类型：race；严重度：低；状态：观察
- 证据：CollectionPanel.tsx save :269-282（带 version）、runSubmit :354+、busy 禁用 :547-548；提交后无终态轮询对账；跨设备依赖 F-D01/F-D03 脆弱守卫。
- 修复方向：提交后轮询终态刷新。

## 域 E —— worker 缓存与共享表并发语义

判定：dictionary_value_cache / attribute_cache / category_commission / category_mapping 主路径 / selection_insights 五处均真原子 upsert ✓；0727e7dc NULL type_id 已双保险无残余 ✓。

### F-E01  category_mapping「cid 规范化归并」check-then-act，并发丢学习累计
- 状态更新（2026-09-09 P1 波）：已修复 ad880ef9（归并改原子 UPDATE 表达式，success_count SQL 侧自增；2 单测）
- 类型：race；严重度：中；状态：待验证
- 证据：local_db_manager.py:540-587 归并旁路在 Python 内存 `_canon.success_count += 1`（:560）后 commit，非原子；并发同键两次计一次；`_canon.source_category_leaf` 后写赢倒刷措辞（:562）。主 upsert（:626）原子。
- 修复方向：归并并入 ON CONFLICT 原子路径（SQL 表达式 success_count+1）。

### F-E02  其余四条低：进度事件 seq 读改写丢事件（task_progress_service.py:25-37）；余额缓存无锁并发击穿+告警重复（mxou_api.py:303-333/:374-378）——**告警去重已加锁修复 14b7ea7b**；双查幂等读接受；三大后台循环无单实例锁（main.py:552/572/576，多副本才触发）；attribute_cache TTL 双路径漂移（category_schema_service.py:42 30d vs assemble:3746 1d）。
- 状态更新（2026-09-09 P1 波）：部分修复 ad880ef9（E-5 TTL 对齐 30d）
- 状态更新（2026-09-09 P2 尾批，本批）：**E-1 已修复**（emit 事务内先 `pg_advisory_xact_lock(hashtext(task_id))` 再 MAX(seq)+1，先例 _CATEGORY_TREE_SYNC_LOCK_KEY；2 单测）；**E-4 已修复**（新 `utils/instance_lock.py` 会话级 `pg_try_advisory_lock`+持连接不还池，fail-open 口径；main.py 清理/旧版店铺同步/指标聚合三循环接线，jobs 版 SKIP LOCKED 天然多副本安全不加锁；5 单测）；**E-3 已修复 14b7ea7b**
- 类型：race；严重度：低；状态：**已修复（E-1/E-3/E-4/E-5；双查幂等读为有意接受）**

## 域 F —— worker 能力重复

### F-F01  Ozon transport 直连十文件：主链路上传/修复无重试、无限流、无类型化错误
- 状态更新（2026-09-09 P2 第一批 14b7ea7b）：retry 通用 `_call_ozon_api` 与上传主链 `/v3/product/import`（ozon_upload_node）收敛 `ozon_post`
- 状态更新（2026-09-09 P2 尾批，本批）：**全量收敛完成**——ozon_status_node 两处轮询（404 回退语义由 OzonNotFoundError 承接）、fetch_back、auth `/v1/seller/info`、pricing currency 回退、logistics_quote（私有 Session 删除）、follow_sell_import 四处（schema/import-by-sku/import-info 轮询/_verify_category_schema）、retry 子图七处（attributes-update/import-prices/v3-UPDATE/全量 CREATE/import-info 轮询/两处 info-list 轮询）、main.py store_health（改 ozon_check_quota）与 auth_verify ozon_valid（OzonError=False/网络异常=None 保真分类）全部收敛；裸 `api-seller.ozon.ru` 直发仅剩 ozon_client.py 自身
- 类型：duplication；严重度：**高**；状态：**已修复（14b7ea7b + 本批）**
- 证据：唯一完整 transport 是 ozon_client.py（限流 :91+tenacity :99-104+typed errors :124）；ozon_upload_node.py:280-294、validation_retry_loop.py:414-430/:2566/:2632/:3331、ozon_status_node.py:136/:339、fetch_back_node.py:50、auth_node.py:78-89、pricing_node.py:49-55、main.py:1166/1392 全部绕过各自手工判状态。429 直接判失败再整图重试（浪费+慢）。
- 修复方向：主链路收敛 ozon_post（薄委托保响应 shape）；配额查改 ozon_check_quota。

### F-F02  retry 修复路径手写定价公式，绕 compute_price 唯一入口
- 类型：duplication；严重度：**高**；状态：**已修复 78d3ba5d**（新唯一入口 `pricing_estimate.derive_list_prices`：ceil 对齐主链 + min 50% 底线对齐 update_min_price_floor；retry 三处接线；源码绊线锁定无手写残余；5 单测）
- 证据：validation_retry_loop.py:2238-2240/:2615/:2627 `old_price=int(s*1.2)`（int 截断 vs compute_price 的 ceil，pricing_estimate.py:104/144）、`min_price=int(s*0.9)`（vs update_min_price_floor 0.5×+钳制，ozon_client.py:306-347）——修复改价可能把 PK 顶到拒单线。
- 修复方向：repair 统一走 compute_price / update_min_price_floor。

### F-F03  中：三处私有 requests 直发（logistics_quote.py:21/:38、follow_sell_import_node.py:147/:199/:210）+ assemble 内联第二套字典 API（assemble_ozon_product_node.py:3118-3145/:3679-3700 vs ozon_dict_values.py:18-93）；错误码裸构造三套分类口径（ozon_errors typed 仅 ozon_client 消费）。
- 状态更新（2026-09-09 P2 尾批，本批）：**已修复**——logistics_quote/follow_sell 私有直发随 F-F01 收敛 ozon_post；assemble 内联字典 API 委托 `utils.ozon_dict_values`（新增 `fetch_dictionary_values` 承载 v0.72 巨型字典首页即止语义，None=失败不写缓存契约保留；values/search 内联委托 `search_dictionary_values`）；错误码口径随 typed errors 全面采纳而统一（见 F-F01）
- 类型：duplication；严重度：中；状态：**已修复（本批）**

### F-F04  唯一入口其余核查通过：compute_price/title_formula/cap_attribute_values 调用面统一，未发现未过闸的新 values 出口 ✓。

## 域 B —— skill 并发/缓存/组装一致性（内联补扫）

### F-B01  ThreadPoolExecutor 三处（cli.py:249/:1683/:1763）为批量/双线程/多关键词并行，共享状态均为局部候选列表，未见跨线程竞态实证
- 类型：race；严重度：低；状态：观察
- 缓存命名空间分布健康（seller_analytics 4 处为主，无同数据双缓存 TTL 分叉实证）；四条组装路径差异已由 v0.69-0.72 批次收敛（match_evidence/discovery_meta 语义有契约）。

## 域 G —— webui 表面 + 引导漂移（内联补扫）

### F-B02  aibuy mtop token 运行时过期不触发刷新——每候选静默降级 CDP，复合评分 category/visual 两分量全哑
- 状态更新（2026-09-09 用户拍板后修复，本批）：**主体已修复**——①`_mtop_request` 遇 TOKEN_EXOIRED/**TOKEN_ILLEGAL**（第二波真单又实证非法令牌）时用响应 Set-Cookie 下发的新 `_m_h5_tk` 原地重签重试一次并回写 settings（零导航自愈，mtop 标准协议）；②CDP 徽章文本（符合N/3、全部符合）经 `_badge_effectiveness` 归一后注入 score_match（此前只认 aibuy matchBadgeFull 字符串，CDP 官方全部符合进不了分）；③`visual_signal` badge_eff>0 恒优先；④**护栏判定基准改 max(标题分, 复合分)**——官方类目/视觉证据在场时直接放行，不再被纯标题分一票拦截（用户三次拍板：有权威类目信息为什么还拦）；信号缺在场零分稀释时标题分保底零回归。真单对照（同关键词暖风机 15 单）：保留 8→15、median 0.425→0.579、mean 0.304→0.557、官方全部符合候选 conf 0→0.636。
- 多品类验证（2026-09-09 第三波，1e3ff47a）：**AK 详情回填 1688 完整类目路径落地**（用户口径「1688 有类目信息匹配 Ozon 类目顺其自然」）——过置信闸的匹配调 ainext offer_detail（24h 缓存）回填中文名到 match_1688_category_name。三品类 30/30 全匹配、类目回填 30/30：保温杯 median 0.885（类目「日用餐厨饮具>饮水用具>保温杯」）、手机壳 0.626（「数码、电脑>手机配件>手机保护套」）、宠物碗 0.653（「宠物及园艺>猫狗用品>猫猫食具」）。1688 侧产品验证（graph 信封）：source_category_path 带完整类目路径，且类目自校验闸正确丢弃不一致的本地猜测（'保暖杯'≠来源类目→信封不带 ozon_category 交 worker 全链）。
- 类目一致性二次复核（同批收口，2 commit）：过闸匹配后 LLM 判定 1688 类目全路径 vs Ozon 面包屑全路径（category mode 专用品类 prompt——首版复用 product prompt 把 Термосы↔保温杯 误判 NO 真单实证后修正），不一致 → conf 封顶 0.5 + match_category_divergent 标记（**降权不拦截**）；LLM 调用失败经缓存键甄别不罚。真单 v2：正确类目候选保持原分（0.805），错配咖啡杯×2/背包→杯套全部降权。aibuy token 自动获取闭环补全：EXPIRED/ILLEGAL 响应无新 cookie 时作废缓存走 claim 门控导航刷新（600s 冷却）——实证 ILLEGAL 不下发新 Set-Cookie，重试仍失败路径已观测。
- 类型：duplication/race；严重度：中；状态：**主体已修复（本批）**，category 分量待实机复核
- 证据：暖风机 15 单真单（本地 Worker，analysis_20260909_212224.json）：aibuy mtop 全程 `FAIL_SYS_TOKEN_EXOIRED::令牌过期`，日志零刷新尝试 → 15/15 降级 CDP 网页图搜。根因 `_aibuy_token_valid`（ozon_image_search.py:516）是格式级校验（4 key 齐备+_m_h5_tk 非空），token「格式有效但服务端过期」被判有效 → `search_by_image_aibuy`:861 跳过静默读/刷新链直接打 mtop；`_mtop_request` 遇 EXPIRED 返回 {} 无刷新重试。后果：①每候选多付一次 mtop 失败往返；②CDP 候选无 `category_name`/`normalization_score`/badge → score_match 按 `category_present=False`+`visual_present=False` 回落纯标题分——复合评分 0.45 类目分量与 0.35 视觉分量在 CDP-only 批次结构性缺席。
- 修复方向：`_mtop_request` 识别 EXPIRED → 触发一次 claim 门控的 token 刷新（复用 aibuy_refresh_claim 600s 冷却）→ 重试一次；仍败再降级 CDP。
- 附带事实（本批数据）：保留候选 conf 0.425~0.598（median 0.425，即旧标题分口径），7 拒全部护栏+LLM 判不同品，语义切分（护栏用标题分）工作正常。

### F-G01  cli 实际 22 子命令 vs AGENTS.md「60 秒表」约 10 个（SKILL.md 覆盖较全）；错误码 14 与文档一致 ✓；SKILL.md auto-submit/--to-box 语义已是 v0.70 后口径 ✓
- 状态更新（2026-09-09 P1 波）：已修复 ad880ef9（AGENTS 技能表补 discover-task/report/import-cookies/seller）
- 类型：drift；严重度：低；状态：待修（AGENTS 表补齐即可）

## 第二波汇总

| 域 | 高 | 中 | 低 |
|---|---|---|---|
| C 任务生命周期 | 1 | 3 | 2 |
| D 后台任务/草稿 | 1 | 4 | 3 |
| E 缓存并发 | 0 | 1 | 4 |
| F 能力重复 | 2 | 2 | 1 |
| B skill 并发 | 0 | 0 | 1 |
| G 引导漂移 | 0 | 0 | 1 |
| **合计** | **4** | **10** | **12** |
