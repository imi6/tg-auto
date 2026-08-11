# Docker 部署指南 🐳

本文档介绍如何使用 Docker 部署 Telegram 监控系统。

---

## 📋 前置要求

- Docker Engine 20.10+
- Docker Compose 2.0+
- 至少 2GB 可用内存
- x86_64 架构服务器

### 安装 Docker（如未安装）

```bash
# CentOS/RHEL
sudo yum install -y docker docker-compose
sudo systemctl start docker
sudo systemctl enable docker

# Ubuntu/Debian
sudo apt update
sudo apt install -y docker.io docker-compose
sudo systemctl start docker
sudo systemctl enable docker

# 验证安装
docker --version
docker-compose --version
```

---

## 🚀 快速启动

### 1. 配置环境变量

首次部署需要配置 `.env` 文件：

```bash
# 如果 .env 文件不存在，复制模板
cp config.example.env .env

# 编辑配置文件
nano .env
```

**必填配置**:
```env
TG_API_ID=your_telegram_api_id
TG_API_HASH=your_telegram_api_hash
WEB_USERNAME=admin
WEB_PASSWORD=your_secure_password
```

**可选配置**:
```env
OPENAI_API_KEY=sk-xxx                    # AI 功能
EMAIL_USERNAME=your@email.com            # 邮件通知
EMAIL_PASSWORD=your_password
```

### 2. 构建并启动

```bash
# 构建镜像并启动容器
docker-compose up -d

# 查看启动日志
docker-compose logs -f

# 查看容器状态
docker-compose ps
```

### 3. 访问服务

打开浏览器访问：
- Web 界面: http://your-server-ip:8000
- 登录账号: 使用 `.env` 中配置的 `WEB_USERNAME` 和 `WEB_PASSWORD`

---

## 📦 常用命令

### 容器管理

```bash
# 启动服务
docker-compose up -d

# 停止服务
docker-compose stop

# 重启服务
docker-compose restart

# 停止并删除容器
docker-compose down

# 停止并删除容器、镜像、数据卷（⚠️ 慎用）
docker-compose down -v --rmi all
```

### 日志查看

```bash
# 查看实时日志
docker-compose logs -f

# 查看最近 100 行日志
docker-compose logs --tail=100

# 查看特定服务日志
docker-compose logs -f tg-monitor
```

### 容器操作

```bash
# 进入容器
docker-compose exec tg-monitor bash

# 在容器内执行命令
docker-compose exec tg-monitor python --version

# 查看容器资源使用
docker stats telegram-monitor
```

---

## 🔄 更新部署

### 更新代码

```bash
# 拉取最新代码
git pull

# 重新构建并启动
docker-compose up -d --build

# 或者分步操作
docker-compose build
docker-compose up -d
```

### 更新配置

```bash
# 修改 .env 文件后
nano .env

# 重启容器使配置生效
docker-compose restart
```

---

## 💾 数据持久化

项目使用 Docker Volume 持久化以下数据：

```yaml
volumes:
  - ./data:/app/data              # 配置文件、账号信息
  - ./logs:/app/logs              # 应用日志
  - ./downloads:/app/downloads    # 下载的文件
  - ./sessions:/app/sessions      # Telegram session 文件
```

**⚠️ 重要**:
- 这些目录会在宿主机创建
- 删除容器不会删除这些数据
- 备份时只需备份这些目录

---

## 🔒 安全建议

### 1. 修改默认密码

```env
# .env 文件中设置强密码
WEB_USERNAME=your_admin_name
WEB_PASSWORD=your_strong_password_here
```

### 2. 限制网络访问

**仅本地访问**:
```yaml
ports:
  - "127.0.0.1:8000:8000"  # 只监听本地
```

**使用 Nginx 反向代理**:
```nginx
server {
    listen 80;
    server_name your-domain.com;
    
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

### 3. 文件权限

```bash
# 限制敏感文件权限
chmod 600 .env
chmod 600 sessions/*.session
```

---

## 📊 资源监控

### 查看容器资源使用

```bash
# 实时监控
docker stats telegram-monitor

# 输出示例
CONTAINER ID   NAME               CPU %   MEM USAGE / LIMIT   MEM %
abc123def456   telegram-monitor   2.5%    450MiB / 2GiB      22.5%
```

### 调整资源限制

编辑 `docker-compose.yml`:

```yaml
deploy:
  resources:
    limits:
      cpus: '4.0'      # 最多 4 核
      memory: 4G       # 最多 4GB
```

然后重启：
```bash
docker-compose up -d
```

---

## 🛠️ 故障排查

### 容器无法启动

```bash
# 查看详细错误日志
docker-compose logs tg-monitor

# 检查配置文件
docker-compose config

# 验证环境变量
docker-compose exec tg-monitor env | grep TG_
```

### 端口被占用

```bash
# 检查端口占用
netstat -tulpn | grep 8000
# 或
lsof -i :8000

# 修改端口映射
# 编辑 docker-compose.yml
ports:
  - "8080:8000"  # 改为 8080
```

### 权限问题

```bash
# 容器内文件权限问题
docker-compose exec tg-monitor ls -la /app/data

# 修复权限
sudo chown -R 1000:1000 data/ logs/ downloads/ sessions/
```

### 内存不足

```bash
# 查看系统内存
free -h

# 增加 swap（临时方案）
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
```

---

## 🌐 生产环境部署

### 使用生产配置

```bash
# 使用生产环境配置启动
docker-compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

### 配置自动启动

```bash
# Docker 服务开机自启
sudo systemctl enable docker

# 容器随 Docker 自启（docker-compose.yml 中已配置 restart: always）
```

### 配置日志轮转

日志已在 `docker-compose.yml` 中配置自动轮转：

```yaml
logging:
  driver: "json-file"
  options:
    max-size: "10m"   # 单文件最大 10MB
    max-file: "3"     # 保留 3 个文件
```

---

## 📈 性能优化

### 1. 使用镜像加速

```bash
# 配置 Docker 镜像源（国内服务器）
sudo mkdir -p /etc/docker
sudo tee /etc/docker/daemon.json <<-'EOF'
{
  "registry-mirrors": [
    "https://docker.mirrors.ustc.edu.cn",
    "https://hub-mirror.c.163.com"
  ]
}
EOF

sudo systemctl daemon-reload
sudo systemctl restart docker
```

### 2. 构建优化

```dockerfile
# Dockerfile 中已使用清华源加速
RUN pip install --no-cache-dir -r requirements.txt \
    -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### 3. 多阶段构建（可选优化）

如需进一步减小镜像体积，可使用多阶段构建，但当前配置已经足够精简。

---

## 🔄 备份与恢复

### 备份数据

```bash
# 创建备份目录
mkdir -p ~/tg-monitor-backup

# 备份数据
tar -czf ~/tg-monitor-backup/backup-$(date +%Y%m%d).tar.gz \
    data/ logs/ sessions/ .env

# 自动备份脚本（可加入 crontab）
#!/bin/bash
BACKUP_DIR=~/tg-monitor-backup
DATE=$(date +%Y%m%d_%H%M%S)
tar -czf $BACKUP_DIR/backup-$DATE.tar.gz data/ logs/ sessions/ .env
# 保留最近 7 天的备份
find $BACKUP_DIR -name "backup-*.tar.gz" -mtime +7 -delete
```

### 恢复数据

```bash
# 停止容器
docker-compose down

# 恢复数据
tar -xzf ~/tg-monitor-backup/backup-20260811.tar.gz

# 重新启动
docker-compose up -d
```

---

## 📞 技术支持

遇到问题？

1. 查看日志: `docker-compose logs -f`
2. 检查配置: `docker-compose config`
3. 查看健康状态: `docker-compose ps`
4. 提交 Issue: [GitHub Issues](https://github.com/djksps1/telegram-monitor/issues)

---

**祝部署顺利！** 🚀
