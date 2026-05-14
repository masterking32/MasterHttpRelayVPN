from __future__ import annotations

import asyncio
import sqlite3
import time
from pathlib import Path

from .models import ProbeTarget, RouteScore, RuntimeMetrics


class RouteIntelligenceStore:
    def __init__(self, path: str):
        self.path = Path(path)
        self._lock = asyncio.Lock()
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS route_scores (
                    ip TEXT NOT NULL,
                    port INTEGER NOT NULL,
                    sni TEXT NOT NULL,
                    profile TEXT NOT NULL,
                    sampled_at REAL NOT NULL,
                    median_rtt_ms REAL NOT NULL,
                    jitter_ms REAL NOT NULL,
                    packet_loss REAL NOT NULL,
                    handshake_success REAL NOT NULL,
                    session_stability REAL NOT NULL,
                    score REAL NOT NULL,
                    success_count INTEGER NOT NULL DEFAULT 0,
                    failure_count INTEGER NOT NULL DEFAULT 0,
                    cooldown_until REAL NOT NULL DEFAULT 0,
                    retired INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS route_runtime_metrics (
                    ip TEXT NOT NULL,
                    port INTEGER NOT NULL,
                    sni TEXT NOT NULL,
                    profile TEXT NOT NULL,
                    observed_at REAL NOT NULL,
                    disconnects INTEGER NOT NULL,
                    retransmissions INTEGER NOT NULL,
                    latency_spikes INTEGER NOT NULL,
                    packet_delay_variance REAL NOT NULL
                )
                """
            )

    async def record_score(self, score: RouteScore) -> None:
        async with self._lock:
            await asyncio.to_thread(self._record_score_sync, score)

    def _record_score_sync(self, score: RouteScore) -> None:
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                """INSERT INTO route_scores
                (ip,port,sni,profile,sampled_at,median_rtt_ms,jitter_ms,packet_loss,handshake_success,session_stability,score,success_count,failure_count)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    score.target.ip,
                    score.target.port,
                    score.target.sni,
                    score.target.transport_profile,
                    score.sampled_at,
                    score.median_rtt_ms,
                    score.jitter_ms,
                    score.packet_loss,
                    score.handshake_success_rate,
                    score.session_stability,
                    score.score,
                    1 if score.score > 0 else 0,
                    0 if score.score > 0 else 1,
                ),
            )

    async def record_runtime_metrics(self, target: ProbeTarget, metrics: RuntimeMetrics, observed_at: float | None = None) -> None:
        async with self._lock:
            await asyncio.to_thread(self._record_runtime_metrics_sync, target, metrics, observed_at or time.time())

    def _record_runtime_metrics_sync(self, target: ProbeTarget, metrics: RuntimeMetrics, observed_at: float) -> None:
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                """INSERT INTO route_runtime_metrics
                (ip,port,sni,profile,observed_at,disconnects,retransmissions,latency_spikes,packet_delay_variance)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (target.ip, target.port, target.sni, target.transport_profile, observed_at, metrics.disconnects, metrics.retransmissions, metrics.latency_spikes, metrics.packet_delay_variance),
            )

    async def top_routes(self, limit: int = 5, decay_window_s: float = 900.0) -> list[tuple[str, int, str, str, float]]:
        now = time.time()
        async with self._lock:
            return await asyncio.to_thread(self._top_routes_sync, limit, now, decay_window_s)

    def _top_routes_sync(self, limit: int, now: float, decay_window_s: float):
        with sqlite3.connect(self.path) as conn:
            rows = conn.execute(
                """SELECT s.ip,s.port,s.sni,s.profile,
                AVG(s.score * CASE WHEN (? - s.sampled_at) >= ? THEN 0.1 ELSE (1.0 - ((? - s.sampled_at)/?)*0.9) END)
                - COALESCE(AVG(ABS(s.score - ss.mean_score)), 0.0) * 0.2
                - COALESCE(SUM(m.disconnects + m.retransmissions + m.latency_spikes) * 0.02, 0.0)
                - COALESCE(AVG(m.packet_delay_variance) * 0.01, 0.0) AS decayed
                FROM route_scores s
                LEFT JOIN (
                    SELECT ip,port,sni,profile,AVG(score) AS mean_score FROM route_scores GROUP BY ip,port,sni,profile
                ) ss ON ss.ip=s.ip AND ss.port=s.port AND ss.sni=s.sni AND ss.profile=s.profile
                LEFT JOIN route_runtime_metrics m ON m.ip=s.ip AND m.port=s.port AND m.sni=s.sni AND m.profile=s.profile
                WHERE s.retired = 0 AND s.cooldown_until < ?
                GROUP BY s.ip,s.port,s.sni,s.profile
                ORDER BY decayed DESC
                LIMIT ?""",
                (now, max(1.0, decay_window_s), now, max(1.0, decay_window_s), now, max(1.0, decay_window_s), now, limit),
            ).fetchall()
            return rows
