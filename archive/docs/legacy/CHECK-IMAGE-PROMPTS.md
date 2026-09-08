# 生图提示词「还在用旧的」排查清单

> 场景：改了 `worker/config/image_prompts.json`（v0.15 起热加载），但线上生图**仍用旧提示词**。

## 先排除代码层（本仓库已确认无问题）

- `worker/config/image_prompts.json`：**已是最新提示词**（commit `03ac7a7`，2026-08-05）
- `worker/src/utils/image_prompts.py`：每次现读磁盘**无缓存**，文件缺失/损坏才回退模块级默认
- 10 个生图节点全部走 `get_image_prompt()`，**无硬编码旧提示词**
- 模块级兜底 `_DEFAULT_PROMPTS` 也已是新提示词

**结论：问题在服务器部署状态，不在代码。**

## Sentry 铁证（为什么怀疑服务器）

线上 Sentry 上报的 `release=dev`、`environment=local`（1899 个事件全部如此）：

- `deploy.sh` 会注入 `VERSION=$(cat VERSION)` → 镜像 `APP_VERSION=0.25.0` → Sentry `release=0.25.0`
- `release=dev` 说明**线上容器不是从 0.25.0 构建的**（旧镜像 / 构建时没传 VERSION / 旧 compose）

## 服务器排查步骤（按序执行）

### 1. 确认服务器 config 文件内容

```bash
grep "创意营销风格" /opt/ozon-worker/worker/config/image_prompts.json
# 有输出 → 服务器文件已是新提示词；无输出 → 服务器没拉到新 commit，先 git pull
```

### 2. 确认容器挂了 config bind mount

```bash
docker inspect <worker容器名> --format '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{println}}{{end}}'
# 必须看到: /opt/ozon-worker/worker/config -> /app/config (ro)
# 没有 → 容器是旧 compose 起的（v0.15 之前的 compose 无挂载），改文件不生效
```

### 3. 确认镜像 tag / APP_VERSION

```bash
docker ps --format '{{.Image}} {{.Names}}'
# 镜像应为 ozon-worker:0.25.0（不是 :latest 或旧 tag）
docker exec <worker容器名> printenv APP_VERSION
# 应为 0.25.0（不是 dev）
```

### 4. 确认生图日志实际用的提示词（v0.26 新增日志）

```bash
docker logs <worker容器名> 2>&1 | grep "mxou 生图 POST" | tail -3
# 应看到 prompt='产品：xxx。生成该产品的电商营销主图...'（新提示词开头）
# 若看到旧提示词开头（"无其他品牌logo/水印..."）→ 容器在用镜像内 COPY 的旧 config
```

### 5. 不符合 → 重新部署

```bash
cd /opt/ozon-worker/deploy && bash deploy.sh   # 或 update.sh
# deploy.sh 自动注入 VERSION、挂载 config、重建镜像
```

## 一键验证命令（部署后）

```bash
# 生图日志确认实际 prompt（v0.26）
docker logs <worker容器名> 2>&1 | grep "mxou 生图 POST" | tail -3

# 容器内直接读加载器生效的提示词
docker exec <worker容器名> python -c \
  "from utils.image_prompts import get_image_prompt; print(get_image_prompt('main', title='测试')[:60])"
```

## 为什么生图失败不该怪提示词

Sentry 显示生图失败大头是 **grsai violation（×126）** 和 **nano-banana-fast 降级失败（×139）**——
与提示词新旧无关，是：
1. 旧版「轮询超时 → 降级重 POST」双倍计费（**v0.26 已修**：轮询超时不重试不降级）
2. violation 直接降级不重试（**v0.26 已修**：violation 有界重试 2 次）
3. 任务无限重跑重烧（**v0.26 已修**：stale 重置有界化 + 生图幂等缓存）
