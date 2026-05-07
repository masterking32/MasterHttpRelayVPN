# main.py
import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from cert_installer import install_ca, is_ca_trusted
from mitm import CA_CERT_FILE
from proxy_server import ProxyServer

__version__ = "1.0.0"

def parse_args():
    p = argparse.ArgumentParser(prog="domainfront-tunnel")
    p.add_argument("-c", "--config", default=os.environ.get("DFT_CONFIG", "config.json"))
    p.add_argument("-p", "--port", type=int, default=os.environ.get("DFT_PORT"))
    p.add_argument("--host", default=os.environ.get("DFT_HOST"))
    p.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"], default=os.environ.get("DFT_LOG_LEVEL"))
    p.add_argument("-v", "--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--install-cert", action="store_true")
    p.add_argument("--no-cert-check", action="store_true")
    return p.parse_args()

def main():
    args = parse_args()
    try:
        config = json.loads(Path(args.config).read_text())
    except Exception:
        sys.exit(1)
    env_map = {"DFT_AUTH_KEY": "auth_key", "DFT_SCRIPT_ID": "script_id"}
    for env_k, cfg_k in env_map.items():
        if os.environ.get(env_k): config[cfg_k] = os.environ[env_k]
    if args.port: config["listen_port"] = args.port
    if args.host: config["listen_host"] = args.host
    if args.log_level: config["log_level"] = args.log_level
    if "auth_key" not in config: 
        sys.exit(1)
    mode = config.get("mode", "domain_fronting")
    reqs = {"custom_domain": ["custom_domain"], "domain_fronting": ["front_domain", "worker_host"], "google_fronting": ["worker_host"]}
    for r in reqs.get(mode, []):
        if r not in config: 
            sys.exit(1)
    if mode == "apps_script":
        sid = config.get("script_ids") or config.get("script_id")
        if not sid or sid == "YOUR_APPS_SCRIPT_DEPLOYMENT_ID":
            sys.exit(1)
    if args.install_cert:
        sys.exit(0 if install_ca(CA_CERT_FILE) else 1)
    if mode == "apps_script":
        if not Path(CA_CERT_FILE).exists():
            from mitm import MITMCertManager
            MITMCertManager()
        if not args.no_cert_check and not is_ca_trusted(CA_CERT_FILE):
            install_ca(CA_CERT_FILE)
    try:
        asyncio.run(ProxyServer(config).start())
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
