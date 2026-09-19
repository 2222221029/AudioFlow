"""懒人听书签名与节流的回归测试。

## 背景（来自参考实现的逆向实测）

`接口/懒人听书/逆向分析报告.md` §8.2「与 AudioFlow 实现的差异（重要修正）」给出的实测记录：

    sc 仅算空参数       → status=483 请求验证失败
    sc 不含 sc 参数     → status=483 请求验证失败
    sc 含 meta+公共参数 → status=0  ✅ 返回 token（49 字符）

即 `tempToken` / `AutoRegister` 的 `sc` 必须把**业务参数（meta）与公共参数
（imei/nwt/q/mode）一并**按 key 升序参与 MD5，且 `sc` 自身不参与计算。

修复前本文件的签名用例会失败：`fetch_temp_token` 用 `calc_sc(path, {})`，
只对空参数签名，请求里也不带公共参数。由于 `tests/test_lrts_login.py` 把
`fetch_temp_token` 整体 mock 掉了，这个缺陷此前无法被测试发现。

## 节流

`_throttle_audio_request()` 修复前只在每章入口调用一次（`_throttle_audio_request`
的唯一调用点），而降档探测循环内部会连续发出 3~4 次 `getListenPath` 请求，
直接对应服务端 `status=114 下载过于频繁` 风控。正确做法是让**每个 HTTP 请求**
都过闸（参考实现把闸门放在 `call()` 内）。
"""

from __future__ import annotations

import json
import unittest
from unittest import mock

from core import lrts_manager as lm
from core.lrts_manager import LrtsAppClient, calc_sc

TEMP_TOKEN_PATH = "/yyting/usercenter/tempToken.action"
AUTO_REGISTER_PATH = "/yyting/usercenter/AutoRegister.action"
V3_LISTEN_PATH = lm.V3_LISTEN_PATH

# 参与签名的公共参数（缺一即 483）
COMMON_FIELDS = ("imei", "nwt", "q", "mode")


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


class CapturingSession:
    """记录每一次请求的 (url, params)，并按序返回预定响应。"""

    def __init__(self, payloads=None):
        self.headers = {}
        self.calls = []
        self._payloads = list(payloads or [])

    def _next(self):
        return FakeResponse(self._payloads.pop(0) if self._payloads else {})

    def get(self, url, params=None, timeout=None):
        self.calls.append(("GET", url, dict(params or {})))
        return self._next()

    def post(self, url, data=None, timeout=None):
        self.calls.append(("POST", url, dict(data or {})))
        return self._next()


def _client_with(session):
    client = LrtsAppClient(imei="test-imei-0001")
    client.session = session
    return client


def _signed_payload(params: dict) -> dict:
    """按协议还原参与签名的参数集合（剔除 sc 自身）。"""
    return {key: value for key, value in params.items() if key != "sc"}


class LrtsTempTokenSignatureTest(unittest.TestCase):
    def test_temp_token_signature_covers_meta_and_common_params(self):
        session = CapturingSession([{"status": 0, "token": "T" * 49}])
        client = _client_with(session)

        with (
            mock.patch.object(lm, "build_device_info", return_value={"imei": "x"}),
            mock.patch.object(lm, "rsa_encrypt_meta", return_value="META-BLOB"),
        ):
            token = client.fetch_temp_token()

        self.assertEqual(token, "T" * 49)

        method, url, params = session.calls[0]
        self.assertEqual(method, "GET")
        self.assertTrue(url.endswith(TEMP_TOKEN_PATH), url)
        self.assertEqual(params["meta"], "META-BLOB")
        for field in COMMON_FIELDS:
            self.assertIn(field, params, f"{field} 必须随请求发出并参与签名")
        self.assertEqual(params["imei"], "test-imei-0001")

        self.assertEqual(
            params["sc"],
            calc_sc(TEMP_TOKEN_PATH, _signed_payload(params)),
            "sc 必须由 meta + 公共参数共同计算（且不含 sc 自身）",
        )

    def test_temp_token_signature_is_not_computed_over_empty_params(self):
        """锁定回归：sc 不能等于"只对空参数"计算的结果。"""
        session = CapturingSession([{"status": 0, "token": "T" * 49}])
        client = _client_with(session)

        with (
            mock.patch.object(lm, "build_device_info", return_value={"imei": "x"}),
            mock.patch.object(lm, "rsa_encrypt_meta", return_value="META-BLOB"),
        ):
            client.fetch_temp_token()

        _, _, params = session.calls[0]
        self.assertNotEqual(
            params["sc"],
            calc_sc(TEMP_TOKEN_PATH, {}),
            "sc 只对空参数签名会返回 status=483",
        )

    def test_auto_register_fallback_signs_meta_and_common_params(self):
        session = CapturingSession([
            {"status": 483, "msg": "请求验证失败"},
            {"status": 0, "token": "A" * 49},
        ])
        client = _client_with(session)

        with (
            mock.patch.object(lm, "build_device_info", return_value={"imei": "x"}),
            mock.patch.object(lm, "rsa_encrypt_meta", return_value="META-BLOB"),
        ):
            token = client.fetch_temp_token()

        self.assertEqual(token, "A" * 49)
        self.assertEqual(len(session.calls), 2)

        _, url, params = session.calls[1]
        self.assertTrue(url.endswith(AUTO_REGISTER_PATH), url)
        self.assertEqual(params["meta"], "META-BLOB")
        for field in COMMON_FIELDS:
            self.assertIn(field, params, f"{field} 必须随请求发出并参与签名")
        self.assertEqual(
            params["sc"],
            calc_sc(AUTO_REGISTER_PATH, _signed_payload(params)),
        )


class LrtsRequestThrottleTest(unittest.TestCase):
    def test_every_client_request_passes_through_the_global_throttle(self):
        session = CapturingSession([{"status": 0}])
        client = _client_with(session)

        with mock.patch.object(lm, "_throttle_audio_request") as throttle:
            client.get(lm.READ_HOST, "/yyting/probe.action", {"a": "b"})

        throttle.assert_called_once_with()

    def test_quality_probe_loop_throttles_each_request(self):
        """降档探测循环会连发多次 getListenPath，每次都必须过闸。"""
        session = CapturingSession([
            {"status": 0, "data": {"path": "", "quality": 0}},
            {"status": 0, "data": {"path": "", "quality": 0}},
            {"status": 0, "data": {"path": "/audio/x.m4a", "quality": 1}},
        ])
        client = _client_with(session)
        client.token = "token-1"

        with mock.patch.object(lm, "_throttle_audio_request") as throttle:
            client.get_play_path(
                entity_type=lm.ALBUM_ENTITY_TYPE,
                entity_id=23353737,
                chapter_id=99001,
                section=1,
                op_type=1,
                track_id=555,
                quality=3,
            )

        listen_calls = [c for c in session.calls if c[1].endswith(V3_LISTEN_PATH)]
        self.assertGreaterEqual(len(listen_calls), 2, "应发生多次降档探测请求")
        self.assertEqual(
            throttle.call_count,
            len(session.calls),
            "每个 HTTP 请求都应过闸（修复前只在每章入口过一次）",
        )


if __name__ == "__main__":
    unittest.main()
