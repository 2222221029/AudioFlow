#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Restricted Feishu bridge for the AudioFlow Agent.

The interaction model follows PGZXB/dsh-feishu, while deliberately exposing
only AudioFlow-owned operations. No shell, editor, workspace, or filesystem
tool is registered here.
"""

import json
import logging
import os
import secrets
import threading
import time
from pathlib import Path


_ACTION_LOCK = threading.RLock()


def _atomic_write(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _string_list(value):
    if isinstance(value, str):
        value = value.replace("\r", "\n").replace(",", "\n").splitlines()
    return [str(item).strip() for item in (value or []) if str(item).strip()]


class FeishuActionStore:
    """Persistent, one-time authorization records for interactive cards."""

    def __init__(self, path):
        self.path = Path(path)

    def _load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {"version": 1, "actions": {}}
        except (OSError, ValueError, TypeError):
            return {"version": 1, "actions": {}}

    def create(self, plan_id, service_id, *, user_id="", chat_id="", ttl=86400):
        nonce = secrets.token_urlsafe(24)
        now = int(time.time())
        with _ACTION_LOCK:
            data = self._load()
            actions = data.setdefault("actions", {})
            actions[nonce] = {
                "plan_id": str(plan_id),
                "service_id": str(service_id),
                "user_id": str(user_id or ""),
                "chat_id": str(chat_id or ""),
                "created_at": now,
                "expires_at": now + max(300, int(ttl)),
                "used_at": 0,
            }
            data["actions"] = {
                key: item for key, item in actions.items()
                if item.get("expires_at", 0) > now and not (item.get("used_at") and item.get("used_at") < now - 86400)
            }
            _atomic_write(self.path, data)
        return nonce

    def consume(self, nonce, service_id, plan_id, user_id, chat_id):
        now = int(time.time())
        with _ACTION_LOCK:
            data = self._load()
            item = (data.get("actions") or {}).get(str(nonce or ""))
            if not item:
                raise ValueError("卡片操作不存在或已过期")
            if item.get("used_at"):
                raise ValueError("该卡片已经处理，请勿重复操作")
            if item.get("expires_at", 0) <= now:
                raise ValueError("该卡片已过期")
            if item.get("service_id") != str(service_id) or item.get("plan_id") != str(plan_id):
                raise ValueError("卡片操作与重命名计划不匹配")
            if item.get("user_id") and item.get("user_id") != str(user_id or ""):
                raise PermissionError("该卡片不属于当前用户")
            if item.get("chat_id") and item.get("chat_id") != str(chat_id or ""):
                raise PermissionError("该卡片不属于当前会话")
            item["used_at"] = now
            _atomic_write(self.path, data)
            return dict(item)


def feishu_authorized(config, user_id, chat_id):
    """Fail closed; configured user and chat allowlists are both enforced."""
    users = set(_string_list(config.get("allowed_users")))
    chats = set(_string_list(config.get("allowed_chats")))
    if not users and not chats:
        return False
    if users and str(user_id or "") not in users:
        return False
    if chats and str(chat_id or "") not in chats:
        return False
    return True


def _attr(value, *path):
    current = value
    for key in path:
        if current is None:
            return None
        current = current.get(key) if isinstance(current, dict) else getattr(current, key, None)
    return current


class FeishuBridge:
    def __init__(self, notification_manager, agent_manager, rename_plan_manager, action_path):
        self.notifications = notification_manager
        self.agent = agent_manager
        self.rename_plans = rename_plan_manager
        self.actions = FeishuActionStore(action_path)
        self.lock = threading.RLock()
        self.started = set()

    def _services(self):
        return [
            item for item in self.notifications.load().get("services") or []
            if item.get("type") == "feishu" and item.get("enabled", True)
        ]

    def start(self):
        """Start one official Feishu WebSocket client per configured app."""
        try:
            import lark_oapi as lark
        except ImportError:
            if self._services():
                logging.error("Feishu channel is configured but lark-oapi is not installed")
            return
        for service in self._services():
            config = service.get("config") or {}
            app_id = str(config.get("app_id") or "").strip()
            app_secret = str(config.get("app_secret") or "").strip()
            fingerprint = (service.get("id"), app_id, app_secret[-6:])
            if not app_id or not app_secret or fingerprint in self.started:
                continue
            handler = (
                lark.EventDispatcherHandler.builder("", "")
                .register_p2_im_message_receive_v1(lambda data, sid=service.get("id"): self._on_message(sid, data))
                .register_p2_card_action_trigger(lambda data, sid=service.get("id"): self._on_card_action(sid, data))
                .build()
            )
            client = lark.ws.Client(app_id, app_secret, event_handler=handler, log_level=lark.LogLevel.WARNING)
            thread = threading.Thread(target=self._run_client, args=(client, service.get("id")), daemon=True)
            with self.lock:
                self.started.add(fingerprint)
            thread.start()

    @staticmethod
    def _run_client(client, service_id):
        try:
            client.start()
        except Exception:
            logging.exception("Feishu WebSocket client stopped: %s", service_id)

    def _service(self, service_id):
        return next((item for item in self._services() if item.get("id") == service_id), None)

    def _identity(self, data):
        user_id = (
            _attr(data, "event", "sender", "sender_id", "open_id")
            or _attr(data, "event", "operator", "open_id")
            or _attr(data, "operator", "open_id")
            or _attr(data, "operator", "operator_id", "open_id")
            or ""
        )
        chat_id = (
            _attr(data, "event", "message", "chat_id")
            or _attr(data, "event", "context", "open_chat_id")
            or _attr(data, "context", "open_chat_id")
            or ""
        )
        return str(user_id), str(chat_id)

    def _on_message(self, service_id, data):
        service = self._service(service_id)
        if not service:
            return
        user_id, chat_id = self._identity(data)
        config = service.get("config") or {}
        if not feishu_authorized(config, user_id, chat_id):
            logging.warning("Rejected unauthorized Feishu message: service=%s user=%s chat=%s", service_id, user_id, chat_id)
            return
        message_type = _attr(data, "event", "message", "message_type") or ""
        if message_type != "text":
            self.notifications.send_feishu_text(config, "目前仅支持文字消息。", chat_id or user_id, "chat_id" if chat_id else "open_id")
            return
        raw = _attr(data, "event", "message", "content") or "{}"
        try:
            text = str((json.loads(raw) or {}).get("text") or "").strip()
        except (TypeError, ValueError):
            text = ""
        if not text:
            return
        threading.Thread(
            target=self._agent_reply,
            args=(service_id, config, user_id, chat_id, text),
            daemon=True,
        ).start()

    def _agent_reply(self, service_id, config, user_id, chat_id, text):
        target, target_type = (chat_id, "chat_id") if chat_id else (user_id, "open_id")
        try:
            result = self.agent.chat(text, f"feishu:{service_id}:{user_id or chat_id}")
            message = (result.get("message") or {}).get("content") or "Agent 没有返回内容。"
        except Exception as exc:
            logging.exception("Feishu Agent request failed: %s", service_id)
            message = f"Agent 处理失败：{exc}"
        try:
            self.notifications.send_feishu_text(config, message, target, target_type)
        except Exception:
            logging.exception("Feishu Agent reply failed: %s", service_id)

    def _on_card_action(self, service_id, data):
        service = self._service(service_id)
        if not service:
            return {"toast": {"type": "error", "content": "飞书渠道已停用"}}
        user_id, chat_id = self._identity(data)
        config = service.get("config") or {}
        if not feishu_authorized(config, user_id, chat_id):
            return {"toast": {"type": "error", "content": "无权执行此操作"}}
        value = _attr(data, "event", "action", "value") or _attr(data, "action", "value") or {}
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                value = {}
        action = str(value.get("action") or "")
        plan_id = str(value.get("plan_id") or "")
        try:
            self.actions.consume(value.get("nonce"), service_id, plan_id, user_id, chat_id)
            if action == "confirm_rename":
                plan = self.rename_plans.confirm(plan_id)
                content = f"已确认并执行整理计划 {plan_id}。"
            elif action == "resolve_safe_rename":
                plan = self.rename_plans.resolve_safe(plan_id)
                plan = self.rename_plans.confirm(plan_id)
                content = f"已保留全部风险/特殊文件，并执行其余安全整理：{plan_id}。"
            elif action == "cancel_rename":
                plan = self.rename_plans.cancel(plan_id)
                content = f"已取消整理计划 {plan_id}。"
            else:
                raise ValueError("不支持的卡片操作")
            logging.info("Feishu rename action completed: plan=%s status=%s", plan_id, plan.get("status"))
            return {"toast": {"type": "success", "content": content}}
        except (KeyError, ValueError, PermissionError, OSError) as exc:
            return {"toast": {"type": "error", "content": str(exc)}}
