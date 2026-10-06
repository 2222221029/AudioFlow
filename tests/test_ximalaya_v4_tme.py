"""TME 系专辑（isTme=true，《大奉打更人》）移动端 V4 通道回归（v1.0.44）。

背景：V4 baseInfo 返回 128KMP3（ID3-MP3 容器、music-xmcdn.tencentmusic.com
CDN）。此前被两个历史逻辑误拒：CDN 白名单不认 tencentmusic.com 域名、
容器校验只放行 m4a。修复后沙盒端到端实测下载 2000 集成功（5.90MB，ID3）。
"""

from __future__ import annotations

import base64
import tempfile
from pathlib import Path

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