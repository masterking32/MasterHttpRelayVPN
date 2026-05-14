# Getting Started (Complete Guide)

This is the **full, practical setup guide** for first-time users.
If you follow this page in order, you can go from zero to a working proxy.

---

## Quick Outcome

When done correctly:
- Local HTTP proxy works at `127.0.0.1:8085`
- Local SOCKS5 proxy works at `127.0.0.1:1080`
- HTTPS websites open without certificate warnings
- You can run route checks with `--scan` and stability-first checks with `--adaptive-scan`

---

## Prerequisites

- Python `3.10+`
- A Google account (for Apps Script relay)
- Git (optional, ZIP download also works)
- A browser where you can set manual proxy

---

## 1) Download The Project

### Option A — ZIP

Download and extract:

- <https://github.com/masterking32/MasterHttpRelayVPN/archive/refs/heads/python_testing.zip>

Then open terminal in extracted folder.

### Option B — Git

```bash
git clone https://github.com/masterking32/MasterHttpRelayVPN.git
cd MasterHttpRelayVPN
```

---

## 2) Deploy The Google Apps Script Relay

1. Open <https://script.google.com/> and sign in.
2. Click **New project**.
3. Delete default code.
4. Open local file [`apps_script/Code.gs`](../apps_script/Code.gs), copy all code, paste into Apps Script.
5. Change:

   ```javascript
   const AUTH_KEY = "your-secret-password-here";
   ```

   to your own long random secret.

6. Click **Deploy** → **New deployment**.
7. Select **Web app**.
8. Set **Execute as** = **Me**.
9. Set **Who has access** = **Anyone**.
10. Deploy, authorize, copy **Deployment ID**.

You now need **both** values locally:
- `Deployment ID`
- `AUTH_KEY`

---

## 3) Start The App (Recommended)

### Windows

```cmd
start.bat
```

### Linux / macOS

```bash
chmod +x start.sh
./start.sh
```

What launcher does:
- creates `.venv`
- installs dependencies
- runs setup wizard if `config.json` is missing
- starts proxy

---

## 4) Fill Setup Wizard Correctly

When prompted:
1. `auth_key` = exactly same as `AUTH_KEY` in Apps Script
2. `script_id` = your Deployment ID
3. Keep HTTP port `8085` unless busy
4. Keep LAN sharing disabled unless you need other devices

The wizard creates `config.json`.

---

## 5) Configure Browser Proxy

| Field | Value |
|---|---|
| Proxy type | HTTP |
| Address | `127.0.0.1` |
| Port | `8085` |

For Firefox: Settings → General → Network Settings → Manual proxy.
Enable proxy for HTTPS too.

---

## 6) Install Local CA (HTTPS Required)

The proxy generates `ca/ca.crt`.
If auto-install fails, install manually.

### Windows
1. Open `ca/ca.crt`
2. Install Certificate
3. Current User
4. Place in **Trusted Root Certification Authorities**
5. Restart browser fully

### macOS
1. Open `ca/ca.crt` in Keychain Access
2. Open certificate → Trust
3. Set **Always Trust**
4. Restart browser

### Ubuntu / Debian

```bash
sudo cp ca/ca.crt /usr/local/share/ca-certificates/masterhttp-relay.crt
sudo update-ca-certificates
```

Restart browser.

### Firefox (if needed)
Firefox can use a separate trust store:
- Settings → Privacy & Security → Certificates → View Certificates → Authorities → Import `ca/ca.crt`
- Enable trust for website identification

---

## 7) Verify It Works

- Open normal websites through browser proxy.
- If `unauthorized` appears: `AUTH_KEY` mismatch between Apps Script and `config.json`.
- If HTTPS certificate errors appear: CA not trusted correctly.

---

## 8) Route Quality Commands

### Fast reachability scan

```bash
python main.py --scan
```

Use suggested `google_ip` in `config.json`.

### Stability-first adaptive scan (recommended for unstable networks)

```bash
python main.py --adaptive-scan
```

This ranking is based on route stability metrics (not only minimum ping).

---

## 9) Manual Start (Without launcher)

### Windows

```cmd
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python setup.py
.venv\Scripts\python main.py
```

### Linux / macOS

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python setup.py
.venv/bin/python main.py
```

---

## 10) Common Problems (Short)

- `unauthorized`: auth key mismatch
- proxy connects but sites fail: wrong Deployment ID or script deployment not public
- HTTPS warnings: CA not installed/trusted
- some services block Google egress: use Exit Node guide

---

## Next Docs

- [Troubleshooting](TROUBLESHOOTING.md)
- [Configuration](CONFIGURATION.md)
- [Exit Node](exit-node/EXIT_NODE_DEPLOYMENT.md)
- [Architecture](ARCHITECTURE.md)
