# -*- coding: utf-8 -*-
"""喜马拉雅搜索结果解析与排序回归测试。

1) 解析器需兼容 H5/App 多种响应嵌套（曾出现「返回 200 但解析到 0 个结果项」，
   从而退化到网页接口，导致顺序与官方 App 不一致）。
2) 单平台搜索结果必须保留平台原生顺序（与官方 App 一致），不做播放量重排。
"""
import unittest

from core.ximalaya_manager import XimalayaManager


def _album(i, title=None):
    return {"albumId": str(i), "albumTitle": title or f"专辑{i}", "playCount": i * 100, "trackCount": i}


class SearchParseShapeTest(unittest.TestCase):
    def setUp(self):
        self.m = XimalayaManager()

    def _extract(self, data):
        return XimalayaManager._extract_search_result_items(data)

    def test_h5_albumviews_shape(self):
        data = {"ret": 0, "data": {"albumViews": {"albums": [{"albumInfo": _album(1)}]}}}
        items = self._extract(data)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["albumInfo"]["albumId"], "1")

    def test_web_result_response_docs_shape(self):
        data = {"data": {"result": {"response": {"docs": [_album(2), _album(3)]}}}}
        items = self._extract(data)
        self.assertEqual([i["albumId"] for i in items], ["2", "3"])

    def test_deep_nested_shape_with_context(self):
        # 用户环境实测形态：顶层 ['ret','data','context']，专辑深埋其中
        data = {
            "ret": 0,
            "data": {"page": {"modules": [{"body": {"list": [_album(7), _album(8)]}}]}},
            "context": {"traceId": "x"},
        }
        items = self._extract(data)
        self.assertEqual([i["albumId"] for i in items], ["7", "8"])

    def test_deep_scan_ignores_non_album_lists(self):
        # 不应把主播/标签等非专辑数组当成搜索结果
        data = {"data": {"anchors": [{"nickname": "某主播"}], "tags": [{"name": "标签"}]}}
        self.assertEqual(self._extract(data), [])

    def test_normalize_unwraps_albuminfo(self):
        album = self.m._normalize_search_album({"albumInfo": _album(9, "庆余年")})
        self.assertEqual(album["id"], "9")
        self.assertEqual(album["title"], "庆余年")

    def test_looks_like_album(self):
        self.assertTrue(XimalayaManager._looks_like_album({"albumInfo": {}}))
        self.assertTrue(XimalayaManager._looks_like_album({"albumId": "1", "title": "t"}))
        self.assertFalse(XimalayaManager._looks_like_album({"nickname": "n"}))
        self.assertFalse(XimalayaManager._looks_like_album("str"))


class SinglePlatformOrderTest(unittest.TestCase):
    """单平台搜索保留原生顺序（Docker 环境可跑；本环境缺 Crypto 时跳过）。"""

    def test_single_platform_search_preserves_provider_order(self):
        try:
            from core.enhanced_search_manager import EnhancedSearchManager
        except Exception as exc:  # 本环境缺 pycryptodome
            self.skipTest(f"enhanced_search_manager 不可导入: {exc}")

        # 平台（App/官方接口）原生顺序：相关度高但播放量低者排前
        provider_order = [
            {"id": "a", "title": "低播放但相关", "plays": 1},
            {"id": "b", "title": "高播放热门", "plays": 10_000_000},
            {"id": "c", "title": "中等", "plays": 500},
        ]

        mgr = EnhancedSearchManager.__new__(EnhancedSearchManager)
        mgr.cookie_manager = None
        mgr._search_platform_cached = lambda kw, pf: [dict(i) for i in provider_order]
        results = mgr._search_books("庆余年", platform="喜马拉雅")
        # 必须与平台原生顺序一致，不被播放量重排
        self.assertEqual([r["id"] for r in results], ["a", "b", "c"])


if __name__ == "__main__":
    unittest.main()
