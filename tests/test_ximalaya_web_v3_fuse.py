"""ret=1001「系统繁忙」熔断：连续触发后进入全局冷却（v1.0.43）。"""

from __future__ import annotations

import time

from core.ximalaya_download_manager import XimalayaDownloadManager


def _reset(cls):
    cls._WEB_V3_BUSY_EPISODES = 0
    cls._WEB_V3_BUSY_EPISODE_STARTED_AT = 0.0
    cls._WEB_V3_RATE_LIMITED_UNTIL = 0.0
    cls._WEB_V3_MIN_INTERVAL = cls._WEB_V3_BASE_INTERVAL
    cls._WEB_V3_SUCCESS_STREAK = 0


class TestWebV3BusyFuse:
    def test_fuse_not_tripped_below_trigger(self):
        cls = XimalayaDownloadManager
        _reset(cls)
        trigger = cls._WEB_V3_BUSY_FUSE_TRIGGER
        for _ in range(max(1, trigger - 1)):
            cls._mark_web_v3_busy()
        assert cls._WEB_V3_RATE_LIMITED_UNTIL == 0.0

    def test_fuse_trips_global_cooldown_after_trigger(self):
        cls = XimalayaDownloadManager
        _reset(cls)
        trigger = cls._WEB_V3_BUSY_FUSE_TRIGGER
        for _ in range(trigger):
            cls._mark_web_v3_busy()
        assert cls._WEB_V3_RATE_LIMITED_UNTIL > time.monotonic()
        remaining = cls._WEB_V3_RATE_LIMITED_UNTIL - time.monotonic()
        assert remaining >= cls._WEB_V3_BUSY_FUSE_COOLDOWN * 0.9

    def test_wait_slot_blocks_while_fused(self):
        cls = XimalayaDownloadManager
        _reset(cls)
        trigger = cls._WEB_V3_BUSY_FUSE_TRIGGER
        for _ in range(trigger):
            cls._mark_web_v3_busy()
        fused_until = cls._WEB_V3_RATE_LIMITED_UNTIL
        cls._WEB_V3_LAST_REQUEST_AT = 0.0
        before = time.monotonic()
        cls._wait_for_web_v3_slot()
        elapsed = time.monotonic() - before
        assert elapsed >= 0.0
        assert fused_until <= time.monotonic() or elapsed >= 1.0

    def test_expired_fuse_clears_on_reset(self):
        cls = XimalayaDownloadManager
        _reset(cls)
        trigger = cls._WEB_V3_BUSY_FUSE_TRIGGER
        for _ in range(trigger):
            cls._mark_web_v3_busy()
        assert cls._WEB_V3_RATE_LIMITED_UNTIL > 0.0
        cls._clear_web_v3_rate_limit()
        assert cls._WEB_V3_RATE_LIMITED_UNTIL == 0.0