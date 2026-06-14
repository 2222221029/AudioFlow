#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Small Qt signal/thread compatibility layer for the Docker server build."""

from __future__ import annotations

import time


class Signal:
    def __init__(self, *args, **kwargs):
        self._callbacks = []

    def connect(self, callback):
        if callable(callback):
            self._callbacks.append(callback)

    def emit(self, *args, **kwargs):
        for callback in list(self._callbacks):
            callback(*args, **kwargs)


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

