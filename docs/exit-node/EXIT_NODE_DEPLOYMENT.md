# Exit Node Deployment Guide (Cloudflare / VPS)

This guide explains how to deploy an exit node for MasterHttpRelayVPN on free platforms or your own VPS server.

Traffic path:

Browser -> Local Proxy -> Apps Script -> Exit Node -> Target Website

Use this when destinations block Google datacenter egress.

## 1) Choose One Provider

- Cloudflare Workers (free tier available)
- **Your Own VPS** (full control, Linux server — automated installer included)

You only need one provider.

## 2) Set PSK In Code

Each template includes:

const PSK = "CHANGE_ME_TO_A_STRONG_SECRET";

Replace that value with a long random secret.

Important:
- Use the same PSK in your local config under exit_node.psk.
- Never share your deployed URL together with a valid PSK.

## 3) Deploy On Cloudflare Workers

Source file: apps_script/cloudflare_worker.js

Steps:
1. Sign in at https://dash.cloudflare.com
2. Go to Compute -> Workers & Pages.
3. Create Application -> Start with Hello World -> Deploy -> Edit Code.
4. Replace code with apps_script/cloudflare_worker.js content.
5. Set PSK constant in code.
6. Deploy.
7. Copy URL, usually like https://YOUR-WORKER.YOUR-SUBDOMAIN.workers.dev

## 4) Deploy On Your Own VPS  (Linux only)

Source files:
- `apps_script/vps_exit_node.py`  — the relay server
- `apps_script/setup_vps_exit_node.sh`  — automated installer (recommended)

Requirements:
- A **Linux** VPS (Ubuntu / Debian / CentOS / Fedora / Arch — any systemd distro).
- Python 3.10 or later (the installer will install it automatically if absent).
- Root / sudo access.

### One-command install (fetches everything from GitHub)

SSH into your VPS and run **one** of these:

```bash
# with curl:
curl -fsSL https://raw.githubusercontent.com/masterking32/MasterHttpRelayVPN/python_testing/apps_script/setup_vps_exit_node.sh | sudo bash

# with wget:
wget -qO- https://raw.githubusercontent.com/masterking32/MasterHttpRelayVPN/python_testing/apps_script/setup_vps_exit_node.sh | sudo bash
```

The script automatically downloads `vps_exit_node.py` from GitHub, so no `git clone` is needed first. It will ask for a port (default: 8181) and a PSK (auto-generates one if left blank), then install everything and print the `config.json` snippet at the end.

Note:
- To rotate the PSK, edit `/etc/exit-node.env` and restart: `systemctl restart exit-node`.

## 5) Configure MasterHttpRelayVPN

Update `config.json`:

For Cloudflare:
```json
"exit_node": {
  "enabled": true,
  "provider": "cloudflare",
  "url": "https://YOUR-WORKER.YOUR-SUBDOMAIN.workers.dev",
  "psk": "CHANGE_ME_TO_A_STRONG_SECRET",
  "mode": "full",
  "hosts": [
    "chatgpt.com",
    "openai.com",
    "claude.ai",
    "anthropic.com"
  ]
}
```

For your own VPS:
```json
"exit_node": {
  "enabled": true,
  "provider": "vps",
  "url": "https://YOUR-VPS-DOMAIN-OR-IP:8181",
  "psk": "CHANGE_ME_TO_A_STRONG_SECRET",
  "mode": "full",
  "hosts": [
    "example.com",
    "openai.com",
    "claude.ai",
    "anthropic.com"
  ]
}
```

Provider values:
- `cloudflare`
- `vps`

If `mode` is `selective`, only hosts listed in `hosts` use the exit node.
If `mode` is `full`, all relayed traffic uses the exit node.

## 6) Failover — Multiple Exit Nodes

If you have more than one deployed exit node, you can list all URLs so the proxy automatically switches to a healthy one when the active URL goes down, then switches back to the primary when it recovers.

```json
"exit_node": {
  "enabled": true,
  "provider": "cloudflare",
  "url": "https://PRIMARY-WORKER.YOUR-SUBDOMAIN.workers.dev",
  "urls": [
    "https://PRIMARY-WORKER.YOUR-SUBDOMAIN.workers.dev",
    "https://FALLBACK-WORKER.YOUR-SUBDOMAIN.workers.dev"
  ],
  "psk": "CHANGE_ME_TO_A_STRONG_SECRET",
  "mode": "full",
  "health_check_interval": 30,
  "health_check_failures_before_failover": 3
}
```

How it works:
- `urls` — ordered list of exit node URLs. The first entry is the primary. The rest are fallbacks.
- The proxy pings each URL in the background every `health_check_interval` seconds.
- After `health_check_failures_before_failover` consecutive ping failures, that URL is marked dead and the next healthy one is used automatically.
- When the primary URL recovers, the proxy switches back to it.
- If all URLs are down, traffic falls back to direct Apps Script relay until one recovers.

Notes:
- `url` and `urls[0]` can be the same entry — the proxy deduplicates automatically.
- If you only set `urls` (and leave `url` empty), the first entry in `urls` is used as the primary.
- The PSK must be the same secret across all exit nodes in the pool.
- Minimum `health_check_interval` is 10 seconds. Default is 30.
- Default `health_check_failures_before_failover` is 3.
