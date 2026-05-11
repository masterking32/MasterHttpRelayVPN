# Docker Guide

Use Docker when you want the proxy isolated from your system Python environment.

## Requirements

- Docker
- Docker Compose
- A completed `config.json`

## Setup

Create `config.json` first:

```bash
cp config.example.json config.json
```

Edit `config.json` and set at least:

```json
{
  "script_id": "YOUR_APPS_SCRIPT_DEPLOYMENT_ID",
  "auth_key": "THE_SAME_SECRET_AS_CODE_GS"
}
```

Then start the container:

```bash
docker compose up -d
```

The compose file exposes:

| Port | Use |
|------|-----|
| `8085` | HTTP proxy |
| `1080` | SOCKS5 proxy |

Configure your browser to use HTTP proxy `127.0.0.1:8085`.

## Useful Commands

```bash
docker compose up -d          # Start in background
docker compose logs -f        # Follow logs
docker compose restart        # Restart after config changes
docker compose down           # Stop and remove container
docker compose build          # Rebuild after code changes
```

## Certificate Handling

The container writes the generated CA into `./ca` on your host because [docker-compose.yml](../docker-compose.yml) mounts that directory.

Install this file on the host, not inside the container:

```text
ca/ca.crt
```

Running `python main.py --install-cert` inside the container cannot update your host OS or browser trust store.

## Config And Secrets

[docker-compose.yml](../docker-compose.yml) mounts `config.json` read-only into the container. Your secrets stay on the host and are not baked into the image.

Do not commit or share your real `config.json`, `auth_key`, `ca/`, or exit-node PSK.
