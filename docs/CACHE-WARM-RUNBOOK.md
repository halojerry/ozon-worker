# 属性缓存全量化运维手册（CACHE-WARM-RUNBOOK）— v0.70 / 三桶策略 v0.72

> 目标：**部署即全量**——每次部署/升级后，全部 7424 类目的属性 schema + 字典值
> 缓存直接灌入 PG，首次上架不再等 Ozon API 懒加载（schema 单次拉取 10s+ 且
> 拖慢类目校验与属性填充）。

## 缓存体系速览

| 表 | 内容 | 唯一键 | TTL（v0.70 起） |
|---|---|---|---|
| `attribute_cache` | 类目属性 schema | (dc, type_id, language) | **30 天** |
| `dictionary_value_cache` | 属性字典值 | (attr_id, dc, type_id, language)；**全局桶 dc=tp=0**（v0.72） | **30 天** |

- 三处 TTL 写点保持一致：`warm_category_cache.py` / `init_data.py` / `utils/local_db_manager.py`（懒加载回写默认 `expires_in`）。
- 运行时懒加载：miss → Ozon API → 回写 PG（30 天）。TTL 内重复部署/任务零 Ozon 调用。
- 只预热 `ZH_HANS`（dictionary_value_id 跨语言通用，见 AGENTS.md「需牢记的约定」）。

## ⚠️ v0.72 三桶策略（字典缓存撑爆 40G 盘事故根治，改字典链路前必读）

`dictionary_value_cache` 按 (attr,dc,tp) 键曾把全局字典按类目整份复制——品牌 85
字典 5.18MB × 每节点一份，17% 预热即 3.83GB、全量外推 20GB+。策略入口
`worker/src/utils/dict_value_cache.py`：

| 桶 | 判定 | 存储 | 例子 |
|---|---|---|---|
| **global** | schema `category_dependent=false`（跨类目逐字节一致，md5 实证） | **(attr, 0, 0, language) 全局一份**（哨兵键，零 DDL） | 原产国 4389 / 保证 10400 类 |
| **scoped** | `category_dependent=true` 且 ≤2000 值 | (attr, dc, tp, language) 现状 | 类型 8229（每类目 1 值）/ 颜色 10096 / HS 编码 22232 |
| **ephemeral** | **首页（limit=2000）即 has_next**（>2000 值无底洞） | **不物化**；运行时 value→id 走 `/values/search`（实测可用） | 品牌 85（20k+ 值，跨类目交集仅 ~900/2000） |

- 读侧：scoped 未命中自动回退全局桶（`OzonCategoryQuery.get_dictionary_values` 内置）。
- `limit` 契约 max=2000（5000 被静默钳——v0.72 前 warm 的 5000 是违约调用）。
- 防复发守卫：warm 起步 + 每 50 节点查磁盘余量，<5G 自动中止；cos-update.sh 备份轮转（保留 3 份）+ 排除缓存 JSON；compose logging 封顶 50MB×3；缓存 JSON 已进 .gitignore/.dockerignore。

## 一次性动作：全量预热 + 导出上 COS（仅首次做）

```bash
# 在有 Ozon 凭证的服务器上（worker 容器内执行；分段跑防中断，可 screen/tmux 挂后台）
export OZON_CLIENT_ID=<id> OZON_API_KEY=<key>   # v0.70 起无凭证直接退出，不再有硬编码兜底

# ① 全量预热（分片，每 1000 个一段，全量 ~16h，限流参数已内建）
# ⚠️ v0.72 定案：服务器 3.6G RAM，warm 并行分片 ≤2 片（9 片并行曾顶爆容器内存上限）
docker compose exec -T worker python scripts/warm_category_cache.py --all --pg-only
# 中断续传：--offset 1000 / 2000 / ...（跳过已完成的类目也可直接重跑 --all，upsert 幂等）
# （内置磁盘余量守卫：<5G 自动中止，清理后续跑即可）

# ② 覆盖率审计（只读）——确认接近 100%
docker compose exec -T worker python scripts/warm_category_cache.py --coverage

# ③ 导出 JSON（流式写 worker/assets/，注意磁盘余量；三桶后量级从数百 MB 降一个量级以上）
docker compose exec -T worker python scripts/warm_category_cache.py --export-only

# ④ 上传 COS（路径固定：ozon-worker/cache/，部署脚本从这里拉；勿放图片/部署包路径）
coscli cp worker/assets/attribute_schemas_zh.json cos://yss-1256275613/ozon-worker/cache/attribute_schemas_zh.json
coscli cp worker/assets/dictionary_values_zh.json cos://yss-1256275613/ozon-worker/cache/dictionary_values_zh.json
```

> ⚠️ JSON 不进 git（数百 MB 超仓库承载）。COS 对象与 skill 部署包同 bucket、
> **不同前缀**，不触碰 `file/images/*`（产品图）与生命周期规则覆盖面。

## 日常：部署/升级自动灌入（已接线，无需操作）

`deploy.sh` / `cos-update.sh` 在 init_data 之后自动：

1. 从 COS 下载两个缓存 JSON → `docker compose cp` 进 worker 容器 `/app/assets/`；
2. 后台跑 `warm_category_cache.py --import-only`（upsert 幂等，日志 `/app/logs/warm_import.log`）；
3. COS 无 JSON → 跳过（init_data 会打 warning 提示懒加载），部署不阻断。

## 保活：30 天 TTL 的 re-warm

TTL 到期自动衰减回懒加载（不报错，只是首单变慢）。建议部署机 crontab 每周补一次：

```cron
# 每周日 03:00 覆盖率审计 + 全量补预热（幂等，只补过期/缺失）
0 3 * * 0 cd /path/to/ozon-worker/deploy && docker compose exec -T worker python scripts/warm_category_cache.py --coverage >> /tmp/cache_coverage.log 2>&1
10 3 * * 0 cd /path/to/ozon-worker/deploy && docker compose exec -T worker sh -c "OZON_CLIENT_ID=<id> OZON_API_KEY=<key> python scripts/warm_category_cache.py --all --pg-only" >> /tmp/cache_rewarm.log 2>&1
```

（凭证建议放部署机 root-only 的 env 文件再 source，勿写进 crontab 明文。）

## 常用命令速查

| 命令 | 用途 |
|---|---|
| `--coverage [--coverage-sample N]` | 只读覆盖率审计 + 缺失抽样（不碰 Ozon/不写 PG） |
| `--limit N --pg-only` | 预热前 N 个（部署脚本默认 200） |
| `--all --pg-only [--offset N]` | 全量/分片预热（需凭证） |
| `--export-only` | 导出 JSON 到 `worker/assets/`（需凭证） |
| `--import-only` | 从 JSON upsert 进 PG（无需凭证；部署脚本自动调用） |
