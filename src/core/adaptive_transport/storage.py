from __future__ import annotations

import asyncio
import sqlite3
import time
from pathlib import Path

from .models import ProbeTarget, RouteScore


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

    async def top_routes(self, limit: int = 5, decay_window_s: float = 900.0) -> list[tuple[str, int, str, str, float]]:
        cutoff = time.time() - decay_window_s
        async with self._lock:
            return await asyncio.to_thread(self._top_routes_sync, limit, cutoff)

    def _top_routes_sync(self, limit: int, cutoff: float):
        with sqlite3.connect(self.path) as conn:
            rows = conn.execute(
                """SELECT ip,port,sni,profile,
                AVG(score * CASE WHEN sampled_at >= ? THEN 1.0 ELSE 0.5 END) AS decayed
                FROM route_scores
                WHERE retired = 0 AND cooldown_until < ?
                GROUP BY ip,port,sni,profile
                ORDER BY decayed DESC
                LIMIT ?""",
                (cutoff, time.time(), limit),
            ).fetchall()
            return rows
