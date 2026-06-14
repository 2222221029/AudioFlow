# -*- coding: utf-8 -*-
"""
起点听书扫码登录与音频接口。

该文件按授权版打包程序中恢复出的 PYZ 模块逻辑重建，保留原类名和方法签名，
供现有 UI 扫码登录、搜索、详情、章节与播放地址获取流程调用。
"""
import base64
import json
import re
import time
import uuid
from pathlib import Path

import requests
import urllib3

from core.platform_config import download_dir

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class QrcodeLogin:
    """起点二维码登录。"""

    def __init__(self):
        self.base_url = "https://ptlogin.yuewen.com"
        self.session = requests.Session()
        self.uuid = str(uuid.uuid4())
        self.session_key = None
        self.user_agent = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36 Edg/141.0.0.0"
        )
        self._setup_headers()

    def _setup_headers(self):
        self.session.headers.update({
            "User-Agent": self.user_agent,
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Referer": "https://passport.qidian.com/",
            "Sec-CH-UA": '"Microsoft Edge";v="141", "Not?A_Brand";v="8", "Chromium";v="141"',
            "Sec-CH-UA-Mobile": "?0",
            "Sec-CH-UA-Platform": '"Windows"',
            "Sec-Fetch-Dest": "script",
            "Sec-Fetch-Mode": "no-cors",
            "Sec-Fetch-Site": "cross-site",
        })
        self.session.cookies.set("ywlt", "19", domain=".yuewen.com")
        self.session.cookies.set("ywbackurl_10", "https%3A%2F%2Fmy.qidian.com%2F", domain=".yuewen.com")

    def _generate_callback(self):
        return f"jQuery{int(time.time() * 1000)}_{int(time.time() * 1000000) % 1000000}"

    @staticmethod
    def _unwrap_jsonp(text, callback_name):
        text = text.strip()
        prefix = f"{callback_name}("
        if text.startswith(prefix):
            text = text[len(prefix):]
        if text.endswith(")"):
            text = text[:-1]
        return text

    def get_qrcode(self):
        print("[*] 正在获取二维码...")
        callback = self._generate_callback()
        params = {
            "callback": callback,
            "appId": "10",
            "areaId": "1",
            "source": "",
            "returnurl": "https://my.qidian.com/",
            "version": "",
            "imei": "",
            "qimei": "",
            "target": "top",
            "ticket": "0",
            "autotime": "30",
            "jumpdm": "qidian",
            "ajaxdm": "",
            "auto": "1",
            "sdkversion": "",
            "method": "LoginV1.qrCodeCallback",
            "uuid": self.uuid,
            "pageId": "",
            "bookId": "",
            "chapterId": "",
            "format": "jsonp",
            "_": str(int(time.time() * 1000)),
        }

        try:
            response = self.session.get(
                f"{self.base_url}/login/qrcode",
                params=params,
                timeout=15,
                verify=True,
            )
            response.encoding = "utf-8"
            data = json.loads(self._unwrap_jsonp(response.text, "LoginV1.qrCodeCallback"))

            if data.get("code") == 0 and data.get("data", {}).get("image"):
                image_base64 = data["data"]["image"]
                if "," in image_base64:
                    image_base64 = image_base64.split(",", 1)[1]

                image_data = base64.b64decode(image_base64)
                Path("qrcode.png").write_bytes(image_data)
                self.session_key = data["data"].get("sessionKey")

                print("[+] ✅ 二维码已保存到 qrcode.png")
                print("[+] SessionKey 已获取")
                print("[+] ⏰ 二维码有效期: 30秒，请立即用手机扫码!")
                return self.uuid

            print(f"[-] 获取二维码失败: {data}")
            return None
        except Exception as exc:
            print(f"[-] 异常: {exc}")
            return None

    def get_ck(self, max_wait=120):
        print(f"\n[*] 请扫描二维码，将在 {max_wait} 秒内检查...")
        if not self.session_key:
            print("[-] 错误：没有sessionKey")
            return None

        start_time = time.time()
        check_count = 0

        while time.time() - start_time < max_wait:
            check_count += 1
            callback = self._generate_callback()
            params = {
                "callback": callback,
                "appId": "10",
                "areaId": "1",
                "source": "",
                "returnurl": "https://my.qidian.com/",
                "version": "",
                "imei": "",
                "qimei": "",
                "target": "top",
                "ticket": "0",
                "autotime": "30",
                "jumpdm": "qidian",
                "ajaxdm": "",
                "auto": "1",
                "sdkversion": "",
                "method": "LoginV1.qrCodeLoginCallback",
                "qrcode": self.session_key,
                "format": "jsonp",
                "_": str(int(time.time() * 1000)),
            }

            try:
                response = self.session.get(
                    f"{self.base_url}/login/qrcodelogin",
                    params=params,
                    timeout=15,
                    verify=True,
                )
                response.encoding = "utf-8"
                data = json.loads(self._unwrap_jsonp(response.text, "LoginV1.qrCodeLoginCallback"))

                code = data.get("code")
                scan_status = data.get("scanStatus")
                elapsed = int(time.time() - start_time)

                if code == 0 and scan_status == "5":
                    print("\n[+] ✅ 扫码成功！")
                    url_302 = data.get("data", {}).get("302url")
                    print("[*] 正在获取 Cookie...")
                    return self._process_sublogin(url_302)

                if code == -1 or scan_status in (None, "", "0", "1", "2", "3", "4"):
                    print(f"\r[*] 检查 #{check_count}: 等待中... ({elapsed}s)", end="", flush=True)
                else:
                    print(f"\n[*] 状态: code={code}, scanStatus={scan_status}")

            except Exception as exc:
                print(f"\n[-] 检查异常: {exc}")

            time.sleep(2)

        print("\n[-] ❌ 超时: 未能在规定时间内获取Cookie")
        return None

    def _process_sublogin(self, url_302):
        if not url_302:
            print("[-] 缺少登录跳转地址")
            return None

        try:
            response = self.session.get(
                url_302,
                timeout=15,
                allow_redirects=True,
                verify=True,
            )
            response.encoding = "utf-8"
            cookies = self.session.cookies.get_dict()
            key_cookies = ("ywkey", "ywguid", "ywOpenId")

            if all(cookies.get(key) for key in key_cookies):
                print("[+] ✅ 成功获取认证信息!")
                for key in key_cookies:
                    value = cookies.get(key, "")
                    print(f"    {key}: {value[:20]}..." if value else f"    {key}: 空")
                print("[+] ✅ 登录成功！")
                return cookies

            print(f"[-] 登录后 Cookie 不完整，当前字段: {list(cookies.keys())}")
            return cookies or None
        except Exception as exc:
            print(f"[-] 处理登录异常: {exc}")
            return None


class QidianAudioSystem:
    """起点听书音频接口。"""

    def __init__(self, cookies_dict=None):
        self.session = requests.Session()
        self.base_url = "https://qdcg.qidian.com"
        self.session.verify = False
        self.cookies_dict = cookies_dict or {}
        self.headers = {
            "Platform": "10",
            "AppId": "50",
            "AreaId": "501000",
            "YwGuid": self.cookies_dict.get("ywguid", ""),
            "YwKey": self.cookies_dict.get("ywkey", ""),
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36 "
                "MicroMessenger/7.0.20.1781(0x6700143B) NetType/WIFI "
                "MiniProgramEnv/Windows WindowsWechat/WMPF"
            ),
        }
        self.download_dir = download_dir() / "起点听书"

    def search(self, keyword, site=3, page_index=1, page_size=50):
        print(f"\n🔍 搜索起点听书: {keyword}")
        print(f"   站点: {site}, 页码: {page_index}, 每页: {page_size}")

        url = f"{self.base_url}/api/search/list"
        params = {
            "key": keyword,
            "pageIndex": page_index,
            "pageSize": page_size,
            "site": site,
            "model": 1,
        }

        try:
            response = self.session.get(
                url,
                params=params,
                headers=self.headers,
                cookies=self.cookies_dict,
                timeout=10,
            )
            data = response.json()

            if data.get("Result") == 0:
                items = data.get("Data", {}).get("items", [])
                print(f"✅ 搜索成功，共 {len(items)} 条结果")
                for index, item in enumerate(items, 1):
                    book_name = item.get("bookName", "未知")
                    author_name = item.get("authorName", "未知")
                    category_name = item.get("categoryName", "未知")
                    book_id = item.get("bookId", "未知")
                    print(f"  {index}. {book_name} | {author_name} | {category_name} | {book_id}")
                return items

            print(f"❌ 搜索失败: {data.get('Message')}")
            return []
        except Exception as exc:
            print(f"❌ 搜索错误: {exc}")
            return []

    def get_audio_detail(self, adid):
        print(f"\n📖 获取音频详情: {adid}")
        url = f"{self.base_url}/api/audio/detail"
        params = {
            "adid": adid,
            "_csrfToken": "",
        }

        try:
            response = self.session.get(
                url,
                params=params,
                headers=self.headers,
                cookies=self.cookies_dict,
                timeout=10,
            )
            data = response.json()

            if data.get("Result") == 0:
                audio_data = data.get("Data")
                print("✅ 获取成功!")
                if isinstance(audio_data, dict):
                    print(f"   主播: {audio_data.get('AnchorName', '未知')}")
                    print(f"   总章节: {audio_data.get('AllAudioChapters', '未知')}")
                return audio_data

            print(f"❌ 获取失败: {data.get('Message')}")
            return None
        except Exception as exc:
            print(f"❌ 详情错误: {exc}")
            return None

    def get_chapter_list(self, adid, page=1):
        url = f"{self.base_url}/api/audio/chapter-list"
        params = {
            "adid": adid,
            "page": page,
            "_csrfToken": "",
        }

        try:
            response = self.session.get(
                url,
                params=params,
                headers=self.headers,
                cookies=self.cookies_dict,
                timeout=10,
            )
            data = response.json()

            if data.get("Result") == 0:
                page_data = data.get("Data", {})
                chapters = page_data.get("Items", [])
                has_next = page_data.get("HasNext", False)
                return chapters, has_next

            print(f"❌ 获取章节失败: {data.get('Message')}")
            return [], False
        except Exception as exc:
            print(f"❌ 错误: {exc}")
            return [], False

    def get_play_url(self, adid, acid):
        url = f"{self.base_url}/api/audio/play"
        params = {
            "adid": adid,
            "acid": acid,
            "_csrfToken": "",
        }

        try:
            response = self.session.get(
                url,
                params=params,
                headers=self.headers,
                cookies=self.cookies_dict,
                timeout=10,
            )
            data = response.json()

            if data.get("Result") == 0:
                return data.get("Data", {}).get("AudioUrl")

            print(f"❌ 获取播放地址失败: {data.get('Message')}")
            return None
        except Exception as exc:
            print(f"❌ 播放地址错误: {exc}")
            return None

    def download_chapter(self, audio_url, chapter_name, idx):
        if not audio_url:
            print(f"❌ 第 {idx} 章缺少下载地址")
            return False

        try:
            head_response = self.session.head(
                audio_url,
                timeout=10,
                allow_redirects=False,
                verify=True,
            )
            if head_response.status_code != 200:
                print(f"❌ HEAD失败: {head_response.status_code}")
                return False

            response = self.session.get(
                audio_url,
                timeout=60,
                stream=True,
                allow_redirects=True,
                verify=True,
            )
            if response.status_code not in (200, 206):
                print(f"❌ 下载失败: {response.status_code}")
                return False

            safe_name = "".join(c if c.isalnum() or c in ".-_" else "_" for c in chapter_name)
            safe_name = re.sub(r"_+", "_", safe_name).strip("_") or f"Chapter_{idx}"
            self.download_dir.mkdir(parents=True, exist_ok=True)
            filepath = self.download_dir / f"{idx:04d}_{safe_name}.mp3"

            with open(filepath, "wb") as file_obj:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        file_obj.write(chunk)

            size = filepath.stat().st_size / 1048576
            print(f"✅ 下载完成: {filepath.name} ({size:.2f} MB)")
            return True
        except Exception as exc:
            print(f"❌ 下载错误: {exc}")
            return False

    def download_all(self, keyword, start_idx=1, end_idx=None):
        books = self.search(keyword, site=3)
        if not books:
            return False

        book = books[0]
        adid = book.get("bookId")
        book_name = book.get("bookName", "Unknown")
        detail = self.get_audio_detail(adid)
        if not detail:
            return False

        all_chapters = []
        page = 1
        while True:
            chapters, has_next = self.get_chapter_list(adid, page)
            if not chapters:
                break
            all_chapters.extend(chapters)
            if not has_next:
                break
            page += 1

        if not all_chapters:
            print("❌ 没有找到章节")
            return False

        safe_book_name = "".join(c if c.isalnum() or c in ".-_" else "_" for c in book_name)
        safe_book_name = re.sub(r"_+", "_", safe_book_name).strip("_") or "Unknown"
        old_download_dir = self.download_dir
        self.download_dir = old_download_dir / safe_book_name
        self.download_dir.mkdir(parents=True, exist_ok=True)

        end_idx = min(end_idx or len(all_chapters), len(all_chapters))
        selected_chapters = all_chapters[start_idx - 1:end_idx]
        success_count = 0
        failed_list = []

        try:
            for offset, chapter in enumerate(selected_chapters):
                real_idx = start_idx + offset
                acid = chapter.get("Acid")
                chapter_name = chapter.get("AudioChapterName", f"Chapter_{real_idx}")
                audio_url = self.get_play_url(adid, acid)
                if not audio_url:
                    failed_list.append(chapter_name)
                    continue

                if self.download_chapter(audio_url, chapter_name, real_idx):
                    success_count += 1
                else:
                    failed_list.append(chapter_name)

                time.sleep(0.2)
        finally:
            self.download_dir = old_download_dir

        print(f"\n📊 下载完成: 成功 {success_count}/{len(selected_chapters)}")
        if failed_list:
            print(f"❌ 失败章节: {failed_list}")
        return success_count > 0


def main():
    login = QrcodeLogin()
    if login.get_qrcode():
        cookies = login.get_ck()
        if cookies:
            print(json.dumps(cookies, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
