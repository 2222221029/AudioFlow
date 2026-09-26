"""喜马拉雅 PC 通道与三端通用凭证派生的回归测试。

覆盖两个新增能力：

1. **PC 通道**（``core/ximalaya_pc_sign.py`` + ``core/ximalaya_pc_source.py``）
   ``mobile/download/v2/track`` 取址 + 纯 Python ``xm-sign``，
   让高音质下载不再依赖 Frida / 真机抓包。

2. **三端通用凭证**（``core/ximalaya_universal_login.py``）
   扫码一次派生出网页 / 电脑版 / App 三套凭证。

全程离线：网络一律用假 session 打桩，符合 ``conftest.assert_no_network`` 的意图。
"""

import base64
import json
import os
import random
import tempfile
import unittest
from unittest import mock

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

from core import ximalaya_pc_sign as signs
from core import ximalaya_pc_source as pcsrc
from core import ximalaya_universal_login as universal
from core.ximalaya_credentials import (
    normalize_ximalaya_mobile_credentials,
    ximalaya_mobile_credential_status,
    ximalaya_mobile_cookie_identity,
)

TEST_TOKEN = "276860626&deadbeefcafe0123456789abcdef0123"
TEST_DEVICE = "73464970-290f-3b53-90e8-74c914f5e77a"
CDN_URL = (
    "https://audiopay.cos.tx.xmcdn.com/storages/abc/xyz-aacv2-128k.m4a"
    "?buy_key=1&sign=2&timestamp=3&token=4"
)


def encrypt_media_url(url):
    """按 PC 端取址响应的格式加密一个地址（AES-128-ECB + PKCS7 + base64url）。"""
    key = bytes.fromhex(pcsrc.PC_MEDIA_AES_KEY_HEX)
    cipher = AES.new(key, AES.MODE_ECB).encrypt(pad(url.encode("utf-8"), AES.block_size))
    return base64.urlsafe_b64encode(cipher).decode("ascii")


class _Resp:
    def __init__(self, payload, status=200, content_type="application/json"):
        self.status_code = status
        self.text = payload if isinstance(payload, str) else json.dumps(payload)
        self.headers = {"content-type": content_type}

    def iter_content(self, chunk_size=0):
        body = self.text.encode("utf-8")
        for index in range(0, len(body), max(1, chunk_size or len(body) or 1)):
            yield body[index:index + max(1, chunk_size or len(body) or 1)]

    def close(self):
        pass


class _FakeSession:
    """按顺序吐出预设响应，并记录每次请求。"""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self._responses:
            return self._responses.pop(0)
        return _Resp({})


class XimalayaPcSignTest(unittest.TestCase):
    def test_session_id_shape_and_self_consistency(self):
        session_id = signs.generate_session_id()
        self.assertEqual(len(session_id), 66)
        self.assertTrue(session_id.endswith(signs.SESSION_SUFFIX_2))

        info = signs.decode_session_id(session_id)
        self.assertEqual(len(info["h8"]), 8)
        self.assertEqual(len(info["r4"]), 4)
        self.assertEqual(len(info["t8"]), 8)
        self.assertIn(info["suffix"], ("201", "202"))

    def test_session_id_is_deterministic_for_a_fixed_seed(self):
        first = signs.generate_session_id(ts_ms=1790393354000, rng=random.Random(42))
        second = signs.generate_session_id(ts_ms=1790393354000, rng=random.Random(42))
        self.assertEqual(first, second)

        other = signs.generate_session_id(ts_ms=1790393354000, rng=random.Random(43))
        self.assertNotEqual(first, other)

    def test_decode_rejects_server_side_form(self):
        # 服务端下发的是 45 字符 _1 形态，用的是另一套算法，必须显式拒绝
        with self.assertRaises(signs.XmSignError):
            signs.decode_session_id("A" * 43 + "_1")

    def test_browser_id_and_sign_shape(self):
        browser_id = signs.random_browser_id()
        self.assertEqual(len(browser_id), 48)

        session_id = signs.generate_session_id()
        token = signs.build_xm_sign(session_id, browser_id)
        self.assertEqual(token, f"{browser_id}&&{session_id}")
        # 尾部换行会让线上 query 变成 sign=...%0A，服务端在权限校验前就 1001
        self.assertFalse(any(char.isspace() for char in token))

    def test_build_xm_sign_rejects_empty_session(self):
        with self.assertRaises(signs.XmSignError):
            signs.build_xm_sign("")

    def test_pc_base_info_sign_uses_per_platform_keys(self):
        win = signs.make_pc_base_info_sign(516265274, 1790393354000, "win")
        mac = signs.make_pc_base_info_sign(516265274, 1790393354000, "mac")
        self.assertNotEqual(win, mac)
        for value in (win, mac):
            self.assertNotIn("=", value)
            self.assertGreater(len(value), 20)

    def test_pc_default_key_matches_mobile_signing_key(self):
        # 源项目自检断言：PC default 一套与移动端签名常量是同一份密钥
        self.assertEqual(signs.PC_SIGN_KEYS["default"], "9e3B103bA2d2cb56e805B3cCeB2512E3")
        self.assertEqual(signs.PC_IV_PREFIXES["default"], "M%6)W5F6@Jj~")
        self.assertEqual(signs.PC_APP_KEY, "0zpnlXAG")

    def test_sign_cache_reuses_and_invalidates(self):
        path = os.path.join(tempfile.mkdtemp(), "xm_session.json")
        cache = signs.XmSignCache(path=path, ttl=3600)

        first = cache.get()
        self.assertEqual(first, cache.get())
        self.assertTrue(os.path.exists(path))

        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
        self.assertEqual(payload["source"], "pure-python")
        self.assertTrue(payload["sessionId"].endswith("_2"))

        cache.invalidate()
        self.assertFalse(os.path.exists(path))
        self.assertNotEqual(first, cache.get())

    def test_sign_cache_survives_process_restart(self):
        path = os.path.join(tempfile.mkdtemp(), "xm_session.json")
        first = signs.XmSignCache(path=path, ttl=3600).get()
        # 新实例从磁盘恢复，不必重新生成（减少风控暴露面）
        self.assertEqual(first, signs.XmSignCache(path=path, ttl=3600).get())


class XimalayaPcSourceTest(unittest.TestCase):
    def test_extract_login_token_accepts_aliases(self):
        self.assertEqual(pcsrc.extract_login_token("a=1; 1&_token=9988&abc; b=2"), "9988&abc")
        self.assertEqual(pcsrc.extract_login_token("_token=7&xy"), "7&xy")
        self.assertEqual(pcsrc.extract_login_token("nothing=1"), "")
        self.assertEqual(pcsrc.cookie_uid("1&_token=9988&abc"), "9988")

    def test_ensure_device_cookie_adds_and_preserves(self):
        cookie, device = pcsrc.ensure_pc_device_cookie(
            "1&_token=9988&abc", "win32", "4.0.15", TEST_DEVICE
        )
        self.assertEqual(device, f"win32&{TEST_DEVICE}&4.0.15")
        self.assertIn("1&_device=win32&", cookie)

        # 已有 1&_device 时必须原样保留，不能被覆盖
        again, device_again = pcsrc.ensure_pc_device_cookie(cookie, "win32", "4.0.15", "other")
        self.assertEqual(again, cookie)
        self.assertEqual(device_again, device)

    def test_pc_cookie_from_token_requires_token(self):
        cookie = pcsrc.pc_cookie_from_token(TEST_TOKEN, device_uuid=TEST_DEVICE)
        self.assertTrue(cookie.startswith(f"1&_token={TEST_TOKEN}"))
        self.assertIn(f"1&_device=win32&{TEST_DEVICE}&4.0.15", cookie)
        with self.assertRaises(pcsrc.PcSourceError):
            pcsrc.pc_cookie_from_token("")

    def test_decrypt_media_url(self):
        encrypted = encrypt_media_url(CDN_URL)
        self.assertEqual(pcsrc.decrypt_pc_media_url(encrypted), CDN_URL)
        # 已是明文时短路返回
        self.assertEqual(pcsrc.decrypt_pc_media_url(CDN_URL), CDN_URL)
        self.assertEqual(pcsrc.decrypt_pc_media_url(""), "")
        self.assertEqual(pcsrc.decrypt_pc_media_url("!!!not-base64!!!"), "")

    def test_cdn_url_validation(self):
        self.assertTrue(pcsrc.looks_like_cdn_url(CDN_URL))
        self.assertFalse(pcsrc.looks_like_cdn_url("https://evil.example.com/a.mp3"))
        self.assertFalse(pcsrc.looks_like_cdn_url("xmcdn.com/storages/a.m4a"))

    def test_build_url_carries_device_and_level(self):
        source = pcsrc.PcTrackSource(session=_FakeSession([]), cookie=self._cookie())
        url = source.build_url("516265274", 2)
        self.assertIn("/mobile/download/v2/track/516265274/ts-", url)
        self.assertIn("device=win32", url)
        self.assertIn("trackQualityLevel=2", url)
        with self.assertRaises(pcsrc.PcSourceError):
            source.build_url("not-a-number", 2)
        with self.assertRaises(pcsrc.PcSourceError):
            source.build_url("516265274", 9)

    def _cookie(self):
        return pcsrc.pc_cookie_from_token(TEST_TOKEN, device_uuid=TEST_DEVICE)

    def _success_payload(self, level=2, size=12345):
        return {
            "ret": 0,
            "data": {
                "downloadAacUrl": encrypt_media_url(CDN_URL),
                "downloadQualityLevel": level,
                "downloadAacSize": size,
                "downloadType": "M4A",
            },
        }

    def test_resolve_success_reports_server_side_level(self):
        session = _FakeSession([_Resp(self._success_payload(level=2))])
        source = pcsrc.PcTrackSource(session=session, cookie=self._cookie())
        result = source.resolve("516265274", 3)

        self.assertTrue(result.ok)
        self.assertEqual(result.url, CDN_URL)
        # 请求档位 3，但标签必须按服务端回传的 2
        self.assertEqual(result.level, 2)
        self.assertEqual(result.tier, "PC 128K")
        self.assertEqual(result.file_size, 12345)
        self.assertTrue(source.headers()["Cookie"])

    def test_resolve_retries_once_with_a_fresh_sign(self):
        session = _FakeSession([_Resp({"ret": -1, "msg": "busy"}), _Resp(self._success_payload())])
        source = pcsrc.PcTrackSource(session=session, cookie=self._cookie())

        result = source.resolve("516265274", 2)
        self.assertTrue(result.ok)
        self.assertEqual(len(session.calls), 2)

        first_sign = session.calls[0][1]["headers"]["xm-sign"]
        second_sign = session.calls[1][1]["headers"]["xm-sign"]
        self.assertNotEqual(first_sign, second_sign)

    def test_resolve_retries_when_signature_rejected_with_html(self):
        session = _FakeSession([
            _Resp("<html>denied</html>", 400, "text/html"),
            _Resp(self._success_payload()),
        ])
        source = pcsrc.PcTrackSource(session=session, cookie=self._cookie())
        self.assertTrue(source.resolve("516265274", 2).ok)

    def test_resolve_reports_signature_failure_after_retry(self):
        session = _FakeSession([
            _Resp("<html>denied</html>", 400, "text/html"),
            _Resp("<html>denied</html>", 400, "text/html"),
        ])
        source = pcsrc.PcTrackSource(session=session, cookie=self._cookie())
        result = source.resolve("516265274", 2)
        self.assertFalse(result.ok)
        self.assertIn("签名", result.message)

    def test_resolve_error_branches(self):
        cases = [
            ({"ret": pcsrc.PC_RET_NOT_LOGGED_IN}, "1&_token"),
            ({"ret": pcsrc.PC_RET_MISSING_DEVICE}, "1&_device"),
            ({"ret": pcsrc.PC_RET_RISK_CONTROL}, "风控"),
        ]
        for payload, keyword in cases:
            session = _FakeSession([_Resp(payload), _Resp(payload)])
            source = pcsrc.PcTrackSource(session=session, cookie=self._cookie())
            result = source.resolve("516265274", 2)
            self.assertFalse(result.ok, payload)
            self.assertIn(keyword, result.message)

    def test_resolve_without_login_skips_network(self):
        session = _FakeSession([])
        source = pcsrc.PcTrackSource(
            session=session, cookie="1&_device=win32&u&4.0.15"
        )
        result = source.resolve("516265274", 2)
        self.assertFalse(result.ok)
        self.assertEqual(session.calls, [])

    def test_resolve_rejects_non_media_response(self):
        session = _FakeSession([_Resp({"ret": 0, "data": {"downloadAacUrl": "garbage!!"}})])
        source = pcsrc.PcTrackSource(session=session, cookie=self._cookie())
        result = source.resolve("516265274", 2)
        self.assertFalse(result.ok)
        self.assertIn("解密失败", result.message)

    def test_probe_is_offline(self):
        source = pcsrc.PcTrackSource(session=_FakeSession([]), cookie=self._cookie())
        info = source.probe()
        self.assertTrue(info["has_login"])
        self.assertTrue(info["cookie_has_device"])
        self.assertEqual(info["sign_platform"], "win")


class XimalayaUniversalLoginTest(unittest.TestCase):
    def test_split_token_forms(self):
        self.assertEqual(universal.split_token(TEST_TOKEN)[0], "276860626")
        self.assertEqual(universal.split_token(f"1&_token={TEST_TOKEN}")[1], TEST_TOKEN)
        self.assertEqual(
            universal.split_token(f"a=1; 1&_token={TEST_TOKEN}; b=2")[1], TEST_TOKEN
        )
        for bad in ("", "abc", "notauid&hex"):
            with self.assertRaises(universal.UniversalLoginError):
                universal.split_token(bad)

    def test_device_uuid_is_persisted_and_reused(self):
        path = os.path.join(tempfile.mkdtemp(), "dev.uuid")
        first = universal.load_or_create_device_uuid(path)
        self.assertEqual(len(first.replace("-", "")), 32)
        # 同一路径必须复用，换设备等同于"每次换一台新机器"，会无谓扩大风控面
        self.assertEqual(first, universal.load_or_create_device_uuid(path))

    def test_derive_produces_three_end_cookies(self):
        device = "73464970-290f-3b53-90e8-74c914f5e77a"
        bundle = universal.derive_universal_credentials(TEST_TOKEN, device_uuid=device)

        self.assertEqual(bundle["uid"], "276860626")
        self.assertEqual(bundle["web_cookie"], f"1&_token={TEST_TOKEN}")
        self.assertIn(f"1&_device=win32&{device}&4.0.15", bundle["pc_cookie"])
        self.assertIn(f"1&_device=android&{device}&9.4.52", bundle["mobile_cookie"])
        self.assertEqual(bundle["mobile_ua"], "ting_9.4.52(V2059A,Android33)")
        # 三端共用同一个设备号，才能保证派生自洽
        self.assertEqual(bundle["device_uuid"], device)

    def test_derived_mobile_credentials_are_accepted_by_the_app(self):
        bundle = universal.derive_universal_credentials(TEST_TOKEN, device_uuid=TEST_DEVICE)
        credential = bundle["mobile_credentials"]

        self.assertTrue(credential["x_tk"].startswith("TAC"))
        normalized = normalize_ximalaya_mobile_credentials(credential)
        self.assertTrue(normalized["x_tk"])
        self.assertTrue(normalized["cookie"])

        status = ximalaya_mobile_credential_status(normalized)
        self.assertTrue(status["complete"], status)

        identity = ximalaya_mobile_cookie_identity(normalized)
        self.assertEqual(identity["uid"], "276860626")
        self.assertEqual(identity["platform"], "android")
        self.assertEqual(identity["device_id"], TEST_DEVICE.replace("-", ""))

    def test_derive_can_skip_ticket_issuing(self):
        bundle = universal.derive_universal_credentials(
            TEST_TOKEN, device_uuid=TEST_DEVICE, issue_ticket=False
        )
        self.assertEqual(bundle["ticket"], "")
        self.assertNotIn("x_tk", bundle["mobile_credentials"])
        self.assertIn("1&_device=android", bundle["mobile_cookie"])

    def test_derive_rejects_anonymous_token(self):
        with self.assertRaises(universal.UniversalLoginError):
            universal.derive_universal_credentials("0&abc", device_uuid=TEST_DEVICE)

    def test_bundle_summary_does_not_leak_secrets(self):
        bundle = universal.derive_universal_credentials(TEST_TOKEN, device_uuid=TEST_DEVICE)
        summary = universal.bundle_summary(bundle)
        serialized = json.dumps(summary, ensure_ascii=False)

        self.assertNotIn(bundle["token"], serialized)
        self.assertNotIn(bundle["ticket"], serialized)
        self.assertTrue(summary["ticket_ready"])
        self.assertEqual(summary["ticket_prefix"], "TAC")
        self.assertTrue(summary["pc_device_present"])

    def test_save_bundle_backs_up_existing_files(self):
        outdir = tempfile.mkdtemp()
        existing = os.path.join(outdir, "pc_cookie.txt")
        with open(existing, "w", encoding="utf-8") as handle:
            handle.write("captured-from-real-device")

        bundle = universal.derive_universal_credentials(TEST_TOKEN, device_uuid=TEST_DEVICE)
        paths = universal.save_bundle(bundle, outdir=outdir)

        # 真机抓包凭证必须先备份，不能被静默覆盖
        self.assertIn("pc_cookie.txt.bak", paths.get("_backed_up", ""))
        with open(existing + ".bak", encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "captured-from-real-device")
        self.assertTrue(os.path.exists(paths["mobile_headers"]))


class XimalayaPcIntegrationTest(unittest.TestCase):
    """PC 通道与三端派生在下载管理器上的接入点。"""

    def _manager(self, cookie):
        from core.ximalaya_download_manager import XimalayaDownloadManager
        return XimalayaDownloadManager(cookie_string=cookie)

    def test_pc_channel_available_with_web_login_cookie(self):
        manager = self._manager(f"1&_token={TEST_TOKEN}")
        self.assertTrue(manager._has_pc_credentials())
        # 设备号与签名都应能在本地补齐，不需要用户提供
        source = manager._pc_source()
        self.assertIn("1&_device=win32&", source.cookie)
        self.assertTrue(source.headers()["xm-sign"])

    def test_pc_channel_unavailable_without_token(self):
        manager = self._manager("wfp=abc; xm-page-viewid=ximalaya-web")
        self.assertFalse(manager._has_pc_credentials())
        self.assertIsNone(manager._pc_source())

    def test_pc_channel_rejects_anonymous_token(self):
        manager = self._manager("1&_token=0&abc")
        self.assertFalse(manager._has_pc_credentials())

    def test_derived_pc_cookie_drives_the_channel(self):
        bundle = universal.derive_universal_credentials(TEST_TOKEN, device_uuid=TEST_DEVICE)
        manager = self._manager(bundle["pc_cookie"])
        self.assertTrue(manager._has_pc_credentials())
        self.assertEqual(manager._pc_source().uid, "276860626")

    def test_quality_labels_cover_the_pc_ladder(self):
        from core.ximalaya_download_manager import (
            PC_QUALITY_CHAIN, PC_QUALITY_LABELS, XimalayaDownloadManager,
        )
        self.assertEqual(PC_QUALITY_CHAIN, (3, 2, 1, 0))
        self.assertEqual(PC_QUALITY_LABELS["PC 256K"], 3)
        self.assertEqual(XimalayaDownloadManager._pc_quality_label(2), "PC 128K")

    def test_pc_download_reports_restricted_without_login(self):
        manager = self._manager("")
        result = manager._download_pc_track("516265274", 3, "/tmp/never-written.m4a")
        self.assertFalse(result)
        self.assertEqual(manager.last_error_type, "restricted")

    def test_legacy_pc_url_helper_no_longer_calls_dead_endpoint(self):
        manager = self._manager("")
        urls = manager._get_pc_audio_urls("516265274")
        self.assertEqual(urls, {})


class QrLoginDerivationTest(unittest.TestCase):
    """扫码成功后应自动派生并保存 App 凭证。"""

    def test_persists_mobile_credential_from_scan_result(self):
        from core import qr_login

        saved = {}

        class _Manager:
            def set_cookie(self, platform, cookie):
                saved[platform] = cookie

        with mock.patch.object(qr_login, "_active_cookie_manager", return_value=_Manager()):
            summary = qr_login._persist_universal_ximalaya_credentials(
                {"1&_token": TEST_TOKEN, "wfp": "x"}
            )

        self.assertTrue(summary["ok"], summary)
        self.assertTrue(summary["saved"])
        self.assertIn("xmly_mobile", saved)
        self.assertTrue(saved["xmly_mobile"]["x_tk"].startswith("TAC"))

    def test_returns_empty_without_login_token(self):
        from core import qr_login
        self.assertEqual(qr_login._persist_universal_ximalaya_credentials({"wfp": "x"}), {})

    def test_never_raises_on_bad_input(self):
        from core import qr_login
        # 派生失败不能影响扫码登录本身
        summary = qr_login._persist_universal_ximalaya_credentials({"1&_token": "broken"})
        self.assertFalse(summary.get("ok", False))
        self.assertTrue(summary.get("error"))


class _BinaryResp:
    """二进制安全的响应桩（媒体流不能被当作文本往返转换）。"""

    def __init__(self, body, declared_size=None, status=200):
        self.status_code = status
        self.body = body
        self.text = ""
        self.headers = {
            "content-type": "audio/mp4",
            "content-length": str(len(body) if declared_size is None else declared_size),
        }

    def iter_content(self, chunk_size=0):
        size = max(1, chunk_size or len(self.body) or 1)
        for index in range(0, len(self.body), size):
            yield self.body[index:index + size]

    def close(self):
        pass


class _MediaSession:
    """取址请求返回 JSON，媒体请求返回文件流。"""

    def __init__(self, media, declared_size=None, encrypted_url=None):
        self.media = media
        self.declared_size = declared_size
        self.encrypted_url = encrypted_url or encrypt_media_url(CDN_URL)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        if "download/v2/track" in url:
            return _Resp({
                "ret": 0,
                "data": {
                    "downloadAacUrl": self.encrypted_url,
                    "downloadQualityLevel": 2,
                    "downloadAacSize": len(self.media),
                    "downloadType": "M4A",
                },
            })
        return _BinaryResp(self.media, self.declared_size)


class XimalayaPcEndToEndTest(unittest.TestCase):
    """派生 → 取址 → 落盘 的完整链路（全程打桩，不发真实请求）。"""

    MEDIA = b"\x00\x00\x00\x18ftypM4A " + b"AUDIODATA" * 2000

    def test_derivation_to_file(self):
        from core.ximalaya_download_manager import XimalayaDownloadManager

        bundle = universal.derive_universal_credentials(TEST_TOKEN, device_uuid=TEST_DEVICE)
        manager = XimalayaDownloadManager(cookie_string=bundle["pc_cookie"])
        manager.session = _MediaSession(self.MEDIA)

        outdir = tempfile.mkdtemp()
        target = os.path.join(outdir, "chapter_001.m4a")
        self.assertTrue(manager._download_pc_track("516265274", 2, target, "第一集"))

        self.assertTrue(os.path.exists(target))
        self.assertEqual(os.path.getsize(target), len(self.MEDIA))
        self.assertEqual(manager.last_download_source, "pc_download_v2_level_2")
        self.assertEqual(manager.last_download_quality_label, "PC 128K")
        self.assertEqual(manager.last_error, "")
        self.assertEqual(len(manager.session.calls), 2)

    def test_truncated_download_is_rejected_and_not_saved(self):
        from core.ximalaya_download_manager import XimalayaDownloadManager

        bundle = universal.derive_universal_credentials(TEST_TOKEN, device_uuid=TEST_DEVICE)
        manager = XimalayaDownloadManager(cookie_string=bundle["pc_cookie"])
        # 声明 18016 字节但只给 5000：CDN 静默截断是真实发生过的故障模式
        manager.session = _MediaSession(self.MEDIA[:5000], declared_size=len(self.MEDIA))

        target = os.path.join(tempfile.mkdtemp(), "truncated.m4a")
        self.assertFalse(manager._download_pc_track("516265274", 2, target))
        self.assertIn("不完整", manager.last_error)
        self.assertFalse(os.path.exists(target))
        self.assertFalse(os.path.exists(target + ".part"))


class XimalayaWebV3LosslessTest(unittest.TestCase):
    """网页版 v3/baseInfo 的无损（FHQ）选择。

    回归重点：旧实现按“是不是 M4A/AAC”打分，FHQ 被判成低优先级，
    于是服务端明明返回了 24bit WAV 母带，最终仍然选中体积只有它 1/20 的
    M4A_128。
    """

    WAV_URL = "https://audiopay.cos.tx.xmcdn.com/storages/hp/01/abc.wav?sign=1"
    M128_URL = "https://audiopay.cos.tx.xmcdn.com/storages/hp/02/def-aacv2-128k.m4a?sign=2"
    M24_URL = "https://audiopay.cos.tx.xmcdn.com/storages/hp/03/ghi.m4a?sign=3"
    MP3_URL = "https://audiopay.cos.tx.xmcdn.com/storages/hp/04/jkl-32k.mp3?sign=4"

    @staticmethod
    def _web_encrypt(url):
        key = bytes.fromhex("aaad3e4fd540b0f79dca95606e72bf93")
        cipher = AES.new(key, AES.MODE_ECB).encrypt(pad(url.encode("utf-8"), AES.block_size))
        return base64.urlsafe_b64encode(cipher).decode("ascii")

    def _entry(self, type_name, level, size, url):
        return {"type": type_name, "qualityLevel": level, "fileSize": size,
                "url": self._web_encrypt(url)}

    def _select(self, entries):
        from core.ximalaya_download_manager import XimalayaDownloadManager
        return XimalayaDownloadManager._select_web_play_candidate({"playUrlList": entries})

    def test_lossless_wins_over_m4a(self):
        candidate = self._select([
            self._entry("M4A_128", 2, 1906367, self.M128_URL),
            self._entry("FHQ", 3, 41673076, self.WAV_URL),
            self._entry("M4A_24", 0, 488599, self.M24_URL),
        ])
        self.assertIsNotNone(candidate)
        url, size, label = candidate
        self.assertEqual(url, self.WAV_URL)
        self.assertEqual(label, "FHQ")
        self.assertEqual(size, 41673076)

    def test_falls_back_to_m4a_when_no_lossless(self):
        url, size, label = self._select([
            self._entry("M4A_24", 0, 488599, self.M24_URL),
            self._entry("M4A_128", 2, 1906367, self.M128_URL),
        ])
        self.assertEqual(url, self.M128_URL)
        self.assertEqual(label, "M4A_128")

    def test_mp3_only_catalogue_still_works(self):
        url, _size, label = self._select([
            self._entry("MP3_32", 0, 630326, self.MP3_URL),
        ])
        self.assertEqual(url, self.MP3_URL)
        self.assertEqual(label, "MP3_32")

    def test_container_rank_order(self):
        from core.ximalaya_download_manager import XimalayaDownloadManager as manager
        lossless = manager._web_container_rank("FHQ")
        self.assertEqual(lossless, manager._web_container_rank("FLAC"))
        self.assertGreater(lossless, manager._web_container_rank("M4A_128"))
        self.assertGreater(manager._web_container_rank("M4A_128"), manager._web_container_rank("MP3_64"))
        self.assertGreater(manager._web_container_rank("MP3_64"), manager._web_container_rank("unknown"))

    def test_web_v3_requests_high_quality_level(self):
        # 服务端只在 trackQualityLevel >= 2 时才把 M4A_128 / FHQ 放进列表
        from core.ximalaya_download_manager import WEB_V3_QUALITY_LEVEL
        self.assertGreaterEqual(WEB_V3_QUALITY_LEVEL, 2)

    def test_container_format_labels_fhq_as_wav(self):
        from core.ximalaya_manager import XimalayaManager
        self.assertEqual(XimalayaManager._web_container_format("FHQ"), "WAV")
        self.assertEqual(XimalayaManager._web_container_format("FLAC"), "FLAC")
        self.assertEqual(XimalayaManager._web_container_format("M4A_128"), "M4A")
        self.assertEqual(XimalayaManager._web_container_format("MP3_64"), "MP3")


class XimalayaQualityRoutingTest(unittest.TestCase):
    """UI 档位 → 后端通道的路由。"""

    def test_pc_quality_labels_route_to_pc_channel(self):
        from core.ximalaya_download_manager import XimalayaDownloadManager

        bundle = universal.derive_universal_credentials(TEST_TOKEN, device_uuid=TEST_DEVICE)
        manager = XimalayaDownloadManager(cookie_string=bundle["pc_cookie"])
        manager.session = _MediaSession(XimalayaPcEndToEndTest.MEDIA)

        target = os.path.join(tempfile.mkdtemp(), "pc_route.m4a")
        self.assertTrue(manager.download_audio_by_quality(
            "516265274", "PC 256K", target, "专辑", "第一集"))
        self.assertTrue(manager.last_download_source.startswith("pc_download_v2_level_"))
        self.assertEqual(manager.last_download_quality_label, "PC 128K")

    def test_pc_auto_quality_uses_the_fallback_chain(self):
        from core.ximalaya_download_manager import XimalayaDownloadManager

        bundle = universal.derive_universal_credentials(TEST_TOKEN, device_uuid=TEST_DEVICE)
        manager = XimalayaDownloadManager(cookie_string=bundle["pc_cookie"])
        manager.session = _MediaSession(XimalayaPcEndToEndTest.MEDIA)

        target = os.path.join(tempfile.mkdtemp(), "pc_auto.m4a")
        self.assertTrue(manager.download_audio_by_quality(
            "516265274", XimalayaDownloadManager.PC_AUTO_QUALITY, target, "专辑", "第一集"))

    def test_subscription_whitelist_accepts_pc_qualities(self):
        try:
            from src.server import web_server
        except Exception as exc:      # pragma: no cover - 环境不完整时跳过
            self.skipTest(f"web_server 不可导入: {exc}")

        self.assertIn(web_server.XMLY_PC_INTERFACE, web_server.XMLY_SUBSCRIPTION_QUALITIES)
        for quality in ("PC 256K", "PC 128K", "PC 64K", "PC 24K"):
            self.assertIn(quality, web_server.XMLY_SUBSCRIPTION_QUALITIES)


class XimalayaWebLosslessQualityTest(unittest.TestCase):
    """「网页无损优先」显式档位。

    网页自动模式（WEB_AUTO_QUALITY）为保护易风控的 Web V3，只在章节确认受限时
    才切到 baseInfo；这个档位是用户主动选择，因此直接走 baseInfo —— 那是唯一
    会出现 FHQ（24bit WAV 母带）的响应。
    """

    QUALITY = "网页无损优先（FHQ WAV）"

    def test_constant_matches_frontend_label(self):
        from core.ximalaya_download_manager import XimalayaDownloadManager
        self.assertEqual(XimalayaDownloadManager.WEB_LOSSLESS_QUALITY, self.QUALITY)

    def test_requires_web_login_and_does_not_send_request(self):
        from core.ximalaya_download_manager import XimalayaDownloadManager

        manager = XimalayaDownloadManager(cookie_string="")
        manager.session = _FakeSession([])
        target = os.path.join(tempfile.mkdtemp(), "nope.m4a")

        self.assertFalse(manager.download_audio_by_quality("516265274", self.QUALITY, target))
        self.assertEqual(manager.last_error_type, "restricted")
        # baseInfo 匿名必被风控拒（ret=1001），应当在发请求之前就挡下
        self.assertEqual(manager.session.calls, [])
        self.assertFalse(os.path.exists(target))

    def test_not_routed_to_mobile_v4(self):
        from core.download_worker import DownloadWorker
        # 名字里带「无损」，但它是网页档位，绝不能被路由到移动端 V4 控制路径
        self.assertFalse(DownloadWorker._is_ximalaya_mobile_v4_quality(self.QUALITY))
        self.assertFalse(DownloadWorker._is_ximalaya_mobile_premium_quality(self.QUALITY))
        # 同时仍按无损对待（文件名要打 [无损] 标记）
        self.assertTrue(DownloadWorker._is_ximalaya_lossless_quality(self.QUALITY))
        self.assertTrue(DownloadWorker._ximalaya_skip_url_fallback(self.QUALITY))

    def test_lossless_marker_only_for_real_web_lossless(self):
        from core.download_worker import DownloadWorker
        self.assertEqual(
            DownloadWorker._ximalaya_actual_quality_marker("web_v3_lossless"), "[无损]"
        )
        # 普通网页 M4A 不该被标成无损
        self.assertEqual(DownloadWorker._ximalaya_actual_quality_marker("web_v3"), "")

    def test_subscription_whitelist_accepts_web_lossless(self):
        try:
            from src.server import web_server
        except Exception as exc:      # pragma: no cover
            self.skipTest(f"web_server 不可导入: {exc}")
        self.assertIn(self.QUALITY, web_server.XMLY_SUBSCRIPTION_QUALITIES)


class XimalayaWebLosslessThrottleTest(unittest.TestCase):
    """网页无损档位的取址节流。

    baseInfo 在 trackQualityLevel>=2 时风控比普通网页档更紧：整张专辑并发下载
    会大量返回 ret=1001「系统繁忙，请稍后再试」。所以这条通道要串行化取址。
    """

    def test_conservative_flag_goes_through_the_gate(self):
        from core.ximalaya_download_manager import XimalayaDownloadManager

        manager = XimalayaDownloadManager(cookie_string="1&_token=1&x")
        calls = []

        def fake_gate(cls):
            calls.append(1)

        with (
            mock.patch.object(XimalayaDownloadManager, "_wait_web_lossless_slot",
                              classmethod(fake_gate)),
            mock.patch.object(manager, "_request_web_track_info", return_value=({}, {})),
        ):
            manager._download_web_authorized("516265274", "/tmp/x.m4a", conservative=True)
            self.assertEqual(len(calls), 1, "保守模式应当在取址前过闸门")

            manager._download_web_authorized("516265274", "/tmp/x.m4a")
            self.assertEqual(len(calls), 1, "普通网页档不应走这道闸门")

    def test_gate_serializes_and_spaces_requests(self):
        from core.ximalaya_download_manager import XimalayaDownloadManager as manager

        manager._WEB_LOSSLESS_LAST_AT = 0.0
        with (
            mock.patch("core.ximalaya_download_manager.time.monotonic", return_value=1000.0),
            mock.patch("core.ximalaya_download_manager.time.sleep") as sleep,
        ):
            manager._wait_web_lossless_slot()   # 首个请求：距上次已超间隔，不等待
            self.assertEqual(sleep.call_count, 0)
            manager._wait_web_lossless_slot()   # 紧接着的第二个：必须等待
            self.assertEqual(sleep.call_count, 1)
            self.assertGreaterEqual(sleep.call_args.args[0], manager._WEB_LOSSLESS_MIN_INTERVAL)

    def test_web_lossless_quality_uses_conservative_route(self):
        """档位分支必须把 conservative=True 传下去。"""
        from core.ximalaya_download_manager import XimalayaDownloadManager

        manager = XimalayaDownloadManager(cookie_string="1&_token=1&x")
        captured = {}

        def fake_web(track_id, save_path, chapter_title="", progress_callback=None,
                     conservative=False):
            captured["conservative"] = conservative
            return True

        with mock.patch.object(manager, "_download_web_authorized", side_effect=fake_web):
            ok = manager.download_audio_by_quality(
                "516265274",
                XimalayaDownloadManager.WEB_LOSSLESS_QUALITY,
                os.path.join(tempfile.mkdtemp(), "x.wav"),
            )

        self.assertTrue(ok)
        self.assertIs(captured.get("conservative"), True)


if __name__ == "__main__":
    unittest.main()
