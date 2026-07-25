---
name: pounding-ozon-probe
description: >
  Ozon 上架工具。当用户发送 1688 链接时直接上架，发送 Ozon 链接时直接跟卖。
  当用户说"帮我找蓝海产品""帮我选品"且没有给链接时，去 Ozon 中国站自动选品。
  支持批量上架、以图搜款。
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

Skill 需要以下凭证才能工作：

| 凭证 | 用途 | 怎么获取 |
|------|------|----------|
| MXOU_TOKEN | 云端 AI 服务密钥 | 访问 https://api.mxou.cn 注册获取（pounding 桌面端用户自动读取） |
| 1688 AK | 1688 商品搜索 | 浏览器打开 https://clawhub.1688.com 登录后复制 |
| Ozon Client ID | Ozon API | Ozon 卖家后台 → 设置 → API 密钥 |
| Ozon API Key | Ozon API | 同上 |

**MXOU_TOKEN**：Skill 会自动从 `~/.pounding/config.json` 读取。如果用户安装了 pounding 桌面端，无需手动设置。如果没有自动读到，向用户索取。

**1688 AK 和 Ozon 店铺凭证**：不会自动读取，必须向用户获取。

拿到凭证后执行：

```bash
python3.12 scripts/cli.py set_token --token <MXOU_TOKEN>
python3.12 scripts/cli.py set_ak --ak <1688_AK>
python3.12 scripts/cli.py set_store --name "主店铺" --client-id <CLIENT_ID> --api-key <API_KEY>
```

**重要**：三个凭证一次性问完用户，不要分多次问。MXOU_TOKEN 如果自动读到了就不用问。

### 第 3 步：验证配置

```bash
python3.12 scripts/cli.py check
```

全部 ✅ 后方可执行业务操作。如有 ❌，按提示修复后再继续。

### 环境要求

- Python 3.12（必须）
- Google Chrome（Skill 自动启动，用户无需手动打开）
- 网络连接（访问 1688、Ozon、Worker）

## ⚠️ 意图路由（最重要）

**先判断用户意图，再选管线。三种意图互斥，不要混用。**

```
用户输入
  ├─ 包含 Ozon URL？ ──→ 【管线B：跟卖上架】直接图搜1688 → 提交Worker
  ├─ 包含 1688 URL？ ──→ 【管线A：1688上架】直接抓取 → 提交Worker
  └─ 没有URL？       ──→ 【管线C：蓝海选品】去Ozon中国站爬取 → 蓝海评分 → 展示候选
```

**关键规则：**
- **用户给了 URL → 直接处理该 URL，不做蓝海评分**
- **用户没给 URL → 去 Ozon 中国站自动选品，用蓝海评分筛选**
- **不要在跟卖/上架流程中混入蓝海计算逻辑**
- **蓝海评分只用于"帮我在 Ozon 上找蓝海产品"这类无 URL 的选品场景**

**所有命令在 `skill/` 目录下执行，使用 `python3.12 scripts/cli.py`。**

## 管线 A：1688 上架（用户给了 1688 URL）

**触发条件**：用户消息中包含 `1688.com` 链接

```bash
python3.12 scripts/cli.py graph --url "https://detail.1688.com/offer/xxx.html" --store "主店铺"
```

自动完成：CDP 抓取 1688 → 组装信封 → 输出 JSON。

如果用户要求上架，把信封提交给 Worker。**不要做蓝海评分，不要去 Ozon 搜索。**

## 管线 B：Ozon 跟卖（用户给了 Ozon URL）

**触发条件**：用户消息中包含 `ozon.ru` 链接

```bash
python3.12 scripts/cli.py follow --ozon-url "https://www.ozon.ru/product/xxx/" --store "主店铺" --auto-submit
```

自动完成：CDP 抓取 Ozon → 图搜 1688 同款 → 组装信封（follow_sell=True）→ 提交 Worker。

**不要做蓝海评分。用户给了具体 Ozon URL，直接帮他跟卖上架。**

## 管线 C：蓝海选品（用户没给 URL）

**触发条件**：用户说"帮我找蓝海产品"、"帮我选品"、"帮我在 Ozon 上找产品"、给关键词但没给 URL

```bash
# 关键词选品（默认 50 个产品）
python3.12 scripts/cli.py discover --keyword "宠物用品"

# 导出 CSV
python3.12 scripts/cli.py discover --keyword "宠物用品" --export csv --output results.csv

# 用户确认后自动提交到 Worker
python3.12 scripts/cli.py discover --keyword "宠物用品" --auto-submit
```

discover 会去 **Ozon 中国站**（tovary-iz-kitaya）自动爬取产品，计算蓝海评分，展示候选列表。

**蓝海评分只在这个管线中使用。** 让用户确认后再提交，不要替用户决定。

## 以图搜款

```bash
python3.12 scripts/cli.py image_search --image "https://example.com/image.jpg"
```

## 批量处理

```bash
python3.12 scripts/batch_test.py --urls-file urls.txt --submit
```

URL 文件中混合 1688/Ozon 链接，自动识别。

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
- **不要**在用户给了 URL 时还去做蓝海评分（URL = 直接处理，无 URL = 蓝海选品）
- **不要**把蓝海评分逻辑混入跟卖/上架流程
- **每次操作前重新判断用户意图**，不要因为上下文中提过蓝海就默认用蓝海逻辑

## 参考文件

| 文件 | 什么时候读 |
|------|-----------|
| `envelope_example.json` | 需要了解完整信封结构时 |
| `field_mapping.md` | 需要了解 1688/Ozon 字段映射时 |
