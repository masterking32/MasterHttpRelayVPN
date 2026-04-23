"""
Google IP Scanner — finds the fastest reachable Google frontend IP.

Scans a list of candidate Google IPs via HTTPS (with SNI fronting), measures
latency, and reports results in a formatted table. Useful for finding the best
IP to configure in config.json when your current IP is blocked.
"""

from __future__ import annotations

import asyncio
import logging
import ssl
import time
from dataclasses import dataclass
from typing import Optional

from constants import CANDIDATE_IPS, GOOGLE_SCANNER_TIMEOUT, GOOGLE_SCANNER_CONCURRENCY

log = logging.getLogger("Scanner")


@dataclass
class ProbeResult:
    """Result of a single IP probe."""
    ip: str
    latency_ms: Optional[int] = None
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.latency_ms is not None


async def _probe_ip(
    ip: str,
    sni: str,
    semaphore: asyncio.Semaphore,
    timeout: float,
) -> ProbeResult:
    """
    Probe a single IP via HTTPS with SNI fronting.

    Args:
        ip: The IP to probe (xxx.xxx.xxx.xxx).
        sni: The SNI hostname to use in TLS handshake.
        semaphore: Rate limiter to control concurrency.
        timeout: Timeout in seconds for the entire probe.

    Returns:
        ProbeResult with latency_ms (if successful) or error message.
    """
    async with semaphore:
        start_time = time.time()
        try:
            # Create SSL context that skips certificate verification
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

            # Connect to IP:443 with SNI set to the fronting domain
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(
                    ip,
                    443,
                    ssl=ctx,
                    server_hostname=sni,
                ),
                timeout=timeout,
            )

            # Send minimal HTTP HEAD request
            request = f"HEAD / HTTP/1.1\r\nHost: {sni}\r\nConnection: close\r\n\r\n"
            writer.write(request.encode())
            await writer.drain()

            # Read response header (first 256 bytes is plenty for HTTP status)
            response = await asyncio.wait_for(reader.read(256), timeout=timeout)

            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

            # Check if we got an HTTP response
            if not response:
                return ProbeResult(ip=ip, error="empty response")

            response_str = response.decode("utf-8", errors="ignore")
            if not response_str.startswith("HTTP/"):
                return ProbeResult(ip=ip, error=f"invalid response: {response_str[:30]!r}")

            # Success — return latency in milliseconds
            elapsed_ms = int((time.time() - start_time) * 1000)
            return ProbeResult(ip=ip, latency_ms=elapsed_ms)

        except asyncio.TimeoutError:
            return ProbeResult(ip=ip, error="timeout")
        except ConnectionRefusedError:
            return ProbeResult(ip=ip, error="connection refused")
        except ConnectionResetError:
            return ProbeResult(ip=ip, error="connection reset")
        except OSError as e:
            return ProbeResult(ip=ip, error=f"network error: {e.strerror or str(e)}")
        except Exception as e:
            return ProbeResult(ip=ip, error=f"probe failed: {type(e).__name__}")


async def run(front_domain: str) -> bool:
    """
    Scan all candidate Google IPs and display results.

    Args:
        front_domain: The SNI hostname to use (e.g. "www.google.com").

    Returns:
        True if at least one IP is reachable, False otherwise.
    """
    timeout = GOOGLE_SCANNER_TIMEOUT
    concurrency = GOOGLE_SCANNER_CONCURRENCY

    print()
    print(f"Scanning {len(CANDIDATE_IPS)} Google frontend IPs")
    print(f"  SNI: {front_domain}")
    print(f"  Timeout: {timeout}s per IP")
    print(f"  Concurrency: {concurrency} parallel probes")
    print()

    # Create semaphore to limit concurrency
    semaphore = asyncio.Semaphore(concurrency)

    # Launch all probes concurrently
    tasks = [
        _probe_ip(ip, front_domain, semaphore, timeout)
        for ip in CANDIDATE_IPS
    ]
    results = await asyncio.gather(*tasks)

    # Sort by latency (successful first, then by speed)
    results.sort(key=lambda r: (not r.ok, r.latency_ms or float("inf")))

    # Display results table
    print(f"{'IP':<20} {'LATENCY':<12} {'STATUS':<25}")
    print(f"{'-' * 20} {'-' * 12} {'-' * 25}")

    ok_count = 0
    for result in results:
        if result.ok:
            print(f"{result.ip:<20} {result.latency_ms:>8}ms   OK")
            ok_count += 1
        else:
            status = result.error or "unknown error"
            print(f"{result.ip:<20} {'—':<12} {status:<25}")

    print()
    print(f"Result: {ok_count} / {len(results)} reachable")

    if ok_count == 0:
        print("No Google IPs reachable from this network.")
        print()
        return False

    # Show top 3 fastest
    fastest = [r for r in results if r.ok][:3]
    print()
    print("Top 3 fastest IPs:")
    for i, result in enumerate(fastest, 1):
        print(f"  {i}. {result.ip} ({result.latency_ms}ms)")

    print()
    print(f"Recommended: Set \"google_ip\": \"{fastest[0].ip}\" in config.json")
    print()
    return True


def scan_sync(front_domain: str) -> bool:
    """
    Wrapper to run async scanner from sync context (e.g. main.py).

    Args:
        front_domain: The SNI hostname to use.

    Returns:
        True if at least one IP is reachable, False otherwise.
    """
    try:
        return asyncio.run(run(front_domain))
    except KeyboardInterrupt:
        print("\nScan interrupted by user.")
        return False
    except Exception as e:
        log.error(f"Scan failed: {e}")
        return False
