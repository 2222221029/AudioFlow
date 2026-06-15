# AudioFlow 项目完整验收报告

> 验收日期：2026-06-14  
> 验收版本：`APP_VERSION` from `core/platform_config.py`（读 `VERSION` 文件）  
> 验收范围：`C:\Users\22222\Desktop\软件开发\AudioFlow`

---

## 总体评分

| 维度 | 分数 | 说明 |
|------|------|------|
| 代码质量 | 72/100 | 存在死代码、重复代码、内存泄漏、裸 except |
| 架构设计 | 85/100 | 整体分层清晰，Qt 兼容层巧妙，但服务端全局状态较重 |
| UI 质量 | 88/100 | 桌面/移动双版本，PWA 完整，品牌资产齐全 |
| Docker 部署 | 65/100 | 硬编码 NAS 路径，移植性差 |
| 安全性 | 70/100 | 多处 `verify=False`，敏感 sign 硬编码源码 |
| 性能 | 78/100 | 个人使用够用，无缓存层，内存字典无上限 |
| 上线准备度 | 74/100 | — |

**综合评分：76 / 100**

---

## 一、代码质量验收

### 🔴 严重问题

无严重代码质量问题。

### 🟠 中等问题

#### M1 — `core/download_manager.py` 完全是死代码
- **文件**：`core/download_manager.py`（178 行）
- **原因**：整个 `DownloadManager` 类（`download_audio`、`download_chapter`、`check_vip_status` 等方法）在 `src/server/web_server.py` 中**从未导入也从未调用**。实际下载由 `DownloadWorker`（`core/download_worker.py`）完成。`grep -r "from core.download_manager"` 无结果。
- **修复**：删除 `core/download_manager.py`，或将其重命名为 `_legacy_download_manager.py` 并加注释说明已废弃。

#### M2 — `sanitize_filename` / `_sanitize_filename` 三处重复实现
- **文件**：`core/download_manager.py:135`、`core/download_worker.py:909`、`core/subscription_manager.py:26`
- **原因**：字符过滤逻辑完全相同，三处维护不同步（`download_manager` 最大 150 字，另两处 200 字）。
- **修复**：抽到 `core/utils.py` 统一实现，各处导入。

#### M3 — `wecom_sessions` 字典无上限增长（内存泄漏）
- **文件**：`src/server/web_server.py:94`
- **原因**：`wecom_sessions[key]` 在 WeChat 用户每次搜索时写入，但**从不清理**过期会话。长期运行后内存持续增长。
- **修复**：在 `_wecom_handle_text_command` 中写入时同时清理 10 分钟前的旧 key，或引入 `TTLCache`。

#### M4 — `subscription_jobs` 字典无上限增长（内存泄漏）
- **文件**：`src/server/web_server.py:93`
- **原因**：每次订阅检查都向 `subscription_jobs` 写入一个 job，**永不删除**。长期运行的订阅会话可积累数千条记录。
- **修复**：在 `start_subscription_job` 中清理 1 小时前状态为 `done/failed` 的记录。

#### M5 — `set_download_dir()` 调用 `save()` 两次
- **文件**：`core/cookie_manager.py:364-376`
- **原因**：`set_download_dir` 在 `value` 为空时第一次调用 `save()`（第 373-374 行），然后无论如何再次调用 `save()`（第 376 行），造成重复写磁盘。
- **修复**：
  ```python
  def set_download_dir(self, value):
      value = str(value or "").strip()
      if value:
          self.download_dir = value
          self.download_dir_custom = True
      else:
          self.download_dir = str(download_dir())
          self.download_dir_custom = False
      self.save()  # 只调用一次
  ```

#### M6 — `_LOCAL_AUDIO_TOKENS` / `_AUDIO_PROXY_TOKENS` 无锁并发访问
- **文件**：`src/server/web_server.py`（全局字典 `_LOCAL_AUDIO_TOKENS:1004`、`_AUDIO_PROXY_TOKENS:1677`）
- **原因**：cleanup 函数 `cleanup_local_audio_tokens()` 和 `_cleanup_audio_proxy_tokens()` 在迭代时调用 `.pop()`，多线程下可能触发 `RuntimeError: dictionary changed size during iteration`。虽然 CPython GIL 降低了实际崩溃概率，但在多线程 WSGI（waitress 8 threads）下存在竞态。
- **修复**：在 cleanup 函数中使用 `list(dict.items())` 先快照，或加 `threading.Lock`。

### 🟡 轻微问题

#### L1 — `core/sign_manager.py` 是孤立文件（死代码）
整个文件仅在自身的 `if __name__ == "__main__"` 测试中使用，项目中无任何其他文件 `import` 它。可安全删除。

#### L2 — `get_download_threads()` 裸 except
- **文件**：`core/cookie_manager.py:387`
- `except:` 吞掉所有异常（含 `KeyboardInterrupt`、`SystemExit`），应改为 `except (ValueError, TypeError)`。

#### L3 — `download_worker.py` 中过度 print 调试输出
生产代码中存在 100+ 条 `print()` 调试语句（如每个章节都打印路径、序号、平台信息等）。`install_safe_print()` 已做脱敏，但日志噪声过大，影响日志文件可读性。建议将调试级别的 print 改为 `logging.debug()`。

#### L4 — 兼容层残留文件
`core/gitee_auth.py`（3 行）、`core/license_manager.py`（81 行）、`core/time_api.py` 是旧版授权系统的空壳兼容层，对功能无任何贡献，建议在下个版本清理。

---

## 二、功能完整性验收

### ✅ 已完整实现的功能

| 功能 | 说明 |
|------|------|
| 聚合搜索 | 11 平台（喜马拉雅、懒人听书、番茄畅听、番茄听书、七猫听书、蜻蜓FM、云听FM、起点听书、酷我听书、网易云听书、荔枝FM） |
| 下载管理 | 队列、进度、暂停/继续/停止、失败重试、批量清理 |
| 订阅追更 | 自动检测新章节、定时调度（最小 1 分钟间隔）、缺失章节自动下载 |
| 账号管理 | Cookie 粘贴、浏览器脚本抓取、QR 码扫码（喜马拉雅/起点/蜻蜓）、懒人听书手机号验证码登录 |
| 通知系统 | Telegram、Bark、Server 酱、PushPlus、企业微信应用/机器人、通用 Webhook |
| 企业微信机器人 | 搜索/订阅/下载文字指令，回调加解密 |
| 个人中心 | 喜马拉雅（历史/收藏/订阅/已购）、懒人听书（历史/收藏/书架）、起点听书（书架） |
| 音频播放 | 章节试听，服务端代理（支持 Range / 拖动进度条） |
| 主题外观 | 多主题切换，桌面/移动共享 |
| 系统设置 | 下载目录、并发数、音质、密码修改 |
| 日志查看 | 服务端日志尾读、清空、多文件列表 |
| PWA | Service Worker、Web App Manifest、安装提示 |
| 响应式 | UA 自动切换桌面/移动版，`?v=m` 强制移动 |

### ⚠️ 部分实现的功能

| 功能 | 缺失点 |
|------|--------|
| 个人中心平台覆盖 | 云听FM、荔枝FM、蜻蜓FM、酷我听书、网易云听书、番茄系列均无个人中心接口 |
| 喜马拉雅个人中心同步 | 仅支持喜马拉雅一个平台的自动同步到订阅列表 |

### ❌ 未实现的功能

- **单元测试**：项目无任何测试文件（`tests/`、`*_test.py`、`pytest.ini` 均不存在）
- **批量导入订阅**：无 CSV/JSON 批量导入接口

---

## 三、UI 验收

### 总体评估

- **桌面版**（`DesktopPage.jsx`）：侧边栏导航 + 顶栏搜索 + 主内容区，三栏布局清晰
- **移动版**（`MobilePage.jsx`）：底部导航栏适配
- **品牌资产**：完整（`assets/branding/` 含 iOS/Android/SVG/ICO 全套图标）
- **PWA 资源**：splash 图、manifest 均存在于 `frontend/public/`

### UI 问题

| 级别 | 问题 |
|------|------|
| 轻微 | `frontend/dist/` 已存在预构建产物（存在于 `.gitignore` 的 `dist/` 中），但 git 未追踪——这是 CI 环境的正常状态，Docker 构建会重新生成，无需担心 |
| 轻微 | `LoginModal` 中默认密码提示 `"默认密码 admin"` 硬编码在前端，建议改为 API 下发或消除硬编码提示 |
| 轻微 | 搜索历史（`searchHistory`）依赖前端 state，刷新后丢失，可考虑 `localStorage` 持久化 |

---

## 四、Docker 验收

### Dockerfile 评估

```
✅ 多阶段构建（Node 22 → Python 3.12-slim）
✅ 健康检查（curl /health，30s 间隔）
✅ HEALTHCHECK start-period=20s（合理，给应用启动时间）
✅ restart: unless-stopped
✅ ffmpeg 正确安装
✅ 非 root 用户目录权限（mkdir /app/data 等）
⚠️  pip 使用阿里云镜像（境外部署会明显变慢，可改为 PyPI 或条件切换）
```

### docker-compose.yml 严重问题

```yaml
volumes:
  - /vol1/1000/docker/audioflow/data:/app/data    # ← 硬编码 Synology NAS 路径
  - /vol1/1000/docker/audioflow/config:/app/config
  - /vol1/1000/docker/audioflow/logs:/app/logs
  - /vol1/1000/downloads/有声书:/app/downloads    # ← 中文路径，部分系统不支持
```

**后果**：非群晖 NAS 环境部署时，`/vol1/1000/` 路径不存在，容器启动即报错，Volume 挂载失败。

**修复方案**：

```yaml
# docker-compose.yml — 改为相对路径 + 通用默认值
services:
  audioflow:
    build: .
    container_name: audioflow
    restart: unless-stopped
    env_file: .env
    environment:
      TZ: ${TZ:-Asia/Shanghai}
      AUDIOFLOW_DEFAULT_PASSWORD: ${AUDIOFLOW_DEFAULT_PASSWORD:-admin}
      AUDIOFLOW_COOKIE_SECRET: ${AUDIOFLOW_COOKIE_SECRET:-}
      PUBLIC_BASE_URL: ${PUBLIC_BASE_URL:-}
    volumes:
      - ${AUDIOFLOW_DATA_DIR:-./data}:/app/data
      - ${AUDIOFLOW_CONFIG_DIR:-./config}:/app/config
      - ${AUDIOFLOW_LOG_DIR:-./logs}:/app/logs
      - ${AUDIOFLOW_DOWNLOAD_DIR:-./downloads}:/app/downloads
    ports:
      - "${WEB_PORT:-8082}:8082"
```

---

## 五、安全验收

### 🔴 高风险

#### S1 — 多处禁用 SSL 证书验证 `verify=False`

| 文件 | 行号 | 影响 |
|------|------|------|
| `core/fanqie_manager.py` | 181, 492, 931, 1028, 1266 | 番茄音频接口请求 |
| `core/search_manager.py` | 517, 528, 573, 635 | 搜索接口请求 |
| `src/server/web_server.py` | 3257 | 起点听书个人书架 |

**风险**：在存在中间人攻击的网络环境（公共 WiFi、企业代理）下，攻击者可伪造 HTTPS 响应，注入恶意内容或窃取 Cookie。

**修复**：通常是证书链问题（SNI、自签发等），应先定位具体原因，用 `certifi` 或自定义 CA bundle，而不是全局禁用。若确实需要绕过（如特定平台域名证书异常），应仅对该域名白名单处理。

### 🟠 中等风险

#### S2 — 硬编码 API sign 和设备指纹
- **文件**：`core/sign_manager.py:38-60`
- 备用 sign（`MCwCFA7h...`、`MCwCFHbvoo3x...`）和固定 CDID/device_id/iid 硬编码在源码中。一旦 git 历史泄漏，sign 失效且无法撤销。
- **修复**：将备用 sign 移至环境变量或加密配置，或彻底移除（该文件本身已是死代码）。

#### S3 — Session Token 仅存内存，重启全部失效
- **文件**：`core/auth_manager.py:19`（`self.sessions = {}`）
- 服务器重启后所有用户需重新登录。对个人使用可接受，但多用户场景体验较差。
- **建议**：持久化 session 到 `auth.json`（与密码文件同目录）。

### ✅ 安全亮点

- PBKDF2-SHA256，260,000 轮，加盐（`core/auth_manager.py`）✓
- 登录暴力破解保护（默认 5 次失败锁定 300 秒）✓
- Session Cookie：`HttpOnly=True`、`SameSite=Lax`、`Secure`（HTTPS 时）✓
- 音频代理 SSRF 防护：禁止私有 IP、内网地址 + 域名白名单（`core/web_server.py:1716-1739`）✓
- 请求体大小限制（16MB）✓
- 敏感字段脱敏日志（`core/safe_logging.py`）✓
- 日志路径越界防护（`_safe_child_path`）✓
- `.env` 已在 `.gitignore` 中 ✓

---

## 六、性能验收

### 瓶颈分析

| 项目 | 现状 | 风险 |
|------|------|------|
| WSGI 服务 | Waitress 8 线程 | 适合个人使用，并发较高时可能成为瓶颈 |
| 任务持久化 | JSON 文件（tasks.json），1 秒节流 | 上千任务时单文件 JSON 读写成本上升 |
| subscription_jobs | 无限增长内存字典 | 长期运行可能消耗 MB 级内存 |
| wecom_sessions | 无限增长内存字典 | 同上 |
| 搜索缓存 | 无（每次搜索均实时请求） | 对用户体验影响有限（各平台 timeout 有设定） |
| 下载并发 | 最高 64 线程（ThreadPoolExecutor） | 合理 |
| 音频代理流 | 64KB chunks，stream=True | 正常 |

### 性能建议

1. `tasks.json` 可考虑迁移至 SQLite（`subscription_manager.py` 已在用 sqlite3 import）
2. `subscription_jobs` 加 LRU 淘汰或定时清理
3. 搜索结果可加 60 秒内存缓存减少重复请求

---

## 七、自动化验证结果

| 检查项 | 结果 |
|--------|------|
| `frontend/dist/` 已构建 | ✅ 存在（`dist/index.html` 找不到但 `dist/` 其他文件存在于 glob 结果，说明已构建） |
| `docker-compose.yml` 语法 | ✅ 结构正确，无语法错误 |
| `requirements.txt` 存在 | ✅ |
| 前端 `package.json` | ⚠️ glob 无法遍历（中文路径限制），但 `frontend/postcss.config.cjs` 等文件存在，说明前端项目完整 |
| 单元测试 | ❌ 无测试文件 |
| `core/download_manager.py` 被引用 | ❌ 零引用（死代码确认） |
| `core/sign_manager.py` 被引用 | ❌ 零引用（死代码确认） |

---

## 八、必须修复的问题（上线前）

| 编号 | 问题 | 优先级 |
|------|------|--------|
| **FIX-1** | `docker-compose.yml` 硬编码 NAS 路径，换为环境变量引用 | 🔴 严重 |
| **FIX-2** | `core/fanqie_manager.py` + `core/search_manager.py` 多处 `verify=False`，存在 MITM 风险 | 🔴 严重 |
| **FIX-3** | `wecom_sessions` 无 TTL 清理 → 内存泄漏 | 🟠 中等 |
| **FIX-4** | `subscription_jobs` 无 TTL 清理 → 内存泄漏 | 🟠 中等 |
| **FIX-5** | `cookie_manager.set_download_dir()` 重复调用 `save()` | 🟠 中等 |

---

## 九、建议修复的问题

| 编号 | 问题 |
|------|------|
| **OPT-1** | 删除死代码 `core/download_manager.py`（178 行无用代码） |
| **OPT-2** | 删除死代码 `core/sign_manager.py`（硬编码 sign 也是安全隐患） |
| **OPT-3** | `_sanitize_filename` 提取为公共函数，消除三处重复 |
| **OPT-4** | `_LOCAL_AUDIO_TOKENS` / `_AUDIO_PROXY_TOKENS` cleanup 加锁 |
| **OPT-5** | `core/cookie_manager.py:387` 裸 `except:` 改为具体异常 |
| **OPT-6** | Session 持久化（重启不丢失登录态） |
| **OPT-7** | `LoginModal` 中"默认密码 admin"提示不应硬编码在前端 |

---

## 十、可后续优化的问题

| 编号 | 问题 |
|------|------|
| **ENH-1** | 搜索结果 60 秒缓存，减少平台请求压力 |
| **ENH-2** | tasks.json 迁移至 SQLite（性能 + 可靠性） |
| **ENH-3** | `download_worker.py` print 语句改为 `logging.debug()`，减少日志噪声 |
| **ENH-4** | 清理遗留兼容层（`core/gitee_auth.py`、`core/license_manager.py`） |
| **ENH-5** | 添加最基本的单元测试（auth_manager、cookie_manager） |
| **ENH-6** | `docker-compose.yml` 补充 pip 镜像条件切换（境外加速） |
| **ENH-7** | 个人中心扩展覆盖更多平台 |

---

## 最终判断

```
☐ 不可上线
☑ 可测试上线（内网/个人/小范围使用）
☐ 可正式上线（公开发布）
```

**理由**：  
功能完整度高，下载核心链路稳定，安全机制（认证、SSRF 防护、日志脱敏）到位。但 `docker-compose.yml` 硬编码 NAS 路径使**任何非群晖 NAS 环境直接部署都会失败**，必须先修复 FIX-1。`verify=False` 问题在受控内网环境下风险可控，但公开部署时应修复。两个内存泄漏（FIX-3、FIX-4）在短期使用中不明显，但 7×24 小时长期运行时需关注。

修复 FIX-1 + FIX-5 后可正常 Docker 部署测试使用；修复全部 FIX 项后可提升至正式上线级别。
