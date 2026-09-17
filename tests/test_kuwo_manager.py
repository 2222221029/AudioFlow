import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core.kuwo_manager import KuwoManager


class FakeResponse:
    def __init__(self, status_code=200, payload=None, body=b"", headers=None):
        self.status_code = status_code
        self._payload = payload
        self._body = body
        self.headers = headers or {}

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload

    def iter_content(self, chunk_size=262144):
        del chunk_size
        yield self._body


class KuwoManagerTest(unittest.TestCase):
    def setUp(self):
        KuwoManager._download_info_cache.clear()

    def test_transient_download_info_failure_is_not_negatively_cached(self):
        manager = KuwoManager()
        failed = FakeResponse(status_code=503)
        success = FakeResponse(payload={
            "code": 200,
            "data": {"url": "http://audio.example/track.mp3", "format": "mp3", "bitrate": 128},
        })

        with mock.patch.object(manager.session, "get", side_effect=[failed, success]) as get:
            self.assertIsNone(manager._get_download_url_internal("rid-1", "mp3", 128))
            info = manager._get_download_url_internal("rid-1", "mp3", 128)

        self.assertEqual(get.call_count, 2)
        self.assertEqual(info["url"], "http://audio.example/track.mp3")

    def test_media_http_error_is_exposed_and_partial_file_is_removed(self):
        manager = KuwoManager()
        response = FakeResponse(status_code=403)
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "track.mp3"
            with mock.patch.object(manager.session, "get", return_value=response):
                self.assertFalse(manager.download_audio("http://audio.example/expired", str(target)))
            self.assertFalse(target.exists())
            self.assertFalse(Path(str(target) + ".part").exists())

        self.assertIn("HTTP 403", manager.last_error)

    def test_paid_media_403_is_reported_as_restricted(self):
        manager = KuwoManager()
        denied = FakeResponse(status_code=403)
        paid = FakeResponse(payload={"code": -1, "msg": "该歌曲为付费内容，请下载酷我音乐客户端后付费收听"})

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "track.mp3"
            with mock.patch.object(manager.session, "get", side_effect=[denied, paid]):
                self.assertFalse(
                    manager.download_audio(
                        "http://audio.example/paid",
                        str(target),
                        chapter_id="rid-paid",
                    )
                )

        self.assertEqual(manager.last_error_type, "restricted")
        self.assertIn("付费内容", manager.last_error)

    def test_successful_media_download_is_atomically_promoted(self):
        manager = KuwoManager()
        body = b"ID3" + b"audio" * 3000
        response = FakeResponse(
            body=body,
            headers={"Content-Type": "audio/mpeg", "Content-Length": str(len(body))},
        )
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "track.mp3"
            with mock.patch.object(manager.session, "get", return_value=response):
                self.assertTrue(manager.download_audio("http://audio.example/track.mp3", str(target)))
            self.assertEqual(target.read_bytes(), body)
            self.assertFalse(Path(str(target) + ".part").exists())
            self.assertEqual(manager.last_error, "")


class KuwoPaginationIntegrityTest(unittest.TestCase):
    """酷我 albumInfo 在并发分页下会偶发把相邻页的响应返给当前页请求。

    错位响应的 success 仍为 True，只重试失败页发现不了；一次错位就是一整页 24 集：
    错位页的 24 个位置拿到别页的 rid（重复下载 24 集），真实的那 24 集永久缺失。
    下面覆盖「检出、修复、不误报」三类行为。
    """

    @staticmethod
    def _page(page_num, names, rids, total=48):
        return {
            "page": page_num,
            "total": total,
            "success": True,
            "music_list": [
                {"rid": rid, "name": name, "duration": 120}
                for rid, name in zip(rids, names)
            ],
        }

    def setUp(self):
        KuwoManager._download_info_cache.clear()

    def test_chapter_number_is_extracted_from_common_title_shapes(self):
        self.assertEqual(KuwoManager._chapter_number_from_name("第433集 将才（一）"), 433)
        self.assertEqual(KuwoManager._chapter_number_from_name("北齐怪谈-0433-将才（一）"), 433)
        self.assertIsNone(KuwoManager._chapter_number_from_name("【听友群：SMT20216688】"))

    def test_cross_page_rid_duplication_marks_involved_pages(self):
        # 第 2 页实际拿到第 1 页的数据：两页 rid 完全重复
        page1 = self._page(1, [f"第{i}集" for i in range(1, 25)], [f"r{i}" for i in range(1, 25)])
        page2 = self._page(2, [f"第{i}集" for i in range(1, 25)], [f"r{i}" for i in range(1, 25)])
        manager = KuwoManager()
        self.assertEqual(manager._find_misaligned_pages({1: page1, 2: page2}, [1, 2]), [1, 2])

    def test_split_episode_is_not_a_false_positive(self):
        # 一集拆成多段（-001/-002，且 1837 缺号）是正常结构，不能误报并触发无谓重抓
        page = self._page(77, [
            "北齐怪谈-1835-祖相（一）",
            "北齐怪谈-1836-祖相（二）-001",
            "北齐怪谈-1836-祖相（二）-002",
            "北齐怪谈-1838-登基！！（一）",
        ], ["r1835", "r1836a", "r1836b", "r1838"])
        manager = KuwoManager()
        self.assertEqual(manager._find_misaligned_pages({77: page}, [77]), [])

    def test_duplicate_names_in_one_page_are_flagged(self):
        page = self._page(5, ["第97集 甲", "第97集 甲"], ["r97", "r98"])
        manager = KuwoManager()
        self.assertEqual(manager._find_misaligned_pages({5: page}, [5]), [5])

    def test_get_chapters_repairs_misaligned_page_and_keeps_rids_unique(self):
        manager = KuwoManager()
        manager._page_request_interval = 0
        good1 = self._page(1, [f"第{i}集" for i in range(1, 25)], [f"r{i}" for i in range(1, 25)])
        good2 = self._page(2, [f"第{i}集" for i in range(25, 49)], [f"r{i}" for i in range(25, 49)])
        wrong2 = self._page(2, [f"第{i}集" for i in range(1, 25)], [f"r{i}" for i in range(1, 25)])

        state = {"page2_calls": 0}

        def fake_fetch(album_id, page_num, session=None):
            del album_id, session
            if page_num == 1:
                return good1
            state["page2_calls"] += 1
            # 第一次返回错位数据，串行重抓时返回正确数据
            return wrong2 if state["page2_calls"] == 1 else good2

        with mock.patch.object(manager, "_fetch_single_page", side_effect=fake_fetch):
            chapters = manager.get_chapters("album-x", page=1, page_size=1000)

        self.assertEqual(len(chapters), 48)
        rids = [chapter["id"] for chapter in chapters]
        self.assertEqual(len(rids), len(set(rids)), "同一 rid 不允许出现两次")
        self.assertEqual(chapters[24]["order_num"], 25)
        self.assertEqual(chapters[24]["title"], "第25集")
        self.assertGreaterEqual(state["page2_calls"], 2, "错位页必须被重抓")

    def test_duplicate_rid_is_dropped_when_repair_cannot_fix_it(self):
        manager = KuwoManager()
        manager._page_request_interval = 0
        manager._page_concurrency = 1
        good1 = self._page(1, [f"第{i}集" for i in range(1, 25)], [f"r{i}" for i in range(1, 25)])
        # 第 2 页无论重抓多少次都返回第 1 页的数据（模拟服务端持续错配）
        wrong2 = self._page(2, [f"第{i}集" for i in range(1, 25)], [f"r{i}" for i in range(1, 25)])

        def fake_fetch(album_id, page_num, session=None):
            del album_id, session
            return good1 if page_num == 1 else wrong2

        with mock.patch.object(manager, "_fetch_single_page", side_effect=fake_fetch):
            chapters = manager.get_chapters("album-x", page=1, page_size=1000)

        # 宁可少下这 24 集（订阅侧能重新判定为缺失），也不能重复下载成一堆重复文件
        self.assertEqual(len(chapters), 24)
        rids = [chapter["id"] for chapter in chapters]
        self.assertEqual(len(rids), len(set(rids)))


if __name__ == "__main__":
    unittest.main()
