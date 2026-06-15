from __future__ import annotations

import re
from typing import Any

from core.meta_legacy_core import api_clients, config as legacy_config, docker_web, network_utils


def first_value(data: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def extract_year(value: Any) -> str:
    match = re.search(r"(19|20)\d{2}", str(value or ""))
    return match.group(0) if match else ""


def normalize_cover_url(data: dict[str, Any]) -> str:
    cover = first_value(data, "bestCover", "cover", "pic", "thumb_url", "audio_thumb_uri", "coverUrl")
    if cover.startswith("//"):
        return "https:" + cover
    return cover


def split_names(value: Any) -> list[str]:
    if isinstance(value, list):
        raw = value
    else:
        raw = re.split(r"[,，/、;；\n]+", str(value or ""))
    names = []
    for item in raw:
        text = str(item).strip()
        if text and text not in names:
            names.append(text)
    return names


def collect_tags(data: dict[str, Any]) -> list[str]:
    tags = []
    for key in ("category", "category_name", "categoryName", "finished"):
        value = data.get(key)
        if value:
            tags.extend(re.split(r"[,，\s]+", str(value)))
    raw = data.get("tags") or data.get("tag_list") or []
    if isinstance(raw, str):
        tags.extend(re.split(r"[,，\s]+", raw))
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                tags.append(str(item.get("name") or item.get("tagName") or item.get("tag_name") or "").strip())
            else:
                tags.append(str(item).strip())
    result = []
    for tag in tags:
        text = str(tag).strip()
        if text and text not in result:
            result.append(text)
    return result


def resolve_category_id(category_text: str) -> str:
    category_map = getattr(legacy_config, "CATEGORY_MAP", {})
    category_text = str(category_text or "").strip()
    if not category_text:
        return ""
    if category_text in category_map:
        return category_text
    for key, name in category_map.items():
        if category_text == name or category_text in name or name in category_text:
            return key
    return ""


def normalize_ximalaya_payload(raw: dict[str, Any]) -> dict[str, Any]:
    info = raw.get("albumPageMainInfo", raw or {})
    anchor = first_value(info, "anchorName", "nickname")
    if not anchor and isinstance(raw.get("anchorInfo"), dict):
        anchor = first_value(raw["anchorInfo"], "anchorName", "nickname")
    return {
        "title": first_value(info, "albumTitle", "title", "name"),
        "subtitle": first_value(info, "customTitle", "subtitle", "shortIntro"),
        "author": "",
        "announcer": anchor,
        "artist": anchor,
        "desc": first_value(info, "detailRichIntro", "intro"),
        "cover": first_value(info, "cover", "coverUrlLarge", "coverUrlMiddle"),
        "releaseDate": extract_year(first_value(info, "createDate", "updateDate", "createdAt", "createAt")),
    }


def normalize_metadata(data: dict[str, Any], platform: str = "") -> dict[str, Any]:
    data = dict(data or {})
    author = first_value(data, "author", "writer")
    anchor = first_value(data, "announcer", "artist", "anchor", "reader", "narrator")
    category_text = first_value(data, "category", "category_name", "categoryName")
    desc = network_utils.clean_html_tags(first_value(data, "desc", "info", "description"))
    return {
        "title": first_value(data, "title", "name", "album", "book_name"),
        "subtitle": first_value(data, "subtitle"),
        "author": ", ".join(split_names(author)),
        "anchor": ", ".join(split_names(anchor)),
        "authors": split_names(author),
        "anchors": split_names(anchor),
        "desc": desc,
        "cover_url": normalize_cover_url(data),
        "year": extract_year(first_value(data, "releaseDate", "date", "year", "publish_time", "createDate", "updateDate")),
        "finished": first_value(data, "finished"),
        "category": resolve_category_id(category_text),
        "category_text": category_text,
        "tags": collect_tags(data),
        "platform": platform or data.get("_platform") or "",
        "raw": data,
    }


def fetch_by_id(api_source: str, api_id: str) -> dict[str, Any]:
    if hasattr(docker_web, "fetch_api_metadata"):
        return docker_web.fetch_api_metadata(api_source, api_id)
    api_source = (api_source or "").strip()
    api_id = (api_id or "").strip()
    if not api_id:
        raise ValueError("请先填写平台专辑 ID")
    if api_source == "喜马拉雅":
        return normalize_metadata(normalize_ximalaya_payload(api_clients.ximalaya_api("album", api_id)), api_source)
    if api_source == "懒人听书":
        return normalize_metadata(api_clients.lanren_api(api_id), api_source)
    if api_source == "酷我听书":
        return normalize_metadata(api_clients.kuwo_api(api_id), api_source)
    if api_source == "番茄畅听":
        return normalize_metadata(api_clients.fanqie_api(api_id), api_source)
    if api_source == "起点听书":
        cookie = legacy_config.get_platform_cookies().get("qidian", "")
        return normalize_metadata(api_clients.qidian_api(api_id, cookie_str=cookie), api_source)
    if api_source == "网易云听书":
        return normalize_metadata(api_clients.netease_ting_api(api_id), api_source)
    if api_source == "云听fm":
        return normalize_metadata(api_clients.yunting_api(api_id), api_source)
    if api_source == "蜻蜓fm":
        return normalize_metadata(api_clients.qingting_api(api_id), api_source)
    raise ValueError(f"暂不支持的平台：{api_source}")


def fetch_by_link(platform: str, url: str) -> dict[str, Any]:
    if hasattr(docker_web, "fetch_link_metadata"):
        return docker_web.fetch_link_metadata(url, platform)
    platform = (platform or "起点听书").strip()
    url = (url or "").strip()
    if not url:
        raise ValueError("请先填写分享链接")
    html = network_utils.fetch_share_page_html(url, timeout=15)
    if platform == "起点听书":
        data = network_utils.parse_qidian_share_html(html, url)
    elif platform == "番茄畅听":
        data = network_utils.parse_fanqie_share_html(html, url)
    else:
        raise ValueError(f"暂不支持的链接平台：{platform}")
    if not data:
        raise ValueError("未能从链接中解析到专辑信息")
    data["_platform"] = platform
    return normalize_metadata(data, platform)
