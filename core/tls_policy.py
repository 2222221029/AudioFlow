# -*- coding: utf-8 -*-
"""TLS 校验收支策略（单一出口）。

背景：多个平台管理器为兼容自签/证书异常的 CDN 域名默认关闭了证书校验
（``verify=False``）。对自部署的下载器这是常见取舍，但**必须是显式策略**，
不能散落在各平台文件里且无人提及。本模块提供：

* ``tls_verify(platform_default)`` —— 按环境变量覆盖后的最终取值；
* ``warn_if_verification_disabled(scope)`` —— 在关闭校验时打一条启动告警，
  提醒部署方可通过 ``AUDIOFLOW_TLS_VERIFY=1`` 重新开启（官方域名证书链完好）。

安全影响：关闭校验意味着中间人可篡改音频流与（极少数场景下）登录态 Cookie
的传输。首选做法是保持默认关闭以兼容既有运行环境，同时提供一键开启。
"""

import os


def tls_verify(platform_default: bool = False) -> bool:
    """返回本进程实际使用的 TLS 校验收支开关。

    环境变量 ``AUDIOFLOW_TLS_VERIFY``：
      * ``1/true/yes/on`` → 强制开启（推荐）；
      * ``0/false/no/off`` → 强制关闭；
      * 未设置 → 使用平台默认值（默认 False）。
    """
    value = str(os.getenv("AUDIOFLOW_TLS_VERIFY", "") or "").strip().lower()
    if value in ("1", "true", "yes", "on"):
        return True
    if value in ("0", "false", "no", "off"):
        return False
    return bool(platform_default)


def warn_if_verification_disabled(scope: str = "") -> None:
    """在关闭证书校验的会话初始化处调用，输出一次性启动告警。"""
    if not tls_verify():
        label = f"[{scope}] " if scope else ""
        print(
            f"⚠️ {label}TLS 证书校验处于关闭状态（兼容部分平台 CDN）。"
            "如需开启，请设置 AUDIOFLOW_TLS_VERIFY=1（官方域名证书链完好）。"
        )