"""喜马拉雅音质档位与失败语义的回归测试。

## 背景（来自参考实现的实测定界）

`接口/喜马拉雅/喜马拉雅下载接口分析.md:65-83` 给出的旧版直连接口
（`mobile/redirect/free/play/{track_id}/{level}`）实测档位表：

    /0        → 无 CDN 音质标记，实测 ~24.8 kbps
    /1 /2     → …-aacv2-48K.m4a，实测 48.8 kbps
    /3 及以上 → …-aacv2-96K.m4a，实测 96.8 kbps（/3~/10 返回同一文件，96K 即最高档）

修复前 `quality_level_map['96K'] = 2`，且注释断言 "level 2 and level 96 currently
resolve to the same 96K M4A" —— 与实测冲突。后果是"选 96K"的请求第一发就打到
`/2` 拿到 48K 文件，成功路径再把它标记成 `96K`，形成**用户无法察觉的静默降级**。

## 失败语义（另一条 P0）

参考实现 `ximalaya_dl.py:113-118` 把两类失败分开：

    PermissionDenied  —— 真的没权限（下架/未购买）→ 可以降级到下一档
    TransientError    —— 网络类瞬时错误 → 必须原地重试，绝不降级

其文档 `:223-232` 记录了误判代价：「537 集里 31 个文件被静默降级」。并发 8~12 时
502/503/504 是高频事件，因此"任何非 200 都降级"会把网关抖动伪装成音质问题。

## 权限码

`ret=726`（立即购买畅听）是付费集无权限的权威信号。修复前代码只识别 `ret == 130`，
726 落到通用错误分支后靠 `permission_words` 文本匹配"购买"二字偶然命中 ——
依赖服务端文案不变，且会继续降级白跑两个档位。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core.ximalaya_download_manager import XimalayaDownloadManager


class FakeStreamResponse:
    def __init__(self, status_code=200, headers=None, body=b""):
        self.status_code = status_code
        self.headers = headers or {}
        self._body = body

    def iter_content(self, chunk_size=None):
        del chunk_size
        if self._body:
            yield self._body

    def close(self):
        return None


def _json_response(payload, status_code=200):
    body = json.dumps(payload).encode("utf-8")
    return FakeStreamResponse(
        status_code=status_code,
        headers={
            "content-type": "application/json;charset=utf-8",
            "content-length": str(len(body)),
        },
        body=body,
    )


class XimalayaQualityLevelTest(unittest.TestCase):
    def setUp(self):
        self.manager = XimalayaDownloadManager()
        self.tmp = Path(tempfile.mkdtemp(prefix="ximalaya-quality-"))

    def _download(self, quality, responses, **kwargs):
        """跑一次旧版直连下载，返回 (是否成功, 请求到的 level 列表)。"""
        levels = []

        def fake_get(url, **call_kwargs):
            del call_kwargs
            levels.append(url.rstrip("/").rsplit("/", 1)[-1])
            return responses[min(len(levels) - 1, len(responses) - 1)]

        save_path = self.tmp / f"{quality}-{len(levels)}.m4a"
        with mock.patch.object(self.manager.session, "get", side_effect=fake_get):
            ok = self.manager._download_m4a_direct_api(
                "123456", quality, str(save_path), "测试章节", **kwargs
            )
        return ok, levels

    # --- 档位表本身 --------------------------------------------------------
    def test_96k_chain_does_not_start_at_the_48k_level(self):
        levels = self.manager.legacy_redirect_levels["96K"]
        self.assertNotEqual(
            levels[0],
            2,
            "旧直连的 /2 实测返回 48K（-aacv2-48K.m4a），不能作为 96K 的首选档位",
        )
        self.assertEqual(levels[0], 3, "96K 的首选档位应为实测的 /3")

    def test_24k_and_48k_levels_match_measured_bitrates(self):
        self.assertEqual(self.manager.legacy_redirect_levels["24K"][0], 0)
        self.assertEqual(self.manager.legacy_redirect_levels["48K"][0], 1)

    def test_quality_level_map_head_matches_fallback_chain(self):
        """quality_level_map 必须与降级链首项保持一致，避免两套档位语义。"""
        for quality, levels in self.manager.legacy_redirect_levels.items():
            self.assertEqual(self.manager.quality_level_map[quality], levels[0], quality)

    def test_fallback_chains_are_monotonically_descending(self):
        for quality, levels in self.manager.legacy_redirect_levels.items():
            self.assertEqual(
                list(levels),
                sorted(levels, reverse=True),
                f"{quality} 的降级链必须逐档下降",
            )

    def test_96k_request_never_targets_the_48k_level(self):
        ok, levels = self._download("96K", [FakeStreamResponse(status_code=200)])
        self.assertFalse(ok)
        self.assertTrue(levels, "应至少发出一次请求")
        self.assertNotEqual(levels[0], "2", "96K 的首发请求打到了 48K 档位 /2")

    # --- 失败语义：瞬时错误不得降级 ---------------------------------------
    def test_transient_http_error_does_not_downgrade_quality(self):
        """502/503/504 是网关抖动，换档位同样失败，不能伪装成音质不可用。"""
        for status in (429, 500, 502, 503, 504, 522, 524):
            with self.subTest(status=status):
                ok, levels = self._download("96K", [FakeStreamResponse(status_code=status)])
                self.assertFalse(ok)
                self.assertEqual(
                    len(levels),
                    1,
                    f"HTTP {status} 触发了音质降级（请求档位序列 {levels}）",
                )
                self.assertEqual(self.manager.last_error_type, "download_failed")

    def test_permission_http_error_still_allows_downgrade(self):
        """404/410 属于内容不可用，继续尝试更低档位是合理的。"""
        ok, levels = self._download(
            "96K",
            [
                FakeStreamResponse(status_code=404),
                FakeStreamResponse(status_code=404),
                FakeStreamResponse(status_code=404),
            ],
        )
        self.assertFalse(ok)
        self.assertGreater(len(levels), 1, "权限/不可用类错误应继续尝试下一档")

    # --- 权限码 726 --------------------------------------------------------
    def test_ret_726_is_classified_as_restricted_without_downgrading(self):
        ok, levels = self._download(
            "96K",
            [_json_response({"ret": 726, "msg": "立即购买畅听"})],
            allow_public_fallback=False,
        )
        self.assertFalse(ok)
        self.assertEqual(len(levels), 1, "726 表示无权限，换档位不会有不同结果")
        self.assertEqual(
            self.manager.last_error_type,
            "restricted",
            "726 必须显式归类为权限不足，而不是依赖错误文案里的关键词",
        )


if __name__ == "__main__":
    unittest.main()
