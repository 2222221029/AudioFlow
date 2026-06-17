#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LRTS manager backed by the Android app API.

The login flow is SMS based:
1. fetch a temporary token with a generated device ``imei``;
2. send a verification code to the phone number;
3. exchange phone + code for a persistent ``token``;
4. store ``imei`` + ``token`` as the project's LRTS credential.

The persisted value lives in the existing CookieManager ``lrts`` slot for
compatibility with the rest of the app, but it is JSON credentials rather than
a browser Cookie header.
"""

from __future__ import annotations

import base64
import html
import hashlib
import json
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests

try:
    from Crypto.Cipher import PKCS1_v1_5
    from Crypto.PublicKey import RSA
except ImportError:  # pragma: no cover - dependency is listed in requirements.
    PKCS1_v1_5 = None  # type: ignore
    RSA = None  # type: ignore


SALT = "vYCmm+6CFVykQk5w0wiUDliCQRA="
_LRTS_AUDIO_DEBUG_DONE = False
READ_HOST = "https://dapis.mting.info"
API_HOST = "https://dapi.mting.info"
RSA_PUB_B64 = (
    "MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQCTa5IO+9A0L6eIX+KtvM4o3zCRE0QXX/63Pdcp+"
    "ME4Px8LIpfNSHqhSMJW2jUsO6eCRGxbsvOnUqxM7uG4hbQfSqSSmaReaInT5DIlWpSzUtdm+"
    "BViIyqKi/1Z2buGWEb/ML836JiRY4WgcVOLWGpde3ZTddWvQ1Hm3bZ/+hGbswIDAQAB"
)
APP_HEADERS = {
    "User-Agent": "Android12/yyting/unknown/unknown/ch_yyting/257/8.0.1",
    "Accept-Encoding": "gzip,deflate,sdch",
    "ClientVersion": "8.0.1",
    "Referer": "yytingting.com",
}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        return default


_LRTS_AUDIO_CONCURRENCY = max(1, min(3, _env_int("LRTS_AUDIO_CONCURRENCY", 1)))
_RATE_SEMAPHORE = threading.Semaphore(_LRTS_AUDIO_CONCURRENCY)
_RATE_LOCK = threading.Lock()
_LAST_REQUEST_TIME = 0.0
_MIN_REQUEST_INTERVAL = max(0.5, _env_float("LRTS_MIN_REQUEST_INTERVAL", 1.2))
_Q_LOCK = threading.Lock()
_Q_COUNTERS: dict[str, int] = {}


class RateLimitError(Exception):
    def __init__(self, msg="download too frequently, please retry later"):
        super().__init__(msg)


class IllegalRequestError(Exception):
    def __init__(self, msg="illegal request, please retry after cooldown"):
        super().__init__(msg)


def calc_sc(path: str, params: dict[str, str]) -> str:
    if params:
        pairs = [f"{key}={params[key]}" for key in sorted(params.keys())]
        sign_str = f"{path}?{'&'.join(pairs)}{SALT}"
    else:
        sign_str = f"{path}{SALT}"
    return hashlib.md5(sign_str.encode("utf-8")).hexdigest().lower()


def build_device_info(imei: str) -> dict[str, Any]:
    return {
        "androidId": imei,
        "imei": imei,
        "imsi": "",
        "mac": "02:00:00:00:00:00",
        "serialNo": "",
        "deviceMd5": hashlib.md5(imei.encode()).hexdigest(),
        "key": uuid.uuid4().hex[:16],
        "nowTime": int(time.time() * 1000),
        "oaid": "",
        "oldImei": "",
        "umengId": "",
        "lrid": "",
    }


def rsa_encrypt_meta(device: dict[str, Any]) -> str:
    if RSA is None or PKCS1_v1_5 is None:
        raise RuntimeError("pycryptodome is required for LRTS SMS login")
    payload = json.dumps(device, separators=(",", ":"), ensure_ascii=False)
    key = RSA.import_key(base64.b64decode(RSA_PUB_B64))
    cipher = PKCS1_v1_5.new(key)
    chunks = []
    data = payload.encode("utf-8")
    for i in range(0, len(data), 117):
        chunks.append(cipher.encrypt(data[i:i + 117]))
    return base64.b64encode(b"".join(chunks)).decode("ascii")


def _to_int(value, default=0):
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def _norm_cover(url):
    value = str(url or "").strip()
    if value.startswith("//"):
        return "https:" + value
    return value


def _extract_list(data):
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    for key in ("list", "bookChapterList", "audioList", "chapterList", "data"):
        value = data.get(key)
        if isinstance(value, list):
            return value
    inner = data.get("data")
    if isinstance(inner, dict):
        return _extract_list(inner)
    return []


def _first(data: dict, *keys):
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return value
    return ""


def _credential_json(credential: dict[str, Any]) -> str:
    return json.dumps(credential, ensure_ascii=False, separators=(",", ":"))


def parse_lrts_credentials(value) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    text = str(value or "").strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        pass
    # Backward-compatible parser for old "token=...; imei=..." strings.
    out = {}
    for part in text.replace("\n", ";").split(";"):
        if "=" not in part:
            continue
        key, val = part.split("=", 1)
        key = key.strip()
        val = val.strip()
        if key and val:
            out[key] = val
    return out


def normalize_lrts_credentials(value) -> str:
    data = parse_lrts_credentials(value)
    if not data:
        return ""
    if data.get("token") and data.get("imei"):
        data.setdefault("auth_type", "lrts_app_sms")
    return _credential_json(data)


def credentials_from_login(phone: str, imei: str, login_resp: dict) -> dict[str, Any]:
    credential = {
        "auth_type": "lrts_app_sms",
        "phone": phone,
        "imei": imei,
        "token": login_resp.get("token", ""),
        "userId": login_resp.get("userId"),
        "account": login_resp.get("account", ""),
        "nickname": login_resp.get("nickname") or login_resp.get("nickName", ""),
        "uuid": login_resp.get("uuid", ""),
        "lrid": login_resp.get("lrid", ""),
        "loginKey": login_resp.get("loginKey", ""),
        "vipExpireTime": login_resp.get("vipExpireTime"),
        "subscribe": login_resp.get("subscribe"),
        "subscribeStatus": login_resp.get("subscribeStatus"),
        "status": login_resp.get("status"),
        "saved_at": int(time.time()),
    }
    return {k: v for k, v in credential.items() if v is not None and v != ""}


def _throttle_audio_request():
    global _LAST_REQUEST_TIME
    with _RATE_LOCK:
        now = time.time()
        wait = _MIN_REQUEST_INTERVAL - (now - _LAST_REQUEST_TIME)
        if wait > 0:
            time.sleep(wait)
        _LAST_REQUEST_TIME = time.time()


class LrtsAppClient:
    def __init__(self, imei: str | None = None, token: str = "", q: str = "0", nwt: str = "1", mode: str = "0"):
        self.imei = imei or uuid.uuid4().hex[:16]
        self.token = token
        self.q = q
        self.nwt = nwt
        self.mode = mode
        self.q_seq = 1
        self.session = requests.Session()
        self.session.headers.update(APP_HEADERS)
        self._last_path = ""

    def _next_q(self) -> str:
        key = f"{self.imei}:{self.token or 'guest'}"
        with _Q_LOCK:
            current = max(_Q_COUNTERS.get(key, 0), self.q_seq - 1) + 1
            _Q_COUNTERS[key] = current
            self.q_seq = current + 1
            return str(current)

    def _signed_params(self, business: dict[str, Any]) -> dict[str, str]:
        params = {key: str(value) for key, value in business.items() if value is not None}
        params.update({"imei": self.imei, "nwt": self.nwt, "q": self._next_q(), "mode": self.mode})
        if self.token:
            params["token"] = self.token
        path = urlparse(self._last_path).path
        params["sc"] = calc_sc(path, params)
        return params

    def get(self, host: str, path: str, params: dict[str, Any] | None = None) -> dict:
        self._last_path = path
        response = self.session.get(host + path, params=self._signed_params(params or {}), timeout=30)
        response.raise_for_status()
        return response.json()

    def fetch_temp_token(self) -> str:
        meta = rsa_encrypt_meta(build_device_info(self.imei))
        path = "/yyting/usercenter/tempToken.action"
        try:
            response = self.session.get(READ_HOST + path, params={"sc": calc_sc(path, {}), "meta": meta}, timeout=30)
            data = response.json() if response.text else {}
            token = data.get("token") or (data.get("data") or {}).get("token")
            if token:
                self.token = token
                return token
        except Exception as exc:
            print(f"[lrts] tempToken failed: {exc}")

        path = "/yyting/usercenter/AutoRegister.action"
        response = self.session.get(API_HOST + path, params={"meta": meta, "sc": calc_sc(path, {"meta": meta})}, timeout=30)
        data = response.json() if response.text else {}
        if data.get("status") == 0 and data.get("token"):
            self.token = data["token"]
            return self.token
        print(f"[lrts] AutoRegister status={data.get('status')} msg={data.get('msg')}")
        return ""

    def send_sms_code(self, phone: str, code_type: int = 15, login_key: str = "") -> dict:
        return self.get(API_HOST, "/yyting/usercenter/getVerifyCode.action", {
            "phoneNum": phone,
            "type": code_type,
            "loginKey": login_key,
        })

    def sms_login(self, phone: str, verify_code: str) -> dict:
        data = self.get(API_HOST, "/yyting/usercenter/ClientLogon.action", {
            "type": 2,
            "account": phone,
            "verifyCode": verify_code,
        })
        if data.get("status") == 0 and data.get("token"):
            self.token = data["token"]
        return data

    def search_batch(self, keyword: str, page: int = 1, page_size: int = 20) -> dict:
        return self.get(READ_HOST, "/yyting/search/searchBatch.action", {
            "type": 0,
            "keyWord": keyword,
            "pageNum": page,
            "pageSize": page_size,
            "searchOption": "",
        })

    def book_search(self, keyword: str, page: int = 1, page_size: int = 20) -> dict:
        return self.get(READ_HOST, "/yyting/bookclient/BookSearch.action", {
            "type": 0,
            "keyWord": keyword,
            "pageNum": page,
            "pageSize": page_size,
        })

    def search_album(self, keyword: str, page: int = 1, page_size: int = 20) -> dict:
        return self.get(READ_HOST, "/yyting/search/searchAlbum.action", {
            "keyWord": keyword,
            "pageNum": page,
            "pageSize": page_size,
        })

    def book_detail(self, book_id: int) -> dict:
        return self.get(READ_HOST, "/yyting/page/bookDetailPage.action", {"bookId": book_id})

    def album_detail(self, album_id: int) -> dict:
        return self.get(READ_HOST, "/yyting/page/ablumnDetailPage.action", {"ablumnId": album_id})

    def book_chapters(self, book_id: int, page: int = 1, sort_type: int = 0, is_up: int = 1) -> dict:
        return self.get(READ_HOST, "/yyting/bookclient/ClientGetBookResource.action", {
            "bookId": book_id,
            "pageNum": page,
            "pageSize": 50,
            "sortType": sort_type,
            "isUp": is_up,
        })

    def album_chapters(self, album_id: int, sort_type: int = 0) -> dict:
        return self.get(READ_HOST, "/yyting/snsresource/getAblumnAudios.action", {
            "ablumnId": album_id,
            "pageNum": 1,
            "pageSize": 10000,
            "sortType": sort_type,
        })

    def get_play_path(self, entity_type: int, entity_id: int, chapter_id: int, section: int = 1, op_type: int = 1) -> dict:
        params = {
            "entityType": entity_type,
            "entityId": entity_id,
            "id": chapter_id,
            "section": section,
            "opType": op_type,
            "lastPath": "",
            "generateFactor": "",
            "httpStatus": 0,
            "bizError": "",
        }
        # 码率档位开关：懒人 iOS(v3 接口)用 quality=3 拿高码率(320k)。先尝试在当前接口附加该参数
        # （签名 sc 会自动把它算进去）。环境变量 LRTS_AUDIO_QUALITY=3 启用。
        import os
        quality = os.getenv("LRTS_AUDIO_QUALITY")
        if quality:
            params["quality"] = quality
        return self.get(READ_HOST, "/yyting/gateway/getListenPath.action", params)

    def fetch_all_chapters(self, entity_type: int, entity_id: int) -> list[dict]:
        if entity_type == 2:
            return _extract_list(self.album_chapters(entity_id))
        all_items = []
        page = 1
        total = None
        while True:
            data = self.book_chapters(entity_id, page=page)
            if data.get("status") not in (0, None):
                raise RuntimeError(f"chapter list failed: status={data.get('status')} msg={data.get('msg')}")
            batch = _extract_list(data)
            if not batch:
                break
            all_items.extend(batch)
            total = data.get("sections") or total
            if total and len(all_items) >= int(total):
                break
            if len(batch) < 50:
                break
            page += 1
        return all_items


def lrts_send_sms_code(phone: str) -> dict:
    client = LrtsAppClient()
    client.fetch_temp_token()
    data = client.send_sms_code(phone)
    data["_imei"] = client.imei
    data["_token"] = client.token
    return data


def lrts_sms_login(phone: str, code: str, imei: str = "", temp_token: str = "") -> tuple[dict, str]:
    client = LrtsAppClient(imei=imei or None, token=temp_token or "")
    if not client.token:
        client.fetch_temp_token()
    data = client.sms_login(phone, code)
    credential = credentials_from_login(phone, client.imei, data) if data.get("status") == 0 else {}
    return data, normalize_lrts_credentials(credential)


class LRTSManager:
    def __init__(self):
        self.cookie_string = ""
        self.credentials: dict[str, Any] = {}
        self.session = requests.Session()
        self.session.headers.update(APP_HEADERS)
        self.last_chapter_warning = ""
        self._client: LrtsAppClient | None = None

    def set_cookie(self, cookie_string, is_server_cookie=False):
        credential = parse_lrts_credentials(cookie_string)
        self.credentials = credential
        self.cookie_string = normalize_lrts_credentials(credential) if credential else ""
        self._client = None
        if credential.get("token") and credential.get("imei"):
            print(f"[lrts] app credential set (imei={credential.get('imei')}, userId={credential.get('userId', '-')})")
        elif cookie_string:
            print("[lrts] ignored non-app LRTS credential; please login with SMS code")

    def clear_server_cookie(self):
        self.set_cookie("")

    def force_clear_server_cookie(self):
        self.set_cookie("")

    def _validate_svip_before_use(self):
        return False

    def _client_or_guest(self) -> LrtsAppClient:
        if self._client is not None:
            return self._client
        token = str(self.credentials.get("token") or "")
        imei = str(self.credentials.get("imei") or "") or None
        self._client = LrtsAppClient(imei=imei, token=token)
        self.session = self._client.session
        if not token:
            try:
                self._client.fetch_temp_token()
            except Exception as exc:
                print(f"[lrts] guest token failed: {exc}")
        return self._client

    def _parse_entity_ref(self, value, fallback_type=0) -> tuple[int, int]:
        text = str(value or "").strip()
        if text.startswith(("http://", "https://")):
            parsed = urlparse(text)
            qs = parse_qs(parsed.query)
            book_id = (qs.get("book") or qs.get("bookId") or qs.get("id") or [""])[0]
            match = re.search(r"/book/(\d+)", parsed.path or "")
            if not book_id and match:
                book_id = match.group(1)
            if book_id:
                # Web book pages use player-info type=2 even when share.do carries type=4.
                return 2, _to_int(book_id)
        if ":" in text:
            left, right = text.split(":", 1)
            if str(left).strip() == "4":
                left = "2"
            return _to_int(left, fallback_type), _to_int(right)
        return fallback_type, _to_int(text)

    def _entity_ref(self, entity_type, entity_id) -> str:
        return f"{_to_int(entity_type)}:{_to_int(entity_id)}"

    def _iter_search_items(self, data: dict):
        for block in (data, data.get("data") if isinstance(data, dict) else {}):
            if not isinstance(block, dict):
                continue
            for key in ("bookResult", "albumResult", "ablumnResult", "resourceResult", "list"):
                value = block.get(key)
                if isinstance(value, list):
                    yield from value
                elif isinstance(value, dict):
                    for nested in value.values():
                        if isinstance(nested, list):
                            yield from nested
        if isinstance(data.get("data"), list):
            yield from data["data"]

    def _item_entity(self, item: dict) -> tuple[int, int]:
        entity_type = _to_int(_first(item, "baseEntityType", "entityType", "type"), 0)
        entity_id = _first(item, "baseEntityId", "entityId", "bookId", "ablumnId", "id", "parentId")
        return entity_type, _to_int(entity_id)

    def search_books(self, keyword):
        entity_type, entity_id = self._parse_entity_ref(keyword, fallback_type=0)
        if entity_id and str(keyword or "").strip().startswith(("http://", "https://")):
            detail = self.get_book_detail(self._entity_ref(entity_type, entity_id)) or {}
            title = detail.get("title") or f"懒人听书 {entity_id}"
            return [{
                "id": self._entity_ref(entity_type, entity_id),
                "title": title,
                "author": detail.get("author") or "懒人听书",
                "platform": "懒人听书",
                "cover": detail.get("cover") or "",
                "plays": detail.get("plays") or 0,
                "episodes": detail.get("episodes") or 0,
                "status": detail.get("status") or "",
                "description": detail.get("description") or "",
                "category": detail.get("category") or "",
                "tags": [],
                "created_at": "",
                "updated_at": "",
                "_lrts_entity_type": entity_type,
                "_lrts_entity_id": entity_id,
            }]

        client = self._client_or_guest()
        responses = []
        for name in ("book_search", "search_batch", "search_album"):
            try:
                data = getattr(client, name)(keyword, page_size=50)
                print(f"[lrts] {name}: status={data.get('status')} msg={data.get('msg', '')}")
                responses.append(data)
                if data.get("status") == 0:
                    break
            except Exception as exc:
                print(f"[lrts] {name} failed: {exc}")

        books = []
        seen = set()
        for data in responses:
            for item in self._iter_search_items(data):
                if not isinstance(item, dict):
                    continue
                entity_type, entity_id = self._item_entity(item)
                if not entity_id:
                    continue
                ref = self._entity_ref(entity_type, entity_id)
                if ref in seen:
                    continue
                seen.add(ref)
                title = _first(item, "name", "bookName", "ablumnName", "entityName", "title")
                books.append({
                    "id": ref,
                    "title": title,
                    "author": _first(item, "author", "authorName", "anchorName", "nickname", "announcer") or "懒人听书",
                    "platform": "懒人听书",
                    "cover": _norm_cover(_first(item, "cover", "coverUrl", "coverPath", "bestCover", "pic")),
                    "plays": _to_int(_first(item, "plays", "playCount", "play")),
                    "episodes": _to_int(_first(item, "sections", "countTrack", "chapterCount", "audioCount")),
                    "status": "已完结" if _first(item, "isFinished", "finishState", "finished") else "连载中",
                    "description": _first(item, "desc", "description", "albumDesc"),
                    "category": _first(item, "category", "cateName", "categoryName"),
                    "tags": [],
                    "created_at": "",
                    "updated_at": "",
                    "_lrts_entity_type": entity_type,
                    "_lrts_entity_id": entity_id,
                })
        print(f"[lrts] search results: {len(books)}")
        return books

    def get_book_detail(self, book_id):
        client = self._client_or_guest()
        entity_type, entity_id = self._parse_entity_ref(book_id, fallback_type=0)
        try:
            data = client.album_detail(entity_id) if entity_type == 2 else client.book_detail(entity_id)
        except Exception as exc:
            print(f"[lrts] detail failed: {exc}")
            return self._fetch_web_book_detail(entity_id)
        info = data.get("data") if isinstance(data.get("data"), dict) else data
        if not isinstance(info, dict):
            return self._fetch_web_book_detail(entity_id)
        title = _first(info, "name", "bookName", "ablumnName", "entityName", "title") or f"book_{entity_id}"
        detail = {
            "id": self._entity_ref(entity_type, entity_id),
            "title": title,
            "author": _first(info, "author", "authorName", "anchorName", "nickname", "announcer") or "未知",
            "platform": "懒人听书",
            "cover": _norm_cover(_first(info, "cover", "coverUrl", "coverPath", "bestCover", "pic")),
            "plays": _to_int(_first(info, "plays", "playCount", "play")),
            "episodes": _to_int(_first(info, "sections", "countTrack", "chapterCount", "audioCount")),
            "status": "已完结" if _first(info, "isFinished", "finishState", "finished") else "连载中",
            "description": _first(info, "desc", "description", "albumDesc"),
            "category": _first(info, "category", "cateName", "categoryName"),
            "tags": [],
            "created_at": "",
            "updated_at": "",
        }
        if title == f"book_{entity_id}":
            return self._fetch_web_book_detail(entity_id) or detail
        return detail

    def _fetch_web_book_detail(self, book_id: int) -> dict | None:
        if not book_id:
            return None
        try:
            resp = requests.get(
                f"https://www.lrts.me/book/{book_id}",
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=20,
            )
            resp.raise_for_status()
            text = resp.text
        except Exception as exc:
            print(f"[lrts] web detail failed: {exc}")
            return None
        title = ""
        match = re.search(r"<title>(.*?)</title>", text, re.S | re.I)
        if match:
            title = html.unescape(re.sub(r"\s+", " ", match.group(1)).strip()).split("-有声小说下载", 1)[0]
        desc = ""
        match = re.search(r'<meta\s+name=["\']description["\']\s+content=["\'](.*?)["\']', text, re.S | re.I)
        if match:
            desc = html.unescape(re.sub(r"\s+", " ", match.group(1)).strip())
        cover = ""
        match = re.search(r'<img[^>]+(?:class=["\'][^"\']*book[^"\']*["\'][^>]+)?src=["\'](https?://[^"\']+)["\']', text, re.I)
        if match:
            cover = match.group(1)
        return {
            "id": self._entity_ref(2, book_id),
            "title": title or f"懒人听书 {book_id}",
            "author": "懒人听书",
            "platform": "懒人听书",
            "cover": _norm_cover(cover),
            "plays": 0,
            "episodes": 0,
            "status": "",
            "description": desc,
            "category": "",
            "tags": [],
            "created_at": "",
            "updated_at": "",
        }

    def get_chapters(self, book_id, max_chapters=None):
        client = self._client_or_guest()
        entity_type, entity_id = self._parse_entity_ref(book_id, fallback_type=0)
        self.last_chapter_warning = ""
        max_chapters = max(1000, min(200000, _to_int(max_chapters or os.getenv("LRTS_MAX_CHAPTERS"), 50000)))
        try:
            items = client.fetch_all_chapters(entity_type, entity_id)
        except Exception as exc:
            self.last_chapter_warning = str(exc)
            print(f"[lrts] chapters failed: {exc}")
            items = self._fetch_web_book_chapters(entity_id) if entity_id else []
            if items:
                entity_type = 2
                self.last_chapter_warning = ""
            else:
                return []
        if not items and entity_id:
            web_items = self._fetch_web_book_chapters(entity_id)
            if web_items:
                items = web_items
                entity_type = 2
        if len(items) > max_chapters:
            self.last_chapter_warning = f"懒人听书章节达到本地上限 {max_chapters} 章，可设置 LRTS_MAX_CHAPTERS 调整。"
            items = items[:max_chapters]

        chapters = []
        for idx, item in enumerate(items, 1):
            chapter_id = _first(item, "chapterId", "id", "sectionId")
            section = _to_int(_first(item, "section", "chapterSection"), idx)
            title = _first(item, "chapterName", "name", "title") or f"第{section}章"
            chapters.append({
                "id": str(chapter_id or section),
                "chapter_id": str(chapter_id or section),
                "title": str(title).replace("\ufeff", "").strip(),
                "order_num": section,
                "duration": _to_int(_first(item, "duration", "length", "playTime")),
                "is_paid": _to_int(_first(item, "isPaid", "is_paid", "payType", "feeType")) > 0,
                "_chapter_data": {
                    "entity_type": entity_type,
                    "entity_id": entity_id,
                    "section": section,
                    "id": chapter_id,
                    "chapter_id": chapter_id,
                },
            })
        print(f"[lrts] chapters loaded: {len(chapters)}")
        return chapters

    def _fetch_web_book_chapters(self, book_id: int, page_size: int = 50) -> list[dict]:
        if not book_id:
            return []
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Referer": f"https://www.lrts.me/book/{book_id}",
            "X-Requested-With": "XMLHttpRequest",
        })
        items = []
        page = 0
        while True:
            try:
                resp = session.get(f"https://www.lrts.me/ajax/book/{book_id}/{page}/{page_size}", timeout=20)
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:
                print(f"[lrts] web chapters failed page={page}: {exc}")
                break
            batch = (((data or {}).get("data") or {}).get("data") or [])
            if not batch:
                break
            for item in batch:
                if not isinstance(item, dict):
                    continue
                section = _to_int(item.get("section"), len(items) + 1)
                items.append({
                    "id": section,
                    "chapterId": section,
                    "section": section,
                    "chapterName": html.unescape(str(item.get("resName") or f"第{section}章")).strip(),
                    "fatherResId": item.get("fatherResId") or book_id,
                    "payType": item.get("payType"),
                    "isPaid": _to_int(item.get("payType")) > 0,
                    "_web_res_id": item.get("resId"),
                })
            page += 1
            if page > 2000:
                break
        if items:
            print(f"[lrts] web chapters loaded: {len(items)}")
        return items

    def get_audio_url(self, book_id, chapter_id, chapter_data=None):
        if chapter_data:
            nested = chapter_data.get("_chapter_data") or chapter_data.get("lrts_data")
            if isinstance(nested, dict):
                chapter_data = {**chapter_data, **nested}
        else:
            chapter_data = {}
        entity_type, entity_id = self._parse_entity_ref(book_id, fallback_type=_to_int(chapter_data.get("entity_type"), 0))
        entity_type = _to_int(chapter_data.get("entity_type"), entity_type)
        entity_id = _to_int(chapter_data.get("entity_id"), entity_id)
        chapter = _to_int(chapter_data.get("id") or chapter_data.get("chapter_id") or chapter_id)
        section = _to_int(chapter_data.get("section"), 1)
        if not entity_id or not chapter:
            return None
        try:
            import os
            with _RATE_SEMAPHORE:
                _throttle_audio_request()
                client = self._client_or_guest()
                # 取流专用 UA 开关：懒人按客户端类型分码率，安卓只给 48k。
                # 填入 iOS UA（环境变量 LRTS_AUDIO_UA）可尝试拿高码率；仅替换取流这一步的 UA，
                # 不影响搜索/章节（懒人下载并发=1，临时改 header 安全）。
                audio_ua = os.getenv("LRTS_AUDIO_UA")
                old_ua = client.session.headers.get("User-Agent") if audio_ua else None
                if audio_ua:
                    client.session.headers["User-Agent"] = audio_ua
                try:
                    data = client.get_play_path(entity_type, entity_id, chapter, section, op_type=1)
                finally:
                    if audio_ua and old_ua is not None:
                        client.session.headers["User-Agent"] = old_ua
        except Exception as exc:
            print(f"[lrts] getListenPath failed: {exc}")
            return None
        if data.get("status") == 114:
            raise RateLimitError(data.get("msg") or "download too frequently")
        if data.get("status") == 4 and "非法请求" in str(data.get("msg") or ""):
            raise IllegalRequestError(data.get("msg") or "非法请求")
        if data.get("status") != 0:
            print(f"[lrts] getListenPath status={data.get('status')} msg={data.get('msg')}")
            return None
        # 一次性诊断：打印懒人音频接口返回的字段名，排查高码率(320k)是否已藏在某字段里（只打字段名，不含含 token 的完整 URL）
        global _LRTS_AUDIO_DEBUG_DONE
        if not _LRTS_AUDIO_DEBUG_DONE:
            _LRTS_AUDIO_DEBUG_DONE = True
            d = data.get("data") or {}
            print(f"[lrts-audio-debug] UA={os.getenv('LRTS_AUDIO_UA') or '默认安卓'} mimeType={d.get('mimeType')} fileSize={d.get('fileSize')} fileLength={d.get('fileLength')}")
        url = (data.get("data") or {}).get("path") or data.get("path")
        return self._normalize_url(url)

    def _normalize_url(self, url):
        value = str(url or "").strip()
        if value.startswith("//"):
            return "https:" + value
        return value

    def _normalize_audio_url(self, url):
        return self._normalize_url(url)

    def extend_audio_url(self, url, extend_days=30):
        return url or ""

    def download_audio(self, url, save_path, progress_callback=None):
        try:
            response = self.session.get(url, stream=True, timeout=120)
            response.raise_for_status()
            total = int(response.headers.get("Content-Length") or 0)
            done = 0
            Path(save_path).parent.mkdir(parents=True, exist_ok=True)
            with open(str(save_path), "wb") as file:
                for chunk in response.iter_content(chunk_size=262144):
                    if not chunk:
                        continue
                    file.write(chunk)
                    done += len(chunk)
                    if progress_callback:
                        progress_callback(done, total)
            size = os.path.getsize(str(save_path))
            if size > 10240:
                return True
            os.remove(str(save_path))
            return False
        except Exception as exc:
            print(f"[lrts] download failed: {exc}")
            return False

    def generate_sign(self, api_path, base_params):
        params = {key: str(value) for key, value in (base_params or {}).items()}
        params["sc"] = calc_sc(api_path, params)
        return params


def get_lrts_manager():
    if not hasattr(get_lrts_manager, "_instance"):
        get_lrts_manager._instance = LRTSManager()
    return get_lrts_manager._instance
