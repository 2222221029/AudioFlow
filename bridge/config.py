from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class BridgeConfig:
    bind_host: str = "0.0.0.0"
    port: int = 17891
    token: str = ""
    frida_address: str = "127.0.0.1:27042"
    package_name: str = "com.ximalaya.ting.android"
    supported_version: str = "9.4.52.3"
    cookie: str = ""
    user_agent: str = "ting_9.4.52.3(com.ximalaya.ting.android,Android)"
    accept_language: str = "zh-CN,zh;q=0.9"
    api_device: str = "android"
    host: str = "mobile.ximalaya.com"

    @classmethod
    def load(cls, path: str | Path) -> "BridgeConfig":
        config_path = Path(path)
        raw = {}
        if config_path.exists():
            with config_path.open("r", encoding="utf-8") as source:
                loaded = json.load(source)
            if not isinstance(loaded, dict):
                raise ValueError("Bridge 配置必须是 JSON 对象")
            raw = loaded

        env_map = {
            "token": "AUDIOFLOW_BRIDGE_TOKEN",
            "bind_host": "AUDIOFLOW_BRIDGE_HOST",
            "port": "AUDIOFLOW_BRIDGE_PORT",
            "frida_address": "AUDIOFLOW_BRIDGE_FRIDA_ADDRESS",
        }
        for key, env_name in env_map.items():
            value = os.environ.get(env_name)
            if value not in (None, ""):
                raw[key] = value

        allowed = {field.name for field in cls.__dataclass_fields__.values()}
        values = {key: value for key, value in raw.items() if key in allowed}
        if "port" in values:
            values["port"] = int(values["port"])
        config = cls(**values)
        config.validate()
        return config

    def validate(self) -> None:
        if not self.token or len(self.token) < 24:
            raise ValueError("token 至少需要 24 个字符；请先运行 bridge/start_bridge.ps1")
        if not 1 <= int(self.port) <= 65535:
            raise ValueError("port 必须在 1-65535 之间")
        if self.api_device not in {"android", "android2"}:
            raise ValueError("api_device 只能是 android 或 android2")
