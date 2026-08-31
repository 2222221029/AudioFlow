import json
import sys
import unittest
from unittest import mock

import requests
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad

from core.qr_login import (
    QRSession,
    _DRIVERS,
    _NETEASE_DEVICE_UA,
    _NETEASE_EAPI_KEY,
    _NETEASE_EAPI_MARKER,
    _drive_netease,
)


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
            mock.patch("core.qr_login._netease_device_id", return_value="A" * 52),
            mock.patch("core.qr_login._netease_now_ms", return_value=1700000000123),
            mock.patch("core.qr_login.time.sleep", return_value=None),
            mock.patch.dict(sys.modules, {"qrcode": qrcode_module}),
        ):
            _drive_netease(session)
        return session, http, qrcode_module

    @staticmethod
    def _decrypt_call(call):
        encrypted = bytes.fromhex(call[1]["data"]["params"])
        plaintext = unpad(
            AES.new(_NETEASE_EAPI_KEY, AES.MODE_ECB).decrypt(encrypted),
            AES.block_size,
        ).decode("utf-8")
        path, body, digest = plaintext.split(_NETEASE_EAPI_MARKER, 2)
        return path, json.loads(body), digest

    def test_netease_driver_generates_qr_and_saves_confirmed_cookie(self):
        session, http, qrcode_module = self._run([
            _response({"code": 200, "unikey": "qr-key"}, {"sDeviceId": "web-device"}),
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
        qrcode_module.make.assert_called_once_with(
            "https://music.163.com/login?codekey=qr-key"
            "&chainId=v1_web-device_web_login_1700000000123"
        )
        self.assertTrue(http.calls[0][0].endswith("/eapi/login/qrcode/unikey"))
        self.assertTrue(http.calls[-1][0].endswith("/eapi/login/qrcode/client/login"))

        key_path, key_body, _ = self._decrypt_call(http.calls[0])
        check_path, check_body, _ = self._decrypt_call(http.calls[-1])
        self.assertEqual(key_path, "/api/login/qrcode/unikey")
        self.assertEqual(check_path, "/api/login/qrcode/client/login")
        self.assertEqual(key_body["type"], 3)
        self.assertEqual(check_body["key"], "qr-key")
        self.assertEqual(check_body["type"], 3)
        self.assertFalse(key_body["e_r"])
        self.assertFalse(check_body["e_r"])
        self.assertEqual(key_body["header"]["deviceId"], "A" * 52)
        self.assertEqual(check_body["header"]["deviceId"], "A" * 52)
        self.assertEqual(http.calls[0][1]["headers"]["User-Agent"], _NETEASE_DEVICE_UA)
        self.assertIn("deviceId=" + "A" * 52, http.calls[0][1]["headers"]["Cookie"])

    def test_netease_driver_rejects_success_without_account_cookie(self):
        session, _, _ = self._run([
            _response({"code": 200, "unikey": "qr-key"}),
            _response({"code": 803, "message": "授权登录成功"}, {"NMTID": "device"}),
        ])

        self.assertEqual(session.status, "failed")
        self.assertIn("未返回账号 Cookie", session.message)

    def test_netease_driver_accepts_cookie_from_response_body(self):
        session, _, _ = self._run([
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
        session, _, _ = self._run([
            _response({"code": 200, "unikey": "qr-key"}),
            _response({"code": 800, "message": "二维码已过期"}),
        ])

        self.assertEqual(session.status, "expired")
        self.assertIn("已过期", session.message)

    def test_netease_driver_explains_risk_rejection_without_exposing_detail(self):
        session, _, _ = self._run([
            _response({"code": 200, "unikey": "qr-key"}),
            _response({"code": 8821, "message": "请切换其他登录方式或升级新版本再试"}),
        ])

        self.assertEqual(session.status, "failed")
        self.assertIn("网易云拒绝", session.message)
        self.assertIn("最新版网易云音乐 APP", session.message)
        self.assertNotIn("切换其他登录方式", session.message)

    def test_netease_is_registered_as_qr_platform(self):
        self.assertIs(_DRIVERS["netease"], _drive_netease)


if __name__ == "__main__":
    unittest.main()
