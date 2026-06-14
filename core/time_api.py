#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
时间工具类
使用本地时间生成时间戳（用于API签名等）
"""

import time


class TimeAPI:
    """时间工具管理器 - 使用本地时间"""
    
    def __init__(self):
        pass
        
    def get_timestamp(self, use_cache: bool = True) -> int:
        """获取10位秒级时间戳（使用本地时间）"""
        return int(time.time())
    
    def get_timestamp_ms(self, use_cache: bool = True) -> int:
        """获取13位毫秒级时间戳（使用本地时间）"""
        return int(time.time() * 1000)
    
    def get_timestamp_str(self, use_cache: bool = True) -> str:
        """获取10位秒级时间戳字符串"""
        return str(self.get_timestamp(use_cache))
    
    def get_timestamp_ms_str(self, use_cache: bool = True) -> str:
        """获取13位毫秒级时间戳字符串"""
        return str(self.get_timestamp_ms(use_cache))
    
    def clear_cache(self):
        """清除缓存（兼容性方法，实际无缓存）"""
        pass


# 全局时间API实例
time_api = TimeAPI()


def get_timestamp() -> int:
    """获取10位秒级时间戳（使用本地时间）"""
    return time_api.get_timestamp()


def get_timestamp_ms() -> int:
    """获取13位毫秒级时间戳（使用本地时间）"""
    return time_api.get_timestamp_ms()


def get_timestamp_str() -> str:
    """获取10位秒级时间戳字符串"""
    return time_api.get_timestamp_str()


def get_timestamp_ms_str() -> str:
    """获取13位毫秒级时间戳字符串"""
    return time_api.get_timestamp_ms_str()


def clear_time_cache():
    """清除时间缓存（兼容性方法）"""
    time_api.clear_cache()


if __name__ == "__main__":
    # 测试时间工具
    print("🧪 测试时间工具...")
    
    print(f"10位时间戳: {get_timestamp()}")
    print(f"13位时间戳: {get_timestamp_ms()}")
    print(f"10位字符串: {get_timestamp_str()}")
    print(f"13位字符串: {get_timestamp_ms_str()}")
