"""TME 系专辑（isTme=true，《大奉打更人》）移动端 V4 通道回归（v1.0.44+）。

背景：V4 baseInfo 返回 128KMP3（ID3-MP3 容器、music-xmcdn.tencentmusic.com
CDN）。此前被两个历史逻辑误拒：CDN 白名单不认 tencentmusic.com 域名、
容器校验只放行 m4a。修复后沙盒端到端实测下载 2000 集成功（5.90MB，ID3）。
v1.0.46 追加：高清档（level 1，M4A_64）同为 MP3 容器 + 容器被拒时降级链继续。
"""

from __future__ import annotations

import base64
import tempfile
from pathlib import Path
from unittest import mock

from core.ximalaya_download_manager import XimalayaDownloadManager


class TestV4TmeCdn:
    def test_looks_like_cdn_url_accepts_tencent_music(self):
        cls = XimalayaDownloadManager
        url = ("https://music-xmcdn.tencentmusic.com/long/M5000031abc.mp3"
               "?sign=abc&timestamp=123&cos_token=xyz")
        assert cls._looks_like_cdn_url(url)

    def test_looks_like_cdn_url_rejects_foreign_host(self):
        cls = XimalayaDownloadManager
        assert not cls._looks_like_cdn_url("https://evil.example.com/a.mp3")

    def test_validate_mobile_media_accepts_id3_mp3_for_standard(self):
        cls = XimalayaDownloadManager
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.mp3"
            path.write_bytes(b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\x00" * 64)
            ok, _ = cls._validate_mobile_media(str(path), 0, "audio/mpeg")
            assert ok
            ok2, _ = cls._validate_mobile_media(str(path), 2, "audio/mpeg")
            assert ok2

    def test_validate_mobile_media_accepts_id3_mp3_for_high(self):
        """高清档（level 1，M4A_64）在 TME 专辑中同为 ID3-MP3 容器。"""
        cls = XimalayaDownloadManager
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "h.mp3"
            path.write_bytes(b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\x00" * 64)
            ok, _ = cls._validate_mobile_media(str(path), 1, "audio/mpeg")
            assert ok

    def test_validate_mobile_media_still_rejects_garbage(self):
        cls = XimalayaDownloadManager
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.bin"
            path.write_bytes(b"\xde\xad\xbe\xef" * 16)
            ok, reason = cls._validate_mobile_media(str(path), 0, "application/octet-stream")
            assert not ok
            assert reason

    def test_decrypt_v2_xor_roundtrip(self):
        """V2 算法自洽：构造密文 → 解密函数还原出 CDN 明文直链。"""
        cls = XimalayaDownloadManager
        plain = ("https://music-xmcdn.tencentmusic.com/long/M5000031abc.mp3?sign=a")
        raw = plain.encode("utf-8")
        dynamic_key = bytes(range(16))
        table = cls._MOBILE_DOWNLOAD_V2_SUBSTITUTION
        inv = {v: i for i, v in enumerate(table)}
        payload = bytes(
            inv[raw[i]
                ^ cls._MOBILE_DOWNLOAD_V2_XOR_KEY[i % len(cls._MOBILE_DOWNLOAD_V2_XOR_KEY)]
                ^ dynamic_key[i % len(dynamic_key)]]
            for i in range(len(raw))
        )
        cipher = base64.urlsafe_b64encode(payload + dynamic_key).decode("ascii")
        decrypted = cls._decrypt_mobile_play_url_raw(cipher, 2)
        assert decrypted == plain


class TestV4TmeChainFallback:
    """容器被拒（普通档 quality_unavailable）时降级链应继续到更低档。"""

    def test_chain_falls_back_when_ordinary_container_rejected(self):
        cls = XimalayaDownloadManager
        m = cls.__new__(cls)
        m.last_error = ""
        m.last_error_type = ""
        m.last_download_source = ""
        m.mobile_credentials = {"cookie": "x", "x_tk": "y", "user_agent": "ua",
                                "api_device": "android2"}

        called = []

        def fake_quality(track, save, level, title="", progress_callback=None):
            called.append(level)
            if level in (3, 2):
                m.last_error = f"level{level} 无直链"
                m.last_error_type = "quality_unavailable"
                return False
            if level == 1:
                m.last_error = ("移动端返回的高清音质全景声音轨不是受支持的"
                                "MP4/M4A 容器，已拒绝保存")
                m.last_error_type = "quality_unavailable"
                return False
            m.last_error = ""
            m.last_error_type = ""
            m.last_download_source = "mobile_v4_level_0"
            return True

        with mock.patch.object(XimalayaDownloadManager, "_download_mobile_quality",
                               side_effect=fake_quality):
            ok = m._download_mobile_best_available("1017780000", "/tmp/x.mp3", "t")
        assert ok
        assert called == [3, 2, 1, 0], f"降级链未完整执行: {called}"

    def test_chain_stops_on_hard_failure(self):
        """非 quality_unavailable 的硬失败仍应立刻中断（不隐藏 DRM/网络错误）。"""
        cls = XimalayaDownloadManager
        m = cls.__new__(cls)
        m.last_error = ""
        m.last_error_type = ""
        m.mobile_credentials = {"cookie": "x", "x_tk": "y", "user_agent": "ua",
                                "api_device": "android2"}

        called = []

        def fake_quality(track, save, level, title="", progress_callback=None):
            called.append(level)
            m.last_error = f"level{level} 签名风控"
            m.last_error_type = "rate_limited"
            return False

        with mock.patch.object(XimalayaDownloadManager, "_download_mobile_quality",
                               side_effect=fake_quality):
            ok = m._download_mobile_best_available("1017780000", "/tmp/x.mp3", "t")
        assert not ok
        assert called == [3], f"硬失败应立即中断: {called}"