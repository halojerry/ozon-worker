# 业务模型全景拓扑图

> 与 `worker/src/graphs/graph.py` 当前代码逐节点核对（v0.27.0）。看完这张图就能理解整个业务模型。
> 旧版 `docs/WORKER-TOPOLOGY.md` 的拓扑段落已过时（v0.11），以本文为准。

---

## 0. 一图流（ASCII 全景）

```
┌─────────────────────────── Skill（客户本地，Python≥3.12） ───────────────────────────┐
│                                                                                      │
│   Chrome CDP 通道（cdp_client + chrome_launcher，自动启动/登录/反检测）               │
│                                                                                      │
│   ┌─ graph（1688选品）──▶ CDP抓1688商品页 ─┐                                          │
│   │                                        ├─▶ build_graph_envelope ──┐              │
│   ├─ follow（Ozon跟卖）─▶ CDP抓竞品页 + 以图搜款(1688同款) ─┐            │              │
│   │                      （_pick_best_match 相关性护栏）    ├─▶ 组装信封 ─┼─▶ submit    │
│   ├─ discover（Ozon选品）▶ 搜索/类目页采集 → 蓝海评分 →     │           │  (POST)       │
│   │                      1688匹配+利润计算 → 用户确认       ┘           │              │
│   └─ image_search / check / get_ak / batch_test                       │              │
│                                                                       ▼              │
└──────────────────────────────────────────────────────────────────────────────────────┘
                                                        GraphInput 信封 JSON
                                                         {draft, source, extensions}
                                                        token / ozon_client_id / ozon_api_key
                                                                    │
                                                                    ▼
┌─────────────────────────── Worker（云端 Docker：Worker + PostgreSQL） ─────────────────┐
│                                                                                      │
│   POST /api/v1/submit_task ─▶ 鉴权(Supabase tokens) ─▶ 限流(300/min) ─▶ PG队列        │
│        (ozon_product_tasks, FOR UPDATE SKIP LOCKED) ─▶ 50并发 LangGraph              │
│                                                                                      │
│   auth → check_quota ─┬─▶ follow_sell_import（跟卖）                                  │
│                       └─▶ ingest（1688 直采）                                        │
│                            │                                                          │
│                            ▼                                                          │
│   pricing（定价失败阻断）→ assemble（类目匹配失败阻断）→ scene_generation_llm          │
│                            │                                                          │
│                            ▼                                                          │
│   Phase1 并行: white_bg_gen ──┐                                                       │
│                multi_angle_gen─┤                                                      │
│   Phase2 并行: detail/social_proof/comparison/scene_1/2/3                             │
│                + variant_primary_loop(多SKU) 或 main_image_gen(单SKU)                 │
│                            │                                                          │
│                            ▼                                                          │
│   prepare（翻译/净化/排序）→ ozon_validate ──▶ ozon_upload ─▶ ozon_status             │
│                            │失败                     ▲           │                   │
│                            ▼                         │           ▼                   │
│                    validation_retry_wrapper ◀────────┴─ 修复循环 │                   │
│                    （靶向修复子图，≤3次）                            │                  │
│                            │                                   │approved             │
│                            ▼                                   ▼                      │
│                    learning_record（自学习回写 category_mapping）                     │
│                                                                                      │
│   PG: category_tree_nodes / logistics_rates / dictionary_value_cache /               │
│       category_mapping / size_mappings / ozon_product_tasks / progress               │
└──────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 1. 全景业务模型（Mermaid）

```mermaid
flowchart TB
    subgraph SKILL["Skill 客户本地（Python ≥3.12，Chrome CDP）"]
        direction TB
        CHROME["Chrome CDP<br/>cdp_client + chrome_launcher<br/>自动启动/登录态/反检测"]
        G["graph 1688选品<br/>CDP抓取1688商品页"]
        F["follow Ozon跟卖<br/>CDP抓竞品页 + 以图搜款<br/>_pick_best_match相关性护栏"]
        D["discover Ozon选品<br/>采集→蓝海评分→1688匹配<br/>→利润计算→用户确认"]
        ENV["build_graph_envelope<br/>组装三层信封 {draft, source, extensions}<br/>变体折叠为单SKU"]
        CHROME --> G & F & D
        G & F & D --> ENV
    end

    ENV -- "POST /api/v1/submit_task<br/>GraphInput JSON" --> API

    subgraph WORKER["Worker 云端 Docker（FastAPI + LangGraph）"]
        direction TB
        API["submit_task<br/>鉴权 Supabase tokens<br/>限流300/min → PG队列<br/>50并发"]
        AUTH["auth 节点<br/>token校验 + 店铺配额"]
        API --> AUTH

        AUTH -- "失败" --> END1["END"]
        AUTH -- "通过" --> QUOTA["check_quota 早期配额检查<br/>不足则阻断(不浪费GPU)"]
        QUOTA -- "quota blocked" --> END1
        QUOTA -- "跟卖 follow_sell=true" --> FSI["follow_sell_import<br/>import-by-sku / 竞品属性"]
        QUOTA -- "1688 直采" --> ING["ingest 数据摄入"]

        FSI -- "类目解析失败" --> RETRY["validation_retry_wrapper"]
        FSI -- "正常" --> PRICE
        ING --> PRICE["pricing 定价<br/>物流费率+佣金+汇率<br/>失败[PRICING_FAILED]阻断"]
        PRICE -- "失败" --> END1
        PRICE -- "成功" --> ASSEMBLE["assemble_ozon_product<br/>类目匹配(pg_trgm+学习表)<br/>属性字典值填充"]

        ASSEMBLE -- "类目匹配失败/置信度<0.3" --> END1
        ASSEMBLE -- "成功" --> SCENE["scene_generation_llm<br/>LLM生成3个场景描述"]

        SCENE --> P1A["white_bg_gen"] & P1B["multi_angle_gen"]
        P1A & P1B --> P2A["detail_gen"]
        P1A & P1B --> P2B["social_proof_gen"]
        P1A & P1B --> P2C["comparison_gen"]
        P1A & P1B --> P2D["scene_1_gen"]
        P1A & P1B --> P2E["scene_2_gen"]
        P1A & P1B --> P2F["scene_3_gen"]
        P1A & P1B --> P2G["variant_primary_loop<br/>多SKU主图"]
        P1A & P1B --> P2H["main_image_gen<br/>单SKU主图"]

        P2A & P2B & P2C & P2D & P2E & P2F & P2G & P2H --> PREP["prepare_ozon_upload<br/>俄语翻译+净化+IMG_ORDER排序"]

        PREP --> VAL["ozon_validate 预检测"]
        VAL -- "失败" --> RETRY
        VAL -- "通过" --> UPLOAD["ozon_upload 上传Ozon"]
        UPLOAD --> STATUS["ozon_status 状态轮询<br/>approved / pending(≤3次) / error"]

        STATUS -- "approved" --> LEARN["learning_record<br/>回写 category_mapping 学习表"]
        STATUS -- "pending 重试" --> STATUS
        STATUS -- "error" --> RETRY
        RETRY -- "upload_status=success/pending" --> LEARN
        RETRY -- "失败" --> END1
        LEARN --> END1

        subgraph RETRYSUB["validation_retry_loop 靶向修复子图（≤3次）"]
            direction TB
            PE["parse_error<br/>解析Ozon错误"] --> CE["classify_error<br/>错误分类"]
            CE --> R1["error_repair_llm<br/>属性/描述/标题修复"]
            CE --> R2["repair_prepare<br/>重量尺寸/payload重建"]
            CE --> R3["repair_pricing<br/>价格修复"]
            CE --> R4["repair_dimensions<br/>体积重量重算"]
            R1 & R2 & R3 & R4 --> RV["revalidate → reupload → recheck_status"]
            RV -- "不可修复" --> FR["final_result 终止"]
        end
        RETRY --- RETRYSUB
    end

    subgraph EXT["外部服务"]
        OZON["Ozon Seller API<br/>类目/属性/上传/审核"]
        MXOU["MXOU<br/>生图(banana/gpt-image-2)<br/>LLM(deepseek-v4-flash)"]
        SUPABASE["Supabase<br/>tokens鉴权 / users.quota余额"]
        PG["PostgreSQL<br/>类目树/物流费率/字典值/学习表/任务队列"]
        SENTRY["Sentry 错误监控"]
    end

    UPLOAD --> OZON
    STATUS --> OZON
    ASSEMBLE --> OZON
    VAL --> OZON
    API --> SUPABASE
    API --> PG
    P1A & P1B & P2A & P2B & P2C & P2D & P2E & P2F & P2G & P2H --> MXOU
    PREP --> MXOU
    ASSEMBLE --> MXOU
    API --> SENTRY
```

---

## 2. Worker 节点流（精确版，与 graph.py 逐行对应）

```mermaid
flowchart LR
    ENTRY(["ENTRY"]) --> AUTH["auth"]
    AUTH -->|"失败"| END1(["END"])
    AUTH -->|"成功"| CK["check_quota"]
    CK -->|"quota blocked"| END1
    CK -->|"follow_sell=true"| FSI["follow_sell_import"]
    CK -->|"1688"| ING["ingest"]
    FSI -->|"ozon_product_id为空"| END1
    FSI -->|"类目解析失败"| VW["validation_retry_wrapper"]
    FSI -->|"正常"| PR["pricing"]
    ING --> PR
    PR -->|"[PRICING_FAILED]"| END1
    PR -->|"正常"| AS["assemble_ozon_product"]
    AS -->|"类目匹配失败/conf<0.3"| END1
    AS -->|"成功"| SGL["scene_generation_llm"]
    SGL --> WB["white_bg_gen"]
    SGL --> MA["multi_angle_gen"]
    WB --> D1["detail_gen"]
    MA --> D1
    WB --> D2["social_proof_gen"]
    MA --> D2
    WB --> D3["comparison_gen"]
    MA --> D3
    WB --> D4["scene_1_gen"]
    MA --> D4
    WB --> D5["scene_2_gen"]
    MA --> D5
    WB --> D6["scene_3_gen"]
    MA --> D6
    WB --> VP["variant_primary_loop<br/>(多SKU)"]
    MA --> VP
    WB --> MI["main_image_gen<br/>(单SKU)"]
    MA --> MI
    VP --> PREP["prepare_ozon_upload"]
    MI --> PREP
    D1 & D2 & D3 & D4 & D5 & D6 --> PREP
    PREP --> OV["ozon_validate"]
    OV -->|"通过"| OU["ozon_upload"]
    OV -->|"失败"| VW
    OU --> OS["ozon_status"]
    OS -->|"approved / imported+success"| LR["learning_record"]
    OS -->|"pending ≤3次"| OS
    OS -->|"error / unknown"| VW
    VW -->|"success / pending"| LR
    VW -->|"失败"| END1
    LR --> END1
```

> **两条管线的汇合点**：跟卖（`follow_sell_import`）与直采（`ingest`）在 `pricing` 汇合，
> 之后共用完全相同的「定价 → 组装 → 生图 → 上传 → 审核 → 学习」流水线。
> **唯一区别**：`assemble` 对跟卖走 `_assemble_follow_sell` 轻量模式（复用竞品属性+类目），
> 对直采走 `_build_items_deterministically` 完整模式。

---

## 3. 修复循环子图（validation_retry_loop，最多 3 次）

```mermaid
flowchart LR
    PE["parse_error 解析错误"] --> CE["classify_error 分类"]
    CE -->|"属性/描述/标题类"| R1["error_repair_llm"]
    CE -->|"重量/尺寸/变体"| R2["repair_prepare"]
    CE -->|"价格类 INVALID_PRICE"| R3["repair_pricing"]
    CE -->|"体积重量 ML_INCORRECT_VOLUME_WEIGHT"| R4["repair_dimensions"]
    R1 & R2 & R3 & R4 --> RV["revalidate"]
    RV -->|"通过"| RU["reupload"] --> RS["recheck_status"]
    RV -->|"仍失败"| PE
    RS -->|"通过"| FR(["final_result → learning_record"])
    RS -->|"仍失败"| PE
    RS -->|"重试超限"| FR
    RV -->|"不可修复(如 PRODUCT_ALREADY_EXISTS)"| FR
```

> **靶向修复原则**：有 `product_id` 时用增量 API（`/v1/product/attributes/update`、
> `/v1/product/prices/update`），**不重跑全管线**（不重新生图/LLM）。无 `product_id` 才走 CREATE 模式。

---

## 4. Skill 三条管线细节

```mermaid
flowchart LR
    subgraph P1["graph — 1688 选品上架"]
        U1["1688 URL"] --> C1["CDP 抓取商品页<br/>价格/属性/图片/描述"]
        C1 --> E1["build_graph_envelope<br/>变体折叠 + 采购成本(含运费)"]
    end
    subgraph P2["follow — Ozon 跟卖"]
        U2["Ozon URL"] --> C2["CDP 抓竞品页<br/>标题/价格/属性/类目面包屑"]
        C2 --> IS["以图搜款 1688<br/>ozon_image_search CDP网页版图搜"]
        IS --> PM["_pick_best_match 相关性护栏<br/>badge+标题重叠打分, 不符拒绝"]
        PM --> E2["follow_sell_cloud 组装<br/>follow_type: hand/api"]
    end
    subgraph P3["discover — Ozon 选品"]
        U3["Ozon 搜索/类目页"] --> C3["采集产品列表"]
        C3 --> AN["ozon_seller_analytics<br/>月销/增长率/佣金(登录时)"]
        AN --> BS["蓝海评分 + 表格挑选"]
        BS --> M3["1688 识图匹配 + 利润计算"]
        M3 --> E3["build_envelope_from_discovery"]
    end
    E1 & E2 & E3 --> SUB["submit_envelope → Worker"]
```

---

## 5. 数据表 & 状态流转

### PG 核心表（`worker/src/storage/database/`）

| 表 | 用途 | 读写方 |
|----|------|--------|
| `category_tree_nodes` | 7424 类目中俄双语（同一 ID 跨语言一致） | init_data 导入 / 类目匹配读 |
| `logistics_rates` | 物流费率（定价用；为空时兜底费率虚高 3-4 倍⚠️） | init_data 导入 / pricing 读 |
| `dictionary_value_cache` | 属性字典值缓存（JSONB，ZH_HANS↔RU 同一 dict_id） | assemble / retry 读写 |
| `category_mapping` | 类目学习表（跟卖/直采优先查，approved 后回写） | follow/assemble 读，learning_record 写 |
| `size_mappings` | 尺码表（4 张 CSV 随镜像分发） | size_mapper 读 |
| `ozon_product_tasks` | 任务队列（FOR UPDATE SKIP LOCKED） | submit 写 / worker 消费 |
| `progress` | 任务进度持久化（重启恢复） | task_processor 写 |

### 成功判据（v0.21 收紧）

```
learning_record 只认: moderate_status == "approved"
（imported 即 success / pending+product_id 视为成功 —— 均已删除）
```

### 错误码

统一在 `worker/src/api/errors.py`（12 个 `WorkerErrorCode`）：`INVALID_REQUEST`、`AUTH_FAILED`、`QUOTA_BLOCKED`、`PRICING_FAILED`、`CATEGORY_MATCH_FAILED` 等。

---

## 6. 关键设计决策速查

| 决策 | 内容 |
|------|------|
| 类目匹配 | 不用 LLM：pg_trgm 相似度 + jieba 分词末级 + 同义词表 + 学习表优先 |
| 字典属性 | 绝不手填文本，未匹配 → 跳过（报「请从列表中选择」的根因已修） |
| 中文零容忍 | 必填俄语属性含中文 → 跳过；描述净化移除拉丁/中文/营销词 |
| 危险品 9782 | 只挑「非危险」安全默认，不取第一个字典值 |
| 定价失败 | 阻断上架，不兜底 ¥1000 |
| 跟卖双模式 | hand（CREATE 重建防侵权）/ api（import-by-sku 复制）；offer_id 统一 `follow_{竞品ID}` |
| 竞品图 | 禁上传竞品 ir.ozone.ru 图补位（0 图下架根因） |
| 尺寸防线 | draft_sanity 入队拦截 weight>50kg/单边>5m；cm→mm 阈值 200 |
| 鉴权余额 | 用 `users.quota`，绝不用僵尸字段 `tokens.remain_quota` |
| 生图模型 | main/social_proof 用 gpt-image-2，其余 banana；提示词外置热加载 |
