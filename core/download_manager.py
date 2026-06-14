#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import requests
import threading
from pathlib import Path
from typing import Dict, Optional
from core.cookie_manager import CookieManager


class DownloadManager:
    """下载管理器"""
    
    def __init__(self):
        self.cookie_manager = CookieManager()
        self.download_dir = self.cookie_manager.get_download_dir()
        self.active_downloads = {}  # 存储正在进行的下载任务
        self.download_lock = threading.Lock()
        
    def set_download_dir(self, download_dir: str):
        """设置下载目录"""
        self.download_dir = download_dir
        # 确保目录存在
        Path(download_dir).mkdir(parents=True, exist_ok=True)
        
    def get_download_dir(self) -> str:
        """获取下载目录"""
        return self.download_dir
        
    def check_vip_status(self) -> bool:
        """兼容旧调用：自用版不做授权或额度校验。"""
        print("✅ 自用版：下载不经过授权或额度校验")
        return True
    
    def download_audio(self, url: str, filename: str, platform: str = "未知平台", album_title: str = "", chapter_index: int = 0, quality: Optional[str] = None) -> Optional[str]:
        """下载音频文件（支持音质参数）"""
        try:
            print("✅ 自用版：开始下载（不进行授权或额度校验）")
            
            # 确保下载目录存在
            Path(self.download_dir).mkdir(parents=True, exist_ok=True)
            
            # 构造完整路径
            safe_filename = self._sanitize_filename(filename)
            
            # 如果有专辑标题,创建专辑文件夹
            if album_title:
                safe_album_title = self._sanitize_filename(album_title)
                if self.cookie_manager.get_cookie('organize_by_platform_enabled') == 'true':
                    album_dir = os.path.join(self.download_dir, self._sanitize_filename(platform or "未知平台"), safe_album_title)
                else:
                    album_dir = os.path.join(self.download_dir, safe_album_title)
                Path(album_dir).mkdir(parents=True, exist_ok=True)
                
                # 检查是否启用分章节保存
                split_enabled = self.cookie_manager.get_cookie('split_chapters_enabled') == 'true'
                
                if split_enabled and chapter_index > 0:
                    # 启用分章节保存 - 创建章节范围文件夹
                    # 获取每个文件夹的章节数量
                    chapters_per_folder_str = self.cookie_manager.get_cookie('chapters_per_folder')
                    if chapters_per_folder_str and chapters_per_folder_str.isdigit():
                        chapters_per_folder = int(chapters_per_folder_str)
                    else:
                        chapters_per_folder = 200  # 默认200章节
                    
                    # 计算应该放在哪个文件夹（根据用户设置的章节数）
                    folder_start = ((chapter_index - 1) // chapters_per_folder) * chapters_per_folder + 1
                    folder_end = folder_start + chapters_per_folder - 1
                    folder_name = f"{folder_start}-{folder_end}章"
                    
                    # 创建章节范围文件夹
                    chapter_range_dir = os.path.join(album_dir, folder_name)
                    Path(chapter_range_dir).mkdir(parents=True, exist_ok=True)
                    
                    full_path = os.path.join(chapter_range_dir, safe_filename)
                    print(f"📥 开始下载: {filename}")
                    print(f"   保存到: {album_title}/{folder_name}/{safe_filename}")
                else:
                    # 不分章节,但仍然保存到专辑文件夹
                    full_path = os.path.join(album_dir, safe_filename)
                    print(f"📥 开始下载: {filename}")
                    print(f"   保存到: {album_title}/{safe_filename}")
            else:
                # 没有专辑标题,直接保存到下载目录
                full_path = os.path.join(self.download_dir, safe_filename)
                print(f"📥 开始下载: {filename}")
                print(f"   保存路径: {full_path}")
            
            # 如果没有提供quality参数，从cookie获取用户设置的音质
            if quality is None:
                audio_format = self.cookie_manager.get_cookie('audio_format') or 'M4A'
                audio_quality = self.cookie_manager.get_cookie('audio_quality') or '64K'
                quality = f"{audio_format} {audio_quality}"
                print(f"   🎧 使用设置中的音质: {quality}")
            
            # 根据平台选择下载方式
            if platform == "喜马拉雅":
                # 调用喜马拉雅管理器的下载方法（会传递quality参数）
                from .ximalaya_manager import XimalayaManager
                xm_manager = XimalayaManager()
                success = xm_manager.download_audio(url, full_path, quality)
                return full_path if success else None
            
            # 其他平台使用原有下载逻辑
            # 设置请求头
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            
            # 发送请求下载文件
            response = requests.get(url, headers=headers, timeout=30, stream=True)
            response.raise_for_status()
            
            # 保存文件
            with open(full_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=262144):
                    if chunk:
                        f.write(chunk)
            
            # 检查文件大小
            file_size = os.path.getsize(full_path)
            if file_size > 1024:  # 大于1KB认为下载成功
                print(f"✅ 下载成功: {filename} ({file_size // 1024}KB)")
                return full_path
            else:
                print(f"❌ 下载失败: {filename} (文件太小)")
                os.remove(full_path)  # 删除无效文件
                return None
                
        except Exception as e:
            print(f"❌ 下载失败: {filename} - {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def _sanitize_filename(self, filename: str) -> str:
        """清理文件名，移除非法字符"""
        # 移除或替换非法字符
        illegal_chars = ['<', '>', ':', '"', '/', '\\', '|', '?', '*']
        for char in illegal_chars:
            filename = filename.replace(char, '_')
        
        # 限制文件名长度
        if len(filename) > 150:
            filename = filename[:150]
            
        # 确保文件名不为空
        if not filename:
            filename = "未命名音频"
            
        # 添加默认扩展名（如果需要）
        if not os.path.splitext(filename)[1]:
            filename += ".m4a"
            
        return filename
        
    def download_chapter(self, chapter_info: Dict, album_title: str, platform: str = "未知平台") -> Optional[str]:
        """下载章节"""
        try:
            print("✅ 自用版：开始下载（不进行授权或额度校验）")
            
            # 获取音频URL（这里需要根据实际实现调整）
            # 这只是一个示例，实际实现需要根据平台获取真实的音频URL
            audio_url = chapter_info.get('url', '')
            if not audio_url:
                print(f"❌ 章节没有音频URL: {chapter_info.get('title', '未知章节')}")
                return None
            
            # 构造文件名
            chapter_title = chapter_info.get('title', '未知章节')
            filename = f"{album_title}_{chapter_title}.m4a"
            
            # 下载文件
            return self.download_audio(audio_url, filename, platform)
            
        except Exception as e:
            print(f"❌ 下载章节失败: {e}")
            return None
