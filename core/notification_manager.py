#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import logging
import time
from copy import deepcopy
from pathlib import Path
from urllib.parse import quote

import requests

from .platform_config import config_dir


DEFAULT_SCENES = {
    "download_completed": True,
    "download_failed": True,
    "subscription_queued": True,
    "subscription_checked": False,
}

CHANNEL_LABELS = {
    "telegram": "Telegram",
    "bark": "Bark",
    "serverchan": "Server 酱",
    "pushplus": "PushPlus",
    "wecom_robot": "企业微信机器人",
    "webhook": "通用 Webhook",
}


def _now():
    return int(time.time())


def _clean_text(value, max_len=4000):
    value = str(value or "").strip()
    if len(value) > max_len:
        return value[:max_len - 3] + "..."
    return value


def _mask(value, keep=4):
    value = str(value or "")
    if not value:
        return ""
    if len(value) <= keep * 2:
        return "*" * len(value)
    return f"{value[:keep]}{'*' * 8}{value[-keep:]}"


def _compact_error(value, max_len=200):
    text = str(value or "").strip()
    return text[:max_len] + "..." if len(text) > max_len else text


class NotificationManager:
    """MoviePilot-style notification services and scene switches."""

    def __init__(self, path=None):
        self.path = Path(path or config_dir() / "notifications.json")
        self._config = None

    def default_config(self):
        return {
            "version": 1,
            "enabled": False,
            "scenes": deepcopy(DEFAULT_SCENES),
            "services": [],
            "updated_at": _now(),
        }

    def load(self):
        if self._config is not None:
            return self._config
        if not self.path.exists():
            self._config = self.default_config()
            return self._config
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                data = {}
        except Exception:
            logging.exception("failed to load notification config")
            data = {}
        base = self.default_config()
        base.update({k: v for k, v in data.items() if k in {"version", "enabled", "scenes", "services", "updated_at"}})
        scenes = deepcopy(DEFAULT_SCENES)
        scenes.update(data.get("scenes") or {})
        base["scenes"] = scenes
        base["services"] = [self._normalize_service(item) for item in (data.get("services") or []) if isinstance(item, dict)]
        self._config = base
        return self._config

    def save(self, config):
        data = self.default_config()
        data.update({k: v for k, v in (config or {}).items() if k in {"enabled", "scenes", "services"}})
        scenes = deepcopy(DEFAULT_SCENES)
        scenes.update(data.get("scenes") or {})
        data["scenes"] = scenes
        data["services"] = [self._normalize_service(item) for item in data.get("services") or [] if isinstance(item, dict)]
        data["updated_at"] = _now()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)
        self._config = data
        return data

    def public_config(self):
        data = deepcopy(self.load())
        for service in data.get("services") or []:
            config = service.get("config") or {}
            service["configured"] = self._is_service_configured(service)
            for key in ("token", "bot_token", "send_key", "key", "chat_id", "url"):
                if config.get(key):
                    config[f"{key}_masked"] = _mask(config.get(key), keep=5 if key == "url" else 4)
            for key in ("token", "bot_token", "send_key", "key"):
                config.pop(key, None)
            if service.get("type") == "webhook":
                config.pop("url", None)
        data["available_channels"] = [{"type": key, "name": value} for key, value in CHANNEL_LABELS.items()]
        data["file"] = str(self.path)
        return data

    def _normalize_service(self, service):
        service_type = str(service.get("type") or "").strip()
        config = dict(service.get("config") or {})
        return {
            "id": str(service.get("id") or f"{service_type}-{int(time.time() * 1000)}"),
            "name": str(service.get("name") or CHANNEL_LABELS.get(service_type) or service_type or "通知渠道").strip(),
            "type": service_type,
            "enabled": bool(service.get("enabled", True)),
            "switchs": list(service.get("switchs") or []),
            "config": config,
        }

    def _is_service_configured(self, service):
        config = service.get("config") or {}
        service_type = service.get("type")
        if service_type == "telegram":
            return bool(config.get("bot_token") and config.get("chat_id"))
        if service_type == "bark":
            return bool(config.get("key"))
        if service_type == "serverchan":
            return bool(config.get("send_key"))
        if service_type == "pushplus":
            return bool(config.get("token"))
        if service_type == "wecom_robot":
            return bool(config.get("key"))
        if service_type == "webhook":
            return bool(config.get("url"))
        return False

    def notify(self, scene, title, text="", payload=None):
        data = self.load()
        if not data.get("enabled") or not data.get("scenes", {}).get(scene, False):
            return {"sent": 0, "failed": 0, "skipped": True, "results": []}
        message = {
            "scene": scene,
            "title": _clean_text(title, 200),
            "text": _clean_text(text),
            "payload": payload or {},
            "date": _now(),
            "source": "audioflow",
        }
        results = []
        for service in data.get("services") or []:
            if not service.get("enabled", True):
                continue
            switches = service.get("switchs") or []
            if switches and scene not in switches:
                continue
            if not self._is_service_configured(service):
                results.append({"service": service.get("name"), "ok": False, "error": "配置不完整"})
                continue
            try:
                self._send(service, message)
                results.append({"service": service.get("name"), "ok": True})
            except Exception as exc:
                logging.exception("notification send failed: %s", service.get("name"))
                results.append({"service": service.get("name"), "ok": False, "error": str(exc)})
        return {
            "sent": sum(1 for item in results if item.get("ok")),
            "failed": sum(1 for item in results if not item.get("ok")),
            "results": results,
        }

    def test(self, service_id=None, service=None):
        config = self.load()
        if service:
            services = [self._normalize_service(service)]
        else:
            services = config.get("services") or []
        if service_id and not service:
            services = [item for item in services if item.get("id") == service_id]
        if not services:
            raise ValueError("没有可测试的通知渠道")
        message = {
            "scene": "test",
            "title": "AudioFlow通知测试",
            "text": "如果你看到这条消息，说明通知渠道已接通。",
            "payload": {},
            "date": _now(),
            "source": "audioflow",
        }
        results = []
        for service in services:
            if not service.get("enabled", True):
                results.append({"service": service.get("name"), "ok": False, "error": "渠道未启用"})
                continue
            if not self._is_service_configured(service):
                results.append({"service": service.get("name"), "ok": False, "error": "配置不完整"})
                continue
            try:
                detail = self._send(service, message)
                results.append({"service": service.get("name"), "ok": True, "detail": detail})
            except Exception as exc:
                logging.exception("notification test failed: %s", service.get("name"))
                results.append({"service": service.get("name"), "ok": False, "error": str(exc)})
        return {
            "sent": sum(1 for item in results if item.get("ok")),
            "failed": sum(1 for item in results if not item.get("ok")),
            "results": results,
        }

    def _send(self, service, message):
        service_type = service.get("type")
        config = service.get("config") or {}
        self._validate_config(service_type, config)
        if service_type == "telegram":
            return self._send_telegram(config, message)
        if service_type == "bark":
            return self._send_bark(config, message)
        if service_type == "serverchan":
            return self._send_serverchan(config, message)
        if service_type == "pushplus":
            return self._send_pushplus(config, message)
        if service_type == "wecom_robot":
            return self._send_wecom_robot(config, message)
        if service_type == "webhook":
            return self._send_webhook(config, message)
        raise ValueError(f"不支持的通知渠道：{service_type}")

    def _validate_config(self, service_type, config):
        required = {
            "telegram": ("bot_token", "chat_id"),
            "bark": ("key",),
            "serverchan": ("send_key",),
            "pushplus": ("token",),
            "wecom_robot": ("key",),
            "webhook": ("url",),
        }.get(service_type)
        if required is None:
            raise ValueError(f"不支持的通知渠道：{service_type}")
        missing = [key for key in required if not str(config.get(key) or "").strip()]
        if missing:
            raise ValueError("配置不完整：" + "、".join(missing))

    def _post_json(self, url, payload, timeout=12):
        response = requests.post(url, json=payload, timeout=timeout)
        response.raise_for_status()
        return response

    def _response_json(self, response):
        try:
            return response.json()
        except ValueError as exc:
            raise ValueError(f"通知服务返回非 JSON 响应：{_compact_error(response.text)}") from exc

    def _assert_provider_ok(self, response, provider, ok_codes=None, ok_field="code", message_fields=None):
        response.raise_for_status()
        data = self._response_json(response)
        ok_codes = set(ok_codes if ok_codes is not None else (0,))
        value = data.get(ok_field)
        if value in ok_codes or str(value) in {str(item) for item in ok_codes}:
            return {"provider": provider, "response": data}
        message_fields = message_fields or ("message", "msg", "errmsg", "description", "error")
        error = next((data.get(key) for key in message_fields if data.get(key)), None)
        raise ValueError(f"{provider} 推送失败：{_compact_error(error or data)}")

    def _send_telegram(self, config, message):
        url = f"https://api.telegram.org/bot{config['bot_token']}/sendMessage"
        payload = {
            "chat_id": config["chat_id"],
            "text": f"{message['title']}\n\n{message['text']}".strip(),
            "disable_web_page_preview": True,
        }
        response = self._post_json(url, payload)
        data = self._response_json(response)
        if data.get("ok") is True:
            return {"provider": "telegram", "message_id": (data.get("result") or {}).get("message_id")}
        raise ValueError(f"Telegram 推送失败：{_compact_error(data.get('description') or data)}")

    def _send_bark(self, config, message):
        server = str(config.get("server") or "https://api.day.app").rstrip("/")
        title = quote(message["title"])
        body = quote(message["text"] or message["title"])
        url = f"{server}/{config['key']}/{title}/{body}"
        response = requests.get(url, timeout=12)
        return self._assert_provider_ok(response, "Bark", ok_codes=(200,), message_fields=("message", "msg"))

    def _send_serverchan(self, config, message):
        url = f"https://sctapi.ftqq.com/{config['send_key']}.send"
        response = requests.post(url, data={"title": message["title"], "desp": message["text"]}, timeout=12)
        return self._assert_provider_ok(response, "Server 酱", ok_codes=(0,), message_fields=("message", "msg"))

    def _send_pushplus(self, config, message):
        payload = {
            "token": config["token"],
            "title": message["title"],
            "content": message["text"],
            "template": "txt",
        }
        if config.get("topic"):
            payload["topic"] = config["topic"]
        response = self._post_json("https://www.pushplus.plus/send", payload)
        return self._assert_provider_ok(response, "PushPlus", ok_codes=(200,), message_fields=("msg", "message"))

    def _send_wecom_robot(self, config, message):
        url = f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={config['key']}"
        payload = {"msgtype": "text", "text": {"content": f"{message['title']}\n{message['text']}".strip()}}
        response = self._post_json(url, payload)
        return self._assert_provider_ok(response, "企业微信机器人", ok_codes=(0,), ok_field="errcode", message_fields=("errmsg", "message", "msg"))

    def _send_webhook(self, config, message):
        url = config["url"]
        method = str(config.get("method") or "POST").upper()
        headers = dict(config.get("headers") or {})
        if method == "GET":
            response = requests.get(url, params=message, headers=headers, timeout=12)
        else:
            response = requests.request(method, url, json=message, headers=headers, timeout=12)
        response.raise_for_status()
        return {"provider": "webhook", "status_code": response.status_code}
