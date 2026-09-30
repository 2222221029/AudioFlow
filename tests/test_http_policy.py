# -*- coding: utf-8 -*-
"""`core/http_policy.py` 的行为锁定测试。

核心锁定一条顺序铁律（移植分析 P1-7）：**先解读响应体，再看体积**。
参考实现记录过：付费集的 47 字节 JSON 拒绝体被报成「响应过小」，
用户完全看不出真因是「这一集没权限」。
"""

import json

import pytest

from core import http_policy
from core.errors import (
    ContentInvalidError,
    PermissionDenied,
    TransientError,
)


def _write(tmp_path, name, payload: bytes):
    path = tmp_path / name
    path.write_bytes(payload)
    return str(path)


class TestErrorBodyDetection:
    def test_47_byte_paywall_json_is_reported_as_permission_not_size(self, tmp_path):
        """最关键的一条：体积很小，但真因是权限。"""
        body = json.dumps({"msg": "立即购买畅听", "ret": 726}, ensure_ascii=False).encode("utf-8")
        assert len(body) <= http_policy.SNIFF_MAX_BYTES
        path = _write(tmp_path, "a.m4a", body)

        is_error, message = http_policy.looks_like_error_body(path, "application/json")
        assert is_error is True
        assert "726" in message or "购买" in message
        assert "过小" not in message

        with pytest.raises(PermissionDenied):
            http_policy.raise_for_error_body(path, "application/json")

    def test_ret_50_is_permission_denied(self, tmp_path):
        path = _write(tmp_path, "a.m4a", json.dumps({"ret": 50, "msg": "登录已失效"}).encode())
        with pytest.raises(PermissionDenied):
            http_policy.raise_for_error_body(path, "application/json")

    def test_ret_1001_is_transient_not_permission(self, tmp_path):
        path = _write(tmp_path, "a.m4a", json.dumps({"ret": 1001, "msg": "系统繁忙"}).encode())
        with pytest.raises(TransientError):
            http_policy.raise_for_error_body(path, "application/json")

    def test_html_error_page_is_content_invalid(self, tmp_path):
        path = _write(tmp_path, "a.m4a", b"<!DOCTYPE html><html><body>403 Forbidden</body></html>")
        with pytest.raises(ContentInvalidError):
            http_policy.raise_for_error_body(path, "text/html")

    def test_tiny_non_json_payload_is_flagged(self, tmp_path):
        path = _write(tmp_path, "a.m4a", b"\x00\x01\x02")
        is_error, message = http_policy.looks_like_error_body(path, "audio/mp4")
        assert is_error is True
        assert "不完整" in message

    def test_empty_file_is_flagged(self, tmp_path):
        path = _write(tmp_path, "a.m4a", b"")
        is_error, _ = http_policy.looks_like_error_body(path, "audio/mp4")
        assert is_error is True


class TestRealAudioIsNotFlagged:
    def test_mp3_like_big_file_passes(self, tmp_path):
        # >2KB 且 content-type 是 audio → 直接短路，不做任何读取
        path = _write(tmp_path, "a.mp3", b"ID3\x04\x00\x00" + b"\x00" * 8192)
        assert http_policy.looks_like_error_body(path, "audio/mpeg") == (False, "")
        http_policy.raise_for_error_body(path, "audio/mpeg")   # 不抛

    def test_m4a_without_content_type_and_large_passes(self, tmp_path):
        payload = b"\x00\x00\x00\x20ftypM4A " + b"\x00" * 8192
        path = _write(tmp_path, "a.m4a", payload)
        assert http_policy.looks_like_error_body(path, "") == (False, "")

    def test_flac_passes(self, tmp_path):
        path = _write(tmp_path, "a.flac", b"fLaC" + b"\x00" * 8192)
        assert http_policy.looks_like_error_body(path, "audio/flac") == (False, "")


class TestPeekIsSafe:
    def test_missing_file_returns_no_error(self, tmp_path):
        missing = str(tmp_path / "nope.m4a")
        assert http_policy.looks_like_error_body(missing, "") == (False, "")
        http_policy.raise_for_error_body(missing, "")   # 不抛

    def test_directory_path_tolerated(self, tmp_path):
        assert http_policy.looks_like_error_body(str(tmp_path), "") == (False, "")


class TestAudioSniffing:
    @pytest.mark.parametrize(
        "payload",
        [
            b"fLaC\x00\x00\x00\x22",
            b"RIFF\x00\x00\x00\x00WAVEfmt ",
            b"OggS\x00\x02\x00\x00",
            b"caff\x00\x01\x00\x00",
            b"ID3\x04\x00\x00\x00",
            b"\xff\xfb\x90\x00",
            b"\x00\x00\x00\x20ftypM4A ",
        ],
    )
    def test_known_containers_are_audio(self, payload):
        assert http_policy.is_probably_audio(payload) is True

    @pytest.mark.parametrize("payload", [b"", b"{", b"<!DOCTYPE", b"\x00\x01\x02\x03"])
    def test_junk_is_not_audio(self, payload):
        assert http_policy.is_probably_audio(payload) is False


class TestExtensionSniffing:
    def test_m4a_brand_stays_m4a(self, tmp_path):
        """M4A/M4B/M4P brand 明确是纯音频，必须保留 .m4a。

        参考实现记录过：旧版见 ftyp 一律改 .mp4，2115 个音频文件全被改名。
        """
        path = _write(tmp_path, "a.mp4", b"\x00\x00\x00\x20ftypM4A " + b"\x00" * 64)
        assert http_policy.sniff_extension(path, ".mp4") == ".m4a"

    def test_generic_ftyp_also_resolves_to_m4a_for_audiobooks(self, tmp_path):
        """与本项目既有 _detect_mobile_media_format 行为一致（不引入变化）。"""
        path = _write(tmp_path, "a.mp4", b"\x00\x00\x00\x20ftypmp42" + b"\x00" * 64)
        assert http_policy.sniff_extension(path, ".mp3") == ".m4a"

    @pytest.mark.parametrize(
        "payload,expected",
        [
            (b"fLaC\x00\x00", ".flac"),
            (b"RIFF\x00\x00\x00\x00WAVE", ".wav"),
            (b"OggS\x00\x02", ".ogg"),
            (b"caff\x00\x01", ".caf"),
            (b"ID3\x04\x00", ".mp3"),
            (b"\xff\xfb\x90\x00", ".mp3"),
        ],
    )
    def test_known_containers_map_to_extensions(self, tmp_path, payload, expected):
        path = _write(tmp_path, "a.bin", payload)
        assert http_policy.sniff_extension(path, ".xxx") == expected

    def test_unknown_payload_keeps_declared_extension(self, tmp_path):
        path = _write(tmp_path, "a.bin", b"\x00\x01\x02\x03\x04\x05")
        assert http_policy.sniff_extension(path, ".mp3") == ".mp3"

    def test_missing_file_keeps_declared_extension(self, tmp_path):
        assert http_policy.sniff_extension(str(tmp_path / "no"), ".m4a") == ".m4a"
