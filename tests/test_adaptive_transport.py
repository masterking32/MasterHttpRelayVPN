import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from core.adaptive_transport.engine import AdaptiveRouteEngine
from core.adaptive_transport.hygiene import validate_public_ip
from core.adaptive_transport.models import ProbeTarget, RouteScore


class HygieneTests(unittest.TestCase):
    def test_rejects_private(self):
        with self.assertRaises(ValueError):
            validate_public_ip("10.0.0.1")

    def test_accepts_public(self):
        self.assertEqual(validate_public_ip("8.8.8.8"), "8.8.8.8")


class EngineTests(unittest.IsolatedAsyncioTestCase):
    async def test_circuit_breaker(self):
        with tempfile.TemporaryDirectory() as td:
            engine = AdaptiveRouteEngine(f"{td}/intel.db")
            t = ProbeTarget(ip="8.8.8.8", port=443, sni="www.google.com")
            for _ in range(engine.cfg.circuit_breaker_failures - 1):
                self.assertFalse(engine.register_route_failure(t))
            self.assertTrue(engine.register_route_failure(t))

    async def test_select_route_sticky(self):
        with tempfile.TemporaryDirectory() as td:
            engine = AdaptiveRouteEngine(f"{td}/intel.db")
            t = ProbeTarget(ip="8.8.8.8", port=443, sni="www.google.com")
            r1 = RouteScore(t, 100, 1, 0.0, 1.0, 0.9, 0.8)
            r2 = RouteScore(t, 90, 1, 0.0, 1.0, 0.9, 0.81)
            chosen = await engine.select_route([r1], gameplay_active=True)
            chosen2 = await engine.select_route([r2], gameplay_active=True)
            self.assertIs(chosen, chosen2)


if __name__ == "__main__":
    unittest.main()
