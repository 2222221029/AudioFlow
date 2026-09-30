# -*- coding: utf-8 -*-
"""`core/quality_check.py` 的行为锁定测试（移植分析 P2-12）。

⚠ 本文件的 MP3/FLAC/WAV/M4A 用例**用 ffmpeg 真实编码**生成，而不是手工拼字节 ——
手工拼的「假容器」只能证明解析器不崩，证明不了它能读对**真实编码器产出**的
时长与码率，而后者才是体检的价值所在。

ffmpeg 不可用时相关用例自动 skip（不 fail），保证 CI 在有/无 ffmpeg 时都稳定。
"""

import os
import shutil
import subprocess

import pytest

from core import quality_check as qc

FFMPEG = shutil.which("ffmpeg")


def _make_audio(path, *, seconds=3.0, codec="libmp3lame", bitrate="128k", fmt=None):
    """用 ffmpeg 生成一段真实音频（正弦波）。返回是否成功。"""
    if not FFMPEG:
        return False
    args = [
        FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
    ]
    if bitrate:
        args += ["-b:a", bitrate]
    if fmt:
        args += ["-f", fmt]
    args.append(str(path))
    try:
        subprocess.run(args, check=True, capture_output=True, timeout=60)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return False
    return os.path.exists(str(path)) and os.path.getsize(str(path)) > 0


needs_ffmpeg = pytest.mark.skipif(not FFMPEG, reason="需要 ffmpeg 生成真实音频")


class TestQualityMapping:
    @pytest.mark.parametrize(
        "quality,expected",
        [
            ("24K", 18), ("48K", 40), ("96K", 80), ("128K", 110), ("320K", 280),
            ("lossless", 500), ("flac", 500), ("best", 0),
        ],
    )
    def test_exact_matches(self, quality, expected):
        assert qc.expected_min_kbps(quality) == expected

    @pytest.mark.parametrize(
        "quality,expected",
        [
            ("96K 标准", 80),
            ("无损优先（自动降级）", 500),
            ("128k", 110),
        ],
    )
    def test_fuzzy_matches(self, quality, expected):
        assert qc.expected_min_kbps(quality) == expected

    @pytest.mark.parametrize("quality", ["", None, "自动最高音质", "mystery"])
    def test_unknown_quality_has_no_floor(self, quality):
        assert qc.expected_min_kbps(quality) == 0


class TestProbeMissingAndBroken:
    def test_missing_file(self, tmp_path):
        result = qc.probe_audio(str(tmp_path / "nope.mp3"))
        assert result["exists"] is False
        assert result["error"]
        # probe_audio 只负责探参数，不判 verdict；verdict 由 check_file 给
        assert "verdict" not in result

    def test_empty_file(self, tmp_path):
        path = tmp_path / "empty.mp3"
        path.write_bytes(b"")
        result = qc.probe_audio(str(path))
        assert result["exists"] is True
        assert result["error"]
        assert result["size"] == 0

    def test_garbage_file(self, tmp_path):
        path = tmp_path / "junk.mp3"
        path.write_bytes(b"\x00\x01\x02\x03" * 1000)
        result = qc.probe_audio(str(path))
        assert result["container"] == ""
        assert "无法识别" in result["error"]

    def test_directory_path(self, tmp_path):
        result = qc.probe_audio(str(tmp_path))
        assert result["exists"] is False


@needs_ffmpeg
class TestRealMp3:
    def test_probes_constant_bitrate(self, tmp_path):
        path = tmp_path / "t.mp3"
        assert _make_audio(path, codec="libmp3lame", bitrate="128k")
        result = qc.probe_audio(str(path))
        assert result["container"] == "mp3"
        assert result["bitrate_kbps"] == 128
        assert result["duration"] > 0

    def test_detects_low_bitrate_against_requested_quality(self, tmp_path):
        """核心场景：请求 96K 但实际拿到 48K —— 必须报 low_bitrate。

        这正是 `docs/接口参考借鉴分析.md` P0-1 记录的静默失效：
        「用户以为下了 96K，实际 48K，且文件名写着 96K」。
        """
        path = tmp_path / "low.mp3"
        assert _make_audio(path, codec="libmp3lame", bitrate="48k")
        result = qc.check_file(str(path), expected_quality="96K")
        assert result["verdict"] == "low_bitrate"
        assert any("码率不达标" in i for i in result["issues"])

    def test_passes_when_bitrate_meets_quality(self, tmp_path):
        path = tmp_path / "ok.mp3"
        assert _make_audio(path, codec="libmp3lame", bitrate="128k")
        result = qc.check_file(str(path), expected_quality="96K")
        assert result["verdict"] == "ok"
        assert result["issues"] == []

    def test_no_floor_means_never_low_bitrate(self, tmp_path):
        path = tmp_path / "any.mp3"
        assert _make_audio(path, codec="libmp3lame", bitrate="48k")
        result = qc.check_file(str(path), expected_quality="best")
        assert result["verdict"] == "ok"


@needs_ffmpeg
class TestRealFlac:
    def test_probes_flac_streaminfo(self, tmp_path):
        path = tmp_path / "t.flac"
        if not _make_audio(path, codec="flac", bitrate=None):
            pytest.skip("ffmpeg 无法编码 flac")
        result = qc.probe_audio(str(path))
        assert result["container"] == "flac"
        assert result["duration"] > 0

    def test_flac_bitrate_is_high(self, tmp_path):
        path = tmp_path / "t.flac"
        if not _make_audio(path, codec="flac", bitrate=None):
            pytest.skip("ffmpeg 无法编码 flac")
        result = qc.probe_audio(str(path))
        # 无损档要求 >= 500kbps；3 秒正弦波压缩率极高，可能达不到，
        # 这里只断言「解析出了码率」，不拿合成信号当无损判据
        assert result["bitrate_kbps"] >= 0


@needs_ffmpeg
class TestRealWav:
    def test_probes_wav_fmt_chunk(self, tmp_path):
        path = tmp_path / "t.wav"
        if not _make_audio(path, bitrate=None, fmt="wav"):
            pytest.skip("ffmpeg 无法编码 wav")
        result = qc.probe_audio(str(path))
        assert result["container"] == "wav"
        assert result["duration"] > 0
        assert result["bitrate_kbps"] > 0


@needs_ffmpeg
class TestRealM4a:
    def test_probes_mp4_mvhd(self, tmp_path):
        path = tmp_path / "t.m4a"
        if not _make_audio(path, codec="aac", bitrate="128k", fmt="ipod"):
            pytest.skip("ffmpeg 无法编码 m4a")
        result = qc.probe_audio(str(path))
        assert result["container"] == "m4a"
        assert result["duration"] > 0


class TestDurationAndSizeChecks:
    def test_size_mismatch_is_damaged(self, tmp_path):
        """CDN 静默截断的场景（参考文档 P1-6 记录过：本地 4.95MB / 服务端 16.80MB）。"""
        path = tmp_path / "truncated.m4a"
        path.write_bytes(b"\x00\x00\x00\x20ftypM4A " + b"\x00" * 30000)
        result = qc.check_file(str(path), expected_size=16 * 1024 * 1024)
        assert result["verdict"] == "damaged"
        assert any("体积偏小" in i for i in result["issues"])

    def test_size_within_tolerance_passes(self, tmp_path):
        path = tmp_path / "ok.m4a"
        payload = b"\x00\x00\x00\x20ftypM4A " + b"\x00" * 100000
        path.write_bytes(payload)
        result = qc.check_file(str(path), expected_size=len(payload))
        assert result["verdict"] == "ok"

    def test_tiny_file_is_damaged(self, tmp_path):
        path = tmp_path / "tiny.mp3"
        path.write_bytes(b"ID3\x04\x00\x00" + b"\x00" * 100)
        result = qc.check_file(str(path))
        assert result["verdict"] == "damaged"

    @needs_ffmpeg
    def test_duration_mismatch_is_damaged(self, tmp_path):
        path = tmp_path / "short.mp3"
        assert _make_audio(path, seconds=2.0, codec="libmp3lame", bitrate="128k")
        result = qc.check_file(str(path), expected_duration=600.0)   # 声明 10 分钟
        assert result["verdict"] == "damaged"
        assert any("时长不符" in i for i in result["issues"])


class TestCheckDirectory:
    def test_excludes_partial_files(self, tmp_path):
        (tmp_path / "0001 a.m4a.part").write_bytes(b"x" * 50000)
        (tmp_path / "0001 a.m4a.part.s0").write_bytes(b"x" * 50000)
        report = qc.check_directory(str(tmp_path))
        assert report["total"] == 0

    @needs_ffmpeg
    def test_mixed_directory_report(self, tmp_path):
        assert _make_audio(tmp_path / "0001 good.mp3", codec="libmp3lame", bitrate="128k")
        assert _make_audio(tmp_path / "0002 low.mp3", codec="libmp3lame", bitrate="48k")
        (tmp_path / "0003 broken.mp3").write_bytes(b"\x00" * 50000)

        report = qc.check_directory(str(tmp_path), expected_quality="96K")
        assert report["total"] == 3
        assert report["ok"] == 1
        assert report["low_bitrate"] == 1
        assert report["damaged"] == 1
        assert report["healthy"] is False
        assert report["pass_rate"] == pytest.approx(33.3, abs=0.2)
        assert len(report["files"]) == 2

    @needs_ffmpeg
    def test_all_healthy_directory(self, tmp_path):
        for i in range(1, 4):
            assert _make_audio(tmp_path / f"{i:04d} t.mp3", codec="libmp3lame", bitrate="128k")
        report = qc.check_directory(str(tmp_path), expected_quality="96K")
        assert report["total"] == 3
        assert report["healthy"] is True
        assert report["pass_rate"] == 100.0
        assert report["files"] == []

    def test_empty_directory(self, tmp_path):
        report = qc.check_directory(str(tmp_path))
        assert report["total"] == 0
        assert report["healthy"] is False
        assert report["pass_rate"] == 0.0

    def test_missing_directory(self, tmp_path):
        report = qc.check_directory(str(tmp_path / "nope"))
        assert report["total"] == 0

    def test_extension_filter(self, tmp_path):
        (tmp_path / "a.txt").write_bytes(b"x" * 50000)
        (tmp_path / "b.mp3").write_bytes(b"\x00" * 50000)
        report = qc.check_directory(str(tmp_path), extensions=(".mp3",))
        assert report["total"] == 1

    @needs_ffmpeg
    def test_needs_redo_lists_problem_files(self, tmp_path):
        assert _make_audio(tmp_path / "ok.mp3", codec="libmp3lame", bitrate="128k")
        bad = tmp_path / "bad.mp3"
        assert _make_audio(bad, codec="libmp3lame", bitrate="48k")
        report = qc.check_directory(str(tmp_path), expected_quality="96K")
        redo = qc.needs_redo(report)
        assert len(redo) == 1
        assert redo[0].endswith("bad.mp3")

    def test_needs_redo_on_empty_report(self):
        assert qc.needs_redo({}) == []
        assert qc.needs_redo(None) == []


class TestUnreadableIsNotDamaged:
    """权限问题 ≠ 文件损坏。

    真踩过：在一个只读挂载点上跑体检，整个目录的文件全被报成「损坏」，
    `--list-redo` 导出一份全是噪声的清单。权限读不到的文件重下也解决不了。
    """

    @pytest.fixture
    def unreadable(self, tmp_path):
        if os.geteuid() == 0:
            pytest.skip("root 无视文件权限，无法构造 unreadable 场景")
        path = tmp_path / "secret.mp3"
        path.write_bytes(b"\xff\xfb\x90\x00" + b"\x00" * 50000)
        os.chmod(str(path), 0o000)
        yield str(path)
        os.chmod(str(path), 0o644)

    def test_verdict_is_unreadable_not_damaged(self, unreadable):
        result = qc.check_file(unreadable)
        assert result["verdict"] == "unreadable"

    def test_unreadable_excluded_from_redo_list(self, unreadable, tmp_path):
        report = qc.check_directory(str(tmp_path))
        assert report["unreadable"] == 1
        assert qc.needs_redo(report) == []

    def test_unreadable_does_not_tank_pass_rate(self, unreadable, tmp_path):
        """一个权限问题不该把整个目录的达标率拉成 0%。"""
        report = qc.check_directory(str(tmp_path))
        assert report["readable"] == 0
        assert report["pass_rate"] == 0.0      # 无可读文件
        assert report["healthy"] is False

    @needs_ffmpeg
    def test_mixed_readable_and_unreadable(self, unreadable, tmp_path):
        # ⚠ 放同目录：`list_audio_files` 不递归子目录（刻意的 —— 专辑目录是平的）
        assert _make_audio(tmp_path / "good.mp3", codec="libmp3lame", bitrate="128k")
        report = qc.check_directory(str(tmp_path))
        assert report["unreadable"] >= 1
        assert report["ok"] == 1
        assert report["pass_rate"] == 100.0     # 分母只算可读的
        assert qc.needs_redo(report) == []      # 权限问题不进重下清单


class TestRedoVerdicts:
    def test_redo_set_is_explicit(self):
        assert qc.REDO_VERDICTS == frozenset({"low_bitrate", "damaged"})

    def test_missing_file_is_not_in_redo_set(self):
        assert "missing" not in qc.REDO_VERDICTS
        assert "unreadable" not in qc.REDO_VERDICTS
