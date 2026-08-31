import sys
import unittest
from unittest import mock

import requests

from core.qr_login import QRSession, _DRIVERS, _drive_netease


def _response(payload, cookies=None):
    response = mock.Mock()
    response.json.return_value = payload
    response.cookies = requests.cookies.cookiejar_from_dict(cookies or {})
    return response


class _FakeHttpSession:
    def __init__(self, responses):
        self.headers = {}
        self.cookies = requests.cookies.RequestsCookieJar()
        self.responses = list(responses)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        response = self.responses.pop(0)
        self.cookies.update(response.cookies)
        return response


class _FakeQrImage:
    def save(self, output, format=None):
        output.write(b"fake-png")


class NeteaseQrLoginTest(unittest.TestCase):
    def _run(self, responses):
        http = _FakeHttpSession(responses)
        qrcode_module = mock.Mock()
        qrcode_module.make.return_value = _FakeQrImage()
        session = QRSession("netease")
        with (
            mock.patch("requests.Session", return_value=http),
            mock.patch(
                "core.netease_cloud_audiobook_manager.NeteaseCloudAudiobookManager._encrypt_params",
                side_effect=lambda payload: dict(payload),
            ) as encrypt,
            mock.patch("core.qr_login.time.sleep", return_value=None),
            mock.patch.dict(sys.modules, {"qrcode": qrcode_module}),
        ):
            _drive_netease(session)
        return session, http, qrcode_module, encrypt

    def test_netease_driver_generates_qr_and_saves_confirmed_cookie(self):
        session, http, qrcode_module, encrypt = self._run([
            _response({"code": 200, "unikey": "qr-key"}),
            _response({"code": 801, "message": "等待扫码"}, {"NMTID": "device"}),
            _response({"code": 802, "message": "已扫码"}),
            _response(
                {"code": 803, "message": "授权登录成功"},
                {"MUSIC_U": "member-token", "__csrf": "csrf-token"},
            ),
        ])

        self.assertEqual(session.status, "success")
        self.assertEqual(session.cookies["MUSIC_U"], "member-token")
        self.assertEqual(session.cookies["__csrf"], "csrf-token")
        self.assertTrue(session.qr_image.startswith("data:image/png;base64,"))
        qrcode_module.make.assert_called_once_with("https://music.163.com/login?codekey=qr-key")
        self.assertTrue(http.calls[0][0].endswith("/weapi/login/qrcode/unikey"))
        self.assertTrue(http.calls[-1][0].endswith("/weapi/login/qrcode/client/login"))
        self.assertEqual(http.calls[0][1]["data"], {"type": 1})
        self.assertEqual(http.calls[-1][1]["data"], {"key": "qr-key", "type": 1})
        self.assertEqual(encrypt.call_count, 4)

    def test_netease_driver_rejects_success_without_account_cookie(self):
        session, _, _, _ = self._run([
            _response({"code": 200, "unikey": "qr-key"}),
            _response({"code": 803, "message": "授权登录成功"}, {"NMTID": "device"}),
        ])

        self.assertEqual(session.status, "failed")
        self.assertIn("未返回账号 Cookie", session.message)

    def test_netease_driver_accepts_cookie_from_response_body(self):
        session, _, _, _ = self._run([
            _response({"code": 200, "unikey": "qr-key"}),
            _response({
                "code": 803,
                "message": "授权登录成功",
                "cookie": "MUSIC_U=body-token; __csrf=body-csrf",
            }),
        ])

        self.assertEqual(session.status, "success")
        self.assertEqual(session.cookies["MUSIC_U"], "body-token")
        self.assertEqual(session.cookies["__csrf"], "body-csrf")

    def test_netease_driver_reports_expired_qr(self):
        session, _, _, _ = self._run([
            _response({"code": 200, "unikey": "qr-key"}),
            _response({"code": 800, "message": "二维码已过期"}),
        ])

        self.assertEqual(session.status, "expired")
        self.assertIn("已过期", session.message)

    def test_netease_is_registered_as_qr_platform(self):
        self.assertIs(_DRIVERS["netease"], _drive_netease)


if __name__ == "__main__":
    unittest.main()
