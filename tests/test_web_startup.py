import sys
import types
import unittest
from unittest import mock

from src.server import web_server


class WebStartupTests(unittest.TestCase):
    def test_background_services_continue_after_one_fails(self):
        scheduler = mock.Mock(side_effect=RuntimeError("scheduler failed"))
        feishu = mock.Mock()
        developer = mock.Mock()

        with (
            mock.patch.object(web_server, "ensure_subscription_scheduler", scheduler),
            mock.patch.object(web_server.feishu_bridge, "start", feishu),
            mock.patch.object(web_server.developer_agent_manager, "reconcile", developer),
            self.assertLogs(level="ERROR"),
        ):
            web_server._initialize_background_services()

        scheduler.assert_called_once_with()
        feishu.assert_called_once_with()
        developer.assert_called_once_with()

    def test_main_serves_without_running_background_services_synchronously(self):
        calls = []
        initializer = mock.Mock()

        class DeferredThread:
            def __init__(self, *, target, name, daemon):
                self.target = target
                self.name = name
                self.daemon = daemon

            def start(self):
                calls.append("thread-started")

        def serve(*args, **kwargs):
            calls.append("serve")
            initializer.assert_not_called()

        waitress = types.SimpleNamespace(serve=serve)
        with (
            mock.patch.object(web_server, "_initialize_background_services", initializer),
            mock.patch.object(web_server.threading, "Thread", DeferredThread),
            mock.patch.dict(sys.modules, {"waitress": waitress}),
            mock.patch.dict(web_server.os.environ, {"FLASK_DEBUG": "", "HOST": "127.0.0.1", "PORT": "18082"}),
        ):
            web_server.main()

        self.assertEqual(calls, ["thread-started", "serve"])


if __name__ == "__main__":
    unittest.main()
