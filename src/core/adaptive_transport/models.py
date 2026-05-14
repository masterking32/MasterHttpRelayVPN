from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal
import time

ProbeKind = Literal["tcp_syn", "tls", "h2_preface", "quic"]


@dataclass(slots=True, frozen=True)
class ProbeTarget:
    ip: str
    port: int
    sni: str
    alpn: tuple[str, ...] = ("h2", "http/1.1")
    transport_profile: str = "vless_reality"


@dataclass(slots=True)
class ProbeObservation:
    kind: ProbeKind
    ok: bool
    latency_ms: float
    packet_loss: float = 0.0


@dataclass(slots=True)
class RouteScore:
    target: ProbeTarget
    median_rtt_ms: float
    jitter_ms: float
    packet_loss: float
    handshake_success_rate: float
    session_stability: float
    score: float
    sampled_at: float = field(default_factory=time.time)
