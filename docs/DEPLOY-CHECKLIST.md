# Ozon Worker 服务器部署清单(v0.29.0+)

> 服务器**无法访问 GitHub** → 所有代码分发走 **COS**(cd.yml 打 tag 自动出包)。
> 本清单覆盖:首次部署 / 日常升级 / 回滚。三条命令搞定,以后发版无感。

## 架构

```
打 tag v0.29.x → CI 自动 → COS /ozon-worker/
  ├── ozon-worker-deploy-v0.29.x.tar.gz  (deploy/ + worker/ 源码)
  └── manifest.json                       (最新版本指针 + sha256)

服务器: bash deploy/update.sh
  → 读 manifest → 对比本地 VERSION → 下载 → sha256 校验 → 备份
  → 覆盖(.env 保留) → docker compose build → 优雅重建 → 健康检查 → 失败自动回滚
```

## 一、首次部署(约 15 分钟)

```bash
# 1. 准备目录 + 下载部署包(COS 公网, 无需凭证)
mkdir -p /opt/ozon && cd /opt/ozon
curl -O https://yss-1256275613.cos.ap-guangzhou.myqcloud.com/ozon-worker/manifest.json
# 从 manifest 拿 package 文件名, 或用最新版本号:
curl -O https://yss-1256275613.cos.ap-guangzhou.myqcloud.com/ozon-worker/ozon-worker-deploy-v0.29.0.tar.gz
tar -xzf ozon-worker-deploy-v0.29.0.tar.gz

# 2. 配置 .env(必填 PGDATABASE_URL; COS_* 可留默认)
cd /opt/ozon/deploy
cp .env.example .env
vim .env          # 填入 PGDATABASE_URL / SUPABASE_URL / SUPABASE_KEY 等

# 3. 一键部署(自动: 建镜像 → 启动 → 初始化数据 → 预热类目缓存)
bash deploy.sh
```

**如果服务器能访问 GitHub**(可选):`git clone https://github.com/halojerry/ozon-worker.git` 后直接跑 `bash deploy/deploy.sh`,效果相同(deploy.sh 检测到无 worker 源码也会自动走 COS)。

## 二、日常升级(以后每次发版,一条命令)

```bash
cd /opt/ozon/deploy
bash update.sh              # 升级到最新版
# bash update.sh v0.28.0   # 指定版本 / 回滚
```

脚本自动完成:manifest 检测新版本 → 下载 → sha256 校验(不符即停)→ 备份当前版到 `backups/` → 解压覆盖(**生产 .env 不覆盖**)→ 重建镜像 → 优雅关闭排空任务(最多 5 分钟)→ 健康检查 → **失败自动回滚**。

## 三、回滚

```bash
cd /opt/ozon/deploy
bash update.sh v0.28.0     # 回滚到指定版本(COS 有包即可)
# 或手动: 恢复 backups/v{旧版本}_{时间戳}/ 后 rebuild
```

## 四、运维速查

| 操作 | 命令 |
|------|------|
| 服务状态 | `docker compose ps` / `docker compose logs -f worker` |
| 健康检查 | `curl http://localhost:8080/api/v1/health` |
| 查看任务 | `curl http://localhost:8080/api/v1/task_statistics` |
| 手动重启 | `docker compose restart worker`(优雅关闭, 排空后停) |
| 备份位置 | `backups/v{旧版本}_{时间戳}/`(worker + deploy, 无 .env) |
| 查看当前版本 | `cat VERSION` |

## 五、安全约定

- **生产 `.env` 绝不覆盖**:cos-update.sh 打包时已排除 `.env`, 解压也不碰它
- **备份先行**:每次升级前自动备份, 回滚即恢复
- **sha256 校验**:下载包与 manifest 哈希不符直接中止, 不会用坏包覆盖
- **优雅关闭**:`stop_grace_period: 5m` + drain——升级不打断运行中的上架任务
