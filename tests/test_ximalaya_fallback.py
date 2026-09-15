# -*- coding: utf-8 -*-
"""喜马拉雅章节加载：并发路径失败时回退串行分页，避免「正在加载章节→暂无章节」。"""
import unittest
from unittest import mock

from core.ximalaya_manager import XimalayaManager


def _ch(tid, order):
    return {"id": str(tid), "track_id": str(tid), "title": f"第{order}集", "order_num": order}


class XimalayaFallbackTest(unittest.TestCase):
    def setUp(self):
        self.manager = XimalayaManager()

    def test_concurrent_empty_falls_back_to_serial(self):
        # 并发大页路径因 get_album_detail 拿不到总数(A)而返回空
        # （网页详情接口被风控/WFP 校验失败时 total_episodes=0）；
        # 必须回退到不依赖 detail 的串行分页，而不是返回[]导致「暂无章节」。
        first = [_ch(i, i) for i in range(1, 201)]
        second = [_ch(i, i) for i in range(201, 351)]
        with mock.patch.object(self.manager, "_fetch_chapters_concurrent", return_value=[]), \
             mock.patch.object(
                 self.manager, "get_album_chapters_page",
                 side_effect=lambda album_id, page=1, page_size=200, log_summary=True: (
                     (first, 350) if page == 1 else (second, 350)
                 ),
             ):
            chapters = self.manager.get_album_chapters("127295668", page=1, page_size=2000, log_summary=False)
        self.assertEqual(len(chapters), 350)
        self.assertEqual(chapters[0]["order_num"], 1)
        self.assertEqual(chapters[-1]["order_num"], 350)

    def test_concurrent_success_returns_directly(self):
        # 并发路径成功时直接用其结果，不额外分页
        concurrent_result = [_ch(i, i) for i in range(1, 3001)]
        with mock.patch.object(self.manager, "_fetch_chapters_concurrent", return_value=concurrent_result), \
             mock.patch.object(self.manager, "get_album_chapters_page") as page_mock:
            chapters = self.manager.get_album_chapters("127295668", page=1, page_size=2000, log_summary=False)
        self.assertEqual(len(chapters), 3000)
        page_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
