#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Small Qt signal/thread compatibility layer for the Docker server build."""

from __future__ import annotations

import logging
import time


class Signal:
    def __init__(self, *args, **kwargs):
        self._callbacks = []

    def connect(self, callback):
        if callable(callback):
            self._callbacks.append(callback)

    def emit(self, *args, **kwargs):
        # 单个回调抛异常不能影响其它回调，更不能把异常抛回 emit 的调用方——否则下载主循环
        # 里每章触发的进度 emit 一旦遇到回调(set_task 等)偶发失败，就会中断整个下载循环、
        # 进度卡死（文件仍由线程池下完，进度条却停在中途）。逐个隔离、失败仅记日志。
        for callback in list(self._callbacks):
            try:
                callback(*args, **kwargs)
            except Exception:
                logging.getLogger(__name__).warning("signal callback failed", exc_info=True)


class SignalDescriptor:
    def __init__(self, *args, **kwargs):
        self._name = ""

    def __set_name__(self, owner, name):
        self._name = f"_{name}_signal"

    def __get__(self, instance, owner):
        if instance is None:
            return self
        signal = instance.__dict__.get(self._name)
        if signal is None:
            signal = Signal()
            instance.__dict__[self._name] = signal
        return signal


def pyqtSignal(*args, **kwargs):
    return SignalDescriptor(*args, **kwargs)


class QObject:
    def __init__(self, *args, **kwargs):
        super().__init__()


class QThread(QObject):
    def __init__(self, parent=None):
        super().__init__()
        self._parent = parent

    @staticmethod
    def msleep(milliseconds):
        time.sleep(milliseconds / 1000)

    def isRunning(self):
        return False

    def wait(self, timeout=None):
        return True

    def terminate(self):
        return None


class _TimerTimeout:
    def __init__(self):
        self._callback = None

    def connect(self, callback):
        self._callback = callback

    def emit(self):
        if self._callback:
            self._callback()


class QTimer(QObject):
    def __init__(self, *args, **kwargs):
        super().__init__()
        self.timeout = _TimerTimeout()
        self._active = False

    def start(self, interval=None):
        self._active = True

    def stop(self):
        self._active = False

