import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlparse

from core.ximalaya_download_manager import XimalayaDownloadManager


class FakeResponse:
    def __init__(self, status_code=200, headers=None, body=b"", json_data=None):
        self.status_code = status_code
        self.headers = headers or {}
        self._body = body
        self._json_data = json_data

    def iter_content(self, chunk_size=1):
        if self._body:
            yield self._body

    def json(self):
        return self._json_data


class XimalayaDownloadManagerTest(unittest.TestCase):
    @staticmethod
    def _mobile_credentials(ticket="member-mobile-ticket"):
        return {
            "x_tk": ticket,
            "cookie": "1&*token=123456&mobile-session",
            "user_agent": "ting_9.4.74.3(com.ximalaya.ting.android,Android)",
            "device": "android",
        }

    @staticmethod
    def _spatial_info(level, name, url, size):
        return FakeResponse(json_data={
            "ret": 0,
            "trackBaseVO": {
                "trackInfo": {
                    "playUrlInfos": [{
                        "qualityLevel": level,
                        "qualityName": name,
                        "hasAuthorized": True,
                        "decodeUrl": url,
                        "fileSize": size,
                    }],
                },
            },
        })

    def test_mobile_quality_labels_map_to_exact_levels(self):
        manager = XimalayaDownloadManager()
        self.assertEqual(manager._mobile_quality_profile("无损真人录制")["key"], "lossless")
        self.assertEqual(manager._mobile_quality_profile("杜比全景声")["key"], "dolby_atmos")
        self.assertEqual(manager._mobile_quality_profile("DOLBY_ATMOS")["key"], "dolby_atmos")
        self.assertEqual(manager._mobile_quality_profile("Audio Vivid 菁彩声")["key"], "audio_vivid")
        self.assertIsNone(manager._mobile_quality_profile("M4A 96K"))

    def test_android2_sign_matches_captured_official_request(self):
        sign = XimalayaDownloadManager._build_mobile_v4_sign(
            "559285269", 1786632464075, device="android2"
        )

        self.assertEqual(sign, "oH66rHnhH3qwK7bgy9j-I8qv5cFahEEZvMTEuYVeUlI=")
        self.assertFalse(any(char.isspace() for char in sign))

    def test_android2_request_url_exactly_matches_captured_official_request(self):
        url = XimalayaDownloadManager._mobile_v4_request_url(
            "559285269", 1786632464075, "android2", 1
        )

        self.assertEqual(
            url,
            "https://mobile.ximalaya.com/mobile-playpage/track/v4/baseInfo/1786632464075"
            "?device=android2&sign=oH66rHnhH3qwK7bgy9j-I8qv5cFahEEZvMTEuYVeUlI="
            "&trackId=559285269&trackQualityLevel=1",
        )
        self.assertNotIn("%0A", url.upper())

    def test_dolby_atmos_uses_level_twelve_and_validates_ec3_container(self):
        body = b"\x00\x00\x00\x18ftypM4A \x00\x00\x00\x00M4A isom" + b"ec-3" + (b"audio" * 1000)
        size = len(body)
        info = self._spatial_info(12, "杜比全景声", "https://audio.example/atmos.m4a", size)
        audio = FakeResponse(
            headers={"content-type": "audio/mp4", "content-length": str(size)},
            body=body,
        )

        with contextlib.redirect_stdout(io.StringIO()):
            manager = XimalayaDownloadManager(
                cookie_string="_token=member",
                mobile_credentials=self._mobile_credentials("mobile-ticket"),
            )
            with tempfile.TemporaryDirectory() as tmp:
                save_path = Path(tmp) / "track.m4a"
                with mock.patch.object(manager.session, "get", side_effect=[info, audio]) as get:
                    ok = manager.download_audio_by_quality("979576276", "杜比全景声", str(save_path))
                self.assertTrue(save_path.exists())
                self.assertFalse(Path(str(save_path) + ".part").exists())

        self.assertTrue(ok)
        query = parse_qs(urlparse(get.call_args_list[0].args[0]).query)
        self.assertEqual(query["trackQualityLevel"], ["12"])
        self.assertEqual(get.call_args_list[1].args[0], "https://audio.example/atmos.m4a")
        self.assertEqual(manager.last_download_source, "mobile_v4_level_12")
        self.assertEqual(manager.last_download_quality_label, "杜比全景声")

    def test_audio_vivid_uses_level_thirteen_and_validates_av3a_container(self):
        body = b"\x00\x00\x00\x18ftypM4A \x00\x00\x00\x00M4A isom" + b"av3a" + (b"audio" * 1000)
        size = len(body)
        info = self._spatial_info(13, "Audio Vivid 菁彩声", "https://audio.example/vivid.m4a", size)
        audio = FakeResponse(headers={"content-type": "audio/mp4"}, body=body)

        with contextlib.redirect_stdout(io.StringIO()):
            manager = XimalayaDownloadManager(mobile_credentials=self._mobile_credentials("mobile-ticket"))
            with tempfile.TemporaryDirectory() as tmp:
                save_path = Path(tmp) / "track.m4a"
                with mock.patch.object(manager.session, "get", side_effect=[info, audio]) as get:
                    ok = manager.download_audio_by_quality("979576276", "Audio Vivid 菁彩声", str(save_path))
                self.assertTrue(save_path.exists())

        self.assertTrue(ok)
        query = parse_qs(urlparse(get.call_args_list[0].args[0]).query)
        self.assertEqual(query["trackQualityLevel"], ["13"])
        self.assertEqual(manager.last_download_source, "mobile_v4_level_13")

    def test_dolby_never_accepts_lower_level_or_calls_media_url(self):
        info = FakeResponse(json_data={
            "ret": 0,
            "playUrlInfos": [{
                "qualityLevel": 2,
                "qualityName": "超高音质",
                "decodeUrl": "https://audio.example/lower.m4a",
            }],
        })

        with contextlib.redirect_stdout(io.StringIO()):
            manager = XimalayaDownloadManager(mobile_credentials=self._mobile_credentials("mobile-ticket"))
            with tempfile.TemporaryDirectory() as tmp:
                save_path = Path(tmp) / "track.m4a"
                with mock.patch.object(manager.session, "get", return_value=info) as get:
                    ok = manager.download_audio_by_quality("979576276", "杜比全景声", str(save_path))
                self.assertFalse(save_path.exists())

        self.assertFalse(ok)
        self.assertEqual(get.call_count, 1)
        self.assertIn("不会回退", manager.last_error)

    def test_dolby_rejects_plain_aac_disguised_as_level_twelve(self):
        body = b"\x00\x00\x00\x18ftypM4A \x00\x00\x00\x00M4A isom" + b"mp4a" + (b"audio" * 1000)
        info = self._spatial_info(12, "杜比全景声", "https://audio.example/plain-aac.m4a", len(body))
        audio = FakeResponse(headers={"content-type": "audio/mp4"}, body=body)

        with contextlib.redirect_stdout(io.StringIO()):
            manager = XimalayaDownloadManager(mobile_credentials=self._mobile_credentials("mobile-ticket"))
            with tempfile.TemporaryDirectory() as tmp:
                save_path = Path(tmp) / "track.m4a"
                with mock.patch.object(manager.session, "get", side_effect=[info, audio]):
                    ok = manager.download_audio_by_quality("979576276", "杜比全景声", str(save_path))
                self.assertFalse(save_path.exists())
                self.assertFalse(Path(str(save_path) + ".part").exists())

        self.assertFalse(ok)
        self.assertIn("未检测到 Dolby", manager.last_error)

    def test_lossless_requires_login_and_never_calls_lower_quality_api(self):
        with contextlib.redirect_stdout(io.StringIO()):
            manager = XimalayaDownloadManager()
            with tempfile.TemporaryDirectory() as tmp:
                with mock.patch.object(manager.session, "get") as get:
                    ok = manager.download_audio_by_quality(
                        "539592153", "无损真人录制", str(Path(tmp) / "track.flac")
                    )

        self.assertFalse(ok)
        self.assertEqual(manager.last_error_type, "restricted")
        self.assertIn("登录", manager.last_error)
        get.assert_not_called()

    def test_web_cookie_never_impersonates_mobile_credentials(self):
        with contextlib.redirect_stdout(io.StringIO()):
            manager = XimalayaDownloadManager(cookie_string="_token=member; device_id=browser")
            with tempfile.TemporaryDirectory() as tmp:
                with mock.patch.object(manager.session, "get") as get:
                    ok = manager.download_audio_by_quality(
                        "539592153", "无损真人录制", str(Path(tmp) / "track.flac")
                    )

        self.assertFalse(ok)
        self.assertIn("x-tk", manager.last_error)
        get.assert_not_called()

    def test_ios_request_uses_captured_mobile_headers_not_web_cookie(self):
        captured = """Cookie: channel=ios-b1; 1&*token=123456&mobile-session
User-Agent: ting_v9.4.94_c5(CFNetwork, iOS 26.1, iPhone15,2)
x-tk: ios-member-ticket
Accept-Language: zh-CN,zh-Hans;q=0.9
"""
        denied = FakeResponse(json_data={"ret": 50, "msg": "未登陆"})

        with contextlib.redirect_stdout(io.StringIO()):
            manager = XimalayaDownloadManager(
                cookie_string="_token=browser-member; device_id=browser",
                mobile_credentials=captured,
            )
            with tempfile.TemporaryDirectory() as tmp:
                with mock.patch.object(manager, "_build_mobile_v4_sign", return_value="signed"):
                    with mock.patch.object(manager.session, "get", return_value=denied) as get:
                        ok = manager.download_audio_by_quality(
                            "539592153", "无损真人录制", str(Path(tmp) / "track.flac")
                        )

        self.assertFalse(ok)
        request = get.call_args
        query = parse_qs(urlparse(request.args[0]).query)
        self.assertEqual(query["device"], ["ios"])
        self.assertEqual(request.kwargs["headers"]["x-tk"], "ios-member-ticket")
        self.assertEqual(request.kwargs["headers"]["Cookie"], "channel=ios-b1; 1&*token=123456&mobile-session")
        self.assertNotIn("browser-member", request.kwargs["headers"]["Cookie"])
        self.assertTrue(request.kwargs["headers"]["User-Agent"].startswith("ting_v9.4.94"))
        self.assertIn("重新抓取", manager.last_error)

    def test_lossless_uses_android_v4_level_three_and_direct_flac(self):
        lossless_size = 5004
        info = FakeResponse(json_data={
            "ret": 0,
            "trackBaseVO": {
                "trackInfo": {
                    "isXimiUhqTrack": True,
                    "isXimiUhqAuthorized": True,
                    "playUrlInfos": [{
                        "qualityLevel": 3,
                        "qualityName": "无损音质",
                        "hasAuthorized": True,
                        "decodeUrl": "https://audio.example/member-lossless.flac",
                        "fileSize": lossless_size,
                    }],
                },
            },
        })
        audio = FakeResponse(
            headers={"content-type": "audio/flac", "content-length": str(lossless_size)},
            body=b"fLaC" + (b"audio" * 1000),
        )

        cookie = "_token=member"
        with contextlib.redirect_stdout(io.StringIO()):
            manager = XimalayaDownloadManager(
                cookie_string=cookie,
                mobile_credentials=self._mobile_credentials(),
            )
            with tempfile.TemporaryDirectory() as tmp:
                save_path = Path(tmp) / "track.flac"
                with mock.patch.object(manager.session, "get", side_effect=[info, audio]) as get:
                    ok = manager.download_audio_by_quality(
                        "539592153", "无损真人录制", str(save_path)
                    )
                self.assertTrue(save_path.exists())
                self.assertTrue(save_path.read_bytes().startswith(b"fLaC"))

        self.assertTrue(ok)
        self.assertEqual(get.call_count, 2)
        api_call = get.call_args_list[0]
        self.assertIn("/mobile-playpage/track/v4/baseInfo/", api_call.args[0])
        api_query = parse_qs(urlparse(api_call.args[0]).query)
        self.assertEqual(api_query["trackQualityLevel"], ["3"])
        self.assertEqual(api_query["trackId"], ["539592153"])
        self.assertFalse(any(char.isspace() for char in api_query["sign"][0]))
        self.assertNotIn("%0A", api_call.args[0].upper())
        self.assertEqual(api_call.kwargs["headers"]["x-tk"], "member-mobile-ticket")
        self.assertEqual(api_call.kwargs["headers"]["Cookie"], "1&*token=123456&mobile-session")
        self.assertEqual(api_call.kwargs["headers"]["Accept"], "*/*")
        self.assertEqual(api_call.kwargs["headers"]["Cookie2"], "$version=1")
        self.assertNotIn("xmly_x_tk", api_call.kwargs["headers"]["Cookie"])
        self.assertEqual(get.call_args_list[1].args[0], "https://audio.example/member-lossless.flac")
        self.assertEqual(manager.last_download_source, "mobile_v4_lossless")
        self.assertEqual(manager.last_download_expected_size, lossless_size)

    def test_android_v4_retries_android2_when_android_sign_branch_is_rejected(self):
        body = b"fLaC" + (b"audio" * 1000)
        rejected = FakeResponse(json_data={"ret": 1001, "msg": "系统繁忙"})
        info = self._spatial_info(
            3, "无损音质", "https://audio.example/member-lossless.flac", len(body)
        )
        audio = FakeResponse(
            headers={"content-type": "audio/flac", "content-length": str(len(body))},
            body=body,
        )

        with contextlib.redirect_stdout(io.StringIO()):
            manager = XimalayaDownloadManager(mobile_credentials=self._mobile_credentials())
            with tempfile.TemporaryDirectory() as tmp:
                save_path = Path(tmp) / "track.flac"
                with mock.patch.object(manager.session, "get", side_effect=[rejected, info, audio]) as get:
                    ok = manager.download_audio_by_quality(
                        "539592153", "无损真人录制", str(save_path)
                    )

        self.assertTrue(ok)
        first_query = parse_qs(urlparse(get.call_args_list[0].args[0]).query)
        second_query = parse_qs(urlparse(get.call_args_list[1].args[0]).query)
        self.assertEqual(first_query["device"], ["android"])
        self.assertEqual(second_query["device"], ["android2"])
        self.assertNotEqual(
            first_query["sign"],
            second_query["sign"],
        )

    def test_captured_android2_request_uses_android2_first(self):
        credentials = self._mobile_credentials()
        credentials["api_device"] = "android2"
        denied = FakeResponse(json_data={"ret": 50, "msg": "未登录"})

        with contextlib.redirect_stdout(io.StringIO()):
            manager = XimalayaDownloadManager(mobile_credentials=credentials)
            with tempfile.TemporaryDirectory() as tmp:
                with mock.patch.object(manager.session, "get", return_value=denied) as get:
                    ok = manager.download_audio_by_quality(
                        "539592153", "无损真人录制", str(Path(tmp) / "track.flac")
                    )

        self.assertFalse(ok)
        self.assertEqual(get.call_count, 1)
        query = parse_qs(urlparse(get.call_args.args[0]).query)
        self.assertEqual(query["device"], ["android2"])
        self.assertEqual(manager.last_error_type, "restricted")

    def test_lossless_rejects_lower_quality_response_without_fallback(self):
        info = FakeResponse(json_data={
            "ret": 0,
            "playUrlInfos": [{
                "qualityLevel": 1,
                "qualityName": "高清",
                "decodeUrl": "https://audio.example/low.m4a",
            }],
        })

        with contextlib.redirect_stdout(io.StringIO()):
            manager = XimalayaDownloadManager(
                cookie_string="_token=member", mobile_credentials=self._mobile_credentials()
            )
            with tempfile.TemporaryDirectory() as tmp:
                save_path = Path(tmp) / "track.flac"
                with mock.patch.object(manager.session, "get", return_value=info) as get:
                    ok = manager.download_audio_by_quality(
                        "539592153", "LOSSLESS", str(save_path)
                    )
                self.assertFalse(save_path.exists())

        self.assertFalse(ok)
        self.assertEqual(get.call_count, 1)
        self.assertIn("不会回退", manager.last_error)

    def test_lossless_rejects_encrypted_mobile_url(self):
        info = FakeResponse(json_data={
            "ret": 0,
            "playUrlInfos": [{
                "qualityLevel": 3,
                "qualityName": "lossless",
                "hasAuthorized": True,
                "url": "encrypted-mobile-play-url",
            }],
        })

        with contextlib.redirect_stdout(io.StringIO()):
            manager = XimalayaDownloadManager(
                cookie_string="_token=member", mobile_credentials=self._mobile_credentials()
            )
            with tempfile.TemporaryDirectory() as tmp:
                with mock.patch.object(manager.session, "get", return_value=info) as get:
                    ok = manager.download_audio_by_quality(
                        "539592153", "FLAC", str(Path(tmp) / "track.flac")
                    )

        self.assertFalse(ok)
        self.assertEqual(get.call_count, 1)
        self.assertEqual(manager.last_error_type, "restricted")
        self.assertIn("不会绕过", manager.last_error)

    def test_track_level_lossless_denial_overrides_nested_url(self):
        info = FakeResponse(json_data={
            "ret": 0,
            "trackInfo": {
                "isXimiUhqAuthorized": False,
                "playUrlInfos": [{
                    "qualityLevel": 3,
                    "decodeUrl": "https://audio.example/should-not-download.flac",
                }],
            },
        })

        with contextlib.redirect_stdout(io.StringIO()):
            manager = XimalayaDownloadManager(
                cookie_string="_token=member", mobile_credentials=self._mobile_credentials()
            )
            with tempfile.TemporaryDirectory() as tmp:
                with mock.patch.object(manager.session, "get", return_value=info) as get:
                    ok = manager.download_audio_by_quality(
                        "539592153", "LOSSLESS", str(Path(tmp) / "track.flac")
                    )

        self.assertFalse(ok)
        self.assertEqual(get.call_count, 1)
        self.assertEqual(manager.last_error_type, "restricted")
        self.assertIn("没有", manager.last_error)

    def test_lossless_rejects_non_flac_payload(self):
        info = FakeResponse(json_data={
            "ret": 0,
            "playUrlInfos": [{
                "qualityLevel": 3,
                "hasAuthorized": True,
                "decodeUrl": "https://audio.example/fake-lossless.flac",
            }],
        })
        fake_audio = FakeResponse(
            headers={"content-type": "audio/mp4"},
            body=b"ftyp" + (b"aac" * 1000),
        )

        with contextlib.redirect_stdout(io.StringIO()):
            manager = XimalayaDownloadManager(
                cookie_string="_token=member", mobile_credentials=self._mobile_credentials()
            )
            with tempfile.TemporaryDirectory() as tmp:
                save_path = Path(tmp) / "track.flac"
                with mock.patch.object(manager.session, "get", side_effect=[info, fake_audio]):
                    ok = manager.download_audio_by_quality(
                        "539592153", "无损真人录制", str(save_path)
                    )
                self.assertFalse(save_path.exists())

        self.assertFalse(ok)
        self.assertIn("不是可直接播放的 FLAC", manager.last_error)

    def test_m4a_permission_response_is_exposed_as_restricted(self):
        with contextlib.redirect_stdout(io.StringIO()):
            manager = XimalayaDownloadManager(cookie_string="_token=example")
            response = FakeResponse(
                headers={"content-type": "application/json", "content-length": "32"},
                body=b'{"ret":130,"msg":"VIP required"}',
            )
            with tempfile.TemporaryDirectory() as tmp:
                with mock.patch.object(manager.session, "get", return_value=response):
                    ok = manager.download_audio_by_quality("123", "M4A_96K", str(Path(tmp) / "track.m4a"))

        self.assertFalse(ok)
        self.assertEqual(manager.last_error_type, "restricted")
        self.assertTrue(manager.last_error)

    def test_http_forbidden_is_exposed_as_restricted(self):
        with contextlib.redirect_stdout(io.StringIO()):
            manager = XimalayaDownloadManager()
            response = FakeResponse(status_code=403)
            with tempfile.TemporaryDirectory() as tmp:
                with mock.patch.object(manager.session, "get", return_value=response):
                    ok = manager.download_audio_by_quality("123", "M4A_96K", str(Path(tmp) / "track.m4a"))

        self.assertFalse(ok)
        self.assertEqual(manager.last_error_type, "restricted")

    def test_successful_member_redirect_keeps_original_download_path(self):
        audio = FakeResponse(
            headers={"content-type": "audio/mp4", "content-length": "4096"},
            body=b"ftyp" + (b"audio" * 1000),
        )

        with contextlib.redirect_stdout(io.StringIO()):
            manager = XimalayaDownloadManager(cookie_string="_token=member")
            with tempfile.TemporaryDirectory() as tmp:
                save_path = Path(tmp) / "track.m4a"
                with mock.patch.object(manager.session, "get", return_value=audio) as get:
                    ok = manager.download_audio_by_quality(
                        "member-track", "M4A_96K", str(save_path)
                    )

        self.assertTrue(ok)
        self.assertEqual(get.call_count, 1)
        self.assertEqual(
            get.call_args.args[0],
            "http://mobile.ximalaya.com/mobile/redirect/free/play/member-track/96",
        )
        self.assertEqual(get.call_args.kwargs["headers"]["Cookie"], "_token=member")

    def test_public_free_track_falls_back_after_redirect_ret_130(self):
        redirect_error = FakeResponse(
            headers={"content-type": "application/json", "content-length": "32"},
            body=b'{"ret":130,"msg":"130","seed":0}',
        )
        public_info = FakeResponse(json_data={
            "ret": 0,
            "isPublic": True,
            "isPaid": False,
            "isVip": False,
            "isVipFree": False,
            "hqNeedVip": False,
            "paidType": 0,
            "priceTypeId": 0,
            "priceTypeEnum": 0,
            "vipFreeType": 0,
            "vipFirstStatus": 0,
            "sampleDuration": 0,
            "priceTypes": [],
            "playPathHq": "",
            "playPathAacv224": "https://audio.example/free-track.mp3",
            "playPathAacv224Size": 4096,
        })
        audio = FakeResponse(
            headers={"content-type": "audio/mpeg", "content-length": "4096"},
            body=b"ID3" + (b"audio" * 1000),
        )

        with contextlib.redirect_stdout(io.StringIO()):
            manager = XimalayaDownloadManager()
            with tempfile.TemporaryDirectory() as tmp:
                save_path = Path(tmp) / "track.m4a"
                with mock.patch.object(
                    manager.session, "get", side_effect=[redirect_error, public_info, audio]
                ) as get:
                    ok = manager.download_audio_by_quality(
                        "28311957", "M4A_96K", str(save_path)
                    )
                self.assertTrue(save_path.exists())
                self.assertGreater(save_path.stat().st_size, 1024)

        self.assertTrue(ok)
        self.assertEqual(manager.last_error_type, "")
        self.assertEqual(manager.last_download_source, "public_base_info:playPathAacv224")
        self.assertIn("device=ios", get.call_args_list[1].args[0])
        self.assertNotIn("Cookie", get.call_args_list[1].kwargs["headers"])
        self.assertEqual(get.call_args_list[2].args[0], "https://audio.example/free-track.mp3")

    def test_paid_track_never_uses_public_cdn_fallback(self):
        redirect_error = FakeResponse(
            headers={"content-type": "application/json", "content-length": "32"},
            body=b'{"ret":130,"msg":"130","seed":0}',
        )
        paid_info = FakeResponse(json_data={
            "ret": 0,
            "isPublic": True,
            "isPaid": True,
            "playUrl64": "https://audio.example/paid-track.mp3",
        })

        with contextlib.redirect_stdout(io.StringIO()):
            manager = XimalayaDownloadManager()
            with tempfile.TemporaryDirectory() as tmp:
                with mock.patch.object(
                    manager.session, "get", side_effect=[redirect_error, paid_info]
                ) as get:
                    ok = manager.download_audio_by_quality(
                        "paid-track", "M4A_96K", str(Path(tmp) / "track.m4a")
                    )

        self.assertFalse(ok)
        self.assertEqual(manager.last_error_type, "restricted")
        self.assertEqual(get.call_count, 2)
        self.assertNotIn("Cookie", get.call_args_list[1].kwargs["headers"])

    def test_vip_track_never_uses_public_cdn_fallback(self):
        redirect_error = FakeResponse(
            headers={"content-type": "application/json", "content-length": "32"},
            body=b'{"ret":130,"msg":"130","seed":0}',
        )
        vip_info = FakeResponse(json_data={
            "ret": 0,
            "isPublic": True,
            "isVipFree": True,
            "playUrl64": "https://audio.example/vip-track.mp3",
        })

        with contextlib.redirect_stdout(io.StringIO()):
            manager = XimalayaDownloadManager(cookie_string="_token=member")
            with tempfile.TemporaryDirectory() as tmp:
                with mock.patch.object(
                    manager.session, "get", side_effect=[redirect_error, vip_info]
                ) as get:
                    ok = manager.download_audio_by_quality(
                        "vip-track", "M4A_96K", str(Path(tmp) / "track.m4a")
                    )

        self.assertFalse(ok)
        self.assertEqual(manager.last_error_type, "restricted")
        self.assertEqual(get.call_count, 2)


if __name__ == "__main__":
    unittest.main()
