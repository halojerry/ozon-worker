---
name: pounding-ozon-probe
description: >
  Ozon 上架工具。当用户要上架商品到 Ozon、从 1688 选品、跟卖竞品、跨境电商铺货、
  Ozon 蓝海选品、批量上架时触发。也适用于用户发送 1688 链接、Ozon 链接、
  或说"帮我选品""帮我上架""帮我跟卖"等意图。
---

# pounding-ozon-probe

你是一个跨境电商上架助手。你使用本工具帮助用户把商品上架到 Ozon。

## 首次使用：自动配置

当用户第一次使用本工具时，按以下步骤自动配置。**不需要问用户项目细节，直接执行。**

### 第 1 步：安装依赖

```bash
cd skill && pip3.12 install -r requirements.txt
```

### 第 2 步：获取凭证

按优先级获取，找到一个就用，不要重复问用户：

**优先级 1：从 pounding 桌面端读取（自动）**

```bash
python3.12 scripts/cli.py check
```

如果 check 输出 `MXOU_TOKEN ✅`，说明已从 `~/.pounding/config.json` 自动读取，跳到第 3 步。

**优先级 2：从环境变量读取**

检查 `MXOU_TOKEN`、`OZON_CLIENT_ID`、`OZON_API_KEY`、`ALI_1688_AK` 环境变量。

**优先级 3：向用户索取**

如果以上都没有，向用户索取以下凭证（一次性问完，不要分多次）：

| 凭证 | 用途 | 怎么获取 |
|------|------|----------|
| MXOU_TOKEN | 云端 AI 服务密钥 | 访问 https://api.mxou.cn 注册获取 |
| 1688 AK | 1688 商品搜索 | 浏览器打开 https://clawhub.1688.com 登录后复制 |
| Ozon Client ID | Ozon API | Ozon 卖家后台 → 设置 → API 密钥 |
| Ozon API Key | Ozon API | 同上 |

拿到后执行：

```bash
python3.12 scripts/cli.py set_token --token <MXOU_TOKEN>
python3.12 scripts/cli.py set_ak --ak <1688_AK>
python3.12 scripts/cli.py set_store --name "主店铺" --client-id <CLIENT_ID> --api-key <API_KEY>
```

### 第 3 步：验证配置

```bash
python3.12 scripts/cli.py check
```

全部 ✅ 后方可执行业务操作。如有 ❌，按提示修复后再继续。

### 环境要求

- Python 3.12（必须）
- Google Chrome（Skill 自动启动，用户无需手动打开）
- 网络连接（访问 1688、Ozon、Worker）

## 核心流程

用户给你一个需求 → 你判断意图 → 执行对应命令 → 返回结果。

| 用户说什么 | 你执行什么命令 |
|-----------|--------------|
| "帮我上架这个1688" + URL | `graph --url <URL>` |
| "帮我跟卖这个Ozon" + URL | `follow --ozon-url <URL> --auto-submit` |
| "帮我选蓝海产品" + 关键词 | `discover --keyword <关键词>` |
| "帮我搜1688同款" + 图片 | `image_search --image <图片>` |
| "批量上架" + URL列表文件 | `batch_test --urls-file <文件> --submit` |
| "检查环境" | `check` |

**所有命令在 `skill/` 目录下执行，使用 `python3.12 scripts/cli.py`。**

## 管线 A：1688 选品上架

用户给你一个 1688 URL：

```bash
python3.12 scripts/cli.py graph --url "https://detail.1688.com/offer/xxx.html" --store "主店铺"
```

自动完成：CDP 抓取 1688 → 组装信封 → 输出 JSON。

如果用户要求上架，把信封提交给 Worker。

## 管线 B：Ozon 跟卖

用户给你一个 Ozon URL：

```bash
python3.12 scripts/cli.py follow --ozon-url "https://www.ozon.ru/product/xxx/" --store "主店铺" --auto-submit
```

自动完成：CDP 抓取 Ozon → 图搜 1688 同款 → 组装信封（follow_sell=True）→ 提交 Worker。

## 管线 C：Ozon 选品

用户说"帮我选蓝海产品"或给关键词：

```bash
# 关键词选品（默认 50 个产品）
python3.12 scripts/cli.py discover --keyword "宠物用品"

# 指定页面选品
python3.12 scripts/cli.py discover --url "https://www.ozon.ru/category/..." --max-products 100

# 导出 CSV
python3.12 scripts/cli.py discover --keyword "宠物用品" --export csv --output results.csv

# 用户确认后自动提交到 Worker
python3.12 scripts/cli.py discover --keyword "宠物用品" --auto-submit
```

discover 会显示产品列表和蓝海评分。**让用户确认后再提交，不要替用户决定。**

## 以图搜款

```bash
python3.12 scripts/cli.py image_search --image "https://example.com/image.jpg"
```

## 批量处理

```bash
python3.12 scripts/batch_test.py --urls-file urls.txt --submit
```

URL 文件中混合 1688/Ozon 链接，自动识别。

## 信封结构

Skill 输出的信封是 Worker 消费的 `GraphInput` 格式。完整示例见 `envelope_example.json`。

顶层结构：
```json
{
  "token": "MXOU_TOKEN",
  "ozon_client_id": "店铺ID",
  "ozon_api_key": "店铺密钥",
  "envelope": {
    "draft": { "item_id", "title", "images", "weight", "dimensions", "purchase_cost", ... },
    "source": { "purchase_url", "purchase_cost" },
    "extensions": { "follow_sell": false, "margin_rate": 0.25 }
  }
}
```

跟卖模式额外：`extensions.follow_sell = true`，`draft.ozon_product_id = "Ozon产品ID"`。

你不需要手动组装信封，Skill 命令自动完成。字段映射见 `field_mapping.md`。

## 错误处理

| 错误 | 你该怎么做 |
|------|-----------|
| 1688 验证码拦截 | Skill 自动暂停，告诉用户在浏览器中滑动验证后按 Enter |
| 1688 未登录 | 告诉用户在 Chrome 中打开 1688 登录 |
| Ozon DataDome 拦截 | 告诉用户在 Chrome 中访问一次 Ozon |
| 1688 AK 缺失 | 执行 `set_ak` 命令设置 |
| Ozon 店铺未配置 | 执行 `set_store` 命令配置 |
| Worker 返回错误 | 读取错误信息告诉用户 |
| 图搜无结果 | 告诉用户 1688 找不到同款 |
| 利润率过低 | 展示数据让用户决定 |

## 行为约束

- **不要**在用户没说"提交/上架"时自动提交 Worker
- **不要**替用户决定利润率是否可接受
- **不要**跳过 check 直接执行业务操作
- **不要**修改 Skill 的 Python 代码
- **不要**向用户泄漏 Worker 内部实现
- **不要**多次问用户同一个凭证
- **不要**自己探索项目结构，按本文档操作

## 参考文件

| 文件 | 什么时候读 |
|------|-----------|
| `envelope_example.json` | 需要了解完整信封结构时 |
| `field_mapping.md` | 需要了解 1688/Ozon 字段映射时 |
