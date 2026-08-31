import base64
import json
from unittest import mock
from unittest.mock import Mock

from core.qr_login import QRSession, _drive_qidian
from core.search_manager import SearchManager
from src.features.qidian.audio_system import QidianAudioSystem, QrcodeLogin, normalize_qidian_cookies


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


def test_qidian_qr_accepts_dynamic_jsonp_and_keeps_image_in_memory():
    encoded = base64.b64encode(b"fake-png").decode("ascii")
    response = Mock()
    payload = {
        "code": 0,
        "data": {
            "sessionKey": "qr-session",
            "image": f"data:image/png;base64,{encoded}",
        },
    }
    response.text = f"jQuery123_456({json.dumps(payload)});"
    login = QrcodeLogin()
    login.session.get = Mock(return_value=response)

    assert login.get_qrcode() == login.uuid
    assert login.session_key == "qr-session"
    assert login.qr_image == f"data:image/png;base64,{encoded}"
    response.raise_for_status.assert_called_once()


def test_qidian_qr_rejects_malformed_jsonp_without_repairing_it():
    response = Mock()
    response.text = 'jQuery123_456({"code":0,"data":{"sessionKey":"qr-session"});'
    login = QrcodeLogin()
    login.session.get = Mock(return_value=response)

    assert login.get_qrcode() is None
    assert login.last_error
    assert login.session_key is None


def test_qidian_qr_driver_does_not_require_a_writable_working_directory():
    encoded = base64.b64encode(b"fake-png").decode("ascii")

    class FakeLogin:
        last_error = ""
        qr_image = f"data:image/png;base64,{encoded}"

        def get_qrcode(self):
            return "uuid"

        def get_ck(self, max_wait=120):
            return {"ywguid": "guid", "ywkey": "key"}

    session = QRSession("qidian")
    with mock.patch("src.features.qidian.audio_system.QrcodeLogin", FakeLogin):
        _drive_qidian(session)

    assert session.status == "success"
    assert session.qr_image == FakeLogin.qr_image
    assert session.cookies == {"ywguid": "guid", "ywkey": "key"}


def test_qidian_chapter_list_accepts_lowercase_response_shape():
    response = Mock()
    response.json.return_value = {
        "code": 0,
        "data": {
            "items": [{"acid": "chapter-1", "audioChapterName": "第一集"}],
            "hasNext": True,
        },
    }
    system = QidianAudioSystem({"ywguid": "guid", "ywkey": "key"})
    system.session.get = Mock(return_value=response)

    chapters, has_next = system.get_chapter_list("audio-1", page=1)

    assert chapters == [{"acid": "chapter-1", "audioChapterName": "第一集"}]
    assert has_next is True


def test_search_manager_normalizes_lowercase_qidian_chapters():
    manager = _manager_without_init()
    manager.qidian_cookies = {"ywguid": "guid", "ywkey": "key"}

    with mock.patch(
        "core.search_manager.QidianAudioSystem.get_chapter_list",
        return_value=([{
            "acid": "chapter-1",
            "audioChapterName": "第一集",
            "duration": 123,
        }], False),
    ):
        chapters = manager.get_qidian_chapters("audio-1")

    assert chapters[0]["id"] == "chapter-1"
    assert chapters[0]["title"] == "第一集"
    assert chapters[0]["duration"] == 123
