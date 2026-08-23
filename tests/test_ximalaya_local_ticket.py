import base64
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

from core.ximalaya_credentials import ximalaya_mobile_ticket_metadata
from core.ximalaya_local_ticket import (
    LocalTicketError,
    clear_mobile_ticket_cache,
    generate_mobile_ticket,
)


class XimalayaLocalTicketTest(unittest.TestCase):
    credentials = {
        "x_tk": "TAC" + base64.urlsafe_b64encode(
            b"prefixcom.ximalaya.ting.android!1.3.27!9.4.52.3!b=playTrack&s=play&u=123456"
        ).rstrip(b"=").decode(),
        "cookie": "1&_device=android&22015971-35cb-4c99-bb32-b3be8cf79608&9.4.52.3;1&_token=123456&session",
        "user_agent": "ting_9.4.52.3(com.ximalaya.ting.android,Android)",
        "device": "android",
    }

    def setUp(self):
        clear_mobile_ticket_cache()

    def tearDown(self):
        clear_mobile_ticket_cache()

    def test_generates_logged_in_play_ticket_from_existing_session(self):
        with mock.patch("core.ximalaya_local_ticket.time.time", return_value=1787328000), mock.patch(
            "core.ximalaya_local_ticket.uuid.uuid4",
            return_value=mock.Mock(bytes=bytes.fromhex("00112233445566778899aabbccddeeff")),
        ):
            ticket = generate_mobile_ticket(self.credentials)

        metadata = ximalaya_mobile_ticket_metadata(ticket)
        self.assertEqual(metadata["uid"], "123456")
        self.assertEqual(metadata["business"], "playTrack")
        self.assertEqual(metadata["scene"], "play")

    def test_cookie_only_ticket_matches_friend_script_golden_output(self):
        cookie_only = {
            "cookie": (
                "1&_device=android&22015971-35cb-4c99-bb32-b3be8cf79608&9.3.33.3;"
                "1&_token=123456&session"
            ),
        }
        with mock.patch("core.ximalaya_local_ticket.time.time", return_value=1787328000), mock.patch(
            "core.ximalaya_local_ticket.uuid.uuid4",
            return_value=mock.Mock(bytes=bytes.fromhex("00112233445566778899aabbccddeeff")),
        ):
            ticket = generate_mobile_ticket(cookie_only)

        self.assertEqual(
            ticket,
            "TACaoh2ACIBWXE1y0yZuzKzvoz3lggiEHtCcZ4q7jOrGQVAKnj3BgSddnzyzWI6k"
            "Hpz3J1Cmc77bDlgCKFUtrYB1iFLDjxjb20ueGltYWxheWEudGluZy5hbmRyb2lkITEu"
            "My4yNyE5LjMuMzMuMyFiPXBsYXlUcmFjayZzPXBsYXkmdT0xMjM0NTY",
        )
        metadata = ximalaya_mobile_ticket_metadata(ticket)
        self.assertEqual(metadata["uid"], "123456")

    def test_rejects_session_without_matching_login_bundle(self):
        with self.assertRaises(LocalTicketError):
            generate_mobile_ticket({"cookie": "channel=android"})

    def test_default_generates_a_fresh_ticket_for_each_request(self):
        generated = [
            mock.Mock(bytes=bytes.fromhex("00112233445566778899aabbccddeeff")),
            mock.Mock(bytes=bytes.fromhex("ffeeddccbbaa99887766554433221100")),
        ]
        with mock.patch.dict("os.environ", {
            "XIMALAYA_LOCAL_TICKET_TTL_SECONDS": "0",
        }), mock.patch(
            "core.ximalaya_local_ticket.time.time", return_value=1787328000
        ), mock.patch(
            "core.ximalaya_local_ticket.uuid.uuid4", side_effect=generated
        ) as uuid4:
            first = generate_mobile_ticket(self.credentials)
            second = generate_mobile_ticket(self.credentials)

        self.assertNotEqual(first, second)
        self.assertEqual(uuid4.call_count, 2)

    def test_reuses_one_ticket_within_session_ttl(self):
        generated_uuid = mock.Mock(bytes=bytes.fromhex("00112233445566778899aabbccddeeff"))
        with mock.patch.dict("os.environ", {
            "XIMALAYA_LOCAL_TICKET_TTL_SECONDS": "900",
        }), mock.patch(
            "core.ximalaya_local_ticket.time.monotonic", side_effect=[100.0, 500.0]
        ), mock.patch(
            "core.ximalaya_local_ticket.uuid.uuid4", return_value=generated_uuid
        ) as uuid4:
            first = generate_mobile_ticket(self.credentials)
            second = generate_mobile_ticket(self.credentials)

        self.assertEqual(first, second)
        uuid4.assert_called_once()

    def test_rotates_ticket_after_session_ttl(self):
        generated = [
            mock.Mock(bytes=bytes.fromhex("00112233445566778899aabbccddeeff")),
            mock.Mock(bytes=bytes.fromhex("ffeeddccbbaa99887766554433221100")),
        ]
        with mock.patch.dict("os.environ", {
            "XIMALAYA_LOCAL_TICKET_TTL_SECONDS": "60",
        }), mock.patch(
            "core.ximalaya_local_ticket.time.monotonic", side_effect=[100.0, 161.0]
        ), mock.patch(
            "core.ximalaya_local_ticket.time.time", side_effect=[1787328000, 1787328061]
        ), mock.patch(
            "core.ximalaya_local_ticket.uuid.uuid4", side_effect=generated
        ) as uuid4:
            first = generate_mobile_ticket(self.credentials)
            second = generate_mobile_ticket(self.credentials)

        self.assertNotEqual(first, second)
        self.assertEqual(uuid4.call_count, 2)

    def test_concurrent_chapters_share_one_cached_ticket(self):
        generated_uuid = mock.Mock(bytes=bytes.fromhex("00112233445566778899aabbccddeeff"))
        with mock.patch.dict("os.environ", {
            "XIMALAYA_LOCAL_TICKET_TTL_SECONDS": "900",
        }), mock.patch(
            "core.ximalaya_local_ticket.time.monotonic", return_value=100.0
        ), mock.patch(
            "core.ximalaya_local_ticket.uuid.uuid4", return_value=generated_uuid
        ) as uuid4, ThreadPoolExecutor(max_workers=16) as executor:
            tickets = list(executor.map(
                lambda _index: generate_mobile_ticket(self.credentials),
                range(64),
            ))

        self.assertEqual(len(set(tickets)), 1)
        uuid4.assert_called_once()


if __name__ == "__main__":
    unittest.main()
