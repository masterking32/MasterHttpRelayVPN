import base64
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from relay.relay_response import parse_relay_json, split_raw_response, split_set_cookie


class RelayResponseTests(unittest.TestCase):
    def test_split_set_cookie_preserves_expires_comma(self):
        raw = (
            "sid=abc; Expires=Wed, 21 Oct 2026 07:28:00 GMT; Path=/, "
            "theme=dark; Path=/"
        )

        self.assertEqual(
            split_set_cookie(raw),
            [
                "sid=abc; Expires=Wed, 21 Oct 2026 07:28:00 GMT; Path=/",
                "theme=dark; Path=/",
            ],
        )

    def test_parse_relay_json_rebuilds_http_response_and_set_cookie_lines(self):
        data = {
            "s": 200,
            "h": {
                "Content-Type": "text/plain",
                "Content-Encoding": "gzip",
                "Set-Cookie": [
                    "a=1; Path=/, b=2; Path=/",
                ],
            },
            "b": base64.b64encode(b"hello").decode(),
        }

        raw = parse_relay_json(data, max_body_bytes=1024)
        status, headers, body = split_raw_response(raw)

        self.assertEqual(status, 200)
        self.assertEqual(body, b"hello")
        self.assertEqual(headers["content-type"], "text/plain")
        self.assertEqual(headers["content-length"], "5")
        self.assertNotIn("content-encoding", headers)
        self.assertEqual(raw.count(b"Set-Cookie:"), 2)

    def test_parse_relay_json_error_returns_gateway_response(self):
        raw = parse_relay_json({"e": "unauthorized"}, max_body_bytes=1024)
        status, headers, body = split_raw_response(raw)

        self.assertEqual(status, 502)
        self.assertIn(b"auth/permission error", body)


if __name__ == "__main__":
    unittest.main()
