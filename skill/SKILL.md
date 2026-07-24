---
name: pounding-ozon-probe
version: 0.3.0
description: >
  Ozon 上架工具：1688 选品、Ozon 跟卖、以图搜款、批量上架。
  当用户要上架商品到 Ozon、从 1688 选品、跟卖竞品、跨境电商铺货时触发。
---

# pounding-ozon-probe

Skill 端工具集，负责**数据抓取 + 信封组装 + 提交云端 Worker**。

## ⚠️ 首次使用必须配置

在执行任何操作前，必须完成以下配置。缺少任何一项，Skill 会拒绝执行并提示配置方法。

**请严格按顺序执行，不要跳过任何步骤。**

### 第一步：安装依赖

```bash
pip3.12 install -r requirements.txt
```

### 第二步：获取并设置 MXOU_TOKEN

MXOU_TOKEN 是云端 AI 服务的认证密钥，所有操作都需要它。

1. 访问 https://api.mxou.cn 注册并获取 API Token
2. 设置到本地：

```bash
python3.12 scripts/cli.py set_token --token <你的MXOU_TOKEN>
```

如果已安装 pounding 桌面端，会自动从 ~/.pounding/config.json 读取，无需手动设置。

首次设置后，Skill 会自动向云端验证 token 有效性、余额和激活状态。验证结果缓存 24 小时，期间无需重复验证。

### 第三步：获取 1688 AK

1688 AK 用于商品搜索和数据获取。

```bash
# 方式 1：自动获取（需要 Chrome 浏览器，30 秒超时）
python3.12 scripts/cli.py get_ak

# 方式 2：手动获取（推荐）
# 浏览器打开 https://clawhub.1688.com → 登录 → 复制 AK
python3.12 scripts/cli.py set_ak --ak <你的AK>
```

AK 过期时会自动尝试重新获取。失败则提示手动操作。

### 第四步：配置 Ozon 店铺

从 Ozon 卖家后台获取凭证：设置 → API 密钥

```bash
# 配置店铺（支持多店铺）
python3.12 scripts/cli.py set_store --name "主店铺" --client-id <ID> --api-key <KEY>

# 查看已配置的店铺
python3.12 scripts/cli.py list_stores
```

### 第五步：验证配置

```bash
python3.12 scripts/cli.py check
```

全部 ✅ 后方可执行上架操作。

## 环境要求

- **Python 3.12**（必须，二进制模块编译版本）
- **Google Chrome**（必须，CDP 浏览器抓取依赖）

## 多店铺管理

支持同时管理多个 Ozon 店铺，每个店铺独立的 client_id + api_key。

```bash
# 配置多个店铺
python3.12 scripts/cli.py set_store --name "主店铺" --client-id 5371047 --api-key xxx
python3.12 scripts/cli.py set_store --name "测试店铺" --client-id 1234567 --api-key yyy

# 查看所有店铺
python3.12 scripts/cli.py list_stores

# 使用指定店铺上架
python3.12 scripts/cli.py graph --url <1688 URL> --store "主店铺"
python3.12 scripts/cli.py follow --ozon-url <Ozon URL> --store "测试店铺"
```

不指定 `--store` 时使用默认店铺（第一个配置的店铺）。

## 命令详解

### check — 环境检查

```bash
python3.12 scripts/cli.py check
```

自动检测：Chrome 安装、CDP 启动、1688 登录态、Ozon DataDome、凭证状态、Worker 连通性、Ozon API 认证。

### graph — 1688 选品上架

```bash
python3.12 scripts/cli.py graph \
  --url "https://detail.1688.com/offer/xxx.html" \
  --store "主店铺" \
  --category-query "俄语类目词"
```

流程：CDP 抓取 1688 → 组装信封 → 提交云端 Worker 全流程上架。

| 参数 | 说明 |
|------|------|
| `--url` | 1688 商品详情页 URL |
| `--store` | Ozon 店铺名称（可选） |
| `--category-query` | Ozon 类目关键词（俄语，可选） |
| `--retries` | CDP 重试次数（默认 3） |

### follow — Ozon 跟卖

```bash
python3.12 scripts/cli.py follow \
  --ozon-url "https://www.ozon.ru/product/xxx/" \
  --store "主店铺" \
  --auto-submit
```

流程：CDP 抓取 Ozon 竞品 → 图搜 1688 同款 → 组装信封 → 提交云端 Worker 跟卖管线。

| 参数 | 说明 |
|------|------|
| `--ozon-url` | Ozon 商品页 URL |
| `--store` | Ozon 店铺名称（可选） |
| `--auto-submit` | 自动提交到 Worker |

### image_search — 以图搜款

```bash
python3.12 scripts/cli.py image_search --image "https://example.com/image.jpg"
python3.12 scripts/cli.py image_search --image "/path/to/local/image.jpg"
```

CDP 网页版图搜（准确率 ~100%），支持 URL 和本地图片。

### get_ak — 获取 1688 AK

```bash
python3.12 scripts/cli.py get_ak
```

通过 Chrome 浏览器自动获取 1688 AK，保存到本地配置。30 秒超时，失败则提示手动获取。

### batch_test — 批量处理

```bash
python3.12 scripts/batch_test.py \
  --urls-file urls.txt \
  --submit
```

URL 文件自动识别 1688 / Ozon，混合输入即可。

| 参数 | 说明 |
|------|------|
| `--urls-file` | URL 列表文件 |
| `--submit` | 实际提交（不加则 dry-run） |
| `--start` | 起始索引 |
| `--limit` | 处理数量 |
| `--delay` | 每个 URL 间隔秒数（默认 3） |

## 两条管线

### 管线 A：1688 选品

```
1688 URL → CDP 抓取 → 组装信封 → 云端 Worker 全流程上架
```

### 管线 B：Ozon 跟卖

```
Ozon URL → CDP 抓取 → 图搜 1688 → 组装信封 → 云端 Worker 跟卖管线
```

信封结构参考：`envelope_example.json`
字段映射参考：`field_mapping.md`

## 凭证存储

所有凭证存储在 `data/config/` 目录（Skill 自有配置）：

| 文件 | 内容 |
|------|------|
| `stores.json` | Ozon 多店铺凭证（set_store 管理） |
| `settings.json` | MXOU_TOKEN + 1688 AK（set_token/set_ak 管理） |
| `auth_cache.json` | 云端鉴权缓存（自动管理，24 小时有效） |

## 鉴权机制

所有核心操作（搜索、抓取、提交）在执行前会自动向云端 Worker 验证凭证。

- 验证内容：token 有效性 + 账户余额 + 激活状态
- 验证结果缓存 24 小时，期间不重复请求云端
- 缓存过期后自动重新验证
- 验证失败（余额不足/未激活）会拒绝执行并提示原因

## 已知限制

- Chrome 必须运行（CDP 依赖本地浏览器）
- 首次使用需手动登录 1688 和访问 Ozon（建立登录态 / DataDome 信任）
- 以图搜款准确率 ~100%，但部分小众品类可能匹配不到
- Ozon 不提供重量 / 尺寸，从 1688 获取
- Worker 地址已内置（https://worker.mxou.cn），无需配置
