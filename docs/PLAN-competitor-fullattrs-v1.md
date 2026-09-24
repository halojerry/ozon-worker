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

## 6. Gate 实录（2026-09-25，本地 Docker + 测试店 5381204）

### 6.1 首跑失败 → 根因二段取证（首版 A7a 缺前台化）

- 首跑 follow（洗衣机清洁片 2790719032，同 URL）→ **DOM 兜底零日志**，缓存 chars=0、
  attrs=5 键短表 → 任务 failed「缺少必填属性」。
- 根因：A7a 兜底跑在 **v0.78 静默化复用的后台 tab** 上——`scrollBy` 照滚，但后台 tab
  渲染步骤整体跳过、IntersectionObserver 回调永不派发（取证节 1.2 的「后台 0 行」
  正是同一机制，首版代码只修了「滚动+解析」没修「tab 必须前台」）。
- 次坑：二跑 `from_cache=true`——`follow/` 信封缓存 TTL 21600s，重测必须同时清
  `ozon_cdp/` **和** `follow/` 两命名空间。

### 6.2 修复：CdpTab.bring_to_front() + 兜底前激活

- `cdp_client.py` 新增 `bring_to_front()`（Page.bringToFront，失败静默返回 False）。
- `ozon_scraper.py` DOM 兜底块执行前 `tab.bring_to_front()` + 0.4s 生效等待。
  trade-off：抓取期该 tab 短暂前台 ~6s（滑块重试路径已有可见 tab 先例）。

### 6.3 重跑实证（清双缓存后）

| 项 | 首跑 | 重跑 |
|---|---|---|
| DOM 兜底日志 | 无 | `DOM 兜底提取全表特征: 13 行（懒加载触发后）` |
| ozon_cdp 缓存 characteristics | 0 行 | **13 行**（Application area / Features of use / Units in one product / Package / Country of manufacture 等，短表 5 键全无） |
| 信封 ozon_attributes | 5 键 | **13 键** |
| 任务终态 | failed 缺必填属性 | **completed**，product_id=5837014560（import-by-sku 复制卡链，29 属性上卡，attributes/update 200） |

**结论**：同 URL 同竞品，5 键短表 → failed；13 键全表 → completed。全表特征不仅抬
fill 率，直接补上必填缺口救单。

### 6.4 诚实边界

- 本单为 follow hand 模式（类目解析失败自动降级 import-by-sku 复制竞品整卡），
  **A5 竞品证据 prompt（A7b）在该链路不触发**——复制卡链拿到的是竞品整卡本体
  （29 属性），强于 prompt 证据。A7b 生效面 = CREATE 新卡路径，待后续 discover/
  graph 单实机观察。
- skill 全量回归 1646 passed（首跑时 79 failed 为实机 follow 进程与 pytest 并行
  互踩串行闸/缓存的假阴性，单跑即绿，无并行后全量复跑全绿）。
