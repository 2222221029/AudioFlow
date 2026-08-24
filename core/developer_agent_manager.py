#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Lifecycle manager for the native dsh + PGZXB/dsh-feishu developer Agent."""

import logging
import json
import os
import subprocess
import threading
import time
from pathlib import Path, PurePosixPath

CATALOG_ROUTES = {
    "deepseek": "deepseek",
    "openai": "openai",
    "anthropic": "anthropic",
    "gemini": "google",
    "openrouter": "openrouter",
}


def _lines(value):
    if isinstance(value, str):
        value = value.replace("\r", "\n").replace(",", "\n").splitlines()
    return [str(item).strip() for item in (value or []) if str(item).strip()]


def _runtime_path(value):
    text = str(value or "").strip()
    return PurePosixPath(text) if text.startswith("/") else Path(text)


class DeveloperAgentManager:
    def __init__(self, agent_manager, notification_manager, config_root, runtime_root):
        self.agent = agent_manager
        self.notifications = notification_manager
        self.config_root = Path(config_root) / "dsh"
        self.runtime_root = Path(runtime_root)
        self.profile = "audioflow-developer"
        self.lock = threading.RLock()
        self.process = None
        self.log_handle = None
        self.started_at = 0
        self.last_error = ""

    def _executable(self):
        candidates = [
            self.runtime_root / "node_modules" / ".bin" / "dsh",
            self.runtime_root / "node_modules" / ".bin" / "dsh.cmd",
        ]
        return next((item for item in candidates if item.exists()), None)

    def status(self):
        with self.lock:
            running = self.process is not None and self.process.poll() is None
            code = None if not self.process else self.process.poll()
        config = self.agent.store.config.get("developer_agent") or {}
        return {
            "available": self._executable() is not None,
            "enabled": bool(config.get("enabled")),
            "configured": bool(config.get("feishu_app_id") and config.get("feishu_app_secret")),
            "running": running,
            "pid": self.process.pid if running else None,
            "exit_code": code,
            "started_at": self.started_at or None,
            "last_error": self.last_error,
            "profile": self.profile,
            "workspace": config.get("default_cwd") or "/workspace",
            "repo_roots": _lines(config.get("repo_roots")) or ["/workspace"],
            "mode": "native-dsh-feishu-full-code-agent",
        }

    def _provider(self):
        provider_id, spec, model_config = self.agent._provider_config()
        route = CATALOG_ROUTES.get(provider_id) or provider_id
        profile = {
            "apiKeyEnv": "AUDIOFLOW_DEVELOPER_API_KEY",
            "baseURL": model_config["base_url"],
        }
        if provider_id not in CATALOG_ROUTES:
            profile.update({
                "displayName": spec.get("name") or provider_id,
                "api": "openai-completions",
                "models": [{"id": model_config["model"], "name": model_config["model"]}],
            })
        return route, model_config, profile

    def _validate(self):
        config = self.agent.store.config.get("developer_agent") or {}
        if not config.get("enabled"):
            raise ValueError("完整代码 Agent 尚未启用")
        if not config.get("feishu_app_id") or not config.get("feishu_app_secret"):
            raise ValueError("请配置开发 Agent 专用飞书 App ID 和 App Secret")
        if not _lines(config.get("allowed_users")) and not _lines(config.get("allowed_chats")):
            raise ValueError("完整代码 Agent 必须配置用户或群聊白名单")
        roots = [_runtime_path(item) for item in (_lines(config.get("repo_roots")) or ["/workspace"])]
        cwd = _runtime_path(config.get("default_cwd") or "/workspace")
        if config.get("require_working_dir", True) and not any(cwd == root or root in cwd.parents for root in roots):
            raise ValueError("默认工作目录必须位于允许的项目根目录内")
        executable = self._executable()
        if not executable:
            raise ValueError("原生 DSH 运行时未安装，请重新构建 Docker 镜像")
        route, model, _profile = self._provider()
        if not model.get("model") or not model.get("base_url"):
            raise ValueError("当前 AI 平台缺少模型或 API 地址")
        if not model.get("api_key"):
            raise ValueError("当前 AI 平台尚未配置 API Key")
        for service in self.notifications.load().get("services") or []:
            if service.get("type") == "feishu" and (service.get("config") or {}).get("app_id") == config.get("feishu_app_id"):
                raise ValueError("完整代码 Agent 必须使用独立的飞书应用，不能与 AudioFlow 通知机器人共用 App ID")
        return config, executable, roots, cwd, route, model

    def _write_profile(self, config, roots, cwd, route, model):
        profile_dir = self.config_root / "profiles" / self.profile
        profile_dir.mkdir(parents=True, exist_ok=True)
        _route, _model, provider_profile = self._provider()
        patch = [
            {
                "id": "llm-pi-ai",
                "name": "@deepseek-ai/dsh-llm-pi-ai",
                "config": {"providers": {route: provider_profile}},
            },
            {
                "id": "feishu",
                "name": "@dsh-feishu/dsh-feishu",
                "config": {
                    "defaultCwd": str(cwd),
                    "dataDir": str(self.config_root / "feishu"),
                    "provider": route,
                    "model": model["model"],
                    "allowedUsers": _lines(config.get("allowed_users")),
                    "allowedChats": _lines(config.get("allowed_chats")),
                    "repoRoots": [str(item) for item in roots],
                    "requireWorkingDir": bool(config.get("require_working_dir", True)),
                    "unknownCommand": "passthrough",
                },
            },
        ]
        target = profile_dir / "cordis.patch.yml"
        tmp = target.with_suffix(".yml.tmp")
        # JSON is a YAML subset and avoids serializing secrets or custom tags.
        tmp.write_text(json.dumps(patch, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, target)

    def _ensure_profile(self, executable):
        profile_dir = self.config_root / "profiles" / self.profile
        if (profile_dir / "package.json").exists():
            return
        env = os.environ.copy()
        env["DSH_HOME"] = str(self.config_root)
        plugin = self.runtime_root / "node_modules" / "@dsh-feishu" / "dsh-feishu"
        result = subprocess.run(
            [str(executable), "plugin", "--profile", self.profile, "add", f"link:{plugin}"],
            cwd=str(self.runtime_root),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=90,
            shell=os.name == "nt",
        )
        if result.returncode:
            raise ValueError(f"初始化 DSH profile 失败：{result.stdout[-500:]}")

    def start(self):
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                return self.status()
            config, executable, roots, cwd, route, model = self._validate()
            self._ensure_profile(executable)
            self._write_profile(config, roots, cwd, route, model)
            self.config_root.mkdir(parents=True, exist_ok=True)
            Path(str(cwd)).mkdir(parents=True, exist_ok=True)
            log_path = self.config_root / "developer-agent.log"
            self.log_handle = open(log_path, "ab", buffering=0)
            env = os.environ.copy()
            env.update({
                "DSH_HOME": str(self.config_root),
                "FEISHU_APP_ID": config["feishu_app_id"],
                "FEISHU_APP_SECRET": config["feishu_app_secret"],
                "FEISHU_ALLOWED_USERS": ",".join(_lines(config.get("allowed_users"))),
                "FEISHU_ALLOWED_CHATS": ",".join(_lines(config.get("allowed_chats"))),
                "AUDIOFLOW_DEVELOPER_API_KEY": model["api_key"],
                "DSH_PERMISSION_MODE": "workspace-write",
            })
            if self.agent.store.config.get("provider") == "deepseek":
                env["DEEPSEEK_API_KEY"] = model["api_key"]
                env["DEEPSEEK_BASE_URL"] = model["base_url"]
            try:
                self.process = subprocess.Popen(
                    [str(executable), "--profile", self.profile],
                    cwd=str(cwd),
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=self.log_handle,
                    stderr=subprocess.STDOUT,
                    shell=os.name == "nt",
                )
            except Exception:
                self.log_handle.close()
                self.log_handle = None
                raise
            self.started_at = int(time.time())
            self.last_error = ""
            threading.Thread(target=self._monitor, args=(self.process,), daemon=True).start()
            logging.info("native dsh-feishu developer Agent started: pid=%s route=%s", self.process.pid, route)
            return self.status()

    def _monitor(self, process):
        code = process.wait()
        with self.lock:
            if process is self.process:
                if code:
                    self.last_error = f"DSH 进程退出，代码 {code}；请查看 config/dsh/developer-agent.log"
                if self.log_handle:
                    self.log_handle.close()
                    self.log_handle = None
        logging.warning("native dsh-feishu developer Agent exited: code=%s", code)

    def stop(self):
        with self.lock:
            process = self.process
            if not process or process.poll() is not None:
                return self.status()
            process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        return self.status()

    def reconcile(self):
        enabled = bool((self.agent.store.config.get("developer_agent") or {}).get("enabled"))
        try:
            if enabled:
                if self.process is not None and self.process.poll() is None:
                    self.stop()
                return self.start()
            return self.stop()
        except Exception as exc:
            self.last_error = str(exc)
            logging.exception("native dsh-feishu developer Agent reconciliation failed")
            return self.status()
