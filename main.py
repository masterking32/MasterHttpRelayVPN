#!/usr/bin/env python3
"""
DomainFront Tunnel — Bypass DPI censorship via Google Apps Script.

Run a local HTTP proxy that tunnels all traffic through a Google Apps
Script relay fronted by www.google.com (TLS SNI shows www.google.com
while the encrypted Host header points at script.google.com).
"""

import argparse
import asyncio
import json
import logging
import os
import sys

# Project modules live under ./src — put that folder on sys.path so the
# historical flat imports ("from proxy_server import …") keep working.
_SRC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from cert_installer import install_ca, uninstall_ca, is_ca_trusted
from constants import __version__
from lan_utils import log_lan_access
from google_ip_scanner import scan_sync
from logging_utils import configure as configure_logging, print_banner
from mitm import CA_CERT_FILE
from proxy_server import ProxyServer


def setup_logging(level_name: str):
    configure_logging(level_name)


_PLACEHOLDER_AUTH_KEYS = {
    "",
    "CHANGE_ME_TO_A_STRONG_SECRET",
    "your-secret-password-here",
}


def parse_args():
    parser = argparse.ArgumentParser(
        prog="domainfront-tunnel",
        description="Local HTTP proxy that relays traffic through Google Apps Script.",
    )
    parser.add_argument(
        "-c", "--config",
        default=os.environ.get("DFT_CONFIG", "config.json"),
        help="Path to config file (default: config.json, env: DFT_CONFIG)",
    )
    parser.add_argument(
        "-p", "--port",
        type=int,
        default=None,
        help="Override listen port (env: DFT_PORT)",
    )
    parser.add_argument(
        "--host",
        default=None,
        help="Override listen host (env: DFT_HOST)",
    )
    parser.add_argument(
        "--socks5-port",
        type=int,
        default=None,
        help="Override SOCKS5 listen port (env: DFT_SOCKS5_PORT)",
    )
    parser.add_argument(
        "--disable-socks5",
        action="store_true",
        help="Disable the built-in SOCKS5 listener.",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default=None,
        help="Override log level (env: DFT_LOG_LEVEL)",
    )
    parser.add_argument(
        "-v", "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--install-cert",
        action="store_true",
        help="Install the MITM CA certificate as a trusted root and exit.",
    )
    parser.add_argument(
        "--uninstall-cert",
        action="store_true",
        help="Remove the MITM CA certificate from trusted roots and exit.",
    )
    parser.add_argument(
        "--no-cert-check",
        action="store_true",
        help="Skip the certificate installation check on startup.",
    )
    parser.add_argument(
        "--scan",
        action="store_true",
        help="Scan Google IPs to find the fastest reachable one and exit.",
    )
    parser.add_argument(
        "--update-adblock",
        action="store_true",
        help="Force-refresh all adblock blocklists, update the cache, and exit.",
    )
    parser.add_argument(
        "--check-update",
        action="store_true",
        help="Check if a newer version is available and print the result, then exit.",
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help=(
            "Check for a newer version; if found, download and apply it, then exit. "
            "Exit code 2 means an update was applied (re-run pip install afterwards)."
        ),
    )
    return parser.parse_args()


def _run_update_check(args, current_version: str, project_root: str) -> None:
    """Handle --check-update and --update flags, then sys.exit."""
    setup_logging("INFO")
    _log = logging.getLogger("Updater")

    from updater import check_update, apply_update
    from pathlib import Path

    _log.info("Checking for updates (current: v%s) …", current_version)
    release = check_update(current_version)

    if release is None:
        _log.info("Already up to date (v%s).", current_version)
        sys.exit(0)

    _log.info(
        "New version available: v%s  →  %s",
        release["version"], release["html_url"],
    )
    if release["body"]:
        print("\nRelease notes:\n" + release["body"] + "\n")

    if args.check_update:
        # Just report — don't apply.
        sys.exit(0)

    # --update: apply it
    ok = apply_update(release, Path(project_root))
    if not ok:
        _log.error("Update failed. Check the output above.")
        sys.exit(1)

    _log.info(
        "Update applied. Re-run pip install -r requirements.txt "
        "to pick up any new dependencies."
    )
    sys.exit(2)   # exit 2 = update applied; start scripts re-run pip install


def main():
    args = parse_args()

    # ── Update / version check (no config needed) ─────────────────────────
    if args.check_update or args.update:
        _run_update_check(
            args,
            current_version=__import__("constants", fromlist=["__version__"]).__version__,
            project_root=os.path.dirname(os.path.abspath(__file__)),
        )

    # Handle cert-only commands before loading config so they can run standalone.
    if args.install_cert or args.uninstall_cert:
        setup_logging("INFO")
        _log = logging.getLogger("Main")

        if args.install_cert:
            _log.info("Installing CA certificate…")
            if not os.path.exists(CA_CERT_FILE):
                from mitm import MITMCertManager
                MITMCertManager()  # side-effect: creates ca/ca.crt + ca/ca.key
            ok = install_ca(CA_CERT_FILE)
            sys.exit(0 if ok else 1)

        _log.info("Removing CA certificate…")
        ok = uninstall_ca(CA_CERT_FILE)
        if ok:
            _log.info("CA certificate removed successfully.")
        else:
            _log.warning("CA certificate removal may have failed. Check logs above.")
        sys.exit(0 if ok else 1)

    config_path = args.config

    try:
        with open(config_path) as f:
            config = json.load(f)
    except FileNotFoundError:
        print(f"Config not found: {config_path}")
        # Offer the interactive wizard if it's available and we're on a TTY.
        wizard = os.path.join(os.path.dirname(os.path.abspath(__file__)), "setup.py")
        if os.path.exists(wizard) and sys.stdin.isatty():
            try:
                answer = input("Run the interactive setup wizard now? [Y/n]: ").strip().lower()
            except EOFError:
                answer = "n"
            if answer in ("", "y", "yes"):
                import subprocess
                rc = subprocess.call([sys.executable, wizard])
                if rc != 0:
                    sys.exit(rc)
                try:
                    with open(config_path) as f:
                        config = json.load(f)
                except Exception as e:
                    print(f"Could not load config after setup: {e}")
                    sys.exit(1)
            else:
                print("Copy config.example.json to config.json and fill in your values,")
                print("or run: python setup.py")
                sys.exit(1)
        else:
            print("Run: python setup.py   (or copy config.example.json to config.json)")
            sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Invalid JSON in config: {e}")
        sys.exit(1)

    # Environment variable overrides
    if os.environ.get("DFT_AUTH_KEY"):
        config["auth_key"] = os.environ["DFT_AUTH_KEY"]
    if os.environ.get("DFT_SCRIPT_ID"):
        config["script_id"] = os.environ["DFT_SCRIPT_ID"]

    # CLI argument overrides
    if args.port is not None:
        config["listen_port"] = args.port
    elif os.environ.get("DFT_PORT"):
        config["listen_port"] = int(os.environ["DFT_PORT"])

    if args.host is not None:
        config["listen_host"] = args.host
    elif os.environ.get("DFT_HOST"):
        config["listen_host"] = os.environ["DFT_HOST"]

    if args.socks5_port is not None:
        config["socks5_port"] = args.socks5_port
    elif os.environ.get("DFT_SOCKS5_PORT"):
        config["socks5_port"] = int(os.environ["DFT_SOCKS5_PORT"])

    if args.disable_socks5:
        config["socks5_enabled"] = False

    if args.log_level is not None:
        config["log_level"] = args.log_level
    elif os.environ.get("DFT_LOG_LEVEL"):
        config["log_level"] = os.environ["DFT_LOG_LEVEL"]

    for key in ("auth_key",):
        if key not in config:
            print(f"Missing required config key: {key}")
            sys.exit(1)

    if config.get("auth_key", "") in _PLACEHOLDER_AUTH_KEYS:
        print(
            "Refusing to start: 'auth_key' is unset or uses a known placeholder.\n"
            "Pick a long random secret and set it in both config.json AND "
            "the AUTH_KEY constant inside Code.gs (they must match)."
        )
        sys.exit(1)

    # Always Apps Script mode — force-set for backward-compat configs.
    config["mode"] = "apps_script"
    sid = config.get("script_ids") or config.get("script_id")
    if not sid or (isinstance(sid, str) and sid == "YOUR_APPS_SCRIPT_DEPLOYMENT_ID"):
        print("Missing 'script_id' in config.")
        print("Deploy the Apps Script from Code.gs and paste the Deployment ID.")
        sys.exit(1)

    # ── Adblock force-refresh ─────────────────────────────────────────────
    if args.update_adblock:
        setup_logging(config.get("log_level", "INFO"))
        _log = logging.getLogger("Main")
        adblock_cfg = config.get("adblock", False)
        if adblock_cfg is True:
            adblock_cfg = {}
        if isinstance(adblock_cfg, dict) and not adblock_cfg.get("enabled", True):
            adblock_cfg = None
        if not adblock_cfg:
            _log.error(
                "adblock is not enabled in config.json. "
                "Set \"adblock\": {\"enabled\": true} to use this feature."
            )
            sys.exit(1)
        sys.path.insert(0, _SRC_DIR)
        from adblock import AdBlocker, DEFAULT_SOURCES, DEFAULT_UPDATE_HOURS
        blocker = AdBlocker(
            cache_dir=adblock_cfg.get("cache_dir", "adblock_cache"),
            sources=adblock_cfg.get("sources", DEFAULT_SOURCES),
            update_hours=adblock_cfg.get("update_interval_hours", DEFAULT_UPDATE_HOURS),
        )
        async def _do_update():
            await blocker.load(force=True)
        asyncio.run(_do_update())
        _log.info("Adblock lists updated successfully.")
        sys.exit(0)

    # ── Google IP Scanner ──────────────────────────────────────────────────
    if args.scan:
        setup_logging("INFO")
        front_domain = config.get("front_domain", "www.google.com")
        _log = logging.getLogger("Main")
        _log.info(f"Scanning Google IPs (fronting domain: {front_domain})")
        ok = scan_sync(front_domain)
        sys.exit(0 if ok else 1)

    setup_logging(config.get("log_level", "INFO"))
    log = logging.getLogger("Main")

    print_banner(__version__)
    log.info("DomainFront Tunnel starting (Apps Script relay)")

    log.info("Apps Script relay : SNI=%s → script.google.com",
             config.get("front_domain", "www.google.com"))
    script_ids = config.get("script_ids") or config.get("script_id")
    if isinstance(script_ids, list):
        log.info("Script IDs        : %d scripts (sticky per-host)", len(script_ids))
        for i, sid in enumerate(script_ids):
            log.info("  [%d] %s", i + 1, sid)
    else:
        log.info("Script ID         : %s", script_ids)

    # Ensure CA file exists before checking / installing it.
    # MITMCertManager generates ca/ca.crt on first instantiation.
    if not os.path.exists(CA_CERT_FILE):
        from mitm import MITMCertManager
        MITMCertManager()  # side-effect: creates ca/ca.crt + ca/ca.key

    # Auto-install MITM CA if not already trusted
    if not args.no_cert_check:
        if not is_ca_trusted(CA_CERT_FILE):
            log.warning("MITM CA is not trusted — attempting automatic installation…")
            ok = install_ca(CA_CERT_FILE)
            if ok:
                log.info("CA certificate installed. You may need to restart your browser.")
            else:
                log.error(
                    "Auto-install failed. Run with --install-cert (may need admin/sudo) "
                    "or manually install ca/ca.crt as a trusted root CA."
                )
        else:
            log.info("MITM CA is already trusted.")

    # ── LAN sharing configuration ────────────────────────────────────────
    lan_sharing = config.get("lan_sharing", False)
    listen_host = config.get("listen_host", "127.0.0.1")
    if lan_sharing:
        # If LAN sharing is enabled and host is still localhost, change to all interfaces
        if listen_host == "127.0.0.1":
            config["listen_host"] = "0.0.0.0"
            listen_host = "0.0.0.0"
            log.info("LAN sharing enabled — listening on all interfaces")

    # If either explicit LAN sharing is enabled or we bind to all interfaces,
    # print concrete IPv4 addresses users can use on other devices.
    lan_mode = lan_sharing or listen_host in ("0.0.0.0", "::")
    if lan_mode:
        socks_port = config.get("socks5_port", 1080) if config.get("socks5_enabled", True) else None
        log_lan_access(config.get("listen_port", 8080), socks_port)

    try:
        asyncio.run(_run(config))
    except KeyboardInterrupt:
        log.info("Stopped")


def _make_exception_handler(log):
    """Return an asyncio exception handler that silences Windows WinError 10054
    noise from connection cleanup (ConnectionResetError in
    _ProactorBasePipeTransport._call_connection_lost), which is harmless but
    verbose on Python/Windows when a remote host force-closes a socket."""
    def handler(loop, context):
        exc = context.get("exception")
        cb  = context.get("handle") or context.get("source_traceback", "")
        if (
            isinstance(exc, ConnectionResetError)
            and "_call_connection_lost" in str(cb)
        ):
            return  # suppress: benign Windows socket cleanup race
        log.error("[asyncio]  %s", context.get("message", context))
        if exc:
            loop.default_exception_handler(context)
    return handler


async def _run(config):
    loop = asyncio.get_running_loop()
    _log = logging.getLogger("asyncio")
    loop.set_exception_handler(_make_exception_handler(_log))
    server = ProxyServer(config)
    try:
        await server.start()
    finally:
        await server.stop()


if __name__ == "__main__":
    main()
