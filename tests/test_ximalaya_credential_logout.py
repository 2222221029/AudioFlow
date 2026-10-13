"""喜马拉雅凭证联动清理的回归测试。

背景（用户报告）：在账号管理页删除喜马拉雅（网页登录）后，
「移动版 V4：本地出票就绪」与「网页指纹：已就绪」两项状态不消失 ——
因为移动端凭证存放在 cookies.json 的独立字段 ``xmly_mobile``，
网页指纹落在独立文件 ``config/ximalaya_wfp.json``，
而删除接口只清了 ``xmly`` 一个字段。

本模块验证：
* DELETE /api/cookies/xmly 会联动删除 xmly_mobile 并清除 wfp 文件；
* DELETE 其他平台不做联动（各平台凭证互不影响）；
* POST /api/cookies/clear 同样完成上述清理；
* 设备 UUID（ximalaya_device_uuid）是本机指纹，明确**保留**不删。
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core.platform_config import config_dir


def _fake_wfp_file() -> Path:
    path = Path(config_dir()) / "ximalaya_wfp.json"
    path.write_text(json.dumps({"wfp": "testwfpvalue12345", "openId": "open-1"}), encoding="utf-8")
    return path


class XimalayaCredentialCascadeTests(unittest.TestCase):
    """删除/清空 Cookie 时，喜马拉雅三件套应联动清理。"""

    def setUp(self):
        import src.server.web_server as web_server

        self.web_server = web_server
        # 独立 cookies 存储，避免污染/依赖真实配置文件
        self.saved_cookies = dict(web_server.cookie_manager.cookies)
        web_server.cookie_manager.cookies = {}
        # 独立 wfp 文件
        self.wfp_path = _fake_wfp_file()

    def tearDown(self):
        self.web_server.cookie_manager.cookies = self.saved_cookies
        try:
            self.wfp_path.unlink()
        except OSError:
            pass

    def _save_credentials(self):
        cm = self.web_server.cookie_manager
        cm.cookies["xmly"] = "1&_token=1&a; 1&_device=android&uuid&9.4.52"
        cm.cookies["xmly_mobile"] = {
            "cookie": "1&_device=android&uuid&9.4.52; 1&_token=1&a",
            "x_tk": "Tticket",
        }
        cm.save = mock.Mock()  # 不落盘

    def _client(self):
        return self.web_server.app.test_client()

    def _auth_patch(self):
        return mock.patch.object(self.web_server, "current_user", return_value={"username": "t"})

    def test_delete_xmly_cascades_mobile_and_wfp(self):
        self._save_credentials()
        self.assertTrue(self.wfp_path.exists())

        with (
            self._auth_patch(),
            mock.patch.object(self.web_server.cookie_manager, "load"),
            mock.patch.object(self.web_server.search_manager, "set_cookie"),
            mock.patch.object(self.web_server.search_manager, "set_ximalaya_mobile_credentials") as set_mobile,
        ):
            response = self._client().delete("/api/cookies/xmly")

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["deleted"], True)
        # 联动清理项必须在响应中可见
        self.assertIn("移动端V4凭证", data.get("cleared", []))
        self.assertIn("网页指纹", data.get("cleared", []))
        # 存储确实被清
        self.assertNotIn("xmly", self.web_server.cookie_manager.cookies)
        self.assertNotIn("xmly_mobile", self.web_server.cookie_manager.cookies)
        # 下载器内存态同步清空
        set_mobile.assert_called_once_with({})
        # wfp 文件被删除 → 状态回到「未生成」
        self.assertFalse(self.wfp_path.exists())

    def test_delete_other_platform_does_not_cascade(self):
        self._save_credentials()
        self.wfp_path.write_text(json.dumps({"wfp": "keepme1234567"}), encoding="utf-8")

        with (
            self._auth_patch(),
            mock.patch.object(self.web_server.cookie_manager, "load"),
            mock.patch.object(self.web_server.search_manager, "set_cookie"),
        ):
            response = self._client().delete("/api/cookies/lrts")

        self.assertEqual(response.status_code, 200)
        # 其他平台删除不影响喜马拉雅凭证
        self.assertIn("xmly_mobile", self.web_server.cookie_manager.cookies)
        self.assertTrue(self.wfp_path.exists())
        self.assertEqual(response.get_json().get("cleared"), [])

    def test_delete_xmly_when_nothing_to_clear_still_ok(self):
        # 未保存任何凭证（且无 wfp 文件）时删除不应报错，cleared 为空
        self.wfp_path.unlink()
        with (
            self._auth_patch(),
            mock.patch.object(self.web_server.cookie_manager, "load"),
            mock.patch.object(self.web_server.search_manager, "set_cookie"),
        ):
            response = self._client().delete("/api/cookies/xmly")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json().get("cleared"), [])

    def test_clear_all_cascades_wfp_and_memory(self):
        self._save_credentials()
        with (
            self._auth_patch(),
            mock.patch.object(self.web_server.cookie_manager, "load"),
            mock.patch.object(self.web_server.search_manager, "set_cookie") as set_cookie,
            mock.patch.object(self.web_server.search_manager, "set_ximalaya_mobile_credentials") as set_mobile,
        ):
            response = self._client().post("/api/cookies/clear")

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data["ok"])
        self.assertIn("网页指纹", data.get("cleared_extras", []))
        self.assertFalse(self.wfp_path.exists())
        # 内存态清理覆盖喜马拉雅与主要平台
        set_mobile.assert_called_once_with({})
        called_platforms = {call.args[0] for call in set_cookie.call_args_list}
        self.assertIn("xmly", called_platforms)

    def test_device_uuid_survives_logout(self):
        """设备 UUID 是本机指纹（文档要求持久化复用），退出账号不得删除。"""
        uuid_path = Path(config_dir()) / "ximalaya_device_uuid"
        created = not uuid_path.exists()
        if created:
            uuid_path.write_text("0123456789abcdef0123456789abcdef", encoding="utf-8")
        try:
            self._save_credentials()
            with (
                self._auth_patch(),
                mock.patch.object(self.web_server.cookie_manager, "load"),
                mock.patch.object(self.web_server.search_manager, "set_cookie"),
            ):
                self._client().delete("/api/cookies/xmly")
            self.assertTrue(uuid_path.exists())
        finally:
            if created:
                uuid_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
