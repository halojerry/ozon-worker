# PLAN follow-copy-attrs-v1 (A6) — 复制卡特征保全：采纳竞品页面信息

> 分支 `feat/follow-copy-attrs-v1`（2026-09-25）。用户驱动：「fill 率还是太低，
> 但是 ozon 的竞品页面本身也有信息 我们不采纳吗」。

## 一、取证

- 官方契约（ozon MCP describe_method）：
  - `/v1/product/import-by-sku`：**复制竞品整卡**——含已过审特征表；
  - `/v1/product/attributes/update`：增量（删除已填做不到）；
  - `/v3/product/import`：**完全更新语义**——payload 之外的属性全量清掉。
- 事故链：follow 复制建卡（竞品特征表在手）→ 我方稀疏 payload 后续 import → **把自己的复制卡特征洗掉**。实测：按摩器复制卡 6447343398 终局 14/34=41%；`/v4` 反查源卡 404（API 只回自家卡，竞品原表只能从复制卡读回）。
- 深层：hand follow「防侵权 CREATE 重建」不走 offer 预检，但 **Ozon 平台侧 offer_id 唯一键照样把同 offer 的 CREATE 变成对既有卡的 upsert**——洗卡发生在所有 /v3 import 出口（主 upload / retry UPDATE / retry CREATE 三处）。

## 二、修复（三层 + 出口统一）

1. **复制点读回**：follow_sell_import_node import-by-sku 确认点追加 `/v4` 读回复制卡原带特征表 → `state.follow_copied_attributes`（三处 channel 声明）。
2. **payload 合并**：`merge_copied_card_attributes`——我方已填我方权威；个体值键（9048/9024/4180/4191/85 族/23171）恒不抄；其余缺口照抄竞品原值（含 dictionary_value_id）。
3. **UPDATE 兜底**：已存在卡重提不经复制点 → prepare 对现卡就地 `/v4` 读回（语义=未提及属性维持现状）。
4. **三出口统一**：`preserve_existing_card_attributes` 公共函数（pid 解析链 item.product_id > prefer_product_id > find_product_by_offer；`/v4` 读回+merge），接线主 upload / retry UPDATE / retry CREATE 三处 POST 前。全链非致命。

## 三、测试

`worker/tests/test_follow_copy_attrs_v085.py` 8 用例：复制点读回（滤非法行/原值照存）/ v4 容错 / 合并规则 / 个体值键不覆盖 / CREATE 跳过 / channel 三声明 / UPDATE 就地读回 / upload 接线冒烟。follow+attr 系回归 40+ 绿。（v081 单跑红是 v5 裸赋值残留的已知顺序依赖格局，非本批引入。）

## 四、Gate（本地 Docker + 测试店，2026-09-25）

- **按摩器卡复提 ×3**（已洗伤的卡）：run3 实证 hand follow 走 retry `_full_import_create` 出口（单点钩子不够 → 抽公共函数）；终态 15/34=44%，防洗生效（不再掉）但竞品原表早已被洗——「防洗=不再丢失，不=找回」。
- **喷雾器新 follow（e0c1a74c，第一手完整链）**：复制卡 27 属性读回 → `A6 复制卡特征合并: +20 个竞品已过审属性` → **completed+approved（6447633689，17027941/92796），有效口径 26/31 = 83%**（对照按摩器复制卡被洗后 41%）。

## 五、Defer

- 竞品原表找回（已被洗的存量卡）：无 API 通道读竞品卡属性，只能等人工/重跟。
- graph（非 follow）路径采纳竞品特征：依赖 skill 侧抓全竞品特征表（信封 ozon_attributes 目前只 5 个英文名键）——skill 采集批立项。
