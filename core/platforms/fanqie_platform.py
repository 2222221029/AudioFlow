#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
番茄畅听平台模块
独立处理番茄畅听平台的搜索、下载等功能
"""

import sys
import requests
import re
from pathlib import Path
from typing import List, Optional, Dict, Any
from dataclasses import dataclass
# BeautifulSoup导入被注释，因为未使用
# from bs4 import BeautifulSoup

# 添加项目根目录到路径
sys.path.append(str(Path(__file__).parent.parent))
from data_models import Book

@dataclass
class FanqieConfig:
    """番茄畅听平台配置 - 基于EXE完整流程文档"""
    # 搜索API端点
    search_url: str = "https://api5-sinfonlinec.novelfm.com/novelfm/bookmall/search/page/v1/"
    # 章节列表API端点
    chapters_url: str = "https://api5-sinfonlinec.novelfm.com/novelfm/bookapi/directory/all_infos/v1/"
    # 音频API端点
    audio_url: str = "https://reading.snssdk.com/reading/reader/audio/playinfo/"
    headers: Dict[str, str] = None  # type: ignore
    base_params: Dict[str, str] = None  # type: ignore
    
    def __post_init__(self):
        if self.headers is None:
            self.headers = {
                'Content-Type': 'application/json; charset=utf-8',
                'User-Agent': 'com.xs.fm/608 (Linux; U; Android 9; zh_CN; 2210132C; Build/PQ3A.190605.07021633;tt-ok/3.12.13.17)'
            }
        if self.base_params is None:
            self.base_params = {
                "device_platform": "android",
                "os": "android",
                "ssmix": "a",
                "cdid": "dbced431-030e-4871-9f3a-e25662362a1b",
                "channel": "xiaomi_3040_64",
                "aid": "3040",
                "app_name": "novel_fm",
                "version_code": "608",
                "version_name": "6.0.8.32",
                "manifest_version_code": "608",
                "update_version_code": "60832",
                "resolution": "1440*2560",
                "dpi": "640",
                "device_type": "2210132C",
                "device_brand": "Xiaomi",
                "language": "zh",
                "os_api": "28",
                "os_version": "9",
                "ac": "wifi",
                "device_id": "3942194090368537",
                "iid": "1109875180222825",
                "comment_tag_c": "5",
                "vip_state": "0",
                "host_abi": "arm64-v8a",
                "category_style": "1",
                "need_personal_recommend": "1",
                "ab_sdk_version": "13894333,90360738,91027585,91016290,90948530,14097720,91068610,90352477,91196326,91281044,90128754,14129758,90940692,91048838,90940693,90174492,90362507,91279070,13971990,91249698,14131875,90116954,91283376,90098839,90179956,90332092,14209352,91002847,91294212,14083346,11531138,13876802,90111254,90948551,90167135,91246655,90331711,91046125,90126074,91008840,13478636,90110758,13747600,13616778,91092331,90989827,91273322,90948314,90107036,91270651,91225968,91268610,5711287,90114353,90098780,90952506,91048633,91247455,90614667,90939708,91004056,90941017,14013673,90661280,90941890,13859562,90177793,90609513",
                "rom_version": "PQ3A.190605.07021633+release-keys"
            }

class FanqiePlatform:
    """番茄畅听平台处理器"""
    
    def __init__(self):
        self.config = FanqieConfig()
        self.platform_name = "番茄畅听"
    
    def search_books(self, query: str) -> List[Book]:
        """搜索番茄畅听书籍"""
        try:
            print(f"🔍 搜索番茄畅听书籍: {query}")
            import time
            
            # 生成新的时间戳
            params = self.config.base_params.copy()
            params["_rticket"] = str(int(time.time() * 1000))
            
            request_body = {
                "query": query,
                "limit": 20,
                "offset": 0
            }
            
            try:
                response = requests.post(
                    self.config.search_url,
                    params=params,
                    headers=self.config.headers,
                    json=request_body,
                    timeout=10
                )
                
                if response.status_code == 200:
                    result = response.json()
                else:
                    print(f"请求失败: {response.status_code}")
                    return []
                    
            except Exception as e:
                print(f"请求异常: {e}")
                return []
            
            # 解析JSON响应
            books = []
            
            if 'data' in result and 'search_data' in result['data']:
                search_data = result['data']['search_data']
                
                for book_item in search_data:
                    if 'books' in book_item and len(book_item['books']) > 0:
                        book_data = book_item['books'][0]
                        
                        try:
                            book = Book(
                                title=book_data.get('book_name', ''),
                                author=book_data.get('author', ''),
                                description=book_data.get('abstract', ''),
                                book_id=str(book_data.get('book_id', '')),
                                cover_url=book_data.get('thumb_url', ''),
                                source="fanqie"
                            )
                            if book.title and book.book_id:
                                books.append(book)
                        except Exception as e:
                            print(f"⚠️ 解析番茄畅听书籍项失败: {e}")
                            continue
            
            print(f"✅ 番茄畅听搜索完成，找到 {len(books)} 本书")
            return books
            
        except Exception as e:
            print(f"❌ 番茄畅听搜索失败: {e}")
            return []
    
    def get_book_details(self, book_id: str) -> Optional[Book]:
        """获取书籍详情"""
        try:
            print(f"🔍 获取番茄畅听书籍详情: {book_id}")
            print("⚠️ 番茄畅听获取详情功能待实现")
            return None
        except Exception as e:
            print(f"❌ 获取番茄畅听书籍详情失败: {e}")
            return None
    
    def get_chapters(self, book_id: str) -> List[Dict[str, Any]]:
        """获取章节列表 - 使用番茄官方API"""
        try:
            print(f"🔍 获取番茄畅听章节列表: {book_id}")
            
            # 方法一：使用番茄官方API（推荐）
            chapters = self.get_chapters_fanqie(book_id)
            if chapters:
                return chapters
            
            # 方法二：使用cenguigui.cn API作为备选
            print("🔄 尝试使用cenguigui.cn API...")
            chapters = self.get_chapters_cenguigui(book_id)
            if chapters:
                return chapters
            
            print("❌ 所有章节API都失败了")
            return []
            
        except Exception as e:
            print(f"❌ 获取番茄畅听章节列表失败: {e}")
            return []
    
    def get_chapters_fanqie(self, book_id: str) -> List[Dict[str, Any]]:
        """使用番茄官方API获取章节列表"""
        try:
            print("📚 使用番茄官方API获取章节...")
            
            # 番茄官方章节API
            chapter_url = "https://fanqienovel.com/api/reader/directory/detail"
            params = {'bookId': book_id}
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                'Referer': 'https://fanqienovel.com/',
                'Accept': 'application/json, text/plain, */*',
                'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8'
            }
            
            print(f"📤 发送GET请求到: {chapter_url}")
            print(f"📦 参数: bookId={book_id}")
            
            response = requests.get(chapter_url, params=params, headers=headers, timeout=10)
            print(f"📥 响应状态码: {response.status_code}")
            
            if response.status_code == 200:
                result = response.json()
                print(f"📄 响应数据: {result}")
                
                if result.get('code') == 0 and 'data' in result:
                    chapters = []
                    data = result['data']
                    
                    if 'chapterListWithVolume' in data:
                        volumes = data['chapterListWithVolume']
                        print(f"📚 找到 {len(volumes)} 个卷")
                        
                        for volume_idx, volume in enumerate(volumes):
                            print(f"📖 处理第 {volume_idx + 1} 卷")
                            if isinstance(volume, list) and len(volume) > 0:
                                # 如果volume是列表，直接遍历章节
                                for i, chapter in enumerate(volume):
                                    try:
                                        chapter_info = {
                                            'chapter_id': str(chapter.get('itemId', f'chapter_{i+1}')),
                                            'chapter_title': chapter.get('title', f'第{i+1}章'),
                                            'chapter_url': chapter.get('url', ''),
                                            'duration': chapter.get('duration', 0),
                                            'is_downloaded': False,
                                            'source': 'fanqie'
                                        }
                                        if chapter_info['chapter_id']:
                                            chapters.append(chapter_info)
                                            print(f"✅ 添加章节: {chapter_info['chapter_title']}")
                                    except Exception as e:
                                        print(f"⚠️ 解析章节项失败: {e}")
                                        continue
                            elif isinstance(volume, dict) and 'chapterList' in volume:
                                # 如果volume是字典，查找chapterList
                                chapter_list = volume['chapterList']
                                for i, chapter in enumerate(chapter_list):
                                    try:
                                        chapter_info = {
                                            'chapter_id': str(chapter.get('itemId', f'chapter_{i+1}')),
                                            'chapter_title': chapter.get('title', f'第{i+1}章'),
                                            'chapter_url': chapter.get('url', ''),
                                            'duration': chapter.get('duration', 0),
                                            'is_downloaded': False,
                                            'source': 'fanqie'
                                        }
                                        if chapter_info['chapter_id']:
                                            chapters.append(chapter_info)
                                            print(f"✅ 添加章节: {chapter_info['chapter_title']}")
                                    except Exception as e:
                                        print(f"⚠️ 解析章节项失败: {e}")
                                        continue
                    
                    print(f"✅ 番茄官方API获取章节完成，找到 {len(chapters)} 个章节")
                    return chapters
                else:
                    print(f"❌ 番茄官方API返回错误: {result}")
                    return []
            else:
                print(f"❌ 番茄官方API请求失败: {response.status_code}")
                print(f"错误响应: {response.text}")
                return []
                
        except Exception as e:
            print(f"❌ 番茄官方API异常: {e}")
            return []
    
    def get_chapters_cenguigui(self, book_id: str) -> List[Dict[str, Any]]:
        """使用cenguigui.cn API获取章节列表"""
        try:
            print("📚 使用cenguigui.cn API获取章节...")
            
            # cenguigui.cn章节API
            chapter_url = "https://api.cenguigui.cn/api/tomato/api/all_items.php"
            params = {"book_id": book_id}
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                'Accept': 'application/json, text/plain, */*',
                'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8'
            }
            
            print(f"📤 发送GET请求到: {chapter_url}")
            print(f"📦 参数: book_id={book_id}")
            
            response = requests.get(chapter_url, params=params, headers=headers, timeout=10)
            print(f"📥 响应状态码: {response.status_code}")
            
            if response.status_code == 200:
                result = response.json()
                print(f"📄 响应数据: {result}")
                
                chapters = []
                
                # 根据API响应结构解析
                if isinstance(result, list):
                    # 如果直接返回章节列表
                    for i, item in enumerate(result):
                        try:
                            chapter_info = {
                                'chapter_id': str(item.get('item_id', item.get('id', f'chapter_{i+1}'))),
                                'chapter_title': item.get('title', item.get('name', f'第{i+1}章')),
                                'chapter_url': item.get('url', ''),
                                'duration': item.get('duration', item.get('time', 0)),
                                'is_downloaded': False,
                                'source': 'fanqie'
                            }
                            if chapter_info['chapter_id']:
                                chapters.append(chapter_info)
                        except Exception as e:
                            print(f"⚠️ 解析章节项失败: {e}")
                            continue
                            
                elif isinstance(result, dict):
                    # 如果返回的是对象，查找章节数据
                    if 'data' in result:
                        data = result['data']
                        if 'items' in data:
                            items = data['items']
                        elif 'chapters' in data:
                            items = data['chapters']
                        elif 'list' in data:
                            items = data['list']
                        else:
                            items = data
                            
                        if isinstance(items, list):
                            for i, item in enumerate(items):
                                try:
                                    chapter_info = {
                                        'chapter_id': str(item.get('item_id', item.get('id', item.get('chapter_id', f'chapter_{i+1}')))),
                                        'chapter_title': item.get('title', item.get('name', item.get('chapter_title', f'第{i+1}章'))),
                                        'chapter_url': item.get('url', ''),
                                        'duration': item.get('duration', item.get('time', 0)),
                                        'is_downloaded': False,
                                        'source': 'fanqie'
                                    }
                                    if chapter_info['chapter_id']:
                                        chapters.append(chapter_info)
                                except Exception as e:
                                    print(f"⚠️ 解析章节项失败: {e}")
                                    continue
                
                print(f"✅ cenguigui.cn API获取章节完成，找到 {len(chapters)} 个章节")
                return chapters
            else:
                print(f"❌ cenguigui.cn API请求失败: {response.status_code}")
                print(f"错误响应: {response.text}")
                return []
                
        except Exception as e:
            print(f"❌ cenguigui.cn API异常: {e}")
            return []
    
    def download_audio(self, book_id: str, chapter_id: str, output_path: str, voice_config: Optional[Dict[str, Any]] = None) -> bool:
        """下载音频文件 - 基于EXE完整流程文档"""
        try:
            print(f"🔍 下载番茄畅听音频: {book_id}/{chapter_id}")
            
            # 如果没有提供音色配置，使用默认配置
            if not voice_config:
                voice_config = {
                    "name": "AI甜美少女音",
                    "tone_id": "1",
                    "is_real_person": "0",
                    "ai_voice_id": "sweet_girl"
                }
            
            # 使用EXE文档中的音频API
            audio_url = self.get_audio_url_exe_api(chapter_id, voice_config)
            if not audio_url:
                print("❌ 无法获取音频URL")
                return False
            
            # 下载音频文件
            return self.download_audio_file(audio_url, output_path)
            
        except Exception as e:
            print(f"❌ 下载番茄畅听音频失败: {e}")
            return False
    
    def get_audio_url_exe_api(self, chapter_id: str, voice_config: Dict[str, Any]) -> Optional[str]:
        """使用EXE文档中的音频API获取音频URL，优先使用新API"""
        try:
            print(f"🎵 获取音频URL: {chapter_id}")
            
            # 首先尝试使用新的API（优先级最高）
            new_api_url = f"https://api.cenguigui.cn/api/tomato/changdunovel/?id={chapter_id}"
            print(f"📤 尝试新API: {new_api_url}")
            
            response = requests.get(new_api_url, timeout=15)
            
            if response.status_code == 200:
                result = response.json()
                print(f"📄 新API响应: {result}")
                
                if result.get('code') == 200 and 'data' in result:
                    audio_url = result['data'].get('url')
                    if audio_url:
                        print(f"✅ 通过新API获取音频URL成功: {audio_url[:100]}...")
                        return audio_url
                    else:
                        print(f"❌ 新API未返回有效音频URL")
            else:
                print(f"❌ 新API请求失败: {response.status_code}")
            
            # 如果新API失败，回退到原始API
            print(f"🔄 回退到原始API...")
            import time
            
            # 构建请求参数 - 基于EXE文档
            params = {
                "item_ids": chapter_id,
                "pv_player": "-1",
                "aid": "3040",
                "_rticket": str(int(time.time() * 1000))
            }
            
            # 根据音色类型添加不同参数
            if voice_config.get("is_real_person") == "1":
                # 真人音色参数
                params.update({
                    "tone_id": voice_config["tone_id"],
                    "is_real_person": "1",
                    "real_person_voice": "1"
                })
            else:
                # AI音色参数
                params.update({
                    "tone_id": voice_config["tone_id"],
                    "is_real_person": "0",
                    "ai_voice_id": voice_config.get("ai_voice_id", "")
                })
            
            # 请求头 - 基于EXE文档
            headers = {
                'User-Agent': 'com.xs.fm/608 (Linux; U; Android 9; zh_CN; 2210132C; Build/PQ3A.190605.07021633;tt-ok/3.12.13.17)',
                'Accept': '*/*',
                'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
                'Connection': 'keep-alive'
            }
            
            print(f"📤 发送音频请求到: {self.config.audio_url}")
            print(f"📦 音频参数: {params}")
            
            response = requests.get(
                self.config.audio_url,
                params=params,
                headers=headers,
                timeout=15
            )
            
            print(f"📥 音频响应状态码: {response.status_code}")
            
            if response.status_code == 200:
                result = response.json()
                print(f"📄 音频响应数据: {result}")
                
                if result.get('code') == 0 and 'data' in result:
                    data = result['data']
                    if isinstance(data, list) and len(data) > 0:
                        audio_info = data[0]
                        audio_url = audio_info.get('main_url')
                        is_encrypt = audio_info.get('is_encrypt', True)
                        
                        if audio_url and not is_encrypt:
                            print(f"✅ 获取音频URL成功: {audio_url[:100]}...")
                            return audio_url
                        elif audio_url and is_encrypt:
                            print(f"⚠️ 音频已加密，无法直接下载")
                            return None
                        else:
                            print(f"❌ 未返回有效音频URL")
                            return None
                    else:
                        print(f"❌ 音频数据为空")
                        return None
                else:
                    print(f"❌ 音频API返回错误: {result.get('message', '未知错误')}")
                    return None
            else:
                print(f"❌ 音频API请求失败: {response.status_code}")
                print(f"错误响应: {response.text}")
                return None
                
        except Exception as e:
            print(f"❌ 获取音频URL异常: {e}")
            return None
    
    def download_audio_file(self, audio_url: str, output_path: str) -> bool:
        """下载音频文件到指定路径"""
        try:
            print(f"📥 开始下载音频文件: {audio_url[:100]}...")
            
            # 请求头
            headers = {
                'User-Agent': 'com.xs.fm/608 (Linux; U; Android 9; zh_CN; 2210132C; Build/PQ3A.190605.07021633;tt-ok/3.12.13.17)',
                'Accept': '*/*',
                'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
                'Connection': 'keep-alive'
            }
            
            response = requests.get(audio_url, headers=headers, stream=True, timeout=30)
            response.raise_for_status()
            
            # 确保输出目录存在
            output_dir = Path(output_path).parent
            output_dir.mkdir(parents=True, exist_ok=True)
            
            # 下载并保存文件
            with open(output_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
            
            print(f"✅ 音频文件下载完成: {output_path}")
            return True
            
        except Exception as e:
            print(f"❌ 下载音频文件失败: {e}")
            return False
    
    def is_available(self) -> bool:
        """检查平台是否可用"""
        return True  # 番茄畅听平台独立可用
    
    def get_platform_info(self) -> Dict[str, Any]:
        """获取平台信息"""
        return {
            "name": self.platform_name,
            "search_url": self.config.search_url,
            "chapters_url": self.config.chapters_url,
            "audio_url": self.config.audio_url,
            "available": self.is_available(),
            "features": {
                "search": True,      # 基于EXE文档实现
                "download": True,    # 基于EXE文档实现
                "chapters": True,    # 基于EXE文档实现
                "voice_selection": True,  # 支持27种音色
                "details": False     # 待实现
            },
            "voice_count": 27,  # 支持27种音色
            "api_version": "EXE完整流程文档v1.0"
        }

def create_fanqie_platform() -> FanqiePlatform:
    """创建番茄畅听平台实例"""
    return FanqiePlatform()

# 测试函数
def test_fanqie_platform():
    """测试番茄畅听平台"""
    print("🚀 开始测试番茄畅听平台...")
    
    platform = create_fanqie_platform()
    info = platform.get_platform_info()
    
    print(f"📱 平台信息: {info}")
    
    # 测试搜索
    books = platform.search_books("斗罗大陆")
    print(f"🔍 搜索结果: {len(books)} 本书")
    
    for book in books[:3]:  # 显示前3本书
        print(f"  📖 {book.title} - {book.author}")

if __name__ == "__main__":
    test_fanqie_platform()
