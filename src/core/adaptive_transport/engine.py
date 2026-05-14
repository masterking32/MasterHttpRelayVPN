from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass

from .hygiene import validate_public_ip
from .models import ProbeTarget, RouteScore
from .probe import AsyncRouteProbe, summarize
from .storage import RouteIntelligenceStore


@dataclass(slots=True)
class AdaptiveRouteConfig:
    sample_ratio: float = 0.2
    min_concurrency: int = 4
    max_concurrency: int = 48
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

    async def evaluate(self, targets: list[ProbeTarget], cancel_event: asyncio.Event | None = None) -> list[RouteScore]:
        sampled = [t for t in targets if random.random() <= self.cfg.sample_ratio]
        if not sampled:
            sampled = targets[: min(3, len(targets))]
        concurrency = max(self.cfg.min_concurrency, min(self.cfg.max_concurrency, len(sampled)))
        sem = asyncio.Semaphore(concurrency)
        results: list[RouteScore] = []

        async def worker(t: ProbeTarget):
            validate_public_ip(t.ip)
            if cancel_event and cancel_event.is_set():
                return
            async with sem:
                samples = await self.probe.probe(t, include_quic=t.transport_profile == "quic")
                med, jit, loss, hs, stable = summarize(samples)
                score_value = (stable * 0.35) + ((1.0 - min(1.0, loss)) * 0.25) + (hs * 0.25) + (1.0 / (1.0 + jit + med / 100.0) * 0.15)
                score = RouteScore(t, med, jit, loss, hs, stable, score_value)
                results.append(score)
                await self.store.record_score(score)

        await asyncio.gather(*(worker(t) for t in sampled), return_exceptions=False)
        return sorted(results, key=lambda r: r.score, reverse=True)

    async def select_route(self, candidates: list[RouteScore], gameplay_active: bool) -> RouteScore | None:
        if not candidates:
            return self._active_route
        now = time.time()
        if self._active_route and gameplay_active and now < self._active_until:
            return self._active_route
        best = candidates[0]
        if self._active_route:
            improvement = best.score - self._active_route.score
            if gameplay_active and improvement < 0.05:
                return self._active_route
            if now - self._active_route.sampled_at < self.cfg.switch_guard_s:
                return self._active_route
        self._active_route = best
        self._active_until = now + self.cfg.sticky_session_s
        return best

    def register_route_failure(self, target: ProbeTarget) -> bool:
        key = f"{target.ip}:{target.port}:{target.sni}"
        self._failure_counts[key] = self._failure_counts.get(key, 0) + 1
        return self._failure_counts[key] >= self.cfg.circuit_breaker_failures
