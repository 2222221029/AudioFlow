# AudioFlow Docker / NAS 部署

## 推荐：GHCR 镜像部署

每次 `main` 分支更新后，GitHub Actions 会自动构建镜像并推送到：

```text
ghcr.io/2222221029/audioflow:latest
```

也会同步推送当前版本号标签，例如：

```text
ghcr.io/2222221029/audioflow:0.03
```

飞牛 NAS 的 Compose 建议使用镜像部署，不需要在 NAS 上编译源码：

```yaml
services:
  audioflow:
    image: ghcr.io/2222221029/audioflow:latest
    container_name: audioflow
    restart: unless-stopped
    environment:
      TZ: Asia/Shanghai
      AUDIOFLOW_DEFAULT_USERNAME: admin
      AUDIOFLOW_DEFAULT_PASSWORD: admin
      AUDIOFLOW_COOKIE_SECRET: 请改成一段很长的随机字符串
      PUBLIC_BASE_URL: ""
    volumes:
      - /vol1/1000/docker/audioflow/data:/app/data
      - /vol1/1000/docker/audioflow/config:/app/config
      - /vol1/1000/docker/audioflow/logs:/app/logs
      - /vol1/1000/downloads/有声书:/app/downloads
    ports:
      - "8082:8082"
```

更新时在飞牛 Compose 页面重新拉取镜像并重新部署。也可以用 SSH：

```bash
docker pull ghcr.io/2222221029/audioflow:latest
docker compose up -d
```

如果拉取镜像提示无权限，请到 GitHub 仓库的 Packages 页面把 `audioflow` 镜像可见性设为 Public。

确认当前运行版本：

```bash
curl http://127.0.0.1:8082/health
```

## 企业微信应用交互

通知系统支持两种企业微信渠道：

- `企业微信机器人`：群聊机器人 Webhook，只能推送。
- `企业微信应用`：自建应用模式，支持主动推送和接收文字指令。

企业微信应用需要在 AudioFlow 通知系统里新增渠道并填写：

```text
企业 ID：企业微信后台“我的企业”中的 CorpID
应用 AgentId：自建应用的 AgentId
应用 Secret：自建应用的 Secret
默认接收人：@all 或指定成员 UserID
回调 Token：企业微信“接收消息”里配置的 Token
EncodingAESKey：企业微信“接收消息”里配置的 EncodingAESKey
```

保存后，渠道卡片会显示回调 URL，复制到企业微信自建应用的“接收消息”配置里。示例：

```text
https://你的域名/api/wecom/callback/wecom_app-xxxx
```

企业微信回调通常需要公网可访问地址。局域网 NAS 地址无法被企业微信服务器直接访问时，需要反向代理、DDNS、内网穿透或公网域名。

当前支持的企业微信文字指令：

```text
帮助
状态
搜索 关键词
订阅 序号
下载 序号
```

使用示例：

```text
搜索 三体
订阅 1
下载 1
```

## 快速启动

下面是源码构建方式，仅在需要 NAS 本地编译时使用。

```bash
cd /vol1/1000/docker-build/tingshu-downloader-docker
cp .env.example .env
docker compose up -d --build
```

访问地址：

```text
http://NAS_IP:8082
```

本项目是单容器部署：同一个 `audioflow` 容器同时提供 React Web UI 和后端 API。

## 推荐路径

按你的 NAS 路径，`.env` 保持：

```env
AUDIOFLOW_DATA_DIR=/vol1/1000/docker/audioflow/data
AUDIOFLOW_CONFIG_DIR=/vol1/1000/docker/audioflow/config
AUDIOFLOW_LOG_DIR=/vol1/1000/docker/audioflow/logs
AUDIOFLOW_DOWNLOAD_DIR=/vol1/1000/downloads/有声书
```

下载文件在容器内路径为 `/app/downloads`。每个专辑目录会保存 `source.json`，方便其他工具读取来源信息。

## 覆盖旧容器

```bash
docker compose down
docker compose up -d --build
```

如果旧项目曾经创建过前后端两个容器，先查看：

```bash
docker ps -a --format "table {{.Names}}\t{{.Image}}\t{{.Status}}"
```

只保留 `audioflow` 容器。确认无误后可删除旧的前端容器：

```bash
docker rm -f tingshu-downloader-frontend
```

## 反向代理

反向代理到：

```text
http://127.0.0.1:8082
```

前端默认使用同源 `/api`，无需写死 `localhost`。刷新页面由后端 SPA fallback 返回 `index.html`，不会 404。

## 安全配置

首次登录默认：

```text
admin / admin
```

可在 `.env` 修改首次默认值：

```env
AUDIOFLOW_DEFAULT_USERNAME=admin
AUDIOFLOW_DEFAULT_PASSWORD=admin
```

可选启用 Cookie 加密：

```env
AUDIOFLOW_COOKIE_SECRET=一段随机长密钥
```

密钥丢失后已加密 Cookie 无法恢复。

## 部署检查

```bash
docker compose config --quiet
docker compose build audioflow
docker compose up -d
docker logs -f audioflow
```

进入容器健康检查：

```bash
curl http://127.0.0.1:8082/health
```
