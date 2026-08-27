import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

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

from core.agent_manager import AgentManager


class StubAgentManager(AgentManager):
    def __init__(self, root, responses):
        super().__init__(root / "agent.json", root / "sessions.json")
        self.responses = list(responses)
        self.calls = []
        self.store.save_config({
            "enabled": True,
            "provider": "deepseek",
            "providers": {"deepseek": {"api_key": "sk-test", "model": "deepseek-chat"}},
        })

    def _complete(self, spec, config, messages, tools, max_tokens=1024):
        self.calls.append({"messages": messages, "max_tokens": max_tokens})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class AgentFullCleanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def _response(entries):
        return {"content": json.dumps({
            "suggestions": [
                {
                    "relative_source": entry["relative_source"],
                    "clean_title": entry["current_title"] + "·AI",
                    "changed": True,
                    "reason": "去除播者附加文案",
                    "confidence": 0.9,
                }
                for entry in entries
            ]
        }, ensure_ascii=False)}

    def test_batch_contract_uses_full_clean_token_budget(self):
        entries = [
            {"relative_source": "001.mp3", "source_name": "001.mp3", "current_title": "开始", "chapter": 1, "unit": "集", "neighbors": ["002.mp3"]},
            {"relative_source": "002.mp3", "source_name": "002.mp3", "current_title": "继续", "chapter": 2, "unit": "集", "neighbors": ["001.mp3"]},
        ]
        manager = StubAgentManager(self.root, [self._response(entries)])
        result = manager.clean_titles_batch({"title": "测试书"}, {}, entries)

        self.assertEqual(len(result["suggestions"]), 2)
        self.assertEqual(result["suggestions"][0]["action"], "rename")
        self.assertEqual(manager.calls[0]["max_tokens"], 4096)
        self.assertIn("多段式真标题", manager.calls[0]["messages"][1]["content"])

    @patch("core.agent_manager.time.sleep")
    def test_batch_retries_with_two_backoff_delays(self, sleep_mock):
        entries = [{"relative_source": "001.mp3", "source_name": "001.mp3", "current_title": "开始"}]
        manager = StubAgentManager(self.root, [ValueError("invalid"), ValueError("timeout"), self._response(entries)])

        result = manager.clean_titles_batch({}, {}, entries)

        self.assertEqual(len(result["suggestions"]), 1)
        self.assertEqual(sleep_mock.call_args_list[0].args, (2,))
        self.assertEqual(sleep_mock.call_args_list[1].args, (5,))
        self.assertEqual(len(manager.calls), 3)


if __name__ == "__main__":
    unittest.main()
