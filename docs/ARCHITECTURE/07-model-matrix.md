# 07 · 模型调用矩阵：LLM / Vision / 生图 / 余额 / 台账

> 唯一封装：`call_mxou_chat_api`（utils/mxou_api.py:286，mxou_llm.py 纯 re-export 保 OutOfQuota 冒泡）、`call_mxou_image_api`（:592）。
> 网关 `https://api.mxou.cn`；全局限流 450 RPM/token 滑动窗口 + 429 指数退避（mxou_rate_limiter.py）。

## 1. worker/config/*.json（bind mount 热加载，改 prompt 无需重建镜像）

| 文件 | model | 要点 | 加载方式 |
|---|---|---|---|
| attributes_llm_cfg.json | deepseek-v4-flash-vision-exp | 属性 LLM+标题/描述翻译读此 model；max_completion_tokens=4096 | 每次现读 |
| category_match_v2_cfg.json | 同上 | 类目 LLM 匹配；max_completion_tokens=1024 | 现读 |
| attr_disambiguation_cfg.json | 同上 | 属性消歧选编号(-1 弃权)；⚠️ **模块级缓存无 TTL——改文件需重启**（假热加载，09） | 缓存 |
| translate_russian_cfg.json | 同上 | 翻译+验收负检（含西里尔且无中文） | 现读 |
| error_repair_llm_cfg.json | 同上 | retry 错误修复；`_call_mxou_llm` 温度默认 0.3 | 现读 |
| scene_generation_llm_cfg.json / visual_vars_llm_cfg.json | 同上 | ⚠️ 用 `max_tokens` 键（其余用 max_completion_tokens，读错键落默认）；代码默认温度 0.7 | 现读 |
| imagegen.json | main/social_proof=gpt-image-2.5，其余 8 节点=nano-banana-fast | 回滚=改本文件两键（热加载），勿 sed 代码 | 每次生图现读 |
| image_prompts.json | — | 10 模板 Jinja2 中文，v8 俄文文字策略 | 每次渲染现读 |
| attr_class_defaults.json / attr_synonyms.json / category_synonyms.json / restricted_keywords.json | — | 类目默认/同义词 14 组/类目搜索扩展/受限词表 | 热加载 |
| category_mapping_seed.json | — | 5 条 curated 种子（仅 init_data） | 初始化 |

⚠️ 键风格不统一：`max_completion_tokens` vs `max_tokens`——消费代码分别对应，读错键即静默落默认（09）。

## 2. 调用点 → 模型矩阵（worker 侧）

| # | 调用点 | 位置 | 模型来源 | 图 | 失败行为 |
|---|---|---|---|---|---|
| 1 | 类目 LLM 匹配 | assemble:2797 | category_match_v2_cfg | 无 | None 降级；OutOfQuota raise |
| 2 | 类目仲裁 _llm_rank_categories | assemble:4055（调用 :556/:1815/:1941/:2400、follow_sell_import:601） | **硬编码 vision-exp**(:4136) | ≤3 | None 降级；OutOfQuota raise |
| 3 | 属性 LLM 消歧 | attr_value_matcher:343 | attr_disambiguation_cfg | ≤3 | abstain→skipped；OutOfQuota raise |
| 4 | vision 推断属性 | prepare:1434 | 硬编码 vision-exp | ≤3 | 空响应 continue；**无 OutOfQuota 特判**（外层兜底吞） |
| 5 | A5 schema-LLM | prepare:4028（kill-switch LLM_SCHEMA_FILL=0，默认开） | 硬编码 vision-exp | ≤3 | ⚠️ 整段 except Exception **吞 OutOfQuota** |
| 6 | A5 中文→俄语翻译 | attr_fill_extras:615 | 硬编码 vision-exp | 无 | ⚠️ except Exception 吞 OutOfQuota 返 "" |
| 7 | 标题翻译/生成 | prepare:276/297/311 | attributes_llm_cfg | 无 | OutOfQuota raise；失败→简化重试→公式→原文 |
| 8 | 标题兜底公式 | prepare:2544 | 硬编码 vision-exp | 无 | 失败→类目名兜底标题 |
| 9 | 描述富文本 | prepare:522 | 硬编码 vision-exp（temp 0.3/2000） | 无 | OutOfQuota raise；其余→静态兜底 HTML |
| 10 | 采集箱 AI 字段再生 | ai_field_service:151（routes/drafts_routes:325） | 默认 vision-exp | 无 | 失败→HTTP 422；**无 OutOfQuota 特判（500）** |
| 11 | hashtag | assemble:4014 | **无 LLM**（字典+西里尔提取+`#товар` 兜底） | — | 恒有输出 |
| 12 | 错误修复 LLM | retry:1956 | error_repair_llm_cfg | 无 | 解析失败走非 LLM 修复 |
| 13 | 强制俄语标题 ×2 | retry:2030/:2101 | 硬编码 vision-exp | 无 | OutOfQuota raise |
| 14 | BR_chinese 批量翻译+负检 | retry:1503 | translate_russian_cfg | 无 | 负检失败清空（宁缺不上） |
| 15 | revalidate 翻译 ×2 | retry:3153/:3181 | translate_russian_cfg | 无 | ⚠️ except Exception 吞 OutOfQuota |
| 16/17 | 场景/视觉变量 | scene_generation_llm_node:59 / visual_vars_llm_node:318 | 各自 cfg | ≤3 | OutOfQuota raise；其余→默认场景/确定性回退 |
| 18 | 标题去拉丁 | title_sanitizer:85 | 硬编码 vision-exp | 无 | OutOfQuota raise；其余→正则兜底 |
| 19 | 生图 10 节点 | 见 06 | imagegen.json get_image_model | ≤2 | API 内三级降级；violation/quota raise |
| 20 | retry 主图重生成 | retry:3656 | get_image_model("main") | refs | 失败 warn-and-pass |
| 22 | R4 换类目 | retry:1105-1240 | **无 LLM**（规则搜索+rebuild，meta 直置 R2b） | — | 无解→入箱/终态 |
| 23 | SEO 流量词 | seo_keywords_routes:34 | **无 LLM**（PG ILIKE） | — | — |

## 3. 生图模型三源（勿混写，mxou_api.py:24-26 注释自警）

`PRIMARY_IMAGE_MODEL="gpt-image-2.5"`（API 缺省参数）vs `imagegen.json nodes.main`（实际生效）vs `DEFAULT_NODE_MODEL="gpt-image-2"`（配置损坏兜底）——同一概念三个默认值（09）。

## 4. 余额判定红绿灯（get_mxou_balance，mxou_api.py:1299-1367）

```
真欠费   = MXOU 实查返回【负数】且无 soft/hard_limit 哨兵 → 拒（402）
哨兵 0   = 字面 balance≤0 且任一 limit>0（订阅/无限账号 100M）→ 返 None 降级放行
无字段   = 用户会话 quota ÷ quota_per_unit → soft/hard_limit → None
None     = fail-open，Supabase users.quota stale 镜像兜底（unlimited 恒放行；本地无 Supabase 无条件放行）
```

- `_check_balance_cached`（:496-526）：30s TTL + **token 指纹绑定**（防多用户互染）；**首查 0.0 不写缓存、二次直查确认**（B2 防缓存污染大面积 402）。
- 低余额告警：0<balance<¥50（BALANCE_ALERT_THRESHOLD）→ token 指纹 30min 去重 → POST TASK_NOTIFY_URL。
- 402 文案带 source 标识（mxou_real/mxou_session/supabase/unknown，main.py:1794-1826）。
- ⚠️ 余额探测 HTTP 不进 mxou_call_ledger；多 worker 缓存不共享（只告警不拦）。

## 5. 台账（mxou_call_ledger）

- 唯一写入口 `mxou_ledger_service.record_call:41`（独立短事务全吞错）；`finish_call:91`（WHERE outcome='pending' 单向幂等，白名单 ok/failed/config_error）。
- 写入点全在 mxou_api.py：chat :356 / 生图 :745（endpoint=`image_gen:<model>`，主/降级各一行）；model 列**调用点显式传优先** > endpoint 提取；tenant = 显式参 > ContextVar trace。
- 余额 pre-check 拦下的调用不记；余额查询本身不记。对账脚本 `worker/scripts/reconcile_mxou.py`。

## 6. 失败分类

- `_is_out_of_quota_response:529`：401/403 或 body 额度词 → `MxouOutOfQuotaError`（永久）。
- `MxouContentViolationError`：violation/关键词 → 不重试不降级（永久）。
- 任务级永久分类 `_is_permanent_task_error`（task_processor.py:40-58）：isinstance + 消息前缀 `OUT_OF_QUOTA:`/含「内容违规」→ failed 不耗 retry；Sentry 单一 fingerprint。
- graph 层刻意不挂 langgraph retry_policy（graph.py:447）。
- **OutOfQuota 吞异常回归口**（v0.63.1 修复后被新代码重新引入 ×5）：A5 整段、_translate_ru、revalidate 两处翻译、ai_field_service——余额耗尽时静默降级而非「请充值」（09-#4）。

## 7. skill 侧直调（绕过 worker 治理面）

**并非零模型调用**——4 处 `deepseek-v4-flash` 直连 `api.mxou.cn/v1/chat/completions`（无限流/无台账/无余额闸/无 Sentry 指纹；`mxou_call_ledger` 对账缺这 4 类）：

| 位置 | 用途 |
|---|---|
| ozon_discovery.py:3152 | 同品/类目语义 YES-NO 判定（护栏边界） |
| ozon_discovery.py:3215 | 类目歧义消解选编号 |
| cloud_probe.py:3569 | 类目关键词 ZH→RU 翻译兜底（⚠️ 未禁 thinking） |
| cloud_probe.py:3927 | slug→中文 1688 关键词翻译（30 天缓存） |

另有余额直连 config_store.py:844（与 worker 同源不同实现）。详见 09-#8。

## 8. 疑点（并入 09-findings §模型）

1. OutOfQuota 吞异常回归口 ×5（上表 ⚠️）。
2. 生图模型三源漂移；回滚只改 imagegen 但 PRIMARY 常量不同步。
3. LLM 模型名双轨（worker vision-exp 部分硬编码绕 config ~12 处；skill 用 flash）——上游更名要改 ~16 处。
4. 温度漂移（config 全 0，但 scene/visual 代码默认 0.7、_call_mxou_llm 默认 0.3、描述重试 0.3）+ 键名 max_tokens/max_completion_tokens 不统一。
5. 翻译验收负检不一致：retry/attr_fill/ai_field 都「含西里尔且无中文」，prepare 主路径只查正向 `_has_cyrillic`——中俄混合标题可漏过（靠二次兜底）。
6. attr_disambiguation_cfg 假热加载（模块级缓存无 TTL）。
7. category_synonyms.json 末尾 4 个值是空格字符串而非数组。
8. skill 直调无治理（成本只在网关侧可见）。
