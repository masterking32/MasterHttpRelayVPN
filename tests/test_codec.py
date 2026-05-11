import gzip
import pathlib
import sys
import unittest
import zlib


ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from core import codec


class CodecTests(unittest.TestCase):
    def test_supported_encodings_always_include_gzip_and_deflate(self):
        encodings = codec.supported_encodings()
        self.assertIn("gzip", encodings)
        self.assertIn("deflate", encodings)

    def test_decode_passes_through_unknown_empty_and_invalid_payloads(self):
        raw = b"plain-body"
        self.assertIs(codec.decode(raw, ""), raw)
        self.assertIs(codec.decode(raw, "identity"), raw)
        self.assertIs(codec.decode(raw, "unknown"), raw)
        self.assertEqual(codec.decode(raw, "gzip"), raw)

    def test_decode_gzip_and_deflate(self):
        raw = b"hello through compression"
        gzip_body = gzip.compress(raw)
        zlib_body = zlib.compress(raw)
        raw_deflate_body = zlib.compress(raw, wbits=-zlib.MAX_WBITS)

        self.assertEqual(codec.decode(gzip_body, "gzip"), raw)
        self.assertEqual(codec.decode(zlib_body, "deflate"), raw)
        self.assertEqual(codec.decode(raw_deflate_body, "deflate"), raw)


if __name__ == "__main__":
    unittest.main()
