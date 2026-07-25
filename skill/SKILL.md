---
name: pounding-ozon-probe
version: 0.4.0
description: >
  Ozon 上架工具：1688 选品、Ozon 跟卖、以图搜款、批量上架、Ozon 自动选品。
  当用户要上架商品到 Ozon、从 1688 选品、跟卖竞品、跨境电商铺货、Ozon 蓝海选品时触发。
---

# pounding-ozon-probe

Skill 端工具集，负责**数据抓取 + 信封组装 + 提交云端 Worker**。

## ⚠️ 首次使用必须配置

在执行任何操作前，必须完成以下配置。缺少任何一项，Skill 会拒绝执行并提示配置方法。

**请严格按顺序执行，不要跳过任何步骤。**

### 第一步：安装依赖

```bash
pip3.12 install -r requirements.txt
```

### 第二步：获取并设置 MXOU_TOKEN

MXOU_TOKEN 是云端 AI 服务的认证密钥，所有操作都需要它。

1. 访问 https://api.mxou.cn 注册并获取 API Token
2. 设置到本地：

```bash
python3.12 scripts/cli.py set_token --token <你的MXOU_TOKEN>
```

如果已安装 pounding 桌面端，会自动从 ~/.pounding/config.json 读取，无需手动设置。

首次设置后，Skill 会自动向云端验证 token 有效性、余额和激活状态。验证结果缓存 24 小时，期间无需重复验证。

### 第三步：获取 1688 AK

1688 AK 用于商品搜索和数据获取。

```bash
# 方式 1：自动获取（需要 Chrome 浏览器，30 秒超时）
python3.12 scripts/cli.py get_ak

# 方式 2：手动获取（推荐）
# 浏览器打开 https://clawhub.1688.com → 登录 → 复制 AK
python3.12 scripts/cli.py set_ak --ak <你的AK>
```

AK 过期时会自动尝试重新获取。失败则提示手动操作。

### 第四步：配置 Ozon 店铺

从 Ozon 卖家后台获取凭证：设置 → API 密钥

```bash
# 配置店铺（支持多店铺）
python3.12 scripts/cli.py set_store --name "主店铺" --client-id <ID> --api-key <KEY>

# 查看已配置的店铺
python3.12 scripts/cli.py list_stores
```

### 第五步：验证配置

```bash
python3.12 scripts/cli.py check
```

全部 ✅ 后方可执行上架操作。

## 功能一览

| 功能 | 命令 | 说明 |
|------|------|------|
| 1688 选品上架 | `graph` | 1688 商品页 → 组装信封 → Worker 上架 |
| Ozon 跟卖 | `follow` | Ozon 竞品 → 图搜 1688 → Worker 跟卖 |
| 以图搜款 | `image_search` | CDP 网页图搜，支持 URL / 本地图片 |
| Ozon 选品 | `discover` | Ozon 中国站/搜索/类目页自动选品，蓝海评分，1688匹配，利润计算 |
| 环境检查 | `check` | 检测 Chrome、CDP、登录态、凭证、Worker 连通性 |
| 批量处理 | `batch_test` | URL 列表批量识别 1688/Ozon 并处理 |

## Skill 能力矩阵

| 能力 | 说明 | 依赖 |
|------|------|------|
| CDP 浏览器抓取 | 通过本地 Chrome CDP 抓取 1688 / Ozon 页面 | Chrome 浏览器 |
| 1688 AK API | 结构化搜索 1688 商品（标题、价格、供应商、图片） | 1688 AK |
| 1688 图搜 | 以图搜款，从 Ozon / 本地图片反查 1688 同款 | 1688 AK + CDP |
| Ozon Widget API (CDP) | 获取标题、价格、图片、跟卖人数（无需凭证） | Chrome CDP |
| Ozon Seller API | 获取佣金率、重量、尺寸、品牌、类目 | `--client-id` / `--api-key` |
| Ozon Premium Analytics | 获取月销量、转化率（CDP 欺骗方式） | 需登录 seller.ozon.ru |
| 蓝海评分 | 综合评分 0-100，衡量产品竞争力 | discover 命令内置 |
| 1688 匹配 + 利润计算 | 自动匹配 1688 采购价，计算利润率 | 1688 AK |
| Worker 提交 | 将信封提交云端 Worker 执行上架全流程 | MXOU_TOKEN |

## 环境要求

- **Python 3.12**（必须，二进制模块编译版本）
- **Google Chrome**（必须，CDP 浏览器抓取依赖）

## 多店铺管理

支持同时管理多个 Ozon 店铺，每个店铺独立的 client_id + api_key。

```bash
# 配置多个店铺
python3.12 scripts/cli.py set_store --name "主店铺" --client-id 5371047 --api-key xxx
python3.12 scripts/cli.py set_store --name "测试店铺" --client-id 1234567 --api-key yyy

# 查看所有店铺
python3.12 scripts/cli.py list_stores

# 使用指定店铺上架
python3.12 scripts/cli.py graph --url <1688 URL> --store "主店铺"
python3.12 scripts/cli.py follow --ozon-url <Ozon URL> --store "测试店铺"
```

不指定 `--store` 时使用默认店铺（第一个配置的店铺）。

## 命令详解

### check — 环境检查

```bash
python3.12 scripts/cli.py check
```

自动检测：Chrome 安装、CDP 启动、1688 登录态、Ozon DataDome、凭证状态、Worker 连通性、Ozon API 认证。

### graph — 1688 选品上架

```bash
python3.12 scripts/cli.py graph \
  --url "https://detail.1688.com/offer/xxx.html" \
  --store "主店铺" \
  --category-query "俄语类目词"
```

流程：CDP 抓取 1688 → 组装信封 → 提交云端 Worker 全流程上架。

| 参数 | 说明 |
|------|------|
| `--url` | 1688 商品详情页 URL |
| `--store` | Ozon 店铺名称（可选） |
| `--category-query` | Ozon 类目关键词（俄语，可选） |
| `--retries` | CDP 重试次数（默认 3） |

### follow — Ozon 跟卖

```bash
python3.12 scripts/cli.py follow \
  --ozon-url "https://www.ozon.ru/product/xxx/" \
  --store "主店铺" \
  --auto-submit
```

流程：CDP 抓取 Ozon 竞品 → 图搜 1688 同款 → 组装信封 → 提交云端 Worker 跟卖管线。

| 参数 | 说明 |
|------|------|
| `--ozon-url` | Ozon 商品页 URL |
| `--store` | Ozon 店铺名称（可选） |
| `--auto-submit` | 自动提交到 Worker |

### discover — Ozon 自动选品

从 Ozon 中国站、搜索结果、类目页等自动发现蓝海产品。

```bash
# 中国站选品（默认，50 个产品）
python3.12 scripts/cli.py discover

# 关键词搜索选品
python3.12 scripts/cli.py discover --keyword "宠物用品" --max-products 100

# 任意 Ozon 页面选品
python3.12 scripts/cli.py discover --url "https://www.ozon.ru/category/..." --max-products 200

# 带 Seller API 凭证（获取佣金率+重量/尺寸）
python3.12 scripts/cli.py discover --keyword "..." --client-id 5371047 --api-key xxx

# 导出 CSV/JSON
python3.12 scripts/cli.py discover --keyword "..." --export csv --output results.csv
python3.12 scripts/cli.py discover --keyword "..." --export both --output results

# 用户确认后自动提交到 Worker
python3.12 scripts/cli.py discover --keyword "..." --auto-submit
```

**参数说明：**

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--url` | Ozon 页面 URL（搜索/类目/品牌/促销页） | 中国站 highlight |
| `--keyword` | 搜索关键词（自动构造搜索 URL） | — |
| `--max-products` | 最多分析产品数 | 50 |
| `--min-margin` | 最低利润率% | 15 |
| `--max-sellers` | 最大跟卖人数 | 10 |
| `--fx-rate` | RUB→CNY 汇率 | 0.075 |
| `--client-id` | Ozon Client ID（获取佣金+重量） | — |
| `--api-key` | Ozon API Key | — |
| `--export` | 导出格式：csv/json/both | — |
| `--output` | 导出文件路径 | auto |
| `--auto-submit` | 用户确认后自动提交到 Worker | false |

**蓝海评分算法（0-100分）：**

| 因子 | 权重 | 评分标准 |
|------|------|----------|
| 跟卖人数 | 30% | <5=100, <10=90, <50=60, <200=30, >200=10 |
| 利润率 | 30% | >40%=100, >30%=85, >20%=70, >10%=40, <10%=15 |
| 月销量 | 20% | 1-50=80(蓝海), 50-200=60, 200-1000=40, >1000=20 |
| 价格区间 | 10% | 500-5000₽=100, 100-500₽=70 |
| 佣金率 | 10% | <10%=100, <15%=70, <20%=40 |

**数据来源：**

| 数据 | 来源 | 需要凭证？ |
|------|------|-----------|
| 标题/价格/图片/跟卖人数 | Ozon Widget API（CDP） | 否 |
| 佣金率/重量/尺寸/品牌/类目 | Ozon Seller API | 是（--client-id/--api-key） |
| 月销量/转化率 | Ozon Premium Analytics（CDP 欺骗） | 是（需登录 seller.ozon.ru） |
| 1688 采购价/供应商 | 1688 AK API + CDP 图搜 | 是（1688 AK） |

**输出格式：**

CSV 导出包含 18 列：
`product_id, title, price_rub, category, brand, commission_fbp, commission_rfbs, monthly_sales, monthly_revenue, competing_sellers, min_competitor_price, weight_g, dimensions, match_1688_url, match_1688_price, profit_margin, blue_ocean_score, verdict`

### image_search — 以图搜款

```bash
python3.12 scripts/cli.py image_search --image "https://example.com/image.jpg"
python3.12 scripts/cli.py image_search --image "/path/to/local/image.jpg"
```

CDP 网页版图搜（准确率 ~100%），支持 URL 和本地图片。

### get_ak — 获取 1688 AK

```bash
python3.12 scripts/cli.py get_ak
```

通过 Chrome 浏览器自动获取 1688 AK，保存到本地配置。30 秒超时，失败则提示手动获取。

### batch_test — 批量处理

```bash
python3.12 scripts/batch_test.py \
  --urls-file urls.txt \
  --submit
```

URL 文件自动识别 1688 / Ozon，混合输入即可。

| 参数 | 说明 |
|------|------|
| `--urls-file` | URL 列表文件 |
| `--submit` | 实际提交（不加则 dry-run） |
| `--start` | 起始索引 |
| `--limit` | 处理数量 |
| `--delay` | 每个 URL 间隔秒数（默认 3） |

## 两条管线

### 管线 A：1688 选品

```
1688 URL → CDP 抓取 → 组装信封 → 云端 Worker 全流程上架
```

### 管线 B：Ozon 跟卖

```
Ozon URL → CDP 抓取 → 图搜 1688 → 组装信封 → 云端 Worker 跟卖管线
```

### 管线 C：Ozon 自动选品（discover）

```
Ozon 页面 → CDP 抓取产品列表 → 蓝海评分 → 1688 匹配+利润计算 → 用户确认 → Worker 提交
```

### 数据采集架构说明

完整的数据管线同时使用 **1688 AK API** 和 **Chrome CDP** 两种方式，而非仅依赖 CDP：

- **1688 AK API**：提供结构化商品数据（标题、价格、供应商、SKU、图片 URL），速度快、数据规范，是搜索和批量查询的主力通道。
- **Chrome CDP**：用于浏览器端操作——图搜（以图搜款）、登录态维持、Ozon DataDome 绕过、页面动态内容抓取。CDP 是 AK API 无法覆盖的场景的补充通道。
- **Ozon Seller API**：通过 REST 接口获取佣金率、重量、尺寸等结构化数据，需要 `--client-id` / `--api-key` 凭证。

三条通道互补：AK API 负责 1688 侧结构化数据，Seller API 负责 Ozon 侧结构化数据，CDP 负责浏览器端交互和非结构化页面抓取。

信封结构参考：`envelope_example.json`
字段映射参考：`field_mapping.md`

## 凭证存储

所有凭证存储在 `data/config/` 目录（Skill 自有配置）：

| 文件 | 内容 |
|------|------|
| `stores.json` | Ozon 多店铺凭证（set_store 管理） |
| `settings.json` | MXOU_TOKEN + 1688 AK（set_token/set_ak 管理） |
| `auth_cache.json` | 云端鉴权缓存（自动管理，24 小时有效） |

## 鉴权机制

所有核心操作（搜索、抓取、提交）在执行前会自动向云端 Worker 验证凭证。

- 验证内容：token 有效性 + 账户余额 + 激活状态
- 验证结果缓存 24 小时，期间不重复请求云端
- 缓存过期后自动重新验证
- 验证失败（余额不足/未激活）会拒绝执行并提示原因

## 已知限制

- Chrome 必须运行（CDP 依赖本地浏览器）
- 首次使用需手动登录 1688 和访问 Ozon（建立登录态 / DataDome 信任）
- 以图搜款准确率 ~100%，但部分小众品类可能匹配不到
- Ozon 不提供重量 / 尺寸，从 1688 获取
- Worker 地址已内置（https://worker.mxou.cn），无需配置
