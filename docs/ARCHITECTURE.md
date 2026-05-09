# Architecture

MasterHttpRelayVPN is a local proxy plus a user-deployed relay.

## Simple Flow

```text
Browser or app
  -> local HTTP/SOCKS5 proxy
  -> Google-facing fronted TLS connection
  -> Apps Script relay
  -> target website
```

The network sees a Google-facing connection. The relay request carries the real target URL inside encrypted traffic.

## Main Pieces

| File or folder | Purpose |
|----------------|---------|
| [main.py](../main.py) | CLI entry point. Loads config, handles cert commands, starts the proxy. |
| [setup.py](../setup.py) | Interactive wizard that writes `config.json`. |
| [start.bat](../start.bat) | Windows one-click launcher. Creates venv, installs deps, runs setup, starts proxy. |
| [start.sh](../start.sh) | Linux/macOS launcher with the same role. |
| [config.example.json](../config.example.json) | Example configuration and defaults. |
| [apps_script/Code.gs](../apps_script/Code.gs) | Google Apps Script relay deployed by the user. |
| [src/proxy/proxy_server.py](../src/proxy/proxy_server.py) | HTTP CONNECT, MITM routing, SOCKS5, host policy decisions. |
| [src/proxy/mitm.py](../src/proxy/mitm.py) | Local certificate authority and generated leaf certificates. |
| [src/relay/domain_fronter.py](../src/relay/domain_fronter.py) | Apps Script relay client, batching, retries, H1/H2 transport selection. |
| [src/relay/h2_transport.py](../src/relay/h2_transport.py) | Optional HTTP/2 transport for multiplexing. |
| [src/core/cert_installer.py](../src/core/cert_installer.py) | CA install/uninstall support for OS and Firefox stores. |
| [src/core/google_ip_scanner.py](../src/core/google_ip_scanner.py) | Google frontend scanner used by `python main.py --scan`. |

## Request Handling

1. The browser sends HTTP or HTTPS proxy traffic to `127.0.0.1:8085`.
2. For HTTPS, the proxy can perform local MITM using the generated CA.
3. Host rules decide whether traffic is direct, blocked, bypassed, or relayed.
4. Relayed requests are encoded as JSON for Apps Script.
5. Apps Script fetches the destination and returns a serialized HTTP response.
6. The local proxy reconstructs the HTTP response for the browser.

## Performance Features

- Warm TLS connection pool for H1 fallback.
- HTTP/2 multiplexing when the `h2` package is installed.
- Batching for static sub-resource bursts.
- Optional multiple `script_ids` for load balancing.
- Optional range-parallel download acceleration for large files.
- Optional exit node for destinations that block Google egress.

## Exit Node Path

```text
Browser -> Local proxy -> Apps Script -> Exit node -> Target website
```

Exit nodes can run on Cloudflare Workers or your own VPS. See [Exit Node Guide](exit-node/EXIT_NODE_DEPLOYMENT.md).
