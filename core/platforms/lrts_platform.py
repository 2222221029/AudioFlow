#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
懒人听书平台模块
基于 lrts_manager.py 的完整平台实现
参考 TingShuDownloader 4.7.20 C++ 应用的 API 行为
"""

import sys
from pathlib import Path
from typing import List, Optional, Dict, Any

sys.path.append(str(Path(__file__).parent.parent))
from data_models import Book


class LRTSPlatform:
    """懒人听书平台处理器（包装 LRTSManager）"""

    def __init__(self, lrts_manager=None):
        self.lrts_manager = lrts_manager
        self.platform_name = "懒人听书"

    def search_books(self, query: str) -> List[Book]:
        """搜索懒人听书书籍"""
        try:
            if not self.lrts_manager:
                print("[lrts-platform] 未提供 LRTSManager，无法搜索")
                return []
            results = self.lrts_manager.search_books(query)
            books = []
            for item in results:
                books.append(Book(
                    title=item.get("title", ""),
                    author=item.get("author", ""),
                    description=item.get("description", ""),
                    book_id=item.get("id", ""),
                    cover_url=item.get("cover", ""),
                    source="lrts",
                    category=item.get("category", ""),
                    status=item.get("status", ""),
                    episodes=item.get("episodes", 0),
                    plays=item.get("plays", 0),
                ))
            return books
        except Exception as e:
            print(f"❌ 懒人听书搜索失败: {e}")
            return []

    def get_book_details(self, book_id: str) -> Optional[Book]:
        """获取书籍详情"""
        try:
            if not self.lrts_manager:
                print("[lrts-platform] 未提供 LRTSManager，无法获取详情")
                return None
            info = self.lrts_manager.get_book_detail(book_id)
            if not info:
                return None
            return Book(
                title=info.get("title", ""),
                author=info.get("author", ""),
                description=info.get("description", ""),
                book_id=info.get("id", book_id),
                cover_url=info.get("cover", ""),
                source="lrts",
                category=info.get("category", ""),
                status=info.get("status", ""),
                episodes=info.get("episodes", 0),
                plays=info.get("plays", 0),
            )
        except Exception as e:
            print(f"❌ 获取懒人听书书籍详情失败: {e}")
            return None

    def get_chapters(self, book_id: str) -> List[Dict[str, Any]]:
        """获取章节列表"""
        try:
            if not self.lrts_manager:
                print("[lrts-platform] 未提供 LRTSManager，无法获取章节")
                return []
            return self.lrts_manager.get_chapters(book_id) or []
        except Exception as e:
            print(f"❌ 获取懒人听书章节失败: {e}")
            return []

    def download_audio(self, book_id: str, chapter_id: str, output_path: str) -> bool:
        """下载音频文件"""
        try:
            if not self.lrts_manager:
                print("[lrts-platform] 未提供 LRTSManager，无法下载")
                return False
            url = self.lrts_manager.get_audio_url(book_id, chapter_id)
            if not url:
                print(f"❌ 无法获取音频 URL: {book_id}/{chapter_id}")
                return False
            return self.lrts_manager.download_audio(url, output_path)
        except Exception as e:
            print(f"❌ 下载懒人听书音频失败: {e}")
            return False

    def set_cookie(self, cookie_string: str) -> None:
        """设置 Cookie"""
        if self.lrts_manager:
            self.lrts_manager.set_cookie(cookie_string)

    def is_available(self) -> bool:
        """检查平台是否可用"""
        return self.lrts_manager is not None

    def get_platform_info(self) -> Dict[str, Any]:
        """获取平台信息"""
        return {
            "name": self.platform_name,
            "base_url": "https://m.lrts.me",
            "available": self.is_available(),
            "features": {
                "search": self.is_available(),
                "download": self.is_available(),
                "chapters": self.is_available(),
                "details": self.is_available(),
            }
        }


def create_lrts_platform(lrts_manager=None) -> LRTSPlatform:
    """创建懒人听书平台实例"""
    return LRTSPlatform(lrts_manager)
