# PLAN-cross-platform-sourcing-v1 — 货源平台扩展：淘宝/天猫 + 拼多多（v1）

> 2026-09-10。用户指令：「货源方面新增淘宝和pdd」。基于四插件参考（goldminer v2.43 /
> maozi 3.2.6 / ozonAI V2.3.3 / 上品帮逆向）+ 全仓 1688 耦合面调研（file:line 证据见
> §2，调研子代理产出）。SDD 执行，分支 `feat/cross-platform-sourcing`。

## 0. 一句话

`graph` 直传链从「仅 1688」扩为「1688 / 淘宝 / 天猫 / 拼多多」四平台：skill 侧新增
平台适配器（URL 识别 + CDP/mtop 抓取 → 统一 ProductInfo → 复用现有信封组装），
worker 侧最小兼容层（图片 CDN 白名单 / Referer / offer_id 解析），契约登记
`source.platform`；**taobao/pdd 信封不参与 L0 类目学习写侧**（防 cid 数字空间污染）。

## 1. 范围与不做

**做**：
- `graph --url` 与 `batch_test` URL 列表识别淘宝(item.taobao.com) / 天猫(detail.tmall.com) /
  拼多多(yangkeduo.com|pinduoduo.com /goods|goods1|goods2) 四平台（1688 现状不动）。
- 每平台一个抓取适配器，产出统一 ProductInfo（title/price/images/freight/weight/dims/
  attributes/supplier/item_id），复用 `_collapse_variants_to_single` 与信封组装。
- worker 最小兼容层（§4 批1）。
- 实机 gate：四平台各 ≥1 单走本地 Docker 全链路。

**不做（明确 defer）**：
- 图搜匹配扩展到淘宝/pdd（Ozon 竞品图 → 淘宝搜同款）——另立批8 ⑤跨平台货源地图。
- Taobao/pdd 参与类目学习/L0（本期省略 cid 字段，worker 学习链零改动）。
- AliExpress / WB / TEMU / JD（上品帮枚举里的其余平台）。
- worker 侧按 platform 分流的业务逻辑（信封透传即可，零强制消费）。

## 2. 现状取证（1688 耦合面，改动权威清单）

### skill 侧
| 触点 | 位置 | 现状 |
|---|---|---|
| graph URL→item_id | `cli.py:377-388` | `/(\d+)\.html` + detail.1688.com 兜底拼接 |
| batch URL 分派 | `batch_test.py:144-171` | `"1688.com" in line` → `offer/(\d+)` → type="1688" |
| URL 解析器 | `ak_1688_client.py:931-960` | platform 恒 "1688" |
| CDP 探针硬门 | `capabilities/browser_probe/service.py:2489-2490` | `if '1688.com' not in target_url: raise`（第一闸） |
| 1688 抓取 | `service.py:61-857 EXTRACT_1688_JS` | 三路：`__INIT_DATA__` / React fiber / DOM |
| freightCny | `service.py:761-793` | 购物车 DOM 文本正则 |
| 变体折叠 | `cloud_probe.py:918-983` | 平台无关（吃 variants + cost + shipping）✅ 可复用 |
| 信封组装 | `cloud_probe.py:1748/2362-2418` | item_id/purchase_url/purchase_cost 平铺 draft |
| 登录检测 | `service.py:1199` | 登录页判定已含 login.taobao.com（淘宝系账号同族）✅ |
| cookie 红线 | `lib/cookie_harvest.py:26` | 明确不搬 taobao.com 邻域 cookie（维持不动） |

### worker 侧（purchase_url 换平台会坏/退化的清单）
| # | 触点 | 位置 | 影响 |
|---|---|---|---|
| A1 | 图片白名单 | `utils/image_url_guard.py:34-40` | 仅 alicdn/1688 子串——**pddpic/yangkeduo 全拒**；taobaocdn 未列 |
| A2 | 生图参考过滤 | white_bg/multi_angle/main_image 三 node + variant_primary_loop | A1 拒 → pdd 货源生图退化为纯文本提示词 |
| A3 | E1 原图转存 | `utils/cos_uploader.py:110-153` | 白名单拒 → 0 图卡 → validate 全外链硬拦无解（ozon_validate_node.py:608） |
| A4 | 下载 Referer | `utils/image_url_processor.py:20-40` | 仅 1688/alicdn 补 referer；draft_image_mirror 裸 UA（pdd 热链可能 403） |
| B1 | cid 空间冲突 | `utils/category_mapping_learn.py:20-41` + learning_record_node.py:400-693 | taobao/pdd 类目 id 塞 source_category_id 会与 1688 AK cid 撞同一学习表 |
| C1 | offer_id 解析 | `services/source_candidate_service.py:44-47` | 只认 detail.1688.com → 新平台退化为整条 URL 作唯一键（可接受，但最好补） |
| C2 | 契约/注释 | state.py:152,199-203 | item_id 注释「1688商品ID」；EnvelopeSourceCategory 语义=1688 cid |

**定价链/校验链平台无关（不坏）**：pricing_node cost_cny、_validate_draft_required_fields
（只查 URL 类型非空）、R4 重配吃中文 source_category 词（淘宝中文可复用；pdd 缺类目路径则
R4 退化为无源词搜索兜底——可接受）。

### 参考实现（直接移植）
| 平台 | 方案 | 出处 |
|---|---|---|
| 淘宝/天猫 | mtop `mtop.taobao.pcdetail.data.get/1.0/`（appKey=12574478，h5api.m.taobao.com，`_m_h5_tk` cookie 签名——与现有 aibuy 1688 mtop 同机制，`ozon_image_search.py:42,519-666` 可参照）+ `mtop.taobao.detail.getdesc/7.0/` 详情描述；ozonAI 走页面 React VO（headImageVO/componentsVO + `window.__*` + featureAttributes） | goldminer background.js:14017；docs/competitor/maozier-plugin-full.md:193-203 |
| 拼多多 | 页面嵌入 `window.rawData.store.initDataObj.goods` / `rawData.goods`；兜底 `GET {origin}/proxy/api/api/oak/integration/render/sku?pdduid={cookie pdd_user_id}` | goldminer background.js:13725-13747；maozi inject.js |
| 架构模式 | 每平台一个适配器 → 统一 collect 契约 | ozonAI 按平台拆 content-script（taobao-main.js / pdd-main.js） |

## 3. 全局约束（红线，全程有效）

- **Tier A**：本分支 + PR；测试命令照旧（worker 全量需 PGDATABASE_URL 指 5433；skill
  用 `.venv314`；worktree 内用主仓绝对路径 `/Volumes/os/dev/ozon-worker/skill/.venv314/bin/python`）。
- **功能测试只打本地 Docker**（http://localhost:8080），禁生产 worker.mxou.cn。
- **cookie 明文绝不落日志/响应/报告/DB**；登录态用用户自己 Chrome 会话（CDP），不造新凭证链路；
  `cookie_harvest.py` 的 taobao 排除红线不动（本方案不需要跨域搬 cookie）。
- **信封边界**：skill 不调任何 Ozon 上架 API；worker 不抓 1688/淘宝/pdd。
- 测试选品避开危险品/敏感类目（驱蚊/鼠药/香烟/医疗/成人）。
- 逐文件 `git add`；主仓工作树有他 session WIP，勿动 /Volumes/os/dev/ozon-worker 的未提交文件。
- 改 langgraph 相关节点时：节点要读的字段必须声明进该节点 Input model（本期 worker 侧无节点改动，
  预期不触发；若实现中发现需要，先停下来升级为计划变更）。
- **compile.py 三清单**：新 lib 文件归 COPY_FILES（明文，参照 cloud_probe 先例——适配器改动
  频繁且含长 JS，明文跨平台一致）+ 同步跑 `test_compile_lists.py`。

## 4. 批次（TDD，SDD 逐批 implement→review→fix）

### 批1 worker 最小兼容层（先行，可独立合并）
1. `image_url_guard.py`：白名单 + `pddpic.com`/`yangkeduo.com`/`pinduoduo.com`/`taobaocdn.com`
   子串；`_THUMBNAIL_PATTERN` 补淘宝/pdd 缩略后缀形态（`_60x60`、`.jpg_400x400` 类）——保持
   现有「缩略恒拒」语义，测试锁旧行为不回归。
2. `image_url_processor.py`：Referer 分派扩展——taobao/pdd 域给对应 Referer（taobao 详情页 /
   pdd goods 页）；下载失败降级路径不变。
3. `source_candidate_service._offer_id_from_url`：+ 淘宝 `id=(\d+)`、pdd `goods[_\d]*=(\d+)` 模式。
4. 契约登记：CONTRACT-v4 §1（draft 层加可选 `source.platform`（"1688"|"taobao"|"tmall"|"pdd"），
   worker 零强制消费，缺失=按 URL 域名推断）；API-OVERVIEW 变更记录一行。
5. 测试：`tests/test_image_guard_multi_platform_v1.py`（guard 白名单/缩略/降级）+
   `tests/test_offer_id_multi_platform.py` + referer 分派用例。全部纯 mock。

### 批2 skill 平台路由 + 淘宝/天猫适配器
1. 新 `skill/scripts/lib/source_platforms.py`：`parse_platform_url(url) -> PlatformTarget(platform,
   item_id, canonical_url) | None`——四平台识别（1688 现有三种模式并入）；纯函数优先 TDD。
2. `cli.py cmd_graph` + `batch_test.parse_urls_file`：接入 parse_platform_url（type 扩
   "taobao"/"tmall"/"pdd"；`--type-filter` choices 同步）；1688 路径逐字节保持。
3. 新 `skill/scripts/lib/taobao_client.py`：
   - `fetch_product(cdp_url, url, target) -> ProductInfo`：CDP 开 item 页 → 页内注入 fetch mtop
     `mtop.taobao.pcdetail.data.get`（页上下文自带 `_m_h5_tk` 签名与登录 cookie，免手签）→
     归一 ProductInfo{title, price_range, images(主图+SKU图), sku_props, freight(包邮→0/地区件),
     weight_kg?, props(featureAttributes)}；超时/风控页（滑块/验证码选择器复用 service.py:804-810）
     → 人话报错 + 可选等待登录（复用 skill-login-wait 分级 UX 先例）。
   - 天猫 URL 同适配器（detail.tmall.com 同 mtop 协议）。
4. `browser_probe/service.py:2489` 硬门：改为平台白名单（1688/taobao/tmall 域放行，各自探针
   分派；pdd 在批3）。
5. 登录态：`_check_login_live` 按平台分派（taobao 用 login.taobao.com 判定，service.py:1199 现成）。
6. 测试：parse_platform_url 全矩阵 + taobao mtop 归一（mock CDP evaluate 返回录制 JSON 夹具）+
   风控/未登录分支。夹具用脱敏录制或手工构造（绝不提交真实 cookie）。

### 批3 拼多多适配器
1. 新 `skill/scripts/lib/pdd_client.py`：`fetch_product` 读 `window.rawData`（store.initDataObj.goods
   / rawData.goods 双形态）→ ProductInfo；rawData 缺失 → render/sku 兜底 fetch（pdduid 从 cookie）；
   风控/强制 App 跳转 → 明确报错「请在工具 Chrome 登录拼多多后重试」。
2. service.py 硬门/登录分派补 pdd 域。
3. ⚠️ 风险标注（写进模块 docstring）：pdd web 反爬迭代频繁，rawData 结构随版本漂移——本适配器
   以「失败出声、绝不半猜」为纪律（抓不到就报错，不编造字段）。
4. 测试：同批2 模式（录制夹具 mock）。

### 批4 信封组装接线
1. `cloud_probe.build_graph_envelope`：按 parse_platform_url 分派——1688 走原链逐字节不变；
   taobao/tmall/pdd → adapter.fetch_product → 复用 `_collapse_variants_to_single`（平台 SKU 结构
   归一到现有 variants 形状）→ `_validate_and_fix_product_data` → draft 组装
   （item_id=平台 item_id、purchase_url=canonical_url、purchase_cost=代表变体价+freight）。
2. `build_envelope_from_discovery` 不动（discover 仍 1688 匹配）。
3. **信封纪律**：taobao/tmall/pdd 信封**省略** `source_category_id`/`source.category_id`/
   `source_category_path` 之外的所有 cid 类键，且 `category_mapping` 学习写侧自然跳过（无 cid 即
   不写）——学习表零污染；`draft.ozon_category` 缺省走 worker 文本+LLM 链。
4. `source.platform` 填入（批1 契约）；`extensions.discovery_meta` 不适用直传流，不填。
5. weight/dims 缺失：走现有 DEFAULT_WEIGHT_G=500 / 尺寸 2:1.5:1 兜底 + worker volume_weight_guard
   兜底（已有，不重复造）。
6. 测试：分派矩阵 + 折叠复用 + 信封键纪律（锁「新平台信封绝无 cid 键」）+ 1688 回归（现有
   graph 测试全绿不改动）。
7. compile.py：两个新 lib 进 COPY_FILES + `test_compile_lists.py` 绿。

### 批5 实机 gate（本批最后，≥4 单本地 Docker 全链路）
- **gate 前置（批1 Minor #1 责任归属）**：把 `image_url_processor._referer_for_url` 接进两条活下载链
  （`cos_uploader` E1 转存下载 + `draft_image_mirror`）——批1 只交付了分派函数（`_download_image`
  无生产调用方）；首批 taobao/pdd 信封出现前不动 1688 生产链，批5 是唯一会出现新平台信封的批次，
  接线在本批完成并有 gate 单实证。pdd 若仍 403 → 走下方降级口径并如实记录。
- 真实淘宝 1 单 + 天猫 1 单 + 拼多多 1 单 + 1688 回归 1 单 → `--to-box` 或直提测试店；
  验收：信封组装字段全、四平台轮转零串图、本地 Docker 任务终态如实（completed 需真 approved）、
  学习表（category_mapping/category_match_log）零新平台行写入。
- 拼多多若遇平台升级风控：允许 gate 降级为「错误出声 + 入箱流程走通」，但必须如实记录在计划文末。

## 5. 验收（PR 合并门槛）

- worker 全量 + skill 全量绿；`gen_api_docs` 零漂移；ci.sh --quick 绿。
- 批1-4 每批 SDD review 通过；全局红线零违反（grep 日志无 cookie）。
- 批5 gate 记录追加到本文末尾（实机 Gate 结果记录节）。

## 实机 Gate 结果记录

（待批5 执行后回填）
