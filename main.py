#!/usr/bin/env python3
"""
DomainFront Tunnel — Bypass DPI censorship via Domain Fronting.

Run a local HTTP proxy that tunnels all traffic through a CDN using
domain fronting: the TLS SNI shows an allowed domain while the encrypted
HTTP Host header routes to your Cloudflare Worker relay.
"""

import argparse
import asyncio
import json
import logging
import os
import sys

from cert_installer import install_ca, is_ca_trusted
from mitm import CA_CERT_FILE
from proxy_server import ProxyServer

__version__ = "1.0.0"


def setup_logging(level_name: str):
    level = getattr(logging, level_name.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(name)-12s] %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )


def parse_args():
    parser = argparse.ArgumentParser(
        prog="domainfront-tunnel",
        description="Local HTTP proxy that tunnels traffic through domain fronting.",
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
        "--no-cert-check",
        action="store_true",
        help="Skip the certificate installation check on startup.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    config_path = args.config

    try:
        with open(config_path) as f:
            config = json.load(f)
    except FileNotFoundError:
        print(f"Config not found: {config_path}")
        print("Copy config.example.json to config.json and fill in your values.")
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

    if args.log_level is not None:
        config["log_level"] = args.log_level
    elif os.environ.get("DFT_LOG_LEVEL"):
        config["log_level"] = os.environ["DFT_LOG_LEVEL"]

    for key in ("auth_key",):
        if key not in config:
            print(f"Missing required config key: {key}")
            sys.exit(1)

    mode = config.get("mode", "domain_fronting")
    if mode == "custom_domain" and "custom_domain" not in config:
        print("Mode 'custom_domain' requires 'custom_domain' in config")
        sys.exit(1)
    if mode == "domain_fronting":
        for key in ("front_domain", "worker_host"):
            if key not in config:
                print(f"Mode 'domain_fronting' requires '{key}' in config")
                sys.exit(1)
    if mode == "google_fronting":
        if "worker_host" not in config:
            print("Mode 'google_fronting' requires 'worker_host' in config (your Cloud Run URL)")
            sys.exit(1)
    if mode == "apps_script":
        sid = config.get("script_ids") or config.get("script_id")
        if not sid or (isinstance(sid, str) and sid == "YOUR_APPS_SCRIPT_DEPLOYMENT_ID"):
            print("Mode 'apps_script' requires 'script_id' in config.")
            print("Deploy the Apps Script from appsscript/Code.gs and paste the Deployment ID.")
            sys.exit(1)

    # ── Certificate installation ──────────────────────────────────────────
    if args.install_cert:
        setup_logging("INFO")
        _log = logging.getLogger("Main")
        _log.info("Installing CA certificate…")
        ok = install_ca(CA_CERT_FILE)
        sys.exit(0 if ok else 1)

    setup_logging(config.get("log_level", "INFO"))
    log = logging.getLogger("Main")

    mode = config.get("mode", "domain_fronting")
    log.info("DomainFront Tunnel starting (mode: %s)", mode)

    if mode == "custom_domain":
        log.info("Custom domain    : %s", config["custom_domain"])
    elif mode == "google_fronting":
        log.info("Google fronting   : SNI=%s → Host=%s",
                 config.get("front_domain", "www.google.com"), config["worker_host"])
        log.info("Google IP         : %s", config.get("google_ip", "216.239.38.120"))
    elif mode == "apps_script":
        log.info("Apps Script relay : SNI=%s → script.google.com",
                 config.get("front_domain", "www.google.com"))
        script_ids = config.get("script_ids") or config.get("script_id")
        if isinstance(script_ids, list):
            log.info("Script IDs        : %d scripts (round-robin)", len(script_ids))
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
    else:
        log.info("Front domain (SNI) : %s", config.get("front_domain", "?"))
        log.info("Worker host (Host) : %s", config.get("worker_host", "?"))

    log.info("Proxy address      : %s:%d", config.get("listen_host", "127.0.0.1"), config.get("listen_port", 8080))

    try:
        asyncio.run(ProxyServer(config).start())
    except KeyboardInterrupt:
        log.info("Stopped")


if __name__ == "__main__":
    main()
