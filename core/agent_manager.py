#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""AudioFlow application Agent runtime.

File organization side effects stay behind application-owned tools so the
download source filter, review decisions and two-stage execution remain
authoritative even when a separate developer Agent has broader permissions.
"""

import base64
import hashlib
import importlib.util
import json
import os
import re
import secrets
import threading
import time
import uuid
from pathlib import Path

import requests
from cryptography.fernet import Fernet, InvalidToken


PROVIDERS = {
    "deepseek": {"name": "DeepSeek", "kind": "openai", "base_url": "https://api.deepseek.com", "model": "deepseek-chat"},
    "openai": {"name": "OpenAI", "kind": "openai", "base_url": "https://api.openai.com/v1", "model": "gpt-4.1-mini"},
    "anthropic": {"name": "Anthropic Claude", "kind": "anthropic", "base_url": "https://api.anthropic.com/v1", "model": "claude-sonnet-4-20250514"},
    "gemini": {"name": "Google Gemini", "kind": "gemini", "base_url": "https://generativelanguage.googleapis.com/v1beta", "model": "gemini-2.5-flash"},
    "openrouter": {"name": "OpenRouter", "kind": "openai", "base_url": "https://openrouter.ai/api/v1", "model": "deepseek/deepseek-chat-v3-0324"},
    "ollama": {"name": "Ollama", "kind": "openai", "base_url": "http://host.docker.internal:11434/v1", "model": "qwen3:8b", "api_key_optional": True},
    "qwen": {"name": "通义千问 / DashScope", "kind": "openai", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-plus"},
    "moonshot": {"name": "Moonshot / Kimi", "kind": "openai", "base_url": "https://api.moonshot.cn/v1", "model": "moonshot-v1-8k"},
    "zhipu": {"name": "智谱 GLM", "kind": "openai", "base_url": "https://open.bigmodel.cn/api/paas/v4", "model": "glm-4-flash"},
    "doubao": {"name": "豆包 / 火山引擎", "kind": "openai", "base_url": "https://ark.cn-beijing.volces.com/api/v3", "model": ""},
    "siliconflow": {"name": "SiliconFlow", "kind": "openai", "base_url": "https://api.siliconflow.cn/v1", "model": "deepseek-ai/DeepSeek-V3"},
    "custom": {"name": "自定义 OpenAI 兼容", "kind": "openai", "base_url": "", "model": ""},
}

SYSTEM_PROMPT = """你是 AudioFlow 的有声书管理助手。你可以帮助用户查看下载任务、检查、复核并执行有声书整理计划。
安全规则：
1. 你不能直接访问文件系统、执行命令、删除或重命名文件。
2. 重命名必须先调用工具生成确定性的完整计划，再由用户通过 AudioFlow 明确确认。
3. 用户明确说“确认/执行计划”并给出计划 ID 时，可以调用 confirm_rename_plan；不要再声称没有执行工具。
4. needs_review 计划不能直接执行。用户同意保留全部风险文件、只整理安全文件时，先调用 resolve_rename_plan_safe，再调用 confirm_rename_plan。
5. 不得声称已经执行尚未确认的整理；遇到歧义时要求用户复核。
6. 回答简洁，清楚说明实际查询或工具执行结果。
7. AI 对风险文件只能提出结构化建议；建议必须经过 AudioFlow 校验和用户确认。
"""

RENAME_REVIEW_RULES = """你是中文有声书文件名复核器。逐条分析提供的风险项，判断真实章节名，不得编造文件、不得输出路径、不得决定最终执行。规则：
1. 广告/运营废话必须去除：求赞求评论求订阅求收藏求分享、卖货（微信号/QQ群/公众号/联系方式）、新书推广（搜：xxx、新书上架）、更新通知与日期排期（每天中午12点更新）、加更补更爆更、冠名打赏、节日祝福、平台出品、中奖名单、带货。
2. 全书完/大结局/全书终/完结是正文结束标识，必须保留；标识周围的广告照常去除（例：第250章 大结局（求订阅、分享、评论）→ 大结局）。
3. 多段式真标题是完整标题，不要因空格切分（例：「朝里 楼里 七国」「真真假假 假假真真」）。
4. 整段都是演播者吐槽、无法切出章节名的（例：「唉呀妈呀！川哥又可以出来领盒饭了！」）选 keep，在 reason 说明，交用户决定。
5. 分集标记是标题一部分，保留：（上）（中）（下）（一）（1）（Ⅰ）（壹）等尾部标记。
6. 「无题」标题保留为「无题」，只去广告。
7. 非章节文件：片花/预告/主题曲/剧情歌/番外/花絮/楔子/序章/彩蛋=专辑内容，建议 accept 且保留原标签（片花就叫「片花」）；更新通知/直播回听/求赞/带货/作者节目（如「小川有话说」）=运营内容，建议 quarantine。
8. 疑似其他书籍的文件（文件名含与本书无关的书名号）选 keep。
9. 不确定时一律 keep；只有能从文件名和相邻章节明确判断时才 rename。
只输出 JSON，不要 Markdown。"""

RENAME_CLEAN_RULES = """你是中文有声书文件名全量清洗器。对每条输入只返回纯标题，不含章节号、序号或扩展名。规则：
1. 去除广告/运营废话：求赞求评论求订阅求收藏求分享、卖货（微信号/QQ群/公众号/联系方式）、新书推广（搜：xxx、新书上架）、更新通知与日期排期、加更补更爆更、冠名打赏、节日祝福、平台出品、中奖名单、带货。
2. 全书完/大结局/全书终/完结是正文结束标识，必须保留；标识周围的广告照常去除。
3. 多段式真标题是完整标题，不要因空格切分；不确定时保留完整标题。
4. 整段都是演播者吐槽且无法切出章节名时，保留规则引擎标题并说明原因。
5. 分集标记是标题的一部分，保留：（上）（中）（下）（一）（1）（Ⅰ）（壹）等尾部标记。
6. 「无题」保留为「无题」，只去广告。
只处理输入条目，不得编造文件，不得输出路径。只输出 JSON，不要 Markdown。"""

TOOL_SCHEMAS = [
    {"type": "function", "function": {"name": "list_downloads", "description": "列出最近的下载任务及状态", "parameters": {"type": "object", "properties": {"status": {"type": "string", "enum": ["all", "active", "completed", "failed"]}, "limit": {"type": "integer", "minimum": 1, "maximum": 50}}}}},
    {"type": "function", "function": {"name": "list_rename_plans", "description": "列出有声书重命名计划", "parameters": {"type": "object", "properties": {"status": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "get_rename_plan", "description": "读取一个重命名计划的详情", "parameters": {"type": "object", "properties": {"plan_id": {"type": "string"}}, "required": ["plan_id"]}}},
    {"type": "function", "function": {"name": "create_rename_plan", "description": "为已完成下载任务或下载目录内的文件夹分析文件并生成待确认的重命名计划；不会执行重命名", "parameters": {"type": "object", "properties": {"task_id": {"type": "string"}, "folder": {"type": "string"}, "album_title": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "analyze_rename_plan_with_ai", "description": "使用当前 AI 模型分析计划中的风险文件并保存建议；不会执行重命名", "parameters": {"type": "object", "properties": {"plan_id": {"type": "string"}}, "required": ["plan_id"]}}},
    {"type": "function", "function": {"name": "apply_ai_rename_suggestions", "description": "应用用户明确接受的 AI 建议到计划；仍需最终确认才会执行", "parameters": {"type": "object", "properties": {"plan_id": {"type": "string"}, "suggestion_ids": {"type": "array", "items": {"type": "string"}}}, "required": ["plan_id", "suggestion_ids"]}}},
    {"type": "function", "function": {"name": "create_rename_rule_draft", "description": "根据计划和已接受的修正生成重命名规则草稿；草稿不会自动启用", "parameters": {"type": "object", "properties": {"plan_id": {"type": "string"}}, "required": ["plan_id"]}}},
    {"type": "function", "function": {"name": "resolve_rename_plan_safe", "description": "复核计划时保留所有风险/特殊文件不动，只让安全章节进入待确认状态；仍不会执行", "parameters": {"type": "object", "properties": {"plan_id": {"type": "string"}}, "required": ["plan_id"]}}},
    {"type": "function", "function": {"name": "confirm_rename_plan", "description": "在用户明确确认后执行一个 pending_confirmation 整理计划", "parameters": {"type": "object", "properties": {"plan_id": {"type": "string"}}, "required": ["plan_id"]}}},
    {"type": "function", "function": {"name": "cancel_rename_plan", "description": "取消一个尚未执行的整理计划", "parameters": {"type": "object", "properties": {"plan_id": {"type": "string"}}, "required": ["plan_id"]}}},
]


def _atomic_write(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


class AgentStore:
    def __init__(self, config_path, sessions_path):
        self.config_path = Path(config_path)
        self.sessions_path = Path(sessions_path)
        self.lock = threading.RLock()
        self._fernet = self._make_fernet()
        self.config = self._load_config()
        self.sessions = self._load_json(self.sessions_path, {"version": 1, "sessions": {}})

    def _make_fernet(self):
        supplied = os.getenv("AUDIOFLOW_AGENT_SECRET") or os.getenv("AUDIOFLOW_COOKIE_SECRET")
        if supplied:
            self.key_source = "environment"
            digest = hashlib.sha256(supplied.encode("utf-8")).digest()
            return Fernet(base64.urlsafe_b64encode(digest))
        key_path = self.config_path.with_name("agent.key")
        if key_path.exists():
            self.key_source = "local-key-file"
            return Fernet(key_path.read_bytes().strip())
        key_path.parent.mkdir(parents=True, exist_ok=True)
        key = Fernet.generate_key()
        key_path.write_bytes(key)
        try:
            os.chmod(key_path, 0o600)
        except OSError:
            pass
        self.key_source = "local-key-file"
        return Fernet(key)

    @staticmethod
    def _load_json(path, fallback):
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else fallback
        except (OSError, ValueError, TypeError):
            return fallback

    def _load_config(self):
        data = self._load_json(self.config_path, {})
        encrypted = data.pop("encrypted_secrets", "")
        secrets_data = {}
        if encrypted:
            try:
                secrets_data = json.loads(self._fernet.decrypt(encrypted.encode("ascii")).decode("utf-8"))
            except (InvalidToken, ValueError, TypeError, json.JSONDecodeError):
                secrets_data = {}
        if isinstance(secrets_data.get("providers"), dict):
            provider_secrets = secrets_data.get("providers") or {}
            developer_secrets = secrets_data.get("developer_agent") or {}
        else:
            provider_secrets = secrets_data
            developer_secrets = {}
        data.setdefault("enabled", False)
        data.setdefault("provider", "deepseek")
        data.setdefault("runner", "native")
        data.setdefault("fast_mode", True)
        data.setdefault("history_messages", 10)
        data.setdefault("providers", {})
        data.setdefault("developer_agent", {})
        for key, value in provider_secrets.items():
            data.setdefault("providers", {}).setdefault(key, {})["api_key"] = value
        if developer_secrets.get("feishu_app_secret"):
            data["developer_agent"]["feishu_app_secret"] = developer_secrets["feishu_app_secret"]
        return data

    def save_config(self, payload):
        with self.lock:
            current = self.config
            next_config = {
                "version": 1,
                "enabled": bool(payload.get("enabled", current.get("enabled", False))),
                "provider": str(payload.get("provider") or current.get("provider") or "deepseek"),
                "runner": str(payload.get("runner") or current.get("runner") or "native"),
                "fast_mode": bool(payload.get("fast_mode", current.get("fast_mode", True))),
                "history_messages": max(4, min(20, int(payload.get("history_messages", current.get("history_messages", 10)) or 10))),
                "providers": {},
                "developer_agent": {},
                "updated_at": int(time.time()),
            }
            incoming = payload.get("providers") if isinstance(payload.get("providers"), dict) else {}
            for provider_id in PROVIDERS:
                old = (current.get("providers") or {}).get(provider_id) or {}
                new = incoming.get(provider_id) if isinstance(incoming.get(provider_id), dict) else {}
                item = {
                    "base_url": str(new.get("base_url") if "base_url" in new else old.get("base_url") or PROVIDERS[provider_id]["base_url"]).strip(),
                    "model": str(new.get("model") if "model" in new else old.get("model") or PROVIDERS[provider_id]["model"]).strip(),
                    "api_key": str(new.get("api_key") or old.get("api_key") or "").strip(),
                }
                next_config["providers"][provider_id] = item
            old_dev = current.get("developer_agent") or {}
            new_dev = payload.get("developer_agent") if isinstance(payload.get("developer_agent"), dict) else {}
            repo_roots = new_dev.get("repo_roots") if "repo_roots" in new_dev else old_dev.get("repo_roots") or ["/workspace"]
            if isinstance(repo_roots, str):
                repo_roots = repo_roots.replace("\r", "\n").replace(",", "\n").splitlines()
            repo_roots = [str(item).strip() for item in (repo_roots or []) if str(item).strip()]
            next_config["developer_agent"] = {
                "enabled": bool(new_dev.get("enabled", old_dev.get("enabled", False))),
                "feishu_app_id": str(new_dev.get("feishu_app_id") if "feishu_app_id" in new_dev else old_dev.get("feishu_app_id") or "").strip(),
                "feishu_app_secret": str(new_dev.get("feishu_app_secret") or old_dev.get("feishu_app_secret") or "").strip(),
                "default_cwd": str(new_dev.get("default_cwd") if "default_cwd" in new_dev else old_dev.get("default_cwd") or "/workspace").strip(),
                "repo_roots": repo_roots or ["/workspace"],
                "require_working_dir": bool(new_dev.get("require_working_dir", old_dev.get("require_working_dir", True))),
                "allowed_users": str(new_dev.get("allowed_users") if "allowed_users" in new_dev else old_dev.get("allowed_users") or "").strip(),
                "allowed_chats": str(new_dev.get("allowed_chats") if "allowed_chats" in new_dev else old_dev.get("allowed_chats") or "").strip(),
            }
            if next_config["provider"] not in PROVIDERS:
                raise ValueError("不支持的 AI 平台")
            if next_config["runner"] not in {"native", "deepseek-harness"}:
                raise ValueError("不支持的 Agent 运行器")
            self.config = next_config
            self._persist_config()
            return self.public_config()

    def _persist_config(self):
        data = {k: v for k, v in self.config.items() if k not in {"providers", "developer_agent"}}
        data["providers"] = {}
        data["developer_agent"] = {k: v for k, v in (self.config.get("developer_agent") or {}).items() if k != "feishu_app_secret"}
        secret_map = {"providers": {}, "developer_agent": {}}
        for provider_id, item in (self.config.get("providers") or {}).items():
            data["providers"][provider_id] = {k: v for k, v in item.items() if k != "api_key"}
            if item.get("api_key"):
                secret_map["providers"][provider_id] = item["api_key"]
        developer_secret = (self.config.get("developer_agent") or {}).get("feishu_app_secret")
        if developer_secret:
            secret_map["developer_agent"]["feishu_app_secret"] = developer_secret
        data["encrypted_secrets"] = self._fernet.encrypt(json.dumps(secret_map).encode("utf-8")).decode("ascii")
        _atomic_write(self.config_path, data)

    def public_config(self):
        result = {k: v for k, v in self.config.items() if k not in {"providers", "developer_agent"}}
        result["providers"] = {}
        configured = self.config.get("providers") or {}
        for provider_id, spec in PROVIDERS.items():
            item = configured.get(provider_id) or {}
            key = str(item.get("api_key") or "")
            result["providers"][provider_id] = {
                **spec,
                "base_url": item.get("base_url") or spec["base_url"],
                "model": item.get("model") or spec["model"],
                "configured": bool(key) or bool(spec.get("api_key_optional")),
                "api_key_masked": (key[:3] + "..." + key[-3:]) if len(key) > 8 else ("已配置" if key else ""),
            }
        developer = dict(self.config.get("developer_agent") or {})
        secret = str(developer.pop("feishu_app_secret", "") or "")
        developer["configured"] = bool(developer.get("feishu_app_id") and secret)
        developer["feishu_app_secret_masked"] = (secret[:3] + "..." + secret[-3:]) if len(secret) > 8 else ("已配置" if secret else "")
        result["developer_agent"] = developer
        return result

    def list_sessions(self):
        items = list((self.sessions.get("sessions") or {}).values())
        items.sort(key=lambda item: item.get("updated_at", 0), reverse=True)
        return [{k: v for k, v in item.items() if k != "messages"} for item in items]

    def get_session(self, session_id):
        return (self.sessions.get("sessions") or {}).get(session_id)

    def add_exchange(self, session_id, user_content, assistant_content, tool_events=None):
        with self.lock:
            now = int(time.time())
            if not session_id:
                session_id = "agt-" + uuid.uuid4().hex[:12]
            session = self.sessions.setdefault("sessions", {}).setdefault(session_id, {
                "id": session_id, "title": user_content[:36], "created_at": now, "messages": [],
            })
            session["messages"].extend([
                {"role": "user", "content": user_content, "created_at": now},
                {"role": "assistant", "content": assistant_content, "tool_events": tool_events or [], "created_at": int(time.time())},
            ])
            session["messages"] = session["messages"][-80:]
            session["updated_at"] = int(time.time())
            session["preview"] = assistant_content[:80]
            _atomic_write(self.sessions_path, self.sessions)
            return session

    def delete_session(self, session_id):
        with self.lock:
            removed = self.sessions.setdefault("sessions", {}).pop(session_id, None)
            if removed:
                _atomic_write(self.sessions_path, self.sessions)
            return bool(removed)


class AgentManager:
    def __init__(self, config_path, sessions_path, tools=None):
        self.store = AgentStore(config_path, sessions_path)
        self.tools = tools or {}
        self.harness_lock = threading.Lock()

    def set_tools(self, tools):
        self.tools = dict(tools or {})

    def status(self):
        harness_available = importlib.util.find_spec("deepseek_harness") is not None
        return {
            "config": self.store.public_config(),
            "harness": {
                "available": harness_available,
                "selected": self.store.config.get("runner") == "deepseek-harness",
                "mode": "restricted-application-tools",
                "message": "SDK 已检测到" if harness_available else "未安装 deepseek-harness-sdk，原生运行时可正常使用",
            },
            "security": {
                "encrypted": True,
                "key_management": self.store.key_source,
                "message": "密钥由 config/agent.key 自动管理" if self.store.key_source == "local-key-file" else "使用部署环境提供的固定密钥",
            },
            "tools": [item["function"]["name"] for item in TOOL_SCHEMAS],
        }

    def _provider_config(self):
        provider_id = self.store.config.get("provider") or "deepseek"
        spec = PROVIDERS[provider_id]
        item = (self.store.config.get("providers") or {}).get(provider_id) or {}
        return provider_id, spec, {
            "base_url": str(item.get("base_url") or spec["base_url"]).rstrip("/"),
            "model": str(item.get("model") or spec["model"]),
            "api_key": str(item.get("api_key") or ""),
        }

    def _validate_ready(self):
        if not self.store.config.get("enabled"):
            raise ValueError("Agent 尚未启用，请先完成模型配置")
        provider_id, spec, config = self._provider_config()
        if not config["base_url"] or not config["model"]:
            raise ValueError("请配置模型名称和 API 地址")
        if not config["api_key"] and not spec.get("api_key_optional"):
            raise ValueError(f"请先配置 {spec['name']} API Key")
        if self.store.config.get("runner") == "deepseek-harness" and importlib.util.find_spec("deepseek_harness") is None:
            raise ValueError("deepseek-harness-sdk 尚未安装；请改用原生运行时或在 Linux 容器中安装 SDK")
        if self.store.config.get("runner") == "deepseek-harness" and provider_id != "deepseek":
            raise ValueError("当前安全 Harness 组合仅支持 DeepSeek；其他平台请使用原生运行时")
        return provider_id, spec, config

    def test_provider(self, provider_id=None):
        current = self.store.config.get("provider")
        if provider_id:
            self.store.config["provider"] = provider_id
        try:
            _pid, spec, config = self._validate_ready()
            response = self._complete(spec, config, [{"role": "user", "content": "只回复 AudioFlow Agent 已连接"}], tools=[])
            return {"connected": True, "reply": response.get("content") or "已连接", "model": config["model"]}
        finally:
            self.store.config["provider"] = current

    @staticmethod
    def _json_object(value):
        candidate = str(value or "").strip()
        if candidate.startswith("```"):
            candidate = candidate.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        try:
            data = json.loads(candidate)
        except (TypeError, ValueError):
            match = re.search(r"\{.*\}", candidate, re.S)
            if not match:
                raise ValueError("AI 未返回可解析的结构化结果")
            try:
                data = json.loads(match.group(0))
            except (TypeError, ValueError) as exc:
                raise ValueError("AI 返回的结构化结果格式错误") from exc
        if not isinstance(data, dict):
            raise ValueError("AI 返回结果必须是 JSON 对象")
        return data

    def analyze_rename_plan(self, plan, max_items=80):
        """Ask the configured model to review only unresolved plan items."""
        _provider_id, spec, config = self._validate_ready()
        unresolved = [
            issue for issue in (plan.get("issues") or [])
            if issue.get("blocking", True) and not issue.get("resolved")
        ]
        issue_map = {}
        for issue in unresolved:
            key = issue.get("relative_source") or issue.get("file")
            if key:
                issue_map.setdefault(key, []).append(issue.get("message") or issue.get("type"))
        candidates = []
        items = plan.get("items") or []
        for index, item in enumerate(items):
            key = item.get("relative_source") or item.get("source_name")
            if key not in issue_map:
                continue
            neighbors = []
            for neighbor in items[max(0, index - 1):index + 2]:
                if neighbor is not item:
                    neighbors.append(neighbor.get("source_name"))
            candidates.append({
                "relative_source": key,
                "kind": item.get("kind"),
                "source_name": item.get("source_name"),
                "current_title": item.get("clean_title") or item.get("special_label") or "",
                "suggested_target": item.get("target_name"),
                "special_type": item.get("special_type"),
                "issues": issue_map[key],
                "neighbors": neighbors,
            })
            if len(candidates) >= max(1, min(120, int(max_items or 80))):
                break
        if not candidates:
            return {"summary": "当前计划没有需要 AI 分析的风险文件。", "suggestions": [], "model": config["model"]}
        contract = {
            "album": plan.get("album") or {},
            "rules": (plan.get("rule_snapshot") or {}).get("rules") or {},
            "candidates": candidates,
            "response_schema": {
                "summary": "简短中文总结",
                "suggestions": [{
                    "relative_source": "必须完全复制输入值",
                    "action": "keep|accept|rename|quarantine",
                    "clean_title": "仅 action=rename 时给出清洗后的纯标题，否则为空",
                    "reason": "中文理由",
                    "confidence": "0 到 1",
                }],
            },
        }
        prompt = (
            RENAME_REVIEW_RULES + "\n"
            + json.dumps(contract, ensure_ascii=False)
        )
        result = self._complete(spec, config, [
            {"role": "system", "content": "严格输出符合约定的 JSON 对象。"},
            {"role": "user", "content": prompt},
        ], tools=[])
        data = self._json_object(result.get("content"))
        data["model"] = config["model"]
        return data

    def clean_titles_batch(self, album, rules, entries, max_tokens=4096):
        """Clean one ordered batch of chapter titles with bounded retries."""
        _provider_id, spec, config = self._validate_ready()
        batch = [dict(entry) for entry in (entries or []) if isinstance(entry, dict)]
        if not batch:
            return {"suggestions": [], "summary": "没有需要清洗的章节。", "model": config["model"]}
        contract = {
            "album": album or {},
            "rules": rules or {},
            "entries": batch,
            "response_schema": {
                "suggestions": [{
                    "relative_source": "必须完全复制输入值",
                    "clean_title": "清洗后纯标题（不含章节号/序号）",
                    "changed": True,
                    "reason": "中文理由",
                    "confidence": 0,
                }],
            },
        }
        prompt = RENAME_CLEAN_RULES + "\n" + json.dumps(contract, ensure_ascii=False)
        errors = []
        for attempt in range(3):
            try:
                result = self._complete(spec, config, [
                    {"role": "system", "content": "严格输出符合约定的 JSON 对象。"},
                    {"role": "user", "content": prompt},
                ], tools=[], max_tokens=max(1024, int(max_tokens or 4096)))
                data = self._json_object(result.get("content"))
                raw_suggestions = data.get("suggestions")
                if not isinstance(raw_suggestions, list):
                    raise ValueError("AI 全量清洗结果缺少 suggestions 数组")
                known = {str(entry.get("relative_source") or "") for entry in batch}
                suggestions = []
                for raw in raw_suggestions:
                    if not isinstance(raw, dict):
                        continue
                    key = str(raw.get("relative_source") or "")
                    if key not in known:
                        continue
                    clean_title = str(raw.get("clean_title") or "").strip()[:220]
                    changed = bool(raw.get("changed"))
                    if "changed" not in raw:
                        current = next((entry.get("current_title") for entry in batch
                                        if str(entry.get("relative_source") or "") == key), "")
                        changed = clean_title != str(current or "").strip()
                    try:
                        confidence = max(0.0, min(1.0, float(raw.get("confidence") or 0)))
                    except (TypeError, ValueError):
                        confidence = 0.0
                    suggestions.append({
                        "relative_source": key,
                        "clean_title": clean_title,
                        "changed": changed,
                        "action": "rename" if changed else "keep",
                        "reason": str(raw.get("reason") or "AI 未提供理由").strip()[:500],
                        "confidence": confidence,
                    })
                return {
                    "suggestions": suggestions,
                    "summary": str(data.get("summary") or "全量清洗建议已生成").strip()[:1000],
                    "model": config["model"],
                }
            except (TypeError, ValueError, requests.RequestException) as exc:
                errors.append(str(exc))
                if attempt < 2:
                    time.sleep((2, 5)[attempt])
        raise ValueError("全量 AI 清洗失败：" + (errors[-1] if errors else "未知错误"))

    def propose_rename_rule_draft(self, plan):
        """Generate a constrained partial rule pack from reviewed plan evidence."""
        _provider_id, spec, config = self._validate_ready()
        analysis = plan.get("ai_analysis") or {}
        if not analysis.get("suggestions"):
            raise ValueError("请先让 AI 分析该计划，再生成规则草稿")
        current = (plan.get("rule_snapshot") or {}).get("rules") or {}
        evidence = {
            "album": plan.get("album") or {},
            "current_rules": current,
            "suggestions": analysis.get("suggestions") or [],
            "applied_ids": analysis.get("applied_ids") or [],
            "allowed_changes": {
                "cleanup": ["ad_keywords", "ad_patterns", "preserve_keywords", "title_exceptions", "split_ad_after_first_space"],
                "special_files": ["content_labels", "operational_labels", "content_default", "operational_default", "unknown_default"],
                "format": ["chapter_template", "special_template", "prefix_width", "chapter_width", "chapter_unit", "prefix_strategy", "prefix_start", "smart_title_separator"],
            },
        }
        prompt = (
            "根据已经复核的有声书计划提出最小规则修改。不要修改文件安全、碰撞、回滚或确认机制。"
            "只输出 JSON：{\"name\":\"...\",\"description\":\"...\",\"scope\":\"global|platform|album\","
            "\"selector\":\"...\",\"rules\":{...}}。只包含确有证据需要变化的字段；正则必须有明确边界。\n"
            + json.dumps(evidence, ensure_ascii=False)
        )
        result = self._complete(spec, config, [
            {"role": "system", "content": "严格输出重命名规则草稿 JSON。"},
            {"role": "user", "content": prompt},
        ], tools=[])
        return self._json_object(result.get("content"))

    def _call_tool(self, name, args):
        if name not in self.tools:
            raise ValueError("工具不可用")
        return self.tools[name](**(args or {}))

    @staticmethod
    def _render_tool_result(name, output):
        if name == "list_downloads":
            tasks = output.get("tasks") or []
            if not tasks:
                return "没有找到符合条件的下载任务。"
            lines = [f"{item.get('title') or item.get('id')} · {item.get('status')} · {item.get('success', 0)}/{item.get('total', 0)} · {item.get('id')}" for item in tasks[:20]]
            return "最近下载任务：\n" + "\n".join(lines)
        if name == "list_rename_plans":
            plans = output.get("plans") or []
            if not plans:
                return "当前没有符合条件的整理计划。"
            lines = [f"{item.get('title') or item.get('id')} · {item.get('status')} · {item.get('id')}" for item in plans[:20]]
            return "整理计划：\n" + "\n".join(lines)
        if name == "get_rename_plan":
            summary = output.get("summary") or {}
            unresolved = [item for item in output.get("issues") or [] if item.get("blocking", True) and not item.get("resolved")]
            return (
                f"计划 {output.get('id')} 当前为 {output.get('status')}。章节 {summary.get('chapters', 0)}，"
                f"待处理 {summary.get('planned', 0)}，待确认问题 {len(unresolved)}。"
            )
        if name == "create_rename_plan":
            return f"计划 {output.get('id') or ''} 已生成，正在等待你确认。"
        message = output.get("message") if isinstance(output, dict) else ""
        if message:
            return str(message)
        if name == "analyze_rename_plan_with_ai":
            return f"AI 风险分析已完成，共生成 {output.get('suggestions', 0)} 条建议；建议尚未应用。"
        if name == "apply_ai_rename_suggestions":
            return f"已把选中的 AI 建议写入计划 {output.get('id')}，仍需最终确认后才会执行。"
        if name == "create_rename_rule_draft":
            return f"规则草稿 {output.get('name') or output.get('id')} 已生成，需在规则中心测试并启用。"
        return "操作已完成。"

    def _fast_route(self, content):
        if not self.store.config.get("fast_mode", True):
            return None
        text = str(content or "").strip()
        plan_match = re.search(r"\b([a-f0-9]{10}|[a-z][a-z0-9-]{7,39})\b", text, re.I)
        plan_id = plan_match.group(1) if plan_match else ""
        route = None
        args = {}
        if plan_id and re.search(r"(?:确认|执行).*(?:计划|重命名)|(?:计划|重命名).*(?:确认|执行)", text):
            route, args = "confirm_rename_plan", {"plan_id": plan_id}
        elif plan_id and re.search(r"取消.*(?:计划|重命名)|(?:计划|重命名).*取消", text):
            route, args = "cancel_rename_plan", {"plan_id": plan_id}
        elif plan_id and "AI" in text.upper() and any(word in text for word in ("分析", "复核")):
            route, args = "analyze_rename_plan_with_ai", {"plan_id": plan_id}
        elif plan_id and "AI" in text.upper() and "应用" in text:
            route, args = "apply_ai_rename_suggestions", {"plan_id": plan_id, "suggestion_ids": []}
        elif plan_id and "规则草稿" in text:
            route, args = "create_rename_rule_draft", {"plan_id": plan_id}
        elif any(word in text for word in ("列出", "查看")) and "整理计划" in text:
            route, args = "list_rename_plans", {"status": ""}
        elif any(word in text for word in ("最近下载", "下载任务", "完成的下载")) and "生成" not in text:
            route, args = "list_downloads", {"status": "all", "limit": 20}
        elif text == "为最近完成的手动下载生成整理计划":
            downloads = self._call_tool("list_downloads", {"status": "completed", "limit": 10})
            tasks = downloads.get("tasks") or []
            if not tasks:
                return "没有找到已完成的下载任务。", []
            route, args = "create_rename_plan", {"task_id": tasks[0]["id"]}
        if not route or route not in self.tools:
            return None
        try:
            output = self._call_tool(route, args)
            event = {"name": route, "arguments": args, "status": "success", "result": output}
        except Exception as exc:
            output = {"error": str(exc)}
            event = {"name": route, "arguments": args, "status": "error", "error": str(exc)}
        answer = str(output.get("error") or self._render_tool_result(route, output))
        return answer, [event]

    def chat(self, content, session_id=None):
        content = str(content or "").strip()
        if not content:
            raise ValueError("消息不能为空")
        _provider_id, spec, config = self._validate_ready()
        started = time.monotonic()
        fast = self._fast_route(content)
        if fast:
            answer, tool_events = fast
            saved = self.store.add_exchange(session_id, content, answer, tool_events)
            return {
                "session": saved, "message": saved["messages"][-1],
                "latency_ms": int((time.monotonic() - started) * 1000), "mode": "local-fast-path",
            }
        session = self.store.get_session(session_id) if session_id else None
        history_limit = max(4, min(20, int(self.store.config.get("history_messages") or 10)))
        history = list((session or {}).get("messages") or [])[-history_limit:]
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.extend({"role": item.get("role"), "content": item.get("content") or ""} for item in history)
        messages.append({"role": "user", "content": content})
        tool_events = []
        answer = ""
        for _ in range(3):
            result = self._complete(spec, config, messages, tools=TOOL_SCHEMAS)
            calls = result.get("tool_calls") or []
            if not calls:
                answer = str(result.get("content") or "处理完成")
                break
            messages.append({"role": "assistant", "content": result.get("content") or "", "tool_calls": calls})
            for call in calls:
                name = call.get("name") or ""
                args = call.get("arguments") or {}
                try:
                    if name not in self.tools:
                        raise ValueError("工具不可用")
                    output = self.tools[name](**args)
                    event = {"name": name, "arguments": args, "status": "success", "result": output}
                except Exception as exc:
                    output = {"error": str(exc)}
                    event = {"name": name, "arguments": args, "status": "error", "error": str(exc)}
                tool_events.append(event)
                messages.append({"role": "tool", "tool_call_id": call.get("id"), "name": name, "content": json.dumps(output, ensure_ascii=False, default=str)})
            if (self.store.config.get("fast_mode", True) and calls
                    and all(event.get("status") == "success" for event in tool_events[-len(calls):])):
                answer = "\n".join(
                    self._render_tool_result(event["name"], event.get("result") or {})
                    for event in tool_events[-len(calls):]
                )
                break
        if not answer:
            answer = "工具调用次数过多，已停止。请缩小请求范围后重试。"
        saved = self.store.add_exchange(session_id, content, answer, tool_events)
        return {
            "session": saved, "message": saved["messages"][-1],
            "latency_ms": int((time.monotonic() - started) * 1000), "mode": "model",
        }

    def _complete(self, spec, config, messages, tools, max_tokens=1024):
        if self.store.config.get("runner") == "deepseek-harness":
            return self._harness(config, messages, tools, max_tokens=max_tokens)
        if spec["kind"] == "anthropic":
            return self._anthropic(config, messages, tools, max_tokens=max_tokens)
        if spec["kind"] == "gemini":
            return self._gemini(config, messages, tools, max_tokens=max_tokens)
        return self._openai(config, messages, tools, max_tokens=max_tokens)

    def _harness(self, config, messages, tools, max_tokens=1024):
        """Run Harness without shell/filesystem plugins.

        Harness returns a validated tool proposal; AudioFlow itself executes the
        application callback and keeps all confirmation rules server-side.
        """
        from deepseek_harness import DeepSeekHarness

        contract = {
            "response_schema": {
                "content": "给用户的中文回复",
                "tool_calls": [{"id": "唯一字符串", "name": "工具名", "arguments": {}}],
            },
            "available_tools": [item["function"] for item in tools],
            "messages": messages,
        }
        prompt = (
            "根据下面的对话继续处理。只输出一个 JSON 对象，不要 Markdown。"
            "需要工具时 content 可为空；不需要工具时 tool_calls 必须为空。\n"
            + json.dumps(contract, ensure_ascii=False)
        )
        cordis = Path(__file__).with_name("agent_harness.cordis.yml")
        runtime_root = self.store.sessions_path.with_name("agent_harness_sessions")
        workspace = self.store.sessions_path.with_name("agent_harness_workspace")
        runtime_root.mkdir(parents=True, exist_ok=True)
        workspace.mkdir(parents=True, exist_ok=True)
        with self.harness_lock:
            old_env = {key: os.environ.get(key) for key in ("DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL")}
            os.environ["DEEPSEEK_API_KEY"] = config["api_key"]
            os.environ["DEEPSEEK_BASE_URL"] = config["base_url"]
            try:
                with DeepSeekHarness(
                    provider="deepseek-official",
                    model=config["model"],
                    max_tokens=max(1024, int(max_tokens or 1024)),
                    cwd=str(workspace),
                    session_root=str(runtime_root),
                    cordis=str(cordis),
                ) as harness:
                    response = str(harness.run(prompt).final_response or "")
            finally:
                for key, value in old_env.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value
        candidate = response.strip()
        if candidate.startswith("```"):
            candidate = candidate.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        try:
            data = json.loads(candidate)
        except (TypeError, ValueError):
            return {"content": response, "tool_calls": []}
        calls = []
        allowed = {item["function"]["name"] for item in tools}
        for item in data.get("tool_calls") or []:
            if item.get("name") in allowed and isinstance(item.get("arguments") or {}, dict):
                calls.append({
                    "id": str(item.get("id") or secrets.token_hex(6)),
                    "name": item["name"],
                    "arguments": item.get("arguments") or {},
                })
        return {"content": str(data.get("content") or ""), "tool_calls": calls}

    @staticmethod
    def _request(method, url, **kwargs):
        response = requests.request(method, url, timeout=(10, 45), **kwargs)
        try:
            data = response.json()
        except ValueError:
            data = {}
        if response.status_code >= 400:
            detail = data.get("error") if isinstance(data, dict) else None
            if isinstance(detail, dict):
                detail = detail.get("message")
            raise ValueError(str(detail or f"模型服务返回 HTTP {response.status_code}"))
        return data

    def _openai(self, config, messages, tools, max_tokens=1024):
        headers = {"Content-Type": "application/json"}
        if config["api_key"]:
            headers["Authorization"] = "Bearer " + config["api_key"]
        converted = []
        for item in messages:
            if item.get("role") == "assistant" and item.get("tool_calls"):
                converted.append({
                    "role": "assistant",
                    "content": item.get("content") or None,
                    "tool_calls": [
                        {
                            "id": call.get("id"),
                            "type": "function",
                            "function": {
                                "name": call.get("name"),
                                "arguments": json.dumps(call.get("arguments") or {}, ensure_ascii=False),
                            },
                        }
                        for call in item["tool_calls"]
                    ],
                })
            else:
                converted.append(item)
        payload = {"model": config["model"], "messages": converted, "temperature": 0.2, "max_tokens": max(1, int(max_tokens or 1024))}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        data = self._request("POST", config["base_url"] + "/chat/completions", headers=headers, json=payload)
        message = ((data.get("choices") or [{}])[0].get("message") or {})
        calls = []
        for item in message.get("tool_calls") or []:
            fn = item.get("function") or {}
            try:
                arguments = json.loads(fn.get("arguments") or "{}")
            except (TypeError, ValueError):
                arguments = {}
            calls.append({"id": item.get("id") or secrets.token_hex(6), "name": fn.get("name"), "arguments": arguments})
        return {"content": message.get("content") or "", "tool_calls": calls}

    def _anthropic(self, config, messages, tools, max_tokens=1024):
        system = "\n".join(str(item.get("content") or "") for item in messages if item.get("role") == "system")
        converted = []
        for item in messages:
            role = item.get("role")
            if role == "system":
                continue
            if role == "tool":
                converted.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": item.get("tool_call_id"), "content": item.get("content") or ""}]})
            elif role == "assistant" and item.get("tool_calls"):
                blocks = ([{"type": "text", "text": item.get("content")}] if item.get("content") else [])
                blocks += [{"type": "tool_use", "id": call.get("id"), "name": call.get("name"), "input": call.get("arguments") or {}} for call in item["tool_calls"]]
                converted.append({"role": "assistant", "content": blocks})
            else:
                converted.append({"role": role, "content": item.get("content") or ""})
        anthropic_tools = [{"name": t["function"]["name"], "description": t["function"]["description"], "input_schema": t["function"]["parameters"]} for t in tools]
        payload = {"model": config["model"], "system": system, "messages": converted, "max_tokens": max(1, int(max_tokens or 1024)), "temperature": 0.2}
        if anthropic_tools:
            payload["tools"] = anthropic_tools
        data = self._request("POST", config["base_url"] + "/messages", headers={"x-api-key": config["api_key"], "anthropic-version": "2023-06-01", "content-type": "application/json"}, json=payload)
        text = "\n".join(item.get("text", "") for item in data.get("content") or [] if item.get("type") == "text")
        calls = [{"id": item.get("id"), "name": item.get("name"), "arguments": item.get("input") or {}} for item in data.get("content") or [] if item.get("type") == "tool_use"]
        return {"content": text, "tool_calls": calls}

    def _gemini(self, config, messages, tools, max_tokens=1024):
        system = "\n".join(str(item.get("content") or "") for item in messages if item.get("role") == "system")
        contents = []
        for item in messages:
            role = item.get("role")
            if role == "system":
                continue
            if role == "tool":
                parts = [{"functionResponse": {"name": item.get("name"), "response": {"result": item.get("content") or ""}}}]
                contents.append({"role": "user", "parts": parts})
            elif role == "assistant" and item.get("tool_calls"):
                parts = ([{"text": item.get("content")}] if item.get("content") else [])
                parts += [{"functionCall": {"name": call.get("name"), "args": call.get("arguments") or {}}} for call in item["tool_calls"]]
                contents.append({"role": "model", "parts": parts})
            else:
                contents.append({"role": "model" if role == "assistant" else "user", "parts": [{"text": item.get("content") or ""}]})
        declarations = [{"name": t["function"]["name"], "description": t["function"]["description"], "parameters": t["function"]["parameters"]} for t in tools]
        payload = {"systemInstruction": {"parts": [{"text": system}]}, "contents": contents, "generationConfig": {"temperature": 0.2, "maxOutputTokens": max(1, int(max_tokens or 1024))}}
        if declarations:
            payload["tools"] = [{"functionDeclarations": declarations}]
        url = f"{config['base_url']}/models/{config['model']}:generateContent?key={config['api_key']}"
        data = self._request("POST", url, headers={"Content-Type": "application/json"}, json=payload)
        parts = (((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or [])
        text = "\n".join(item.get("text", "") for item in parts if item.get("text"))
        calls = []
        for item in parts:
            call = item.get("functionCall")
            if call:
                calls.append({"id": secrets.token_hex(6), "name": call.get("name"), "arguments": call.get("args") or {}})
        return {"content": text, "tool_calls": calls}
