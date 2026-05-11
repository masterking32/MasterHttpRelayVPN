# Security Notes

MasterHttpRelayVPN is a powerful local proxy. Treat its secrets and generated certificates carefully.

## Responsibility

This project is provided for educational, testing, and research use. You are responsible for following applicable laws, network rules, account rules, and service terms.

## Secrets You Must Protect

Never share:

- `config.json`
- `auth_key`
- `ca/ca.key`
- the full `ca/` folder
- an exit-node URL together with a valid PSK
- live Apps Script Deployment IDs paired with a valid `auth_key`

## Why The CA Matters

The local CA lets the proxy handle HTTPS traffic from your browser. Anyone with your `ca/ca.key` could impersonate websites for browsers that trust that CA.

Keep `ca/` private. If it is exposed, remove the old certificate from trust stores, delete `ca/`, and let the app generate a new CA.

## Recommended Defaults

- Keep `listen_host` as `127.0.0.1` unless you intentionally use LAN sharing.
- Keep `verify_ssl` as `true`.
- Use a long random `auth_key`.
- Use a separate long random exit-node PSK.
- Rotate secrets if you pasted them into chat, logs, screenshots, or issue reports.

## LAN Sharing Risk

With LAN sharing enabled, other devices on your network can use the proxy. Only enable it on trusted networks and turn it off when finished.

## Google Apps Script Quotas

Apps Script deployments have daily execution quotas. Heavy browsing, video, and multiple users can consume quota quickly. If quota is exhausted, relay responses may fail until the quota resets.

## Removing The CA

Run:

```bash
python main.py --uninstall-cert
```

You can also remove the certificate manually from your OS and browser trust stores.
