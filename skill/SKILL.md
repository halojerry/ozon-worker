---
name: pounding-ozon-probe
description: >
  采集 1688 商品数据并组装 GraphInput 信封，用于提交到云端 Worker 完成 Ozon 上架。
  当用户要上架商品到 Ozon、从 1688 选品、跨境电商铺货时触发。
---

# pounding-ozon-probe

抓取 1688 商品、组装信封，交给云端 Worker 完成 Ozon 上架。

## 依赖

```bash
cd skill/
pip install -r requirements.txt
playwright install chromium
cp .env.example .env   # 填入凭证
```

## 调用方式

agent 通过 shell 执行：

```bash
python3 scripts/cli.py graph \
  --item-id <1688商品ID> \
  --url <1688商品链接> \
  --category-query "<俄语搜索词>"
```

**参数**:

| 参数 | 必填 | 说明 |
|---|---|---|
| `--item-id` | 是 | 1688 商品 offer ID |
| `--url` | 是 | 1688 商品详情页 URL |
| `--category-query` | 否 | Ozon 类目搜索词（俄语），不传则用商品标题 |
| `--max-retries` | 否 | CDP 重试次数，默认 3 |

## 返回值

输出为 JSON，结构如下：

```json
{
  "summary": {
    "item_id": "...",
    "title": "...",
    "purchase_cost": 5.5,
    "weight": 227,
    "dimensions": {"length": 140, "width": 80, "height": 10},
    "images": 10,
    "attributes": 8,
    "variants_found": 3,
    "category": "поилка",
    "supplier": "义乌市...",
    "shipping": "浙江金华 中通 ¥3"
  },
  "envelope": {
    "token": "sk-...",
    "ozon_client_id": "4718259",
    "ozon_api_key": "...",
    "envelope": {
      "draft": { "... 产品数据 ..." },
      "source": { "purchase_url": "...", "purchase_cost": 5.5 },
      "extensions": {}
    }
  }
}
```

- `summary` — 给人看的摘要，用于告知用户采集结果
- `summary.variants_found` — 1688 原始变体数量（仅供参考，已折叠为单产品）
- `envelope` — 给 Worker 的数据包，直接提交即可，**不要修改内部结构**

> **单产品上传模式**：不管 1688 产品有多少个变体（颜色/尺寸/数量），Skill 层自动折叠为 1 个变体。
> - 颜色/尺寸变体 → 取中位数价格
> - 数量变体 → 选「1只装」
> - 采购成本已含 1688 国内运费（freightCny）

## 提交到 Worker

拿到 `envelope` 后，提交到云端 Worker：

```bash
curl -X POST https://<worker-host>/submit_task \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <envelope.token>" \
  -d '<envelope>'
```

**响应处理**:

```json
// 成功
{"ok": true, "task_id": "...", "message": "任务已提交"}

// 失败 - 额度不足
{"ok": false, "error": "insufficient_quota", "remain_quota": 1.5}

// 失败 - token 无效
{"ok": false, "error": "invalid_token"}
```

## Worker 返回结果处理

提交后可通过 `/task_status/<task_id>` 轮询结果：

```json
// 处理中
{"status": "processing", "stage": "image_generation"}

// 完成
{"status": "completed", "product_id": 123456, "offer_id": "...",
 "profit_estimation": {"cost_cny": 5.5, "price_rub": 350, "profit_rub": 120, "margin": 0.35}}

// 失败
{"status": "failed", "error": "category_match_failed", "message": "未找到匹配的Ozon类目"}
```

## 回复用户

- **采集成功**：告知商品标题、价格、SKU 数量、图片数量
- **提交成功**：告知 task_id，建议稍后查看
- **上架完成**：告知 product_id、Ozon 链接、预估利润
- **失败**：根据 error 字段给出建议：
  - `category_match_failed` → 尝试更具体的俄语类目词
  - `insufficient_quota` → 告知用户额度不足
  - `validation_failed` → Ozon 属性校验失败，Worker 会自动重试修复

## 部署

skill 运行在本地，确保依赖已安装（`pip install -r requirements.txt && playwright install chromium`）且 `.env` 凭证已配置。
