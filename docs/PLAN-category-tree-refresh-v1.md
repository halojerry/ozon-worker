# PLAN — 类目树数据更新（category_tree_refresh_v1）

> 2026-09-09。F-B03 连带发现的执行计划。目标：`category_tree_nodes` 与 Ozon 现役类目结构一致
> （RU + ZH_HANS 双语），让面包屑路径精配（F-B03 修复）、jieba 匹配、L0 学习表、采集箱类目选择器
> 全部吃到准确数据。
>
> **用户口径**：Ozon 类目 API 一次调用即可全量拿树，每语言各一次，共 2 次。

## 1. 现状与根因（已取证）

| 事实 | 证据 |
|---|---|
| 表数据来自 2025 静态快照 | `init_data.import_category_tree` 只读 `assets/category_tree.json`（scripts/init_data.py:141） |
| 双语各 7992 节点，ZH/RU 同数 = 同一 ID 集双语版 | 本地 PG 实测 `SELECT language, COUNT(*)` |
| 缺 Ozon 现役子树 | 真树验证：面包屑 `Туризм… > Термосы, фляги и питьевые системы > Термосы` 未命中；RU 树含 Терм 词仅 Термошорты/Термопара 等无关项；ZH 树无独立保温杯类目 |
| 同步基础设施已存在但未被系统性使用 | `ozon_category_query.sync_category_tree_nodes`（advisory lock + 双语 upsert + disabled 覆盖 ：1660）、`category_cache` JSONB 持久快照、assemble 懒加载回写（:1506，仅「缓存为空」分支触发，生产未跑到） |
| API 契约 | `POST /v1/description-category/tree`，body `{"language": "RU"|"ZH_HANS"|...}`，**单次调用返回该语言全量树**，递归结构；`sync_category_tree_nodes._walk` 已实现 flatten |

## 2. 方案

### Task 1 — 刷新脚本 `scripts/refresh_category_tree.py`

单一入口，幂等，可重复跑：

1. 读店铺凭证（stores.json 主店铺 / env `OZON_CLIENT_ID`+`OZON_API_KEY`）。
2. 对 `--languages RU,ZH_HANS`（默认双语言）逐语言：
   - `ozon_post("/v1/description-category/tree", {"language": lang})` 拉全量树（走全局限流/重试/类型化错误）；
   - 原始响应落 `category_cache` JSONB（`set_category_cache`，供审计与回滚）；
   - `sync_category_tree_nodes(tree_data, lang, skip_if_nonempty=False)` 全量 upsert。
3. **消失节点软失效**：导入完成后，`language=lang AND (dc,tp) NOT IN 新树集合` 的节点置
   `disabled=true`（不物理删除——L0/category_mapping 的历史 dc/tp 引用需要可识别失效，
   物理删除会让 join 静默丢失）。数量写进差异报告。
4. **差异报告**（打印 + `data/category_tree_refresh_<ts>.json`）：
   - 每语言节点数（前→后）、新增 (dc,tp) 数、消失（软失效）数、名称变更数；
   - `category_mapping` 中引用了消失 dc/tp 的行数（L0 影响面预警）。
5. `--dry-run`：拉树 + 算差异，不落库（先跑一次看体量再真导）。

### Task 2 — `sync_category_tree_nodes` 小补丁

- upsert 的 DO UPDATE 已覆盖 `disabled`（:1660）✓ 不动；
- 新增「软失效」helper（或并入 refresh 脚本）：仅 refresh 流程调用，一次 UPDATE 完成，
  与 Task 1 报告联动。带 2 个单测（新树缺失→disabled=true；恢复出现的类目→disabled=false，
  复用既有 upsert 路径自动达成）。

### Task 3 — 真树验证 gate（刷新后必跑）

```bash
# ① 面包屑精配命中（F-B03 修复的真实验收）——保温杯批真实面包屑
docker run --rm -v $PWD/worker/src:/app/src -w /app \
  -e PYTHONPATH=/opt/venv/lib/python3.12/site-packages:/app/src -e APP_WORKSPACE_PATH=/app \
  -e PGDATABASE_URL="postgresql://postgres:localdev123@host.docker.internal:5433/ozon" \
  --entrypoint /usr/local/bin/python ozon-worker-tmp:verify -c "
from graphs.nodes.assemble_ozon_product_node import _resolve_skill_category
hit = _resolve_skill_category({'source':'page','namespace':'widget',
  'category_path':'Туризм, рыбалка, охота > Туризм и отдых на природе > Термосы, фляги и питьевые системы > Термосы'})
assert hit and hit['_resolved_by_path']
print('OK', hit['description_category_id'], hit['type_id'])"
# ② jieba 搜索 smoke：search_categories("保温杯") 命中新类目
# ③ L0 存活率：SELECT COUNT(*) FROM category_mapping cm
#    LEFT JOIN category_tree_nodes t ON cm.dc=t.description_category_id AND t.language='ZH_HANS'
#    WHERE t.id IS NULL  → 应≈0（消失类目引用数，来自 Task1 报告）
# ④ warm_category_cache --coverage：新 dc/tp 的 attribute_cache 覆盖率会先跌（懒加载会补），记录基线
```

### Task 4 — 测试与文档收尾

- `test_skill_category_direct.py` 等硬编码 dc/tp fixture（17028653/92147 棘轮扳手等）核对：
  树更新后类目若改名/失效则修 fixture（ID 稳定，预期只需小调）；
- `docs/DEPLOY.md` + `docs/CACHE-WARM-RUNBOOK.md` 补「类目树刷新」节（命令 + 建议频率：月度
  或 Ozon 大促前后）；
- AGENTS.md 数据初始化节补一行刷新命令。

## 3. 风险与对策

| 风险 | 对策 |
|---|---|
| 消失类目上有在途草稿/任务 | disabled 只影响新匹配，upload 校验失败已有 R4 换类目路径兜底 |
| 树体积增长（7992 → 预计 2万+ type） | 树查询全走 (dc,tp)/full_path 索引 + pg_trgm，量级无虞 |
| L0/category_mapping 引用失效 | 软失效可识别 + Task1 报告预警；类目 ID 为 Ozon 稳定标识，存量映射预期高存活 |
| attribute_cache 覆盖率暂时下跌 | 懒加载回写自动补（v0.69 T3.3 闭环），高峰期可跑 warm 分片预热 top 类目 |
| 拉树被限流 | 走 ozon_post（全局限流+429 重试），共 2 次调用，风险极低 |

## 4. 验收标准

1. 保温杯真实面包屑 `get_node_by_full_path` 端到端命中（Task 3 ①命令）；
2. 双语节点数一致且 > 7992，差异报告产出；
3. L0 失效引用 ≈ 0；
4. worker 全量测试绿（fixture 适配后）；
5. refresh 脚本幂等（连跑两次第二次零变更）。

## 5. 排期建议

Task 1+2 半天（脚本主体是组装既有件），Task 3 验证半小时，Task 4 半天。生产执行窗口：
低峰期跑 refresh（2 次 API 调用 + 一次 UPDATE，分钟级），无需停机。
