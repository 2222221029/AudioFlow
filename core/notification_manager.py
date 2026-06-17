#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import logging
import os
import time
from copy import deepcopy
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

import requests

from .platform_config import config_dir


class _SafeFmt(dict):
    def __missing__(self, key):
        return ""


def render_template(template, fields):
    """安全渲染：{name} 占位，缺失变量按空字符串，渲染异常回退原文。"""
    try:
        return str(template or "").format_map(_SafeFmt(fields or {}))
    except Exception:
        return str(template or "")


def wecom_album_url(platform, album_id, fallback=""):
    """各平台官方专辑页 URL（用于卡片点击跳转），未知平台回退 fallback。"""
    aid = str(album_id or "").strip()
    if not aid:
        return fallback
    mapping = {
        "喜马拉雅": f"https://www.ximalaya.com/album/{aid}",
        "懒人听书": f"https://www.lrts.me/album/{aid}",
        "蜻蜓FM": f"https://www.qingting.fm/channels/{aid}",
        "酷我听书": f"http://www.kuwo.cn/album_detail/{aid}",
        "荔枝FM": f"https://www.lizhi.fm/album/{aid}",
    }
    return mapping.get(str(platform or "").strip()) or fallback


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
    "wecom_app": "企业微信应用",
    "wecom_robot": "企业微信机器人",
    "webhook": "通用 Webhook",
}

# 企业微信交互指令消息模板（可在 UI 配置；变量用 {name} 占位，缺失变量按空字符串处理）
DEFAULT_WECOM_TEMPLATES = {
    "search_item_title": "{index}. {title}",
    "search_item_desc": "{platform} · {author} · {episodes}章\n回复「订阅 {index}」或「下载 {index}」",
    "subscribe_title": "✅ 已订阅：{title}",
    "subscribe_desc": "章节数：{episodes}{job_suffix}",
    "download_title": "⬇️ 已加入下载：{title}",
    "download_desc": "章节数：{episodes}\n任务 ID：{task_id}",
    "search_empty": "🔍 没有搜索到：{keyword}（平台：{platform}）",
    "processing_search": "⏳ 正在搜索，结果会以卡片形式推送给你…",
    "processing_subscribe": "⏳ 正在订阅，处理结果会推送给你…",
    "processing_download": "⏳ 正在创建下载任务，结果会推送给你…",
    "help_title": "AudioFlow 企业微信指令",
    "help_desc": "帮助：显示指令\n状态：服务版本与任务数\n搜索 关键词：全平台搜索（卡片推送）\n搜索 平台 关键词：指定平台，如「搜索 喜马拉雅 三体」\n下一页 / 上一页：翻看搜索结果\n订阅 序号 / 下载 序号：操作搜索结果",
    # 场景通知卡片（notify_<scene>_title/_desc），download_partial/stopped 复用 download_completed
    "notify_subscription_queued_title": "🆕 订阅发现新章节：{title}",
    "notify_subscription_queued_desc": "平台：{platform}\n新增/缺失：{missing_count} 章\n任务：{task_id}",
    "notify_subscription_checked_title": "🔎 订阅检测发现缺失：{title}",
    "notify_subscription_checked_desc": "平台：{platform}\n缺失：{missing_count} 章",
    "notify_download_completed_title": "✅ 下载完成：{title}",
    "notify_download_completed_desc": "平台：{platform}\n成功：{success} 章 · 失败：{failed} 章\n任务：{task_id}",
    "notify_download_failed_title": "❌ 下载失败：{title}",
    "notify_download_failed_desc": "错误：{error}\n任务：{task_id}",
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
            "wecom_templates": deepcopy(DEFAULT_WECOM_TEMPLATES),
            "updated_at": _now(),
        }

    def _merge_templates(self, raw):
        templates = deepcopy(DEFAULT_WECOM_TEMPLATES)
        for key, value in (raw or {}).items():
            if key in DEFAULT_WECOM_TEMPLATES and isinstance(value, str) and value.strip():
                templates[key] = value
        return templates

    def get_wecom_templates(self):
        return self._merge_templates(self.load().get("wecom_templates"))

    def save_wecom_templates(self, templates):
        current = deepcopy(self.load())
        current["wecom_templates"] = templates or {}
        self.save(current)
        return self.get_wecom_templates()

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
        base["wecom_templates"] = self._merge_templates(data.get("wecom_templates"))
        base["services"] = [self._normalize_service(item) for item in (data.get("services") or []) if isinstance(item, dict)]
        self._config = base
        return self._config

    def save(self, config):
        data = self.default_config()
        data.update({k: v for k, v in (config or {}).items() if k in {"enabled", "scenes", "services"}})
        scenes = deepcopy(DEFAULT_SCENES)
        scenes.update(data.get("scenes") or {})
        data["scenes"] = scenes
        data["wecom_templates"] = self._merge_templates((config or {}).get("wecom_templates"))
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
            for key in ("token", "bot_token", "send_key", "key", "secret", "encoding_aes_key", "chat_id", "url"):
                if config.get(key):
                    config[f"{key}_masked"] = _mask(config.get(key), keep=5 if key == "url" else 4)
            for key in ("token", "bot_token", "send_key", "key", "secret", "encoding_aes_key", "_access_token"):
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
        if service_type == "wecom_app":
            return bool(config.get("corp_id") and config.get("agent_id") and config.get("secret"))
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
        if service_type == "wecom_app":
            return self._send_wecom_app(config, message)
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
            "wecom_app": ("corp_id", "agent_id", "secret"),
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

    def _wecom_api_base(self, config):
        return str(config.get("api_base") or "https://qyapi.weixin.qq.com").rstrip("/")

    def _wecom_access_token(self, config):
        cache_key = "_access_token"
        cached = config.get(cache_key) or {}
        if cached.get("token") and cached.get("expires_at", 0) > time.time() + 60:
            return cached["token"]
        response = requests.get(
            f"{self._wecom_api_base(config)}/cgi-bin/gettoken",
            params={"corpid": config["corp_id"], "corpsecret": config["secret"]},
            timeout=12,
        )
        data = self._response_json(response)
        if data.get("errcode") not in (0, "0"):
            raise ValueError(f"企业微信应用获取 access_token 失败：{_compact_error(data.get('errmsg') or data)}")
        token = data.get("access_token")
        if not token:
            raise ValueError("企业微信应用获取 access_token 失败：响应缺少 access_token")
        config[cache_key] = {"token": token, "expires_at": time.time() + int(data.get("expires_in") or 7200)}
        return token

    def send_wecom_app_text(self, config, content, to_user=None):
        token = self._wecom_access_token(config)
        payload = {
            "touser": str(to_user or config.get("to_user") or "@all").strip() or "@all",
            "msgtype": "text",
            "agentid": int(config["agent_id"]),
            "text": {"content": _clean_text(content)},
            "safe": 0,
        }
        response = self._post_json(f"{self._wecom_api_base(config)}/cgi-bin/message/send?access_token={token}", payload)
        return self._assert_provider_ok(response, "企业微信应用", ok_codes=(0,), ok_field="errcode", message_fields=("errmsg", "message", "msg"))

    def send_wecom_app_news(self, config, articles, to_user=None):
        """主动推送图文卡片消息。articles: [{title, description, url, picurl}]，最多 8 条。"""
        token = self._wecom_access_token(config)
        arts = []
        for a in (articles or [])[:8]:
            if not isinstance(a, dict):
                continue
            art = {"title": _clean_text(a.get("title") or "无标题", 128)}
            if a.get("description"):
                art["description"] = _clean_text(a.get("description") or "", 512)
            if a.get("url"):
                art["url"] = str(a.get("url"))
            if a.get("picurl"):
                art["picurl"] = str(a.get("picurl"))
            arts.append(art)
        if not arts:
            return self.send_wecom_app_text(config, "（无内容）", to_user=to_user)
        payload = {
            "touser": str(to_user or config.get("to_user") or "@all").strip() or "@all",
            "msgtype": "news",
            "agentid": int(config["agent_id"]),
            "news": {"articles": arts},
        }
        response = self._post_json(f"{self._wecom_api_base(config)}/cgi-bin/message/send?access_token={token}", payload)
        return self._assert_provider_ok(response, "企业微信应用", ok_codes=(0,), ok_field="errcode", message_fields=("errmsg", "message", "msg"))

    def _send_wecom_app(self, config, message):
        payload = message.get("payload") or {}
        album = payload.get("album") or {}
        task = payload.get("task") or {}
        scene = str(message.get("scene") or "")
        # download_partial/stopped 复用 download_completed 模板
        tpl_scene = "download_completed" if scene.startswith("download_") and scene != "download_failed" else scene
        templates = self.get_wecom_templates()
        title_tpl = templates.get(f"notify_{tpl_scene}_title")
        desc_tpl = templates.get(f"notify_{tpl_scene}_desc")
        if title_tpl and desc_tpl:
            fields = {
                "title": album.get("title") or task.get("title") or message.get("title") or "",
                "platform": album.get("platform") or "",
                "missing_count": payload.get("missing_count", ""),
                "task_id": payload.get("task_id") or task.get("id") or task.get("task_id") or "",
                "success": payload.get("success", ""),
                "failed": payload.get("failed", ""),
                "error": payload.get("error", ""),
            }
            article = {
                "title": render_template(title_tpl, fields),
                "description": render_template(desc_tpl, fields),
            }
            if album.get("cover"):
                article["picurl"] = album.get("cover")
            url = wecom_album_url(
                album.get("platform"),
                album.get("id") or album.get("album_id") or album.get("book_id"),
                os.getenv("PUBLIC_BASE_URL") or "",
            )
            if url:
                article["url"] = url
            return self.send_wecom_app_news(config, [article])
        content = f"{message['title']}\n{message['text']}".strip()
        return self.send_wecom_app_text(config, content)

    def _send_wecom_robot(self, config, message):
        url = self._wecom_robot_url(config["key"])
        payload = {"msgtype": "text", "text": {"content": f"{message['title']}\n{message['text']}".strip()}}
        response = self._post_json(url, payload)
        return self._assert_provider_ok(response, "企业微信机器人", ok_codes=(0,), ok_field="errcode", message_fields=("errmsg", "message", "msg"))

    def _wecom_robot_url(self, value):
        value = str(value or "").strip()
        if not value:
            raise ValueError("企业微信机器人配置不完整：key")
        if value.startswith(("http://", "https://")):
            parsed = urlparse(value)
            key = (parse_qs(parsed.query).get("key") or [""])[0].strip()
            if parsed.netloc != "qyapi.weixin.qq.com" or parsed.path != "/cgi-bin/webhook/send" or not key:
                raise ValueError("企业微信机器人 Webhook URL 无效，请填写企业微信机器人完整 Webhook 地址或 key 参数")
            return f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={key}"
        if "key=" in value:
            key = (parse_qs(value.split("?", 1)[-1]).get("key") or [""])[0].strip()
            if key:
                return f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={key}"
        return f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={value}"

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
