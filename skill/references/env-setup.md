# 环境准备（首次使用）

## 安装依赖

```bash
cd skill && python3 -m pip install -r requirements.txt
```

## 获取凭证

| 凭证 | 用途 | 获取方式 |
|------|------|----------|
| MXOU_TOKEN | 云端 AI 服务密钥 | 自动从 `~/.pounding/config.json` 读取（pounding 桌面端用户无需手动设置）。没有则向用户索取。 |
| 1688 AK | 1688 商品搜索 | 浏览器打开 https://clawhub.1688.com 登录后复制 |
| Ozon Client ID + API Key | Ozon API | Ozon 卖家后台 → 设置 → API 密钥 |

三个凭证一次性问完用户。MXOU_TOKEN 自动读到了就跳过。

```bash
python3 scripts/cli.py set_token --token <MXOU_TOKEN>
python3 scripts/cli.py set_ak --ak <1688_AK>
python3 scripts/cli.py set_store --name "主店铺" --client-id <CLIENT_ID> --api-key <API_KEY>
```

## 验证配置

```bash
python3 scripts/cli.py check
```

全部 ✅ 后方可执行业务操作。如有 ❌，按提示修复。

**首次使用（v0.28.6）**：`check` 会启动工具专用 Chrome（**独立 profile，完全不影响日常浏览器**，
也不会杀/重启用户自己的 Chrome），未登录 1688 时自动打开登录页 + Ozon Seller 卖家后台
（discover 运营指标需要），交互环境等待登录后按 Enter 继续。
**登录状态保存在工具独立 profile**（`data/browser/`，更新时保留），一次性登录长期有效；
命令结束自动关闭工具 Chrome（用完即关，不累积窗口、不常驻）。

## check 失败排查表

| ❌ 项 | 原因 | 修复 |
|---|---|---|
| Chrome 未安装 | 系统无 Google Chrome | 安装 Google Chrome（工具自动启动，无需手动配置） |
| Chrome 版本过旧 | Chrome < 100 | 升级 Chrome 到最新版 |
| 1688 AK 无效 | AK 过期或未配置 | `python3 scripts/cli.py get_ak`（自动获取）或 `set_ak` 手动设置 |
| 1688 未登录 | Chrome 中未登录 1688 | 在 Chrome 打开 1688.com 登录（工具会提示） |
| Ozon 店铺未配置 | `data/config/stores.json` 无店铺 | `python3 scripts/cli.py set_store --name 主店铺 --client-id xxx --api-key xxx` |
| MXOU_TOKEN 无效 | token 过期或未配置 | 向用户索取新 token：`python3 scripts/cli.py set_token --token <token>` |
| Worker 不可达 | 网络问题或 Worker 宕机 | 检查网络；`curl -s https://worker.mxou.cn/health` 确认服务状态 |

## 环境要求

- Python ≥ 3.12（必须）
- Google Chrome（工具自动启动，无需手动打开）

## data/ 目录语义（防误删）

| 路径 | 用途 | 可否删除 |
|---|---|---|
| `data/config/` | 凭证（stores.json / token / ak） | ❌ **绝对不能删**（删了要重新配置全部凭证） |
| `data/discovery/` | discover 选品结果落盘 | 可清理旧文件 |
| `data/logs/` | 运行日志 | 可清理旧文件 |
| `data/cache/` | 磁盘缓存（TTL 自动过期） | 可清理 |
| `wave*.txt` / `urls_*.txt` / `test_run_*` | 测试遗留文件 | 可删除 |
