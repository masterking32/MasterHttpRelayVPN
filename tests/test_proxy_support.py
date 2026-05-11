import pathlib
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from core.constants import CACHE_TTL_STATIC_LONG
from proxy.proxy_support import (
    ResponseCache,
    has_unsupported_transfer_encoding,
    host_matches_rules,
    is_ip_literal,
    load_host_rules,
    parse_content_length,
)


class ProxySupportTests(unittest.TestCase):
    def test_is_ip_literal_handles_ipv4_ipv6_and_hostnames(self):
        self.assertTrue(is_ip_literal("127.0.0.1"))
        self.assertTrue(is_ip_literal("[2001:db8::1]"))
        self.assertFalse(is_ip_literal("example.com"))

    def test_load_host_rules_and_match_suffixes(self):
        rules = load_host_rules(["Example.com", ".example.org", "api.example.net."])
        self.assertTrue(host_matches_rules("example.com", rules))
        self.assertTrue(host_matches_rules("sub.example.org", rules))
        self.assertTrue(host_matches_rules("api.example.net", rules))
        self.assertFalse(host_matches_rules("example.org", rules))
        self.assertFalse(host_matches_rules("other.test", rules))

    def test_parse_content_length_uses_exact_header_name(self):
        headers = (
            b"HTTP/1.1 200 OK\r\n"
            b"X-Content-Length: 999\r\n"
            b"Content-Length: 42\r\n\r\n"
        )
        self.assertEqual(parse_content_length(headers), 42)
        self.assertEqual(parse_content_length(b"HTTP/1.1 200 OK\r\n\r\n"), 0)

    def test_has_unsupported_transfer_encoding(self):
        self.assertFalse(
            has_unsupported_transfer_encoding(
                b"Transfer-Encoding: identity\r\n\r\n"
            )
        )
        self.assertTrue(
            has_unsupported_transfer_encoding(
                b"Transfer-Encoding: chunked\r\n\r\n"
            )
        )
        self.assertTrue(
            has_unsupported_transfer_encoding(
                b"Transfer-Encoding: gzip, chunked\r\n\r\n"
            )
        )


class ResponseCacheParseTtlTests(unittest.TestCase):
    def test_static_asset_uses_long_ttl(self):
        raw = (
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: image/png\r\n\r\n"
            b"body"
        )
        self.assertEqual(
            ResponseCache.parse_ttl(raw, "https://example.com/logo.png"),
            CACHE_TTL_STATIC_LONG,
        )

    def test_private_no_store_and_set_cookie_disable_caching(self):
        private_resp = (
            b"HTTP/1.1 200 OK\r\n"
            b"Cache-Control: private, max-age=600\r\n\r\n"
            b"body"
        )
        no_store_resp = (
            b"HTTP/1.1 200 OK\r\n"
            b"Cache-Control: no-store\r\n\r\n"
            b"body"
        )
        cookie_resp = (
            b"HTTP/1.1 200 OK\r\n"
            b"Set-Cookie: sid=1\r\n"
            b"Content-Type: image/png\r\n\r\n"
            b"body"
        )

        self.assertEqual(ResponseCache.parse_ttl(private_resp, "https://example.com/app.js"), 0)
        self.assertEqual(ResponseCache.parse_ttl(no_store_resp, "https://example.com/app.js"), 0)
        self.assertEqual(ResponseCache.parse_ttl(cookie_resp, "https://example.com/logo.png"), 0)


if __name__ == "__main__":
    unittest.main()
