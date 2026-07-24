---
name: pounding-ozon-probe-dev
version: 0.2.0
description: >
  开发者文档 — 完整架构细节、Worker 路由逻辑、属性 ID 表、故障排查。
  此文件不打包进 dist/，仅供开发者参考。
---

# pounding-ozon-probe — 开发者文档

⚠️ 此文件包含内部架构细节，不面向终端用户。

## 架构概述

```
Skill（本地）                    Worker（云端 worker.mxou.cn）
──────────────                   ─────────────────────────────
1688 CDP 抓取                    类目匹配 → 定价 → 属性填充
Ozon CDP 抓取                    AI 生图 → 校验 → 上传 Ozon
以图搜款（CDP 网页版）             自学习记录
组装 GraphInput 信封
POST /api/v1/submit_task  ───→  LangGraph 管线执行
```

## Worker 内部路由

### 管线 A：1688 选品

```
ingest → category_match → pricing → assemble_ozon_product → prepare_ozon_upload
→ generate_images → ozon_validate → validation_retry_loop → ozon_upload → ozon_status
→ learning_record
```

### 管线 B：Ozon 跟卖

```
follow_sell_import（import-by-sku）→ assemble_ozon_product → prepare_ozon_upload
→ generate_images → ozon_validate → validation_retry_loop → ozon_upload → ozon_status
→ learning_record
```

**路由逻辑**：`extensions.follow_sell=true` 时走跟卖管线。Worker 尝试 import-by-sku，成功则跟卖，失败则走全流程。

## 属性 ID 关键表

| attr_id | 名称 | 类型 | 说明 |
|---------|------|------|------|
| 9782 | 危险品等级 | 字典属性 | 某些类目必填，**不能跳过** |
| 22508 | 品牌注册国 | 自由文本 | 硬编码为 "Китай" |
| 23487 | 制造商 | 自由文本 | 用 `draft.supplier` 填充，不写空值 |
| 23536 | 标记码 | 系统自动 | Ozon 自动设置，**必须跳过** |

## 品牌默认值

所有产品强制默认为 `Нет бренда`（dictionary_value_id=126745801）。
不管 1688 数据或 LLM 匹配到什么品牌，一律覆盖。
代码位置：`assemble_ozon_product_node.py:1007-1022`

## 俄语类目树

`category_tree_nodes` 表存中俄双语（`language=ZH_HANS` / `language=RU`）。
同一 `description_category_id` / `type_id` 跨语言一致。
属性 schema + 字典值已切换到 `language=RU`，LLM 不再翻译属性值。

## 数据抓取字段来源

### Ozon 抓取（CDP + API）

| 字段 | 来源 | 说明 |
|------|------|------|
| title | JSON-LD | 俄语标题 |
| images[] | HTML+JSON-LD | 竞品图片（60-80 张） |
| price | JSON-LD | 参考价格 |
| category | API breadcrumbs | 类目路径 |
| attributes{} | API widget | {类型, 颜色, 材质, 原产国} |
| hashtags[] | DOM | ["#тег1", "#тег2"] |
| description | JSON-LD | 产品描述 |
| sku | JSON-LD | 产品 ID |

### 1688 抓取（CDP）

| 字段 | 来源 | 说明 |
|------|------|------|
| title | 页面 | 中文标题 |
| images[] | 页面 | 商品图片（优先 ww1200 质量） |
| price | 页面 | 采购价格 (CNY) |
| weight | 页面 | 重量 (克) |
| dimensions | 页面 | {length, width, height} (mm) |
| attributes{} | 页面 | 1688 属性 |
| supplier | 页面 | 供应商名 |
| variants[] | 页面 | SKU 变体 |

## 描述净化

`_sanitize_description()` 在翻译后移除：
- 拉丁文字符
- 中文字符
- URL / 邮件 / 电话
- 营销词

代码位置：`prepare_ozon_upload_node.py`

## Cython 编译

核心库编译为 `.so` / `.pyd` 保护源码：

```bash
cd skill/
pip3 install cython setuptools
python3 compile.py
```

编译文件：ak_1688_client、chrome_launcher、config_store、ozon_scraper、ozon_image_search
仅复制：cli.py、cloud_probe.py、batch_test.py

输出到 `dist/` 目录，可直接分发。

## 配置存储

```
skill/data/config/
├── stores.json       # Ozon 多店铺 {default, stores: {name: {client_id, api_key, ...}}}
└── settings.json     # mxou_token, ali_1688_ak, sentry_dsn
```

MXOU_TOKEN 自动从 `~/.pounding/config.json` 读取（如果存在），否则需手动设置。

## 故障排查

### DESCRIPTION_DECLINE 多重根因

1. 产品名含拉丁/中文字符 → `ozon_validate_node` 应阻断（已修复）
2. 属性值含中文 → 俄语类目树 ID 映射解决（`language=RU` 字典值直连）
3. 图片含文字/URL/物流信息 → AI 模型局限性，标记为 warning 不阻断（已修复）
4. 类目不匹配 → 已添加一致性检查 + 俄语标题重新匹配（已修复）

### deepseek-v4-flash reasoning tokens

该模型默认启用推理，`reasoning_tokens` 消耗 `max_tokens` 配额。
翻译/生图 prompt 的 `max_tokens` 至少设为 200，否则输出为空。

### 物流费率表为空

兜底费率 `weight * 0.15 CNY` 是实际费率的 3-4 倍。
部署时必须确保 `init_data.py` 在 worker 启动前执行完毕。

### validation_retry_loop 三大缺陷（已修复）

1. `state.draft` 为空 → LLM 收不到产品上下文 → 回退到 `ozon_payload.items[0].name`
2. `recheck_status_node` 在 `imported` 即宣告成功 → 已改为额外轮询 `moderate_status`
3. `type_id=0` 导致 `/v3/product/import` 报错 → 模板含 `type_id` 字段

## Worker API 端点

| 功能 | 路径 | 方法 |
|------|------|------|
| 提交任务 | `POST /api/v1/submit_task` | POST |
| 查询状态 | `GET /api/v1/task_status/{id}` | GET（含 progress 字段） |
| 取消任务 | `POST /api/v1/cancel_task/{id}` | POST |
| 任务统计 | `GET /api/v1/task_statistics` | GET |
| 健康检查 | `GET /api/v1/health` | GET |
| Swagger | `GET /api/v1/docs` | GET |
