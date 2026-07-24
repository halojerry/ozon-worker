# PRD: Worker 边界重整 + 1688 以图搜款集成 v9

## 1. 背景

### 1.1 v8 遗留问题

v8 实现了 Ozon CDP 抓取 + 跟卖搜索，但存在以下架构问题：

1. **边界模糊**：`import-by-sku` 在 Skill 端直接调 Ozon API，违反「Skill 不调用上架 API」原则
2. **两条线路未在 Worker 区分**：1688 和 Ozon 跟卖走同一个管线，Worker 无法针对性优化
3. **1688 以图搜款缺失**：已有成熟参考实现（`1688-product-find v1.7.0`），但未集成
4. **跟卖管线冗余**：类目匹配和属性填写在跟卖场景下是多余的（竞品卡片已包含）

### 1.2 参考实现

`/Users/halo/Downloads/1688-product-find-1.7.0`（以下简称 `pf`）是一个成熟的 1688 Skill，提供：

| 命令 | 功能 |
|------|------|
| `image_search --image <file>` | 以图搜款（base64 上传图片到 1688 AK API） |
| `text_search <query>` | 文本搜索（我们已有） |
| `get_ak` | 浏览器获取 1688 AK |
| `auth_status` | 检查授权状态 |
| `authorize` | OAuth 授权流程 |
| `query_all_scope` | 查询可用权限列表 |

与我们共享：
- 同一个 API 端点（`skills-gateway.1688.com`）
- 同一套 AK 格式（base64url / `AK:Secret`）
- 同一套 HMAC-SHA256 签名算法
- 同一个 AK 存储位置（`.1688-AK/.ak_store.json`）

## 2. 目标

### 2.1 Worker-Skill 边界重整

```
┌─ Skill（Agent 工具，本地）──────────────────────────────┐
│                                                          │
│  1688 URL ──→ CDP 抓取 ──→ 完整 GraphInput              │
│                                                          │
│  Ozon URL ──→ CDP 抓图+标题 ──→ 翻译 ──→ 1688 搜索     │
│              ──→ 组装 GraphInput                         │
│                  draft.ozon_product_id = "3726236911"    │
│                  extensions.follow_sell = true           │
│                                                          │
│  图片文件  ──→ 图片预处理 ──→ 1688 API 以图搜款 ──→ 输出│
│                                                          │
│  Skill 不调任何 Ozon 上架 API                            │
│  仅负责：抓取 + 搜索 + 组装信封                          │
└──────────────────────────────────────────────────────────┘

┌─ Worker（云端 Docker 管线）──────────────────────────────┐
│                                                          │
│  收到 envelope，路由：                                   │
│                                                          │
│  follow_sell = false（1688 管线，现有）                   │
│    auth → category → pricing → assemble                 │
│    → images → validate → upload → learning              │
│                                                          │
│  follow_sell = true（跟卖管线，新增）                     │
│    auth → import-by-sku(复制卡片+获取类目)               │
│    → pricing(1688成本算定价)                             │
│    → images(竞品图 + 生图)                               │
│    → upload(v3/product/import, 同 offer_id=更新)         │
│    → learning                                            │
│    ⚡ 跳过: category(类目已有), assemble(属性已有)       │
│                                                          │
│  两种管线输出一致：                                      │
│    {task_id, product_id, purchase_url,                  │
│     purchase_cost, profit_estimation}                   │
└──────────────────────────────────────────────────────────┘
```

### 2.2 1688 以图搜款

```
图片文件 (JPG/PNG/WEBP, ≤5MB)
  │
  ├─ _image.py 预处理 (Pillow)
  │   ├─ >800x800 → thumbnail 缩小
  │   ├─ 非 JPEG → 转 JPEG
  │   └─ RGBA/LA/P → RGB 白底
  │
  └─ 1688 AK API: POST /api/find_product/1.0.0
       body: {imgBase64: "...", pageSize: 10, ...}
       ↓
     返回匹配商品列表
```

## 3. 实现计划

### 3.1 Worker 侧改动

#### 3.1.1 State 扩展

`worker/src/graphs/state.py`:

```python
class GraphInput(BaseModel):
    # ... 现有字段 ...
    extensions: Dict[str, Any]  # 已有，新增 follow_sell 标记
    # extensions.follow_sell = true 时走跟卖管线
```

#### 3.1.2 主图路由

`worker/src/graphs/graph.py`:

```python
def route_by_type(state: GlobalState) -> str:
    extensions = state.envelope.get("extensions", {})
    if extensions.get("follow_sell"):
        return "follow_sell_import"
    return "category_match"  # 现有1688管线
```

#### 3.1.3 跟卖管线节点（新增/复用）

| 节点 | 类型 | 说明 |
|------|------|------|
| `follow_sell_import_node` | **新增** | import-by-sku 复制竞品卡片，获取 `description_category_id` + `type_id` |
| `pricing_node` | **复用** | 1688 采购成本计算定价 |
| `assemble_ozon_product_node` | **复用** | 精简：跳过 LLM 属性匹配，直接用竞品属性 + offer_id=follow_xxx |
| `scene_generation_llm` + 生图 | **复用** | 竞品图 + MXOU 生图 |
| `ozon_validate_node` | **复用** | 预检 |
| `ozon_upload_node` | **复用** | v3/product/import（同 offer_id=更新） |
| `ozon_status_node` | **复用** | 状态轮询 |
| `learning_record_node` | **复用** | 学习记录 |

#### 3.1.4 import-by-sku 节点

```python
def follow_sell_import_node(state: GlobalState) -> GlobalState:
    """
    1. POST /v1/product/import-by-sku
       body: {items: [{sku: ozon_product_id, offer_id: "follow_{id}", ...}]}
    2. 轮询 import task 状态
    3. GET /v3/product/info/list 获取类目 ID
    4. 写入 state.description_category_id + state.type_id
    """
```

### 3.2 Skill 侧改动

#### 3.2.1 follow_sell_cloud 精简

删除 `import-by-sku` 调用，改为设置 envelope 标记：

```python
# 旧代码（删除）:
import_by_sku_resp = req.post("https://api-seller.ozon.ru/v1/product/import-by-sku", ...)

# 新代码:
draft = envelope["envelope"]["draft"]
draft["ozon_product_id"] = product_id  # 竞品的 Ozon product_id
envelope["envelope"]["extensions"]["follow_sell"] = True
```

#### 3.2.2 集成 1688-product-find 模块

从 `pf` 复制/适配以下文件到 `skill/scripts/lib/`：

| 源文件 | 目标 | 说明 |
|--------|------|------|
| `pf/scripts/_image.py` | `skill/scripts/lib/image_preprocessor.py` | 图片预处理 |
| `pf/scripts/_http.py` 中的 `search_products` | 扩展现有 `ak_1688_client.py` | 加 `search_by_image()` |
| `pf/scripts/capabilities/image_search/` | 合并到 `ak_1688_client.py` | 以图搜款逻辑 |

需要对现有 `ak_1688_client.py` 做的改动：

```python
# 新增函数
def search_by_image(
    image_path: str = "",
    image_url: str = "",
    page_size: int = 10,
    sort_type: str = "",
    score_level: str = "high",
    purchase_amount: int = 1,
    tags: str = "4306497",
) -> list[dict[str, Any]]:
    """
    1688 以图搜款。
    
    Args:
        image_path: 本地图片路径
        image_url: 图片 URL
        page_size: 返回数量（1-20）
    
    Returns:
        匹配商品列表
    """
```

#### 3.2.3 CLI 新增 image_search 命令

```python
# cli.py
ip = sub.add_parser("image_search", help="以图搜款")
ip.add_argument("--image", required=True, help="图片路径或 URL")
ip.add_argument("--limit", type=int, default=10)
ip.set_defaults(func=cmd_image_search)
```

#### 3.2.4 集成 auth 命令

```python
# cli.py 新增
# get_ak → 浏览器获取 AK
# auth_status → 检查授权状态
```

这些从 `pf` 的 `scripts/_auth.py` + `scripts/authorize.py` + `scripts/callback_server.py` 适配过来。

### 3.3 文件变更清单

| 操作 | 文件 | 说明 |
|------|------|------|
| 🆕 | `skill/scripts/lib/image_preprocessor.py` | 从 pf 适配的图片预处理 |
| ✏️ | `skill/scripts/lib/ak_1688_client.py` | 新增 `search_by_image()` |
| ✏️ | `skill/scripts/cli.py` | 新增 `image_search`、`get_ak`、`auth_status` 命令 |
| ✏️ | `skill/scripts/cloud_probe.py` | `follow_sell_cloud` 删除 import-by-sku，设 follow_sell 标记 |
| ✏️ | `worker/src/graphs/state.py` | GraphInput 无需改动（extensions 已支持） |
| ✏️ | `worker/src/graphs/graph.py` | 主图加路由判断 `follow_sell` |
| 🆕 | `worker/src/graphs/nodes/follow_sell_import_node.py` | import-by-sku 节点 |
| ✏️ | `worker/src/graphs/nodes/assemble_ozon_product_node.py` | 跟卖模式：跳过 LLM 属性匹配，直接构建 |
| 📄 | `docs/PRD-worker-stability-v9.md` | 本文档 |

## 4. 两条线路完整对比

| | 1688 管线 | 跟卖管线 |
|---|----------|---------|
| **入口** | 1688 URL | Ozon URL |
| **Skill 工作** | CDP 抓取全部数据 | CDP 抓图+标题 → 翻译 → 1688搜索 |
| **envelope 标记** | `follow_sell: false` | `follow_sell: true` + `ozon_product_id` |
| **Worker 第一步** | category 类目匹配 | import-by-sku 复制卡片 |
| **Worker 类目** | pg_trgm + LLM | 竞品卡片已有 |
| **Worker 属性** | LLM 完整组装 | 竞品卡片已有（跳过） |
| **Worker 定价** | 1688 成本计算 | 1688 成本计算（同） |
| **Worker 图片** | MXOU 全流程生图 | 竞品图 + MXOU 生图 |
| **Worker 上传** | v3/product/import（新建） | v3/product/import（同 offer_id=更新） |
| **输出** | purchase_url + profit | purchase_url + profit（一致） |

## 5. 以图搜款使用场景

```
用户拍了一张产品照片
  → python3 cli.py image_search --image photo.jpg
  → 返回 1688 同款/相似商品列表
  → 用户挑选 → python3 cli.py graph --item-id <1688_id>
  → 组装信封 → Worker 上架
```

也可以集成到跟卖管线中：

```
Ozon URL → CDP 抓竞品主图
  → image_search(竞品主图)  # 新增：以图搜同款
  → CDP 抓取匹配的 1688 商品
  → 组装信封 → Worker
```

## 6. 分阶段实施

### Phase 1: 边界重整（本次）
- Worker 加 `follow_sell_import_node` + 主图路由
- Skill `follow_sell_cloud` 删 import-by-sku
- 两条线路端到端跑通

### Phase 2: 以图搜款集成（本次）
- 从 pf 适配 `_image.py` → `image_preprocessor.py`
- `ak_1688_client.py` 加 `search_by_image()`
- CLI 加 `image_search` 命令

### Phase 3: Auth 命令（可选，后续）
- `get_ak`、`auth_status`、`authorize`
- 需要 OAuth 回调服务器等基础设施

## 7. 测试验证

- [ ] 1688 URL → 完整管线 → product_id + purchase_url
- [ ] Ozon URL → 跟卖管线 → product_id + purchase_url
- [ ] `image_search --image photo.jpg` → 1688 匹配商品列表
- [ ] `cli.py check` 新前置条件（image_search 依赖检查）
- [ ] Worker 跟卖管线：import-by-sku → pricing → upload
