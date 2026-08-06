# pounding-ozon-probe

> ⚠️ 本文件为维护者文档。AI Agent 请遵循 `SKILL.md`。

1688 (Alibaba) → Ozon 全流程工具：选品、采集、信封组装、上架 Ozon。

## 安装

```bash
pip install -r requirements.txt
# Chrome 自动启动（CDP），无需安装 Playwright
```

## 凭证

凭证存储在 `data/config/`（非环境变量）：用 `python3 scripts/cli.py set_token / set_ak / set_store` 配置。

## 用法

```bash
# 1. 搜索 1688 商品
python3 scripts/cli.py search "宠物饮水机"

# 2. CDP 探针抓取单个商品
python3 scripts/cli.py probe --url https://detail.1688.com/offer/980815374096.html

# 3. 组装完整 GraphInput envelope
python3 scripts/cli.py graph --item-id 980815374096 --category-query "поилка"
```

## Python API

> ⚠️ Agent 请勿自行写代码调 API（见 SKILL.md §8）。以下为维护者参考：

```python
from scripts.cloud_probe import build_graph_envelope_with_retry

graph = build_graph_envelope_with_retry(
    item_id="1006906626070",
    detail_url="https://detail.1688.com/offer/1006906626070.html",
    category_query="вентилятор",
    max_retries=3,
)
# graph = {token, ozon_client_id, ozon_api_key, envelope}
# envelope = {draft, source, extensions}  (三层结构)
print(graph["envelope"]["draft"]["title"])
```

## 输出格式

envelope 采用三层结构 `{draft, source, extensions}`，与 worker 端对齐：

```json
{
  "token": "sk-...",
  "ozon_client_id": "4718259",
  "ozon_api_key": "...",
  "envelope": {
    "draft": {
      "item_id": "980815374096",
      "title": "宠物自动饮水器...",
      "description": "",
      "currency": "CNY",
      "purchase_cost": 5.5,
      "purchase_url": "https://detail.1688.com/offer/980815374096.html",
      "weight": 227,
      "dimensions": {"length": 0, "width": 0, "height": 0},
      "images": ["https://cbu01.alicdn.com/..."],
      "attributes": {"品牌": "...", "材质": "..."},
      "variants": [
        {"sku_id": "...", "name": "白色", "color": "白色", "model": "",
         "image": "...", "price": 5.5, "original_price": 5.5,
         "size": "one size", "stock": 100}
      ],
      "supplier": "义乌市阔折塑料制品厂",
      "stock": 100,
      "shipping": {"origin": "浙江金华", "freightCny": 3, "carrier": "中通快递"},
      "ozon_category": {"description_category_id": "17028929", "type_id": "504866264"}
    },
    "source": {
      "purchase_url": "https://detail.1688.com/offer/980815374096.html",
      "purchase_cost": 5.5
    },
    "extensions": {}
  }
}
```

> **字段说明**：`draft.dimensions` 单位为 **mm**（1688 原数据为 cm，已自动 ×10 转换）；`draft.weight` 单位为 **克**；`variant.price` 为 1688 SKU 采购成本（CNY），**不做加价**（定价由云端 worker 统一处理）。

## 文件结构

```
pounding-ozon-probe/
  scripts/
    cli.py                    # CLI 入口 (search/probe/graph)
    _const.py                 # 常量
    _errors.py                # 异常
    cloud_probe.py            # build_graph_envelope + 质量门禁
    lib/
      ak_1688_client.py       # 1688 AK API + CDP 富化
      ozon_api.py             # Ozon 类目解析
      reference_images.py     # 图片过滤
      config_store.py         # 凭证管理
      task_paths.py           # 临时文件
      logging_utils.py        # 结构化日志
    capabilities/
      browser_probe/
        service.py            # Chrome CDP 探针
        stealth.py            # 反检测
```

## 与 pounding-ozon-hybrid 的关系

`pounding-ozon-probe` 是 `pounding-ozon-hybrid` 的数据采集子集，不含：
- 管线执行引擎 (pipeline.py)
- Supabase 数据库 (supabase_client.py)
- 服装尺码映射 (size_mapping.py)
- n8n 云端工作流
- 自学习/验证/修复
