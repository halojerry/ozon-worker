# SECRET-PURGE-RUNBOOK — 密钥出库操作手册（仓库 PUBLIC 应急）

> 2026-09-11 全历史扫描结论：8 组真实密钥曾入 git 历史，仓库 PUBLIC 可被任意提取。
> 完整串清单在**本地** `/tmp/purge-strings.txt`（绝不入库——本手册只含指纹前缀）。
> 用户政策：「密钥不进源码库」（已落 docs/CONVENTIONS.md 密钥纪律节）。

## 第一步（最高优先，密钥持有者执行）：轮换

| # | 密钥 | 指纹前缀 | 轮换位置 |
|---|---|---|---|
| 1 | Supabase service_role JWT | `eyJhbGci…`（219 字符） | Supabase Dashboard → Settings → API → 重置 service_role |
| 2 | Ozon 店铺 4718259 api_key | `cd1d0a10-` | Ozon Seller → API 密钥页作废重建 + 更新 stores.json/credential |
| 3 | Ozon 店铺 5381204 api_key | `0b4d15cf-` | 同上 |
| 4 | Ozon 店铺 5371047 api_key | `db64d282-` | 同上 |
| 4b | Ozon 店铺 key ×4（pipeline.json 内嵌，店铺号未知） | `16d3650e-`/`4bc7a919-`/`bcfd9c1c-`/`bd01d353-` | 对照卖家后台已建 key 列表排查并作废 |
| 5 | MXOU token ×4 变体 | `Ccpo3ziB…`(JyL/LyL)/`ODyGgd9…`/`sk-2C9…` | api.mxou.cn 重置 key |
| 6 | GRSAI 生图 key | `sk-fbee388b` | grsai.dakka.com.cn 重置 |
| 7 | 1688 AK（base64） | `RmZTWVRi…` | 1688 开放平台重发 AK |
| 8 | aibuy mtop token | `6499814d` | 重新收割登录态（token 随登录轮换自动作废，低优先） |
| 9 | Sentry DSN key（低危客户端型） | `a2491a43` | 可选：Sentry 项目设置轮换 DSN |

**轮换完成前，历史重写只是卫生工程——旧值仍在公开历史里可用。**

## 第二步（owner 确认后执行）：git 历史重写

前置：`git-filter-repo` 已装于 skill/.venv314/bin；替换串已提取（/tmp/purge-strings.txt，10 条）。

```bash
# 0) 全新镜像 clone（不在现有工作树操作）
cd /tmp && git clone --mirror https://github.com/halojerry/ozon-worker.git ogw-purge && cd ogw-purge
# 1) 路径整删（这些目录/文件本不该入库）
git filter-repo --invert-paths --path deploy/.env \
  --path skill/data/ --path deploy/skill/data/ --force
# 2) 串替换（10 条真密钥 → ***REMOVED***）
git filter-repo --replace-text /tmp/purge-strings.txt --force
# 3) 验证：重写后全历史 gitleaks 零命中（仅剩测试夹具假值如需要一并清可加串）
gitleaks detect --log-opts="--all --no-merges" --redact -v
# 4) 推送前禁用按 tag 触发的三条工作流（防旧版本重部署）
gh workflow disable cd.yml -R halojerry/ozon-worker
gh workflow disable build-skill.yml -R halojerry/ozon-worker
gh workflow disable skill-distribute.yml -R halojerry/ozon-worker
# 5) 临时解锁分支保护 force push（gh api 改 protection 或 Dashboard 手动）→ 强推
git push --force --mirror    # 或逐 ref：dev/main/全部 tag
# 6) 恢复分支保护 + 重新 enable 工作流 + 核对 tag 指向
# 7) 所有本地 clone/worktree 重建（旧 hash 全失效）；
#    新 clone 后跑 worker 全量测试回归基线
```

## 第三步（重写后）：残留清理
- GitHub PR 页旧 diff 缓存（如 PR #13 移除密钥的 diff 反向显示旧值）只有 GitHub Support 能清：提工单附本清单指纹。
- 部署机/CI 缓存里的旧引用镜像无害（内容同源），无需处理。

## 防回潮（已随 S1 批落地）
- CI secret-scan 加全树扫描（--no-git）步骤
- worker/tests/test_no_secrets_in_tree.py：已知密钥指纹对 git 跟踪文件零命中断言
- docs/CONVENTIONS.md 密钥纪律节（env 传入/夹具假值三规则/轮换 SOP）
