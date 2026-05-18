from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from .hygiene import validate_public_ip
from .models import ProbeTarget, RouteScore, RuntimeMetrics, SessionState
from .probe import AsyncRouteProbe, summarize
from .storage import RouteIntelligenceStore

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class AdaptiveRouteConfig:
    min_concurrency: int = 1
    max_concurrency: int = 16
    sticky_session_s: float = 180.0
    switch_guard_s: float = 30.0
    circuit_breaker_failures: int = 4


class AdaptiveRouteEngine:
    def __init__(self, db_path: str, cfg: AdaptiveRouteConfig | None = None):
        self.cfg = cfg or AdaptiveRouteConfig()
        self.store = RouteIntelligenceStore(db_path)
        self.probe = AsyncRouteProbe()
        self._active_route: RouteScore | None = None
        self._active_until = 0.0
        self._failure_counts: dict[str, int] = {}
        self._session = SessionState()
        self._scan_tasks: set[asyncio.Task] = set()
        self._stopped = False

    async def evaluate(self, targets: list[ProbeTarget], cancel_event: asyncio.Event | None = None) -> list[RouteScore]:
        if self._stopped:
            return []
        ordered_targets = sorted(targets, key=self._route_key)
        concurrency = max(1, min(self.cfg.max_concurrency, max(self.cfg.min_concurrency, len(ordered_targets))))
        sem = asyncio.Semaphore(concurrency)
        results: list[RouteScore] = []

        async def worker(t: ProbeTarget):
            validate_public_ip(t.ip)
            if self._stopped or (cancel_event and cancel_event.is_set()):
                return
            async with sem:
                if self._stopped or (cancel_event and cancel_event.is_set()):
                    return
                samples = await self.probe.probe(t, include_quic=t.transport_profile == "quic")
                med, jit, loss, hs, stable = summarize(samples)
                score_value = (stable * 0.45) + ((1.0 - min(1.0, loss)) * 0.25) + (hs * 0.20) + (1.0 / (1.0 + jit + med / 100.0) * 0.10)
                score = RouteScore(t, med, jit, loss, hs, stable, score_value)
                results.append(score)
                logger.info(
                    "route_score_breakdown",
                    extra={
                        "route": self._route_key(t),
                        "median_rtt_ms": med,
                        "jitter_ms": jit,
                        "packet_loss": loss,
                        "handshake_success_rate": hs,
                        "session_stability": stable,
                        "score": score_value,
                    },
                )
                await self.store.record_score(score)

        self._scan_tasks = {asyncio.create_task(worker(t)) for t in ordered_targets}
        try:
            await asyncio.gather(*self._scan_tasks, return_exceptions=False)
        finally:
            self._scan_tasks.clear()
        return sorted(results, key=lambda r: (r.score, -r.packet_loss, -r.jitter_ms, -r.median_rtt_ms, self._route_key(r.target)), reverse=True)

    async def select_route(self, candidates: list[RouteScore], gameplay_active: bool) -> RouteScore | None:
        if self._stopped:
            return None
        now = time.time()
        if not candidates:
            logger.info("route_rejected", extra={"reason": "no_candidates", "selected": self._route_key(self._active_route.target) if self._active_route else None})
            return self._active_route
        best = candidates[0]
        if self._active_route and gameplay_active and not self._hard_failure(self._active_route.target):
            self._session.state = "session_active"
            logger.info("session_transition", extra={"transition": "session_active", "route": self._route_key(self._active_route.target)})
            logger.info("route_rejected", extra={"reason": "session_active_locked", "selected": self._route_key(self._active_route.target), "candidate": self._route_key(best.target)})
            return self._active_route
        if self._active_route and now - self._active_route.sampled_at < self.cfg.switch_guard_s and not self._hard_failure(self._active_route.target):
            logger.info("route_rejected", extra={"reason": "switch_guard", "selected": self._route_key(self._active_route.target), "candidate": self._route_key(best.target)})
            return self._active_route
        if self._active_route and not self._hard_failure(self._active_route.target):
            logger.info("route_rejected", extra={"reason": "active_route_not_failed", "selected": self._route_key(self._active_route.target), "candidate": self._route_key(best.target)})
            return self._active_route
        self._active_route = best
        self._active_until = now + self.cfg.sticky_session_s
        self._session.state = "session_stable_window" if gameplay_active else "session_start"
        self._session.stable_since = now
        logger.info("session_transition", extra={"transition": self._session.state, "route": self._route_key(best.target)})
        logger.info("route_selected", extra={"route": self._route_key(best.target), "score": best.score, "median_rtt_ms": best.median_rtt_ms, "jitter_ms": best.jitter_ms, "packet_loss": best.packet_loss, "handshake": best.handshake_success_rate, "stability": best.session_stability})
        return best

    async def record_runtime_metrics(self, target: ProbeTarget, metrics: RuntimeMetrics) -> None:
        await self.store.record_runtime_metrics(target, metrics)

    def register_route_failure(self, target: ProbeTarget) -> bool:
        key = self._route_key(target)
        self._failure_counts[key] = self._failure_counts.get(key, 0) + 1
        logger.info("session_transition", extra={"transition": "session_fail", "route": key, "failure_count": self._failure_counts[key]})
        return self._hard_failure(target)

    def bound_transport_route(self) -> ProbeTarget | None:
        return self._active_route.target if self._active_route else None

    async def shutdown(self) -> None:
        self._stopped = True
        for task in list(self._scan_tasks):
            task.cancel()
        if self._scan_tasks:
            await asyncio.gather(*self._scan_tasks, return_exceptions=True)
        self._scan_tasks.clear()

    def _route_key(self, target: ProbeTarget) -> str:
        return f"{target.ip}:{target.port}:{target.sni}:{target.transport_profile}"

    def _hard_failure(self, target: ProbeTarget) -> bool:
        return self._failure_counts.get(self._route_key(target), 0) >= self.cfg.circuit_breaker_failures
