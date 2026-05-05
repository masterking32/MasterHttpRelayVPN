import base64
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from relay.domain_fronter import DomainFronter


class DomainFronterPayloadTests(unittest.TestCase):
    def make_fronter(self):
        return DomainFronter({
            "google_ip": "216.239.38.120",
            "front_domain": "www.google.com",
            "script_id": "dummy-deployment-id",
            "auth_key": "test-secret",
        })

    def test_build_payload_strips_proxy_and_ip_leaking_headers(self):
        payload = self.make_fronter()._build_payload(
            "GET",
            "https://example.com/",
            {
                "User-Agent": "unit-test",
                "X-Forwarded-For": "198.51.100.10",
                "X-Real-IP": "198.51.100.10",
                "Via": "proxy",
                "Proxy-Authorization": "secret",
            },
            b"",
        )

        self.assertEqual(payload["h"], {"User-Agent": "unit-test"})

    def test_build_payload_does_not_readd_headers_when_all_are_stripped(self):
        payload = self.make_fronter()._build_payload(
            "GET",
            "https://example.com/",
            {
                "X-Forwarded-For": "198.51.100.10",
                "Forwarded": "for=198.51.100.10",
                "Via": "proxy",
            },
            b"",
        )

        self.assertNotIn("h", payload)

    def test_build_payload_base64_encodes_body_and_content_type(self):
        payload = self.make_fronter()._build_payload(
            "POST",
            "https://example.com/api",
            {"Content-Type": "application/json"},
            b'{"ok":true}',
        )

        self.assertEqual(base64.b64decode(payload["b"]), b'{"ok":true}')
        self.assertEqual(payload["ct"], "application/json")


if __name__ == "__main__":
    unittest.main()
