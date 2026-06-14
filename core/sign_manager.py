# -*- coding: utf-8 -*-
"""
Sign管理器 - 用于获取和验证dragonlongzhu.cn API的sign参数
"""

import requests
import time
import json
import os
from typing import Optional, Dict, Any


class SignManager:
    """Sign参数管理器"""
    
    def __init__(self):
        self.sign_api_url = "https://api5.novelfm.com/activity/carrier_flow/mobile/get_sign/"
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
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Host": "api5.novelfm.com",
            "Connection": "keep-alive"
        }
        self.cached_sign = None
        self.sign_expire_time = 0
        self.backup_signs = [
            "MCwCFA7hjHrq059XiwgK81k3GlEh6ZFqAhQUBklkG7X3ElxUXElpAdc7KczNKQ==",
            "MCwCFHbvoo3xoW+4uEk3JqF/nnNAU7BLAhRf6qnRTpybm2RYiQUuInIN/CaBbQ=="
        ]
    
    def generate_timestamp(self) -> str:
        """生成当前时间戳"""
        return str(int(time.time() * 1000))
    
    def get_sign(self, force_refresh: bool = False) -> Optional[str]:
        """获取sign参数"""
        current_time = time.time()
        
        # 如果缓存有效且不强制刷新，返回缓存的sign
        if not force_refresh and self.cached_sign and current_time < self.sign_expire_time:
            print(f"🔄 使用缓存的sign: {self.cached_sign[:50]}...")
            return self.cached_sign
        
        # 生成新的时间戳
        params = self.base_params.copy()
        params["_rticket"] = self.generate_timestamp()
        
        try:
            print(f"🔐 正在获取新的sign...")
            response = requests.get(
                self.sign_api_url,
                params=params,
                headers=self.headers,
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                if 'sign' in data and data['sign']:
                    self.cached_sign = data['sign']
                    # 设置过期时间（30分钟后）
                    self.sign_expire_time = current_time + 1800
                    print(f"✅ 获取到新的sign: {self.cached_sign[:50]}...")
                    return self.cached_sign
                else:
                    print(f"❌ API返回空sign: {data}")
                    return None
            else:
                print(f"❌ 获取sign失败: HTTP {response.status_code}")
                return None
                
        except Exception as e:
            print(f"❌ 获取sign异常: {e}")
            return None
    
    def validate_sign(self, sign: str, test_chapter_id: str = "7249948092202975784") -> bool:
        """验证sign是否有效"""
        if not sign:
            return False
        
        test_url = f"https://api.dragonlongzhu.cn/api/tingshu_fanqie.php?video_id={test_chapter_id}&type=mp3&sign={sign}"
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8'
        }
        
        try:
            print(f"🔍 验证sign有效性...")
            # 使用GET请求而不是HEAD，因为HEAD可能被重定向
            response = requests.get(test_url, headers=headers, timeout=10, allow_redirects=True)
            
            if response.status_code == 200:
                content_type = response.headers.get('content-type', '')
                content_length = response.headers.get('content-length', '0')
                
                # 检查是否是音频文件
                if 'audio' in content_type or 'video' in content_type:
                    file_size = int(content_length) if content_length.isdigit() else len(response.content)
                    print(f"✅ Sign验证成功: {file_size/1024/1024:.2f} MB, content-type={content_type}")
                    return True
                elif 'application/octet-stream' in content_type:
                    # 有些音频文件可能被识别为二进制流
                    file_size = int(content_length) if content_length.isdigit() else len(response.content)
                    if file_size > 1024:  # 大于1KB才认为是有效文件
                        print(f"✅ Sign验证成功: {file_size/1024/1024:.2f} MB (二进制流)")
                        return True
                else:
                    print(f"❌ Sign验证失败: 不是音频文件, content-type={content_type}")
                    return False
            elif response.status_code == 302:
                # 重定向可能表示需要不同的参数或sign已过期
                print(f"❌ Sign验证失败: HTTP 302 重定向 (可能sign已过期)")
                return False
            else:
                print(f"❌ Sign验证失败: HTTP {response.status_code}")
                return False
        except Exception as e:
            print(f"❌ Sign验证异常: {e}")
            return False
    
    def get_valid_sign(self) -> Optional[str]:
        """获取有效的sign"""
        # 尝试使用缓存的sign
        if self.cached_sign and self.validate_sign(self.cached_sign):
            return self.cached_sign
        
        # 获取新的sign
        new_sign = self.get_sign(force_refresh=True)
        if new_sign and self.validate_sign(new_sign):
            return new_sign
        
        # 如果新sign无效，尝试备用sign
        print(f"🔄 尝试备用sign...")
        for i, backup_sign in enumerate(self.backup_signs):
            print(f"🔍 测试备用sign {i+1}/{len(self.backup_signs)}...")
            if self.validate_sign(backup_sign):
                print(f"✅ 使用备用sign: {backup_sign[:50]}...")
                self.cached_sign = backup_sign
                # 备用sign也设置30分钟过期
                self.sign_expire_time = time.time() + 1800
                return backup_sign
        
        print("❌ 所有sign都无效")
        return None
    
    def get_audio_url(self, chapter_id: str, audio_type: str = "mp3") -> Optional[str]:
        """使用sign获取音频下载URL"""
        sign = self.get_valid_sign()
        if not sign:
            return None
        
        audio_url = f"https://api.dragonlongzhu.cn/api/tingshu_fanqie.php?video_id={chapter_id}&type={audio_type}&sign={sign}"
        print(f"🎵 生成音频URL: {audio_url[:100]}...")
        return audio_url
    
    def download_audio(self, chapter_id: str, save_path: str, audio_type: str = "mp3") -> bool:
        """下载音频文件"""
        audio_url = self.get_audio_url(chapter_id, audio_type)
        if not audio_url:
            return False
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8'
        }
        
        try:
            print(f"📥 开始下载音频: {save_path}")
            response = requests.get(audio_url, headers=headers, stream=True, timeout=30)
            response.raise_for_status()
            
            with open(save_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=262144):
                    if chunk:
                        f.write(chunk)
            
            file_size = os.path.getsize(save_path)
            print(f"✅ 音频下载成功: {save_path} ({file_size/1024/1024:.2f} MB)")
            return True
            
        except Exception as e:
            print(f"❌ 音频下载失败: {e}")
            return False


# 全局sign管理器实例
_sign_manager = None

def get_sign_manager() -> SignManager:
    """获取全局sign管理器实例"""
    global _sign_manager
    if _sign_manager is None:
        _sign_manager = SignManager()
    return _sign_manager


if __name__ == "__main__":
    # 测试代码
    import os
    
    sign_manager = SignManager()
    
    # 获取有效的sign
    valid_sign = sign_manager.get_valid_sign()
    
    if valid_sign:
        print(f"✅ 获得有效sign: {valid_sign}")
        
        # 测试下载音频
        chapter_id = "7249948092202975784"  # 三姐第1章
        save_path = "test_audio.mp3"
        
        if sign_manager.download_audio(chapter_id, save_path):
            print("✅ 测试下载成功")
        else:
            print("❌ 测试下载失败")
    else:
        print("❌ 无法获取有效sign")
