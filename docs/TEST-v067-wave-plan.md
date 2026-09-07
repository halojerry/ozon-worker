# v0.67 Wave 真实测试计划（类目修复回归 + 留存闭环 + 学习闭环）

> 背景：v0.67 本地已 commit（`e30dd0ed` worker 留存表/P1-6、`24396f86` skill 图搜类目保留），
> 未发版。mock 全绿（worker 1744 / skill 645），本计划用真实 1688 链接过本地 worker 验证三波。
> 测试素材来源：`skill/data/batch_results/` 历史实测记录（2026-07~08 真实抓取过）。

## ⚠️ 红线（测试前必读）

- **禁止用云端生产环境（worker.mxou.cn）测试**——全部走本地 Docker（`http://localhost:8080`）。
- **上架目标店用指定测试店，绝不提交 4718259（生产店）**。
- 本地起 worker 后**先清任务表**（防误激活旧任务真实上架）：
  `DELETE FROM ozon_product_tasks WHERE status IN ('pending','failed','running');`
- 素材链接是 7~8 月实测记录，个别可能已下架——打开确认能访问再用，死了就换同类目。

## 环境准备（一次性）

```bash
cd /Volumes/os/dev/ozon-worker/deploy && docker compose up -d --build   # 含 v0.67 最新代码
docker compose exec worker python scripts/init_data.py                  # 幂等，建 listing_result_log 等表
# 清僵尸任务（本地 PG 端口 5433）
PGPASSWORD=localdev123 psql -h localhost -p 5433 -U postgres -d ozon \
  -c "DELETE FROM ozon_product_tasks WHERE status IN ('pending','failed','running');"
curl -s http://localhost:8080/api/v1/health
```

skill 提交（指向本地 worker + 测试店凭证）：

```bash
cd /Volumes/os/dev/ozon-worker/skill
WORKER_URL=http://localhost:8080 OZON_CLIENT_ID=<测试店ID> OZON_API_KEY=<测试店KEY> \
  .venv314/bin/python scripts/cli.py graph --url "<1688链接>"
```

## Wave A — 类目修复回归（今天错配同域：手套/帽/护膝）

### 素材清单（按测试价值排序，括号内是历史验证点）

| # | 域 | 标题摘要 | 1688 链接 | 预期 | 触发防护 |
|---|---|---|---|---|---|
| A1 | 帽·成人词陷阱 | 跨境复活节辣椒帽派对厨师道具**成人**服装搞笑角色扮演舞台帽 ¥13 | `https://detail.1688.com/offer/1002168618090.html` | 服饰>帽子类，「成人」不得命中 18+ | R2 修饰词剥离 + R1 veto |
| A2 | 帽·防晒多义 | 无痕**防晒帽**女防紫外线太阳帽夏季户外运动 ¥12.99 | `https://detail.1688.com/offer/1065567637763.html` | 帽子类，不得跨域到防护/内衣 | R2 + R2b 消歧 |
| A3 | 帽·安全帽歧义 | 防晒遮阳帽反光**安全帽**遮阳帘…渔夫帽 ¥6.9 | `https://detail.1688.com/offer/883453054866.html` | 遮阳帽（服饰），不是劳保安全帽 | R2b LLM 消歧 |
| A4 | 护膝·跪垫歧义 | 潜水料花园**护膝**…跪垫园林护膝神器 ¥16 | `https://detail.1688.com/offer/1057307655307.html` | 家务跪垫或运动护膝二选一且 LLM 仲裁有据 | 同分跨大类 → `_find_close_top_category_rival` |
| A5 | 手套·隔热 | 跨境牛皮长筒**隔热园艺手套**防切割 ¥24 | `https://detail.1688.com/offer/656192195840.html` | 园艺/防护手套 | R2 |
| A6 | 手套·园艺带爪 | **园艺手套**防刺防水带爪耐磨 ¥2.15 | `https://detail.1688.com/offer/900461431043.html` | 基线正配（对照） | — |
| A7 | 帽·草帽女 | 帽子女超大帽檐草编草帽海边沙滩帽 ¥16 | `https://detail.1688.com/offer/785771958953.html` | 基线正配（对照） | — |
| A8 | 帽·风扇帽 | 太阳能充电大风力户外**风扇帽** ¥33.5 | `https://detail.1688.com/offer/1064155745789.html` | 帽子类（不是家电风扇） | R2b |

**建议第一批先跑 A1/A3/A4**（正是今天 4718259 店事故同域同型：成人词跨域 + 同名跨大类歧义）。

### 今天事故精确链接（可选，从生产任务表取，7 天保留期内还在）

服务器 PG 执行（只读）：

```sql
SELECT id, status, created_at::timestamp(0),
       payload->'envelope'->'draft'->>'title'   AS title_cn,
       payload->'envelope'->'draft'->>'purchase_url' AS url_1688
FROM ozon_product_tasks
WHERE created_at > NOW() - INTERVAL '2 days'
ORDER BY created_at DESC LIMIT 30;
```

找到 6236675187（儿童毛绒牛角针织帽）、6236370021（雷锋帽/骑行帽）对应行的 `url_1688`，
本地重跑同链接即可做「同链接同素材」的精确回归。

### Wave A 断言（跑完 1-2 单即可查）

```sql
-- ① P1-6 修复验证：category_match_log.task_id 应能 join 上任务表（此前是随机 uuid4）
SELECT l.task_id, (t.id IS NOT NULL) AS joined, l.match_layer, l.source_category,
       l.description_category_id, l.created_at::timestamp(0)
FROM category_match_log l
LEFT JOIN ozon_product_tasks t ON t.id::text = l.task_id
ORDER BY l.created_at DESC LIMIT 10;

-- ② 敏感闸验证：匹配结果不得出现 200001462（成人糖果18+）或其他 18+ 子树
SELECT description_category_id, type_id, count(*) FROM category_match_log
WHERE created_at > NOW() - INTERVAL '1 day' GROUP BY 1,2 ORDER BY 3 DESC;
```

```bash
# ③ worker 日志观察：R1 敏感闸 / 修饰词剥离 / LLM 消歧 / match_layer
docker compose -f /Volumes/os/dev/ozon-worker/deploy/docker-compose.yml logs worker 2>&1 \
  | grep -E "sensitive|R1|修饰|消歧|match_layer|recategoriz" | tail -40
```

## Wave B — 留存闭环（listing_result_log）

Wave A 商品走完终态（approve/declined/failed 任意）后：

```sql
SELECT task_db_id, final_status, moderation_status,
       source_url, source_category_leaf,            -- 1688 侧
       description_category_id, type_id, price, old_price,  -- Ozon 侧
       weight_g, dims_mm,
       error_code, left(error_message,80) AS err, match_layer, match_confidence
FROM listing_result_log ORDER BY created_at DESC LIMIT 5;
```

断言：每单一行；`task_db_id` 能 join `ozon_product_tasks.id`；1688 侧/Ozon 侧字段非空；
declined 单的 `errors` jsonb 有结构化原因（GraphOutput 透传验证）。

## Wave C — 学习闭环（L0 命中）

1. 任一 Wave A 商品 **approve** 后查学习表新增：

```sql
SELECT source_category_leaf, description_category_id, type_id, source,
       success_count, fail_count, is_active, last_success_at
FROM category_mapping ORDER BY updated_at DESC LIMIT 10;
-- 期待：新增 source=learned 行，source_category_id 非空，success_count≥1
```

2. **同链接二次提交**（换测试店或先下架第一单）→ 观察 category_match_log 的
   `match_layer` 变为 `L0`（v0.66 信任序：succ≥2 权威接管，succ==1 走弱档 LLM 仲裁）。

## 通过标准（三波全过才发版）

- [ ] Wave A：A1/A3/A4 类目全部命中正确域，零 18+ 子树；category_match_log 可 join（P1-6 ✓）
- [ ] Wave B：终态单在 listing_result_log 有完整行，declined 原因结构化可见
- [ ] Wave C：approve 产生 learned 行；二单同链接 match_layer=L0
- [ ] 全程无端点 5xx、无生图额度异常消耗

## 现状备注

- 版本四源已是 **0.67.0**（另一会话 MCP 批次的 release commit `ce85c5fe`，未打 tag 未推送）；
  本批次两个 commit 叠加其上，wave 全过后一起 tag v0.67.0 发版即可。
- 素材链接若失效：skill `search` 命令可现搜同类目替代（`cli.py search "园艺手套" --export csv`）。

## P2/P3 修复回归记录（v0.68.0，2026-09-06）

方案 `docs/PLAN-wave-p2p3-fixes-v1.md` 四 task 全部落地（8698d74d / 02570550 / 86e4e6c3 / c85e583d），本地 Docker 真实回归（测试店 5381204/5371047，6 单）：

| 素材 | wave 原结果 | 回归结果 | 实证修复 |
|---|---|---|---|
| A2 防晒帽（三提） | approved（Step6.5 救回） | approved，match_layer=L0 直跳遮阳帽 | Task2 真值（dc/tp/meta/weight 全对） |
| A3 渔夫帽 | declined（三角头巾） | declined @遮阳帽，INCORRECT_DIMENSION weight=1g | Task4（L0 救类目）+ Task1（原文暴露真因=skill 垃圾重量） |
| A4 护膝 | 阻断（正确答案在池外） | LLM 确认采纳园艺地垫（后被 Step6.5 改配除草剂被 Ozon 拒，见已知问题） | Task3（仲裁池扩容） |
| A7 草帽 | declined（三角头巾） | **approved**，L0 直跳遮阳帽 | Task4 |
| A8 风扇帽 | declined（三角头巾） | declined @头巾→Step6.5 改配太阳能充电器（已知问题） | Task4（域已对） |
| moderation_texts | 恒缺失 | 5/5 declined 行有俄语原文 | Task1 |

新发现（CHANGELOG 0.68.0 已知问题）：Step 6.5 缺 R2b 豁免（A4→除草剂/A8→太阳能充电器）；skill 信封垃圾重量（A3 1g）。

## v0.68.1 回归记录（2026-09-06）

| 素材 | v0.68.0 结果 | v0.68.1 结果 | 实证 |
|---|---|---|---|
| A3 渔夫帽 | declined（1g 重量） | 信封 weight 1g→**50g**（skill 守卫✓）；仍 declined，真因变为估算尺寸 60×45×30mm 过小触发 Ozon 重量×体积交叉校验（已知问题） | 重量硬下限 |
| A4 护膝 | R2b 确认园艺地垫被 Step6.5 改配除草剂 | 一致性警告照打但**采纳保持**，园艺地垫正常上传；首单被 Ozon 拒（图片/8229）后 R4 重配除草剂再拒（R4 域守卫=新已知问题） | Step6.5 R2b 豁免 |

## Wave D — discover 三源真值复用回归（v0.69 待执行，暖风机 8 连拒重放）

> 背景：生产店 5147639 十二单 discover 商品（暖风机×8/电热毯×2/腰包×1）因信封缺 Ozon 地面
> 真值全部失败（10 阻断 + 2 审核拒）。v0.69 修复：discover 提交前复用页面抓取
> （三源 page>what_to_sell>search_kw，commit 98d2cdb4）+ R2b 判据四段化 + 阻断假
> completed 修复（commit b5179cc5）。

```bash
# 本地 Docker worker（最新代码）+ skill discover 真实选品（Chrome 登录态）
cd /Volumes/os/dev/ozon-worker/deploy && docker compose up -d --build
cd ../skill && WORKER_URL=http://localhost:8080 .venv314/bin/python scripts/cli.py discover --keyword "暖风机 取暖器" --max-products 10
# 挑 3-5 候选提交（对齐现有 discover 提交流程）
```

```sql
-- ① 信封真值到位率（核心断言：source 必须 page 或 what_to_sell，不许 search_kw）
SELECT payload->'envelope'->'draft'->'ozon_category'->>'source' AS cat_source,
       (payload->'envelope'->'draft'->'ozon_attributes' IS NOT NULL) AS has_attrs,
       (payload->'envelope'->'draft'->>'ozon_url' IS NOT NULL) AS has_ozon_url,
       count(*) FROM ozon_product_tasks
WHERE created_at > NOW() - INTERVAL '1 day' GROUP BY 1,2,3;

-- ② match_layer 分布 + R2b 放行情况（期待 Skill 直通或 R2b vision/子串放行）
SELECT match_layer, match_confidence, count(*) FROM category_match_log
WHERE created_at > NOW() - INTERVAL '1 day' GROUP BY 1,2 ORDER BY 3 DESC;

-- ③ 终态真实性（阻断必须 failed，不许再出现 completed 假成功）
SELECT status, count(*) FROM ozon_product_tasks
WHERE created_at > NOW() - INTERVAL '1 day' GROUP BY 1;
```

通过标准：候选 100% 带权威 dc/tp（page/what_to_sell）；暖风机落 17039635 域内叶子放行
（R2b vision/子串）或有据阻断；零 completed 假成功；属性透传到位（has_attrs）。

## Wave D 实测结果（v0.69，2026-09-07 本地联调，主会话执行）

> 素材：skill discover "暖风机 家用" 12 候选取 10（真实 Ozon 商品+1688 货源）；
> 驱动：build_envelope_from_discovery（page truth）→ 本地 worker 跟卖管线到类目定稿
> （auth/quota 打桩，不生图不上传主链路）。

| # | Ozon 商品 | 信封 page 真值 | 最终 dc/tp | 结果 |
|---|---|---|---|---|
| 1 | Тепловентилятор водяной 水暖风机 | dc=10718+路径+3属性 | 17039635/971109685 **水暖风机** | ✓ 正确 |
| 2 | 桌面暖风机(绿) | dc=10724+3属性 | 200001517/93203 **丝袜带** | ✗✗ 错放（1688 货源=黑丝袜 conf0.0 放行） |
| 3 | Мини настольный обогреватель | dc=35375+3属性 | 200001540/91852 **医用空气循环消毒机** | ✗✗ 错放（俄语类目词 pg_trgm sim=0.50 直采） |
| 4 | GM HIBIBUD 电池暖风机 | dc=10713+4属性 | 17039635/971109685 水暖风机 | ~ 域内 |
| 5 | Увлажнитель 加湿器 | dc=10723+5属性 | 53968796/96866 取暖器配件、备件 | ✗ 错放（单字「取暖」sim0.70） |
| 6/7 | 水暖风机 ×2 | dc=10718 | 17039635/971109685 | ✓ |
| 8 | Башенный обогреватель 塔式取暖器 | dc=10713 | — | 🛑 **R2b(v0.69 四段) 正确阻断**（LLM abstain，sim=0.6） |
| 9/10 | GM HIBIBUD ×2 | dc=10713 | 17039635/971109685 水暖风机 | ~ 域内 |

**结论：v0.69 修复本身全部生效**（page 真值 10/10 抓到 dc/路径/属性；R2b 四段在 #8 正确拦截
LLM abstain）——**但 page 权威直通 0/10 全灭**，全部被旧闸拦在半路，最终类目由
follow_sell_import 的 1688 类目 jieba 兜底决定（该路径**绕过 assemble 全部闸**）。

### 实测揪出的真问题（按危害排序）

- **P-A 权威真值被自家守卫拒杀**：`_discover_page_truth` type_id 用 dc 兜底（tp==dc）→
  follow import「疑似 Widget 无效 ID」schema 验证 10/10 全拒 → page 直通门全关。
  修：真抓 type（面包屑/characteristics widget）或传 dc+category_path 让下游走 full_path 解析。
- **P-B follow 管线类目解析绕过全部闸**：follow_sell_import 内部 pg_trgm/jieba
  （sim≥0.5 直采，单字 token sim0.7 也放）+「1688 来源类目兜底」——#2 丝袜带/#3 医疗消毒机/#5
  配件全部经此通道放行；assemble 的 R2b 四段/vision 管不到它。**架构洞，危害最大**。
- **P-C 树快照滞后**：本地 7992 节点树无 Ozon 新 RU 类目（Обогреватели и тепловентиляторы）→
  get_node_by_full_path 权威路径解析未命中。生产树同步（admin 端点）需跑。
- **P-D hand→api import-by-sku 降级有害**：8 单真实提交复制请求，**5 张竞品卡真实建进测试店
  5381204**（6249483548/6249484132/6249485385/6249485997/6249486769）+ 每单白等 180s
  （10 单耗时 35min 的主因）。discover 变体应跳过。
- **P-E 上游货源错配**：#2 暖风机图搜匹配到「宝娜斯黑丝袜」conf=0.0 仍放行——错放#2 的源头
  在 aibuy 货源匹配，类目链只是忠实放大。conf 阈值需强拦。

### Wave D 修复验收（v0.69 第二轮，同素材重放）

修复：`50f6c11b`（skill P-A'/P-D/P-E）+ `44ab7478`（worker P-B/P-D）。本地树已同步 Ozon 最新
（ZH/RU 各 7933 节点）。

| 指标 | 第一轮（修复前） | 第二轮（修复后） |
|---|---|---|
| import-by-sku 真实提交 | 8 次（建 5 张竞品卡） | **0 次** |
| 单均耗时 | ~3.5min（180s 轮询） | ~20s（LLM 仲裁） |
| 跨域错放上架 | 3（丝袜带/医用消毒机/配件） | **2→0**（#2/#5 为毒源缓存 artifact，见下） |
| 正确匹配 | 4（jieba 兜底直采） | 0（见已知缺口①） |
| 有据阻断 | 1 | 8（错类目上架变安全阻断，方向符合 v0.68 拍板） |

**已知缺口（下一批候选）**：
1. **R2b vision 对俄语标题×中文候选树 abstain 率过高**——#1/#6/#7（水暖风机，前轮 jieba
   兜底蒙对的）本轮全部阻断。安全方向但损失好单；需 prompt 注入页面属性/双语路径或种子映射。
2. **毒源缓存 artifact**：#2（丝袜 source conf=0.0）/ #5（其他取暖电器）经 assemble Step1
   sim≥0.5 直采 L1 透传——驱动复用带毒缓存绕过了 P-E 货源守卫；**生产新 discover 会在货源
   接受点被拒**，存量采集箱草稿需人工清理或跑清洗脚本。
3. Step1 sim≥0.5 无 vision 复核的通道仍在（对 clean source 是合理默认，对毒源是漏洞）。
