#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
七猫免费小说平台模块
独立处理七猫免费小说平台的搜索、下载等功能
"""

import sys
import requests
import re
from pathlib import Path
from typing import List, Optional, Dict, Any
from dataclasses import dataclass
from bs4 import BeautifulSoup

# 添加项目根目录到路径
sys.path.append(str(Path(__file__).parent.parent))
from data_models import Book

@dataclass
class QimaoConfig:
    """七猫免费小说平台配置"""
    base_url: str = "https://www.qimao.com"
    search_url: str = "https://www.qimao.com/search"
    headers: Dict[str, str] = None
    
    def __post_init__(self):
        if self.headers is None:
            self.headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Language': 'zh-CN,zh;q=0.8,en-US;q=0.5,en;q=0.3',
                'Accept-Encoding': 'gzip, deflate, br',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
                'Referer': 'https://www.qimao.com/',
            }

class QimaoPlatform:
    """七猫免费小说平台处理器"""
    
    def __init__(self):
        self.config = QimaoConfig()
        self.platform_name = "七猫免费小说"
    
    def search_books(self, query: str) -> List[Book]:
        """搜索七猫免费小说书籍"""
        try:
            print(f"🔍 搜索七猫免费小说书籍: {query}")
            
            # 构建搜索URL
            search_url = f"{self.config.base_url}/search"
            params = {
                'keyword': query
            }
            
            response = requests.get(search_url, params=params, headers=self.config.headers, timeout=10)
            response.raise_for_status()
            
            # 解析搜索结果
            soup = BeautifulSoup(response.text, 'html.parser')
            books = []
            
            # 查找书籍列表 - 多种可能的CSS选择器
            book_items = (
                soup.find_all('div', class_='book-item') or 
                soup.find_all('li', class_='book-item') or
                soup.find_all('div', class_='book-info') or
                soup.find_all('div', class_='search-result-item')
            )
            
            for item in book_items:
                try:
                    # 提取书籍信息
                    title_elem = (
                        item.find('h3') or 
                        item.find('a', class_='title') or 
                        item.find('span', class_='title') or
                        item.find('div', class_='title') or
                        item.find('h2')
                    )
                    title = title_elem.get_text(strip=True) if title_elem else "未知标题"
                    
                    # 提取作者
                    author_elem = (
                        item.find('span', class_='author') or 
                        item.find('p', class_='author') or
                        item.find('div', class_='author') or
                        item.find('span', class_='writer')
                    )
                    author = author_elem.get_text(strip=True) if author_elem else "未知作者"
                    
                    # 提取书籍ID
                    link_elem = item.find('a')
                    book_id = ""
                    if link_elem and link_elem.get('href'):
                        href = link_elem.get('href')
                        # 从URL中提取书籍ID
                        book_id_match = re.search(r'/book/(\d+)', href)
                        if book_id_match:
                            book_id = book_id_match.group(1)
                        else:
                            # 如果没有找到数字ID，使用href作为ID
                            book_id = href.replace('/', '_').replace('.html', '')
                    
                    # 提取封面
                    cover_elem = item.find('img')
                    cover_url = ""
                    if cover_elem:
                        cover_url = cover_elem.get('src') or cover_elem.get('data-src', '')
                        if cover_url and not cover_url.startswith('http'):
                            cover_url = self.config.base_url + cover_url
                    
                    # 提取描述
                    desc_elem = (
                        item.find('p', class_='desc') or 
                        item.find('div', class_='desc') or
                        item.find('p', class_='summary') or
                        item.find('div', class_='summary')
                    )
                    description = desc_elem.get_text(strip=True) if desc_elem else ""
                    
                    if title and book_id:
                        books.append(Book(
                            title=title,
                            author=author,
                            description=description,
                            book_id=book_id,
                            cover_url=cover_url,
                            source="qimao"
                        ))
                except Exception as e:
                    print(f"⚠️ 解析七猫书籍项失败: {e}")
                    continue
            
            print(f"✅ 七猫免费小说搜索完成，找到 {len(books)} 本书")
            return books
            
        except Exception as e:
            print(f"❌ 七猫免费小说搜索失败: {e}")
            return []
    
    def get_book_details(self, book_id: str) -> Optional[Book]:
        """获取书籍详情"""
        try:
            print(f"🔍 获取七猫免费小说书籍详情: {book_id}")
            print("⚠️ 七猫免费小说获取详情功能待实现")
            return None
        except Exception as e:
            print(f"❌ 获取七猫免费小说书籍详情失败: {e}")
            return None
    
    def get_chapters(self, book_id: str) -> List[Dict[str, Any]]:
        """获取章节列表"""
        try:
            print(f"🔍 获取七猫免费小说章节列表: {book_id}")
            print("⚠️ 七猫免费小说获取章节功能待实现")
            return []
        except Exception as e:
            print(f"❌ 获取七猫免费小说章节列表失败: {e}")
            return []
    
    def download_audio(self, book_id: str, chapter_id: str, output_path: str) -> bool:
        """下载音频文件"""
        try:
            print(f"🔍 下载七猫免费小说音频: {book_id}/{chapter_id}")
            print("⚠️ 七猫免费小说下载功能待实现")
            return False
        except Exception as e:
            print(f"❌ 下载七猫免费小说音频失败: {e}")
            return False
    
    def is_available(self) -> bool:
        """检查平台是否可用"""
        return True  # 七猫免费小说平台独立可用
    
    def get_platform_info(self) -> Dict[str, Any]:
        """获取平台信息"""
        return {
            "name": self.platform_name,
            "base_url": self.config.base_url,
            "available": self.is_available(),
            "features": {
                "search": True,
                "download": False,  # 待实现
                "chapters": False,  # 待实现
                "details": False    # 待实现
            }
        }

def create_qimao_platform() -> QimaoPlatform:
    """创建七猫免费小说平台实例"""
    return QimaoPlatform()

# 测试函数
def test_qimao_platform():
    """测试七猫免费小说平台"""
    print("🚀 开始测试七猫免费小说平台...")
    
    platform = create_qimao_platform()
    info = platform.get_platform_info()
    
    print(f"📱 平台信息: {info}")
    
    # 测试搜索
    books = platform.search_books("斗罗大陆")
    print(f"🔍 搜索结果: {len(books)} 本书")
    
    for book in books[:3]:  # 显示前3本书
        print(f"  📖 {book.title} - {book.author}")

if __name__ == "__main__":
    test_qimao_platform()



