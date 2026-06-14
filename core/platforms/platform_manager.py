#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
平台管理器
统一管理四大平台的实例和功能
"""

import sys
from pathlib import Path
from typing import List, Optional, Dict, Any
from dataclasses import dataclass

# 添加项目根目录到路径
sys.path.append(str(Path(__file__).parent.parent))
from data_models import Book

# 导入各平台模块
from platforms.ximalaya_platform import create_ximalaya_platform, XimalayaPlatform
from platforms.lrts_platform import create_lrts_platform, LRTSPlatform
from platforms.qimao_platform import create_qimao_platform, QimaoPlatform
from platforms.fanqie_platform import create_fanqie_platform, FanqiePlatform

@dataclass
class PlatformSearchResult:
    """平台搜索结果数据类"""
    platform: str
    books: List[Book]
    total_count: int
    success: bool
    error_message: Optional[str] = None

class PlatformManager:
    """平台管理器"""
    
    def __init__(self, api_manager=None, lrts_manager=None):
        self.api_manager = api_manager
        self.lrts_manager = lrts_manager
        self.platforms = {}
        self._init_platforms()
    
    def _init_platforms(self):
        """初始化所有平台"""
        # 喜马拉雅平台
        self.platforms["ximalaya"] = create_ximalaya_platform(self.api_manager)
        
        # 懒人听书平台
        self.platforms["lrts"] = create_lrts_platform(self.lrts_manager)
        
        # 七猫免费小说平台
        self.platforms["qimao"] = create_qimao_platform()
        
        # 番茄畅听平台
        self.platforms["fanqie"] = create_fanqie_platform()
    
    def get_available_platforms(self) -> List[str]:
        """获取可用的平台列表"""
        return list(self.platforms.keys())
    
    def get_platform_display_name(self, platform: str) -> str:
        """获取平台显示名称"""
        platform_names = {
            "ximalaya": "喜马拉雅",
            "lrts": "懒人听书", 
            "qimao": "七猫",
            "fanqie": "番茄畅听"
        }
        return platform_names.get(platform, platform)
    
    def get_platform(self, platform: str):
        """获取指定平台实例"""
        return self.platforms.get(platform)
    
    def search_single_platform(self, platform: str, query: str) -> PlatformSearchResult:
        """搜索单个平台"""
        if platform not in self.platforms:
            return PlatformSearchResult(
                platform=platform,
                books=[],
                total_count=0,
                success=False,
                error_message=f"不支持的平台: {platform}"
            )
        
        try:
            platform_instance = self.platforms[platform]
            books = platform_instance.search_books(query)
            
            # 为每个书籍设置source属性
            for book in books:
                book.source = platform
            
            return PlatformSearchResult(
                platform=platform,
                books=books,
                total_count=len(books),
                success=True
            )
        except Exception as e:
            return PlatformSearchResult(
                platform=platform,
                books=[],
                total_count=0,
                success=False,
                error_message=str(e)
            )
    
    def search_all_platforms(self, query: str) -> Dict[str, PlatformSearchResult]:
        """搜索所有平台"""
        results = {}
        for platform in self.platforms.keys():
            results[platform] = self.search_single_platform(platform, query)
        return results
    
    def search_selected_platforms(self, platforms: List[str], query: str) -> Dict[str, PlatformSearchResult]:
        """搜索选定的平台"""
        results = {}
        for platform in platforms:
            if platform in self.platforms:
                results[platform] = self.search_single_platform(platform, query)
        return results
    
    def get_platform_info(self, platform: str) -> Optional[Dict[str, Any]]:
        """获取平台信息"""
        if platform in self.platforms:
            return self.platforms[platform].get_platform_info()
        return None
    
    def get_all_platforms_info(self) -> Dict[str, Dict[str, Any]]:
        """获取所有平台信息"""
        info = {}
        for platform, instance in self.platforms.items():
            info[platform] = instance.get_platform_info()
        return info
    
    def is_platform_available(self, platform: str) -> bool:
        """检查平台是否可用"""
        if platform in self.platforms:
            return self.platforms[platform].is_available()
        return False
    
    def get_available_features(self, platform: str) -> Dict[str, bool]:
        """获取平台可用功能"""
        if platform in self.platforms:
            return self.platforms[platform].get_platform_info().get("features", {})
        return {}

def create_platform_manager(api_manager=None, lrts_manager=None) -> PlatformManager:
    """创建平台管理器实例"""
    return PlatformManager(api_manager, lrts_manager)

# 测试函数
def test_platform_manager():
    """测试平台管理器"""
    print("🚀 开始测试平台管理器...")
    
    # 创建平台管理器
    manager = create_platform_manager()
    
    # 获取所有平台信息
    platforms_info = manager.get_all_platforms_info()
    print("📱 所有平台信息:")
    for platform, info in platforms_info.items():
        print(f"  {info['name']}: 可用={info['available']}, 功能={info['features']}")
    
    # 获取可用平台
    available_platforms = manager.get_available_platforms()
    print(f"📋 可用平台: {available_platforms}")
    
    # 测试搜索
    query = "斗罗大陆"
    print(f"🔍 搜索关键词: {query}")
    
    # 搜索所有平台
    results = manager.search_all_platforms(query)
    
    for platform, result in results.items():
        platform_name = manager.get_platform_display_name(platform)
        if result.success:
            print(f"✅ {platform_name}: 找到 {result.total_count} 本书")
        else:
            print(f"❌ {platform_name}: 搜索失败 - {result.error_message}")

if __name__ == "__main__":
    test_platform_manager()
