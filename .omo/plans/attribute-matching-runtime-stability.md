# PLAN — worker 属性匹配修复 + skill runtime 稳定化（hyperplan 对抗规划输出）

> 生成：2026-08-08 · Sisyphus（deepseek-v4-flash）主持 hyperplan 对抗规划
> 对抗小组：oracle（structural）/ unspecified-high（race·retry）/ artistry（telemetry·学习毒）/ unspecified-low（战术缺口）
> 证据语料：F1-F10（源码缺陷）、A1-A5（Ozon API 机会）、D1-D6（深挖）、E1-E2（竞态）、R1-R15（交叉修正）
> 计划文件状态机：`pending → in_progress → completed`（各 PR 由对应 PR 子目录的 CHECKLIST 追踪）

---

## Provenance

- 输入：`docs/ozon-api-docs-2026-07-05 (3).json`（523 方法官方 API）、`AGENTS.md`、`worker/AGENTS.md`、git log 实证（b536262/c190ee6）
- 4 位对抗成员全票共识：A1 fetch-back 必须 P0；learning 需 provenance 标记；E1 permanent 早退 + 新 WorkerErrorCode；D1 类目错路径显式 scope-out
- 交叉攻击产出 15 条细化修正（R1-R15），已并入各 PR

## 环境修复记录（本计划前置）

- `~/.omo/omo.jsonc`：ultrabrain/deep/unspecified-high/oracle/momus/metis/sisyphus/prometheus → `opencode-go/glm-5.2`；explore/librarian/atlas/sisyphus-junior/quick/unspecified-low → `opencode/deepseek-v4-flash-free`；multimodal-looker → `opencode/mimo-v2.5-free`；本次新增 `plan` agent → glm-5.2；artistry → glm-5.2。ultrabrain 冒烟验证 ✅

---

## PR 清单（7 个 PR）

| # | 名称 | 范围摘要 | 证据 | 并行 | 阻塞 |
|---|---|---|---|---|---|
| **PR-0** | fetch-back 回读闭环（P0） | approved 后 `/v4/product/info/attributes` 回读 → diff → 写回 fetch_back_corrected + 失效 dict 缓存；attr.outcome 遥测；新 WorkerErrorCode 三分类；9782 出 IGNORABLE_CODES；学习门收紧 | A1, R6, R7, R14 | PR-1/PR-3/PR-4 | 阻塞 PR-6 |
| **PR-1** | retry 止血 + 守卫补齐 | 删盲填 L1017-1063；hazard+is_aspect 守卫；limit 2000；prepare post-fill 中文 strip；permanent 早退；8292 移出 KDR；retry_count 跨入口累积；≥2 字符守卫；注释清理 | F1-F5, D3, E1, A4, A5, R2/R5/R10-R13 | PR-0/PR-3/PR-4 | PR-2 在其上叠加 |
| **PR-2** | attribute_matching util 抽取 | 抽 utils/attribute_matching.py；参数化 3 闭包；retry 改调 util；删 language body；测试改 I/O 契约 | F5, F7, D5 | 需 PR-1 先合 | — |
| **PR-3** | skill 顶层 preflight + updater 锁 | `_preflight_runtime` fast-fail；删误导文案；check 不 early return；入口 ensure Chrome；updater.py 加锁；RATE_LIMIT doc 修正 | D2, D6, F8, R3/R4 | PR-0/PR-1/PR-4 | — |
| **PR-4** | CDP 统一 + profile 迁移 | 统一 profile；移 Popen；find_tab release=True；无 tab 登录检测；迁移脚本 | F9, F10, R9 | PR-0/PR-1/PR-3 | — |
| **PR-6** | learning provenance | mappings 加 source 列；历史回填；prepare 按置信消费；default_fallback 禁 success_count 增长；R15 sanity check | D4, R6, R8, R15 | — | 被 PR-0 阻塞；门控 PR-5 |
| **PR-5** | envelope contract | follow 加 ozon_attributes_category；attr_defaults 扩消费（仅 PR-6 后） | R8 | — | 被 PR-6 阻塞 |

## Sequencing Gantt

```
Wave 1（并行）           Wave 2           Wave 3
PR-0 fetch-back ────────────────► PR-6 provenance ──► PR-5 contract
PR-1 retry 止血 ──► PR-2 util 抽取
PR-3 preflight+锁
PR-4 CDP+迁移
```

## Scope-outs（显式）

1. D1 类目错路径 category-repair 新节点 → `docs/TODO-category-repair.md`（P2 单独 PRD）
2. A2 wrong-volume / A3 pictures-info 离线 sweep → 并入 PR-0 遥测基线后评估
3. tips 类目相似品验证 → TODO
4. Cython 4 模块移回明文 → CI smoke test 先行，迁移下期
5. A5 存量污染卡片补救扫荡 → 等 PR-0 遥测给出受影响面
6. **PR-2 assemble 大函数物理移动（utils/attribute_matching.py 全量抽取）→ 显式 scope-out（2026-08-08 执行时决策）**：
   retry 与主路径的核心语义共享已通过 `_get_attribute_schema` PG 缓存优先 + `search_dictionary_values` 共享 util + PR-1 纪律对齐达成；
   2653 行大文件移动 5 个函数（含 800+ 行闭包）是纯代码卫生、零行为收益、高风险 → 记入 `docs/TODO-attribute-util-extraction.md` 后续做
7. **PR-5 attr_defaults 扩消费（9554/18270/9160）→ 显式 scope-out（2026-08-08 执行时决策）**：
   这三个属性 ID 在代码库零引用、无实证语义，盲目加 ID 违背「宁缺毋滥」纪律；现有 6 类关键词匹配已覆盖主要必填属性。
   真实缺口（竞品重量/尺寸 4497/9454/9455/9456）已由 extensions.competitor_weight_g/dimensions_mm 覆盖（apply_competitor_fallback v0.22）。

## 风险总表

| PR | 承重风险 | 回滚 |
|---|---|---|
| PR-0 | fetch-back 1 次 API/成功 + 学习门收紧短期掉成功率 | 学习门回退一行；节点加 feature flag |
| PR-1 | permanent 早退误伤可修复错误 | PERMANENT_ERROR_CODES 可配置，紧急清空 |
| PR-2 | 重构 import 错乱 | shim re-export 保留，git revert |
| PR-3 | preflight 误阻断 | 门槛放宽至 WARNING 不阻断 |
| PR-4 | profile 迁移破坏登录态 | dry-run + 旧 profile 只复制不删 |
| PR-6 | 历史回填误标 | dry-run + 前 100 行抽检 |
| PR-5 | 扩消费后 default 毒化 | 硬门：PR-6 未合不合并 |

跨 PR 复合：PR-0+PR-6 串联断裂（fetch-back 未上线 PR-6 提前合）；PR-1+PR-2 同文件冲突（Wave 1 合 PR-1 才开 PR-2）。
