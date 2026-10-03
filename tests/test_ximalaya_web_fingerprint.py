"""core/ximalaya_web_fingerprint 的回归测试：纯代码（node+jsdom）取号链路。"""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from unittest import mock

from core import ximalaya_web_fingerprint as wfp


class FakeProc:
    def __init__(self, stdout="OPENID=ACM0MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=\n",
                 stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


class WfpNodeFetchTest(unittest.TestCase):
    def setUp(self):
        self._wfp = wfp.wfp_path()
        self._dir = Path(self._wfp).parent

    def _without_cached(self):
        # 移除缓存让 ensure 走生成分支
        try:
            Path(self._wfp).unlink()
        except OSError:
            pass

    def test_find_node_returns_path_when_available(self):
        with mock.patch("shutil.which", return_value="/usr/bin/node"):
            self.assertEqual(wfp.find_node(), "/usr/bin/node")
        with mock.patch("shutil.which", return_value=None):
            self.assertEqual(wfp.find_node(), "")

    def test_ensure_wfp_node_parses_openid_and_saves(self):
        self._without_cached()
        target = Path(self._dir) / "wfp_test.json"
        try:
            target.unlink()
        except OSError:
            pass
        with mock.patch("shutil.which", return_value="/usr/bin/node"), \
             mock.patch("subprocess.run", return_value=FakeProc()) as run:
            result = wfp.ensure_wfp_node(wait=10, out_path=target)
        self.assertTrue(result["wfp_ready"], result)
        self.assertEqual(result["source"], "node-sdk")
        self.assertTrue(result["generated"])
        run.assert_called_once()
        # 调用时传了 --out 与 --timeout
        args = run.call_args.args[0]
        self.assertEqual(args[0], "/usr/bin/node")
        self.assertIn("--timeout", args)
        data = json.loads(target.read_text(encoding="utf-8"))
        self.assertTrue(data["wfp"].startswith("ACM0"))

    def test_ensure_wfp_node_reports_failure_without_node(self):
        with mock.patch("shutil.which", return_value=None):
            result = wfp.ensure_wfp_node(wait=5)
        self.assertFalse(result["wfp_ready"])
        self.assertIn("node", result["error"])

    def test_ensure_wfp_node_reports_timeout(self):
        import subprocess
        with mock.patch("shutil.which", return_value="/usr/bin/node"), \
             mock.patch("subprocess.run",
                        side_effect=subprocess.TimeoutExpired("cmd", 60)):
            result = wfp.ensure_wfp_node(wait=5)
        self.assertFalse(result["wfp_ready"])
        self.assertEqual(result["error"], "取号超时")

    def test_ensure_wfp_uses_cache_when_present(self):
        try:
            Path(self._wfp).parent.mkdir(parents=True, exist_ok=True)
            Path(self._wfp).write_text(json.dumps({"wfp": "ACM0CACHEDVALUE0123456789"}),
                                       encoding="utf-8")
            with mock.patch("shutil.which", return_value="/usr/bin/node"), \
                 mock.patch("subprocess.run") as run:
                result = wfp.ensure_wfp()
            self.assertTrue(result["wfp_ready"])
            self.assertEqual(result["source"], "cached")
            run.assert_not_called()
        finally:
            try:
                Path(self._wfp).unlink()
            except OSError:
                pass

    def test_ensure_wfp_node_path_wins_over_playwright(self):
        # node 链路成功时不应走 playwright fetch_wfp
        self._without_cached()
        target = Path(self._dir) / "wfp_test2.json"
        try:
            target.unlink()
        except OSError:
            pass
        with mock.patch("shutil.which", return_value="/usr/bin/node"), \
             mock.patch("subprocess.run", return_value=FakeProc()), \
             mock.patch.object(wfp, "fetch_wfp", side_effect=AssertionError("不应调用 playwright")):
            result = wfp.ensure_wfp_node(wait=10, out_path=target)
        self.assertTrue(result["wfp_ready"])



if __name__ == "__main__":
    unittest.main()