"""
Domain-fronting helper utilities: SNI pool building, range-request validation,
progress formatting, and stream spool read/write helpers.

Extracted from domain_fronter.py to separate pure helper logic from the
DomainFronter class.
"""

import re
from dataclasses import dataclass

from core.constants import FRONT_SNI_POOL_GOOGLE


__all__ = [
    "HostStat",
    "build_sni_pool",
    "parse_content_range",
    "validate_range_response",
    "format_bytes_human",
    "format_elapsed_short",
    "render_progress_bar",
    "progress_line",
    "spool_write",
    "spool_read",
]


@dataclass
class HostStat:
    """Per-host traffic accounting — useful for profiling slow / heavy sites."""

    requests: int = 0
    cache_hits: int = 0
    bytes: int = 0
    total_latency_ns: int = 0
    errors: int = 0


def build_sni_pool(front_domain: str, overrides: list | None) -> list[str]:
    """Build the list of SNIs to rotate through on new outbound TLS handshakes."""
    if overrides:
        seen: set[str] = set()
        out: list[str] = []
        for item in overrides:
            host = str(item).strip().lower().rstrip(".")
            if host and host not in seen:
                seen.add(host)
                out.append(host)
        if out:
            return out
    front_domain = (front_domain or "").lower().rstrip(".")
    if front_domain.endswith(".google.com") or front_domain == "google.com":
        pool = list(FRONT_SNI_POOL_GOOGLE)
        if front_domain and front_domain not in pool:
            pool.insert(0, front_domain)
        return pool
    return [front_domain] if front_domain else ["www.google.com"]


def parse_content_range(value: str) -> tuple[int, int, int] | None:
    match = re.match(r"^\s*bytes\s+(\d+)-(\d+)/(\d+)\s*$", value or "")
    if not match:
        return None
    start, end, total = (int(group) for group in match.groups())
    if start < 0 or end < start or total <= end:
        return None
    return start, end, total


def validate_range_response(
    status: int,
    resp_headers: dict,
    body: bytes,
    start_off: int,
    end_off: int,
    total_size: int | None = None,
) -> str | None:
    if status != 206:
        return f"status {status}"
    parsed = parse_content_range(resp_headers.get("content-range", ""))
    if not parsed:
        return "missing/invalid Content-Range"
    got_start, got_end, got_total = parsed
    if got_start != start_off or got_end != end_off:
        return f"Content-Range mismatch {got_start}-{got_end}"
    if total_size is not None and got_total != total_size:
        return f"Content-Range total mismatch {got_total}/{total_size}"
    expected = end_off - start_off + 1
    if len(body) != expected:
        return f"short chunk {len(body)}/{expected} B"
    return None


def format_bytes_human(num_bytes: int) -> str:
    value = float(max(0, num_bytes))
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    unit = units[0]
    for unit in units:
        if value < 1024.0 or unit == units[-1]:
            break
        value /= 1024.0
    if unit == "B":
        return f"{int(value)} {unit}"
    return f"{value:.1f} {unit}"


def format_elapsed_short(seconds: float) -> str:
    total = max(0, int(seconds))
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def render_progress_bar(done: int, total: int, width: int = 34) -> str:
    if total <= 0:
        return "[" + ("-" * width) + "]"
    ratio = max(0.0, min(1.0, done / total))
    filled = min(width, int(round(ratio * width)))
    return "[" + ("#" * filled) + ("-" * (width - filled)) + "]"


def progress_line(*, elapsed: float, done: int, total: int, speed_bytes_per_sec: float) -> str:
    return (
        f"[{format_elapsed_short(elapsed)}] "
        f"{render_progress_bar(done, total)} "
        f"{format_bytes_human(done)} / {format_bytes_human(total)} "
        f"({format_bytes_human(int(speed_bytes_per_sec))}/s)"
    )


# ── Parallel-range spool helpers ─────────────────────────────────────────────

def spool_write(file_obj, offset: int, data: bytes) -> None:
    """Write *data* at *offset* in a temp file used for parallel-range spooling."""
    file_obj.seek(offset)
    file_obj.write(data)
    file_obj.flush()


def spool_read(file_obj, offset: int, size: int) -> bytes:
    """Read *size* bytes from *offset* in a parallel-range spool file."""
    file_obj.seek(offset)
    return file_obj.read(size)
