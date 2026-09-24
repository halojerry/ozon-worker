# PLAN — 竞品全表特征采纳（A7 批）：DOM 兜底 + 箱规渗透源头修 + A5 竞品证据

> 分支 `feat/competitor-fullattrs-v1`（worktree，基于 facc21d9）。
> 前序：A1-A4（PR #67 标题证据/类目默认/数值派生）→ A5（PR #68 schema-LLM 兜底）→
> A6（PR #69 复制卡特征保全防 import 洗卡）。本批补最后一环：**竞品页第一手全表特征**。

## 1. 问题与取证

### 1.1 缓存全表为 0（A7 动因）

用户：「ozon 的竞品页面本身也有信息 我们不采纳吗」。取证发现三段断链：

1. **95 个 ozon_attributes 缓存，0 个含全表**——全是 webShortCharacteristics 短表（5 键左右）。
2. API 家族全灭：entrypoint / composer（×参数变体×4）均只下发 `webShortCharacteristics`；
   `webCharacteristics`（全表 widget）**不在任何 API 响应里**。
3. 后台 tab 滚动 0 行——v0.78 静默化把抓取 tab 后台化后，**IntersectionObserver
   永不触发**，全表 section 永不渲染。这是 95 缓存 0 全表的根因。

### 1.2 前台对照实验（根因实证）

前台可见 tab + scrollBy 逐步滚动 → dl(dt/dd) 提取 **7/7 行全表命中**。
结论：全表抓取必须「滚动触发懒加载」+「DOM 直读」，API 路线已死。

## 2. 三件修复

### A7a — skill 竞品全表 DOM 兜底（scripts/lib/ozon_scraper.py）

entrypoint `fullChars` 为空时（静默化后台 tab 的常态），追加 DOM 兜底：
点「все характеристики」展开按钮 → `scrollBy` ×10 触发 IntersectionObserver →
解析 `dl > dt/dd` 逐行提取 → 填 `result["characteristics"]`。
日志锚点：`DOM 兜底提取全表特征： N 行`。API 有 fullChars 时零变化。

### A7c — 箱规渗透源头修（worker）

1688 批发键「箱装数量/起批量」渗到 Ozon 数量属性 8513/11650/23249（gate 实录：
卡 6446931479 被填 500）。两条渗透路径**源头同时封**：

1. 组匹配：`attr_synonyms.json` quantity 组加 `zh_exclude_keywords:
   ["箱装","起批","装箱","批发","批量","包装数量"]`；
   `match_attr_name_synonym`（attribute_utils.py）消费排除表。
2. v0.64 中文直搜旁路（prepare `_fill_optional_dict_attrs`）：
   同名函数第二渗透路径（「箱装数量」共享「数量」字符触发）——rule 循环与
   `_shared` 列表同步过滤 zh_exclude_keywords。**A4 出口语义闸保留**作纵深。

### A7b — A5 prompt 竞品证据（worker/utils/attr_fill_extras.py）

`build_llm_schema_prompt` 注入 `comp_dump`（draft.ozon_attributes 前 30 条，
俄语/英语键）+ prompt 行：竞品特征来自 Ozon 同类商品卡、优先级高于 1688 推断、
品牌不填。LLM 提案仍过 A5 验证闸（字典精确命中/Boolean 直通/数值 sanity），
不因证据来源降验证标准。

## 3. 测试

`tests/test_competitor_fullattrs_v086.py`（4 用例）：
箱规键排除（组匹配）/fill_optional 跳过箱规（旁路路径）/prompt 含竞品特征行/
无竞品时无该行。回归：属性族套件 70 passed + ruff（CI 口径全规则集）+
skill 全量（含 scraper 改动）。

## 4. 实机 Gate（待补）

- [ ] 清 ozon_cdp 缓存 → 本地 Docker 跑一单 follow（新竞品）
- [ ] 日志出现「DOM 兜底提取全表特征： N 行」（N>0）
- [ ] 信封 ozon_attributes 全表键数 > 短表基线（对比 95 缓存均值）
- [ ] A5 日志出现竞品证据采纳行
- [ ] 对比 A6 基线（喷雾器 83%）的增量

## 5. 风险与边界

- DOM 兜底依赖前台结构（dl/dt/dd）——Ozon 改版需重校；失败静默回落短表，
  不阻塞主链（与「富化绝不抛异常阻断」纪律一致）。
- 竞品证据只进 prompt（A5），不直通 payload——验证闸是唯一出口，无绕过路径。
- skill 侧 ozon_scraper.py 改动：发版需走 compile.py（Python 3.12 ABI）流程。
