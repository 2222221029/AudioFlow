# 移动端与 PWA

启动 Web 服务后，在手机浏览器访问：

```text
http://服务器IP:8082
```

## iPhone 添加到主屏幕

1. 使用 Safari 打开 Web 地址。
2. 点击底部分享按钮。
3. 选择「添加到主屏幕」。
4. 确认名称为「AudioFlow」。

移动端已适配 Safe Area，顶部会给 iOS 灵动岛和状态栏预留空间。

## Android 安装 PWA

使用 Chrome 打开 Web 地址后，可通过浏览器菜单中的「安装应用」添加到桌面。

## PWA 资源

PWA 资源位于：

```text
frontend/public/manifest.webmanifest
frontend/public/service-worker.js
frontend/public/platform-logos/
assets/branding/
```

## 移动端功能

- 搜索和平台选择。
- 专辑详情、章节选择、音色选择。
- 播放预览。
- 下载管理。
- 订阅管理。
- 账号 Cookie 管理。
- 系统设置与日志清理。
