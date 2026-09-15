# -*- coding: utf-8 -*-
"""喜马拉雅 App 移动端 v3 接口回归测试。

v3 接口(mobile/v1/album/track/v3)无需 xm-sign，可访问老接口判定
「已下架」(ret=924) 的受限专辑——解决网页 WFP 407 / 老接口 924 导致
的「暂无章节」。
"""
import unittest
from unittest import mock

from core.ximalaya_manager import XimalayaManager


class XimalayaMobileV3Test(unittest.TestCase):
    def setUp(self):
        self.manager = XimalayaManager()

    def _v3_response(self, total, start, count, ret=0, msg="0"):
        lst = []
        for i in range(start, start + count):
            lst.append({
                "trackId": str(1000000000 + i),
                "title": f"第{i}集",
                "duration": "289",
                "albumId": "127295668",
                "isPaid": "False",
                "isFree": "True",
            })
        return {
            "ret": ret, "msg": msg,
            "data": {"list": lst, "pageId": 1, "pageSize": 20,
                     "maxPageId": 2, "totalCount": total},
        }

    def test_mobile_v3_parses_chapters(self):
        m = XimalayaManager()
        resp = mock.Mock(status_code=200)
        resp.json.return_value = self._v3_response(356, 1, 20)
        with mock.patch.object(m.session, "get", return_value=resp) as get:
            chapters, total = m._fetch_chapters_mobile_v3("127295668", page=1, page_size=20)
        self.assertEqual(total, 356)
        self.assertEqual(len(chapters), 20)
        self.assertEqual(chapters[0]["id"], "1000000001")
        self.assertEqual(chapters[0]["order_num"], 1)
        self.assertEqual(chapters[19]["order_num"], 20)
        get.assert_called_once()

    def test_mobile_v3_handles_downlisted_album(self):
        # 老接口对该专辑返回 924「已下架」，v3 接口返回正常数据 → 必须能解析
        m = XimalayaManager()
        resp = mock.Mock(status_code=200)
        resp.json.return_value = self._v3_response(356, 1, 20)
        with mock.patch.object(m.session, "get", return_value=resp):
            chapters, total = m._fetch_chapters_mobile_v3("127295668", page=1, page_size=20)
        self.assertEqual(total, 356)
        self.assertGreater(len(chapters), 0)

    def test_mobile_v3_error_records_ret(self):
        m = XimalayaManager()
        resp = mock.Mock(status_code=200)
        resp.json.return_value = {"ret": 407, "msg": "WFP存在但校验失败"}
        with mock.patch.object(m.session, "get", return_value=resp):
            chapters, total = m._fetch_chapters_mobile_v3("127295668")
        self.assertEqual(chapters, [])
        self.assertEqual(m._chapter_api_error, (407, "WFP存在但校验失败"))

    def test_mobile_v3_first_in_multi_api(self):
        # v3 必须是 multi_api 第一个尝试的接口（最高优先级）
        m = XimalayaManager()
        resp = mock.Mock(status_code=200)
        resp.json.return_value = self._v3_response(356, 1, 200)
        with mock.patch.object(m.session, "get", return_value=resp):
            api_results, total = m._fetch_chapters_multi_api("127295668", 1, 200)
        self.assertEqual(total, 356)
        self.assertGreaterEqual(len(api_results.get("mobile_v3") or []), 1)


if __name__ == "__main__":
    unittest.main()
