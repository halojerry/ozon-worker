# 毛子ERP 浏览器插件（maozi-plugin-3.2.4）全量功能分析

> 本文档基于 Chrome 扩展源码 **maozi-plugin-3.2.4** 做字段级逆向分析（`/var/folders/g9/8bk889_56fq1rgn_23sxckl40000gn/T/opencode/maozier/plugin/maozi-plugin-3.2.4/`，解压自 `plugin.zip`）。
> 方法：manifest.json 权限/注入 → content.js（1.4MB，Vue SPA，含全部 UI）中文字符串提取与上下文还原 → background/popup/inject 脚本交互链路。
> **原则：只记录源码中真实存在的字符串与逻辑，不臆造。**

---

## 1. 插件总览

### 1.1 基本信息

| 项 | 值 |
|---|---|
| manifest_version | 3（MV3） |
| 名称 | 毛子ERP |
| 描述 | OZON & WB 一键跟卖 + AI上品神器，助你高效运营俄罗斯电商平台，解放双手轻松起量！ |
| 版本 | 3.2.4（同时硬编码在 `background.js` 常量 `T` 与 `content.js` 的 `Pe="3.2.4"`） |
| 图标 | assets/logo.png（16/32/48/96/128） |
| 后台 | `background.js`（Service Worker） |
| 弹出页 | popup.html（action.default_popup），入口 `chunks/popup-C2QnKO2P.js` |

### 1.2 文件清单与角色

| 文件 | 大小 | 角色 |
|---|---|---|
| `manifest.json` | 3.5KB | 权限、注入声明 |
| `content-scripts/content.js` | 1.4MB | **核心**：Vue3 SPA，全站注入，所有 UI（悬浮窗/弹窗/卡片 widget）与采集逻辑 |
| `content-scripts/content.css` | 824KB | UI 样式 |
| `background.js` | 5.4KB | Service Worker：API 代理、跨 Tab 通信、Cookie/localStorage 读取、CSP 移除 |
| `inject.js` | 1.5KB | 页面上下文脚本：PDD 原始数据读取、Ozon「天眼」Premium 前端解锁 |
| `popup.html` + `chunks/popup-C2QnKO2P.js` | 461B / 223KB | 浏览器工具栏弹窗（登录态展示/进 ERP） |
| `assets/*` | — | logo.png、logo-mz.png、new.png（新品角标）、favorite.png、favorite-filled.png（收藏） |

### 1.3 权限与授权域名

**permissions**：`activeTab`、`scripting`、`webRequest`、`cookies`、`storage`、`downloads`、`declarativeNetRequest`、`declarativeNetRequestWithHostAccess`

**host_permissions**（决定可注入/可读 Cookie 的域名）：

| 域组 | 域名 |
|---|---|
| 本地 | `http://localhost/*` |
| Ozon 前台 | `*://*.ozon.ru/*`、`*://*.ozon.kz/*`、`*://*.ozon.by/*` |
| 中国货源 | `*://*.1688.com/*`、`https://item.taobao.com/*`、`https://detail.tmall.com/*`、`https://chaoshi.detail.tmall.com/*`、`https://mobile.yangkeduo.com/*`、`http://*.yangkeduo.com/*`、`https://*.pinduoduo.com/*`、`https://item.jd.com/*`、`https://aliexpress.ru/*` |
| Amazon 全部站点 | `*.amazon.com` / `.ae` / `.ca` / `.cn` / `.co.jp` / `.co.uk` / `.au` / `.br` / `.mx` / `.tr` / `.de` / `.fr` / `.in` / `.it` / `.nl` / `.sa` / `.se` / `.sg` |
| Wildberries | `https://www.wildberries.ru/*` |
| 自有后台 | `*://*.maozierp.com/*`（ERP Web 端 `ozon.maozierp.com` / `wb.maozierp.com`，用于取登录 accessToken） |

**content_scripts**：`matches: ["<all_urls>"]`，所有页面注入 `content-scripts/content.js`。页面类型在运行时按 hostname 分流（见 1.5），不支持的页面挂空壳。

**web_accessible_resources**：`inject.js`、`assets/logo.png`、`assets/logo-mz.png`、`assets/new.png`、`assets/favorite.png`、`assets/favorite-filled.png`、`content-scripts/content.css`（全站可访问，供页面上下文脚本与图片使用）。

### 1.4 注入方式（Shadow DOM）

所有 UI 都挂在自定义元素 **`<maozierp-ui>`** 的 shadowRoot 内（`content.js` 用 Web Components 规范创建 custom element，样式通过 `content.css` 注入），消息提示注入 shadowRoot 的 `#message-container`。隔离容器避免被页面样式污染。

### 1.5 页面类型路由（pageType 检测）

`App` 组件按 `window.location.hostname` 分流，注册全局函数供 widget 调用：

| hostname 规则 | pageType | 挂载组件 | 悬浮窗按钮 |
|---|---|---|---|
| `ozon.ru` / `ozon.kz` / `ozon.by` | `ozon` | `ozon`（Kee，最复杂） | 打开OZON后台 / 一键上架 / 计算利润 / 定价工具 / 绑定Cookie / 设置选品 / 隐藏卡片 / 其它卡片 |
| 其中 `/product/` 开头 | 子态 `product`（详情页） | — | 一键上架按钮仅详情页显示 |
| 其中 `/category` `/highlight` `/seller` `/search` `/brand` `/publisher` `/` | 子态 `category`（列表页） | — | 「隐藏卡片」开关 |
| `seller.ozon.ru` + `/app/analytics/graphs` | `analytics_graphs` | `OzonSellerAnalytics`（天眼解锁） | — |
| `1688.com` | `1688` | `1688`（tZ） | 采集商品 / 复制图片 |
| `taobao.com` | `taobao` | `taobao` | 采集商品 / 复制图片 |
| `tmall.com` | `tmall` | `tmall` | 采集商品 / 复制图片 |
| `pinduoduo.com` / `yangkeduo.com` | `pdd` | `pdd` | 采集商品 / 复制图片 |
| `jd.com` | `jd` | `jd` | 采集商品 / 复制图片 |
| `aliexpress.ru` | `aliexpress` | `aliexpress` | 采集商品 / 复制图片 |
| `amazon.*` | `amz` | `amz` | 采集商品 / 复制图片 |
| `wildberries.ru` | `wb` | `wb`（ere） | 打开WB后台 / 一键上架 / 计算利润 / 定价工具 |
| 其它 | `default` | 无 | 不挂载 |

各货源平台详情页子态（决定「采集商品」按钮显隐）：1688=`detail.1688.com/offer`；淘宝=`item.taobao.com/item.htm`；天猫=`detail.tmall.com/item.htm`；拼多多=`goods.html`/`goods1`/`goods2`；京东=`item.jd.com/`；速卖通=`/item/` 或 `/_detail/`；Amazon=`/dp/` 或 `/gp/`。

### 1.6 登录体系

1. 弹窗/悬浮窗未登录态显示「请登录」。
2. 点击后：content script 用 `background GET_TARGET_STORAGE` 读取 ERP Web 站（`ozon.maozierp.com` 或 `wb.maozierp.com`）localStorage 的 **`maozierp-core-access`**（JSON），取其 `accessToken`。
3. token 写入 `chrome.storage.local` 键 **`maozierp-token`** → 调 `api.chrome/check_login` 校验。
4. 校验返回 `code===-1` 清 token；返回 `is_vip<1` 时清空本地缓存的选品规则 `productSelectionRules`。
5. 登录时若 webapp 未登录，自动新开 ERP 登录页，每 2s 轮询（30s 超时）。
6. 全站登录态同步：background 广播 `UPDATE_LOGIN_STATUS` 给所有 tab，App 统一更新。
7. popup 侧：读 `maozierp-token` 显示「已登录 / 未登录」，提供「退出插件登录」（清 token + 刷新当前页）。

---

## 2. 前台商品页注入 UI

### 2.1 悬浮窗（右下角侧边栏）

样式：`fixed bottom-5 right-5`，白色圆角卡片，红色阴影，宽度 144px（w-36），带毛子ERP logo 与黄色折叠圆点。

- **展开态**：logo + 「毛子ERP」标题 + 黄色收起按钮；登录后按 pageType 渲染按钮组（见 1.5 表），按钮为圆角全宽：
  - OZON：`打开OZON后台`（未登录/无 seller tab 时）、`一键上架`（仅详情页）、`计算利润`、`定价工具`（琥珀色强调）、`绑定Cookie`、`设置选品`；
  - 列表页额外 `隐藏卡片` Switch（键 `maozierp-disturbing`，开=隐藏列表页卡片）；详情页额外 `其它卡片` Switch（键 `maozierp-disturbing-detail`）；
  - WB：`打开WB后台`、`一键上架`、`计算利润`、`定价工具`；
  - 货源平台：`采集商品`、`复制图片`；
  - 底部：有新版本时显示红色 `有新版本!点击下载`（跳 download_url）；`进入ERP`；
  - 未登录：`请登录` + `登录有问题？` tooltip（提示 1.关闭浏览器重新打开 2.卸载插件重新安装 3.仍然无法登录请联系客服）。
- **折叠态**：40px 圆形浮动 logo 图标，可拖拽移动，位置记忆到 `maozierp-button-position`（默认 `{bottom:"20px", right:"20px"}`），点击展开。

### 2.2 Ozon 列表页商品卡片 Widget（CategoryWidget）

注入点：Ozon 列表卡片 DOM `.tile-root[data-key]`；类目页另匹配 `#contentScrollPaginator .tile-root` 与 `#paginatorContent .tile-root`。MutationObserver 监听新卡片，widget 自身标记 `data-key` 以 `mz-` 前缀防重复。

卡片结构（渐变粉白底圆角卡片，内嵌每个 SKU）：
- 顶部工具条：logo + 圆形小按钮（tooltip 悬停）：
  - **一键上架**（`window.followProductFromList`）
  - **编辑上架**（`window.editFollowProductFromList`）
  - **计算利润**（`window.openProfitCalculator`）
  - **1688找同款**（`window.searchSimilarProduct`，用主图搜索）
  - **设置显示字段**（`window.openFieldSettings`，打开 FieldSettingsModal）
- 数据行：`字段名：值`，可点击复制（copy 字段显示复制图标），颜色/suffix 按字段配置（见下方字段表）；`跟卖列表` 字段 hover 弹出卖家表格；带 tooltip 悬停提示。
- **选品标签**：命中选品规则时显示彩色 Tag（规则标签名 + 颜色）。
- **新品角标**：`月销量>0 && 上架天数<31` 时右上角叠加 `new.png`。
- **收藏按钮**：右下角爱心图标（favorite.png/favorite-filled.png），切换收藏（`/api.product.favorite/toggle`）。

#### Ozon 列表/详情页可展示字段（36 个，FieldSettingsModal 中勾选）

字段定义 `{field, name, tips, suffix, color, isConvert, copy, show, hidden}`：

| field | 名称 | 默认显示 | 后缀/颜色 | tooltip 原文 |
|---|---|---|---|---|
| category | 类目 | ✅ | | 商品所属类目 |
| rfbs_rate | rFBS佣金 | ✅ | 分三段 Tag（售价≤1500₽/1500-5000₽/>5000₽） | |
| fbp_rate | FBP佣金 | ❌ | 同上三段 | |
| sku | SKU | ✅ | 可复制 | 商品SKU |
| brand | 品牌 | ✅ | 蓝色 | 商品品牌 |
| soldCount | 月销量 | ✅ | 蓝色 | 商品月度销售数量 |
| soldSum | 月销售额 | ✅ | 蓝色 | 商品月度销售额 |
| salesDynamics | 月周转动态 | ✅ | ±%红绿 | 与上一个月相比订单金额总和发生了怎样的变化 |
| avgOrdersOnAccDays | 日销量 | ❌ | 蓝色 | 近一个月销售件数，除以商品有现货的天数，退货和取消不纳入计算 |
| avgGmvOnAccDays | 日销售额 | ❌ | 蓝色/₽ | 近一个月销售金额除以商品有现货的天数… |
| drr | 广告费占比 | ✅ | %/蓝色 | 商品推广费用占所有订单金额的百分比 |
| daysInPromo | 参与促销天数 | ✅ | | 商品近一个月参与促销的天数 |
| discount | 参与促销的折扣 | ✅ | % | 近一个月参与促销的平均折扣 |
| promoRevenueShare | 促销活动的转化率 | ✅ | %/蓝色 | 促销期间订购的金额，在总订购金额的占比 |
| daysWithTrafarets | 付费推广天数 | ✅ | 蓝色 | 近一个月参与模版付费推广的天数 |
| qtyViewPdp | 商品卡浏览量 | ✅ | | 买家打开商品卡片的次数 |
| convToCartPdp | 商品卡加购率 | ✅ | % | 商品卡片浏览次数与浏览后将商品添加到购物车的数量之间的比例 |
| sessionCountSearch | 搜索目录浏览量 | ✅ | | 买家在搜索结果中和类目中查看商品的次数 |
| convToCartSearch | 搜索目录加购率 | ✅ | % | 商品添加到购物车的次数与在目录和搜索结果中浏览次数之间的比例 |
| convViewToOrder | 展示转化率 | ✅ | % | 商品在网站所有页面上的展示次数与订单数量的比例 |
| custom_click_rate | 商品点击率 | ✅ | 橙色#ff7900 | 买家点击商品的次数与商品在网站所有页面上的展示次数之间的比例 |
| salesSchema | 发货模式 | ✅ | | 商品发货模式 |
| nullableRedemptionRate | 退货取消率 | ✅ | %/红#ff4d4f | 商品退货取消率 |
| custom_volume | 长 宽 高 | ✅ | | 商品长宽高(厘米) |
| custom_weight | 重 量 | ✅ | | 商品重量(克) |
| nullableCreateDate | 上架时间 | ✅ | | |
| follow_info | 跟卖列表 | ✅ | 弹出卖家表 | 商品的跟卖者信息列表 |
| follow_min_price | 跟卖最低价 | ✅ | 蓝色/汇率换算 | 商品的跟卖最低价 |
| follow_max_price | 跟卖最高价 | ✅ | 汇率换算 | 商品的跟卖最高价 |
| createDays | 上架天数 | ❌(hidden) | | 商品上架天数 |
| soldSumCny | 月销售额(CNY) | ❌(hidden) | | 商品月度销售额(人民币) |
| soldSumRub | 月销售额(RUB) | ❌(hidden) | | 商品月度销售额(卢布) |
| avgGmvOnAccDaysCny | 日销售额(CNY) | ❌(hidden) | | 商品日度销售额(人民币) |
| category_ids | 类目ID | ❌(hidden) | | |
| rating | 评分 | ❌(hidden) | | 商品评分 |
| reviewsCount | 评论数 | ❌(hidden) | | 商品评论数 |

**FieldSettingsModal**：标题「选择需要展示的数据」，`width:600`，全选 Checkbox + 三列网格勾选 + 底部「已选择 N / M 个字段」，确定后经 `ozon_field_settings` 持久化（保存/恢复字段的 show 状态）。

**跟卖列表弹出表**（follow_info hover）列：头像(logoImageUrl)、卖家(name+link)、地区(credentials 国家码→中文国家名)、SKU(可点链接 + 表头「复制」整列)、价格↓(cardPrice.price)、评分(★ x.x)、评论数。地区默认「俄罗斯」。

### 2.3 Ozon 详情页（/product/）

- **顶部操作条**（插入页面现有价格区域）：`一键上架`（红色，打开 FollowProductModal）、`编辑上架`、`复制图片`、`采集商品`（打开 DetailCollectModal，含 loading）。
- **黑标价标签**（`mz-black-price-placeholder`）：在黑标价旁显示计算出的可上架价。黑标价公式：
  - 无绿标（cardPrice）时：`黑标价 = 黑标价 ÷ 1.0715`
  - 黑 < 绿×1.06：`黑标价 = 黑 × 0.97`
  - 黑 ≥ 绿×1.06：`黑标价 = 黑 × 3.24 − 绿 × 2.24`
  - tooltip 原文：「黑 ÷ 1.0715」/「黑 < 绿 × 1.06：黑标价 = 黑 × 0.97」/「黑 ≥ 绿 × 1.06：黑标价 = 黑 × 3.24 − 绿 × 2.24」
- **利润计算卡片**（`mz-profit-card-placeholder`，ProfitCalculatorCard）：展示黑标价/销售价切换、重量/长宽高、物流（陆运/陆空/空运/邮政物流 ChinaPost）、物流运费、跨境物流费（计抛/不计抛）、抛重/实际重、佣金分段、利润空间、「有利/中等/不利」判定、`恢复当前商品的初始数据`/`重置`/`快速计算利润`/`收起计算利润`。
  - 参数：labelFee(标签费，默认2)、adRate(广告费率，默认0)、miscRate(杂费率，默认4.5)、cnyToRubRate(默认10.97)、物流计划列表（来自 `/api.tool/get_profit_calc_config`，含 rule_sets.general.volume_weight.divisor 默认 12000、各线路 weight/price 范围、tier）。
- **定价工具&利润计算器**：右侧可拖宽 Drawer（宽度记忆 `drawerWidth`，默认500），内嵌 iframe 加载 ERP 站 `#/calculate2?sell_price=&package_weight=&package_length=&package_width=&package_height=&rfbs_rate=&category_ids=`（数据从当前商品自动带入）。

### 2.4 WB（wildberries.ru）商品卡片/详情

- WB 列表页：把 `<maozierp-ui>` 注入 WB 卡片（锚点 `.product-card__bottom-wrap` / `.product-card__bottom`），卡片样式 `mz-wb-bang-item`，滚动/路由懒加载。
- **WbDetailCard**（详情页卡片）：logo + `1688找同款`、类目、SKU(复制)、品牌、月销量、月销售额(₽ 与 ¥换算)、商品价格、库存、评分、评价数、重量(kg)、尺寸(cm)、`跟卖列表`（头像/卖家/复制/价格/评论数/N 个卖家）、跟卖最低价/最高价、按钮：`一键上架` / `编辑上架` / `复制图片` / `采集商品`。
- WB 数据来源：
  - 详情：`https://www.wildberries.ru/__internal/u-card/cards/v4/detail?curr=rub&dest=-1257786&nm={nmIds}`（Header `deviceid`，来自 wbx__sessionID localStorage 或 "wb"）；
  - 月销：批量 POST `/api.chrome/wb_sales`（body `{products:[{sku,...}]}`）；
  - 汇率：`/api.exchange_rate/index`（currency_from=RUB）；
  - 商品卡特征（characteristics）中的包装尺寸「Длина упаковки/Ширина упаковки/Высота упаковки」在采集时清空。
- WB 页面识别：详情=`/catalog/\d+/(detail.aspx|feedbacks|questions)`；列表=根路径/`/catalog/`/`/search`/`/brands`/`/promotions`/`/seller` 等。

### 2.5 货源平台采集（1688/淘宝/天猫/拼多多/JD/速卖通/Amazon）

统一行为：详情页出现「采集商品」「复制图片」按钮，采集后 POST `/api.chrome/collect`，`collect_from` 区分来源。

| 平台 | 采集方式 | collect_from |
|---|---|---|
| 1688 | DOM 解析（collected_by:"dom_parser"）：标题/描述/图片/video/skus/属性/`#productPackInfo` 重量；另有「采集规格」配置（使用方法/表格、标准属性列表、规格选项、详情的笛卡尔积组合、使用默认价格、扁平化规格选项） | `1688` |
| 1688 图搜 | 检测 `image_search/youyuan/index.html?ob_search`，把 ob_search 图片URL写入剪贴板→触发 paste 事件→点击 `.search-btn[data-tracker="pasteImagePreview"]` | — |
| 淘宝 | mtop API `h5api.m.taobao.com/h5/mtop.taobao.pcdetail.data.get/1.0/`（appKey=12574478，`_m_h5_tk` cookie 签名）+ 详情 `mtop.taobao.detail.getdesc/7.0/` | taobao |
| 天猫 | 同 mtop 协议（h5api.m.tmall.com） | tmall |
| 拼多多 | inject.js 读 `window.rawData`（GetPddWindowData 消息）；兜底调 `/proxy/api/api/oak/integration/render/sku?pdduid=`（pdd_user_id cookie）拿 goods/skus | pdd |
| 京东 | 页面 DOM + `img30.360buyimg.com/sku/` 图片 | jd |
| 速卖通 | `https://aliexpress.ru/aer-jsonapi/v1/bx/pdp/web/productData` | aliexpress |
| Amazon | 页面 DOM（按 region 区分国家，含价格换算人民币） | amz |
| Wildberries | 见 2.4 | wb |

`复制图片`：把商品全部图片 URL 复制到剪贴板。

**1688 授权提示**：`应1688要求，请授权1688货源账号后再进行采集，授权后采集更稳定，更快速！`（通过 `/api.source.ali1688/check_valid_account` 检测，向 1688 页面 iframe postMessage `exchange_config` 授权）。

**1688找同款 Drawer**（FindSourceDrawer）：左侧 iframe 加载 `https://aibuy.1688.com/landingpage/home/inventory/products.html?bizType=ERP&customerId=zhijian&outImageAddress={图片URL}`，右侧选中商品批量采集（`/api.source.ali1688/collect`，并发3，进度条+成功/失败计数）；「没有选择商品」提示；支持陆运/陆空/空运选择。

---

## 3. 一键上架配置（FollowProductModal — 核心表单）

### 3.1 弹窗结构

- 标题「一键上架到OZON」，`width:75%`，`min-width:1300px`，`maskClosable:false`。
- **顶部信息条**（可关闭，键 `maozierp-follow-product-alert-closed`）：
  > 「注意：请先选择上架货币，再批量设置价格，如果你选择了多个店铺，请确保所选的店铺货币一致，最终上架货币以店铺设置为准。表格左侧的勾选框只做批量删除变体用途。(如果设置了库存，系统则将在上架成功后自动添加库存)」
- **左侧栏**：店铺选择组件 ListingShopAside（见 3.3）。
- **右侧**：表单 + 变体表格。
- 底部：`上架货币` 下拉 + `显示所有SKU：是/否` + `一键上架至OZON` + `取消`。

### 3.2 表单字段（OZON）

表单 model 默认值：`{scene:"plugin", shop_ids:[], brand:"none", image_order:"none", follow_type:"hand", source_price:"", source_url:"", source_remark:"", watermark_id:0, model_id:"", floating_price:undefined, rows:[]}`。

| 字段 label | name | 控件 | 选项 / 说明 | 默认值 | 校验 |
|---|---|---|---|---|---|
| 选择店铺 | shop_ids | 多选 Select | 每个店铺显示 `[币种] 名称`，可搜索；不校验（左侧栏校验） | 默认选默认店铺 | — |
| 品牌 | brand | Select | **复制当前品牌**(copy) / **无品牌**(none) | none | required「请选择品牌」 |
| 图片顺序 | image_order | Select | **不处理**(none) / **随机打乱**(shuffle) / **主图不变,其余打乱**(main_fixed) | none | required |
| 上架方式 | follow_type | Select | **防侵权跟卖**(hand) / **强制跟卖**(api)。tooltip:「防侵权跟卖：系统会模拟人工手动上架产品，降低下架风险。强制跟卖：1:1复制当前商品卡片，有一定的概率会报错和被下架」 | hand | required |
| 水印 | watermark_id | Select | **不使用**(0) + `/api.watermark/templates` 模板列表。tooltip:「如果店铺有绑定水印则此处设置无效」 | 0 | — |
| 合并变体 | model_id | Input+随机按钮 | placeholder「选填:型号名称」；随机生成 `mz-{随机15位}`。tooltip:「不填保留默认，如果填写了型号名称则按填写的型号合并变体」 | "" | — |
| 浮动价格 | floating_price | InputNumber | placeholder「选填:浮动加价上限」，前缀币种符号，整数。tooltip:「如若填写则在每个店铺随机0至所填写数字中随机加价,请填写整数,例：填写1，则会随机在0.00和0.99之间加价」 | undefined | ≥0 |
| 货源价格 | source_price | Input | placeholder「选填」 | "" | — |
| 货源链接 | source_url | Input | placeholder「选填」 | "" | — |
| 货源备注 | source_remark | Input | placeholder「选填」，宽300px | "" | — |

**货币**（上架货币，选择后影响批量价格币种符号与汇率换算）：

| label | value | 符号 |
|---|---|---|
| [¥]人民币 | CNY | ¥ |
| [₽]俄罗斯卢布 | RUB | ₽ |
| [$]美元 | USD | $ |
| [€]欧元 | EUR | € |
| [Br]白俄罗斯卢布 | BYN | Br |
| [₸]哈萨克斯坦坚戈 | KZT | ₸ |

**显示所有SKU** Switch（importAllSku）：开=表格显示全部变体（懒加载变体数据），关=仅主 SKU。

表单记忆：`followProductFormMemory`（chrome.storage.local），保存 shop_ids/brand/image_order/follow_type/watermark_id/floating_price，下次打开自动恢复（店铺/水印/浮动价分别做合法性校验，非法字段删除）。

### 3.3 店铺选择侧栏（ListingShopAside）

- 模式 Tab：**选择店铺**（shop）/ **店铺分组**（group）。
- 店铺模式：
  - 搜索框「搜索店铺名称」、分页（10/页）、「全选本页」、「已选择 N 家店铺」；
  - 每个店铺卡片：Checkbox + 店铺名 + 币种 + **同步仓库**按钮（`/api.shop/sync_warehouse`，转圈动画）+ 仓库下拉（不选仓库/各仓库）+ 库存输入框（「请输入库存」，placeholder）；
  - 无仓库时显示「暂无仓库，仅上架商品」；「共 N 家店铺，默认库存 M」。
- 分组模式：`/api.shop_group/simple_lists` 分组列表，选中分组后展示组内店铺卡片 + 每店仓库/库存（默认库存来自分组 default_stock）。
- 提交数据 `getSubmitSelection()`：
  - shop 模式 → `{listingMode:"shop", shopIds:[...], shopStockTargets:[{shop_id, warehouse_id?, stock?}]}`（只对「选定了仓库且填了库存」的店输出 warehouse/stock）；
  - group 模式 → `{listingMode:"group", groupId, shopIds:[], shopStockTargets:undefined}`。
- 选择记忆：`followProductShopSelection`（含 listingMode/shopIds/groupId/shopStockTargets）。
- 店铺列表：POST `/api.shop/lists`（{page, page_size:100}）；店铺字段 id/name/currency/is_default/warehouses。

### 3.4 变体表格

列：**序号、主图、变体(标题)、SKU、货号(offer_id)、原售价(sell_price)、我的售价(price)、我的划线价(old_price)、自定义重量(g)、包装尺寸(mm)(选填，长×宽×高)、条形码(FBP)(选填)、操作(删除)**。

- 行选择：`全选` / `反选` / `删除所选`（勾选框「只做批量删除变体用途」，如顶部提示）。
- 校验规则：`货号不能为空`；`划线价必须大于售价`；`请输入有效的价格`。
- 批量操作（表头下拉）：
  - **货号一键生成**（货号生成设置，规则见 3.5）；
  - **批量设置售价**（我的售价列表头）：`原售价倍数`(multiple，默认 0.95，placeholder「请输入倍数，如1.2」+「倍」后缀) / `固定金额`(fixed，placeholder「请输入固定售价」，币种前缀)，应用后回写 price；
  - **批量设置划线价**（我的划线价列头，同上：原售价倍数/固定金额，placeholder「请输入固定划线价」）；
  - **条形码一键生成**（FBP）：时间戳 `YYMMDDHHMMSS` + 6 位随机数；
  - **应用首行重量 / 应用首行尺寸**（同首行）。
- 变体数量控制：至少保留一条数据。

### 3.5 货号（offer_id）生成规则

存储键：`maozierp-offerid-generation-rule`（规则）、`maozierp-offerid-custom-prefix`（前缀，默认 `mz`）。

| 规则 value | 名称 | 生成格式 |
|---|---|---|
| system | 系统默认 | `mz-{YYMMDDHH}-{6位随机数}`（如 mz-26081615-123456） |
| custom_prefix | 自定义前缀 | `{前缀}-{YYMMDDHH}-{6位随机数}` |
| source_sku | 源SKU+随机数 | `{源SKU}-{6位随机数}`（无 SKU 回退 `{前缀}-{YYMMDDHH}-{随机数}`） |
| prefix_sku | 自定义前缀+源SKU | `{前缀}-{源SKU}`（无 SKU 回退 `{前缀}-{YYMMDDHH}-{随机数}`） |

### 3.6 提交 payload（OZON）

POST `/api.selection.follow/import`，body：
```
{ scene:"plugin", shop_ids:[], group_id?, shop_stock_targets?,
  brand, image_order, follow_type, watermark_id, model_id, floating_price,
  source_price, source_url, source_remark, rows:[{sku, offer_id, sell_price, price, old_price, custom_weight, custom_depth, custom_width, custom_height, custom_barcode, stock?...}] }
```
- group 模式下删除 shop_ids/shop_stock_targets，只传 group_id；
- 成功提示「提交成功」，失败「提交失败：{msg}」。

### 3.7 WB 版差异（FollowProductModalWb，标题「一键上架 Wildberries」）

- 货币只有 **人民币(CNY) / 俄罗斯卢布(RUB)** 两项。
- 表单字段：shop_ids、品牌、图片顺序、水印、**合并变体 Switch**（`merge`，默认 1=默认合并变体；tooltip「默认合并变体，如果选择否，则每个变体会单独上架」）、浮动价格、货源价格/货源链接/货源备注（remark）、商品标题/描述/特征(characteristics)/类目(subject_id/subject_name)/包装重量/包装尺寸。
- 货号生成规则只有 3 项：系统默认 / 自定义前缀 / 随机数。
- 表格列：序号、主图、变体、货号、原售价、我的售价、包装重量、包装尺寸、操作；批量操作含「同首行」。
- 变体上限：**最多只能添加30条变体**；`showAll`「显示所有」开关。
- 提交 POST `/api.wb.collect/publish_direct`，body `{merge, shop_id, product_data, publish_type:"plugin"}`，sell_price 乘 RUB→CNY 汇率（`/api.exchange_rate/index`）取整。
- 表单记忆键 `followProductWbFormMemory`；提示条键 `maozierp-follow-product-wb-alert-closed`。

### 3.8 采集/编辑上架（DetailCollectModal 与编辑流）

- **采集商品**（OZON 详情页）：DetailCollectModal「采集商品」，`是否采集所有SKU：是/否` Switch + `批量删除` + 表格（图片 hover 预览/SKU/规格/删除）+ 分页（10/20/50）+ `确认采集`；确认后 POST `/api.selection.follow/edit`（`{scene:"plugin", sku, currecny, rows, json_content, is_direct_collect:1}`）。
- **编辑上架**（OZON）：采集当前商品信息 → POST `/api.selection.follow/edit` → 成功 1.5s 后跳转 **ERP Web `ozon.maozierp.com/#/product/ai/edit?id={jump_id}`**（AI 编辑页，插件外）。tooltip：「编辑上架：将会采集当前商品信息，然后手动编辑上架，你可以更改商品名称、图片、属性等信息。」
- **编辑上架**（WB）：POST `/api.wb.collect/direct_to_draft` → `wb.maozierp.com/#/product/ai/edit-details?id={draft_id}`。

---

## 4. 选品规则设置（ProductSelectionModal）

### 4.1 规则列表

标题「设置选品规则」，`width:900`，顶部「共 N 条规则」+ `新增规则`；表格列：**规则名称、标签、自动收藏(是/否)、是否启用(Switch 启用/禁用)、优先级、更新时间、操作(编辑/删除)**。删除带 Popconfirm「确认删除/确定要删除这个规则吗？删除后无法恢复。」。

保存按钮「保存设置(规则生效)」：把 `is_open===1` 的规则写入 `chrome.storage.local.productSelectionRules`（`{enabledRuleIds, enabledRules:[{id,name,tag,color,auto_favorite,conditions}], lastUpdated}`），并 `window.location.reload()`。

规则 CRUD API：`/api.selection.plugin/rule_list`(GET)、`/api.selection.plugin/add_rule`、`/api.selection.plugin/delete_rule`、`/api.selection.plugin/toggle_rule`（body `{id, is_open:1/0}`）。

### 4.2 规则编辑表单（新增/编辑选品规则，width:600）

基础字段：

| 字段 | name | 控件/约束 | 默认 |
|---|---|---|---|
| 规则名称 | name | 必填，max15「规则名称最多15个字符」 | "" |
| 标签名称 | tag | 必填，max6「标签名称最多6个字符」，卡片中显示 | "" |
| 优先级 | sort | InputNumber 0-100「优先级范围为0-100」，placeholder「请输入优先级」；提示「数值越大优先匹配」+ tooltip「注：这里优先级的意思是指当应用了多条规则时，卡片背景颜色以优先级高的规则为准。如果不理解请保持默认即可」 | 0 |
| 卡片背景颜色 | color | `<input type=color>`，显示色值或「不设置」，可「清除」 | undefined(不设置) |
| 是否自动收藏 | auto_favorite | 单选 是(1)/否(0) | 0 |

条件字段（均默认 undefined=不限；数值型为「最小值 至 最大值」双 Input）：

| 条件 | field 键 | 控件说明 |
|---|---|---|
| 品牌选项 | brand_option | 单选 有品牌(1)/无品牌(0)/不限(2)，默认 2 |
| 评分 | rating_min/max | 0-5 步长0.1，min/max |
| 月销量范围 | soldCount_min/max | 整数 |
| 月销售额范围 | soldSum_min/max | 整数，前缀 ¥ |
| 价格范围 | price_min/max | 整数，前缀 ¥，placeholder 最小价格/最大价格 |
| 重量范围 | custom_weight_min/max | 整数，后缀 g，placeholder 最小重量/最大重量 |
| 上架时间 | nullableCreateDate_min/max | 整数，后缀 天，placeholder 最小天数/最大天数 |
| 月周转动态 | salesDynamics_min/max | %（min 0-100、max 0-1000） |
| 广告费占比 | drr_min/max | % 0-100 |
| 参与促销天数 | daysInPromo_min/max | 天 |
| 参与促销的折扣 | discount_min/max | % 0-100 |
| 促销活动的转化率 | promoRevenueShare_min/max | % 0-100 |
| 付费推广天数 | daysWithTrafarets_min/max | 天 |
| 商品卡浏览量 | qtyViewPdp_min/max | 整数 |
| 商品卡加购率 | convToCartPdp_min/max | % 0-100 |
| 搜索目录浏览量 | sessionCountSearch_min/max | 整数 |
| 搜索目录加购率 | convToCartSearch_min/max | % 0-100 |
| 展示转化率 | convViewToOrder_min/max | % 0-100 |
| 发货模式 | salesSchema | Select 不限/`FBO`/`FBS` |
| 退货取消率 | cancelRate_min/max | % 0-100 |
| 跟卖人数 | seller_count_min/max | 人，max 2000 |
| 跟卖最低价 | follow_min_price_min/max | 小数，precision 2 |

（默认条件对象另含 `avgOrdersOnAccDays`/`avgGmvOnAccDays` 键，但表单 UI 未渲染该两项，仅保留在数据结构中。）

### 4.3 规则匹配逻辑

widget 加载后读 `productSelectionRules`，对商品字段值做条件比对（`Hb`），全部命中则卡片渲染该规则 Tag（标签名+颜色），多条规则时背景色取优先级最高者。匹配字段映射：月销售额条件用 `soldSumCny`、上架时间条件用 `createDays`、退货取消率用 `nullableRedemptionRate`、跟卖人数用 `follow_info` 等。品牌条件排除「--/暂无数据/无/无品牌/без бренда」。

---

## 5. AI 套图

> **源码事实**：`content.js` / `popup.js` / `background.js` 中**不存在**「毛豆、套图、白底图、场景图、卖点图、首屏主视觉、核心卖点、使用场景、AI写文案、gpt-image/dalle」等字符串。AI 套图/文案功能**不在插件包内**，属于毛子ERP Web 端（`ozon.maozierp.com`）。

插件与 AI 编辑的衔接点（均在源码中可证）：

1. **编辑上架入口**：OZON「编辑上架」→ POST `/api.selection.follow/edit` → 返回 `jump_id` → 跳转 `ozon.maozierp.com/#/product/ai/edit?id={jump_id}`；WB「编辑上架」→ `/api.wb.collect/direct_to_draft` → `wb.maozierp.com/#/product/ai/edit-details?id={draft_id}`。跳转路由带 `/ai/` 前缀，即 ERP 的 AI 编辑/套图页面。
2. **利润工具**：`#/calculate2`、`#/product/ai/edit-details` 等路由均由插件 iframe/跳转进入 Web 端。
3. **水印模板**：插件内仅「选择水印」（`/api.watermark/templates`），水印的增删改/制作在 Web 端完成。

若需补全 AI 套图的字段级细节（4 图类型/模块/毛豆计费/AI 文案），需另行分析 `ozon.maozierp.com` 的 Web 端 JS 资源（不在本次插件源码范围内）。

---

## 6. 批量翻译 / 水印

- **批量翻译**：插件包内无「翻译」相关代码。翻译发生在后台/ERP（采集数据加密上传后由服务端处理；`json_content` 为原始 DOM 数据）。
- **水印**：插件侧仅水印**选择**（3.2 表单字段），数据来自 `POST /api.watermark/templates`；「不使用」= 0；「如果店铺有绑定水印则此处设置无效」（店铺级水印优先于弹窗选择）。水印实际叠加渲染由后台在图片处理环节完成（`/api.selection.follow/import` 提交后）。

---

## 7. 设置项汇总（chrome.storage.local 键）

| 存储键 | 内容 |
|---|---|
| `maozierp-token` | ERP accessToken（登录态） |
| `productSelectionRules` | 启用的选品规则 JSON |
| `followProductFormMemory` | OZON 一键上架表单记忆 |
| `followProductWbFormMemory` | WB 一键上架表单记忆 |
| `followProductShopSelection` | 店铺选择记忆（listingMode/shopIds/groupId/shopStockTargets） |
| `maozierp-offerid-generation-rule` | 货号生成规则（system/custom_prefix/source_sku/prefix_sku） |
| `maozierp-offerid-custom-prefix` | 货号自定义前缀（默认 mz） |
| `maozierp-batch-price-value` | 批量售价设置值 |
| `maozierp-batch-old-price-value` | 批量划线价设置值 |
| `maozierp-disturbing` / `maozierp-disturbing-detail` | 列表页/详情页「隐藏卡片」开关 |
| `maozierp-button-position` | 悬浮球位置 |
| `drawerWidth` / `wbDrawerWidth` | OZON/WB 利润工具抽屉宽度（默认500） |
| `maozierp-follow-product-alert-closed` | OZON 弹窗提示条关闭态 |
| `maozierp-follow-product-wb-alert-closed` | WB 弹窗提示条关闭态 |
| `ozon_field_settings` | 列表页显示字段配置 |
| `maozierp-core-access`（在 ERP 站点 localStorage） | Web 端 accessToken（登录来源） |

---

## 8. 与后台 / API 的交互

### 8.1 后端 API 清单（全部经 background `API_REQUEST` → `fetch`，base `https://api.maozierp.com/`，Header：`Authorization: Bearer {token}`、`Client: plugin`、`Plugin-Version: 3.2.4`）

| 路径 | 方法 | 用途 |
|---|---|---|
| `api.chrome/check_login` | — | 登录态校验（返回 is_vip） |
| `api.chrome/collect` | POST | 货源采集数据上报（collect_from: 1688/taobao/tmall/pdd/jd/aliexpress/amz/wb） |
| `api.chrome/check_data` | POST | **加密上报** Ozon 商品数据（sku/type/lang/encrypted_data/iv/signature/timestamp/nonce/original_size/compressed_size/compression_ratio/processing_time） |
| `api.chrome/wb_sales` | POST | WB 月销数据（`{products}`） |
| `api.chrome/check_update` | POST | 版本检查（`{version}` → can_update/download_url） |
| `api.selection.follow/import` | POST | **OZON 一键上架提交**（3.6 payload） |
| `api.selection.follow/edit` | POST | OZON 采集/编辑上架（跳 ERP AI 编辑页） |
| `api.selection.plugin/rule_list` / `add_rule` / `delete_rule` / `toggle_rule` | — | 选品规则 CRUD |
| `api.shop/lists` | POST | OZON 店铺列表（{page,page_size}） |
| `api.shop_group/simple_lists` | GET | 店铺分组列表 |
| `api.shop/set_cookies` | POST | 上报 seller.ozon.ru Cookie（`{cookies:JSON}`） |
| `api.shop/sync_warehouse` | POST | 同步店铺仓库（`{ids:[shopId]}`） |
| `api.watermark/templates` | POST | 水印模板列表 |
| `api.exchange_rate/index` | POST | 汇率（`{currency_from:RUB/CNY}`） |
| `api.product.favorite/toggle` | POST | 收藏/取消收藏（`{productInfo,status}`） |
| `api.product.favorite/skus` | GET | 已收藏 SKU 列表 |
| `api.source.ali1688/check_valid_account` | GET | 1688 货源账号授权检测 |
| `api.source.ali1688/collect` | POST | 1688 找同款批量采集（并发3） |
| `api.wb.collect/publish_direct` | POST | **WB 一键上架提交** |
| `api.wb.collect/direct_to_draft` | POST | WB 采集→草稿（跳 AI 编辑） |
| `api.wb.shop/simple_lists` | POST | WB 店铺列表 |
| `api.tool/get_profit_calc_config` | GET | 利润计算配置（物流线路/规则） |
| `api.user/check_vip_status?platform=ozon` | GET | 天眼 VIP 校验 |

### 8.2 跨 Tab 获取 Ozon Seller 数据（核心链路）

插件在 Ozon 前台页不能直连 seller 后台 API（CORS/登录态），于是：

1. `background` 找到已打开的 `seller.ozon.ru` tab（`CHECK_SELLER_TAB` / `TEST_SELLER_TAB_COMMUNICATION` / `REFRESH_SELLER_TAB`）；
2. 前台 content script 发 `CROSS_TAB_OZON_REQUEST` → background 转发 `OZON_SKU_API_REQUEST` 到 seller tab；
3. seller tab 上的 content script（`P6`）带登录态 fetch seller API，取 `sc_company_id` Cookie 作为 `x-o3-company-id`：
   - **sales**：`POST https://seller.ozon.ru/api/site/seller-analytics/what_to_sell/data/v3`，body `{limit:50, offset:0, filter:{stock:"any_stock", period:"monthly", categories:[], sku}, sort:{key:"sum_gmv_desc"}}`，`x-o3-language: zh-Hans`；
   - **variant**：`POST https://seller.ozon.ru/api/v1/search-variant-model`（{name, limit:50}）；
   - **variant_v2**：`POST https://seller.ozon.ru/api/site/seller-prototype/create-bundle-by-variant-id`（{company_id, variant_id, source:"SOURCE_UI_COPY_MERGED"}）；
   - **search-sku-base**：`POST https://seller.ozon.ru/api/v1/search`（{company_id, need_total, filter:{children_nodes…sku…}, pagination, is_copy_allowed:false}）。
4. 结果回传前台 → 经 `api.chrome/check_data` **加密上报**后台。
5. 错误处理：「请先打开 seller.ozon.ru 页面」「与seller.ozon.ru Tab通信失败」「获取Ozon公司ID失败，请检查seller.ozon.ru登录状态」。

### 8.3 数据加密上报

- AES-CBC：密钥由 `{p1:"mz_sec",p2:"_k3y_",p3:"2024",p4:"_v7x9"}` 拼接后 padEnd 32 字节，随机 16 字节 IV，先 gzip 压缩再加密，base64；
- HMAC-SHA256 签名：密钥 `{s1:"hmac",s2:"_s1gn",s3:"_k3y",s4:"_ultra"}` 拼接，签名做异或+移位混淆后 base64url；
- 上报字段含压缩比/耗时，用于质量监控。

### 8.4 background 能力（消息协议）

| 消息 | 行为 |
|---|---|
| `API_REQUEST` | 后端代理 fetch（统一 Bearer/Client/Plugin-Version 头） |
| `GET_TARGET_STORAGE` | 读指定 tab 的 localStorage（登录取 accessToken） |
| `GET_COOKIES` | 读指定 URL 的 Cookie（含 partitioned cookie 兜底；`abt_data` 校验） |
| `UPDATE_LOGIN_STATUS` | 登录态全站广播 |
| `CHECK_SELLER_TAB` / `TEST_SELLER_TAB_COMMUNICATION` / `REFRESH_SELLER_TAB` | seller.ozon.ru 通信探测/刷新 |
| `CROSS_TAB_OZON_REQUEST` | 跨 Tab 转发 SKU 数据请求 |
| `OPEN_NEW_TAB` | 开新页 |

**declarativeNetRequest 动态规则 id=9001**：对 `^https?://([^/]*\.)?ozon\.(ru|kz|by)/` 主文档移除 `content-security-policy` 响应头（解除前台页 CSP，便于 DOM 采集/内联执行）。

### 8.5 Ozon「天眼」Premium（OzonSellerAnalytics + inject.js）

- 仅 `seller.ozon.ru/app/analytics/graphs` 生效，页面插入 `mz-ozon-analytics-graphs-toolbar-slot` + 「开启天眼」按钮；
- 点按：先校验 VIP（`/api.user/check_vip_status?platform=ozon`，非 VIP 提示「此功能为毛子ERP用户vip功能，请先开通」）→ inject.js 在页面上下文访问 Ozon Vue `$store.state.analytics.premium`，把 `isPremiumSeller/isPremiumLiteSeller` 置 true、`subscription="PREMIUM"`、`periodEnd=明天`，再 dispatch `analytics/graphs/graphsV3/fetchTable` 刷新 → 前端解锁天眼看板（纯前端会话级解锁，非后端开通）。

---

## 9. 对复刻「采集→上架」链路的参考要点

1. **一键上架 UI 范式**：左侧店铺面板（多店勾选+币种标识+仓库/库存）＋右侧「表单(品牌/图片顺序/上架方式/水印/合并变体/浮动价/货源三件套) + 可批量编辑的 SKU 表格」是核心交互骨架；顶部明确告知「多店铺须币种一致」。
2. **价格批量心智**：售价/划线价均支持「原售价倍数 / 固定金额」两种批量模式，货币前缀实时联动。
3. **货号生成规则**：系统默认/自定义前缀/源SKU+随机数/自定义前缀+源SKU 四种，覆盖不同卖家习惯。
4. **选品规则**：22+ 项条件覆盖销量/价格/评分/促销/流量/转化/跟卖竞争全维度，命中即卡片加标签+收藏+背景色，优先级决定背景色覆盖。
5. **跨 Tab 架构**：前台页数据靠后台已登录的 seller tab 转发官方 API 获取（what_to_sell v3），而非硬解析前端 DOM——这是数据质量的关键。
6. **采集→AI 上品闭环**：插件只做「采集+提交」，AI 编辑（套图/翻译）全部在 ERP Web 端 `#/product/ai/edit` 承接，插件以 `jump_id` 跳转衔接。
