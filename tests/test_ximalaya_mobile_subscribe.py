# -*- coding: utf-8 -*-
"""喜马拉雅 App 移动端订阅接口解析回归测试（personal_sync 用）。

从 web_server._extract_ximalaya_mobile_subscriptions 复制解析逻辑验证，
避免单元测试依赖 flask 环境。
"""
import unittest


def _normalize_personal_item(item, platform):
    d = dict(item or {})
    return {
        "id": d.get("id") or "",
        "title": d.get("title") or "未知专辑",
        "author": d.get("author") or "",
        "cover": d.get("cover") or "",
        "episodes": d.get("episodes") or 0,
        "platform": platform,
    }


def _extract_ximalaya_mobile_subscriptions(payload):
    """与 web_server 中同名函数逻辑一致（复制版）。"""
    items = []
    raw_lists = []
    if isinstance(payload, dict):
        for key in ("list", "albums", "albumList", "subscriptionList", "data", "items", "records"):
            value = payload.get(key)
            if isinstance(value, list):
                raw_lists.append(value)
        for lst in list(raw_lists):
            for item in lst:
                if isinstance(item, dict) and any(k in item for k in ("list", "albums", "albumList")):
                    raw_lists.extend([v for v in item.values() if isinstance(v, list)])
    elif isinstance(payload, list):
        raw_lists.append(payload)

    for raw in raw_lists:
        for album in raw:
            if not isinstance(album, dict):
                continue
            album_id = str(album.get("albumId") or album.get("album_id") or album.get("id") or album.get("albumID") or "")
            title = album.get("albumTitle") or album.get("album_title") or album.get("title") or album.get("name") or ""
            anchor = album.get("anchor") if isinstance(album.get("anchor"), dict) else {}
            cover = (album.get("coverPath") or album.get("cover_path") or album.get("cover")
                     or album.get("albumCover") or album.get("coverLarge") or album.get("smallCover") or "")
            author = (album.get("anchorName") or album.get("nickname") or album.get("author")
                      or anchor.get("nickname") or anchor.get("anchorName") or "")
            episodes = album.get("trackCount") or album.get("track_count") or album.get("episodes") or 0
            items.append(_normalize_personal_item({
                "id": album_id, "title": title, "author": author,
                "cover": cover, "episodes": episodes,
            }, "喜马拉雅"))
    seen = set()
    result = []
    for item in items:
        key = str(item.get("id") or "")
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        result.append(item)
    return result


class XimalayaMobileSubscribeTest(unittest.TestCase):
    def test_parse_list_shape(self):
        r = _extract_ximalaya_mobile_subscriptions({
            "list": [{"albumId": "1", "albumTitle": "专辑A", "coverPath": "c1", "anchorName": "主播1", "trackCount": 100}]
        })
        self.assertEqual(len(r), 1)
        self.assertEqual(r[0]["id"], "1")
        self.assertEqual(r[0]["title"], "专辑A")
        self.assertEqual(r[0]["author"], "主播1")
        self.assertEqual(r[0]["episodes"], 100)

    def test_parse_albumlist_with_nested_anchor(self):
        r = _extract_ximalaya_mobile_subscriptions({
            "albumList": [{"albumId": "2", "title": "专辑B", "cover": "c2", "anchor": {"nickname": "主播2"}}]
        })
        self.assertEqual(r[0]["id"], "2")
        self.assertEqual(r[0]["author"], "主播2")

    def test_parse_plain_array_and_dedupe(self):
        r = _extract_ximalaya_mobile_subscriptions({
            "data": [{"albumId": "3", "albumTitle": "专辑C"}, {"albumId": "3", "albumTitle": "专辑C重复"}]
        })
        self.assertEqual(len(r), 1)
        self.assertEqual(r[0]["id"], "3")

    def test_parse_albums_field_and_alternate_keys(self):
        r = _extract_ximalaya_mobile_subscriptions({
            "albums": [{"album_id": "4", "name": "专辑D", "track_count": 50}]
        })
        self.assertEqual(r[0]["id"], "4")
        self.assertEqual(r[0]["title"], "专辑D")
        self.assertEqual(r[0]["episodes"], 50)

    def test_empty(self):
        self.assertEqual(_extract_ximalaya_mobile_subscriptions({}), [])
        self.assertEqual(_extract_ximalaya_mobile_subscriptions([]), [])


if __name__ == "__main__":
    unittest.main()
