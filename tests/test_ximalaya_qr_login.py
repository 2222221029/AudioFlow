import base64
import unittest

from core.qr_login import _base64_image_to_data_url, _file_to_data_url


class XimalayaQrImageTest(unittest.TestCase):
    def test_ximalaya_qr_image_stays_in_memory(self):
        encoded = base64.b64encode(b"fake-png").decode("ascii")

        data_url = _base64_image_to_data_url(encoded)

        self.assertEqual(data_url, f"data:image/png;base64,{encoded}")
        self.assertEqual(_file_to_data_url(data_url), data_url)

    def test_ximalaya_qr_image_rejects_invalid_base64(self):
        with self.assertRaises(ValueError):
            _base64_image_to_data_url("not valid base64!")


if __name__ == "__main__":
    unittest.main()
