---
title: minisign 信任根落地登记（COS 升级链 + skill 更新链）
purpose: T32 cicd-H2 遗留的 7 项人工待办一次落地：keypair 生成、公钥入库、CI pin 补算、签名步骤接线、真机交叉验证
date: 2026-09-23
owner: docs-gov
status: active
---

# minisign 信任根登记 — 2026-09-23

## 密钥信息

| 项 | 值 |
|---|---|
| keynum（8B 指纹） | `dfdb61109c044e23`（minisign 显示为 `234E049C1061DBDF`） |
| 公钥 printline（-P 形态） | `RWTf22EQnAROI7lY39Cx8wo0BtrPA9w55wBTqTAE17AKf7WIqqS/6rYq` |
| 生成 | 2026-09-23，`minisign 0.12`（brew），**空口令**（CI 非交互签名的前提） |
| 公钥 | `deploy/cos-update.pub`（本仓库，随包分发） |
| 私钥 | `~/.ozon-minisign/cos-update.key`（生成机，0600）→ GitHub secret `COS_UPDATE_SIGN_KEY` |
| 用途 | cd.yml 部署包 manifest 签名 + build-skill.yml skill manifest 签名（同一信任根，两条分发链共用） |

## ⚠️ 用户剩余人工步骤（合并后）

1. **离线冷备私钥**：把 `~/.ozon-minisign/cos-update.key` 复制到离线介质（密码管理器/加密 U 盘）。
   生成机磁盘损坏 + GitHub secret 丢失 = 信任根灭失，需换钥重发（所有旧验签客户端要同步公钥）。
2. **服务器首次带外放置**（每台服务器一次性）：旧版 cos-update.sh 尚无验签逻辑，首次跑新版会在
   §1.5 因缺 `deploy/cos-update.pub` + `deploy/verify_manifest.sh` exit 3。**这是有意的信任根设计，
   不能从升级包里自动解压这两个文件**（从待验包取信任根 = 没有信任根）。手动三件：
   ① 服务器装 minisign（`apt install minisign` 或官方静态二进制——verify_manifest.sh 依赖
   `command -v minisign`，缺失 exit 2）；② `scp deploy/cos-update.pub deploy/verify_manifest.sh
   <server>:<安装目录>/deploy/`；③ 重跑 cos-update.sh。
3. 轮换预案：换 keypair → 新公钥入库 + secret 更新 → 服务器/客户端（updater PROD_PUBKEY 硬编码）
   随下一版更新接管——过渡期旧客户端验新 manifest 会失败，需先发含新公钥的版本再切换签名钥。

## 落地过程中发现并修复的缺陷（全部实测）

1. **cd.yml MINISIGN_URL 双错**：release tag 是 `0.11`（无 v 前缀）、资产名是
   `minisign-0.11-linux.tar.gz`（无 `-amd64`）——原 URL 404，CI 会在下载步骤失败。
2. **cd.yml 二进制选取会抓错架构**：包内 `minisign-linux/{aarch64,x86_64}/` 双架构且 aarch64
   在 tar 条目序在前，`find | head -1` 在 amd64 runner 上装 ARM 二进制 → 改显式 x86_64 路径。
3. **空口令私钥下 `minisign -S` 仍读一次口令**：CI stdin 关闭直接挂（本地 0.12 复现）→
   cd.yml / build-skill.yml / sign_cache_hashes.sh 三处统一 `printf '\n' |` 喂空行。
4. **skill 纯 Python 验签器算法字/预哈希双缺陷（链路级，真缺陷）**：minisign 签名文件的
   算法字是**签名模式标记**——`Ed`=纯 Ed25519（legacy，`-l` 旗标）、`ED`=预哈希（**0.11 与
   0.12 的 `-S` 默认**，0.11 源码 `int hashed = 1` 实证）。预哈希构造 = **无键 BLAKE2b-64
   摘要作为消息做纯 Ed25519**（libsodium `crypto_generichash`，0.11 源码 `message_load_hashed`
   实证；不是 SHA-512、不是 RFC 8032ph 的 dom2）。旧解析器只认 `Ed` + 只有纯方程——
   **CI（0.11 默认预哈希）产出的真实签名会被一律拒绝，首个发版就会炸**（RFC 8032 向量
   测试用手搓 blob 掩盖；本日真机交叉验证抓出并修复：Ed/ED 双认 + BLAKE2b 分支）。
5. **build-skill.yml 零签名步骤**：updater.PROD_PUBKEY 填入后 skill manifest 无 .sig 会被
   fail-closed 拦死一切更新——已加与 cd.yml 同款的 install(pinned)+sign+自检+上传 .sig 步骤。

## 验证记录（2026-09-23，本地 minisign 0.12 + 真实 keypair）

- `minisign -S`（空口令管道）签名 → `minisign -V` 验签通过 ✓
- 同一签名/公钥喂 skill 纯 Python `verify_minisign_signature`：通过 ✓；篡改消息/跨公钥：拒 ✓
- `skill/tests/test_updater_manifest_sig_v076.py` 新增真机交叉验证用例（无 minisign 环境自动 skip）
- `worker/tests/test_cos_update_verify_v076.py` 行为矩阵（真 minisign 路径）首次在本机全量执行
  （Task 32 DONE_WITH_CONCERNS 的遗留就此闭环）
- minisign 0.11 linux tarball sha256 实算：`f0a0954413df8531befed169e447a66da6868d79052ed7e892e50a4291af7ae0`
