import pathlib
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from core.constants import FRONT_SNI_POOL_GOOGLE
from relay.fronting_support import (
    build_sni_pool,
    parse_content_range,
    validate_range_response,
)


class BuildSniPoolTests(unittest.TestCase):
    def test_uses_explicit_overrides_with_normalization_and_deduplication(self):
        result = build_sni_pool(
            "www.google.com",
            [" Mail.Google.com ", "mail.google.com", "accounts.google.com."],
        )
        self.assertEqual(result, ["mail.google.com", "accounts.google.com"])

    def test_google_front_domain_uses_google_pool(self):
        result = build_sni_pool("www.google.com", None)
        self.assertEqual(result, list(FRONT_SNI_POOL_GOOGLE))

    def test_non_google_front_domain_falls_back_to_single_host(self):
        self.assertEqual(build_sni_pool("cdn.example.com", None), ["cdn.example.com"])
        self.assertEqual(build_sni_pool("", None), ["www.google.com"])


class RangeParsingTests(unittest.TestCase):
    def test_parse_content_range_accepts_valid_values(self):
        self.assertEqual(parse_content_range("bytes 10-19/20"), (10, 19, 20))

    def test_parse_content_range_rejects_invalid_values(self):
        self.assertIsNone(parse_content_range(""))
        self.assertIsNone(parse_content_range("bytes 10-19/*"))
        self.assertIsNone(parse_content_range("bytes 10-9/20"))
        self.assertIsNone(parse_content_range("bytes 10-19/19"))

    def test_validate_range_response_success_and_failures(self):
        headers = {"content-range": "bytes 10-13/20"}
        body = b"data"

        self.assertIsNone(validate_range_response(206, headers, body, 10, 13, 20))
        self.assertEqual(
            validate_range_response(200, headers, body, 10, 13, 20),
            "status 200",
        )
        self.assertEqual(
            validate_range_response(206, {"content-range": "bytes 11-13/20"}, body, 10, 13, 20),
            "Content-Range mismatch 11-13",
        )
        self.assertEqual(
            validate_range_response(206, headers, b"abc", 10, 13, 20),
            "short chunk 3/4 B",
        )


if __name__ == "__main__":
    unittest.main()
