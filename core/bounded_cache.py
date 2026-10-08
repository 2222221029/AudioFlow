# -*- coding: utf-8 -*-
"""通用有界字典缓存：简单 FIFO 淘汰，防止长驻进程里缓存无限增长。"""


def trim(cache, maxlen=256):
    """把字典裁剪到最多 maxlen 项（淘汰最早的插入项）。

    :param cache: 需要受控的 dict。
    :param maxlen: 保留上限；0/None 表示不裁剪。
    """
    if not cache or not maxlen or maxlen <= 0:
        return cache
    size = len(cache)
    if size <= maxlen:
        return cache
    excess = size - maxlen
    for key in list(cache)[:excess]:
        cache.pop(key, None)
    return cache


def put(cache, key, value, maxlen=256):
    """写入并裁剪，一步到位。"""
    cache[key] = value
    return trim(cache, maxlen)


def get(cache, key, default=None):
    return cache.get(key, default)