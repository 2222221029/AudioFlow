from unittest.mock import Mock

from core.search_manager import SearchManager
from src.features.qidian.audio_system import normalize_qidian_cookies


CAPTURED_HEADERS = """Host: unitelogreport.reader.qq.com\r
Content-Type: application/octet-stream\r
Cookie: QDH=abc==; appId=12; areaId=40; qid=user-id; ywguid=8000001; ywkey=member-key\r
Connection: keep-alive\r
User-Agent: QDReaderAppStore/4664"""


def _manager_without_init():
    manager = SearchManager.__new__(SearchManager)
    manager.qidian_session = Mock()
    manager.qidian_session.cookies = Mock()
    manager.qidian_headers = {}
    return manager


def test_qidian_captured_headers_extract_only_cookie_line():
    cookies = normalize_qidian_cookies(CAPTURED_HEADERS)

    assert cookies == {
        "QDH": "abc==",
        "appId": "12",
        "areaId": "40",
        "qid": "user-id",
        "ywguid": "8000001",
        "ywkey": "member-key",
    }
    assert all("\r" not in value and "\n" not in value for value in cookies.values())


def test_qidian_recovers_cookie_from_legacy_malformed_dictionary():
    cookies = normalize_qidian_cookies({
        "Host: unitelogreport.reader.qq.com\r\nCookie: QDH": "abc==",
        "ywguid": "8000001",
        "ywkey": "member-key",
    })

    assert cookies == {"QDH": "abc==", "ywguid": "8000001", "ywkey": "member-key"}


def test_search_manager_uses_normalized_entitlement_headers():
    manager = _manager_without_init()

    manager.set_qidian_cookie(CAPTURED_HEADERS)

    assert manager.qidian_headers["YwGuid"] == "8000001"
    assert manager.qidian_headers["YwKey"] == "member-key"
    manager.qidian_session.cookies.update.assert_called_once_with(manager.qidian_cookies)


def test_qidian_download_does_not_preflight_signed_url_with_head(tmp_path):
    manager = _manager_without_init()
    manager.qidian_headers = {"User-Agent": "test-agent"}
    response = Mock()
    response.status_code = 206
    response.headers = {"content-type": "audio/mpeg", "content-length": "3"}
    response.iter_content.return_value = [b"abc"]
    manager.qidian_session.get.return_value = response

    output = tmp_path / "chapter.mp3"
    assert manager.download_qidian_audio("https://cdn.example/signed.mp3", output)
    assert output.read_bytes() == b"abc"
    manager.qidian_session.head.assert_not_called()
    manager.qidian_session.get.assert_called_once()
