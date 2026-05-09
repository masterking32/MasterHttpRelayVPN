# Troubleshooting

Start with the symptom you see. Most problems are configuration, certificate trust, or an outdated Apps Script deployment.

## Certificate Errors

Symptoms:

- Browser says the connection is not private.
- Telegram or another app works, but normal HTTPS sites do not.
- Chrome or Edge still fails after you installed the certificate.

Fix:

1. Make sure the proxy has run at least once so `ca/ca.crt` exists.
2. Install `ca/ca.crt` as a trusted root certificate.
3. Fully close and reopen the browser. On Windows, check Task Manager for background Chrome or Edge processes.
4. For Firefox, import the CA separately from **Settings** -> **Privacy & Security** -> **Certificates** -> **View Certificates** -> **Authorities**.

You can also run:

```bash
python main.py --install-cert
```

## `unauthorized`

The shared secret does not match.

Fix:

1. Open [apps_script/Code.gs](../apps_script/Code.gs).
2. Find `const AUTH_KEY = "...";`.
3. Make sure it exactly matches `auth_key` in `config.json`.
4. Deploy a new Apps Script deployment after changing [apps_script/Code.gs](../apps_script/Code.gs).

## `Config not found`

Run the wizard:

```bash
python setup.py
```

Or copy [config.example.json](../config.example.json) to `config.json` and fill `script_id` plus `auth_key`.

## `502 Bad JSON`

Google returned HTML or another unexpected response instead of relay JSON.

Common causes:

- Wrong Deployment ID.
- Apps Script daily quota is exhausted.
- You edited [apps_script/Code.gs](../apps_script/Code.gs) but did not create a new deployment.
- The deployment access setting is not **Anyone**.

Fix:

1. Create a new Apps Script deployment.
2. Copy the new Deployment ID into `config.json`.
3. Confirm the deployment is a Web App with **Execute as: Me** and **Who has access: Anyone**.
4. If quota is exhausted, wait for the quota reset or add more deployments with `script_ids`.

## Page Looks Like Random Characters

Symptoms:

- A website opens as unreadable text like `�` and random symbols.
- The issue appears only for some users or only on some websites.
- HTML, JavaScript, or JSON is shown as binary-looking output instead of a normal page.

Most likely cause:

The target website sent a compressed response, but the browser received it without a usable `Content-Encoding` header. This usually happens when an old Apps Script deployment or exit node still forwards `Accept-Encoding` to the target website.

Fix:

1. Update this project and install dependencies again with `pip install -r requirements.txt`.
2. Redeploy [apps_script/Code.gs](../apps_script/Code.gs) as a new Apps Script deployment.
3. Copy the new Deployment ID into `config.json` if it changed.
4. If you use a Deno exit node, redeploy [apps_script/deno_deploy.ts](../apps_script/deno_deploy.ts).
5. Restart the proxy and fully reopen the browser.

## Connection Timeout

The current `google_ip` may be blocked or slow on your network.

Run:

```bash
python main.py --scan
```

Then set the recommended IP in `config.json`:

```json
"google_ip": "RECOMMENDED_IP"
```

Restart the proxy.

## Slow Browsing

Try these in order:

1. Install all dependencies from [requirements.txt](../requirements.txt), especially `h2`.
2. Run `python main.py --scan` and update `google_ip`.
3. Deploy multiple Apps Script projects and use `script_ids`.
4. Keep `log_level` at `INFO` unless debugging.
5. Use an exit node only when needed, because it adds another hop.

## Browser Proxy Is Set But Sites Do Not Load

Check:

1. The terminal says HTTP proxy is listening on `127.0.0.1:8085`.
2. Browser proxy type is **HTTP**, not HTTPS.
3. HTTPS traffic is also configured to use the same HTTP proxy.
4. The CA is installed and the browser was fully restarted.

## SOCKS5 Works Differently Than HTTP Proxy

Some SOCKS5 clients resolve domains locally and only send raw IPs to the proxy. That can break routes that depend on hostnames.

Use HTTP proxy `127.0.0.1:8085` for browsers and apps that support it. This is especially important for Telegram-style cases where hostname handling matters.

## YouTube Opens But Video Playback Fails

Try:

```json
"youtube_via_relay": true
```

Then restart the proxy. This routes YouTube through the Apps Script relay instead of the direct Google SNI-rewrite path. It may use more Apps Script executions.

If `exit_node.mode` is `full`, YouTube may also be routed through the relay so the exit node can handle all traffic.

## Docker Certificate Confusion

The container generates `ca/ca.crt` into the host `./ca` volume, but certificate installation inside the container does not trust it on your host OS.

Install `ca/ca.crt` manually on the host browser or OS. See [Docker Guide](DOCKER.md).

## Reset The Local Certificate

To remove the certificate from trust stores:

```bash
python main.py --uninstall-cert
```

To generate a fresh CA, stop the proxy and delete the `ca/` folder. The next run will create a new one.

Never share the `ca/` folder.
