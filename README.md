# MasterHttpRelayVPN

[![GitHub](https://img.shields.io/badge/GitHub-MasterHttpRelayVPN-blue?logo=github)](https://github.com/masterking32/MasterHttpRelayVPN) [![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/masterking32/MasterHttpRelayVPN) [![oosmetrics](https://api.oosmetrics.com/api/v1/badge/achievement/85a1f608-5c6d-4fcd-9b7f-b1ff8b680852.svg)](https://oosmetrics.com/repo/masterking32/MasterHttpRelayVPN) [![oosmetrics](https://api.oosmetrics.com/api/v1/badge/achievement/de9bee73-bc68-4f98-ba83-6957007046b1.svg)](https://oosmetrics.com/repo/masterking32/MasterHttpRelayVPN)

**Language:** English | [Persian / فارسی](README_FA.md)

**Telegram Channel 📣:** [https://t.me/masterdnsvpn](https://t.me/masterdnsvpn)

**Special Thanks ❤️:** [Abolix](https://github.com/abolix)

MasterHttpRelayVPN is a local proxy that routes browser traffic through a Google Apps Script relay using domain fronting. The simple path needs only this project and a free Google account. For sites that block Google egress, you can optionally add an exit node later.

```text
Browser -> Local proxy -> Google front -> Your Apps Script relay -> Target site
                         network filter sees a Google-facing connection
```

## Quick Menu 🧭

[Getting Started](docs/GETTING_STARTED.md) | [Docker](docs/DOCKER.md) | [LAN Sharing](docs/LAN_SHARING.md) | [Exit Node](docs/exit-node/EXIT_NODE_DEPLOYMENT.md)

[Configuration](docs/CONFIGURATION.md) | [Troubleshooting](docs/TROUBLESHOOTING.md) | [Security](docs/SECURITY.md) | [Architecture](docs/ARCHITECTURE.md)

## Fast Start ⚡

Before running the local proxy, deploy the Google relay once. You only need a Google account and about two minutes.

## Deploy The Google Relay ☁️

1. Open [Google Apps Script](https://script.google.com/) and sign in.
2. Click **New project**.
3. Delete the default editor content.
4. Open [apps_script/Code.gs](apps_script/Code.gs), copy everything, and paste it into Apps Script.
5. Find this line and replace it with your own long secret:

    ```javascript
    const AUTH_KEY = "your-secret-password-here";
    ```

6. Click **Deploy** -> **New deployment** -> **Web app**.
7. Set **Execute as** to **Me**.
8. Set **Who has access** to **Anyone**.
9. Click **Deploy**, approve the permission screen, and copy the **Deployment ID**.

Keep these two values ready for the setup wizard:

- `Deployment ID` from Google Apps Script
- `AUTH_KEY`, a long secret that must match `auth_key` in your local config

If you want screenshots and more detail, use [Getting Started](docs/GETTING_STARTED.md#2-deploy-the-google-relay).

Download the project with either Git or ZIP, then run the one-click launcher.

**Option A: Git**

```bash
git clone https://github.com/masterking32/MasterHttpRelayVPN.git
cd MasterHttpRelayVPN
```

**Option B: ZIP**

1. Open [the GitHub repository](https://github.com/masterking32/MasterHttpRelayVPN).
2. Click **Code** -> **Download ZIP**.
3. Extract the ZIP file.
4. Open a terminal inside the extracted `MasterHttpRelayVPN` folder.

Then start the app:

**Windows**

```cmd
start.bat
```

**Linux / macOS**

```bash
chmod +x start.sh
./start.sh
```

The launcher creates a virtual environment, installs dependencies, opens the setup wizard if `config.json` is missing, and starts the proxy.

After it starts, configure your browser to use:

| Field | Value |
|-------|-------|
| Proxy type | HTTP |
| Address | `127.0.0.1` |
| Port | `8085` |
| SOCKS5 port, optional | `1080` |

For HTTPS sites, install the generated certificate from `ca/ca.crt` if the app cannot install it automatically. The full setup is in [Getting Started](docs/GETTING_STARTED.md).

## Common Next Steps 🛠️

- If the browser shows certificate warnings, open [Troubleshooting](docs/TROUBLESHOOTING.md#certificate-errors).
- If you see `unauthorized`, make sure `AUTH_KEY` in [apps_script/Code.gs](apps_script/Code.gs) exactly matches `auth_key` in `config.json`.
- If browsing is slow or connections time out, run `python main.py --scan` and see [Configuration Reference](docs/CONFIGURATION.md#diagnostic-commands).
- If ChatGPT, Turnstile, or similar sites block the Google exit IP, use [Exit Node Guide](docs/exit-node/EXIT_NODE_DEPLOYMENT.md).

## Support And Updates 📣

- Telegram channel: [https://t.me/masterdnsvpn](https://t.me/masterdnsvpn)
- Windows client: [MHRWindowsApp](https://github.com/AriPath/MHRWindowsApp)
- Ad blocker filter source: [PersianBlocker](https://github.com/MasterKia/PersianBlocker/)

## Safety 🔒

This project is provided for educational, testing, and research use. You are responsible for following applicable laws and service terms. Never share `config.json`, `auth_key`, `ca/`, or an exit-node URL together with a valid PSK. Read [Security Notes](docs/SECURITY.md) before sharing the proxy with other devices.

## License

MIT
