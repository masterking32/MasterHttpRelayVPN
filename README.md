# MasterHttpRelayVPN

[![GitHub](https://img.shields.io/badge/GitHub-MasterHttpRelayVPN-blue?logo=github)](https://github.com/masterking32/MasterHttpRelayVPN)

A local HTTP proxy that bypasses DPI (Deep Packet Inspection) censorship using **domain fronting**. The proxy tunnels all browser traffic through a CDN — the TLS SNI shows an allowed domain (e.g. `www.google.com`) while the encrypted HTTP Host header routes to your relay endpoint.

> **Branch:** `python_testing` — Python implementation

## How It Works

```
Browser ──► Local Proxy ──► CDN (TLS SNI: www.google.com) ──► Your Relay ──► Target Website
                              DPI sees: "www.google.com" ✓
                              Actual destination: hidden
```

The DPI/firewall only sees the SNI field in the TLS handshake, which shows an innocuous, unblockable domain. The real destination is hidden inside the encrypted HTTP stream.

## Supported Modes

| Mode | Relay | Description |
|------|-------|-------------|
| `apps_script` | Google Apps Script | Fronts through `www.google.com` → `script.google.com`. Free, no server needed. |
| `google_fronting` | Google Cloud Run | Fronts through Google IP → your Cloud Run service. |
| `domain_fronting` | Cloudflare Worker | Classic domain fronting via Cloudflare CDN. |
| `custom_domain` | Custom domain on CF | Direct connection to your custom domain on Cloudflare. |

## Quick Start

### 1. Install

```bash
# Clone the repository
git clone -b python_testing https://github.com/masterking32/MasterHttpRelayVPN.git
cd MasterHttpRelayVPN

# (Optional) Create a virtual environment
python -m venv venv
source venv/bin/activate   # Linux/macOS
venv\Scripts\activate      # Windows

# Install dependencies
pip install -r requirements.txt
```

> **Note:** Python 3.10+ is required. Core functionality has no external dependencies. The optional packages (`cryptography`, `h2`) enable MITM interception and HTTP/2 multiplexing for the `apps_script` mode.

### 2. Deploy the Relay (Code.gs — Google Apps Script)

Before running the local proxy, you need a relay endpoint. The easiest (free) option is Google Apps Script using the included `Code.gs` file.

1. Go to [Google Apps Script](https://script.google.com/) and create a **New project**.
2. Delete the default code in the editor.
3. Copy the entire contents of the [`Code.gs`](Code.gs) file from this repository and paste it into the Apps Script editor.
4. **Change the `AUTH_KEY`** at the top of the script to your own strong secret:
   ```javascript
   const AUTH_KEY = "your-strong-secret-key";
   ```
5. Click **Deploy → New deployment**.
6. Set the deployment type to **Web app**:
   - **Execute as:** Me
   - **Who has access:** Anyone
7. Click **Deploy** and copy the **Deployment ID** (not the URL).
8. Paste the Deployment ID into your `config.json` as the `script_id` value.

> **Important:** The `AUTH_KEY` in `Code.gs` must match the `auth_key` in your `config.json` exactly.

#### How Code.gs Works

`Code.gs` is a Google Apps Script relay that receives HTTP requests from the local proxy (via domain fronting) and forwards them to the actual target websites. It supports two modes:

| Mode | Request Format | Description |
|------|---------------|-------------|
| **Single** | `{ k, m, u, h, b, ct, r }` | Fetches one URL and returns `{ s, h, b }` (status, headers, body) |
| **Batch** | `{ k, q: [{m,u,h,b,ct,r}, ...] }` | Fetches multiple URLs **in parallel** using `UrlFetchApp.fetchAll()` and returns `{ q: [{s,h,b}, ...] }` |

**Request fields:**
- `k` — Auth key (must match `AUTH_KEY`)
- `m` — HTTP method (`GET`, `POST`, etc.)
- `u` — Target URL
- `h` — Request headers (object)
- `b` — Request body (base64-encoded)
- `ct` — Content-Type
- `r` — Follow redirects (`true`/`false`)

**Response fields:**
- `s` — HTTP status code
- `h` — Response headers
- `b` — Response body (base64-encoded)
- `e` — Error message (if any)

#### Updating Code.gs

When you update `Code.gs`, you must create a **new deployment** in Apps Script (Deploy → New deployment) and update the `script_id` in your `config.json` with the new Deployment ID. Editing the code alone does not update a live deployment.

### 3. Configure the Local Proxy

```bash
cp config.example.json config.json
```

Edit `config.json` with your values:

```json
{
  "mode": "apps_script",
  "google_ip": "216.239.38.120",
  "front_domain": "www.google.com",
  "script_id": "YOUR_APPS_SCRIPT_DEPLOYMENT_ID",
  "auth_key": "your-strong-secret-key",
  "listen_host": "127.0.0.1",
  "listen_port": 8085,
  "log_level": "INFO",
  "verify_ssl": true
}
```

### 4. Run

```bash
python main.py
```

### 5. Configure Your Browser

Set your browser's HTTP proxy to `127.0.0.1:8085` (or whatever `listen_host`:`listen_port` you configured).

For `apps_script` mode, you also need to install the generated CA certificate (`ca/ca.crt`) in your browser's trusted root CAs.

## Configuration Reference

### Required Fields

| Field | Description |
|-------|-------------|
| `mode` | One of: `apps_script`, `google_fronting`, `domain_fronting`, `custom_domain` |
| `auth_key` | Shared secret between the proxy and your relay endpoint (must match `AUTH_KEY` in `Code.gs`) |

### Mode-Specific Fields

| Field | Modes | Description |
|-------|-------|-------------|
| `script_id` | `apps_script` | Your deployed Apps Script Deployment ID (or array of IDs for load balancing) |
| `worker_host` | `domain_fronting`, `google_fronting` | Your Worker/Cloud Run hostname |
| `custom_domain` | `custom_domain` | Your custom domain on Cloudflare |
| `front_domain` | `domain_fronting`, `google_fronting`, `apps_script` | The domain shown in TLS SNI (default: `www.google.com`) |
| `google_ip` | `google_fronting`, `apps_script` | Google IP to connect to (default: `216.239.38.120`) |

### Optional Fields

| Field | Default | Description |
|-------|---------|-------------|
| `listen_host` | `127.0.0.1` | Local proxy bind address |
| `listen_port` | `8080` | Local proxy port |
| `log_level` | `INFO` | Logging level: `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `verify_ssl` | `true` | Verify TLS certificates |
| `worker_path` | `""` | URL path prefix for the worker |
| `script_ids` | — | Array of Apps Script IDs for round-robin load balancing |

## Environment Variables

All settings can be overridden via environment variables (useful for containers/CI):

| Variable | Overrides |
|----------|-----------|
| `DFT_CONFIG` | Config file path |
| `DFT_AUTH_KEY` | `auth_key` |
| `DFT_SCRIPT_ID` | `script_id` |
| `DFT_PORT` | `listen_port` |
| `DFT_HOST` | `listen_host` |
| `DFT_LOG_LEVEL` | `log_level` |

## CLI Usage

```
usage: domainfront-tunnel [-h] [-c CONFIG] [-p PORT] [--host HOST]
                          [--log-level {DEBUG,INFO,WARNING,ERROR}] [-v]

options:
  -c, --config CONFIG   Path to config file (default: config.json)
  -p, --port PORT       Override listen port
  --host HOST           Override listen host
  --log-level LEVEL     Override log level
  -v, --version         Show version and exit
```

### Examples

```bash
# Basic usage
python main.py

# Custom config file
python main.py -c /path/to/my-config.json

# Override port
python main.py -p 9090

# Debug logging
python main.py --log-level DEBUG

# Using environment variables
DFT_AUTH_KEY=my-secret DFT_PORT=9090 python main.py
```

## Multiple Script IDs (Load Balancing)

For higher throughput, deploy multiple copies of `Code.gs` to separate Apps Script projects and use an array:

```json
{
  "script_ids": [
    "DEPLOYMENT_ID_1",
    "DEPLOYMENT_ID_2",
    "DEPLOYMENT_ID_3"
  ]
}
```

The proxy will distribute requests across all IDs in round-robin fashion.

## Architecture

```
┌─────────┐     ┌──────────────┐     ┌─────────────┐     ┌──────────┐
│ Browser  │────►│ Local Proxy  │────►│ CDN / Google │────►│  Relay   │──► Internet
│          │◄────│ (this tool)  │◄────│  (fronted)   │◄────│ Endpoint │◄──
└─────────┘     └──────────────┘     └─────────────┘     └──────────┘
                  HTTP/CONNECT         TLS (SNI: ok)        Fetch target
                  MITM (optional)      Host: relay          Return response
```

### Project Structure

| File | Purpose |
|------|---------|
| `main.py` | Entry point, config loading, CLI |
| `proxy_server.py` | Local HTTP/CONNECT proxy server |
| `domain_fronter.py` | Domain fronting engine, connection pooling, relay logic |
| `h2_transport.py` | HTTP/2 multiplexed transport (optional, for performance) |
| `mitm.py` | MITM certificate manager for HTTPS interception |
| `ws.py` | WebSocket frame encoder/decoder (RFC 6455) |
| `Code.gs` | Google Apps Script relay — deploy this to Apps Script as your relay endpoint |
| `config.example.json` | Example configuration file — copy to `config.json` |
| `requirements.txt` | Python dependencies |

## Performance Features

- **HTTP/2 multiplexing**: Single TLS connection handles 100+ concurrent requests
- **Connection pooling**: Pre-warmed TLS connection pool with automatic maintenance
- **Request batching**: Groups concurrent requests into single relay calls (uses batch mode in `Code.gs`)
- **Request coalescing**: Deduplicates identical concurrent GET requests
- **Parallel range downloads**: Splits large downloads into concurrent chunks
- **Response caching**: LRU cache for static assets (configurable, 50 MB default)

## Security Notes

- **Never commit `config.json`** — it contains your `auth_key`. The `.gitignore` excludes it.
- **Change the default `AUTH_KEY` in `Code.gs`** before deploying — the default value is not secure.
- The `ca/` directory contains your generated CA private key. Keep it secure.
- Use a strong, unique `auth_key` to prevent unauthorized use of your relay.
- Set `listen_host` to `127.0.0.1` (not `0.0.0.0`) unless you need LAN access.

## Special Thanks

Special thanks to [@abolix](https://github.com/abolix) for making this project possible.

## License

MIT
