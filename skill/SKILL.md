---
name: pounding-ozon-probe
description: >
  Ozon 上架工具集：1688 选品、Ozon 跟卖、以图搜款、批量上架。
  当用户要上架商品到 Ozon、从 1688 选品、跟卖竞品、跨境电商铺货时触发。
---

# pounding-ozon-probe

Skill 端工具集，负责**数据抓取 + 信封组装**。Worker 端负责**判断流程 + 执行上架**。

## 核心能力

| 能力 | 命令 | 说明 |
|------|------|------|
| 环境检查 | `check` | 自动启动 Chrome、检测登录态、验证凭证 |
| 1688 选品 | `graph` | 抓取 1688 商品 → 组装信封 → 提交 Worker |
| Ozon 跟卖 | `follow` | Ozon 竞品图搜 1688 同款 → 组装信封 → 提交 Worker |
| 以图搜款 | `image_search` | CDP 网页版图搜（比 API 更准确） |
| 获取 AK | `get_ak` | 浏览器自动获取 1688 AK |
| 批量测试 | `batch_test` | 批量处理 URL 列表 |

## 快速开始

```bash
cd skill/
pip install -r requirements.txt

# 1. 检查环境（自动启动 Chrome）
python3 scripts/cli.py check

# 2. 1688 选品上架
python3 scripts/cli.py graph --url "https://detail.1688.com/offer/xxx.html"

# 3. Ozon 跟卖
python3 scripts/cli.py follow --ozon-url "https://www.ozon.ru/product/xxx/"

# 4. 批量处理
python3 scripts/batch_test.py --urls-file urls.txt --client-id 5371047 --api-key xxx --submit
```

## Chrome 浏览器管理

**用户零配置**：Skill 自动启动 Chrome 并保留登录态。

- 自动检测系统（macOS/Windows/Linux）
- 使用用户默认 Chrome profile（保留 1688/Ozon 登录态）
- 自动添加 `--remote-allow-origins=*`（CDP WebSocket 需要）
- 首次使用需在 Chrome 中登录 1688 和访问 Ozon

```bash
# 检查环境（自动启动 Chrome + 检测登录态）
python3 scripts/cli.py check
```

## 命令详解

### check — 环境检查

```bash
python3 scripts/cli.py check
```

自动检测并修复：
- Chrome 浏览器是否安装
- CDP 远程调试是否启动（自动启动）
- 1688 登录态
- Ozon DataDome 信任
- Worker 连接
- Ozon API 凭证

### graph — 1688 选品上架

```bash
python3 scripts/cli.py graph \
  --url "https://detail.1688.com/offer/xxx.html" \
  --category-query "俄语类目词"
```

**流程**：
1. CDP 抓取 1688 商品详情（标题、图片、价格、属性、重量、尺寸）
2. 组装 GraphInput 信封
3. 提交 Worker 全流程上架

**返回值**：

```json
{
  "summary": { "item_id": "...", "title": "...", "purchase_cost": 5.5, "images": 10 },
  "envelope": { "token": "...", "ozon_client_id": "...", "envelope": { "draft": {...}, "source": {...} } }
```

### follow — Ozon 跟卖

```bash
python3 scripts/cli.py follow \
  --ozon-url "https://www.ozon.ru/product/xxx/"
```

**流程**：
1. CDP 抓取 Ozon 竞品页（标题、图片、属性、类目、hashtags）
2. CDP 网页版以图搜款（1688 同款）
3. CDP 抓取 1688 商品详情
4. 组装 GraphInput 信封（`extensions.follow_sell=true`）
5. 提交 Worker 跟卖管线

**返回值**：

```json
{
  "success": true,
  "product_id": "1925631822",
  "title": "Пенообразователь 2 Литра...",
  "best_match": { "id": "...", "title": "...", "price": 9.8 },
  "search_method": "cdp"
}
```

### image_search — 以图搜款

```bash
python3 scripts/cli.py image_search --image "https://example.com/image.jpg"
python3 scripts/cli.py image_search --image "/path/to/local/image.jpg"
```

**CDP 网页版图搜**（推荐）：
- 准确率 ~100%（API 只有 ~80%）
- 使用 1688 网页搜索引擎（比 API 更准）
- 支持 YOLO crop region 框选主体
- 自动提取：标题、价格、销量、供应商、徽章、图片

**返回值**：

```json
{
  "success": true,
  "products": [
    { "id": "732574780546", "title": "...", "price": 20, "badge": "符合2/3个条件", "sold": "9400+件", "supplier": "山东群安消防" }
  ]
}
```

### get_ak — 获取 1688 AK

```bash
python3 scripts/cli.py get_ak
```

浏览器自动获取 1688 AK，保存到 `.env` 和 `.ak_store.json`。

### batch_test — 批量处理

```bash
python3 scripts/batch_test.py \
  --urls-file urls.txt \
  --client-id 5371047 \
  --api-key "xxx" \
  --submit
```

**URL 文件格式**（自动识别 1688/Ozon）：
```
https://detail.1688.com/offer/xxx.html
https://www.ozon.ru/product/xxx/
```

**参数**：

| 参数 | 说明 |
|------|------|
| `--urls-file` | URL 列表文件 |
| `--client-id` | Ozon Client ID |
| `--api-key` | Ozon API Key |
| `--submit` | 实际提交（不加则 dry-run） |
| `--start` | 起始索引 |
| `--limit` | 处理数量 |
| `--delay` | 每个 URL 间隔秒数（默认 3） |

## 数据抓取完整性

### Ozon 抓取（CDP + API）

| 字段 | 来源 | 用途 |
|------|------|------|
| `title` | JSON-LD | 俄语标题 |
| `images[]` | HTML+JSON-LD | 竞品图片（60-80张） |
| `price` | JSON-LD | 参考价格 |
| `currency` | JSON-LD | RUB |
| `category` | API breadcrumbs | 类目路径 |
| `breadcrumbs[]` | API | [{text, link, category_id}] |
| `attributes{}` | API widget | {类型, 颜色, 材质, 原产国} |
| `hashtags[]` | DOM | ["#тег1", "#тег2"] |
| `description` | JSON-LD | 产品描述（容量/尺寸/材质） |
| `sku` | JSON-LD | 产品 ID |

### 1688 抓取（CDP）

| 字段 | 来源 | 用途 |
|------|------|------|
| `title` | 页面 | 中文标题 |
| `images[]` | 页面 | 商品图片 |
| `price` | 页面 | 采购价格 (CNY) |
| `weight` | 页面 | 重量 (克) |
| `dimensions` | 页面 | {length, width, height} (mm) |
| `attributes{}` | 页面 | 1688 属性 |
| `supplier` | 页面 | 供应商名 |
| `variants[]` | 页面 | SKU 变体 |

## 两条管线

### 管线 A：1688 选品

```
1688 URL → CDP 抓取 → 组装信封 → Worker 全流程上架
```

- 信封无 `follow_sell` 标记
- Worker 路由：ingest → category → pricing → assemble → upload

### 管线 B：Ozon 跟卖

```
Ozon URL → CDP 抓取 → 图搜 1688 → 组装信封 → Worker 跟卖管线
```

- 信封标记：`extensions.follow_sell=true`
- Worker 路由：follow_sell_import → assemble
- Skill 提供：Ozon 竞品图 + 1688 匹配商品

**Worker 判断逻辑**：收到信封后，Worker 尝试 import-by-sku，成功则跟卖，失败则走全流程。

## 信封结构

```json
{
  "token": "supabase_token",
  "ozon_client_id": "5371047",
  "ozon_api_key": "xxx",
  "envelope": {
    "draft": {
      "item_id": "1688商品ID",
      "title": "中文标题",
      "images": ["图片URL..."],
      "weight": 1000,
      "dimensions": {"length": 220, "width": 165, "height": 110},
      "purchase_cost": 25.0,
      "purchase_url": "https://detail.1688.com/offer/xxx.html",
      "attributes": {"材质": "不锈钢", "颜色": "黑色"},
      "ozon_product_id": "123456"
    },
    "source": {
      "purchase_url": "https://detail.1688.com/offer/xxx.html",
      "purchase_cost": 25.0
    },
    "extensions": {
      "follow_sell": true,
      "margin_rate": 0.25,
      "commission_rate": 0.10
    }
  }
}
```

## 环境变量

```bash
# .env
OZON_CLIENT_ID=5371047
OZON_API_KEY=xxx
MXOU_TOKEN=xxx
ALI_1688_AK=xxx
WORKER_URL=http://localhost:8080
```

## 已知限制

- Chrome 必须运行（CDP 依赖本地浏览器）
- 首次使用需手动登录 1688 和访问 Ozon（建立登录态/DataDome 信任）
- 以图搜款准确率 ~100%，但部分小众品类可能匹配不到
- Ozon 不提供重量/尺寸，从 1688 获取
