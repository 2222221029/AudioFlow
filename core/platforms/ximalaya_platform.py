#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
喜马拉雅平台模块
独立处理喜马拉雅平台的搜索、下载等功能
"""

import sys
from pathlib import Path
from typing import List, Optional, Dict, Any
from dataclasses import dataclass

# 添加项目根目录到路径
sys.path.append(str(Path(__file__).parent.parent))
from data_models import Book

@dataclass
class XimalayaConfig:
    """喜马拉雅平台配置"""
    base_url: str = "https://www.ximalaya.com"
    search_url: str = "https://www.ximalaya.com/revision/search"
    headers: Dict[str, str] = None
    
    def __post_init__(self):
        if self.headers is None:
            self.headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                'Accept': 'application/json, text/plain, */*',
                'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
                'Accept-Encoding': 'gzip, deflate, br',
                'Connection': 'keep-alive',
                'Referer': 'https://www.ximalaya.com/',
            }

class XimalayaPlatform:
    """喜马拉雅平台处理器"""
    
    def __init__(self, api_manager=None):
        self.api_manager = api_manager
        self.config = XimalayaConfig()
        self.platform_name = "喜马拉雅"
    
    def search_books(self, query: str) -> List[Book]:
        """搜索喜马拉雅书籍"""
        try:
            if self.api_manager:
                # 使用现有的API管理器
                return self.api_manager.search_books(query)
            else:
                # 独立搜索实现（占位符）
                print(f"🔍 搜索喜马拉雅书籍: {query}")
                print("⚠️ 喜马拉雅独立搜索功能待实现，需要API管理器")
                return []
        except Exception as e:
            print(f"❌ 喜马拉雅搜索失败: {e}")
            return []
    
    def get_book_details(self, book_id: str) -> Optional[Book]:
        """获取书籍详情"""
        try:
            if self.api_manager:
                # 使用现有的API管理器
                return self.api_manager.get_book_details(book_id)
            else:
                print(f"🔍 获取喜马拉雅书籍详情: {book_id}")
                print("⚠️ 喜马拉雅独立获取详情功能待实现")
                return None
        except Exception as e:
            print(f"❌ 获取喜马拉雅书籍详情失败: {e}")
            return None
    
    def get_chapters(self, book_id: str) -> List[Dict[str, Any]]:
        """获取章节列表"""
        try:
            if self.api_manager:
                # 使用现有的API管理器
                return self.api_manager.get_chapters(book_id)
            else:
                print(f"🔍 获取喜马拉雅章节列表: {book_id}")
                print("⚠️ 喜马拉雅独立获取章节功能待实现")
                return []
        except Exception as e:
            print(f"❌ 获取喜马拉雅章节列表失败: {e}")
            return []
    
    def download_audio(self, book_id: str, chapter_id: str, output_path: str) -> bool:
        """下载音频文件"""
        try:
            if self.api_manager:
                # 使用现有的API管理器
                return self.api_manager.download_audio(book_id, chapter_id, output_path)
            else:
                print(f"🔍 下载喜马拉雅音频: {book_id}/{chapter_id}")
                print("⚠️ 喜马拉雅独立下载功能待实现")
                return False
        except Exception as e:
            print(f"❌ 下载喜马拉雅音频失败: {e}")
            return False
    
    def is_available(self) -> bool:
        """检查平台是否可用"""
        return self.api_manager is not None
    
    def get_platform_info(self) -> Dict[str, Any]:
        """获取平台信息"""
        return {
            "name": self.platform_name,
            "base_url": self.config.base_url,
            "available": self.is_available(),
            "features": {
                "search": True,
                "download": self.is_available(),
                "chapters": self.is_available(),
                "details": self.is_available()
            }
        }

def create_ximalaya_platform(api_manager=None) -> XimalayaPlatform:
    """创建喜马拉雅平台实例"""
    return XimalayaPlatform(api_manager)

# 测试函数
def test_ximalaya_platform():
    """测试喜马拉雅平台"""
    print("🚀 开始测试喜马拉雅平台...")
    
    platform = create_ximalaya_platform()
    info = platform.get_platform_info()
    
    print(f"📱 平台信息: {info}")
    
    # 测试搜索（需要API管理器）
    if platform.is_available():
        books = platform.search_books("测试")
        print(f"🔍 搜索结果: {len(books)} 本书")
    else:
        print("⚠️ 喜马拉雅平台需要API管理器才能正常工作")

if __name__ == "__main__":
    test_ximalaya_platform()



