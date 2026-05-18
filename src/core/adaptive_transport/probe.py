from __future__ import annotations

import asyncio
import socket
import ssl
import statistics
import time
from dataclasses import dataclass

from .models import ProbeObservation, ProbeTarget


@dataclass(slots=True)
class ProbeConfig:
    timeout_s: float = 2.5
    retries: int = 3


class AsyncRouteProbe:
    def __init__(self, cfg: ProbeConfig | None = None):
        self.cfg = cfg or ProbeConfig()

    async def probe(self, target: ProbeTarget, include_quic: bool = False) -> list[ProbeObservation]:
        samples: list[ProbeObservation] = []
        for _ in range(self.cfg.retries):
            samples.append(await self._tcp_syn_probe(target))
            samples.append(await self._tls_probe(target))
            samples.append(await self._h2_preface_probe(target))
            if include_quic:
                samples.append(await self._quic_probe(target))
        return samples

    async def _tcp_syn_probe(self, target: ProbeTarget) -> ProbeObservation:
        start = time.perf_counter()
        ok = False
        writer = None
        try:
            fut = asyncio.open_connection(target.ip, target.port)
            _, writer = await asyncio.wait_for(fut, timeout=self.cfg.timeout_s)
            ok = True
        except Exception:
            ok = False
        finally:
            if writer is not None:
                writer.close()
                await writer.wait_closed()
        return ProbeObservation("tcp_syn", ok, (time.perf_counter() - start) * 1000)

    async def _tls_probe(self, target: ProbeTarget) -> ProbeObservation:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        ctx.set_alpn_protocols(list(target.alpn))
        start = time.perf_counter()
        writer = None
        ok = False
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(target.ip, target.port, ssl=ctx, server_hostname=target.sni),
                timeout=self.cfg.timeout_s,
            )
            ssl_obj = writer.get_extra_info("ssl_object")
            ok = bool(reader and ssl_obj and ssl_obj.version())
        except Exception:
            ok = False
        finally:
            if writer is not None:
                writer.close()
                await writer.wait_closed()
        return ProbeObservation("tls", ok, (time.perf_counter() - start) * 1000)

    async def _h2_preface_probe(self, target: ProbeTarget) -> ProbeObservation:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        ctx.set_alpn_protocols(["h2"])
        start = time.perf_counter()
        writer = None
        reader = None
        ok = False
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(target.ip, target.port, ssl=ctx, server_hostname=target.sni),
                timeout=self.cfg.timeout_s,
            )
            ssl_obj = writer.get_extra_info("ssl_object")
            if not ssl_obj or ssl_obj.selected_alpn_protocol() != "h2":
                raise RuntimeError("h2 alpn not negotiated")
            writer.write(b"PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n")
            await writer.drain()
            frame_header = await asyncio.wait_for(reader.readexactly(9), timeout=0.4)
            ok = len(frame_header) == 9
        except Exception:
            ok = False
        finally:
            if writer is not None:
                writer.close()
                await writer.wait_closed()
        return ProbeObservation("h2_preface", ok, (time.perf_counter() - start) * 1000)

    async def _quic_probe(self, target: ProbeTarget) -> ProbeObservation:
        loop = asyncio.get_running_loop()
        start = time.perf_counter()
        udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp_socket.setblocking(False)
        ok = False
        try:
            udp_socket.connect((target.ip, target.port))
            initial = bytes.fromhex("c300000001088394c8f03e5157080000449e00000002")
            await loop.sock_sendall(udp_socket, initial)
            response = await asyncio.wait_for(loop.sock_recv(udp_socket, 1200), timeout=min(self.cfg.timeout_s, 0.5))
            ok = bool(response)
        except Exception:
            ok = False
        finally:
            udp_socket.close()
        return ProbeObservation("quic", ok, (time.perf_counter() - start) * 1000)


def summarize(samples: list[ProbeObservation]) -> tuple[float, float, float, float, float]:
    lat = [s.latency_ms for s in samples if s.ok]
    if not lat:
        return 9_999.0, 5_000.0, 1.0, 0.0, 0.0
    median = statistics.median(lat)
    jitter = statistics.pstdev(lat) if len(lat) > 1 else 0.0
    loss = 1.0 - (len(lat) / max(1, len(samples)))
    handshake_success = len([s for s in samples if s.kind == "tls" and s.ok]) / max(1, len([s for s in samples if s.kind == "tls"]))
    stability = 1.0 / (1.0 + jitter + (loss * 100.0))
    return median, jitter, loss, handshake_success, stability
