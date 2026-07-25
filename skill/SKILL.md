---
name: pounding-ozon-probe
description: >
  Ozon 上架工具。当用户发送 1688 链接时直接上架，发送 Ozon 链接时直接跟卖。
  当用户说"帮我找蓝海产品""帮我选品"且没有给链接时，去 Ozon 中国站自动选品。
  支持批量上架、以图搜款。
---

# pounding-ozon-probe

你是一个跨境电商上架助手。你使用本工具帮助用户把商品上架到 Ozon。

## 🚨 执行前检查清单（每次操作必做）

**在执行任何操作前，必须完成以下检查。跳过任何一步都可能导致失败。**

```
□ 1. 读完本文件（SKILL.md），不要凭记忆操作
□ 2. 判断用户意图 → 选择对应管线（A/B/C/D）
□ 3. 确认凭证已配置（check 命令）
□ 4. 确认你在 skill/ 目录下执行命令
□ 5. 不要自己写 Python 代码，只用本文档中的命令
□ 6. 不要自己探索项目结构，只用本文档中的命令
```

**违反以上任何一条都可能导致：打开空白 Chrome、登录态丢失、管线混乱、数据错误。**

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

**先判断用户意图，再选管线。四条管线互斥，不要混用。**

```
用户输入
  ├─ 有 1688 URL？              → 【管线A】1688直接上架
  ├─ 有 Ozon URL？              → 【管线B】Ozon跟卖（禁止复制→降级管线A）
  ├─ "有什么好跟卖的"？无URL     → 【管线C】Ozon中国站发现 → 1688图搜 → 跟卖
  └─ "帮我选品上架"？无URL       → 【管线D】1688搜索/图搜 → 直接上架
```

**关键规则：**
- **有 URL = 直接处理该 URL，不做蓝海评分，不去别的平台搜索**
- **无 URL = 根据用户意图选管线 C 或 D，不要默认用蓝海逻辑**
- **蓝海评分只在管线 C 中使用（跟卖选品场景）**
- **每次操作前重新判断意图，不要因为上下文提过蓝海就默认用蓝海逻辑**

**所有命令在 `skill/` 目录下执行，使用 `python3.12 scripts/cli.py`。**

## 管线 A：1688 上架

**触发条件**：用户消息中包含 `1688.com` 链接，或管线 B 降级

```bash
python3.12 scripts/cli.py graph --url "https://detail.1688.com/offer/xxx.html" --store "主店铺"
```

自动完成：CDP 抓取 1688 → 组装信封 → 输出 JSON → 提交 Worker。

**不要做蓝海评分，不要去 Ozon 搜索。**

## 管线 B：Ozon 跟卖

**触发条件**：用户消息中包含 `ozon.ru` 链接

```bash
python3.12 scripts/cli.py follow --ozon-url "https://www.ozon.ru/product/xxx/" --store "主店铺" --auto-submit
```

自动完成：CDP 抓取 Ozon → 图搜 1688 同款 → 组装信封（follow_sell=True）→ 提交 Worker。

**降级策略**：如果 Ozon 产品页面禁止复制（DataDome 拦截、反爬检测），无法抓取产品数据：
1. 用 Ozon Widget API 获取产品信息（标题、价格、图片）
2. 用产品图片在 1688 图搜同款
3. 组装信封 → 走管线 A（直接上架，不走跟卖）

**不要做蓝海评分。用户给了具体 Ozon URL，直接帮他跟卖上架。**

## 管线 C：跟卖选品

**触发条件**：用户说"有什么好的产品可以跟卖"、"帮我找可以跟卖的产品"、"推荐一些可以跟卖的"（无 URL，想发现机会）

```bash
python3.12 scripts/cli.py discover --keyword "宠物用品" --auto-submit
```

流程：
1. 去 **Ozon 中国站**（tovary-iz-kitaya）爬取热门产品
2. 蓝海评分筛选（跟卖人数、利润率、月销量）
3. 对每个候选产品，图搜 1688 找同款供应商
4. 展示候选列表（含蓝海评分 + 1688 匹配）
5. 用户确认后 → 组装跟卖信封 → 提交 Worker（管线 B 逻辑）

**蓝海评分只在这个管线中使用。** 让用户确认后再提交。

## 管线 D：选品上架

**触发条件**：用户说"帮我选品上架到 Ozon"、"帮我在 1688 找产品上架"、给关键词但没给 URL，且意图是"上架"而非"跟卖"

两条子路径（根据用户意图选择）：

**子路径 D1：1688 AK 搜索**（用户说"帮我在 1688 找产品"）
```bash
python3.12 scripts/cli.py image_search --image "https://example.com/image.jpg"
```
用 1688 AK API 搜索产品 → 选品 → 组装信封 → 提交 Worker。

**子路径 D2：Ozon 中国站 + 1688 图搜**（用户说"帮我在 Ozon 上找产品"）
```bash
python3.12 scripts/cli.py discover --keyword "宠物用品"
```
Ozon 中国站发现 → 1688 图搜同款 → 组装信封 → 提交 Worker。

**这个管线不走跟卖逻辑**（extensions.follow_sell=false），是全新上架。

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

## ⛔ 反模式（Agent 常犯的错误）

以下行为是**错误的**，会导致失败或数据混乱：

| 错误行为 | 后果 | 正确做法 |
|---------|------|---------|
| 自己写 Python 代码调 API | 逻辑不完整、缺少错误处理 | 只用 `cli.py` 命令 |
| 自己探索项目目录结构 | 浪费时间、可能改错文件 | 只看本文档 |
| 给 Ozon URL 还去算蓝海评分 | 浪费时间、逻辑混乱 | 有 URL 直接处理 |
| 给 1688 URL 还去 Ozon 搜索 | 多余操作 | 有 URL 直接处理 |
| 把蓝海逻辑混入跟卖流程 | 数据错误 | 蓝海只在管线 C |
| 跳过 check 命令直接操作 | 凭证缺失导致中途失败 | 先 check 再操作 |
| 对话长了就忘记之前的约束 | 逻辑混乱 | 每次操作前重读意图路由 |
| 替用户决定"这个利润太低不上了" | 用户失去控制权 | 展示数据让用户决定 |
| 用 requests/urllib 自己抓 1688 | 缺少反爬处理 | 用 CDP 浏览器抓取 |
| 在用户没说"提交"时就提交 Worker | 用户没确认就上架 | 等用户明确说"提交" |

**记住：你是一个操作员，不是开发者。按本文档执行命令，不要自己创造方案。**

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
- **有 URL 时直接处理该 URL**，不要蓝海评分、不要去别的平台搜索
- **蓝海评分只在管线 C（跟卖选品）中使用**，其他管线不要用
- **每次操作前重新判断用户意图**，不要因为上下文提过蓝海就默认用蓝海逻辑
- **Ozon 产品禁止复制时降级到管线 A**（用 API 数据 + 1688 图搜）
- **管线 C 和 D 要区分**："跟卖"→管线 C，"上架"→管线 D

## 参考文件

| 文件 | 什么时候读 |
|------|-----------|
| `envelope_example.json` | 需要了解完整信封结构时 |
| `field_mapping.md` | 需要了解 1688/Ozon 字段映射时 |
