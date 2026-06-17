import unittest

from core.qt_compat import Signal


class QtCompatSignalTest(unittest.TestCase):
    def test_emit_isolates_failing_callback(self):
        # 一个回调抛异常不能影响其它回调，也不能把异常抛回 emit 的调用方——
        # 否则下载主循环里每章触发的进度 emit 一旦遇到回调偶发失败就会中断整个下载，
        # 文件仍被线程池下完、进度条却卡在中途。
        hits = []
        sig = Signal()
        sig.connect(lambda v: hits.append(("a", v)))
        sig.connect(lambda v: (_ for _ in ()).throw(RuntimeError("boom")))
        sig.connect(lambda v: hits.append(("c", v)))

        sig.emit(42)  # 不应抛出

        self.assertEqual(hits, [("a", 42), ("c", 42)])


if __name__ == "__main__":
    unittest.main()
