# 竞态与能力重复审计——发现清单（findings）

- 审计计划：`docs/PLAN-race-duplication-audit-v1.md`（方法 A：静态全扫→动态证实→分波修复）
- 日期：2026-09-09
- 状态：**进行中**——两个用户实证靶点（§2.4）已修复并单测锁定；域 A-G 全量扫描待执行
- 条目 ID 规则：`F-<域字母><序号>`；状态机：待验证 → confirmed（已复现/已修复）| refuted | need-more-evidence

## 状态汇总

| ID | 类型 | 严重度 | 一句话 | 状态 |
|---|---|---|---|---|
| F-A01 | duplication | 高 | AK 存储读写位不相交 + 掩码毒化 + 刷新无冷却 → 凭证反复弹浏览器 | confirmed→已修复 12b6c27f |
| F-A02 | duplication | 高 | aibuy token 导航刷新无冷却无记忆 → 1688 登录态失效后每候选导航一次 | confirmed→已修复 093658b7 |
| F-A03 | duplication | 高 | 货源匹配 confidence 纯标题文本，类目/视觉信号不参与 → 0.11~0.38 误低分 | confirmed→评分模块已落地，接线待 WIP 落地 |
| F-A04 | duplication | 中 | Ozon seller cookie 无磁盘缓存，直调失败即开 seller 页等登录 | confirmed（helper 待建，接线点在 WIP 文件） |

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
- 类型：duplication；严重度：高；状态：confirmed → **评分模块已落地，接线待 WIP**
- 证据：`_title_conf`（ozon_discovery.py:2181）纯标题文本相关性独占 confidence；aibuy normalizationScore 只微调排序分（:2322-2333）不进 confidence；类目一致性零参与。低分（<0.3 守卫 :2277）→ 弱档/阻断 → worker 有意入箱不自动上（v0.67/0.68 拍板）。worker 类目判定层已真值优先（L0/页面真值），但评分信号未换轨——「决策层改了、打分没改」。
- 修复：新模块 `skill/scripts/lib/match_scoring.py`——复合评分 = 类目一致性 0.45 + 图搜官方信号 0.35 + 标题文本 0.20；类目上下文缺失比例回落；badge 直通 trusted。已登记 compile.py AUX_FILES。
- 测试：`skill/tests/test_match_scoring.py` 14 用例（全绿）。
- **待办（P0-4 收尾）**：`ozon_discovery._conf_of_best` → `score_match(...)` 接线 + `_attach_match_meta` conf 消费方核对 + 同批候选新旧信号分布对照——目标文件有他会话 WIP（2026-09-09 15:44 仍活跃），WIP 落地后接线。

### F-A04  Ozon seller cookie 无磁盘缓存
- 类型：duplication；严重度：中；状态：confirmed（待修）
- 证据：`_fetch_seller_session_cookies`（ozon_seller_analytics.py:936）每次活 Chrome 现读；直调失败即 `wait_for_seller_login` 开 seller 页（ozon_discovery `_enrich_with_seller_metrics` 回退段）。
- 修复方向：`get_seller_session_cookies` 包装（活读→写 cache.py 30min TTL→失败回落磁盘缓存→双缺才导航）。
- **待办**：接线点 ozon_discovery.py / cloud_probe.py 均有他会话 WIP，等落地后接线；cli.py queries/seller 两调用点可先行。

## 域 B/C/D/E/F/G —— 待扫描

域 B（skill 并发/组装一致性）、域 C（worker 任务生命周期竞态写点）、域 D（后台任务+草稿状态机）、域 E（缓存/共享表并发语义）、域 F（worker transport 收敛面：10 文件绕过 ozon_client 直连）、域 G（webui 表面+引导漂移：cli 22+ 子命令 vs 文档 10 个）按计划 Task 3-8 执行后回填。
