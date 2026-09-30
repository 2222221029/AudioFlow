# -*- coding: utf-8 -*-
"""`core/local_index.py` 的行为锁定测试（移植分析 P1-5）。

重点：
  ① 半截文件（`.part` / `.part.s{i}`）**绝不能**被当成已下载 —— 这是参考实现
     `DownloadEngine.cs:182-183` 明确记录过的坑；
  ② 集号 + 标题前缀的命中必须**保守**：宁可漏判（重下一次）也不能误判
     （把真实缺失当成已下载 = 用户永久缺集）；
  ③ 索引缓存真的省掉了重复扫目录。
"""

import os
import time

import pytest

from core import local_index


@pytest.fixture(autouse=True)
def _clear_cache():
    local_index.invalidate()
    yield
    local_index.invalidate()


def _touch(directory, name, size=2048):
    path = os.path.join(str(directory), name)
    with open(path, "wb") as fh:
        fh.write(b"x" * size)
    return path


class TestPartialFilesExcluded:
    """约束①：半截文件永远不入索引。"""

    @pytest.mark.parametrize(
        "name",
        [
            "0001 第一集.m4a.part",
            "0001 第一集.m4a.part.s0",
            "0001 第一集.m4a.part.s7",
            "_report.json",
        ],
    )
    def test_partial_names_are_skipped(self, tmp_path, name):
        _touch(tmp_path, name)
        assert local_index.list_audio_files(str(tmp_path)) == []

    def test_partial_not_found_by_order_lookup(self, tmp_path):
        _touch(tmp_path, "0001 第一集.m4a.part")
        index = local_index.build_index(str(tmp_path))
        assert local_index.find_existing(index, 1, "第一集") is None

    def test_real_file_next_to_partial_is_found(self, tmp_path):
        _touch(tmp_path, "0001 第一集.m4a.part")
        real = _touch(tmp_path, "0001 第一集.m4a")
        index = local_index.build_index(str(tmp_path))
        assert local_index.find_existing(index, 1, "第一集") == real


class TestSizeFilter:
    def test_tiny_files_are_skipped(self, tmp_path):
        _touch(tmp_path, "0001 第一集.m4a", size=512)
        assert local_index.list_audio_files(str(tmp_path)) == []

    def test_exactly_at_threshold_is_kept(self, tmp_path):
        _touch(tmp_path, "0001 第一集.m4a", size=local_index.MIN_VALID_BYTES)
        assert len(local_index.list_audio_files(str(tmp_path))) == 1


class TestIndexLookup:
    def test_finds_by_order_and_title_prefix(self, tmp_path):
        target = _touch(tmp_path, "0007 第七章 风雨欲来.m4a")
        index = local_index.build_index(str(tmp_path))
        assert local_index.find_existing(index, 7, "第七章 风雨欲来") == target

    def test_prefix_probe_matches_shortened_title(self, tmp_path):
        """落盘标题被截断过也要能命中（只比对前 20 字符）。"""
        target = _touch(tmp_path, "0003 第三章 一个非常非常长的标题被截.m4a")
        index = local_index.build_index(str(tmp_path))
        assert local_index.find_existing(index, 3, "第三章 一个非常非常长的标题被截断了很多很多") == target

    def test_wrong_order_does_not_match(self, tmp_path):
        _touch(tmp_path, "0007 第七章.m4a")
        index = local_index.build_index(str(tmp_path))
        assert local_index.find_existing(index, 8, "第七章") is None

    def test_same_order_different_title_does_not_match(self, tmp_path):
        """⚠ 保守性是硬要求：标题不同就不算命中，宁可重下也不能漏集。"""
        _touch(tmp_path, "0007 第七章 风雨欲来.m4a")
        index = local_index.build_index(str(tmp_path))
        assert local_index.find_existing(index, 7, "完全不同的标题") is None

    def test_blank_title_matches_anything_in_bucket(self, tmp_path):
        target = _touch(tmp_path, "0007 第七章.m4a")
        index = local_index.build_index(str(tmp_path))
        assert local_index.find_existing(index, 7, "") == target

    def test_invalid_order_returns_none(self, tmp_path):
        _touch(tmp_path, "0007 第七章.m4a")
        index = local_index.build_index(str(tmp_path))
        assert local_index.find_existing(index, 0, "第七章") is None
        assert local_index.find_existing(index, None, "第七章") is None

    def test_empty_index_is_safe(self):
        assert local_index.find_existing({}, 1, "任意") is None
        assert local_index.find_existing(None, 1, "任意") is None

    def test_extension_filter_is_respected(self, tmp_path):
        _touch(tmp_path, "0005 第五集.txt")
        index = local_index.build_index(str(tmp_path))
        assert local_index.find_existing(index, 5, "第五集") is None
        assert local_index.find_existing(index, 5, "第五集", extensions=(".txt",)) is not None


class TestFindByStem:
    def test_finds_same_stem_different_extension(self, tmp_path):
        target = _touch(tmp_path, "0001 第一集.mp3")
        assert local_index.find_by_stem(str(tmp_path), os.path.join(str(tmp_path), "0001 第一集.m4a")) == target

    def test_returns_none_when_absent(self, tmp_path):
        assert local_index.find_by_stem(str(tmp_path), os.path.join(str(tmp_path), "0099 没有.m4a")) is None

    def test_ignores_tiny_candidate(self, tmp_path):
        _touch(tmp_path, "0001 第一集.mp3", size=100)
        assert local_index.find_by_stem(str(tmp_path), os.path.join(str(tmp_path), "0001 第一集.m4a")) is None

    def test_blank_stem_is_safe(self, tmp_path):
        assert local_index.find_by_stem(str(tmp_path), "") is None


class TestNormalization:
    def test_whitespace_is_collapsed(self):
        assert local_index._normalize_probe("第一集   标题") == "第一集 标题"

    def test_case_is_not_folded(self):
        """⚠ 不做大小写折叠：那会让不同文件互相误命中。"""
        assert local_index._normalize_probe("ABC") == "ABC"
        assert local_index._normalize_probe("abc") == "abc"

    def test_long_text_is_truncated(self):
        assert len(local_index._normalize_probe("x" * 100)) == 20


class TestCaching:
    def test_second_call_uses_cache(self, tmp_path, monkeypatch):
        _touch(tmp_path, "0001 第一集.m4a")
        local_index.list_audio_files(str(tmp_path))

        calls = {"n": 0}
        real_scandir = os.scandir

        def counting_scandir(path):
            calls["n"] += 1
            return real_scandir(path)

        monkeypatch.setattr(os, "scandir", counting_scandir)
        local_index.list_audio_files(str(tmp_path))
        assert calls["n"] == 0, "第二次调用应该命中缓存，不该再扫目录"

    def test_invalidate_forces_rescan(self, tmp_path, monkeypatch):
        _touch(tmp_path, "0001 第一集.m4a")
        local_index.list_audio_files(str(tmp_path))

        calls = {"n": 0}
        real_scandir = os.scandir

        def counting_scandir(path):
            calls["n"] += 1
            return real_scandir(path)

        monkeypatch.setattr(os, "scandir", counting_scandir)
        local_index.invalidate(str(tmp_path))
        local_index.list_audio_files(str(tmp_path))
        assert calls["n"] == 1

    def test_use_cache_false_always_rescans(self, tmp_path):
        _touch(tmp_path, "0001 第一集.m4a")
        local_index.list_audio_files(str(tmp_path))
        _touch(tmp_path, "0002 第二集.m4a")
        fresh = local_index.list_audio_files(str(tmp_path), use_cache=False)
        assert len(fresh) == 2

    def test_ttl_expiry(self, tmp_path):
        _touch(tmp_path, "0001 第一集.m4a")
        local_index.list_audio_files(str(tmp_path))
        _touch(tmp_path, "0002 第二集.m4a")
        again = local_index.list_audio_files(str(tmp_path), ttl=0)
        assert len(again) == 2


class TestRobustness:
    def test_missing_directory_returns_empty(self, tmp_path):
        assert local_index.list_audio_files(str(tmp_path / "nope")) == []

    def test_file_path_instead_of_directory(self, tmp_path):
        path = _touch(tmp_path, "a.m4a")
        assert local_index.list_audio_files(path) == []

    def test_subdirectories_are_ignored(self, tmp_path):
        os.makedirs(str(tmp_path / "sub"))
        _touch(tmp_path, "sub/0001 第一集.m4a")
        assert local_index.list_audio_files(str(tmp_path)) == []

    def test_blank_directory_is_safe(self):
        assert local_index.list_audio_files("") == []


class TestScalingBehavior:
    def test_index_build_is_single_directory_scan(self, tmp_path, monkeypatch):
        """核心收益：N 集只扫 1 次目录，而不是 N 次（参考实现的 O(n²) 教训）。"""
        for i in range(1, 101):
            _touch(tmp_path, f"{i:04d} 第{i}集.m4a")

        scans = {"n": 0}
        real_scandir = os.scandir

        def counting_scandir(path):
            scans["n"] += 1
            return real_scandir(path)

        monkeypatch.setattr(os, "scandir", counting_scandir)
        index = local_index.build_index(str(tmp_path), use_cache=False)
        assert scans["n"] == 1

        # 100 次查询零额外 syscall
        before = scans["n"]
        for i in range(1, 101):
            assert local_index.find_existing(index, i, f"第{i}集") is not None
        assert scans["n"] == before
