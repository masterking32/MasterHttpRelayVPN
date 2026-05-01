"""
Pretty, column-aligned, color-aware logging for MasterHttpRelayVPN.

Zero extra dependencies. On Windows, ANSI color support is enabled via
the Console API. Colors are disabled automatically when:

  - The output stream is not a TTY (e.g. piped to a file)
  - The NO_COLOR environment variable is set
  - DFT_NO_COLOR=1 is set
"""

from __future__ import annotations

import logging
import os
import sys
import time


# ─── ANSI palette ──────────────────────────────────────────────────────────

RESET   = "\x1b[0m"
BOLD    = "\x1b[1m"
DIM     = "\x1b[2m"
ITALIC  = "\x1b[3m"

# 8-bit / truecolor friendly foreground codes
FG_GRAY    = "\x1b[38;5;245m"
FG_BLUE    = "\x1b[38;5;39m"
FG_CYAN    = "\x1b[38;5;45m"
FG_GREEN   = "\x1b[38;5;42m"
FG_YELLOW  = "\x1b[38;5;214m"
FG_RED     = "\x1b[38;5;203m"
FG_MAGENTA = "\x1b[38;5;177m"
FG_PURPLE  = "\x1b[38;5;141m"
FG_TEAL    = "\x1b[38;5;80m"
FG_ORANGE  = "\x1b[38;5;208m"


LEVEL_STYLE = {
    "DEBUG":    f"{DIM}{FG_GRAY}",
    "INFO":     f"{FG_GREEN}",
    "WARNING":  f"{BOLD}{FG_YELLOW}",
    "ERROR":    f"{BOLD}{FG_RED}",
    "CRITICAL": f"{BOLD}{FG_MAGENTA}",
}

LEVEL_GLYPH = {
    "DEBUG":    "·",
    "INFO":     "•",
    "WARNING":  "!",
    "ERROR":    "✕",
    "CRITICAL": "✕",
}

LEVEL_LABEL = {
    "DEBUG":    "DEBUG",
    "INFO":     "INFO ",
    "WARNING":  "WARN ",
    "ERROR":    "ERROR",
    "CRITICAL": "CRIT ",
}

# Stable per-component color (keeps log scanning easy).
COMPONENT_COLORS = {
    "Main":         FG_CYAN,
    "Proxy":        FG_BLUE,
    "Fronter":      FG_PURPLE,
    "H2":           FG_TEAL,
    "MITM":         FG_ORANGE,
    "Cert":         FG_MAGENTA,
}


# ─── color support detection ───────────────────────────────────────────────

def _supports_color(stream) -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("DFT_NO_COLOR") == "1":
        return False
    if os.environ.get("FORCE_COLOR") or os.environ.get("DFT_FORCE_COLOR"):
        return True
    if not hasattr(stream, "isatty") or not stream.isatty():
        return False
    if sys.platform != "win32":
        return True
    # Try to enable ANSI on Windows 10+ consoles.
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
        # -11 = STD_OUTPUT_HANDLE
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_ulong()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        if kernel32.SetConsoleMode(
            handle, mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING
        ):
            return True
    except Exception:
        return False
    return False


# ─── formatter ─────────────────────────────────────────────────────────────

class PrettyFormatter(logging.Formatter):
    """Column-aligned formatter with optional ANSI colors."""

    COMPONENT_WIDTH = 8

    def __init__(self, *, use_color: bool):
        super().__init__()
        self.use_color = use_color
        self._start = time.time()

    # -- helpers ------------------------------------------------------------

    def _c(self, code: str) -> str:
        return code if self.use_color else ""

    def _fmt_time(self, record: logging.LogRecord) -> str:
        t = time.localtime(record.created)
        ms = int((record.created - int(record.created)) * 1000)
        return f"{time.strftime('%H:%M:%S', t)}"

    def _fmt_level(self, levelname: str) -> str:
        label = LEVEL_LABEL.get(levelname, levelname[:5].ljust(5))
        glyph = LEVEL_GLYPH.get(levelname, "·")
        style = LEVEL_STYLE.get(levelname, "")
        if self.use_color:
            return f"{style}{glyph} {label}{RESET}"
        return f"{glyph} {label}"

    def _fmt_component(self, name: str) -> str:
        label = name[: self.COMPONENT_WIDTH].ljust(self.COMPONENT_WIDTH)
        if not self.use_color:
            return f"[{label}]"
        color = COMPONENT_COLORS.get(name, FG_GRAY)
        return f"{DIM}[{RESET}{color}{label}{RESET}{DIM}]{RESET}"

    def format(self, record: logging.LogRecord) -> str:
        # Pre-render message (honors %-args and {}-args).
        try:
            message = record.getMessage()
        except Exception:
            message = record.msg

        time_part  = self._fmt_time(record)
        level_part = self._fmt_level(record.levelname)
        comp_part  = self._fmt_component(record.name)

        if self.use_color:
            time_part = f"{DIM}{FG_GRAY}{time_part}{RESET}"

        line = f"{time_part}  {level_part}  {comp_part}  {message}"

        # Exception tracebacks: render dimmed below the main line.
        if record.exc_info:
            tb = self.formatException(record.exc_info)
            if self.use_color:
                tb = f"{DIM}{FG_GRAY}{tb}{RESET}"
            line = f"{line}\n{tb}"
        if record.stack_info:
            si = record.stack_info
            if self.use_color:
                si = f"{DIM}{FG_GRAY}{si}{RESET}"
            line = f"{line}\n{si}"

        return line


# ─── public API ────────────────────────────────────────────────────────────

def configure(level: str = "INFO", *, stream=None) -> None:
    """Install the pretty formatter on the root logger.

    Safe to call multiple times; replaces prior handlers set up by this
    module and leaves unrelated handlers alone (for tests / embedding).
    """
    stream = stream or sys.stderr
    use_color = _supports_color(stream)

    handler = logging.StreamHandler(stream)
    handler.setFormatter(PrettyFormatter(use_color=use_color))
    handler.set_name("mhrvpn.pretty")

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Remove previous pretty handler(s) we installed.
    for h in list(root.handlers):
        if getattr(h, "name", "") == "mhrvpn.pretty":
            root.removeHandler(h)
    root.addHandler(handler)

    # Suppress cosmetic asyncio warning spam:
    #   "returning true from eof_received() has no effect when using ssl"
    # It originates in Python's own StreamReaderProtocol when we wrap a
    # stream in TLS via start_tls(); there's nothing actionable to do.
    _install_asyncio_noise_filter()


class _AsyncioNoiseFilter(logging.Filter):
    _SUPPRESSED = (
        "returning true from eof_received() has no effect when using ssl",
    )

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: D401
        try:
            msg = record.getMessage()
        except Exception:
            return True
        return not any(s in msg for s in self._SUPPRESSED)


def _install_asyncio_noise_filter() -> None:
    f = _AsyncioNoiseFilter()
    aio = logging.getLogger("asyncio")
    # Don't stack duplicates on repeat configure() calls.
    for existing in list(aio.filters):
        if isinstance(existing, _AsyncioNoiseFilter):
            aio.removeFilter(existing)
    aio.addFilter(f)


def print_banner(version: str, *, stream=None) -> None:
    """Print a polished startup banner with color fallbacks."""
    stream = stream or sys.stderr
    color = _supports_color(stream)

    def c(code: str) -> str:
        return code if color else ""

    title = "MasterHttpRelayVPN"
    subtitle = "Domain-Fronted Apps Script Relay"
    version_tag = f"v{version}"

    left = f" {title} "
    center = f" {subtitle} "
    right = f" {version_tag} "
    inner_width = max(68, len(left) + len(center) + len(right) + 2)

    gap = inner_width - (len(left) + len(center) + len(right))
    left_gap = gap // 2
    right_gap = gap - left_gap

    top = "╭" + ("─" * inner_width) + "╮"
    mid = "│" + left + (" " * left_gap) + center + (" " * right_gap) + right + "│"
    bot = "╰" + ("─" * inner_width) + "╯"

    if color:
        top = f"{DIM}{FG_GRAY}{top}{RESET}"
        bot = f"{DIM}{FG_GRAY}{bot}{RESET}"
        mid = (
            f"{DIM}{FG_GRAY}│{RESET}"
            f"{BOLD}{FG_CYAN}{left}{RESET}"
            f"{' ' * left_gap}"
            f"{FG_GRAY}{center}{RESET}"
            f"{' ' * right_gap}"
            f"{BOLD}{FG_TEAL}{right}{RESET}"
            f"{DIM}{FG_GRAY}│{RESET}"
        )

    print(top, file=stream)
    print(mid, file=stream)
    print(bot, file=stream)
    stream.flush()
