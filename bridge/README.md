# AudioFlow Bridge

Bridge 使用安卓模拟器内已登录的喜马拉雅 App 为 AudioFlow 按请求生成动态 `x-tk`。它只支持用户自己的账号和已有音频权限，不处理或绕过 DRM。

飞牛 NAS 可以使用仓库根目录的 `docker-compose.redroid.yml`，将 ReDroid、Frida 和 Bridge 全部运行在 NAS 上。首次部署仍需安装仓库内指定版本的喜马拉雅 APK；App 数据保存在 Compose 目录的 `data`，容器重建后无需重复安装。

## 首次使用

1. 准备一个能够开启 Root 的安卓模拟器，并在设置中开启 Root/ADB。
2. 确保 `adb` 在 Windows PATH 中；也可以把模拟器自带的 `adb.exe` 完整路径传给脚本。
3. 双击 PowerShell 或在项目根目录运行：

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\bridge\start_bridge.ps1
   ```

   如果模拟器通过网络 ADB 连接：

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\bridge\start_bridge.ps1 -AdbAddress 127.0.0.1:5555
   ```

4. 脚本会自动安装依赖、Frida Server 和仓库内的喜马拉雅 `9.4.52.3`。在打开的喜马拉雅中登录，然后播放任意音频一次。

脚本最后会显示两行 NAS 环境变量。复制进 NAS 的 `.env`，重新构建并启动 AudioFlow：

```bash
docker compose up -d --build
```

以后只需先启动模拟器，再运行 `bridge/start_bridge.ps1`。不要升级模拟器中的喜马拉雅 App。

## 检查状态

浏览器访问：

```text
http://电脑局域网IP:17891/health
```

`connected: true` 表示已连接；`captured_cookie: true` 表示播放一次后已经自动捕获配套请求头。健康接口不会返回 Cookie 或 `x-tk`。

如果 `captured_cookie` 一直是 `false`，可在 AudioFlow 的喜马拉雅账号设置中保存一次同一 App 请求的 Cookie 和 User-Agent；Bridge 仍会为每次下载实时生成新的 `x-tk`。

## 常见问题

- “没有找到 adb”：使用 `-AdbPath "模拟器目录\adb.exe"`。
- “模拟器没有开放 Root”：在模拟器设置中开启 Root 后完整重启。
- App 版本不支持：卸载模拟器内现有版本，重新运行脚本安装仓库内版本。
- NAS 连接失败：允许 Windows 防火墙入站 TCP 17891，并确认电脑与 NAS 在同一局域网。
- 重启模拟器后无法连接：重新运行 `start_bridge.ps1`，它会恢复端口转发和 Frida Server。
