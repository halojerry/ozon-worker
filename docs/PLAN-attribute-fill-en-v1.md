# PLAN attribute-fill-en-v1 — EN 确定性匹配腿 + 属性填满 + 错误分类修正（二期）

> 分支 `feat/attribute-fill-en-v1`（2026-09-24 开工）。前置：PR #63（category-bridge-v1，
> 根因/事故取证见彼文）。本期四任务按价值排序；改类目链/属性链前先读对应节。

## 侦察结论（2026-09-24 实测）

- **EN 腿**：`_detect_language`（follow 节点）与 `lang_route`（matcher）都是「中文→ZH，
  否则→RU」——**纯拉丁词全落 RU 分支**，对 RU 行 pg_trgm 必空（A2「Food Storage/
  Containers 无候选」根因）。EN 树已落库（7933 行，refresh --languages EN）；
  `search_nodes(language="EN")` 走 pg_trgm 天然可用——「Storage Case」精确命中
  17027937/95483（B2 实验同款）。
- **颜色链**（B2 attr_match_log 10096 skipped_no_value）：1688 颜色值 =「千鸟格收纳筐大号」
  ——**规格名污染**（花纹+品类词混入），RU 映射/字典匹配自然空。vision 推断
  （_INFER_KW 含 цвет）在 follow 线未被兜底消费。
- **A2 死因反转**：moderation 原文 `{'field': 'name', 'message': '产品名称含拉丁字母，
  需改为俄语'}`——是**标题拉丁**（竞品英文标题直用），被误归
  `BR_chinese_hieroglyphs_in_attribute`（属性中文码）→ 分类张冠李戴。
- **属性缺口基线**（B2 completed approved 卡）：填 12 缺 9（no_infer 6 + no_value 3）。

## T1 EN 匹配腿（语言路由三分支）

1. `follow_sell_import_node._detect_language`：中文→ZH_HANS / **含西里尔→RU / 纯拉丁→EN**
   （拉丁含数字尺寸词如 "14 х 10" 混西里尔 х → 按西里尔优先 RU——面包屑 path 段级路由）。
2. `attr_value_matcher.lang_route`：同款三分支（assemble 候选搜索 1797 行消费）。
3. EN 候选仍走**全部既有闸**（R1 敏感 veto / R2b 确认闸 / 精确 type_name 加分 0.95/0.8
   分档沿用 v0.73 类目批口径）——EN 腿只加「能搜到」，不加「免检」。
4. `_ensure_nodes_synced(EN)`：category_cache 无 EN 行时的空表兜底已天然安全
   （search 返回空 → 走后续链），无需改。

**运维**：生产部署后跑一次 `refresh_category_tree.py --languages EN`（本地已落）。

## T2 颜色链（vision 兜底 + 花纹词提取）

1. **vision 兜底**：prepare `_fill_optional_dict_attrs` 对 10096/10097 skip 后、
   vision 推断白名单命中（цвет/_INFER_KW）时调 `_infer_attrs_from_vision` 兜底一轮
   （graph 线已有；follow 线补消费——同一函数，图 URL 状态已有）。
2. **颜色值预处理**：1688 颜色值先剥离品类词（收纳筐/大号/家用等修饰词表），
   提取纯色/花纹词（千鸟格→houndstooth 同义词入 attr_synonyms）；提取不出再走 vision。
3. 宁缺毋滥红线不变：vision/提取都失败 → 留空（绝不猜色）。

## T3 /v4 自家 approved 卡属性模板继承（最大填满增量）

1. **模板源查询**：类目定稿 (dc,tp) 后，`listing_result_log` 取同 (dc,tp) 且
   final_status='completed' 的自家 ozon_product_id（cap 3，最近优先）。
2. **属性反查**：`/v4/product/info/attributes`（product_id 批量 ≤3）拿已过审卡的
   attributes（含 dictionary_value_id）。
3. **合并策略**（prepare `_fill_optional_dict_attrs` 前插「模板层」）：
   - 本商品证据（1688 属性/vision）**恒优先**；模板只补缺口（no_value/no_infer 的洞）。
   - 模板属性白名单过滤：跳过型号 9048/品牌 85/5076/重量尺寸类/条码——这些是商品
     个体值，模板抄=错填。颜色 10096 也**不抄模板**（走 T2 本商品链）。
   - dictionary_value_id 原样继承（同叶子同字典，id 天然兼容）。
   - `attributes_adjusted` 打标 `source=template_inherit`（可审计）。
4. 失败静默（无模板/反查失败/格式异常）——纯增量，绝不阻断。

## T4 name 拉丁分类修正（defer——待再取证）

moderation 原文 `field=name` + 拉丁语义被误归 `BR_chinese_hieroglyphs_in_attribute`
（属性中文码）→ 分类张冠李戴。但 A2 的具体修复失败点（LLM 修标题后重传仍拒）
容器日志已滚无法定位——重映射收益不确定。**defer**：下次复现时取完整日志再定
（parse_error 分类处加 field=name 特征识别的刀口已在此备案）。

## 明确不做

- 布尔/枚举类目级安全默认（含盖子/目标受众/保证）——等 T3 模板继承上线观察缺口
  余量后再定（模板大概率已覆盖这些稳定字段）。
- HS 编码（22232）等合规字段——恒宁缺。

## 测试

- T1：lang_route/_detect_language 三分支单测 + EN 树命中回归（"Storage Case" →
  17027937/95483）+ 西里尔混排（"14 х 10"）不误判 EN。
- T2：颜色预处理单测（千鸟格收纳筐大号 → 千鸟格）+ vision 兜底接线测试。
- T3：模板层合并策略单测（个体值不抄/白名单/dict_id 继承/静默失败）。
- T4：field=name 拉丁 → 标题通道路由测试。
- 全量：worker + skill + 双侧 CI 口径 lint。

## Gate（实机，本地 Docker attrfill-gate + 测试店铺 5381204，2026-09-24 通过）

**单 1**（3436164007，EN 面包屑 Storage Container，task 7a720a84）：
- T1 EN 腿实证：门控仲裁通过 17027933/93712 **Food Container**（LLM vision 确认
  同大类——旧代码此处「无候选」静默绕路）；completed + approved（6446373107）。

**单 2**（5479948583=f2 竞品，task 64170558）——三箭齐发：
- T1：`pg_trgm 'Food Storage' 命中 5 条`（旧 0 候选）→ 同类目 93712 定稿；
- T2：`vision 推断 10096(商品颜色): прозрачный (dict_id=61572)` + 10097——B2 基线
  缺口补上；
- T3：`模板继承补缺 3 个属性（源=单1 卡 6446373107）`；
- completed + approved；**顺手把今早 5 张假 failed 复制卡之一 6443818882 救活**
  （UPDATE 覆盖：类目修正 + 模板属性 + AI 图）——清理挂账 -1。

**gate 修出的一枚**：9024（供应商货号）被模板抄了单 1 的值 → 商品个体值黑名单
+1（PR #65 追加 commit）。

**CI 教训**（PR #66 修复）：test_template_fills 本地绿是吃了本地 PG 的 B2 卡真数据
——CI 空库必红。① 段打桩（get_session 返回固定 pids）后本地/CI 一致。
