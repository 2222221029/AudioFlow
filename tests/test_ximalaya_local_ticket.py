import base64
import unittest
from unittest import mock

from core.ximalaya_credentials import ximalaya_mobile_ticket_metadata
from core.ximalaya_local_ticket import LocalTicketError, generate_mobile_ticket


class XimalayaLocalTicketTest(unittest.TestCase):
    credentials = {
        "x_tk": "TAC" + base64.urlsafe_b64encode(
            b"prefixcom.ximalaya.ting.android!1.3.27!9.4.52.3!b=playTrack&s=play&u=123456"
        ).rstrip(b"=").decode(),
        "cookie": "1&_device=android&22015971-35cb-4c99-bb32-b3be8cf79608&9.4.52.3;1&_token=123456&session",
        "user_agent": "ting_9.4.52.3(com.ximalaya.ting.android,Android)",
        "device": "android",
    }

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


if __name__ == "__main__":
    unittest.main()
