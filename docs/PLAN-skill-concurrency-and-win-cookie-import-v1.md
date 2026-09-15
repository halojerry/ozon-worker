# PLAN — skill 并发竞态治理（discover 多开）+ Windows cookie 导入 v1

> 2026-09-15。两个用户反馈驱动，探针先行（全部结论有 file:line 取证）：
>
> 1. **Q1：多个 discover 命令并发产生竞态，怎么处理？是否支持多线程同时进行？**
> 2. **Q2：1688 跨浏览器 cookie 导入在 Windows 不支持（Chrome 127+ app-bound 加密解不开）**
>
> 实施建议分三个 Tier A 分支（见 §三、§六批次表）；本文是方案定稿，实施前按 WORKFLOW 规范开 worktree。
>
> **实施状态（2026-09-15）**：批 1（fix/skill-concurrency-v1，T1-T4）已合入 dev——PR #24（CI 12 绿）；批 3（feat/win-cookie-import-v1，B-T0~T5）已合入 dev——PR #25（CI 12 绿，含 CI 平台确定性修复批）；SDD 全程：任务级审查×5 + 分支终审×2 + 修复轮×5 全部关环，skill 测试基线 1342→1439。**批 2（fix/skill-concurrency-hardening，T5-T10）未开工**——基于已合入的批 1 续作；各任务审查登记的 defer 项见下文各节与 PR #24/#25 描述。**Windows 真机 gate（B5）待做**：真机跑 `probe-win-cookies --takeover-test` 回传报告 + import-cookies 全链一次。

---

# 第一部分：discover 并发竞态

## A0. 一句话结论（先回答用户问题）

- **进程内并行：支持，且是受控设计**。discover 分析阶段线程池缺省 1 worker（`DISCOVER_WORKERS` 可调，
  `ozon_discovery.py:113/:989-999`）、discover-task `--match-concurrency` 分块并行（`:1473-1499`）、
  fission 线程池（`ozon_fission.py:580`）。且代码自知线程纪律：每 worker 自建 CdpConnection +
  `force_new_tab=True`，注释明言「`CdpConnection._tabs` 无锁，跨线程共享会竞态」（`ozon_discovery.py:992-994`）。
- **跨进程并发（多终端/多 agent 同时跑）：不支持，且无任何系统性防护**。用户反馈成立。现存跨进程锁只有
  4 处（Chrome 启动 profile 锁、updater 锁、探针自检锁、1688 AK 刷新 O_EXCL 占位），全部不覆盖
  discover 的数据面；pounding-mcp 后台路径的「重任务单飞闸」是唯一缓解，但可被 `force=true`、
  同步调用路径、多终端直跑 CLI 三种方式绕过（`pounding_mcp/tasks.py:225-231`，且自身是检查-后行动 TOCTOU）。
- **治理方向（产品语义，不是妥协）**：同一 Chrome 实例上多 discover 并行滚动**本身就是反爬负收益**——
  代码自注「同一 Chrome 多 tab 同时滚动会被反爬识别」（`cli.py:1765`）。因此：
  **重采集命令跨进程默认串行（fail-fast + `--wait` 排队 + `--force` 逃生门），轻命令与重命令的交叉并发做数据面加固**；
  真正的并行吞吐走「多 profile 池」，列为 P2 非本期目标。

## A1. 探针取证清单（8 个实证竞态点）

| # | 共享资源 | 取证 | 并发后果 | 现有保护 |
|---|---|---|---|---|
| 1 | **Chrome 运行期互杀** | 探活 `_is_cdp_available` 单次 HTTP 3s 超时、`except Exception → False`（`chrome_launcher.py:166-176`）；B 进程锁内二次探活失败 → `_kill_chrome_processes`（`:459-462`） | A 正在用时 Chrome 短暂繁忙 → B **杀掉 A 的 Chrome** 重启，A 全部 tab/会话报废 | 仅启动瞬间 per-profile flock（`:446`，`_try_acquire_lock :347-373`，POSIX fcntl / Windows msvcrt 双实现） |
| 2 | **CDP tab 互抢互关** | `find_tab` 按 URL 子串取第一个匹配（`cdp_client.py:384-402`）；`CdpConnection.close` 远程关闭所有未 `release()` 的 tab（`:419-430`）；`CdpTab.close` 默认 `close_remote=True`（`:252-259`） | 两进程拿到**同一个 ozon/seller tab** 互踩导航；B 的 `conn.close()` 把 A 复用中的 tab 远程关掉 | 无（`release()` 只是契约性标记，非强制） |
| 3 | **settings.json 整文件丢失更新** | `set_setting` = 无锁 读→改→写回（`config_store.py:240-244`）；`_atomic_write_json`（`:35-53`）只保证单次写原子 | 并发写任意 key（aibuy token/claim、ak、mxou_token、fx_rate…）→ 后写者基于旧快照写回，**抹掉对方刚写的 key** | 写原子，RMW 无锁 |
| 4 | **aibuy token 刷新 claim 假原子** | `_try_claim_aibuy_refresh` = get_setting 检查 → set_setting 占位，两步无互斥（`ozon_image_search.py:567-582`）；线程级 `_MTOP_TOKEN_ERROR` 模块级 dict 无锁（`:52`，置位/复位 `:782/:791/:946/:951`） | 双进程同时 claim 通过 → **两个 tab 同时导航 1688 轮询**（反爬+配额放大），随后各自整文件 RMW 叠加 #3 | 对照正确样例：`ak_1688_client.py:246-263` 用 `os.open(O_CREAT\|O_EXCL)` 真原子占位 |
| 5 | **discover-task 任务态文件** | `task_id = 秒级时间戳`（`cli.py:2575`）、`_save_task_state` 非原子 `open("w")`（`:2041-2049`）、resume 按 mtime 捡最新（`:2052-2064`）；fission checkpoint 同款（`ozon_fission.py:447-450`） | 同秒启动写同一文件；并发/崩溃读到半截 JSON → except → `{}` → **续跑信息静默丢失**；双 resume 捡同一条分叉重烧图搜配额 | 无 |
| 6 | **导出/发现日志互覆** | 默认导出名固定 `discover_export.csv/.xlsx/.json`（`cli.py:1478-1495`）、发现日志 `discovery_{ts}.json` 秒级名 + 非原子 `write_text`（`ozon_discovery.py:4316-4328`） | 并发互覆；`load_latest_discovery`（`:4346-4362`）读到半截整体解析失败返回 [] | 无 |
| 7 | **cache.py tmp 名固定** | tmp = `path.with_suffix(".json.tmp")` 按 key 确定性（`cache.py:86-93`，v0.14 E3 修的半截问题） | 同 key 并发写共用同一 tmp：一方 `os.replace` 可能 OSError，仅重试一次后**静默丢写**（不会留半截 JSON） | 写原子但 tmp 非唯一 |
| 8 | **batch_test --resume** | 复用旧日志文件 + 整文件非原子覆写（`batch_test.py:771-773/:856-859/:891-893`） | 双实例 resume 同文件互覆丢条目 | 无 |

**交叉命令面**：graph/follow/seller/queries(CDP 兜底)/image_search(cdp 源) 与 discover 共享同一 Chrome
（固定 profile `data/browser/profiles/1688/default`，`chrome_launcher.py:53`；固定端口 9222，`:35`）、
同一 settings.json、同一 aibuy token。MCP 单飞闸只挡后台 heavy 任务互相之间；**image_search 不在
`_HEAVY_KINDS`**（`tasks.py:47-50`），与 discover 并发时正好触发 #4。

## A2. 方案对比

| 方案 | 内容 | 优点 | 缺点 | 判定 |
|---|---|---|---|---|
| **A. 全局重命令串行闸** | CLI 入口层跨进程文件锁，重命令互斥；默认 fail-fast（清晰报错+退出码），`--wait` 排队、`--force` 绕过 | 一把锁消掉整类竞态（#1/#2/#5/#6/#8 的主要触发场景）；与 MCP 单飞语义对齐（闸下沉到 CLI，三种绕过路径全部收敛）；改动小 | 不解决轻×重交叉（#3/#4）；串行等待体验靠 `--wait` 兜底 | ✅ P0 主体 |
| **B. 资源级互斥+全面并行安全化** | 逐项修 #1-#8（锁/原子写/唯一命名/tab 所有权） | 任何组合并发都安全；为 P2 多 profile 池铺路 | 工作量大；且「多 discover 并行滚动」本身反爬有害，修完也不该鼓励 | ✅ P1 分层跟进（轻×重交叉必须修；force 场景受益） |
| **C. 本地 daemon/任务队列** | 常驻进程独占 Chrome，CLI 投递任务 | 彻底 | 过度设计（YAGNI）；常驻进程自身引入新运维面 | ❌ 本期不做，P2 多 profile 池的远期形态再议 |

**推荐 = A+B 分层**：P0（A + 四个止血修复，消用户当下痛点）→ P1（B 剩余项，防 `force` 并行与轻命令交叉）。

## A3. P0 任务拆解（止血批，分支 `fix/skill-concurrency-v1`）

### T1 重命令串行闸（核心）
- 新建 `skill/scripts/lib/lock_utils.py`：从 `chrome_launcher._try_acquire_lock/_release_lock`
  提炼跨平台文件锁（flock / msvcrt.locking），提供 `file_lock(path, timeout, blocking)` 上下文管理器；
  chrome_launcher 改 import 并 re-export 原函数名（零调用方破坏）。
- 闸实现：锁文件 `skill/data/locks/heavy_cdp.lock`；锁文件内容 `{pid, cmd, started_at}`（供报错展示「谁占着」）。
- **闸范围**（与 MCP `_HEAVY_KINDS` 对齐）：`discover` / `discover-multi` / `discover-task` / `graph` /
  `follow` / `seller`。**不进闸**（有意）：`queries`（主通道 cookie 直调免 Chrome，CDP 兜底由 T8 保护）、
  `image_search` aibuy 主通道（免 Chrome；claim 由 T3 保护）、`check`/`import-cookies`（轻量）。
- **缺省行为 fail-fast**：拿不到锁 → 人话报错（占用命令+PID+已运行时长；提示 `--wait` 或 `--force`）→
  **exit 4**（AGENTS 既有惯例 2=session-sync 拒传、3=提交失败，顺延；SKILL.md 命令表登记）。
- `--wait`：阻塞排队，每 30s 打印心跳（前方占用者信息）；`--force`：跳过闸（与 MCP `force` 语义一致）。
- 参数挂载：上述 6 个子命令统一加 `--wait/--force`（cli.py argparse + 各 cmd 入口装饰器 `_heavy_gate`）。
- pounding-mcp 同步路径不改动（CLI 层闸天然覆盖 subprocess）；文档注明权威闸在 CLI。

### T2 Chrome 探活互杀修复（#1）
- `_is_cdp_available` 升级为三态 `_probe_cdp_state(port) -> "up" | "refused" | "busy"`
  （`ConnectionRefusedError/URLError(reason=refused)` → refused；timeout → busy；HTTP 200 且含 Browser → up）。
- `ensure_chrome_cdp` 锁内二次检查改为：**busy → 重试 3×2s → 仍 busy 按「就绪（繁忙）」放行，绝不杀**；
  仅 **refused**（端口确实无人监听）且发现 Chrome 进程存在 → 才走 kill+重启（现 `:459-462` 路径）。
- 锁外初始检查（`:422`）维持「up 即用」。

### T3 aibuy claim 真原子化（#4）
- `_try_claim_aibuy_refresh` 改 `ak_1688_client.py:246-263` 同款 `O_CREAT|O_EXCL` 占位文件
  （`data/config/.aibuy_refresh_claim.json`，内容 `{ts, pid}`）；TTL 沿用 `AIBUY_REFRESH_COOLDOWN_SECONDS`，
  过期文件 unlink 后重试一次；`_clear_aibuy_refresh_claim` 改 unlink。
- `_MTOP_TOKEN_ERROR` 读写包 `threading.Lock()`（match-concurrency>1 线程安全）。

### T4 settings/stores RMW 加锁（#3）
- `config_store.set_setting/remove_setting/set_store/remove_store/set_default_store` 包
  `file_lock(data/locks/settings.lock)`，**读-改-写全段在锁内**；`get_setting` 等纯读不加锁
  （读的是原子写文件，安全）。
- `write_ak_store_file`（`:323-336`）从 `write_text` 改 `_atomic_write_json` 同款 tmp+replace（逐文件）。

P0 预计 ~5 文件：`lock_utils.py`(新)、`cli.py`、`chrome_launcher.py`、`ozon_image_search.py`、`config_store.py`。

## A4. P1 任务拆解（加固批，分支 `fix/skill-concurrency-hardening`）

- **T5 任务态原子化+唯一化（#5）**：`task_id = f"{ts}_{uuid4().hex[:6]}"`（`cli.py:2575`）；
  `_save_task_state` 改 tmp+`os.replace`；fission checkpoint 同款。resume 的 mtime 逻辑不变（自然兼容）。
- **T6 导出防覆盖（#6）**：默认导出路径已存在 → 自动 `_1/_2…` 序号后缀（不改既有「不存在时用原名」
  的习惯），打印实际落盘路径；csv/json tmp+replace；xlsx 先 save 到 tmp 再 replace。
- **T7 cache tmp 唯一化（#7）**：tmp 名加 `{pid}-{uuid4().hex[:6]}` 段，replace 保留一次重试。
- **T8 CDP tab 所有权（#2）**：`CdpConnection` 记录自建 tab（`_created`）；`close()` 只远程关闭自建 tab；
  `find_tab` 借用的 tab 标记 `borrowed=True`——borrowed 的 `close()` 默认只做本地移除、**不远程关**
  （显式 `close_remote=True` 才关）；审查 queries CDP 兜底（`cli.py:3480/:3628`）确保借用 tab 走 release 契约。
- **T9 batch_test resume 锁（#8）**：resume 文件旁 `O_EXCL` claim sidecar（pid+ts，TTL 30min）；
  落盘改 tmp+replace。
- **T10 MCP 单飞闸 TOCTOU**：`start_background` 的 busy 检查与 `_register` 包 registry 文件锁；
  `docs/MCP-SERVER.md` 注明 CLI 层闸为权威、MCP 闸是 UX 前置。

**P2（登记不做）**：多 profile 并行 discover 池（每池独立 user-data-dir+端口，绕开单 Chrome 反爬约束）——
等真实吞吐需求出现再立项。

## A5. 测试计划

- `test_heavy_gate.py`：①锁函数双态（可拿/占用）；②子进程持锁 + CLI 调用重命令 → exit 4 + 报错含占用方信息；
  ③`--wait` 排队放行；④`--force` 直通；⑤Windows msvcrt 分支 mock 单测。
- `test_config_lock.py`：multiprocessing 8 进程 × 各写不同 key → 汇合断言全部 key 存在（丢失更新回归）。
- `test_aibuy_claim.py`：tempdir 两进程竞争 claim → 恰好一方成功；TTL 过期可重claim。
- `test_task_state_atomic.py`：task_id 唯一性；写中断模拟（tmp 残留不影响读）。
- T2/T7/T8 各配针对性单测（三态探活 mock、tmp 并发唯一性、borrowed tab 不远程关）。
- 回归：skill 全量套件绿；`compile.py` 三清单不变式测试（lock_utils 归属 COPY_FILES 或 AUX——新模块**不进编译清单**，避免 ABI 面扩大）。

## A6. 风险与回滚

- **锁残留**：flock 随进程退出由 OS 释放；O_EXCL claim 类有 TTL 兜底（进程被 kill -9 也能自愈）。
- **行为变更**：多开从「默默互相踩」变「第二个明确失败」——这是有意收紧，发版说明必提；
  `--wait/--force` 提供旧习惯逃生门，MCP 侧 `force` 参数语义不变。
- **闸过宽误伤**：范围只含 6 个重命令；若实测有轻量场景被误伤（如 seller 查询型用法），可下拆参数级开关。

---

# 第二部分：Windows 1688/Ozon cookie 导入

## B0. 现状结论（探针）

现有 `import-cookies` 全链路在 `skill/scripts/lib/cookie_harvest.py`（513 行单文件，stdlib-only，
macOS 解密刻意走 `security` CLI + `openssl` 子进程、零新 Python 依赖，`:16/:139`）：

```
cli.py:4089 (cmd_import_cookies)
 └→ cookie_harvest.harvest_and_import (:448)
     ├ 平台闸：sys.platform != "darwin" → unsupported (:356-360；自动兜底 :500 同闸)
     ├ 源扫描：chrome/edge/brave（Chromium 系）+ firefox + safari（ALL_SOURCES :76）
     │   ├ 路径 ~/Library/Application Support/... (:59-75，全 macOS)
     │   ├ Keychain "Safe Storage" → PBKDF2-HMAC-SHA1 16B key (:110-135)
     │   ├ openssl 子进程 AES-128-CBC 解 v10/v11 (:138-155)
     │   ├ Chrome 130+ 明文前 32B=SHA256(host_key) 域哈希剥离 (:216-222)
     │   └ 拷贝 Cookies+-wal/-shm 到 tmp 再 sqlite 读 (:191-198)
     ├ 域过滤：TARGET_DOMAIN_SUFFIXES=("1688.com","ozon.ru","ozone.ru") (:52) + _cookie_dict (:87-104)
     ├ 注入：CDP Storage.setCookies 每批 50 → 工具 Chrome (:385-426)
     └ 验证：readiness probe_alibaba_login / sc_company_id (:429-445)
```

**两个决定方案走向的关键事实**：
1. **注入与验证全走 CDP 明文**——全库对工具 Chrome 的 cookie 读写从不落盘解密
   （session-sync 收割 `ozon_seller_analytics.py:985-1031` 同为 Network.getCookies）。因此
   **Windows 阻塞点只剩「源浏览器 cookie 解密」一步**，工具 Chrome 自身（也是 127+）落盘加密不进链路。
2. Windows 无任何半成品（全库零 ctypes/winreg/CryptUnprotectData 引用）；但 chrome_launcher 已有
   Windows 浏览器路径候选（`:117-126`）与 msvcrt 锁（`:25-29`）可参照。文档预留线索仅一条：
   「Firefox 路线后续可选」（`cookie_harvest.py:21`）。

## B1. 技术背景：Chrome 127+ app-bound encryption（Windows）

- `Local State` → `os_crypt.app_bound_encrypted_key`：base64、前缀 `APPB`，**双层 DPAPI（用户层+SYSTEM 层）**；
  Chrome 经其 elevation service（IElevator COM，**校验调用方 exe 路径**）解密。
- cookie `encrypted_value` 前缀 `v20` = AES-256-GCM(app-bound key)；`v10` = 旧用户 DPAPI（**同用户进程
  ctypes CryptUnprotectData 可直接解**，127 前的老 cookie 行/未跟进 app-bound 的 Chromium 系浏览器走这条）。
- **明确不做：IElevator COM 伪装 / SYSTEM 提权解密**。这是 infostealer 的标准手法——杀软误报必然、
  与 Chrome 安全团队军备竞赛、且与工具的合规形象冲突。产品红线。

## B2. 方案对比

| 方案 | 内容 | 优点 | 缺点 | 判定 |
|---|---|---|---|---|
| **W-A Firefox Windows 源** | `FIREFOX_BASE`（`:73`）加 Windows 路径（`%APPDATA%\Mozilla\Firefox`，profiles.ini 解析）；`_harvest_firefox` 解析逻辑本身平台无关（cookies.sqlite 明文） | 改动最小；Firefox 用户当场全通 | 覆盖面受限于用户用什么浏览器 | ✅ 先行 |
| **W-B Chrome 副本目录 CDP 接管** | 复制最小集（`Local State` + `Default/Network/Cookies*`）到临时目录 → **真实 chrome.exe** `--user-data-dir=<临时目录> --remote-debugging-port=<动态> --headless=new --no-first-run` → CDP `Network.getAllCookies`（Chrome 自己解密 v20，明文出）→ 过滤域 → 现有 `inject_cookies` 注入 → 清理临时目录 | **零解密代码、零提权、零新依赖**（解密主体是 Chrome 自己）；Chrome 136 起「默认用户目录禁 remote debugging」用非默认临时目录天然合规；Edge/Brave 同法 | 依赖「DPAPI blob 不绑数据目录位置」这一机制判断（真机探针验证）；最小复制集边界要试 | ✅ 主通道（探针后定稿） |
| **W-B' DPAPI v10 直解（条件分支）** | 探针发现目标源 v20 占比 0（老版 Chrome / 未跟进 app-bound 的 Chromium 系浏览器）→ ctypes CryptUnprotectData 解 v10 | stdlib、干净 | 对 Chrome 127+ 主流场景无效 | ✅ 作为 W-B 判定矩阵的旁支 |
| **W-C 手动粘贴 Cookie 头** | `import-cookies --paste`（stdin 读 `k=v; k2=v2`）；域判定按 cookie 名指纹（`cookie2/__cn_logon__/_m_h5_tk*`→1688；`sc_company_id/__Secure-access_token/abt_data*`→seller.ozon.ru）+ `--site 1688\|ozon-seller` 显式兜底；复用 inject+verify | 跨平台永久兜底（macOS Safari 未来加密变化也受益）；零风险 | 用户手工 30 秒 | ✅ 同车交付 |
| **W-D 官方扩展导出** | 用户日常 Chrome 装小扩展，chrome.cookies API 读目标域 → 存文件/本地回传 | 体验最好、跨版本稳 | 需要扩展上架/开发者模式安装，运营成本 | ⏸ 后置可选（W-B 失败才立项） |
| W-E COM 伪装/提权解密 | 见 B1 红线 | — | — | ❌ 不做 |

**推荐路径**：B3 探针（真机）→ W-A Firefox + W-C 粘贴先行同车 → W-B 副本接管为主通道（按探针判定矩阵
决定是否加 W-B' 分支）。平台闸从「整机 unsupported」改为 **per-source 可用性报告**
（`harvest_all` 的 darwin 闸下沉：macOS 专属源在 Windows 返回 `unsupported_source`，Firefox/接管通道照常）。

## B3. Windows 真机探针清单（先行任务，只读红线：不解密、不写用户浏览器、不装任何东西）

新 CLI 子命令 `probe-win-cookies`（或独立脚本 `skill/scripts/probe_win_cookies.py`，stdlib-only），输出 JSON 报告：

1. **源枚举**：chrome/edge/brave/firefox 安装与 profile 清单（含 `Local State` 的 profile 目录映射）。
2. **加密形态**：各 Chromium 源 `Local State` 的 `os_crypt.encrypted_key` 前缀（`DPAPI`）与
   `app_bound_encrypted_key` 前缀（`APPB`，存在即 127+）；抽 N 行 cookie `encrypted_value` 前缀分布
   （v10/v11/v20 占比）——**产出 W-B 判定矩阵**（v20 占比 0 → 走 W-B' DPAPI；否则 W-B 接管）。
3. **Firefox 通道**：profiles.ini 解析 + moz_cookies 目标域计数（直接验证 W-A）。
4. **副本接管可行性试验**（探针的高阶项，可选执行）：最小复制集 → 临时目录 → chrome.exe
   `--user-data-dir=<临时> --remote-debugging-port` → `Network.getAllCookies` 中 1688/ozon 域计数。
   记录：最小集是否足够 / 是否要求 `--no-first-run` / headless 与有头差异 / elevator 是否照常放行。
5. 报告落 `data/probe/win_cookies_{ts}.json`，附人话摘要，供回传定稿 W-B 细节。

## B4. 任务拆解（分支 `feat/win-cookie-import-v1`）

- **B-T0 探针脚本**（B3 清单；产物即判定矩阵输入）。
- **B-T1 per-source 平台闸重构**：`harvest_all`（`:356-360`）删整机闸，下沉到各 harvester；
  Windows 源路径常量（chrome/edge/brave/firefox，参照 `chrome_launcher.py:117-126` 候选写法）。
- **B-T2 Firefox Windows**（W-A）：profiles.ini 解析 + 路径接入 `_harvest_firefox`。
- **B-T3 `--paste` 通道**（W-C）：CLI 参数 + cookie 名→域指纹 + `--site` 显式兜底 + 复用
  inject_cookies/verify_after_import；macOS 同步受益。
- **B-T4 副本目录接管**（W-B，按探针定稿）：最小复制集构造（含多 Profile 选择：默认 Default，
  `--browser-profile` 可指定）→ 启动/读 cookie/清理封装为 `_harvest_chromium_via_cdp_takeover`，
  与 `_harvest_chromium` 同返回契约；动态端口分配（避开 9222 主实例）；失败自动降级提示走 W-C。
- **B-T5 文档**：`skill/references/env-setup.md`（Windows 节重写）、`SKILL.md` 命令表与 ⑮ 免登录节、
  `command-reference.md`、CHANGELOG、AGENTS 顶部块。

## B5. 测试计划

- 单测（mock，三平台可跑）：Windows 路径解析、profiles.ini 解析、`--paste` 解析与域指纹、
  per-source 闸返回结构、最小复制集构造（tmpdir 假目录树）。
- 真机 gate（Windows）：探针报告回传 → Firefox 通道全链一次 → 副本接管全链一次（1688 或 seller
  判据转绿）→ `--paste` 一次。SKILL.md 免登录节的 Windows 排错表同步。

---

# 三、实施批次总表

| 批次 | 分支（Tier A） | 内容 | 依赖 |
|---|---|---|---|
| 1 | `fix/skill-concurrency-v1` | A3 T1-T4 + 测试 A5 | 无（可立即开工） |
| 2 | `fix/skill-concurrency-hardening` | A4 T5-T10 | 批 1 合入 |
| 3 | `feat/win-cookie-import-v1` | B4 全部（T0 探针先行） | Windows 真机（探针与 gate） |

发版随车：CHANGELOG、AGENTS.md 顶部块、SKILL.md（新参数 `--wait/--force/--paste/--browser-profile`、
exit 4）；VERSION 四源按发版节奏统一 bump。

## 附：探针取证主要文件
`skill/scripts/cli.py`、`lib/{chrome_launcher,cdp_client,config_store,cache,ozon_image_search,ozon_discovery,ozon_fission,ak_1688_client,cookie_harvest,readiness,ozon_seller_analytics}.py`、
`skill/scripts/batch_test.py`、`pounding-mcp/pounding_mcp/{tasks,skill_runner}.py`、`skill/references/env-setup.md`。
