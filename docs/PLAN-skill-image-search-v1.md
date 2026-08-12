# Skill 识图找货链路改造方案 v1

> 日期: 2026-08-12
> 范围: skill 子项目（1688 识图 → 富化 → 信封）
> 依据: 竞品源码深挖（毛子ERP 3.2.2 / 上品帮 3.2.1）+ 双图实测 + 本仓库链路审计

---

## 1. 问题定义

### 1.1 用户痛点

1688 识图找货准确率不高，**没有徽章 DOM 时货源匹配效率直线下降**。

### 1.2 根因分析（实测证实）

实测 1688 官方图搜页（air.1688.com / aibuy 落地页）**根本不渲染"符合N/M"徽章**（badgeCount=0，未登录状态下）：

- 我们 skill 的 `_pick_best_match`（ozon_discovery.py:1281-1449）把徽章当核心信号之一：
  - `badge_eff >= 1.0` → 直接放行（:1378-1380）
  - 无徽章 → 走降级分支，只靠标题相关性 `conf >= 0.3`（:1415-1424）
- `follow_sell_cloud`（cloud_probe.py:2898-2899）注释明说"无徽标不重搜，交给标题相关性降级"
- 标题相关性依赖 `_RU_ZH_PRODUCT_WORDS` 词对词典（ozon_discovery.py:1064-1177）——**仅 ~70 词对，覆盖极窄**，自认"覆盖极窄"

**结论：我们依赖了一个官方页面默认不展示的装饰性元素作为核心匹配信号，导致无徽章时匹配质量暴跌。**

### 1.3 第二根因：两条识图通道从未结合

| 调用点 | CDP | AK | 关系 |
|---|---|---|---|
| `follow_sell_cloud`（:2905/:2942） | 主 | 备 | `if not matches_raw:` 才降级 |
| `_search_1688_source`（:1542/:1583） | 主 | 备 | CDP 空/被拒 → AK |
| `cli.py image_search`（:316-331） | `--source cdp` | `--source ak` | **互斥** |

**AK 通道原生 `similarity_score`（item.get("score")）解析了但从未被消费**——全库仅 1 处出现（ak_1688_client.py:397），`_pick_best_match` 不读它。AK 结果 badge 恒空 → 永远走"无徽标降级"分支 → 官方相似度信号被白白丢弃。

---

## 2. 竞品深挖收获（毛子ERP 3.2.2 为主）

### 2.1 毛子：图搜接入方式 = 官方落地页 URL 直连（零交互）

```js
// FindSourceDrawer（content.js）
const s = computed(() => {
  const g = "https://aibuy.1688.com/landingpage/home/inventory/products.html?bizType=ERP&customerId=zhijian";
  return n.imageUrl ? `${g}&outImageAddress=${encodeURIComponent(n.imageUrl)}` : g;
});
```

- **`outImageAddress` 参数直连 → 页面自动识图出结果，无需任何点击/注入**（我们实测确认）
- iframe 嵌入 + postMessage 双向通信拿结构化数据：
  - `exchange_config` → `api_config:{eventType:["distribution"]}`（声明授权）
  - `distribution` → `distributionParams`（1688 官方返回的商品列表）
  - `link_to_od` → `offerId` → 打开详情页
- **前置条件**：用户授权 1688 货源账号（`check_valid_account` + 授权弹窗），走官方分销（distribution）API
- 采集走自己后端 `/api.source.ali1688/collect`（服务端调官方 API）

### 2.2 上品帮：模拟人工上传（两跳）

```js
// ZQ 函数（main.js）
const ZQ = async (url) => {
  const file = await fetch(imgUrl).then(r=>r.blob()).then(b=>new File([b],"image.jpg"));
  const input = document.querySelector(".image-file-reader-wrapper");
  input.files = new DataTransfer().files;  // 实际 dt.items.add(file)
  input.dispatchEvent(new Event("change",{bubbles:true}));
  document.querySelector(".search-btn").click();
}
```

- `ob_search` 参数只带图不开搜，必须注入图片+点按钮 → 跳转 air.1688.com 官方图搜页
- 识别/排序/徽章全借官方，插件只负责"把图塞进去"
- 采集数据发自己服务端（shopbang.cn `apiType: goodsCollect1688`）

### 2.3 毛子稳定性设计（值得借鉴）

| 设计 | 实现 | 借鉴价值 |
|---|---|---|
| **DNR 移除 CSP** | background.js `modifyHeaders: content-security-policy remove`（规则 9001） | 解决 iframe 嵌入 1688/Ozon 被 CSP 拦截 |
| **防重复注入** | WXT `ContentScriptContext` 广播 SCRIPT_STARTED，新实例顶掉旧实例 | 我们已有 `find_tab 释放契约`，可对比 |
| **settle-wait 模式** | 包装 XHR/fetch/MutationObserver，等网络静默或 3s 超时再操作 | 提升 CDP 抓取稳定性 |
| **分布式并发池** | `Promise.race` + 3 并发收集（`d(S)` 函数） | 我们 fission 已用类似模式 |
| **渐进轮询** | list 页 500ms interval + rAF 节流 + 420/1200/2600ms 延迟重扫 | 参考 |
| **API 请求加密** | gzip + AES-CBC + HMAC 签名（`/api.chrome/check_data`） | 后端协议参考，非本地必须 |
| **登录态** | token 存 chrome.storage.local + GET_TARGET_STORAGE（跨 tab 读 ERP localStorage）+ UPDATE_LOGIN_STATUS 广播 | 我们已有 `_wait_for_login_session` 三态，可对比 |

### 2.4 毛子 1688 详情抓取（对比我们的 EXTRACT_1688_JS）

- 1688 商品页：**纯 DOM 解析**（`window.contextPath` + `#productPackInfo` 表格 + gallery），采集 POST `/api.chrome/collect`（`collect_from:1688`）
- 淘宝/天猫：**mtop API**（`h5api.m.taobao.com` + `_m_h5_tk` cookie 签名，MD5）
- Ozon 商品页：**entrypoint-api.bx/page/json/v2** 内部接口（webProductHeading-/webPrice-/webDescription-/webSellerList- widgets）——与我们 `ozon_widget.py` 思路一致
- **关键差异**：毛子 1688 抓取后采集数据交给后端，我们本地 `EXTRACT_1688_JS`（service.py:61-800）已提取 title/price/images/attributes/optionGroups/packagingRows/shipping/description/skuDetails——**覆盖比毛子 DOM 解析更全**，这部分我们已领先

---

## 3. 改造方案（分三层）

### 3.1 通道层：新增 aibuy 直连通道 + 无徽章去依赖（P0）

**目标**：识图准确率不再依赖徽章 DOM。

**方案 A（推荐）：mtop imagesearch API 直调（免浏览器，已实测成功）+ aibuy 落地页 CDP 兜底**

```
主: requests 签名直调 mtop.com.alibaba.cbu.crossBorder.lp.imageSearch
    (从 Chrome 会话拿一次 _m_h5_tk cookie → md5(token&t&appKey&data) 签名 → 结构化结果含 offerId/类目/供应商)
备: CDP 打开 aibuy 落地页 ?bizType=ERP&customerId=cbu&outImageAddress={url} (token 失效时)
    (URL直连自动出结果，实测确认；customerId 可省略由 1688 自动补 cbu)
```

实现位置：
- `ozon_image_search.py` 新增 `search_by_image_aibuy(image_url, ...)`：
  - **主路径（API 直调）**：读取 Chrome 会话 cookie（`_m_h5_tk/_m_h5_tk_enc/tfstk/isg`，复用 `chrome_launcher` 现有登录态）→ 先调 `image.upload` 拿 yoloCropRegion → 构造 imagesearch 请求（`imageAddress/imageRegion/pageSize`）→ mtop 签名直调 → 解析结构化结果
  - **⚠️ fail-fast 纪律（Momus 评审）**：无 token / token 过期 / 请求失败 → **快速返回 `[]`（不 raise、不重试、不慢等）**，由调用方降级到 CDP/AK。这保证 test_follow_*（未 mock aibuy）不被破坏，且无 Chrome 环境不阻塞
  - **mtop 签名封装**：新增 `_mtop_sign`/`_mtop_post` 私有函数（ozon_image_search.py 内），**不复用 `_post_1688`**（那是 AK 网关 x-csk 签名，认证体系不同）；完整请求模板（UA/Referer/cookie 组合/H5Request）锁进单函数 + 单测防漂移
  - **兜底路径（CDP）**：token 失效且无法刷新时，CDP 打开 aibuy 落地页 + 事件驱动等待 + 卡片提取
  - **无徽章依赖**——官方返回 `normalizationScore`（相关度分数）+ 结果顺序即官方相关性排序（guest 视图实测=精准排序）
- 缓存纪律：namespace `aibuy_search`（24h，key=image_url+语言维度）；token 缓存进 `data/config/`（含过期时间，过期自动刷新；⚠️ 日志脱敏，不打印 cookie/token 明文）
- **限流**（Momus 评审）：discover 并行路径（ozon_discovery.py:671-675 ThreadPoolExecutor）可能并发 N 个图搜 API 调用触发风控——aibuy 通道**并发上限 2** + 调用间小延迟；follow/cli 单次调用天然安全
- 与 AK 通道关系：aibuy 结果含 offerId 后直接进富化链路（`build_graph_envelope_with_retry`），**不经过 AK 图搜**；AK 图搜保留为无 Chrome 环境的兜底

**方案 B（增强）：iframe + postMessage 拿 distributionParams（对标毛子全自动）**
- 需要用户授权 1688 货源账号（`check_valid_account`）
- 收益：拿到官方结构化商品列表（含 offerId），不解析 DOM
- 风险：distribution 授权是 1688 分销体系，可能受账号资质限制
- **建议二期评估**：先落地 A，A 验证通过后评估 B 的授权门槛

**无徽章去依赖（与 A 配套，必须做，⚠️ 分通道护栏）**：
- `_pick_best_match` 徽章分支重构——**必须加 `trusted_source` 参数区分通道**：
  - **仅 aibuy 来源**（`trusted_source=True`）信任官方排序：**用 API 返回的 `normalizationScore` 做放行信号**（Momus 评审：比 idx_rank 更精准）——`normalization_score_eff ≥ 阈值`（取值域实测确认后定，0-1 或 0-100）即放行，idx_rank 仅作 tiebreak；无 normalizationScore 时退化 `idx_rank ≥ 0.33`（前 2 位）
  - **AK/CDP 来源维持现有护栏不变**（conf≥0.3 + LLM rescue）——**绝不全放松**，否则 discover 的历史错配案例（"花插 ¥1"/"活体羊驼 ¥2000"，代码注释记载）会重放
  - `badge_eff` 从"核心放行条件"降级为"可选加分项"（已 v0.26 部分降权；full-badge 直通 :1378 仅对 badge≥1.0 的候选保留，不影响无徽章路径）
  - 删除/弱化 `follow_sell_cloud` 中"无徽标不重搜"逻辑（cloud_probe.py:2898-2899）
- 标题相关性增强：`_RU_ZH_PRODUCT_WORDS` 词典扩充（70→300）**降为 P2 独立并行轨道**——v0.26 已有 LLM semantic rescue 兜底窄覆盖，不阻塞主链路

### 3.2 匹配层：AK similarity_score 上膛（P1）

**目标**：AK 通道的官方相似度信号不再被丢弃。

- `_parse_product_item`（ak_1688_client.py:397）已解析 `similarity_score`，但无人消费
- 在 `_pick_best_match` 增加 AK score 映射：
  - 确认 score 取值域（0-1 或 0-100，实测 AK 接口验证）
  - 归一化为 `ak_score_eff`（等价 badge_eff 信号），参与评分：`score = idx_rank*50 + conf*30 + max(badge_eff, ak_score_eff)*20`
  - **护栏分支同步接受高分放行**（仅改公式不够——AK 结果 badge 恒空 → 永远走 no-badge 分支，该分支只看 conf/LLM rescue）：`ak_score_eff ≥ 0.8 且 idx_rank ≥ 0.5 → 放行`（与 trusted_source 区分保持一致：AK 走 AK 高分规则，不进 aibuy 的"信任官方排序"规则）
- 效果：AK 结果不再"永远走无徽标降级分支"，官方相似度直接参与排序与放行
- ⚠️ 语义澄清：AK score 属于 AK 通道信号，非 aibuy 的官方排序信号，两者在 `_pick_best_match` 中走不同放行规则，勿混为一谈
- 测试：test_image_search_guardrail.py 补 AK score 场景断言（"无徽标+AK高score放行"）

### 3.3 富化层：图搜→AK+CDP 富化链路对接（P2）

**目标**：识图找到品后，富化链路无感对接。

**现状（审计确认）**：富化入口唯一 = `build_graph_envelope_with_retry(item_id, detail_url)`（内部 = `get_product_details` AK 基础 → `enrich_product_with_cdp` AK+CDP 合并 → 类目 → 变体折叠 → 校验 → 信封）。follow 流程在 Step 5（cloud_probe.py:3055）用 `best["id"]` 构造 detail_url 调用。

**对接点**：aibuy 通道候选只需产出 `{id, title, price, image, normalization_score}` 结构，插入 follow_sell_cloud Step 4 的 `matches` 归一化（:2972-2985）即可无感进入 `_pick_best_match` → Step 5 富化。

**⚠️ 三个图搜调用点全部要接入**（Momus 评审：只做 follow 不够）：
1. `follow_sell_cloud` Step 4（cloud_probe.py:2972-2985）——跟卖主路径
2. `_search_1688_source` CDP 分支（ozon_discovery.py:1542）——discover 管线核心（:656/:672 并行调用）
3. `cli.py image_search --source cdp`（cli.py:317-323）——D1 以图搜款
- 三处共用 `search_by_image_aibuy`，各加一个 aibuy 优先分支即可（成本极低）
- `batch_test`（batch_test.py:304）直接调 `follow_sell_cloud`，**自动继承无需改动**

**offerId 已解决（Step 0 实测 + API 验证）**：aibuy imagesearch API 响应**直接含 `offerId` 字段**——无需"同款找商链接解析"（那是 CDP 时代残留）或 AK 回查。

**⭐ 关键链路：aibuy 找品 → AK 富化 → CDP 补物理数据（已实测打通）**

```
aibuy 图搜 API (找品)                    → offerId + 标题/价格/图/月销/类目ID/供应商/评分
  → get_product_details(offerId)  (AK 富化) → 三级类目/颜色规格SKU/完整标题/图
  → enrich_product_with_cdp       (CDP 补全) → 重量/尺寸/物流/描述/属性/变体选项
```

实测（offerId=707351271432，airtag 宠物项圈）：
- ✅ AK offer_detail 返回：`title/price/images/categories(宠物及园艺→宠物服饰配饰→宠物项圈)/sku_attributes(颜色,规格)`
- ⚠️ `weight_grams/dimensions_mm = None`——**AK 也拿不到物理数据，正是 CDP `probe_1688_page` 要补的**
- 结论：**aibuy 替代的是"AK 图搜 search_by_image"（找品环节），AK 的"详情 offer_detail"（富化环节）完整保留**——两条 AK 能力职责不同，aibuy 只替换其中找品那条，富化那条不动

**AK+CDP 富化链路本身已成熟（无需重构）**：
- AK 基座：title/price/images/categories/sku_attributes（`get_product_details`，ainext workflow offer_detail）——**吃 offerId，与 aibuy 输出天然衔接**
- CDP 补全：packaging/shipping/description/sku_details/attributes/option_groups（`EXTRACT_1688_JS` 20+ 字段）——补 AK 拿不到的重量/尺寸
- 合并规则 `_pick()`（ak_1688_client.py:1088-1111）：title 取长、CDP 非空覆盖
- 降级链完整：api_only 透传 / cdp_degraded 重试 / 软兜底 50g+密度估算 / Worker 100g 硬编码
- **唯一建议**：aibuy 通道若带回更全的物理数据（如毛子 distributionParams 有 SKU 表），**直接进 `draft.weight/dimensions` 或新增 `extensions.aibuy_*` 独立字段**——⚠️ 勿塞进 `competitor_weight_g/dimensions_mm`（该字段语义是"Ozon 竞品兜底"，C2 链会把 1688 数据当竞品数据消费，掩盖数据质量）

### 3.4 稳定性层：毛子技巧移植（P3，可选）

| 改进 | 现状 | 毛子做法 | 收益 |
|---|---|---|---|
| iframe CSP 拦截 | 未用 iframe | DNR modifyHeaders 移除 CSP | aibuy iframe 方案 B 必需 |
| 网络静默等待 | `_poll_probe` 轮询 | XHR/fetch/MutationObserver settle-wait | 减少抓取空页 |
| CDP 连接复用 | 已有 P5 `cdp=shared_cdp` | 跨 tab 复用 seller 会话 | 已领先，无需改 |

---

## 4. 落地步骤与验收

> ⚠️ 优先级（Momus 评审调整）：**Step 4 链路对接提前到 P1**（用户价值落地点，Step 1 不被接入就是死代码）；词典扩充降为 P2 独立并行轨道。改的是 **编译模块**（ozon_discovery.py / ozon_image_search.py 均在 COMPILE_FILES），每步完成需重编译 + `test_compile_lists.py` + 冒烟 import。

### Step 0（预研侦察，已完成 ✅ 2026-08-12）——真机验证未决假设

**侦察结果（Playwright 真实 Chrome，未登录 1688）：**

| 侦察项 | 结论 | 影响 |
|---|---|---|
| ① offerId 可得性 | 卡片 DOM 不暴露 offerId（无 `<a>`/data 属性），但**点击卡片跳转 `detail.1688.com/offer/{offerId}.html`**（实测 offerId=707351271432）；另有 **JSONP 结果接口 `mtop.com.alibaba.cbu.crossBorder.lp.imageSearch`**（requestBody 含 `imageAddress/imageRegion/pageSize`，响应含 offerId） | 获取方式二选一：CDP 拦截点击跳转 URL 提取，或拦截 imagesearch JSONP 响应 |
| ② guest 排序质量 | **未登录视图排序 = 精准图搜排序**——宠物梳图 top8 全为同款（宠物梳/剪），20 卡/page | "卡片顺序即官方相关性排序"立论成立，trusted_source 方案可行 |
| ③ customerId 归属 | **不依赖毛子 `zhijian`**——去掉参数后 1688 自动补 `customerId=cbu`（通用跨境客户 ID），结果正常（top1 仍同款） | 用 `customerId=cbu` 或省略，无借用竞品身份问题 |
| ④ 图搜链路 | URL 直连 → 页面自动调 `image.upload`（拿 yoloCropRegion）→ `imagesearch`（拿结果）→ 渲染卡片，全程零交互 | aibuy 通道实现 = CDP 打开 + 事件驱动等待 + 卡片提取 |

**⚠️ 重要修正（对方案的影响）**：
- **🎯 重大升级：`mtop...imagesearch` API 可免浏览器直调（已实测成功）**——从 Chrome 会话拿一次 cookie（`_m_h5_tk` + `_m_h5_tk_enc` + `tfstk` + `isg`），requests 用 mtop 签名算法（`md5(token & t & appKey & data)`）直调，**返回完整结构化数据**：`offerId/title/price/imageUrl/monthSold/repurchaseRate/cateLevel1Id/cateLevel2Id/companyName/compositeScore/shippingInfoModel/tpYear/offerPublishTime/normalizationScore`。连续 3 次调用全成功，token 复用稳定、响应不刷新 token。
- **token 来源**：冷启动 requests 拿不到（1688 反爬不向无浏览器指纹请求发 cookie，FAIL_SYS_SESSION_EXPIRED）。**必须从真实 Chrome 会话读取**——但我们的 skill 本来就有 Chrome（`chrome_launcher` 常驻 + 登录态保留），一次读取 token 后**图搜全程免浏览器**。
- **推荐实现**：`search_by_image_aibuy` = 读 Chrome cookie（`document.cookie` 或 CDP `Network.getCookies`）→ requests 签名直调 imagesearch API → 解析结构化结果。**CDP 页面解析降级为备选**（token 过期且无法刷新时）。
- **URL 构造定稿**：`https://aibuy.1688.com/landingpage/home/inventory/products.html?bizType=ERP&customerId=cbu&outImageAddress={url}`（或省略 customerId 让 1688 自动补）——仅作为 token 失效时的 CDP 兜底路径。

### Step 1（P0）：aibuy 通道（API 直调为主 + CDP 兜底）
- [ ] `ozon_image_search.py` 新增 `search_by_image_aibuy`：cookie 读取 → image.upload 拿 yoloCropRegion → imagesearch 签名直调 → 结构化解析；CDP 兜底路径
- [ ] token 缓存：`data/config/`（含过期时间，过期自动刷新）；调用失败自动降级 CDP
- [ ] 缓存：`aibuy_search` ns 24h，key 含语言/ID 维度；发布/验收时 `cache_clear("follow")` 或 key 加通道版本（防旧信封缓存掩盖新通道）
- [ ] 测试：`test_aibuy_search.py`（mock mtop 响应，断言签名/URL 构造/解析/缓存 key/降级逻辑）
- **验收**：真实 token 调用提取 ≥5 条结构化候选（含 offerId）；token 失效时自动降级 CDP 出结果

### Step 2（P0）：匹配逻辑去徽章依赖（分通道护栏）
- [ ] `_pick_best_match` 加 `trusted_source` 参数：仅 aibuy 信任官方排序（`normalization_score_eff ≥ 阈值` 放行，取值域实测确认；无此字段退化 idx_rank≥0.33）；AK/CDP 维持现有护栏
- [ ] `follow_sell_cloud` 删"无徽标不重搜"（cloud_probe.py:2898-2899）
- [ ] **列出会翻转/新增的测试用例**：如 `test_pick_best_match_llm_rescue_all_false_still_rejects`（test_wave1_fixes.py:116）在 aibuy trusted_source 下行为变化、AK/CDP 路径维持原断言
- **验收**：`test_image_search_guardrail.py` / `test_wave1_fixes.py` 全绿 + 新增"aibuy trusted 无徽章+靠前放行"、"AK/CDP 无徽章+弱 conf 仍拒绝"双向断言

### Step 3（P1）：AK score 上膛
- [ ] 确认 score 取值域（真实 AK 调用打日志）
- [ ] `_pick_best_match` 接入 `ak_score_eff`（评分公式 + **护栏分支高分放行**：`ak_score_eff≥0.8 且 idx_rank≥0.5`）
- [ ] `test_image_search_guardrail.py` 补 AK score 场景（"无徽标+AK高score放行"）
- **验收**：AK 结果排序不再垫底；高 score 项不再被 conf<0.3 拦截

### Step 4（P1，提前）：链路对接（三调用点统一接入）
- [ ] aibuy 候选 → `{id,title,price,image,normalization_score}` 归一化插入 matches（follow_sell_cloud:2972-2985）；`trusted_source=True` 作为 **`_pick_best_match` 函数参数**（非候选 dict 字段——aibuy 结果单通道同质，无需逐候选标记）
- [ ] `_search_1688_source` CDP 分支（ozon_discovery.py:1542）加 aibuy 优先分支
- [ ] `cli.py image_search --source cdp`（:317-323）加 aibuy 优先分支
- [ ] offerId 直接来自 API 响应字段（API 直调主路径无需"同款找商链接解析"——那是 CDP 时代残留）
- **验收**：`test_follow_*` + discover 相关测试全绿（aibuy 未 mock 时 fail-fast 返回 [] 不破坏断言）；follow/discover/image_search 真机冒烟（aibuy 找到品 → 富化 → 信封），冒烟前清 follow 缓存

### Step 5（P2）：词典扩充（独立并行轨道）
- [ ] `_RU_ZH_PRODUCT_WORDS` 70→300 词对，按高频类目分批
- [ ] 抽查词对质量（错误词对会引入新误匹配，LLM rescue 是最后防线）
- **验收**：抽样 N 个真实 Ozon 标题，`_ru_zh_title_overlap` 命中率提升（如 40%→70%）

### Step 6（P3 可选）：稳定性移植
- [ ] 视方案 B（iframe+postMessage）需要决定是否做 CSP 处理——⚠️ DNR 是 Chrome 扩展 API，我们走 CDP 需用 `Page.setBypassCSP`（新版 Chrome 已移除，需评估替代）；未用 iframe 则跳过

---

## 5. 风险与决策点

| 风险 | 影响 | 缓解 |
|---|---|---|
| aibuy 落地页 DOM 结构不稳定 | 卡片解析失效 | 多选择器兼容 + artifact 快照 + 失败降级现有 CDP 通道 |
| **aibuy 未登录 guest 视图排序≠精准图搜排序**（Step 0 未验证前） | 信任排序的立论根基动摇 | Step 0 用已知同款图对照验证 top1 |
| **1688 风控/反爬**（aibuy 是 ERP 业务页，outImageAddress 非公开参数） | 验证码/限流/参数下线 | 并发/频率上限 + 异常降级 CDP + 定期回归 |
| **`_m_h5_tk` token 生命周期**（冷启动 requests 拿不到，需 Chrome 会话） | token 过期后图搜中断 | token 缓存 + 过期自动刷新（复用 chrome_launcher 登录态）；刷新失败降级 CDP 兜底；**实测同 token 连续调用稳定、响应不刷新** |
| **mtop 签名算法**（`md5(token&t&appKey&data)`，需 H5Request=true） | 签名错误全接口失败 | 实测成功路径已固定（含 cookie 组合/tfstk/isg），封装为单函数 + 单测锁定 |
| **mtop 响应格式版本漂移**（字段名变更） | 结构化解析失败 | 宽松解析（字段缺失不 raise）+ artifact 快照 + 定期回归 |
| **image.upload 失败**（yoloCropRegion 拿不到） | 图搜主路径中断 | Step 1 验证 imagesearch 是否可省 imageRegion（仅 imageAddress）；失败降级 CDP |
| **旧缓存掩盖新通道**（follow 6h 信封缓存 / search CDP 缓存） | 切换后不生效、验收被绕过 | `cache_clear("follow")` + 缓存 key 通道版本 + settings.json 通道开关 |
| **无发布级回滚开关** | 出问题无法快速回退 | settings.json 加 `image_search_channel=aibuy|cdp`（复用参数化模式，零发版回滚） |
| distribution 授权门槛（方案 B） | 全自动采集不可用 | 先落 A，B 二期评估 |
| `_pick_best_match` 分制改动破坏现有测试 | 回归 | test_settings_guardrail 阈值参数化约束 + trusted_source 分通道 + 逐条列出翻转测试 |
| 全局放松护栏波及 discover 宁缺毋滥防线 | 历史错配案例重放 | **trusted_source 仅 aibuy 置 True**，AK/CDP 维持护栏 |
| 编译模块改动未重编译 | 4 平台分发缺二进制 | 每步验收含 compile.py + test_compile_lists + 冒烟 import |
| `_LLM_SEMANTIC_CACHE` 进程内 dict 无上限 | aibuy 扩大无徽章场景后内存增长 | 顺手加容量上限（非阻塞） |

**决策点（需确认）**：
1. 方案 A（aibuy 直连替换）vs 方案 B（iframe+postMessage 全自动）——建议先 A
2. aibuy 落地页 offerId 提取方式——**Step 0 真机侦察后定**（不写入 P0 验收）
3. 词典扩充工作量（70→300 词对）——P2 独立并行轨道，可多人分批

---

## 6. 附录：毛子 API 端点全集（参考）

```
/api.chrome/sku3?sku=          # Ozon SKU 状态查询（update_sales/rich/variant 标记）
/api.chrome/check_data         # 加密上传 Ozon 富化数据（gzip+AES-CBC+HMAC）
/api.chrome/check_login        # 登录态校验
/api.chrome/check_update       # 版本更新检测
/api.chrome/collect            # 1688 商品采集（collect_from:1688）
/api.source.ali1688/check_valid_account  # 1688 分销账号授权校验
/api.source.ali1688/collect    # 1688 分销商品采集（distributionParams）
/api.exchange_rate/index       # 汇率表
/api.selection.follow/import   # 跟卖导入
/api.selection.follow/edit     # 跟卖编辑
/api.user/check_vip_status     # VIP 校验
/api.watermark/templates       # 水印模板
/api.wb.collect/*              # WB 采集/直发
/api.wb.shop/simple_lists      # WB 店铺列表
/api.shop/lists / set_cookies / sync_warehouse   # 店铺/仓储
```
