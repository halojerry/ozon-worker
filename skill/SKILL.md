---
name: pounding-ozon-probe
version: 0.2.0
description: >
  Ozon 上架工具：1688 选品、Ozon 跟卖、以图搜款、批量上架。
  当用户要上架商品到 Ozon、从 1688 选品、跟卖竞品、跨境电商铺货时触发。
---

# pounding-ozon-probe

Skill 端工具集，负责**数据抓取 + 信封组装 + 提交 Worker**。

## 环境要求

- **Python 3.12**（必须，二进制模块编译版本）
- **Google Chrome**（必须，CDP 抓取依赖）
- **pip 依赖**：`pip3.12 install -r requirements.txt`

## 快速开始

```bash
# 1. 安装依赖
pip3.12 install -r requirements.txt

# 2. 首次配置（按顺序执行）
python3.12 scripts/cli.py check                    # 诊断环境 + 自动启动 Chrome
python3.12 scripts/cli.py get_ak                   # 自动获取 1688 AK
python3.12 scripts/cli.py set_token --token <token> # 设置 MXOU_TOKEN
python3.12 scripts/cli.py set_store --name "主店铺" --client-id <ID> --api-key <KEY>

# 3. 上架商品
python3.12 scripts/cli.py graph --url "https://detail.1688.com/offer/xxx.html" --store "主店铺"
python3.12 scripts/cli.py follow --ozon-url "https://www.ozon.ru/product/xxx/" --store "主店铺"

# 4. 批量处理
python3.12 scripts/batch_test.py --urls-file urls.txt --submit
```

## 多店铺管理

支持同时管理多个 Ozon 店铺，每个店铺独立的 client_id + api_key。

```bash
# 配置店铺
python3.12 scripts/cli.py set_store --name "主店铺" --client-id 5371047 --api-key xxx
python3.12 scripts/cli.py set_store --name "测试店铺" --client-id 1234567 --api-key yyy

# 查看所有店铺
python3.12 scripts/cli.py list_stores

# 使用指定店铺上架
python3.12 scripts/cli.py graph --url <1688 URL> --store "主店铺"
python3.12 scripts/cli.py follow --ozon-url <Ozon URL> --store "测试店铺"
```

不指定 `--store` 时使用默认店铺（第一个配置的店铺）。

## 凭证配置

所有凭证存储在 `data/config/` 目录（Skill 自有配置，不依赖外部文件）。

| 凭证 | 获取方式 | 命令 |
|------|---------|------|
| 1688 AK | 浏览器自动获取 | `get_ak`（过期自动刷新） |
| MXOU_TOKEN | 自动读取或手动设置 | `set_token --token <token>` |
| Ozon 店铺 | 手动配置（支持多店铺） | `set_store --name <名> --client-id <ID> --api-key <KEY>` |

## Chrome 浏览器管理

**用户零配置**：Skill 自动启动 Chrome 并保留登录态。

- 自动检测系统（macOS / Windows / Linux）
- 使用用户默认 Chrome profile（保留 1688 / Ozon 登录态）
- 自动添加 `--remote-allow-origins=*`（CDP 需要）
- 首次使用需在 Chrome 中登录 1688 和访问 Ozon

```bash
python3.12 scripts/cli.py check  # 自动启动 Chrome + 检测所有状态
```

## 命令详解

### check — 环境检查

```bash
python3.12 scripts/cli.py check
```

自动检测并修复：Chrome 安装、CDP 启动、1688 登录态、Ozon DataDome、凭证状态、Worker 连通性、Ozon API 认证。

### graph — 1688 选品上架

```bash
python3.12 scripts/cli.py graph \
  --url "https://detail.1688.com/offer/xxx.html" \
  --store "主店铺" \
  --category-query "俄语类目词"
```

流程：CDP 抓取 1688 → 组装信封 → 提交 Worker 全流程上架。

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

流程：CDP 抓取 Ozon 竞品 → 图搜 1688 同款 → 组装信封 → 提交 Worker 跟卖管线。

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

通过 Chrome 浏览器自动获取 1688 AK，保存到 `data/config/settings.json`。AK 过期时自动刷新。

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
1688 URL → CDP 抓取 → 组装信封 → Worker 全流程上架
```

### 管线 B：Ozon 跟卖

```
Ozon URL → CDP 抓取 → 图搜 1688 → 组装信封 → Worker 跟卖管线
```

信封结构参考：`envelope_example.json`
字段映射参考：`field_mapping.md`

## 已知限制

- Chrome 必须运行（CDP 依赖本地浏览器）
- 首次使用需手动登录 1688 和访问 Ozon（建立登录态 / DataDome 信任）
- 以图搜款准确率 ~100%，但部分小众品类可能匹配不到
- Ozon 不提供重量 / 尺寸，从 1688 获取
- Worker 地址已内置（`https://worker.mxou.cn`），无需配置
