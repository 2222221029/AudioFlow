# AudioFlow

AudioFlow是面向 Docker、NAS 和 Web/PWA 的多平台有声书下载工具。前端使用 React + Vite，生产部署时会打包进后端镜像，默认只需要一个容器。

## 推荐部署

GitHub Actions 会自动构建 GHCR 镜像：

```text
ghcr.io/2222221029/audioflow:latest
```

飞牛 NAS Compose 推荐直接使用镜像部署，示例见 `docker-compose.image.yml`。

## Docker 部署

```bash
cp .env.example .env
docker compose up -d --build
```

打开 Web UI：

```text
http://NAS_IP:8082
```

覆盖旧容器：

```bash
docker compose down
docker compose up -d --build
```

## 持久化目录

`.env` 中可配置宿主机路径：

```env
AUDIOFLOW_DATA_DIR=/vol1/1000/docker/audioflow/data
AUDIOFLOW_CONFIG_DIR=/vol1/1000/docker/audioflow/config
AUDIOFLOW_LOG_DIR=/vol1/1000/docker/audioflow/logs
AUDIOFLOW_DOWNLOAD_DIR=/vol1/1000/downloads/有声书
```

容器内目录：

- `/app/data`：运行数据。
- `/app/config`：Cookie、账号、订阅、任务记录和配置。
- `/app/logs`：服务端日志，已启用日志轮转。
- `/app/downloads`：下载后的音频文件，专辑目录会写入 `source.json`。

真实 Cookie、订阅数据库、任务记录、日志和下载文件只应保存在宿主机挂载目录中。

## 账号与安全

默认账号密码：

```text
账号：admin
密码：admin
```

首次默认账号可通过 `.env` 修改：

```env
AUDIOFLOW_DEFAULT_USERNAME=admin
AUDIOFLOW_DEFAULT_PASSWORD=admin
```

登录失败已加入限流保护；连续失败过多会短暂锁定登录。登录后请在“系统设置”中修改密码。

可选启用 Cookie 加密：

```env
AUDIOFLOW_COOKIE_SECRET=请换成一段足够长的随机字符串
```

启用后 `config/cookies.json` 内的 Cookie 会加密保存。请妥善保存该密钥，丢失后已加密 Cookie 无法解密。

## 功能

- 聚合搜索、专辑详情、章节选择、播放预览。
- 下载管理：暂停、继续、停止、失败章节重试、批量清理历史任务。
- 订阅管理：订阅专辑、自动检测缺失章节、补全下载。
- 账号管理：保存、扫码、浏览器抓取或删除平台 Cookie。
- 系统设置：下载目录、默认音质、登录密码、主题、服务诊断、日志查看与清空。
- 移动端完整包含搜索、详情、下载、订阅、个人中心、账号管理、系统设置、主题和日志操作。

## PWA 与移动端

AudioFlow支持安装为 PWA。Android Chrome 会在满足条件后显示安装入口；iOS Safari 需要手动打开分享菜单，选择“添加到主屏幕”。

PWA 已包含：

- `manifest.webmanifest`、`service-worker.js`、离线兜底页和 App Shell 缓存。
- `favicon.ico`、`apple-touch-icon.png`、多尺寸 PNG 图标和 maskable 图标。
- `viewport-fit=cover`、iOS Web App meta、safe-area 适配、底部 Home Indicator 留白。
- Media Session API：播放时向系统提供章节标题、专辑名、作者和封面，支持播放、暂停、上一章、下一章、快进、后退和 seek。

iOS 能力边界：

- Web/PWA 不能直接控制灵动岛原生 UI，只能通过 safe-area 避让顶部区域。
- 锁屏和控制中心显示依赖 iOS 对 Media Session 的支持，不同 iOS 版本表现可能不同。
- 后台播放受 iOS 系统策略限制，通常需要用户主动开始播放后才能持续。
- 部分 PWA 能力在公网或反向代理场景需要 HTTPS；局域网 HTTP 可用于基础访问和下载管理，但安装、缓存、媒体控制能力可能受浏览器限制。

## 前端结构

业务 UI 已迁移到 React 组件：

```text
frontend/index.html          Vite 挂载入口，仅保留 root 和基础 meta
frontend/src/App.jsx
frontend/src/pages/
frontend/src/components/
frontend/src/hooks/
frontend/src/services/
frontend/src/utils/
frontend/src/styles/
frontend/public/
```

项目不再维护旧业务 HTML 页面。浏览器刷新由 Flask SPA fallback 返回 Vite 构建后的 `index.html`。

## API 配置

前端默认通过同源 `/api` 调用后端，不写死 `localhost`：

```env
VITE_API_BASE_URL=/api
```

反向代理场景通常保持默认即可。

## 检查命令

```bash
python -m py_compile src/server/web_server.py core/auth_manager.py core/cookie_manager.py core/subscription_manager.py
python scripts/check_pwa.py
python scripts/smoke_api.py
docker compose config --quiet
docker compose build audioflow
```

## 版本

当前版本号存放在 `VERSION`。每次更新后执行：

```bash
python scripts/bump_version.py
```

脚本会把 `VERSION`、`frontend/package.json` 和 `requirements.txt` 的版本从 `0.01` 递增到 `0.02`、`0.03`，依次类推。
