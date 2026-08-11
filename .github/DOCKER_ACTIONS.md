# GitHub Actions 自动构建 Docker 镜像

本项目已配置 GitHub Actions，可以自动构建并推送 Docker 镜像到 GitHub Container Registry。

---

## 🚀 自动构建触发条件

### 1. **推送到 main 分支**
```bash
git push origin main
```
自动构建并推送 `latest` 标签的镜像

### 2. **创建版本标签**
```bash
git tag v1.0.0
git push origin v1.0.0
```
自动构建并推送以下标签：
- `v1.0.0`
- `v1.0`
- `v1`
- `latest`

### 3. **Pull Request**
创建 PR 时会构建但不推送，用于测试

---

## 📦 使用构建好的镜像

### 镜像地址
```
ghcr.io/你的GitHub用户名/tg-auto:latest
```

### 拉取镜像
```bash
# 拉取最新版本
docker pull ghcr.io/你的GitHub用户名/tg-auto:latest

# 拉取指定版本
docker pull ghcr.io/你的GitHub用户名/tg-auto:v1.0.0
```

### 修改 docker-compose.yml

将 `build:` 配置改为 `image:`：

```yaml
services:
  tg-monitor:
    # 使用预构建镜像（替换为你的实际用户名）
    image: ghcr.io/你的GitHub用户名/tg-auto:latest
    
    # 或者使用本地构建（保持原样）
    # build:
    #   context: .
    #   dockerfile: Dockerfile
    
    container_name: telegram-monitor
    restart: always
    # ... 其他配置保持不变
```

---

## 🔐 镜像权限设置

### 公开镜像（推荐）

1. 进入你的 GitHub 仓库
2. 点击右侧 **Packages** → 找到 `tg-auto`
3. 点击 **Package settings**
4. 滚动到 **Danger Zone**
5. 点击 **Change visibility** → 选择 **Public**

这样任何人都可以拉取镜像，无需登录。

### 私有镜像

如果保持私有，拉取时需要登录：

```bash
# 生成 GitHub Personal Access Token (需要 read:packages 权限)
# https://github.com/settings/tokens

# 登录 GitHub Container Registry
echo "YOUR_TOKEN" | docker login ghcr.io -u YOUR_USERNAME --password-stdin

# 拉取镜像
docker pull ghcr.io/你的GitHub用户名/tg-auto:latest
```

---

## 🔍 查看构建状态

### 方法 1: GitHub Actions 页面
1. 进入你的仓库
2. 点击 **Actions** 标签
3. 查看最近的工作流运行记录

### 方法 2: README 徽章

在 README.md 中添加构建状态徽章：

```markdown
![Docker Build](https://github.com/你的用户名/tg-auto/actions/workflows/docker-build.yml/badge.svg)
```

---

## 📋 工作流详情

### 构建平台
- ✅ `linux/amd64` (x86_64)

### 构建缓存
- ✅ 使用 GitHub Actions Cache 加速构建
- ✅ 只构建变化的层

### 安全性
- ✅ 使用 `GITHUB_TOKEN` 自动认证
- ✅ 无需额外配置 Secrets

---

## 🛠️ 本地测试构建

在推送前测试构建：

```bash
# 构建镜像
docker build -t tg-auto:test .

# 运行测试
docker run --rm -p 8000:8000 tg-auto:test
```

---

## 📝 版本发布流程

### 发布新版本

```bash
# 1. 更新代码
git add .
git commit -m "feat: 新功能"
git push origin main

# 2. 创建版本标签
git tag -a v1.0.0 -m "Release version 1.0.0"
git push origin v1.0.0

# 3. GitHub Actions 自动构建并推送镜像
# 镜像标签: v1.0.0, v1.0, v1, latest
```

### 标签命名规范

遵循 [语义化版本](https://semver.org/lang/zh-CN/)：

- `v1.0.0` - 主版本.次版本.修订号
- `v1.0.1` - Bug 修复
- `v1.1.0` - 新增功能
- `v2.0.0` - 重大更新

---

## 🚨 常见问题

### Q: 构建失败怎么办？
**A**: 查看 Actions 日志，常见原因：
- Dockerfile 语法错误
- 依赖包无法下载
- 网络超时

### Q: 镜像拉取失败？
**A**: 检查权限设置，确保镜像为 Public 或已登录

### Q: 如何使用特定版本？
**A**: 
```yaml
image: ghcr.io/你的用户名/tg-auto:v1.0.0
```

### Q: 可以构建多架构镜像吗？
**A**: 可以，修改 `.github/workflows/docker-build.yml`：
```yaml
platforms: linux/amd64,linux/arm64
```
（需要更长的构建时间）

---

## 📚 相关链接

- [GitHub Actions 文档](https://docs.github.com/cn/actions)
- [GitHub Container Registry 文档](https://docs.github.com/cn/packages)
- [Docker Hub vs GHCR 对比](https://docs.github.com/cn/packages/working-with-a-github-packages-registry/working-with-the-container-registry)

---

**现在推送代码到 GitHub，镜像会自动构建！** 🎉
