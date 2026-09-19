import types
import unittest
from unittest import mock

from support import patched_module

from src.server import web_server


class WebStartupTests(unittest.TestCase):
    def test_background_services_continue_after_one_fails(self):
        scheduler = mock.Mock(side_effect=RuntimeError("scheduler failed"))

        with (
            mock.patch.object(web_server, "ensure_subscription_scheduler", scheduler),
            self.assertLogs(level="ERROR"),
        ):
            web_server._initialize_background_services()

        scheduler.assert_called_once_with()

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
            # 不使用 mock.patch.dict(sys.modules, ...)：它在退出时会恢复整个
            # sys.modules 快照，把 patch 期间新导入的模块一并丢弃（见 tests/support.py）。
            patched_module("waitress", waitress),
            mock.patch.dict(web_server.os.environ, {"FLASK_DEBUG": "", "HOST": "127.0.0.1", "PORT": "18082"}),
        ):
            web_server.main()

        self.assertEqual(calls, ["thread-started", "serve"])


if __name__ == "__main__":
    unittest.main()
