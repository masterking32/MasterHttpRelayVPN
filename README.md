# MasterHttpRelayVPN

[![GitHub](https://img.shields.io/badge/GitHub-MasterHttpRelayVPN-blue?logo=github)](https://github.com/masterking32/MasterHttpRelayVPN)

**[🇮🇷 راهنمای فارسی (Persian)](README_FA.md)**

A free tool that lets you access the internet freely by hiding your traffic behind trusted websites like Google. No VPS or server needed — just a free Google account.

> **How it works in simple terms:** Your browser talks to this tool on your computer. This tool disguises your traffic to look like normal Google traffic. The firewall/filter sees "google.com" and lets it pass. Behind the scenes, a free Google Apps Script relay fetches the real website for you.

---

## Announcement and Support Channel 📢

For the latest news, releases, and project updates, follow our Telegram channel: [Telegram Channel](https://t.me/masterdnsvpn)

---

## How It Works

```
Browser -> Local Proxy -> Google/CDN front -> Your relay -> Target website
             |
             +-> shows google.com to the network filter
```

In normal use, the browser sends traffic to the proxy running on your computer.
The proxy sends that traffic through Google-facing infrastructure so the network only sees an allowed domain such as `www.google.com`.
Your deployed relay then fetches the real website and sends the response back through the same path.

This means the filter sees normal-looking Google traffic, while the actual destination stays hidden inside the relay request.

---

## Step-by-Step Setup Guide

### Step 1: Download This Project

```bash
git clone -b python_testing https://github.com/masterking32/MasterHttpRelayVPN.git
cd MasterHttpRelayVPN
pip install -r requirements.txt
```

> **Can't reach PyPI directly?** Use this mirror instead:
> ```bash
> pip install -r requirements.txt -i https://mirror-pypi.runflare.com/simple/ --trusted-host mirror-pypi.runflare.com
> ```

Or download the ZIP from [GitHub](https://github.com/masterking32/MasterHttpRelayVPN/tree/python_testing) and extract it.

### Step 2: Set Up the Google Relay (Code.gs)

This is the "relay" that sits on Google's servers and fetches websites for you. It's free.

1. Open [Google Apps Script](https://script.google.com/) and sign in with your Google account.
2. Click **New project**.
3. **Delete** all the default code in the editor.
4. Open the [`Code.gs`](Code.gs) file from this project, **copy everything**, and paste it into the Apps Script editor.
5. **Important:** Change the password on this line to something only you know:
   ```javascript
   const AUTH_KEY = "your-secret-password-here";
   ```
6. Click **Deploy** → **New deployment**.
7. Choose **Web app** as the type.
8. Set:
   - **Execute as:** Me
   - **Who has access:** Anyone
9. Click **Deploy**.
10. **Copy the Deployment ID** (it looks like a long random string). You'll need it in the next step.

> ⚠️ Remember the password you set in step 5. You'll use the same password in the config file below.

### Step 3: Configure

1. Copy the example config file:
   ```bash
   cp config.example.json config.json
   ```
   On Windows, you can also just copy & rename the file manually.

2. Open `config.json` in any text editor and fill in your values:
   ```json
   {
     "mode": "apps_script",
     "google_ip": "216.239.38.120",
     "front_domain": "www.google.com",
     "script_id": "PASTE_YOUR_DEPLOYMENT_ID_HERE",
     "auth_key": "your-secret-password-here",
     "listen_host": "127.0.0.1",
     "listen_port": 8085,
     "log_level": "INFO",
     "verify_ssl": true
   }
   ```
   - `script_id` → Paste the Deployment ID from Step 2.
   - `auth_key` → The **same password** you set in `Code.gs`.

### Step 4: Run

```bash
python main.py
```

You should see a message saying the proxy is running on `127.0.0.1:8085`.

### Step 5: Set Up Your Browser

Set your browser to use the proxy:

- **Proxy Address:** `127.0.0.1`
- **Proxy Port:** `8085`
- **Type:** HTTP

**How to set proxy in common browsers:**
- **Firefox:** Settings → General → Network Settings → Manual proxy → enter `127.0.0.1` port `8085` for HTTP Proxy → check "Also use this proxy for HTTPS"
- **Chrome/Edge:** Uses system proxy. Go to Windows Settings → Network → Proxy → Manual setup → enter `127.0.0.1:8085`
- **Or** use extensions like [FoxyProxy](https://addons.mozilla.org/en-US/firefox/addon/foxyproxy-standard/) or [SwitchyOmega](https://chrome.google.com/webstore/detail/proxy-switchyomega/) for easier switching.

### Step 6: Install the CA Certificate (Required for HTTPS)

When using `apps_script` mode, the tool needs to decrypt and re-encrypt HTTPS traffic locally. It generates a CA certificate on first run. **You must install it** or you'll see security warnings on every website.

The certificate file is created at `ca/ca.crt` inside the project folder after the first run.

#### Windows
1. Double-click `ca/ca.crt`.
2. Click **Install Certificate**.
3. Choose **Current User** (or Local Machine for all users).
4. Select **Place all certificates in the following store** → click **Browse** → choose **Trusted Root Certification Authorities**.
5. Click **Next** → **Finish**.
6. Restart your browser.

#### macOS
1. Double-click `ca/ca.crt` — it opens in Keychain Access.
2. It goes into the **login** keychain.
3. Find the certificate, double-click it.
4. Expand **Trust** → set **When using this certificate** to **Always Trust**.
5. Close and enter your password. Restart your browser.

#### Linux (Ubuntu/Debian)
```bash
sudo cp ca/ca.crt /usr/local/share/ca-certificates/masterhttp-relay.crt
sudo update-ca-certificates
```
Restart your browser.

#### Firefox (All Platforms)
Firefox uses its own certificate store, so even after OS-level install you need to do this:
1. Go to **Settings** → **Privacy & Security** → **Certificates** → **View Certificates**.
2. Go to the **Authorities** tab → click **Import**.
3. Select `ca/ca.crt` from the project folder.
4. Check **Trust this CA to identify websites** → click **OK**.

> **Auto-install on startup:** When running in `apps_script` mode the proxy will automatically detect if the CA is not yet trusted and attempt to install it for you. If it succeeds you'll see a confirmation in the log; if it fails (e.g. needs administrator rights) it will print instructions. You can also run `python main.py --install-cert` at any time to (re-)install the certificate.

> ⚠️ **Security note:** This certificate only works locally on your machine. Don't share the `ca/` folder with anyone. If you want to start fresh, delete the `ca/` folder and the tool will generate a new one.

---

## Modes Overview

| Mode | What You Need | Description |
|------|--------------|-------------|
| `apps_script` | Free Google account | **Easiest.** Uses Google Apps Script as relay. No server needed. |
| `google_fronting` | Google Cloud Run service | Uses your own Cloud Run service behind Google's CDN. |
| `domain_fronting` | Cloudflare Worker | Uses a Cloudflare Worker as relay. |
| `custom_domain` | Custom domain on Cloudflare | Connects directly to your domain on Cloudflare. |

Most users should use **`apps_script`** mode — it's free and requires no server.

---

## Configuration Options

### Main Settings

| Setting | What It Does |
|---------|-------------|
| `mode` | Which relay type to use (see table above) |
| `auth_key` | Password shared between your computer and the relay |
| `script_id` | Your Google Apps Script Deployment ID |
| `listen_host` | Where to listen (`127.0.0.1` = only this computer) |
| `listen_port` | Which port to listen on (default: `8085`) |
| `log_level` | How much detail to show: `DEBUG`, `INFO`, `WARNING`, `ERROR` |

### Advanced Settings

| Setting | Default | What It Does |
|---------|---------|-------------|
| `google_ip` | `216.239.38.120` | Google IP address to connect through |
| `front_domain` | `www.google.com` | Domain shown to the firewall/filter |
| `verify_ssl` | `true` | Verify TLS certificates |
| `worker_host` | — | Hostname for Cloudflare/Cloud Run modes |
| `custom_domain` | — | Your custom domain on Cloudflare |
| `script_ids` | — | Multiple Script IDs for load balancing (array) |

### Load Balancing

To increase speed, deploy `Code.gs` multiple times to different Apps Script projects and use all their IDs:

```json
{
  "script_ids": [
    "DEPLOYMENT_ID_1",
    "DEPLOYMENT_ID_2",
    "DEPLOYMENT_ID_3"
  ]
}
```

---

## Updating the Google Relay

If you change `Code.gs`, you must **create a new deployment** in Google Apps Script (Deploy → New deployment) and **update the `script_id`** in your `config.json`. Just editing the code does not update the live version.

---

## Command Line Options

```bash
python main.py                          # Normal start
python main.py -p 9090                  # Use port 9090 instead
python main.py --log-level DEBUG        # Show detailed logs
python main.py -c /path/to/config.json  # Use a different config file
python main.py --install-cert           # Install MITM CA certificate and exit
python main.py --no-cert-check          # Skip automatic CA install check on startup
```

> **Auto-install:** On startup (MITM mode), the proxy automatically checks if the CA certificate is trusted and attempts to install it. Use `--no-cert-check` to skip this. If auto-install fails (e.g. needs elevation), run `python main.py --install-cert` manually or follow Step 6 above.

---

## Architecture

```
┌─────────┐     ┌──────────────┐     ┌─────────────┐     ┌──────────┐
│ Browser  │────►│ Local Proxy  │────►│ CDN / Google │────►│  Relay   │──► Internet
│          │◄────│ (this tool)  │◄────│  (fronted)   │◄────│ Endpoint │◄──
└─────────┘     └──────────────┘     └─────────────┘     └──────────┘
                  HTTP/CONNECT         TLS (SNI: ok)        Fetch target
                  MITM (optional)      Host: relay          Return response
```

---

## Project Files

| File | What It Does |
|------|-------------|
| `main.py` | Starts the proxy |
| `proxy_server.py` | Handles browser connections |
| `domain_fronter.py` | Disguises traffic through CDN/Google |
| `h2_transport.py` | Faster connections using HTTP/2 (optional) |
| `mitm.py` | Handles HTTPS certificate generation |
| `cert_installer.py` | Cross-platform CA certificate installer (Windows/macOS/Linux + Firefox) |
| `ws.py` | WebSocket support |
| `Code.gs` | The relay script you deploy to Google Apps Script |
| `config.example.json` | Example config — copy to `config.json` |

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| "Config not found" | Copy `config.example.json` to `config.json` and fill in your values |
| Browser shows certificate errors | Install the CA certificate (see Step 6 above) |
| Telegram works but browser doesn't load sites | Almost certainly the CA certificate is not installed. Follow Step 6 to install `ca/ca.crt`, then **fully close and reopen your browser** (for Chrome/Edge, make sure no Chrome process is running in the background before reopening). |
| Installed the cert but browser still errors | Chrome and Edge cache certificates — you must **completely close** the browser (check Task Manager / system tray) and reopen it for the new cert to take effect. Firefox requires a separate import (see Step 6 Firefox section). |
| "unauthorized" error | Make sure `auth_key` in `config.json` matches `AUTH_KEY` in `Code.gs` exactly |
| Connection timeout | Try a different `google_ip` or check your internet connection |
| Slow browsing | Deploy multiple `Code.gs` copies and use `script_ids` array for load balancing |
| `502 Bad JSON` error | Google returned an unexpected response (HTML instead of JSON). Causes: wrong `script_id`, Apps Script daily quota exhausted, or the deployment wasn't re-created after editing `Code.gs`. Check your `script_id` and create a **new deployment** if you recently changed `Code.gs`. |

---

## Security Tips

- **Never share your `config.json`** — it has your password in it.
- **Change the default `AUTH_KEY`** in `Code.gs` before deploying.
- **Don't share the `ca/` folder** — it contains your private certificate key.
- Keep `listen_host` as `127.0.0.1` so only your computer can use the proxy.

---

## Disclaimer

MasterHttpRelayVPN is provided for educational, testing, and research purposes only.

- **Provided without warranty:** This software is provided "AS IS", without express or implied warranty, including merchantability, fitness for a particular purpose, and non-infringement.
- **Limitation of liability:** The developers and contributors are not responsible for any direct, indirect, incidental, consequential, or other damages resulting from the use of this project or the inability to use it.
- **User responsibility:** Running this project outside controlled test environments may affect networks, accounts, proxies, certificates, or connected systems. You are solely responsible for installation, configuration, and use.
- **Legal compliance:** You are responsible for complying with all local, national, and international laws and regulations before using this software.
- **Google services compliance:** If you use Google Apps Script or other Google services with this project, you are responsible for complying with Google's Terms of Service, acceptable use rules, quotas, and platform policies. Misuse may lead to suspension or termination of your Google account or deployments.
- **License terms:** Use, copying, distribution, and modification of this software are governed by the repository license. Any use outside those terms is prohibited.

---

## Special Thanks

Special thanks to [@abolix](https://github.com/abolix) for making this project possible.

## License

MIT
