import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

try:
    import requests  # noqa: F401
except ModuleNotFoundError:
    class _RequestException(Exception):
        pass

    sys.modules["requests"] = types.SimpleNamespace(
        RequestException=_RequestException,
        request=lambda *args, **kwargs: None,
        post=lambda *args, **kwargs: None,
    )

if not hasattr(sys.modules["requests"], "RequestException"):
    sys.modules["requests"].RequestException = Exception

from core.agent_manager import AgentManager, AgentStore, PROVIDERS
from core.developer_agent_manager import DeveloperAgentManager


class AgentStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_api_keys_are_encrypted_and_redacted(self):
        store = AgentStore(self.root / "agent.json", self.root / "sessions.json")
        public = store.save_config({
            "enabled": True,
            "provider": "deepseek",
            "providers": {"deepseek": {"api_key": "sk-super-secret-value", "model": "deepseek-chat"}},
        })
        raw = (self.root / "agent.json").read_text(encoding="utf-8")
        self.assertNotIn("sk-super-secret-value", raw)
        self.assertNotIn("api_key\"", json.dumps(public))
        self.assertTrue(public["providers"]["deepseek"]["configured"])
        loaded = AgentStore(self.root / "agent.json", self.root / "sessions.json")
        self.assertEqual(loaded.config["providers"]["deepseek"]["api_key"], "sk-super-secret-value")

    def test_provider_catalog_contains_supported_routes(self):
        expected = {"deepseek", "openai", "anthropic", "gemini", "openrouter", "ollama", "qwen", "moonshot", "zhipu", "doubao", "siliconflow", "custom"}
        self.assertEqual(set(PROVIDERS), expected)

    def test_developer_feishu_secret_is_encrypted_and_redacted(self):
        store = AgentStore(self.root / "agent.json", self.root / "sessions.json")
        public = store.save_config({
            "developer_agent": {
                "enabled": True,
                "feishu_app_id": "cli_developer",
                "feishu_app_secret": "developer-secret-value",
                "default_cwd": "/workspace/project",
                "repo_roots": ["/workspace"],
                "allowed_users": "ou_owner",
            },
        })
        raw = (self.root / "agent.json").read_text(encoding="utf-8")
        self.assertNotIn("developer-secret-value", raw)
        self.assertNotIn("feishu_app_secret", public["developer_agent"])
        self.assertTrue(public["developer_agent"]["configured"])
        loaded = AgentStore(self.root / "agent.json", self.root / "sessions.json")
        self.assertEqual(loaded.config["developer_agent"]["feishu_app_secret"], "developer-secret-value")


class DeveloperAgentManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.agent = AgentManager(self.root / "agent.json", self.root / "sessions.json")
        self.agent.store.save_config({
            "enabled": True,
            "provider": "deepseek",
            "providers": {"deepseek": {"api_key": "sk-test", "model": "deepseek-chat"}},
            "developer_agent": {
                "enabled": True,
                "feishu_app_id": "cli_developer",
                "feishu_app_secret": "feishu-secret",
                "default_cwd": "/workspace/project",
                "repo_roots": ["/workspace"],
                "require_working_dir": True,
                "allowed_users": "ou_owner",
            },
        })
        runtime = self.root / "runtime"
        executable = runtime / "node_modules" / ".bin" / "dsh"
        executable.parent.mkdir(parents=True)
        executable.write_text("#!/bin/sh\n", encoding="utf-8")
        self.notifications = types.SimpleNamespace(load=lambda: {"services": []})
        self.manager = DeveloperAgentManager(self.agent, self.notifications, self.root / "config", runtime)

    def tearDown(self):
        self.temp.cleanup()

    def test_profile_contains_workspace_boundaries_but_no_secrets(self):
        config, _exe, roots, cwd, route, model = self.manager._validate()
        self.manager._write_profile(config, roots, cwd, route, model)
        profile = (self.root / "config" / "dsh" / "profiles" / "audioflow-developer" / "cordis.patch.yml").read_text(encoding="utf-8")
        self.assertIn("@dsh-feishu/dsh-feishu", profile)
        self.assertIn("/workspace/project", profile)
        self.assertIn("ou_owner", profile)
        self.assertNotIn("feishu-secret", profile)
        self.assertNotIn("sk-test", profile)

    @patch("core.developer_agent_manager.subprocess.run")
    def test_profile_bootstrap_links_local_feishu_plugin_without_secrets(self, run_mock):
        run_mock.return_value = Mock(returncode=0, stdout="installed")
        executable = self.manager._executable()

        self.manager._ensure_profile(executable)

        args = run_mock.call_args.args[0]
        kwargs = run_mock.call_args.kwargs
        self.assertEqual(args[:5], [str(executable), "plugin", "--profile", "audioflow-developer", "add"])
        self.assertTrue(args[5].startswith("link:"))
        self.assertIn("@dsh-feishu", args[5])
        self.assertEqual(kwargs["env"]["DSH_HOME"], str(self.root / "config" / "dsh"))
        command_text = " ".join(args)
        self.assertNotIn("feishu-secret", command_text)
        self.assertNotIn("sk-test", command_text)

    def test_notification_bot_app_id_cannot_be_reused(self):
        self.notifications = types.SimpleNamespace(load=lambda: {"services": [{
            "type": "feishu", "config": {"app_id": "cli_developer"},
        }]})
        self.manager.notifications = self.notifications
        with self.assertRaisesRegex(ValueError, "独立的飞书应用"):
            self.manager._validate()

    def test_default_cwd_must_stay_inside_repo_roots(self):
        self.agent.store.config["developer_agent"]["default_cwd"] = "/etc"
        with self.assertRaisesRegex(ValueError, "允许的项目根目录"):
            self.manager._validate()


class FakeAgentManager(AgentManager):
    def __init__(self, root, responses, tools):
        super().__init__(root / "agent.json", root / "sessions.json", tools)
        self.responses = list(responses)
        self.store.save_config({
            "enabled": True,
            "provider": "deepseek",
            "providers": {"deepseek": {"api_key": "sk-test", "model": "deepseek-chat"}},
        })

    def _complete(self, spec, config, messages, tools):
        return self.responses.pop(0)


class AgentRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_tool_proposal_only_creates_confirmation_plan(self):
        calls = []

        def create_plan(task_id):
            calls.append(task_id)
            return {"id": "abc123", "confirmation_required": True}

        manager = FakeAgentManager(self.root, [
            {"content": "", "tool_calls": [{"id": "call-1", "name": "create_rename_plan", "arguments": {"task_id": "task-7"}}]},
            {"content": "计划已生成，等待你确认。", "tool_calls": []},
        ], {"create_rename_plan": create_plan})
        result = manager.chat("整理刚下载的书")
        self.assertEqual(calls, ["task-7"])
        self.assertIn("等待你确认", result["message"]["content"])
        event = result["message"]["tool_events"][0]
        self.assertTrue(event["result"]["confirmation_required"])

    def test_explicit_confirmation_calls_application_execution_tool(self):
        calls = []

        def confirm_plan(plan_id):
            calls.append(plan_id)
            return {"id": plan_id, "status": "completed"}

        manager = FakeAgentManager(self.root, [
            {"content": "", "tool_calls": [{"id": "call-1", "name": "confirm_rename_plan", "arguments": {"plan_id": "abc1234567"}}]},
            {"content": "整理已完成。", "tool_calls": []},
        ], {"confirm_rename_plan": confirm_plan})
        result = manager.chat("确认执行计划 abc1234567")
        self.assertEqual(calls, ["abc1234567"])
        self.assertEqual(result["message"]["tool_events"][0]["result"]["status"], "completed")

    def test_unknown_tool_is_not_executed(self):
        manager = FakeAgentManager(self.root, [
            {"content": "", "tool_calls": [{"id": "call-1", "name": "run_shell", "arguments": {"command": "rm"}}]},
            {"content": "无法执行该操作。", "tool_calls": []},
        ], {})
        result = manager.chat("删除文件")
        self.assertEqual(result["message"]["tool_events"][0]["status"], "error")
        self.assertEqual(result["message"]["tool_events"][0]["error"], "工具不可用")

    def test_common_list_command_uses_local_fast_path_without_model_call(self):
        manager = FakeAgentManager(self.root, [], {
            "list_rename_plans": lambda status="": {
                "plans": [{"id": "abc1234567", "title": "测试书", "status": "pending_confirmation"}],
            },
        })

        result = manager.chat("列出等待确认的整理计划")

        self.assertEqual(result["mode"], "local-fast-path")
        self.assertIn("abc1234567", result["message"]["content"])
        self.assertEqual(manager.responses, [])

    def test_ai_plan_analysis_requires_structured_result(self):
        manager = FakeAgentManager(self.root, [{
            "content": '{"summary":"已复核","suggestions":[{"relative_source":"0001.mp3","action":"keep","clean_title":"","reason":"信息不足","confidence":0.4}]}',
            "tool_calls": [],
        }], {})
        plan = {
            "album": {"title": "测试书"},
            "items": [{"source_name": "0001.mp3", "relative_source": "0001.mp3", "kind": "chapter", "clean_title": "无题"}],
            "issues": [{"relative_source": "0001.mp3", "message": "标题无法确定", "blocking": True, "resolved": False}],
        }

        result = manager.analyze_rename_plan(plan)

        self.assertEqual(result["suggestions"][0]["action"], "keep")
        self.assertEqual(result["model"], "deepseek-chat")

    def test_openai_request_does_not_log_or_return_key(self):
        manager = AgentManager(self.root / "agent.json", self.root / "sessions.json")
        response = {"choices": [{"message": {"content": "ok"}}]}
        with patch.object(manager, "_request", return_value=response) as request_mock:
            result = manager._openai({"base_url": "https://example.test/v1", "model": "m", "api_key": "secret"}, [{"role": "user", "content": "hi"}], [])
        self.assertEqual(result["content"], "ok")
        headers = request_mock.call_args.kwargs["headers"]
        self.assertEqual(headers["Authorization"], "Bearer secret")
        self.assertNotIn("secret", json.dumps(result))

    def test_openai_tool_history_uses_standard_wire_shape(self):
        manager = AgentManager(self.root / "agent.json", self.root / "sessions.json")
        response = {"choices": [{"message": {"content": "done"}}]}
        messages = [
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "name": "list_downloads", "arguments": {"limit": 3}}]},
            {"role": "tool", "tool_call_id": "c1", "name": "list_downloads", "content": "{}"},
        ]
        with patch.object(manager, "_request", return_value=response) as request_mock:
            manager._openai({"base_url": "https://example.test/v1", "model": "m", "api_key": "secret"}, messages, [])
        wire_call = request_mock.call_args.kwargs["json"]["messages"][0]["tool_calls"][0]
        self.assertEqual(wire_call["type"], "function")
        self.assertEqual(wire_call["function"]["name"], "list_downloads")
        self.assertEqual(json.loads(wire_call["function"]["arguments"]), {"limit": 3})


if __name__ == "__main__":
    unittest.main()
