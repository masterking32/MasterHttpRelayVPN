import pathlib
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from core.adblock import parse_hosts_text


class ParseHostsTextTests(unittest.TestCase):
    def test_parses_hosts_and_bare_domains_and_deduplicates(self):
        text = """
        # comment
        0.0.0.0 ads.example.com
        127.0.0.1 tracker.example.com # inline comment
        plain.example.org
        ads.example.com
        EXAMPLE.NET.
        """

        self.assertEqual(
            parse_hosts_text(text),
            [
                "ads.example.com",
                "tracker.example.com",
                "plain.example.org",
                "example.net",
            ],
        )

    def test_skips_invalid_reserved_and_wildcard_entries(self):
        text = """
        localhost
        localhost.localdomain
        192.168.1.1
        ::1
        analytics-*.example.com
        invalid
        bad_domain.example
        host name.example.com
        """

        self.assertEqual(parse_hosts_text(text), [])


if __name__ == "__main__":
    unittest.main()
