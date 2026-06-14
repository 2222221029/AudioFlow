# -*- coding: utf-8 -*-
"""全局日志系统 — 所有输出自动写入文件"""
import sys
import os
import datetime
import traceback
import io

# 日志文件路径
LOG_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(LOG_DIR, 'debug.log')

# 确保 stdout/stderr 使用 UTF-8
if sys.platform == 'win32':
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
    except:
        pass


class Logger:
    """同时输出到控制台和日志文件"""
    def __init__(self):
        self._file = None

    def _ensure_file(self):
        if self._file is None:
            self._file = open(LOG_FILE, 'a', encoding='utf-8')

    def _write(self, level, msg):
        ts = datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]
        line = f"[{ts}] [{level}] {msg}\n"
        # 写控制台
        try:
            sys.__stdout__.write(line)
            sys.__stdout__.flush()
        except:
            pass
        # 写文件
        try:
            self._ensure_file()
            self._file.write(line)
            self._file.flush()
        except:
            pass

    def info(self, msg):
        self._write('INFO', msg)

    def warn(self, msg):
        self._write('WARN', msg)

    def error(self, msg):
        self._write('ERROR', msg)

    def debug(self, msg):
        self._write('DEBUG', msg)

    def traceback(self):
        self._write('ERROR', traceback.format_exc())


_logger = None


def get_logger():
    """获取全局日志实例"""
    global _logger
    if _logger is None:
        _logger = Logger()
    return _logger


def log(msg):
    """快捷日志"""
    get_logger().info(msg)


def log_error(msg):
    """错误日志"""
    get_logger().error(msg)
