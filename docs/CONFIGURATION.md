# Configuration Reference

Most users only need `script_id`, `auth_key`, and the default local ports. This page explains the rest when you need to tune behavior.

## Required Settings

| Setting | Meaning |
|---------|---------|
| `script_id` | Your Google Apps Script Deployment ID. Use this for one deployment. |
| `script_ids` | Array of Deployment IDs for load balancing. Use instead of `script_id`. |
| `auth_key` | Shared password. Must match `AUTH_KEY` inside [apps_script/Code.gs](../apps_script/Code.gs). |

If you use `script_ids`, every deployed copy of [apps_script/Code.gs](../apps_script/Code.gs) must use the same `AUTH_KEY`.

## Proxy Binding

| Setting | Default | Meaning |
|---------|---------|---------|
| `listen_host` | `127.0.0.1` | Address the proxy listens on. Use `127.0.0.1` for only this computer. |
| `http_port` | `8085` | HTTP proxy port for browsers and most apps. |
| `socks5_port` | `1080` | SOCKS5 proxy port. Some apps resolve hostnames locally, so HTTP proxy is often more reliable. |
| `lan_sharing` | `false` | When true, the app listens on LAN interfaces so other devices can use it. |

See [LAN Sharing](LAN_SHARING.md) before enabling access for other devices.

## Domain Fronting

| Setting | Default | Meaning |
|---------|---------|---------|
| `google_ip` | `216.239.38.120` | Google frontend IP to connect through. |
| `front_domain` | `www.google.com` | Domain visible in the fronted TLS connection. |
| `front_domains` | `www.google.com`, `mail.google.com`, `accounts.google.com` | Optional SNI rotation pool. |
| `verify_ssl` | `true` | Verifies TLS certificates for the Google-facing connection. Keep true in normal use. |

If the current Google IP is blocked or slow, run `python main.py --scan` and use the recommended IP.

## Timeouts And Performance

| Setting | Default | Meaning |
|---------|---------|---------|
| `relay_timeout` | `25` | Maximum time for one relayed request. |
| `tls_connect_timeout` | `15` | Timeout for TLS connection setup to the fronted Google endpoint. |
| `tcp_connect_timeout` | `10` | Timeout for direct TCP tunnels and SNI-rewrite connections. |
| `h2_connections` | `2` | Parallel HTTP/2 connections to the relay. More can improve throughput, but uses more resources. |
| `parallel_relay` | `1` | Number of Apps Script deployments to race per safe request when multiple IDs exist. |
| `enable_sub_batch` | `true` | Allows batch splitting across H2 connections for large or mixed request bursts. |

## Downloads

| Setting | Meaning |
|---------|---------|
| `chunked_download_extensions` | File extensions that can use parallel range downloading. `".*"` probes all GET downloads. |
| `chunked_download_min_size` | Minimum file size before range-parallel downloading remains active. |
| `chunked_download_chunk_size` | Size of each range request. |
| `chunked_download_max_parallel` | Maximum simultaneous range requests for one download. |
| `chunked_download_max_chunks` | Soft maximum chunk count. Chunk size is raised automatically for very large files. |

## Host Policies

| Setting | Meaning |
|---------|---------|
| `block_hosts` | Hosts that should return HTTP 403 and never be tunneled. Supports exact names and `.suffix` patterns. |
| `direct_hosts` | Hosts that should always go direct without MITM or relay fronting. |
| `bypass_hosts` | Local or special hosts that bypass MITM and relay. Useful for `.lan`, `.local`, and internal services. |
| `hosts` | Manual DNS override map. Useful for testing or split-DNS workarounds. |
| `direct_google_exclude` | Google services that should stay on the relay path instead of direct tunnel. |
| `youtube_via_relay` | Routes YouTube through Apps Script relay. Useful if the direct Google path causes playback restrictions. |

Example:

```json
{
  "block_hosts": ["ads.example.com", ".doubleclick.net"],
  "direct_hosts": ["chat.openai.com", ".openai.com"],
  "hosts": {
    "example.org": "93.184.216.34",
    ".internal.lan": "192.168.1.10"
  }
}
```

## Exit Node

Use an exit node when a destination blocks Google datacenter egress.

```json
"exit_node": {
  "enabled": true,
  "provider": "cloudflare",
  "url": "https://YOUR-WORKER.YOUR-SUBDOMAIN.workers.dev",
  "psk": "CHANGE_ME_TO_A_STRONG_SECRET",
  "mode": "full",
  "hosts": ["chatgpt.com", "openai.com"]
}
```

| Setting | Meaning |
|---------|---------|
| `exit_node.enabled` | Turns exit-node routing on or off. |
| `exit_node.provider` | `cloudflare`, `vps`, or `custom`. |
| `exit_node.url` | URL for the selected provider. |
| `exit_node.psk` | Shared secret for the exit node. Must match the deployed exit-node code. |
| `exit_node.mode` | `full` for all relayed traffic, `selective` for only listed hosts. |
| `exit_node.hosts` | Host list used by selective mode. |

Deployment steps are in [Exit Node Guide](exit-node/EXIT_NODE_DEPLOYMENT.md).

## Ad Blocking

`adblock_lists` accepts host/domain filter list URLs. The default config uses PersianBlocker lists. Remove the list or set it empty if you do not want this behavior.

## Optional Dependencies

Install everything from [requirements.txt](../requirements.txt) for the full feature set.

| Package | Provides |
|---------|----------|
| `cryptography` | Local MITM certificate generation and HTTPS interception. |
| `h2` | HTTP/2 transport to Apps Script. |
| `brotli` | `Content-Encoding: br` decoding. |
| `zstandard` | `Content-Encoding: zstd` decoding. |

## Command Line Options

```bash
python main.py                          # Start normally
python main.py -p 9090                  # Override HTTP port
python main.py --socks5-port 1081       # Override SOCKS5 port
python main.py --host 0.0.0.0           # Override listen host
python main.py --log-level DEBUG        # More logs
python main.py -c path/to/config.json   # Use another config file
python main.py --install-cert           # Install CA and exit
python main.py --uninstall-cert         # Remove CA and exit
python main.py --no-cert-check          # Skip automatic CA trust check
python main.py --scan                   # Find a faster reachable Google IP
```

Environment overrides are also supported: `DFT_CONFIG`, `DFT_AUTH_KEY`, `DFT_SCRIPT_ID`, `DFT_HTTP_PORT`, `DFT_PORT`, `DFT_HOST`, `DFT_SOCKS5_PORT`, and `DFT_LOG_LEVEL`.

## Diagnostic Commands

Scan Google fronting IPs:

```bash
python main.py --scan
```

Install or remove the local CA:

```bash
python main.py --install-cert
python main.py --uninstall-cert
```

Show detailed logs:

```bash
python main.py --log-level DEBUG
```
