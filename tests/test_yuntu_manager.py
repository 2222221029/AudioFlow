#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""云听FM（radio.cn）接入单测。

覆盖 2026-10 逆向复现的两条通道（不发真实网络请求，全部 mock session）：

1. **App 通道搜索**（ms 网关裸路径 `/search/search/findSearchResourceList`）
   - 签名：`MD5(按key排序的 k=v 用&连接 + '&timestamp=' + 毫秒 + '&key=' + 模块盐).upper()`
   - 无参数：`MD5('timestamp=' + 毫秒 + '&key=' + 盐)`
   - 分页是 **0 基**（实测 pageNo>=1 恒空），专辑 tab 优先、综合 tab 兜底
   - `productId` 公共头决定服务端数据域，缺失时 totalNum 恒 0
2. **/web 通道专辑详情**（单参数 GET 走「路径后缀 + 无参签名」）
   旧实现用 `?id=<albumId>` + 参数签名 → 网关收下但 `data` 恒 null。

字段映射依据真实响应（逆向案例 work/yunting-search/evidence/E-014.md）：
`contentId/albumId`→id、`title`→title、`image`→cover、`childCount`→episodes、
`listenCount`→plays、`endFlag=1`→已完结、`subtitle`→description。
"""

import hashlib
import unittest
from unittest import mock

import requests

from core.yuntu_manager import YunTuManager

WEB_KEY = "f0fc4c668392f9f9a447e48584c214ee"
APP_SALT = "68e251be8b49f462be367df22b212ee3"


def md5_upper(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest().upper()


def response(payload, status_code=200):
    resp = mock.Mock(status_code=status_code)
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    return resp


def album_item(content_id="16341989605390", title="先生鲁迅", **overrides):
    item = {
        "contentId": content_id,
        "title": title,
        "subtitle": "以纪实性语言描述鲁迅的一生",
        "image": "https://ytmedia.radio.cn/CCYT%2F202110%2F18%2Fcover.jpg",
        "contentType": 1,
        "publishTime": 1634198960000,
        "listenCount": 85163,
        "childCount": 8,
        "endFlag": 1,
        "feeType": 0,
        "vipFlag": 0,
    }
    item.update(overrides)
    return item


def single_item(content_id="1203408", title="三国演义 第三回", album_id="15682083075740"):
    return {
        "contentId": content_id,
        "title": title,
        "contentType": 3,
        "albumId": album_id,
        "parentId": album_id,
        "image": "https://ytmedia.radio.cn/single-cover.jpg",
        "listenCount": 493902,
        "childCount": 0,
        "playUrlHigh": "https://ytmedia.radio.cn/high.mp3",
        "playUrlLow": "https://ytmedia.radio.cn/low.mp3",
    }


def search_payload(items, total=None, page_no=0):
    return {
        "code": 0,
        "message": "SUCCESS",
        "data": {
            "pageNo": page_no,
            "pageSize": 100,
            "totalNum": total if total is not None else len(items),
            "totalPage": 1,
            "data": items,
        },
    }


class YunTuSearchTest(unittest.TestCase):
    """App 通道关键词搜索。"""

    def manager(self):
        manager = YunTuManager.__new__(YunTuManager)
        manager.base_url = "https://ytmsout.radio.cn"
        manager.secret_key = WEB_KEY
        manager.session = mock.Mock()
        return manager

    def test_app_sign_uses_sorted_params_and_module_salt(self):
        manager = self.manager()
        params = {"keyWord": "神雕侠侣", "searchTabId": "2", "contentType": "1",
                  "pageNo": "0", "sortType": "0"}
        sign = manager._app_sign(params, "1700000000000")
        expected = md5_upper(
            "contentType=1&keyWord=神雕侠侣&pageNo=0&searchTabId=2&sortType=0"
            f"&timestamp=1700000000000&key={APP_SALT}"
        )
        self.assertEqual(sign, expected)

    def test_app_sign_without_params_uses_timestamp_only(self):
        manager = self.manager()
        self.assertEqual(
            manager._app_sign({}, "1700000000000"),
            md5_upper(f"timestamp=1700000000000&key={APP_SALT}"),
        )

    def test_request_carries_data_domain_headers_and_unique_timestamp(self):
        """productId 缺失 → totalNum 恒 0；timestamp 重复 → 网关 1051 拦截。"""
        manager = self.manager()
        manager.session.get.return_value = response(search_payload([album_item()]))
        with mock.patch("core.yuntu_manager.get_timestamp_ms_str", return_value="1700000000000"):
            manager._search_page("先生鲁迅", "2", "1", 0)

        kwargs = manager.session.get.call_args.kwargs
        headers = kwargs["headers"]
        self.assertEqual(headers["productId"], YunTuManager.APP_PRODUCT_ID)
        self.assertEqual(headers["platformCode"], "XIAOMI")
        self.assertEqual(headers["versionId"], YunTuManager.APP_VERSION_ID)
        self.assertTrue(headers["equipmentId"])
        self.assertEqual(headers["timestamp"], "1700000000000")
        self.assertEqual(kwargs["params"], {
            "keyWord": "先生鲁迅", "searchTabId": "2", "contentType": "1",
            "pageNo": "0", "sortType": "0",
        })

    def test_search_books_prefers_album_tab_and_normalizes_fields(self):
        manager = self.manager()
        manager.session.get.return_value = response(search_payload([
            album_item(),
            album_item(content_id="17561705445550", title="呼兰河传｜萧红经典散文",
                       childCount=36, endFlag=0, listenCount=219242),
        ]))

        books = manager.search_books("鲁迅", page=0, page_size=20)

        self.assertEqual(len(books), 2)
        self.assertEqual(books[0]["id"], "16341989605390")
        self.assertEqual(books[0]["title"], "先生鲁迅")
        self.assertEqual(books[0]["cover"], "https://ytmedia.radio.cn/CCYT%2F202110%2F18%2Fcover.jpg")
        self.assertEqual(books[0]["episodes"], 8)
        self.assertEqual(books[0]["plays"], 85163)
        self.assertEqual(books[0]["status"], "已完结")
        self.assertEqual(books[1]["status"], "连载中")
        self.assertEqual(books[0]["description"], "以纪实性语言描述鲁迅的一生")
        self.assertEqual(books[0]["platform"], "云听FM")
        # 请求走的是专辑 tab
        self.assertEqual(manager.session.get.call_args.kwargs["params"]["searchTabId"], "2")

    def test_search_books_pages_are_zero_based(self):
        manager = self.manager()
        manager.session.get.return_value = response(search_payload([album_item()]))

        manager.search_books("鲁迅", page=2, page_size=20)

        self.assertEqual(manager.session.get.call_args.kwargs["params"]["pageNo"], "2")

    def test_non_album_content_types_are_dropped(self):
        """综合/专辑 tab 会混入单集(3)、资讯(161)等类型，不能当成专辑返回。"""
        manager = self.manager()
        manager.session.get.return_value = response(search_payload([
            album_item(),
            single_item(),
            {"contentId": "1979", "title": "东盟快讯", "contentType": 161},
            {"contentId": "1980", "title": "直播频道", "contentType": 177},
        ]))

        books = manager.search_books("鲁迅", page_size=20)

        self.assertEqual([book["id"] for book in books], ["16341989605390"])

    def test_album_tab_empty_falls_back_to_general_tab(self):
        """实测：专辑 tab 可能 totalNum>0 而列表为空（如「三国演义」），必须回落。"""
        manager = self.manager()
        calls = []

        def route(url, params=None, headers=None, timeout=None, **_kwargs):
            calls.append((url, dict(params or {})))
            tab = str((params or {}).get("searchTabId"))
            if tab == "2":
                return response(search_payload([], total=79))
            return response(search_payload([album_item(content_id="15682083075740", title="三国演义")]))

        manager.session.get.side_effect = lambda url, **kwargs: route(url, **kwargs)

        books = manager.search_books("三国演义", page_size=20)

        self.assertEqual([book["id"] for book in books], ["15682083075740"])
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1][1]["searchTabId"], "")
        self.assertEqual(calls[1][1]["contentType"], "0")

    def test_general_tab_singles_are_grouped_into_albums(self):
        """综合 tab 把有声书按单集返回时，按 albumId 聚合并补专辑详情。"""
        manager = self.manager()

        def route(url, params=None, headers=None, timeout=None, **_kwargs):
            if "/search/search/findSearchResourceList" not in url:
                return response({"code": 0, "message": "SUCCESS", "data": {
                    "id": "15682083075740", "name": "三国演义", "image": "https://ytmedia.radio.cn/a.jpg",
                    "childCount": 200, "endFlag": 1, "listenCount": 7000000,
                    "ownerNickName": "央广播音", "des": "四大名著", "desSimple": "四大名著",
                }})
            tab = str((params or {}).get("searchTabId"))
            if tab == "2":
                return response(search_payload([], total=0))
            return response(search_payload([
                single_item(content_id="1203408", album_id="15682083075740"),
                single_item(content_id="1203417", album_id="15682083075740"),
                single_item(content_id="1203418", album_id="15682083075740"),
                single_item(content_id="999", album_id="22222"),
            ]))

        manager.session.get.side_effect = lambda url, **kwargs: route(url, **kwargs)

        books = manager.search_books("三国演义", page_size=20)

        self.assertEqual([book["id"] for book in books], ["15682083075740", "22222"])
        self.assertEqual(books[0]["title"], "三国演义")
        self.assertEqual(books[0]["author"], "央广播音")
        self.assertEqual(books[0]["episodes"], 200)
        self.assertEqual(books[0]["status"], "已完结")
        self.assertEqual(books[0]["platform"], "云听FM")

    def test_gateway_error_degrades_to_empty_result(self):
        manager = self.manager()
        manager.session.get.return_value = response(
            {"code": 1001, "message": "参数不合法", "data": None}
        )

        self.assertEqual(manager.search_books("三国演义"), [])

    def test_link_or_id_search_bypasses_keyword_api(self):
        manager = self.manager()
        manager.get_album_info = mock.Mock(return_value={"id": "16100096126610", "title": "专辑"})

        books = manager.search_books("16100096126610")

        self.assertEqual(books[0]["id"], "16100096126610")
        manager.session.get.assert_not_called()

    def test_result_limit_is_respected(self):
        manager = self.manager()
        manager.session.get.return_value = response(search_payload([
            album_item(content_id=str(index), title=f"专辑{index}") for index in range(100)
        ]))

        books = manager.search_books("鲁迅", page_size=40)

        self.assertEqual(len(books), 40)


class YunTuWebChannelTest(unittest.TestCase):
    """/web 通道专辑详情（路径后缀 + 无参数签名）。"""

    def manager(self):
        manager = YunTuManager.__new__(YunTuManager)
        manager.base_url = "https://ytmsout.radio.cn"
        manager.secret_key = WEB_KEY
        manager.session = mock.Mock()
        return manager

    def test_album_detail_uses_path_suffix_and_unsigned_query(self):
        manager = self.manager()
        manager.session.get.return_value = response({"code": 0, "message": "SUCCESS", "data": {
            "id": "16606145480290", "name": "神雕侠侣", "image": "https://ytmedia.radio.cn/c.jpg",
            "childCount": 348, "endFlag": 1, "listenCount": 1200000,
            "anchorList": [{"nickName": "周小平"}], "des": "金庸武侠", "desSimple": "金庸武侠",
        }})

        with mock.patch("core.yuntu_manager.get_timestamp_ms_str", return_value="1700000000000"):
            detail = manager.get_album_detail("16606145480290")

        args, kwargs = manager.session.get.call_args
        self.assertEqual(args[0], "https://ytmsout.radio.cn/web/appAlbum/detail/16606145480290")
        self.assertNotIn("params", kwargs)
        expected = md5_upper(f"timestamp=1700000000000&key={WEB_KEY}")
        self.assertEqual(kwargs["headers"]["sign"], expected)
        self.assertEqual(kwargs["headers"]["platformCode"], "WEB")
        # 统一字段 + 旧键兼容
        self.assertEqual(detail["id"], "16606145480290")
        self.assertEqual(detail["title"], "神雕侠侣")
        self.assertEqual(detail["author"], "周小平")
        self.assertEqual(detail["episodes"], 348)
        self.assertEqual(detail["status"], "已完结")
        self.assertEqual(detail["albumTitle"], "神雕侠侣")
        self.assertEqual(detail["albumCover"], "https://ytmedia.radio.cn/c.jpg")
        self.assertEqual(detail["total"], 348)

    def test_album_detail_prefers_owner_over_anchor_list(self):
        manager = self.manager()
        manager.session.get.return_value = response({"code": 0, "message": "SUCCESS", "data": {
            "id": "1", "name": "先生鲁迅", "ownerNickName": "央广之声",
            "anchorList": [{"nickName": "某主播"}], "childCount": 8, "endFlag": 1,
        }})

        detail = manager.get_album_detail("1")

        self.assertEqual(detail["author"], "央广之声")

    def test_empty_data_keeps_legacy_fallback(self):
        manager = self.manager()
        manager.session.get.return_value = response({"code": 0, "message": "SUCCESS", "data": None})
        manager._try_get_cover_from_image_api = mock.Mock(return_value=None)

        self.assertIsNone(manager.get_album_detail("123"))
        manager._try_get_cover_from_image_api.assert_called_once_with("123")


class YunTuAggregationTest(unittest.TestCase):
    """云听FM 已纳入聚合搜索（KEYWORD_SEARCH_PLATFORMS）。"""

    def test_yuntu_is_included_in_keyword_and_id_platforms(self):
        from core.enhanced_search_manager import EnhancedSearchManager

        self.assertIn("云听FM", EnhancedSearchManager.KEYWORD_SEARCH_PLATFORMS)
        self.assertEqual(EnhancedSearchManager.SEARCH_RESULT_LIMITS["云听FM"], 40)

    def test_platform_dispatch_passes_limit_and_enriches_details(self):
        from core.enhanced_search_manager import EnhancedSearchManager

        manager = EnhancedSearchManager.__new__(EnhancedSearchManager)
        manager._keyword_search_cache = {}
        manager._keyword_search_cache_lock = __import__("threading").Lock()
        manager.yuntu_manager = mock.Mock()
        manager.yuntu_manager.search_books.return_value = [
            {"id": "1", "title": "三国演义", "platform": "云听FM"}
        ]
        manager._enrich_search_result_details = mock.Mock()

        books = manager._search_platform_impl("三国演义", "云听FM")

        manager.yuntu_manager.search_books.assert_called_once_with(
            "三国演义", page=0, page_size=EnhancedSearchManager.SEARCH_RESULT_LIMITS["云听FM"]
        )
        manager._enrich_search_result_details.assert_called_once()
        self.assertEqual(books[0]["platform"], "云听FM")

    def test_detail_enrichment_fills_author_episodes_and_plays(self):
        """搜索条目缺主播/章节数/播放量时，用专辑详情小批量补全（与其他平台同机制）。"""
        from core.enhanced_search_manager import EnhancedSearchManager

        manager = EnhancedSearchManager.__new__(EnhancedSearchManager)
        manager.yuntu_manager = mock.Mock()
        manager.yuntu_manager.get_album_detail.return_value = {
            "id": "16606145480290", "title": "神雕侠侣丨多人有声剧", "author": "云听有声书",
            "cover": "https://ytmedia.radio.cn/c.jpg", "episodes": 348, "plays": 1200000,
            "status": "连载中", "description": "金庸原著有声剧",
        }
        books = [{
            "id": "16606145480290", "title": "神雕侠侣丨多人有声剧", "platform": "云听FM",
            "cover": "https://ytmedia.radio.cn/c.jpg", "episodes": 0, "plays": 0,
        }]

        manager._enrich_search_result_details(books, "云听FM", limit=8)

        self.assertEqual(books[0]["author"], "云听有声书")
        self.assertEqual(books[0]["episodes"], 348)
        self.assertEqual(books[0]["plays"], 1200000)
        self.assertEqual(books[0]["status"], "连载中")

    def test_link_or_id_input_does_not_hit_keyword_api(self):
        from core.enhanced_search_manager import EnhancedSearchManager

        manager = EnhancedSearchManager.__new__(EnhancedSearchManager)
        manager._keyword_search_cache = {}
        manager._keyword_search_cache_lock = __import__("threading").Lock()
        manager.yuntu_manager = mock.Mock()
        manager.yuntu_manager.search_by_link_or_id.return_value = {
            "id": "16100096126610", "title": "专辑", "platform": "云听FM"
        }

        books = manager._search_platform_impl("https://ytweb.radio.cn/share/albumDetail?columnId=16100096126610", "云听FM")

        manager.yuntu_manager.search_books.assert_not_called()
        self.assertEqual(books[0]["id"], "16100096126610")


class YunTuMediaOriginFallbackTest(unittest.TestCase):
    """音频 CDN 403 风控 → 源站 OSS 兜底（2026-10-09 修复）。

    现象：倚天屠龙记（592 集）批量下载 100+ 集 403 Forbidden（ytmedia.radio.cn）。
    根因：CDN（openresty 网关）对单出口 IP 批量拉流阈值封禁，触发后所有
    URL 恒 403，与 UA/Referer/Cookie 无关；对象在阿里云 OSS 源站完好
    （同 key 匿名直连 200，支持 Range，字节数一致）。
    """

    CDN_URL = "https://ytmedia.radio.cn/file/202408/06/09/44895954045ee0c509c4a26b9728483b30e8bc5h.mp3"
    ORIGIN_URL = (
        "https://yunting-bj-radio-client.oss-cn-beijing.aliyuncs.com"
        "/file/202408/06/09/44895954045ee0c509c4a26b9728483b30e8bc5h.mp3"
    )

    def setUp(self):
        # 类级冷却状态是共享的，逐用例复位
        YunTuManager._cdn_403_streak = 0
        YunTuManager._cdn_403_until = 0.0

    def tearDown(self):
        YunTuManager._cdn_403_streak = 0
        YunTuManager._cdn_403_until = 0.0

    def test_resolve_media_url_keeps_path_verbatim_and_swaps_host(self):
        # 路径逐字节保留（含 %2F 编码，实测源站两种形态均可）
        ccyt = "https://ytmedia.radio.cn/CCYT%2F2022%2F08%2F18%2F16607872339bee19e9f74829c6e4668b62c724635ah.mp3"
        resolved = YunTuManager.resolve_media_url(ccyt, prefer="origin")
        self.assertTrue(resolved.startswith("https://yunting-bj-radio-client.oss-cn-beijing.aliyuncs.com/CCYT%2F"))
        self.assertIn("16607872339bee19e9f74829c6e4668b62c724635ah.mp3", resolved)

    def test_resolve_media_url_passthrough_non_cdn(self):
        url = "https://example.com/media/a.mp3"
        self.assertEqual(YunTuManager.resolve_media_url(url, prefer="origin"), url)

    def test_candidates_cdn_first_then_origin(self):
        candidates = YunTuManager._audio_candidate_urls(self.CDN_URL)
        self.assertEqual(candidates[0], self.CDN_URL)
        self.assertEqual(candidates[1], self.ORIGIN_URL)

    def test_candidates_origin_first_during_cooldown(self):
        with mock.patch.object(YunTuManager, "_cdn_cooldown_active", return_value=True):
            candidates = YunTuManager._audio_candidate_urls(self.CDN_URL)
        self.assertEqual(candidates[0], self.ORIGIN_URL)

    def test_mark_cdn_403_triggers_global_cooldown_after_threshold(self):
        for _ in range(YunTuManager.CDN_403_COOLDOWN_THRESHOLD):
            YunTuManager._mark_cdn_403()
        self.assertTrue(YunTuManager._cdn_cooldown_active())
        # 冷却中 resolve 默认走源站
        self.assertEqual(
            YunTuManager.resolve_media_url(self.CDN_URL),
            self.ORIGIN_URL,
        )

    def test_clear_cdn_403_resets_streak_and_cooldown(self):
        YunTuManager._mark_cdn_403()
        YunTuManager._clear_cdn_403()
        self.assertFalse(YunTuManager._cdn_cooldown_active())
        self.assertEqual(YunTuManager.resolve_media_url(self.CDN_URL), self.CDN_URL)

    def test_download_audio_falls_back_to_origin_on_cdn_403(self):
        """CDN 403 → 冷却计数 +1，并立刻换源站成功。"""
        manager = YunTuManager()
        http_error = requests.HTTPError("403 Client Error: Forbidden for url: ...")
        http_error.response = mock.Mock(status_code=403)

        def fake_session_to_file(*, url, save_path, **kwargs):
            if url.startswith("https://ytmedia.radio.cn/"):
                raise http_error
            self.assertEqual(url, self.ORIGIN_URL)
            with open(save_path, "wb") as fh:
                fh.write(b"ID3" + b"\x00" * 4096)
            return save_path

        with mock.patch("core.yuntu_manager.download_adapter.session_to_file", side_effect=fake_session_to_file):
            ok = manager.download_audio(self.CDN_URL, "/tmp/yt_fallback.mp3")

        self.assertTrue(ok)
        self.assertEqual(YunTuManager._cdn_403_streak, 1)
        self.assertEqual(manager.last_error, "")
        self.assertFalse(manager.last_error_type)

    def test_download_audio_marks_rate_limited_when_both_fail(self):
        """CDN 与源站都失败 → last_error_type=rate_limited 供上层冷却重试。"""
        manager = YunTuManager()
        cdn_error = requests.HTTPError("403 Client Error: Forbidden")
        cdn_error.response = mock.Mock(status_code=403)
        origin_error = requests.ConnectionError("read timed out")

        def fake_session_to_file(*, url, save_path, **kwargs):
            raise cdn_error if url.startswith("https://ytmedia.radio.cn/") else origin_error

        with mock.patch("core.yuntu_manager.download_adapter.session_to_file", side_effect=fake_session_to_file):
            ok = manager.download_audio(self.CDN_URL, "/tmp/yt_both_fail.mp3")

        self.assertFalse(ok)
        self.assertIn("rate_limited", manager.last_error_type)
        self.assertIn("云听FM下载失败", manager.last_error)

    def test_download_audio_restricted_when_both_sources_404(self):
        """两个候选都 404（对象真缺失）→ restricted，上层不再盲目重试。"""
        manager = YunTuManager()
        missing = requests.HTTPError("404 Client Error: Not Found")
        missing.response = mock.Mock(status_code=404)

        with mock.patch("core.yuntu_manager.download_adapter.session_to_file", side_effect=missing):
            ok = manager.download_audio(self.CDN_URL, "/tmp/yt_missing.mp3")

        self.assertFalse(ok)
        self.assertEqual(manager.last_error_type, "restricted")
        # 没有 403 → 不该计入 CDN 风控冷却
        self.assertEqual(YunTuManager._cdn_403_streak, 0)

    def test_origin_404_while_cdn_blocked_stays_retryable(self):
        """CDN 正被风控时的源站 404 不能判死：对象可能在 CDN 的另一个 bucket。"""
        manager = YunTuManager()
        blocked = requests.HTTPError("403 Client Error: Forbidden")
        blocked.response = mock.Mock(status_code=403)
        missing = requests.HTTPError("404 Client Error: Not Found")
        missing.response = mock.Mock(status_code=404)

        def fake_session_to_file(*, url, save_path, **kwargs):
            raise blocked if url.startswith("https://ytmedia.radio.cn/") else missing

        with mock.patch("core.yuntu_manager.download_adapter.session_to_file", side_effect=fake_session_to_file):
            ok = manager.download_audio(self.CDN_URL, "/tmp/yt_ambig.mp3")

        self.assertFalse(ok)
        self.assertEqual(manager.last_error_type, "rate_limited")

    def test_download_audio_empty_url_short_circuits(self):
        manager = YunTuManager()
        with mock.patch("core.yuntu_manager.download_adapter.session_to_file") as session_to_file:
            ok = manager.download_audio("", "/tmp/yt_empty.mp3")
        self.assertFalse(ok)
        session_to_file.assert_not_called()

    def test_download_audio_uses_segmented_adapter(self):
        """与其它平台一致：走 download_adapter 分段并行下载通道。"""
        manager = YunTuManager()

        def fake_session_to_file(*, url, save_path, **kwargs):
            self.assertTrue(kwargs.get("allow_segmented"))
            with open(save_path, "wb") as fh:
                fh.write(b"ID3" + b"\x00" * 4096)
            return save_path

        with mock.patch("core.yuntu_manager.download_adapter.session_to_file", side_effect=fake_session_to_file) as stf:
            ok = manager.download_audio(self.CDN_URL, "/tmp/yt_adapter.mp3")
        self.assertTrue(ok)
        stf.assert_called_once()


class YunTuWorkerWiringTest(unittest.TestCase):
    """download_worker 云听分支接线（不再裸 requests 单流直拉 CDN）。"""

    def _worker(self):
        from core.download_worker import DownloadWorker

        worker = DownloadWorker.__new__(DownloadWorker)
        worker.platform = "云听FM"
        worker.task_id = "web-test"
        worker.album_id = "17097974899240"
        worker.quality = "标准"
        worker.voice_config = None
        worker.download_dir = "/tmp"
        worker._dbg = lambda *a, **k: None
        return worker

    def test_worker_dispatches_to_manager_download_audio(self):
        import inspect

        source = inspect.getsource(type(self._worker())._download_single_chapter)
        self.assertIn("download_manager.download_audio", source)
        # 旧的裸 requests 直拉分支已移除
        self.assertNotIn("_requests.get(audio_url", source)


if __name__ == "__main__":
    unittest.main()
