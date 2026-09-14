# -*- coding: utf-8 -*-
"""喜马拉雅章节完整分页拉取回归测试。

订阅检测/整本下载调用 get_album_chapters 时，专辑章节数超过单页(200)后
必须自动翻页取全，否则超过 200 集的新章节永远检测不到（误报「无需补全」）。
"""
import unittest
from unittest import mock

from core.ximalaya_manager import XimalayaManager


def _chapter(track_id, order):
    return {
        "id": str(track_id),
        "track_id": str(track_id),
        "title": f"第{order}集",
        "order_num": order,
    }


class XimalayaPaginationTest(unittest.TestCase):
    def setUp(self):
        self.manager = XimalayaManager()

    def test_single_page_when_total_within_page_size(self):
        # 章节数 <= 单页 200：只请求第一页，不翻页
        first_page = [_chapter(i, i) for i in range(1, 201)]
        with mock.patch.object(
            XimalayaManager, "get_album_chapters_page", return_value=(first_page, 200)
        ) as mocked:
            chapters = self.manager.get_album_chapters("127295668", log_summary=False)
        self.assertEqual(len(chapters), 200)
        mocked.assert_called_once()

    def test_full_pagination_when_total_exceeds_page_size(self):
        # 351 集 > 单页 200：自动翻第二页并合并去重
        first_page = [_chapter(i, i) for i in range(1, 201)]
        second_page = [_chapter(i, i) for i in range(201, 352)]
        pages = {1: (first_page, 351), 2: (second_page, 351)}

        def fake_page(album_id, page=1, page_size=200, log_summary=True):
            return pages.get(page, ([], 351))

        with mock.patch.object(XimalayaManager, "get_album_chapters_page", side_effect=fake_page) as mocked:
            chapters = self.manager.get_album_chapters("127295668", log_summary=False)
        self.assertEqual(len(chapters), 351)
        self.assertEqual(chapters[0]["order_num"], 1)
        self.assertEqual(chapters[-1]["order_num"], 351)
        self.assertEqual(mocked.call_count, 2)

    def test_pagination_stops_on_empty_page(self):
        # 翻页遇到空页（API 抖动）不无限循环，返回已取到的章节
        first_page = [_chapter(i, i) for i in range(1, 201)]

        def fake_page(album_id, page=1, page_size=200, log_summary=True):
            if page == 1:
                return (first_page, 1000)
            return ([], 1000)

        with mock.patch.object(XimalayaManager, "get_album_chapters_page", side_effect=fake_page):
            chapters = self.manager.get_album_chapters("127295668", log_summary=False)
        self.assertGreaterEqual(len(chapters), 200)  # 保留第一页结果，不抛异常


if __name__ == "__main__":
    unittest.main()
