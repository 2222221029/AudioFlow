# AudioFlow Docker / NAS 部署

## 快速启动

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
