# 裂变选品（discover --fission）

> 从 SKILL.md §1 与 command-reference.md 管线 C 增强抽取。在种子选品基础上再往深挖一层（种子商品 → 跟卖卖家 → 卖家店铺产品），适合「找更多同类 / 挖同行货源」的意图。

## 用法

```bash
# 种子采集 + 裂变展开（关键词 → 种子 → 卖家 → 店铺产品）
python3 scripts/cli.py discover --keyword "宠物用品" --fission

# 深度 3（需显式 allow）+ 更紧的候选上限
python3 scripts/cli.py discover --keyword "玩具" --fission --max-depth 3 --allow-depth-3 --max-total-products 500

# 非交互（层间不询问继续，适合脚本/CI）
python3 scripts/cli.py discover --keyword "收纳" --fission --non-interactive
```

## 流程

先正常采集种子（管线 C 阶段①②：采集 + 全量数据）→ 裂变展开（BFS：种子商品 → 跟卖卖家 → 卖家店铺产品）→ 回到③表格挑选 → ④批量货源（全部复用）。

## ⚠️ 硬性默认限制（不会无限跑）

| 参数 | 默认 | 说明 |
|------|------|------|
| `--max-total-products` | 300 | 候选总量上限，**主成本控制** |
| `--max-depth` | 2 | 展开深度，>3 需 `--allow-depth-3` 显式开启 |
| `--time-budget` | 600 | 时间预算（秒） |
| `--max-sellers-per-product` | 20 | 每产品最多展开的跟卖卖家数 |
| `--max-products-per-seller` | 15 | 每卖家最多收录的产品数 |
| `--non-interactive` | — | 层间不询问继续 |

**任一预算触顶立即停止**，不会无界扩散。

## ⚠️ 注意事项

- **慢操作**：卖家页串行导航（≥3s 间隔）+ what_to_sell 逐 SKU 限速（1s/SKU），跑一次约 10-60 分钟
- **数据字段**：裂变候选带 `chain_depth`（0=种子/1/2）+ `source_chain`（来源链路 种子→卖家→产品，选中产品时可查看出处）+ `_seed_category_id`（种子类目，同类目 +10 / 跨类目 +3 / 无数据 +0 评分）
- **依赖**：跟卖卖家来自 widget API（需 Ozon 页面正常加载 + 登录态）；部分产品反爬偶发失败 → 自动降级跳过，不影响整体
- 展示候选列表后，等用户确认再提交，不替用户选择
