"""酷我听书「请求形状」的回归测试（分页上限与容器参数）。

## 背景（来自参考实现的逆向实测定界）

`接口/酷我听书/work/re/API_INVENTORY.md` 与 `kuwo_dl/config.py` 给出的实测结论：

1. **分页 `rn` 上限是 100**：传 500/1000 也只返回 100 条，传 6127 直接 504。
   修复前 `KuwoManager._page_size = 24`，而 `enhanced_search_manager` 会用
   `page_size=10000` 拉整本目录 → `10000 / 24 ≈ 417` 个 API 页，
   参考实现同场景只需约 100 页。

2. **`format` 参数决定容器**：
   ```
   br=2000kflac              -> ogg/100k      br=2000kflac&format=mp3 -> mp3/128k
   br=128kmp3                -> aac/100k      br=128kmp3&format=mp3   -> mp3/128k
   ```
   不钉 `format` 时会掉到 ogg/aac 容器。修复前 URL 里**完全没有 format 参数**，
   于是 `320kmp3` 档返回 aac，调用方判定 `format == 'mp3'` 失败后又去试下一个
   比特率，把「每集 1 次请求」放大成最多 3~4 次。

3. **FLAC 档不能钉 `format=mp3`**：参考实现的档位链写作
   `("2000kflac", None, "flac")` —— 该档的 format 必须是 `None`，
   否则即使该集存在 FLAC 音源也只会拿到 mp3。本文件用
   `test_flac_probe_does_not_pin_container` 锁死这条边界，防止"顺手全都加 format"。

4. `192kmp3` **不在** APK 逆向出的档位表内
   （`48kaac 128k 128kaac 128kmp3 320kmp3 320mp3 2000kflac 4000kflac 10000kflac
   20000kflac 20201kmflac 20900kmflac 22000kmgg 23000kflac`），请求它是无效档位。
"""

from __future__ import annotations

import unittest
from unittest import mock

from core.kuwo_manager import KuwoManager

# 参考实现实测的 albumInfo 分页上限
DOCUMENTED_PAGE_SIZE_MAX = 100


class FakeResponse:
    def __init__(self, payload=None, status_code=200):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


class KuwoRequestShapeTest(unittest.TestCase):
    def setUp(self):
        KuwoManager._download_info_cache.clear()

    # --- 分页 ---------------------------------------------------------------
    def test_page_size_respects_documented_api_maximum(self):
        manager = KuwoManager()
        self.assertLessEqual(
            manager._page_size,
            DOCUMENTED_PAGE_SIZE_MAX,
            "albumInfo 的 rn 上限是 100，超过会被忽略（传 6127 直接 504）",
        )

    def test_page_size_is_large_enough_to_avoid_request_explosion(self):
        """整本抓取的页数不应因 rn 过小而爆炸。"""
        manager = KuwoManager()
        # enhanced_search_manager 会以 page_size=10000 请求整本目录
        self.assertLessEqual(
            -(-10000 // manager._page_size),  # 向上取整
            200,
            "10000 集目录的 API 页数应控制在 200 页以内",
        )

    # --- 容器参数 -----------------------------------------------------------
    def _media_url_for(self, preferred_format, bitrate=None):
        manager = KuwoManager()
        with mock.patch.object(
            manager.session,
            "get",
            return_value=FakeResponse({"code": 200, "data": {"url": "", "format": ""}}),
        ) as get:
            manager._get_download_url_internal("rid-1", preferred_format, bitrate)
        self.assertTrue(get.called, "应发出直链请求")
        return get.call_args[0][0]

    def test_mp3_request_pins_mp3_container(self):
        url = self._media_url_for("mp3", 320)
        self.assertIn(
            "format=mp3",
            url,
            "不钉 format 时 br=320kmp3 会返回 aac 容器，导致调用方反复降档重试",
        )

    def test_mp3_request_without_bitrate_pins_mp3_container(self):
        url = self._media_url_for("mp3")
        self.assertIn("format=mp3", url)

    def test_flac_probe_does_not_pin_container(self):
        """FLAC 档必须保持 format 未指定，否则拿不到无损。"""
        url = self._media_url_for("flac")
        self.assertNotIn(
            "format=mp3",
            url,
            "给 2000kflac 钉 format=mp3 会强制服务端返回 mp3，损失无损音源",
        )
        self.assertIn("br=2000kflac", url)

    def test_no_request_uses_bitrate_absent_from_apk_table(self):
        """192kmp3 不在逆向出的档位表内，不应出现在请求里。"""
        for bitrate in (320, 192, 128, None):
            url = self._media_url_for("mp3", bitrate)
            self.assertNotIn(
                "192kmp3",
                url,
                f"bitrate={bitrate} 时请求了不存在的 192kmp3 档位",
            )


if __name__ == "__main__":
    unittest.main()
