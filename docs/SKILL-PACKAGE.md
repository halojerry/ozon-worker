# Skill 统一多平台包机制与稳定性清单

> 2026-08-01 · 与用户确认后整理

## 1. 核心结论：Skill 是「一个包全平台可用」

`compile.py` 生成的自包含 `skill/dist/` 就是**统一分发包**——用户只需下载一个包，
无需按平台下载对应版本。机制：

```
skill/dist/
└── scripts/
    └── lib/
        ├── _loader.py                    # 运行时平台感知加载器
        ├── _native/                      # 各平台编译二进制
        │   ├── darwin-arm64/             # macOS Apple Silicon (.so)
        │   ├── darwin-x86_64/            # macOS Intel (.so, CI 用 Rosetta2 交叉编译)
        │   ├── linux/                    # Linux (.so)
        │   └── win32/                    # Windows (.pyd)
        ├── xxx.py                        # 编译模块 → 生成 stub（指向 _native 对应平台二进制）
        └── yyy.py                        # 纯复制模块（直接 Python 源码）
```

- **运行时选平台**：`_loader.py`/stub 用 `platform.system() + platform.machine()`
  → 加载 `_native/{platform}/` 对应二进制，无需用户干预。
- **CI 构建**（`.github/workflows/build-skill.yml`）：4 平台矩阵（darwin-arm64 /
  darwin-x86_64 / linux / win32）各自编译 → 合并 `_native/` → 打成一个包发布。
- **架构兼容**：darwin-x86_64 在 macos-latest（Apple Silicon）上通过
  `ARCHFLAGS="-arch x86_64"` + 编译后目录改名实现（Rosetta 2 交叉编译）。

## 2. 二进制（编译保护源码）vs 非二进制清单

### 2.1 编译为 .so/.pyd（12 个）— 源码保护

| 模块 | 职责 | 平台 |
|------|------|------|
| `lib/ak_1688_client.py` | 1688 AK API 搜索/详情 | 全平台 |
| `lib/ak_callback.py` | 1688 AK 回调 | 全平台 |
| `lib/chrome_launcher.py` | Chrome 自动启动/CDP 端口 | 全平台 |
| `lib/config_store.py` | 凭证/店铺/定价参数管理 | 全平台 |
| `lib/image_preprocessor.py` | 图片预处理 | 全平台 |
| `lib/ozon_scraper.py` | Ozon 商品页 CDP 抓取 | 全平台 |
| `lib/ozon_image_search.py` | CDP 以图搜款 | 全平台 |
| `lib/reference_images.py` | 参考图选择 | 全平台 |
| `lib/ozon_discovery.py` | 选品发现引擎（蓝海评分） | 全平台 |
| `lib/ozon_api.py` | Ozon API 封装（类目搜索） | 全平台 |
| `cloud_probe.py` | 信封组装 + 管线编排 | 全平台 |
| `capabilities/browser_probe/stealth.py` | 反检测 stealth（稳定，保护价值最高） | 全平台 |

> ⚠️ 每次新增/修改以上模块后，需在 4 个平台各重跑一次 `python3.12 compile.py`。

### 2.2 纯复制（不编译）— 入口/基础设施/API 客户端/探针

| 类型 | 模块 | 说明 |
|------|------|------|
| 入口 | `cli.py`、`batch_test.py` | CLI 入口（无需保护） |
| 基础设施 | `lib/cdp_client.py` | 原生 CDP WebSocket（替代 Playwright） |
| 基础设施 | `lib/utils.py` | parse_price 等共享工具 |
| 基础设施 | `lib/cache.py` | 磁盘缓存（JSON+TTL+SHA256） |
| 基础设施 | `lib/task_paths.py`、`lib/logging_utils.py` | 任务路径/审计日志 |
| **自动更新** | `lib/updater.py` | **COS manifest 检测 + 下载/sha256/备份/回滚** |
| API 客户端 | `lib/ozon_seller.py` | Ozon Seller API（佣金/重量/品牌） |
| API 客户端 | `lib/ozon_widget.py` | Ozon Widget API（产品/跟卖/SKU） |
| API 客户端 | `lib/ozon_seller_analytics.py` | **运营指标借道**（Discover v2 新增） |
| **探针** | `capabilities/browser_probe/service.py` | **明文（2026-08-01 移回）**：改动最频繁，需本地快速迭代与可调试；历史 1e98bcd 踩过 stub 冲突 |
| 包结构 | `scripts/__init__.py`、`_const.py`、`_errors.py`、`lib/__init__.py`、`capabilities/__init__.py`、`capabilities/browser_probe/__init__.py` | import 必需 |

### 2.3 非 .py 分发内容

- `SKILL.md` / `envelope_example.json` / `field_mapping.md` / `requirements.txt` — 文档
- `VERSION` — **版本文件**（自动更新比对依据，updater.py 读取）
- `data/config/settings.json` / `stores.json` — **空模板**（不泄露凭证，用户自行配置）

## 2.5 自动更新机制（2026-08-01 新增）

分发渠道：**腾讯云 COS**（中国用户可直连，GitHub Releases 中国访问慢）。

```
发版（git tag v0.12.0）
  └─ GitHub Actions（build-skill.yml）
      ├─ 4 平台构建 → 合并 → 打包 tar.gz
      ├─ 计算 sha256 → 生成 manifest.json
      ├─ 上传 /skill/<包>.tar.gz + /manifest.json → COS（coscmd）
      └─ GitHub Release（保留）
用户本地 skill
  ├─ 每次命令静默检查 manifest（后台不阻塞，5s 超时，失败静默）
  ├─ 有新版 → 提示"运行 skill update 更新"
  └─ skill update → 下载 → sha256 校验 → 备份 → 覆盖 scripts/+文档+VERSION
     → 保留 data/（凭证/登录态/缓存）→ 失败自动回滚
```

**CI 配置依赖 GitHub Secrets**（发布前需配置）：
| Secret | 说明 |
|--------|------|
| `COS_SECRET_ID` | 腾讯云 SecretId |
| `COS_SECRET_KEY` | 腾讯云 SecretKey |
| `COS_BUCKET` | COS 桶名（如 `ozon-skill-1250000000`） |
| `COS_REGION` | COS 地域（如 `ap-guangzhou`） |
| `COS_MANIFEST_BASE_URL` | manifest 中 url 前缀（如 `https://ozon-skill-1250000000.cos.ap-guangzhou.myqcloud.com`） |

**用户侧配置**：`updater.py` 默认 manifest URL 为占位域名，用户首次装包后需设置
`SKILL_MANIFEST_URL` 环境变量指向真实 COS 域名（或后续把默认值改成真实域名后发一版）。

## 3. 稳定性保障清单（2026-08-01 审计）

### 3.1 已修复的稳定性问题

| # | 问题 | 修复 | 位置 |
|---|------|------|------|
| 1 | **`ozon_seller_analytics.py` 未进包**（Discover v2 新增模块遗漏）→ 用户拿到 dist 包跑 `discover` 运营指标会 ImportError | 加入 `AUX_FILES` 纯复制 | compile.py |
| 2 | AGENTS.md 编译清单过时（写 9 个，实际 13 个）| 更新为 13 个 + 纯复制清单 | AGENTS.md |
| 3 | chrome_launcher 裸 "chrome" 匹配误杀 Electron 进程 | 只匹配 google chrome/chromium + 排除 electron | chrome_launcher.py |
| 4 | Chrome 130+ 禁止默认 profile 远程调试 | cli/batch_test 统一独立 profile | cli.py/batch_test.py |

> ⚠️ **`audit_products.py` 为开发排查工具，不进 dist 包**（编译时排除，勿加入 COMPILE_FILES/AUX_FILES）。
> 同理 `bootstrap_update.py` 只在「旧包无 updater」升级场景单独分发（GitHub Release 附件）。

### 3.2 稳定性验证流程（每次改动后）

```bash
# 1. 完整编译（必须 Python 3.12）
cd skill && python3.12 compile.py          # 13 成功 0 失败

# 2. dist 导入验证（stub→.so 加载 + 纯复制模块）
cd dist && python3.12 -c "
import sys; sys.path.insert(0, '.')
import scripts.lib.ozon_discovery          # 编译模块
import scripts.lib.ozon_seller_analytics   # 纯复制（新增模块验证）
import scripts.cli                         # 入口
print('✅ dist 导入 OK')"

# 3. 本地端到端冒烟（用 dist 而非源码跑一次 check）
cd dist && python3.12 scripts/cli.py check
```

### 3.3 变更 checklist（新增/修改模块时）

- [ ] 新增模块 → 加入 `COMPILE_FILES`（要保护源码）或 `AUX_FILES`（纯复制）
- [ ] 修改编译模块 → 4 平台各重编译（CI 会自动做，本地验证 darwin-arm64 即可）
- [ ] 更新 AGENTS.md「源码保护」清单
- [ ] **新增/修改命令 → 同步 SKILL.md「速查表 → 意图路由 → 错误表」三处**（防文档与代码漂移）
- [ ] **SKILL.md frontmatter `version` 与 `skill/VERSION` 一致**（updater 收敛依据）
- [ ] 跑 3.2 稳定性验证流程
- [ ] dist 包发布走 CI（`git tag v*` → build-skill.yml 全平台构建 + Release）

## 4. 已知约束

- **Python 版本锁定 3.12**：`.so` 文件名含 cpython-312 tag，ABI 不兼容其他版本。
  用户环境必须是 Python 3.12。
- **darwin-x86_64**：CI 在 macos-latest（arm64）上交叉编译，需 ARCHFLAGS +
  目录改名两步，不可在本机直接跑。
- **编译失败不阻断**：compile.py 单个失败会继续（成功数<13 需人工检查），
  建议以 "13 成功, 0 失败" 为准。
