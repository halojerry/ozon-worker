# PRD: Ozon 跟卖（Follow-Sell）完整管线 v8

## 1. 背景

### 1.1 当前系统状态

ozon-worker 是一个两段式 Ozon 上架系统，包含：

- **Skill**（用户本地）：1688 CDP 抓取 → GraphInput 组装 → 提交 Worker
- **Worker**（云端 Docker）：LangGraph 管线 → 类目→定价→属性→生图→校验→上传→自学习

目前 Skill 已支持 1688 和 Ozon 两种 URL：

| URL 类型 | 入口 | 流程 |
|---------|------|------|
| 1688 URL | `probe` + `graph` | CDP 抓取 → 组装信封 → Worker 上架 |
| Ozon URL | `follow` | import-by-sku 复制卡片 → 1688 搜同款 → ? |

### 1.2 v7 遗留问题

Ozon 跟卖流程（v7 实现）存在以下根本性问题：

1. **import-by-sku 复制的产品卡数据完全错误**
   - 复制的是竞品的产品名、类目、属性，跟我们的 1688 货源无关
   - 28 个测试商品中，实际 Ozon 名称与期望类型完全不匹配
   - 例如：气溶胶灭火装置 → 复制出 "Аксессуар для электрощита"（配电柜配件）

2. **无法获取竞品图片**
   - import-by-sku 在 2025 年 3 月后，大部分卖家开启了「Запретить копирование」
   - 28 个商品中仅 2 个有图片（7%），其余 `images: []`

3. **没有图片就无法"有图收款"**
   - 跟卖的核心逻辑是：复制竞品卡片 → 拿到竞品图片 → 同款售卖
   - 缺少图片等于缺少最核心的竞争力

## 2. 问题分析

### 2.1 Ozon 反爬现状

Ozon 使用 DataDome 反爬系统，防御层次：

| 检测维度 | 说明 |
|---------|------|
| TLS 指纹 (JA3/JA4) | `requests`/`httpx` 默认指纹与 Chrome 不一致，触发拦截 |
| IP 信誉 | 非俄罗斯 IP 自动标红 |
| 浏览器指纹 | `navigator.webdriver`、Canvas、WebGL |
| 行为分析 | 滚动速度、鼠标轨迹、点击模式 |

**测试结果：**

- `curl_cffi`（模拟 Chrome TLS）：**403 + `ozon-antibot: 1`**（DataDome 拦截）
- CDP Chrome（真实浏览器 + 正常浏览历史）：**200 ✓**（成功抓取全部数据）
- `curl_cffi` + 俄罗斯代理：**未测试**（预计可通过）

### 2.2 架构决策

**Ozon Scraper 放在 Skill 端还是 Worker 端？**

| 方案 | 优势 | 劣势 |
|------|------|------|
| Skill 端（CDP Chrome） | 借用用户已有的 Chrome 会话，免代理费 | 用户需保持 Chrome 运行 |
| Worker 端（curl_cffi + 代理） | 对用户透明 | 需付费购买俄罗斯代理 |

**决策：Skill 端优先，Worker 端备用。**

用户已经在维护一个 CDP Chrome 用于 1688 抓取，Ozon 可以完全复用同一个 Chrome，零额外成本。
当用户本地的 CDP Chrome 不可用时，回退到 Worker API（需配置俄罗斯代理）。

## 3. 架构设计

### 3.1 整体架构

```
用户提供 URL
  │
  ├─ 是 1688 URL？
  │    └─ 1688 CDP 探针 ──→ 组装 GraphInput ──→ Worker 全新上架
  │
  └─ 是 Ozon URL？
       ├─ Step 1: CDP 打开 Ozon 页面 ──→ 提取 JSON-LD
       │         ┌─ 产品标题 (俄语)
       │         ├─ 产品主图 (ir.ozone.ru CDN)
       │         └─ 类目面包屑
       │
       ├─ Step 2: LLM 翻译标题 ──→ 中文 1688 搜索关键词
       │
       ├─ Step 3: 1688 AK 搜索同款
       │
       ├─ Step 4: import-by-sku 复制竞品卡片
       │         ┌─ 传入竞品主图 (images 参数)
       │         ├─ offer_id = follow_{product_id}
       │         └─ 获得正确的类目结构
       │
       ├─ Step 5: 1688 CDP 探针 ──→ 获取采购成本 + 规格
       │
       └─ Step 6: 组装 GraphInput ──→ Worker 更新卡片
                 ┌─ draft.sku_id = follow_{product_id}（匹配已有卡片）
                 ├─ draft.ozon_category = 竞品类目（跳过 Worker 类目匹配）
                 ├─ draft.images = 竞品图 + 1688 图
                 └─ draft.purchase_cost = 1688 价格
```

### 3.2 Ozon Scraper 三层回退策略

```
抓取 Ozon 商品页:
  1. 优先: 本地 CDP Chrome (已登录, 100% 绕过 DataDome)
          ↓ 失败
  2. 回退: Worker API /api/v1/scrape_ozon (curl-cffi + 可选代理)
          ↓ 失败  
  3. 兜底: 仅用 import-by-sku 获取类目结构 (无图片)
```

### 3.3 CDP Chrome 共享机制

1688 和 Ozon 共用一个 Chrome 实例：

```
Chrome --remote-debugging-port=9222 --remote-allow-origins='*'
  │
  ├─ 1688 CDP 探针: enrich_product_with_cdp()
  │   └─ 打开 1688 详情页 → 提取产品数据
  │
  └─ Ozon CDP 探针: scrape_ozon_product_via_cdp()
      └─ 打开 Ozon 商品页 → 提取 JSON-LD 数据
```

**Session 管理：**
- 用户正常使用浏览器 → 积累浏览历史 → 过 DataDome
- 出现滑块验证码 → 用户在浏览器中手动解决
- Session 文件记录登录状态，每次启动自动恢复

## 4. 实现细节

### 4.1 新增文件

| 文件 | 位置 | 说明 |
|------|------|------|
| Ozon Scraper (Skill) | `skill/scripts/lib/ozon_scraper.py` | CDP + curl-cffi 双模式 |
| Ozon Scraper (Worker) | `worker/src/utils/ozon_scraper.py` | curl-cffi 模式 |
| 批量测试脚本 | `skill/scripts/batch_test.py` | 1688/Ozon 自动分流 |

### 4.2 修改文件

| 文件 | 改动 |
|------|------|
| `skill/scripts/cloud_probe.py` | `follow_sell_cloud()` 重写：集成 CDP Scraper + import-by-sku 传图 + offer_id 覆盖 |
| `worker/src/main.py` | 新增 `/api/v1/scrape_ozon` 端点 |
| `worker/pyproject.toml` | 新增 `curl_cffi`、`websocket-client` 依赖 |
| `skill/.env` | 更新 1688 AK (`RmZTWVR...`) |

### 4.3 Ozon Scraper 核心能力

**CDP 模式 (scrape_ozon_product_via_cdp):**

```
输入: Ozon URL
输出: {success, product_id, slug, images[], title, category, price}
```

抓取逻辑：
1. 通过 CDP WebSocket 新建标签页（`PUT /json/new`）
2. `Page.navigate` 导航到目标 URL
3. 等待 5 秒页面加载
4. JavaScript 直接提取：
   - `document.title` → 产品标题
   - `querySelector('script[type="application/ld+json"]').textContent` → JSON-LD
   - `querySelectorAll('[data-widget="breadcrumb"] a')` → 类目面包屑
5. 解析 JSON-LD 获取主图、名称、价格
6. `document.documentElement.outerHTML` → 正则提取所有 `ir.ozone.ru/s3/multimedia*` 图片
7. 去重（按图片基础 URL，跳过不同尺寸变体）

### 4.4 follow_sell_cloud 完整流程

```python
def follow_sell_cloud(ozon_url: str, auto_submit: bool = False) -> dict:
    """
    Step 1: 解析 Ozon URL → product_id + slug
    Step 2: CDP 抓取 Ozon 页面 → images + title + category
            (失败则回退 Worker API → 再失败则仅 import-by-sku)
    Step 3: import-by-sku 复制卡片
            - 若有竞品图，传入 images 参数
            - offer_id = follow_{product_id}
    Step 4: LLM 翻译 title/slug → 中文 1688 关键词
    Step 5: 1688 AK 搜索 (带 fallback)
    Step 6: (auto_submit) CDP 探针 1688 → 组装 envelope → submit Worker
            - draft.sku_id = follow_{product_id}  ← 关键！匹配已有卡片
            - draft.ozon_category = 竞品类目     ← 跳过 Worker 类目匹配
            - draft.images = 竞品图              ← 有图收款核心
    """
```

### 4.5 Worker 端点

```
POST /api/v1/scrape_ozon
  body: {"url": "https://www.ozon.ru/product/xxx-12345/"}
  
  成功: {"success": true, "product_id": "12345", "images": [...], "title": "...", ...}
  失败: {"success": false, "error": "Ozon 反爬拦截 (DataDome)。请配置 OZON_SCRAPER_PROXY。"}
```

环境变量：`OZON_SCRAPER_PROXY`（可选，俄罗斯住宅代理 URL）

### 4.6 MXOU 翻译增强

**问题：** deepseek-v4-flash 默认启用 reasoning tokens，消耗 `max_tokens` 配额导致输出为空。

**修复：**
- `max_tokens`: 100 → 400（给 reasoning 留足空间）
- 多轮递进式重试：500 → 400 → 300 tokens
- 长 slug 自动截断（超过 6 个单词取前 6 个 → 再不行取前 3 个）
- 失败后回退到 slug 原始单词搜索

### 4.7 1688 AK 搜索增强

三级回退策略：
1. LLM 翻译关键词搜索
2. 原始 slug 关键词搜索（取前 3 个有意义的词）
3. 无结果则标记失败

## 5. 测试结果

### 5.1 CDP Ozon 抓取

| 测试 | 结果 |
|------|------|
| CDP Chrome 连接 | ✅ 通过 |
| Ozon 页面加载 | ✅ 200（非 403，未被 DataDome 拦截） |
| JSON-LD 提取 | ✅ 标题、主图、价格 |
| 图片提取 | ✅ 每商品 50+ 张变体图 |
| 类目面包屑 | ⚠️ 部分页面无面包屑（需 API 补全） |

### 5.2 Ozon 跟卖全流程（28 个 URL）

| 步骤 | 成功率 |
|------|--------|
| MXOU 翻译 slug → 中文 | 28/28 (100%) |
| 1688 AK 搜索匹配 | 28/28 (100%) |
| import-by-sku 复制卡片 | 28/28 (100%) |
| import-by-sku 获得图片 | 2/28 (7%，竞品开启了复制保护) |

### 5.3 1688 CDP 抓取（10 个 URL 抽样）

| 步骤 | 成功率 |
|------|--------|
| CDP 浏览器连接 | 10/10 (100%) |
| 产品详情提取 | 10/10 (100%) |
| 图片提取 | 10/10 (100%) |
| GraphInput 组装 | 10/10 (100%) |

### 5.4 Worker 连接

| 端点 | 状态 |
|------|------|
| `GET /api/v1/health` | ✅ `{"status":"ok","db":"connected"}` |
| `POST /api/v1/submit_task` | ✅ 待全量测试 |

## 6. 部署

### 6.1 Skill 端（用户本地）

```bash
pip install curl_cffi websocket-client

# 启动 CDP Chrome（复用 1688 探针的同一个 Chrome）
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --remote-debugging-port=9222 \
  --remote-allow-origins='*' \
  --user-data-dir=<profile目录>
```

### 6.2 Worker 端（云端 Docker）

```bash
# .env 新增（可选）
OZON_SCRAPER_PROXY=http://user:pass@proxy.ru:8080

# 重建
docker compose build worker && docker compose up -d worker
```

### 6.3 CI 更新

`curl_cffi` 需要编译 libcurl（Dockerfile 中需安装 `libcurl4-openssl-dev`）：

```dockerfile
RUN apt-get update && apt-get install -y libcurl4-openssl-dev && rm -rf /var/lib/apt/lists/*
```

## 7. 已知问题 & 后续计划

### 7.1 滑块验证码

- **场景：** CDP Chrome 频繁抓取 Ozon 可能触发滑块
- **当前处理：** 用户在浏览器中手动解决（与 1688 一致）
- **后续计划：** 集成 2Captcha/Anti-Captcha 自动求解

### 7.2 Worker 端代理

- **场景：** 用户本地 CDP 不可用时，Worker 需独立抓取
- **当前状态：** Worker 端点已实现，缺少俄罗斯代理
- **后续计划：** 
  - 短期：购买 Bright Data / Smartproxy 俄罗斯住宅代理（~$5-10/月）
  - 中期：部署 Worker 到俄罗斯 VPS（天然俄罗斯 IP）

### 7.3 Worker Dockerfile 适配 curl_cffi

- `curl_cffi` 依赖编译好的 libcurl，Docker 镜像需更新
- 当前 Docker 镜像未包含 `curl_cffi`，Worker 端点会返回 500

### 7.4 图片去重优化

- 当前从 HTML 提取的图片包含同一照片的多种尺寸（wc50/wc250/wc1000...）
- 需要按图片 ID 去重，只保留最高分辨率版本

### 7.5 Ozon 类目面包屑

- 部分 Ozon 页面没有面包屑 HTML 元素
- 需要从 Ozon API 获取类目信息（import-by-sku 后的卡片已包含 description_category_id）

### 7.6 批量提交去重

- 同一个 Ozon URL（product_id）可能被重复提交
- `batch_test.py` 已做 product_id 去重（按 ID），但 offer_id 可能冲突
- 后续需要检查 `follow_{product_id}` 是否已存在，已存在则跳过或更新

## 8. 变更记录

| 版本 | 日期 | 内容 |
|------|------|------|
| v8-draft | 2026-07-22 | Ozon Scraper 三层回退、follow_sell_cloud 重写、CDP 共用、batch_test 自动分流 |
