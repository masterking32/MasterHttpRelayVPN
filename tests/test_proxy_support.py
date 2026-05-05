import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from proxy.proxy_support import (
    has_unsupported_transfer_encoding,
    host_matches_rules,
    inject_cors_headers,
    load_host_rules,
    parse_content_length,
)


class ProxySupportTests(unittest.TestCase):
    def test_parse_content_length_matches_exact_header_name(self):
        header_block = (
            b"POST / HTTP/1.1\r\n"
            b"X-Content-Length: 999\r\n"
            b"Content-Length: 42\r\n"
            b"\r\n"
        )
        self.assertEqual(parse_content_length(header_block), 42)

    def test_transfer_encoding_only_identity_is_supported(self):
        self.assertFalse(has_unsupported_transfer_encoding(
            b"POST / HTTP/1.1\r\nTransfer-Encoding: identity\r\n\r\n"
        ))
        self.assertTrue(has_unsupported_transfer_encoding(
            b"POST / HTTP/1.1\r\nTransfer-Encoding: gzip, chunked\r\n\r\n"
        ))

    def test_host_rules_support_exact_and_suffix_matches(self):
        rules = load_host_rules(["localhost", ".local", "Example.COM."])
        self.assertTrue(host_matches_rules("localhost", rules))
        self.assertTrue(host_matches_rules("printer.local", rules))
        self.assertTrue(host_matches_rules("example.com", rules))
        self.assertFalse(host_matches_rules("notexample.com", rules))

    def test_inject_cors_headers_replaces_existing_policy_and_keeps_body(self):
        response = (
            b"HTTP/1.1 200 OK\r\n"
            b"Access-Control-Allow-Origin: https://old.example\r\n"
            b"Content-Type: text/plain\r\n"
            b"Content-Length: 5\r\n"
            b"\r\n"
            b"hello"
        )

        rewritten = inject_cors_headers(response, "https://app.example")
        header_block, body = rewritten.split(b"\r\n\r\n", 1)

        self.assertEqual(body, b"hello")
        self.assertNotIn(b"https://old.example", header_block)
        self.assertIn(
            b"Access-Control-Allow-Origin: https://app.example", header_block
        )
        self.assertIn(b"Access-Control-Allow-Credentials: true", header_block)


if __name__ == "__main__":
    unittest.main()
