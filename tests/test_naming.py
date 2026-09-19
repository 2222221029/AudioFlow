"""`core/naming.py` 的兼容性与行为测试。

本文件的第一组用例采用「复刻旧实现做对照」的方式：把改造前四处
`sanitize_filename` 的实现原样搬进来作为参照，然后在大量边界输入上断言
新函数输出与旧实现**逐字节一致**。这样收敛重复实现的过程是可验证的
零行为变更，而不是"看起来差不多"。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.naming import (
    album_dirname,
    collapse_whitespace,
    sanitize_download_folder_name,
    sanitize_segment,
    unique_path,
)

# --- 改造前四处实现的原样复刻（仅作对照，不参与生产代码） --------------------


def _legacy_download_worker(filename: str) -> str:
    filename = str(filename or "").strip() or "未知"
    for char in ['<', '>', ':', '"', '/', '\\', '|', '?', '*']:
        filename = filename.replace(char, '_')
    if len(filename) > 200:
        filename = filename[:200]
    return filename


def _legacy_subscription_manager(filename) -> str:
    filename = str(filename or "")
    for char in ['<', '>', ':', '"', '/', '\\', '|', '?', '*']:
        filename = filename.replace(char, "_")
    filename = filename.strip()
    if len(filename) > 200:
        filename = filename[:200]
    return filename or "unknown"


def _legacy_ximalaya_download_manager(filename: str) -> str:
    for char in ['<', '>', ':', '"', '/', '\\', '|', '?', '*']:
        filename = filename.replace(char, '_')
    if len(filename) > 150:
        filename = filename[:150]
    if not filename:
        filename = "未知音频"
    return filename


def _legacy_download_manager(filename: str) -> str:
    illegal_chars = ['<', '>', ':', '"', '/', '\\', '|', '?', '*']
    for char in illegal_chars:
        filename = filename.replace(char, '_')
    if len(filename) > 150:
        filename = filename[:150]
    if not filename:
        filename = "未命名音频"
    return filename


# 覆盖：空值、纯空白、纯非法字符、超长、含全角空格、正常中文标题、边界长度
CASES = [
    "",
    None,
    "   ",
    "/",
    "///",
    "<>:" + '"' + "|?*",
    "第001集 门罗",
    "《民调局异闻录·暗夜将至》第001集 门罗",
    "a" * 149,
    "a" * 150,
    "a" * 151,
    "a" * 199,
    "a" * 200,
    "a" * 201,
    "a" * 400,
    "  /第001集/  ",
    "书名\u3000作者",
    "斗破苍穹|多播剧|完结放心听",
    "前/后:标题*测试?",
    "  " + "x" * 210 + "  ",
]


class NamingCompatibilityTest(unittest.TestCase):
    """收敛重复实现不得改变任何既有产物。"""

    def test_download_worker_call_site_is_byte_identical(self):
        for case in CASES:
            with self.subTest(case=repr(case)[:40]):
                self.assertEqual(
                    sanitize_segment(case, max_len=200, fallback="未知", strip=True),
                    _legacy_download_worker(case),
                )

    def test_subscription_manager_call_site_is_byte_identical(self):
        for case in CASES:
            with self.subTest(case=repr(case)[:40]):
                self.assertEqual(
                    sanitize_segment(case, max_len=200, fallback="unknown", strip=True),
                    _legacy_subscription_manager(case),
                )

    def test_ximalaya_download_manager_call_site_is_byte_identical(self):
        for case in CASES:
            if case is None:
                continue  # 旧实现直接对 None 调用 .replace，会抛异常
            with self.subTest(case=repr(case)[:40]):
                self.assertEqual(
                    sanitize_segment(case, max_len=150, fallback="未知音频", strip=False),
                    _legacy_ximalaya_download_manager(case),
                )

    def test_download_manager_call_site_is_byte_identical(self):
        for case in CASES:
            if case is None:
                continue
            with self.subTest(case=repr(case)[:40]):
                self.assertEqual(
                    sanitize_segment(case, max_len=150, fallback="未命名音频", strip=False),
                    _legacy_download_manager(case),
                )


class SanitizeSegmentTest(unittest.TestCase):
    def test_illegal_characters_are_replaced(self):
        self.assertEqual(sanitize_segment('a/b\\c:d*e?f"g<h>i|j'), "a_b_c_d_e_f_g_h_i_j")

    def test_length_limit_is_enforced(self):
        self.assertEqual(len(sanitize_segment("x" * 500, max_len=150)), 150)

    def test_empty_input_falls_back(self):
        self.assertEqual(sanitize_segment("", fallback="兜底"), "兜底")
        self.assertEqual(sanitize_segment("   ", fallback="兜底"), "兜底")
        self.assertEqual(sanitize_segment(None, fallback="兜底"), "兜底")

    def test_download_folder_name_keeps_whitespace_and_uses_its_own_fallback(self):
        self.assertEqual(sanitize_download_folder_name(""), "未知专辑")
        # 不 strip：与 web_server 的原实现一致，但非法字符仍被替换
        self.assertEqual(sanitize_download_folder_name(" a/b "), " a_b ")


class AlbumDirnameTest(unittest.TestCase):
    def test_marketing_suffix_is_truncated_when_short_requested(self):
        title = "斗破苍穹|多播剧|完结放心听|热门动漫原著|大神作者天蚕土豆作品|全站最热"
        self.assertEqual(album_dirname(title, short=True), "斗破苍穹")

    def test_marketing_separators_are_normalized_when_short_is_off(self):
        # '|' 属于非法路径字符，会被替换成 '_'；short=False 只表示不主动截断
        # 营销串，并不保留分隔符本身。
        self.assertEqual(
            album_dirname("斗破苍穹|多播剧|完结放心听"),
            "斗破苍穹_多播剧_完结放心听",
        )

    def test_album_id_is_opt_in(self):
        title = "斗破苍穹|多播剧"
        self.assertEqual(album_dirname(title, 14591196, short=True), "斗破苍穹")
        self.assertEqual(
            album_dirname(title, 14591196, short=True, include_id=True),
            "斗破苍穹 [14591196]",
        )

    def test_album_id_is_ignored_when_absent(self):
        self.assertEqual(
            album_dirname("三体", None, include_id=True),
            "三体",
        )
        self.assertEqual(
            album_dirname("三体", "", include_id=True),
            "三体",
        )

    def test_path_separators_in_title_cannot_escape_the_directory(self):
        self.assertNotIn("/", album_dirname("../../etc/passwd"))


class UniquePathTest(unittest.TestCase):
    def test_returns_original_when_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "new.mp3"
            self.assertEqual(unique_path(target), str(target))

    def test_appends_counter_instead_of_overwriting(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "track.mp3"
            target.write_bytes(b"x")
            second = unique_path(target)
            self.assertEqual(second, str(Path(tmp) / "track (2).mp3"))
            Path(second).write_bytes(b"y")
            self.assertEqual(unique_path(target), str(Path(tmp) / "track (3).mp3"))


class CollapseWhitespaceTest(unittest.TestCase):
    def test_full_width_and_repeated_spaces_collapse(self):
        self.assertEqual(collapse_whitespace("书名\u3000\u3000作者   "), "书名 作者")


if __name__ == "__main__":
    unittest.main()
