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

## 下载后自动重命名

设置 `AUDIOFLOW_AUTO_RENAME=true` 后，完整下载成功的任务会在后台生成重命名计划。系统只分析文件并发送通知，确认前不会修改文件；部分失败的任务不会自动生成计划。

默认建议格式为 `0001-《书名》第001集 标题.ext`。特殊文件和未匹配文件保持不动，标题歧义、目标冲突等问题会阻止执行。请在通知设置中启用“重命名确认”场景；企业微信应用渠道可回复 `确认重命名 计划ID` 或 `取消重命名 计划ID`，其他渠道可使用登录后的 `/api/rename-plans` 接口查看和确认。执行采用持久化计划和两阶段临时文件名，不删除任何文件。

## AudioFlow Agent

桌面端侧栏与移动端“更多”中提供 Agent 工作台。支持 DeepSeek、OpenAI、Anthropic Claude、Google Gemini、OpenRouter、Ollama、通义千问、Kimi、智谱 GLM、豆包、SiliconFlow，以及自定义 OpenAI 兼容地址。API Key 加密保存在 `config/agent.json`，Web API 只返回掩码。

Agent 只拥有 AudioFlow 注册的应用工具：查看下载、查看重命名计划、为已完成任务生成待确认计划。它没有 Shell、文件系统、删除或直接重命名权限，任何重命名仍必须通过现有通知命令或登录后的确认 API 执行。

Docker 镜像默认安装开发预览版 `deepseek-harness-sdk`，无需增加 Compose 环境变量；Agent 仍默认使用稳定的 AudioFlow 原生运行时，可在模型设置中为 DeepSeek 切换到 Harness。模型 API Key 使用持久化目录中的 `config/agent.key` 自动加密，该文件会随现有配置卷保存，也无需在 Compose 中设置根密钥。迁移实例时应一起迁移整个配置目录。

AudioFlow 内置的有声书 Harness 组合不加载 Bash、终端、编辑器、文件系统或子 Agent 插件，因此 Harness 不可用不会影响下载服务和原生 Agent。需要代码开发能力时，可另行启用下述隔离的完整代码 Agent。

### 飞书 Agent

通知设置中可添加“飞书 Agent”渠道。填写飞书自建应用的 App ID、App Secret、默认接收目标，并至少填写一个允许的用户 Open ID 或群聊 Chat ID；两个白名单都为空时，AudioFlow 会拒绝全部入站消息。服务使用飞书长连接接收消息，不需要公网回调 URL，也不需要新增 Compose 环境变量。

飞书中发送的文字会进入独立的 AudioFlow Agent 会话。下载、订阅等通知直接推送到飞书；重命名确认会使用带“确认重命名”和“取消计划”按钮的交互卡片。卡片动作绑定渠道、计划、用户/群聊和一次性随机标识，重复、过期或越权操作均会被拒绝。App Secret 使用 `config/agent.key` 加密落盘，迁移时仍需备份整个 `config` 目录。

此处的 AudioFlow 通知机器人只调用应用自身的受控 Agent 与重命名确认接口，不加载工作目录选择、Shell、编辑器或文件系统能力。需要这些通用代码能力时，启用下述基于 [`PGZXB/dsh-feishu`](https://github.com/PGZXB/dsh-feishu) 的独立完整代码 Agent。

### 飞书完整代码 Agent

Agent 的模型设置中可以另行启用“飞书完整代码 Agent”。该模式直接运行固定版本的 `@deepseek-ai/dsh` 与 [`@dsh-feishu/dsh-feishu`](https://github.com/PGZXB/dsh-feishu)，包含工作目录选择、Bash/PowerShell、文件读写与搜索、字符串编辑器、后台任务、技能、子 Agent、工作流、会话恢复、流式卡片、提问卡和审批卡。

完整代码 Agent 必须使用独立的飞书应用 App ID，不能与 AudioFlow 通知机器人共用同一个长连接。它复用当前选择的 AI 平台、模型和加密 API Key；飞书 App Secret 同样加密保存在 `config/agent.json`。用户和群聊白名单不能同时留空。

Docker Compose 会持久化 `./workspace` 到容器的 `/workspace`；NAS 镜像配置使用 `/vol1/1000/docker/audioflow/workspace`。默认权限为 `workspace-write`，Agent 可直接修改工作区内容；超出工作区或需要提权的操作通过飞书审批卡确认。`/panel` 打开控制面板，`/repo` 选择项目，`/permission` 调整权限，`/sessions` 管理会话。

飞书开发应用需要同时把“事件”和“卡片回调”设置为长连接，并开通 `im.message.receive_v1`、`card.action.trigger` 以及 `im:message`、`im:message:send_as_bot`、`im:chat`、`im:chat.members:read`、`im:resource` 等 `dsh-feishu` 文档列出的权限。完整代码 Agent 是独立子进程；它退出或配置错误不会停止 AudioFlow 下载服务，运行日志位于 `config/dsh/developer-agent.log`。

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
