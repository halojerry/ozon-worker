# 专家工具子集映射表

> 给 agent 的「那位专家 → 该用哪些工具」速查。工具名列表已逐一对真实清单
> `pounding-mcp/pounding_mcp/server.py`（21 个工具，`main` 为入口函数不计）grep 核对，
> 映射表内**只有清单内工具名**，无幻影。逐工具比对证据见文末「核对记录」。

## 专家概览

| 专家 | 专注 | 主工具 | 写操作（须用户确认） |
|------|------|--------|----------------------|
| 店铺优化大师 | 改价/库存/上下架/促销建议 | `analyze_store`, `run_store_action`, `category`, `query`, `seller` | `run_store_action`(bulk_update_*/archive) |
| 选品大师 | 选品/蓝海/趋势 | `discover`, `discover_multi`, `queries`, `search`, `image_search`, `graph`, `follow`, `probe` | `graph`/`follow`/`--auto-submit` |
| 营销大师 | 活动报名/自建促销/定价 | `analyze_store`, `run_store_action`, `category`, `query` | `run_store_action`(actions_register/seller_action_discount) |

## 工具子集映射（逐工具）

> 每行 `✅/⬜` 表示该工具归该专家后是否仍有归属；多专家共用的工具在单元格里标注用途差异。

| 工具（server.py 真实名） | 店铺优化 | 选品 | 营销 | 不归位说明 |
|--------------------------|:-------:|:----:|:----:|-----------|
| `analyze_store` | ✅ 整店画像（决策源） | ⬜ | ✅ promo_ready 源 | 读盘点位，双大师共用 |
| `run_store_action` | ✅ 改价/库存/上下架 | ⬜ | ✅ 活动/促销 | 写操作；operation 归位分域 |
| `category` | ✅ 类目定位校验 | ⬜ | ✅ 活动类目确认 | 辅助读 |
| `query` | ✅ 变更后跟踪 | ⬜ | ✅ 促销后跟踪 | 辅助读 |
| `seller` | ✅ 竞品店铺参照 | ⬜ | ⬜ | 店铺优化可选参照 |
| `discover` | ⬜ | ✅ C/D 主入口 | ⬜ | — |
| `discover_multi` | ⬜ | ✅ 批量选品 | ⬜ | — |
| `queries` | ⬜ | ✅ 蓝海/榜单 | ⬜ | — |
| `search` | ⬜ | ✅ 1688 关键词 | ⬜ | — |
| `image_search` | ⬜ | ✅ 以图搜款 | ⬜ | — |
| `graph` | ⬜ | ✅ 1688 直采上架 | ⬜ | — |
| `follow` | ⬜ | ✅ Ozon 跟卖 | ⬜ | — |
| `probe` | ⬜ | ✅ 单商品探针 | ⬜ | 调试辅助 |
| `check` | ⬜ | ⬜ | ⬜ | 凭证/环境诊断，不归位 |
| `list_stores` | ⬜ | ⬜ | ⬜ | 店铺罗列，环境类，不归位 |
| `set_store` | ⬜ | ⬜ | ⬜ | 凭证配置，环境类，不归位 |
| `set_token` | ⬜ | ⬜ | ⬜ | 凭证配置，环境类，不归位 |
| `set_ak` | ⬜ | ⬜ | ⬜ | 凭证配置，环境类，不归位 |
| `get_ak` | ⬜ | ⬜ | ⬜ | 凭证配置，环境类，不归位 |
| `update` | ⬜ | ⬜ | ⬜ | 自动更新，环境类，不归位 |
| `cleanup` | ⬜ | ⬜ | ⬜ | 磁盘清理，环境类，不归位 |

## 三张图（按专家展开的完整子集）

**店铺优化大师**：`analyze_store`, `run_store_action`(bulk_update_prices|bulk_update_stocks|bulk_archive), `category`, `query`, `seller`

**选品大师**：`discover`, `discover_multi`, `queries`, `search`, `image_search`, `graph`, `follow`, `probe`

**营销大师**：`analyze_store`, `run_store_action`(actions_register|seller_action_discount), `category`, `query`

## 环境 / 凭证 / 工具不归位

以下 7 个工具是**通用环境与凭证类**，不归属任何业务专家，任何专家出现对应需求时都可调用，但语义是「环境准备/维护+」而非业务动作：

| 工具 | 用途 | 说明 |
|------|------|------|
| `check` | 诊断前置（Chrome/凭证/Worker/Ozon 就绪） | 读取环境 |
| `list_stores` | 罗列已配置店铺 | 环境 |
| `set_store` | 配置店铺凭证 | 写（敏感） |
| `set_token` | 设置 MXOU token | 写（敏感） |
| `set_ak` | 设置 1688 AK | 写（敏感） |
| `get_ak` | 浏览器自动获取 AK | 写（本地 Chrome） |
| `update` | 检查并应用 skill 更新 | 写（维护） |
| `cleanup` | 清理缓存/临时 | 写（维护，默认预演） |

> 若某专家 prompt 里标注「写须用户确认」，其语义 = dsh 侧 pre-execute 审批（见 `docs/ozonharness/MCP-TOOLS.md` 的 SAFETY_MAP），与上述写类一致。

## 关键约定

- **工具名以 `server.py` 为准**：`search`、`seller`、`graph`、`query`、`queries`、`category`、`probe`、`cleanup` 均为真实 MCP 工具名，**不是** CLI 脚本名。
- **`run_store_action` operation 归位分域**（防越界）：店铺优化 = `bulk_update_prices`,`bulk_update_stocks`,`bulk_archive`；营销大师 = `actions_register`,`seller_action_discount`。
- **广告投放为 roadmap**：`/api/client/*`（Performance API）**不在 `run_store_action` 支持集**（见 `worker/src/routes/store_actions_routes.py` 模块注释）。任何「广告投放」都是边界，勿当可执行。
- **意图背景**：工具选择的决策树见 `skill/references/command-reference.md` §意图路由决策树 + `pounding-mcp/pounding_mcp/router.py` 意图词表。

## 核对记录（防幻影工具名）

**方法**：`grep -oE '^def [a-z_]+' pounding-mcp/pounding_mcp/server.py | awk '{print $2}'` 列出全部工具名（硬核对，非「看起来对」）。

**清单输出**（22 行，`main` 为入口函数不计入业务工具，实际工具 21 个）：
```
analyze_store  category  check  cleanup  discover  discover_multi  follow
get_ak  graph  image_search  list_stores  main(*)  probe  queries  query
run_store_action  search  seller  set_ak  set_store  set_token  update
```
（`(*)` = `main` 为 `__main__` 入口，非 MCP 工具，剔除。**21 个业务工具**。）

**逐工具比对（映射表引用的每个工具名 ↔ 清单）**：

| 映射表引用 | 清单命中 | 结果 |
|-----------|---------|------|
| `analyze_store` | `analyze_store` | ✅ |
| `run_store_action` | `run_store_action` | ✅ |
| `category` | `category` | ✅ |
| `query` | `query` | ✅ |
| `seller` | `seller` | ✅ |
| `discover` | `discover` | ✅ |
| `discover_multi` | `discover_multi` | ✅ |
| `queries` | `queries` | ✅ |
| `search` | `search` | ✅ |
| `image_search` | `image_search` | ✅ |
| `graph` | `graph` | ✅ |
| `follow` | `follow` | ✅ |
| `probe` | `probe` | ✅ |
| `check` | `check` | ✅ |
| `list_stores` | `list_stores` | ✅ |
| `set_store` | `set_store` | ✅ |
| `set_token` | `set_token` | ✅ |
| `set_ak` | `set_ak` | ✅ |
| `get_ak` | `get_ak` | ✅ |
| `update` | `update` | ✅ |
| `cleanup` | `cleanup` | ✅ |

**无幻影证据**：映射表引用的全部工具名 = 清单 21 个中的 21 个，**无一超出**清单（21/21 命中）。映射表未出现 `main`（入口函数）或任何 CLI/虚构名（如 `batch_test`、`analyze`、`promo`、`ad`）。

**无遗漏证据**：21 个业务工具全部在映射表中出现（14 个归业务专家 + 7 个环境/凭证类已标注不归位 + 说明）。`graph`/`follow`/`discover`/`search` 的写类 flag（auto_submit/to_box）已归选品大师并标注确认。`analyze_store`/`category`/`query` 为多专家共用，已展开。无任何工具被静默丢弃。
