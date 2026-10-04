# PLAN — 存量卡翻新模式 v1（refresh）：价格重核 / 套图重生 / 属性优化

> 状态：**草案（待拍板）** · 起草 2026-10-03 · 姊妹篇 `PLAN-follow-clone-v1.md`
> （跟卖克隆）——两管线共享「主图作钥匙 + 模式作用域图片 lane + 均值锚价 +
> 货源标注」，实施批次可交叉复用。

## 动因（用户拍板 2026-10-03）

存量卡不是上完就完：**出单后要优化**（趁热抬内容质量）、**久没流量**、
**没动销**的卡需要翻新。翻新面向**全量在售商品**（不限跟卖来源），三动作：

1. **价格重核建议**——以商品卡主图为钥匙重新图搜，重锚竞品/货源价，给出建议价（不改，用户确认后应用）；
2. **套图重生成**——以现卡主图为参考重新生成整套图并上传；
3. **属性特征优化**——重跑评级补填/竞品复制链，抬内容评分。

skill + worker 配合（与跟卖模式同构：worker 管卡面读写/生图/改卡，skill 管浏览器上下文的图搜源重匹配）。

## 与现有资产的对齐（复用 vs 新建）

| 环节 | 复用 | 新建 |
|---|---|---|
| 翻新选卡 | `ozon_products_cache`（主图 URL/价格/审核状态已缓存）+ `order_service` 订单（出单/无销信号）+ card_audit 日巡检域水位模式 | 选卡查询（三触发条件参数化） |
| 检测面 | card_audit 五不变量（A rating_gap 自动修 / D price_sanity 自动修 / E profit_reality 已是价格真值比对） | 无——翻新是「显式触发的深修」，与巡检「日级轻修」互补不重叠 |
| 属性优化 | v0.79 rating<90 → improve_attributes 补填链（`CONTENT_RATING_ENHANCE`）+ content_enrich 家族构造器 + A6 竞品复制 | 显式触发入口（既有链是过审后自动，翻新是按需重跑） |
| 改图 | `build_image_update_body` 全量回显（v0.81 fail-closed）+ 改图端点 | 无 |
| 生图 | `image_gen_plan` 5 槽 + 三级降级 + AI 图 COS 白名单 | 现卡主图作**生图参考**的 lane（模式作用域放行 external，同 follow_clone 先例） |
| 价格重核 | `pricing_core`（成本/三档/利润闸）+ skill aibuy 图搜链 + 前 20 均值锚（与跟卖 B3 同函数） | 「主图→图搜→重锚」编排 + 建议价报告面 |
| 防洗卡 | `preserve_existing_card_attributes` 三出口必接（A6 纪律）+ `cap_attribute_values` 值数闸 | 无（铁律照接） |
| 货源标注 | PLAN-follow-clone B4 两列（source_url/source_item_id） | 翻新重匹配命中 → 回写/更新货源列（同表同列） |

## 核心设计

### 1. 选卡与触发（三条件，v1 全部可组合）

- `--after-order N`：近 N 天有单（orders 表按 offer/product 聚合）→ 趁热优化；
- `--no-sales N`：上架 ≥N 天零订单（products_cache × orders LEFT JOIN）→ 没动销；
- `--low-traffic`：analytics face 活着时按曝光/会话筛；**face 降级时如实跳过该条件**（不编造流量值）；
- 排除：`moderate_status != declined`（declined 卡归 `declined_disposition` 管，翻新只管在售卡）；
- 触发面：skill CLI `refresh`（单品 product_id / 批量条件筛选）+ webui 按钮；`--detach` 后台语义与采集族一致。

### 2. 三动作（可单选可组合 `--actions price,images,attrs`）

**① 价格重核（纯建议报告，翻新线永不写价——用户拍板 2026-10-03「价格不改」）**

```
现卡主图 URL（products_cache 已有）
  → skill aibuy 图搜 → 1688 源重匹配（item_id 级）
  → 源现价 + 物流 + 包装 → compute_pricing_core 重算成本链
  → 竞品跟卖列表前 20 均值锚（与跟卖 B3 同一函数）
  → 报告：现价 vs 建议价（三档）vs 利润闸结论 —— 到此为止
```

- **翻新线零价格写入**：无 `--apply`、无确认后写价分支；用户要改价走既有
  `bulk_update_prices` 手动端点（本就存在，不动）；
- 图搜无源（自供货/源下架/匹配低于阈值）→ 如实「无源可比」，不编建议；
- 货源标注联动：重匹配命中即回写 source_url/source_item_id（翻新也是货源信息的刷新通道）。

**② 套图重生成（apply 型，用户显式触发）**

- 参考图 = 现卡主图（external lane 模式作用域放行，缩略/.webp 恒拒不变）；
- 走 `image_gen_plan` 全槽生成 → AI 图转存 COS（白名单天然过闸）→
  `build_image_update_body` 全量回显更新（回读不到的字段省略键，v0.81 铁律）；
- **生图全败 → 不动原图**（fail-closed，批E 语义同款；无 IMAGE_SALVAGE 回退）。

**③ 属性特征优化（apply 型）**

- rating-by-sku → `improve_attributes` 可填集补填（既有链重跑）；
- 可选 A6 竞品复制合并（竞品全表 CDP 兜底链不动）；
- 出口铁律：`preserve_existing_card_attributes` + `cap_attribute_values` + 数值语义闸，一个不少。

### 3. skill + worker 分工（与跟卖模式同构）

- **worker**（全部既有端点可复用/小扩）：选卡查询 + refresh 任务编排（三动作子图复用主图节点）+ 改卡三出口 + 建议价报告；
- **skill**：图搜源重匹配腿（aibuy/CDP 浏览器上下文，与 follow 同链）+ `refresh` CLI 组装提交 + `--detach`；
- 信封：`extensions.refresh = {product_id, actions[], trigger}`（envelope_contract 登记，gen_contract_docs 重生成）。

### 4. 租户×店铺数据边界（用户拍板 2026-10-03：每个用户每个店铺都不一样）

翻新数据面严格按 `tenant × credential（店铺）` 归属，**绝不相邻店铺串数据**：

| 数据 | 归属 | 现状 |
|---|---|---|
| 选卡查询 / refresh 任务 / 冷却窗 | tenant + credential | `ozon_products_cache` / orders 本就两列定位；冷却窗新表同键 |
| 货源标注（source_url/item_id） | tenant | `listing_result_log` 租户列已有；查询按 credential 关联 |
| 翻新配置（触发天数/冷却窗/动作开关） | tenant（可细化到 credential） | `config_service` 租户配置面 + 缺省 env 兜底 |
| 图搜重匹配结果 | tenant + credential（信封随店凭证提交） | 与主链一致 |
| 类目学习表 / 字典缓存 / sku_metrics_pool | **全局共享（W11）** | 刻意不按店拆——一店经验全网复用是设计而非疏漏 |

守卫复用既有闸：跨租户凭证 404（get_decrypted）、跨租户绑店 409
（_assert_client_not_bound_elsewhere）、新端点一律走 `api/deps_tenant`
（v0.75 租户口径 + phase3 断言测试锁不回退）。

### 5. 现有 UPDATE 能力盘点（「我们本身就有更新产品的能力」核实，全复用）

| 通道 | 位置 | 翻新用法 |
|---|---|---|
| import UPDATE 全量回显 | prepare_ozon_upload_node + `preserve_existing_card_attributes` | 属性/内容优化的写出口 |
| `attributes/update` 增量 | validation_retry_loop | 免重审属性修 |
| `regen_image` 单槽重生 | image_service（webui 改图按钮背后） | 套图重生逐槽复用 |
| 价格写入 | `bulk_update_prices` / `update_min_price_floor` | **翻新线不碰**（用户手动通道） |

本方卡（graph 1688 直上来源）与跟卖卡走**同一翻新线**——UPDATE 通道对来源无感知，
选卡面天然覆盖两类卡。

## 批次划分

### R1 worker：选卡 + refresh 编排（Tier A worktree）

- `GET /api/v1/stores/{credential_id}/refresh/candidates`（三条件查询，orders × products_cache）；
- refresh 任务端点（三动作开关）；三动作出口全接防洗卡三闸（测试锁定）；
- 主图作参考的 external lane（模式作用域，回归用例锁全局闸不松）。

### R2 skill：图搜重锚腿 + CLI（Tier A worktree）

- `refresh` 命令（单品/条件批量/`--actions`/`--detach`；**无价格 apply 参数**——
  价格动作只产报告）；
- 主图 URL → 图搜 → 源重匹配 → 信封组装（预估打印一致性口径）。

### R3 webui：翻新工作台（可与跟卖 B4 同批）

- 选卡列表（三条件 tab）+ 建议价 diff 卡片（现价/建议/利润闸）——**只展示**，
  改价走既有 bulk_update_prices 手动入口；
- 货源标注展示（两管线共用 B4 两列）。

### R4 实机 gate

- 测试店存量卡 ≥3 张：1 张出单后优化 + 1 张无销翻新 + 1 张 rating<90 属性补填；
- **防洗卡断言**：改卡前后属性/图 diff——目标字段变更、其余零变动（全量回显语义的正确性证明）；
- 价格建议无源 case 如实报「无源可比」。

## 明确不做

- **翻新线零价格写入**（用户拍板「价格不改」：建议报告可以有，写价永远没有——
  改价是用户经 bulk_update_prices 的独立动作）；
- **不翻新 declined 卡**（`declined_disposition` 唯一决策源，职责不重叠）；
- **不做流量归因/竞品监控**（选卡够用，别长成 BI 项目）；
- **不动生图全败的卡图**（fail-closed，宁缺毋滥同款）。

## 风险登记

| 风险 | 缓解 |
|---|---|
| 改卡洗掉现卡内容（import 全量替换语义） | preserve 三闸 + 全量回显 + R4 diff 断言（v0.81 事故是本批第一动机） |
| 主图质量差 → 参考生图劣化 | 生图全败不动原图；劣化检测（pHash 对比参考）登记 defer 观察 |
| 图搜重匹配错源 → 建议价失真 | 匹配置信阈值 + 无源如实报；利润闸兜底 |
| 翻新频率失控打扰审核 | 每卡冷却窗（默认 7 天）+ card_audit 域水位挂靠同款节流 |
| COS 生命周期误删生图 | AI 图 COS 键域 `file/images/`（白名单口径），与在架卡独立（Ozon 自托管副本） |
