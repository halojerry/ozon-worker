# Worker 云端部署指南

## 架构总览

```
用户本地 (Skill)                         云端服务器
┌──────────────────────┐               ┌─────────────────────────────┐
│ Chrome 浏览器        │               │ Docker Compose              │
│ ├─ 1688 登录态       │   HTTPS       │ ├─ PostgreSQL (5432→5433)   │
│ ├─ Ozon 登录态       │ ──────────→   │ ├─ Worker (5000→8080)      │
│ └─ Skill 脚本        │  POST /api/   │ └─ Nginx (443→8080)        │
│                      │  v1/submit    │                             │
│ 抓取数据 + 组装信封   │               │ 接收信封 → 执行上架管线      │
└──────────────────────┘               └─────────────────────────────┘
```

## 服务器要求

| 项目 | 最低要求 | 推荐 |
|------|---------|------|
| CPU | 2 核 | 4 核 |
| 内存 | 2 GB | 4 GB |
| 磁盘 | 20 GB | 40 GB |
| 系统 | Ubuntu 22.04 / Debian 12 | Ubuntu 22.04 |
| 网络 | 可访问外网 | 固定公网 IP |

## 第一步：服务器初始化

### 1.1 安装 Docker

```bash
# Ubuntu/Debian
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
newgrp docker

# 验证
docker --version
docker compose version
```

### 1.2 安装 Git

```bash
sudo apt update && sudo apt install -y git
```

### 1.3 配置防火墙

```bash
# 开放 8080 端口（Worker API）
sudo ufw allow 8080/tcp

# 如果要用 Nginx 反向代理，开放 80/443
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp

sudo ufw enable
```

## 第二步：部署 Worker

### 2.1 克隆代码

```bash
cd /opt
git clone https://github.com/halojerry/ozon-worker.git
cd ozon-worker
```

### 2.2 配置环境变量

```bash
cd deploy
cp .env.example .env
```

编辑 `.env` 文件：

```bash
# ========== 必填 ==========

# PostgreSQL 密码（改成你自己的强密码）
POSTGRES_PASSWORD=your_strong_password_here

# PostgreSQL 连接串（密码要和上面一致）
PGDATABASE_URL=postgresql://postgres:your_strong_password_here@postgres:5432/ozon

# Supabase 鉴权（用于 token 验证）
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your_supabase_service_role_key

# MXOU 生图 API Key
GRSAI_API_KEY=your_grsai_api_key

# ========== 可选 ==========

# 并发数（默认10，根据服务器配置调整）
MAX_CONCURRENT=10

# 每分钟限流（默认10）
RATE_LIMIT_PER_MINUTE=10

# 日志级别
LOG_LEVEL=INFO
LOG_FORMAT=json
```

### 2.3 一键部署

```bash
bash deploy.sh
```

部署脚本会自动：
1. 检查 Docker 环境
2. 构建 Worker 镜像
3. 启动 PostgreSQL + Worker
4. 初始化数据库（类目树、物流费率）
5. 健康检查

部署成功后会显示：
```
✅ 部署完成！
  Worker API: http://localhost:8080
  Health: http://localhost:8080/health
  Docs: http://localhost:8080/docs
```

### 2.4 验证部署

```bash
# 健康检查
curl http://localhost:8080/health

# 查看日志
docker compose logs -f worker

# 查看任务统计
curl http://localhost:8080/api/v1/task_statistics
```

## 第三步：配置 Nginx 反向代理（推荐）

### 3.1 安装 Nginx

```bash
sudo apt install -y nginx
```

### 3.2 配置 Nginx

```bash
sudo nano /etc/nginx/sites-available/ozon-worker
```

写入：

```nginx
server {
    listen 80;
    server_name your-domain.com;  # 改成你的域名或IP

    client_max_body_size 50m;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 300s;
        proxy_connect_timeout 75s;
    }
}
```

启用配置：

```bash
sudo ln -s /etc/nginx/sites-available/ozon-worker /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx
```

### 3.3 配置 HTTPS（推荐）

```bash
# 安装 Certbot
sudo apt install -y certbot python3-certbot-nginx

# 申请证书（需要域名）
sudo certbot --nginx -d your-domain.com

# 自动续期
sudo certbot renew --dry-run
```

## 第四步：配置 Skill 连接云端 Worker

在用户本地的 `.env` 文件中设置：

```bash
# Worker 地址（选一个）
WORKER_URL=https://your-domain.com        # 如果有域名 + HTTPS
WORKER_URL=http://your-server-ip:8080     # 如果直接 IP 访问
```

验证连接：

```bash
cd skill/
python3 scripts/cli.py check
```

应该显示：
```
🌐 Worker (https://your-domain.com):
  ✅ Service is running (DB: connected)
```

## 第五步：提交任务测试

```bash
# 提交一个测试任务
curl -X POST https://your-domain.com/api/v1/submit_task \
  -H "Content-Type: application/json" \
  -d '{
    "token": "your_supabase_token",
    "ozon_client_id": "5371047",
    "ozon_api_key": "your_ozon_api_key",
    "envelope": {
      "draft": { "item_id": "test", "title": "测试商品" },
      "source": {},
      "extensions": {}
    }
  }'
```

## 日常运维

### 更新 Worker

```bash
cd /opt/ozon-worker/deploy
bash update.sh
```

### 查看日志

```bash
# 实时日志
docker compose logs -f worker

# 最近100行
docker compose logs --tail 100 worker

# 搜索错误
docker compose logs worker 2>&1 | grep -i error
```

### 重启服务

```bash
# 重启 Worker
docker compose restart worker

# 重启全部（含数据库）
docker compose down && docker compose up -d
```

### 数据库备份

```bash
# 备份
docker compose exec postgres pg_dump -U postgres ozon > backup_$(date +%Y%m%d).sql

# 恢复
cat backup_20260724.sql | docker compose exec -T postgres psql -U postgres ozon
```

### 查看任务状态

```bash
# 所有任务统计
curl http://localhost:8080/api/v1/task_statistics

# 查看特定任务
curl http://localhost:8080/api/v1/task_status/{task_id}
```

## 故障排查

### Worker 启动失败

```bash
# 查看日志
docker compose logs worker

# 常见原因：
# 1. PostgreSQL 未就绪 → 等几秒再试
# 2. 数据库连接失败 → 检查 PGDATABASE_URL
# 3. 端口占用 → 改 docker-compose.yml 端口映射
```

### 数据库连接超时

```bash
# 检查 PostgreSQL 状态
docker compose exec postgres pg_isready

# 重启 PostgreSQL
docker compose restart postgres
```

### 内存不足

```bash
# 查看资源使用
docker stats

# 减少并发数
# 编辑 .env: MAX_CONCURRENT=5
docker compose restart worker
```

### 端口被占用

```bash
# 查看端口占用
sudo lsof -i :8080
sudo lsof -i :5433

# 修改 docker-compose.yml 端口映射
# 如改为 8081:5000
```

## 安全建议

1. **不要暴露 PostgreSQL 端口**：已默认绑定 `127.0.0.1:5433`
2. **使用强密码**：`POSTGRES_PASSWORD` 和 Supabase Key
3. **启用 HTTPS**：防止 token 明文传输
4. **限制访问**：用防火墙只允许必要端口
5. **定期备份数据库**

## 配置参考

### docker-compose.yml 关键配置

```yaml
services:
  postgres:
    image: postgres:16-alpine
    ports:
      - "127.0.0.1:5433:5432"  # 只绑定本地
    volumes:
      - pgdata:/var/lib/postgresql/data

  worker:
    build: ../worker
    ports:
      - "8080:5000"  # 对外暴露
    depends_on:
      postgres:
        condition: service_healthy
    env_file: .env
```

### Worker 端口映射

| 服务 | 容器端口 | 宿主端口 | 绑定地址 |
|------|---------|---------|---------|
| PostgreSQL | 5432 | 5433 | 127.0.0.1（仅本地） |
| Worker | 5000 | 8080 | 0.0.0.0（对外） |

### API 端点

| 功能 | 方法 | 路径 |
|------|------|------|
| 提交任务 | POST | `/api/v1/submit_task` |
| 查询状态 | GET | `/api/v1/task_status/{id}` |
| 取消任务 | POST | `/api/v1/cancel_task/{id}` |
| 健康检查 | GET | `/api/v1/health` |
| 任务统计 | GET | `/api/v1/task_statistics` |
| Swagger | GET | `/api/v1/docs` |
