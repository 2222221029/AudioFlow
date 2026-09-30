# -*- coding: utf-8 -*-
"""`core/errors.py` 的行为锁定测试。

这些用例对应移植分析里的 P0-3：把「重试 / 降级」判定从散落各处的
动态 import + 文本匹配，收敛成一张显式的状态码表 + 异常分级。
"""

import pytest

from core import errors


class TestStatusTables:
    def test_transient_status_matches_reference_implementation(self):
        # 与 XimalayaApp Core/Net.cs:33 的 RetryableHttp 一致（本项目原有
        # ximalaya_download_manager.XIMALAYA_TRANSIENT_STATUS 也是这一组）。
        assert errors.TRANSIENT_STATUS == frozenset({408, 425, 429, 500, 502, 503, 504, 522, 524})

    def test_permission_status_matches_reference_implementation(self):
        # 与 Core/Net.cs:34 的 DeniedHttp 一致，并补上 451（法律原因不可用）。
        assert {400, 401, 403, 404, 410} <= errors.PERMISSION_STATUS

    @pytest.mark.parametrize("status", [429, 500, 502, 503, 504, 522, 524, 408, 425])
    def test_is_transient_status(self, status):
        assert errors.is_transient_status(status) is True

    @pytest.mark.parametrize("status", [400, 401, 403, 404, 410, 451])
    def test_is_permission_status(self, status):
        assert errors.is_permission_status(status) is True

    @pytest.mark.parametrize("value", [None, "", "abc", object()])
    def test_status_helpers_tolerate_junk(self, value):
        assert errors.is_transient_status(value) is False
        assert errors.is_permission_status(value) is False

    def test_status_helpers_accept_numeric_strings(self):
        assert errors.is_transient_status("502") is True
        assert errors.is_permission_status("403") is True


class TestClassify:
    def test_native_graded_exceptions(self):
        assert errors.classify(errors.TransientError("x")) == "transient"
        assert errors.classify(errors.PermissionDenied("x")) == "restricted"
        assert errors.classify(errors.RiskControlError("x")) == "rate_limited"
        assert errors.classify(errors.IllegalRequestError("x")) == "illegal_request"
        assert errors.classify(errors.ContentInvalidError("x")) == "content_invalid"

    def test_classify_uses_status_code_attribute(self):
        exc = errors.DownloadError("boom")
        exc.status_code = 503
        assert errors.classify(exc) == "transient"

    def test_classify_reads_status_from_response_object(self):
        class _Resp:
            status_code = 403

        class _HttpError(Exception):
            response = _Resp()

        assert errors.classify(_HttpError("forbidden")) == "restricted"

    def test_stdlib_timeout_and_connection_are_transient(self):
        assert errors.classify(TimeoutError("t")) == "transient"
        assert errors.classify(ConnectionError("c")) == "transient"

    def test_unknown_exception_falls_back_to_download_error(self):
        assert errors.classify(ValueError("?")) == "download_error"


class TestShouldRetry:
    def test_transient_is_retryable(self):
        assert errors.should_retry("transient") is True
        assert errors.should_retry("rate_limited") is True

    def test_permission_and_structural_are_not_retryable(self):
        # 这是参考实现「537 集里 31 个文件被静默降级」那条教训的直接对策。
        assert errors.should_retry("restricted") is False
        assert errors.should_retry("quality_unavailable") is False
        assert errors.should_retry("illegal_request") is False
        assert errors.should_retry("content_invalid") is False

    def test_status_code_overrides_unknown_label(self):
        assert errors.should_retry("", status_code=502) is True
        assert errors.should_retry("", status_code=403) is False

    def test_unknown_label_stays_retryable_to_preserve_old_behaviour(self):
        # 改造前：没有 _error_type 的失败一律走普通重试。不能把它改成静默失败。
        assert errors.should_retry("") is True
        assert errors.should_retry("download_error") is True
        assert errors.should_retry(None) is True


class TestQuotaExhausted:
    @pytest.mark.parametrize(
        "message",
        [
            "电脑版今日下载额度已用完",
            "下载额度已用完或被临时限制",
            "mobile quota exceeded",
        ],
    )
    def test_detects_quota_messages(self, message):
        assert errors.is_quota_exhausted("download_failed", message) is True

    def test_explicit_error_type_wins(self):
        assert errors.is_quota_exhausted("quota_exhausted", "") is True

    def test_ordinary_errors_are_not_quota(self):
        assert errors.is_quota_exhausted("transient", "HTTP 502") is False


class TestRegistry:
    def test_rate_limit_tuples_are_always_isinstance_safe(self):
        # 关键回归：改造前是「动态 import 失败就置 None」，一旦模块改名
        # isinstance(x, None) 会抛 TypeError 或静默失效。现在恒为元组。
        assert isinstance(errors.RATE_LIMIT_TYPES, tuple)
        assert isinstance(errors.ILLEGAL_REQUEST_TYPES, tuple)
        assert isinstance(object(), errors.RATE_LIMIT_TYPES) is False

    def test_register_and_refresh(self):
        class _FakeRateLimit(Exception):
            pass

        errors.register("RateLimitError", _FakeRateLimit)
        errors.refresh_registry()
        try:
            assert _FakeRateLimit in errors.RATE_LIMIT_TYPES
            assert errors.classify(_FakeRateLimit("slow down")) == "rate_limited"
            assert errors.should_retry("rate_limited") is True
        finally:
            errors._REGISTERED.pop("RateLimitError", None)
            errors.refresh_registry()

    def test_register_ignores_non_exception_types(self):
        before = dict(errors._REGISTERED)
        errors.register("NotAnError", int)
        assert errors._REGISTERED == before

    def test_real_lrts_errors_are_registered_when_core_init_runs(self):
        """真实接入点：core/__init__.py 会登记 lrts 的两个异常类。

        ⚠ 这条用例锁定的是「登记能命中真实类」，不是「必须已登记」——
        单独 import core.lrts_manager 不触发 core/__init__ 的补丁时，
        元组为空也必须是安全的（见上一条）。
        """
        import core.lrts_manager as lrts

        errors.register("RateLimitError", lrts.RateLimitError)
        errors.register("IllegalRequestError", lrts.IllegalRequestError)
        errors.refresh_registry()
        assert errors.classify(lrts.RateLimitError()) == "rate_limited"
        assert errors.classify(lrts.IllegalRequestError()) == "illegal_request"
