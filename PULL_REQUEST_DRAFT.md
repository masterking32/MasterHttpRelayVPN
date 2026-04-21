# PR Draft

## Summary

This PR improves `apps_script` mode compatibility and local usability.

Changes included:

- add built-in SOCKS5 listener alongside the HTTP proxy
- use sticky per-host Apps Script routing instead of naive per-request round-robin
- preserve redirect semantics for `307/308` while still normalizing `301/302/303`
- avoid batching/coalescing/cache shortcuts for stateful requests
- make caching safer by skipping requests/responses involving cookies, auth, private cache directives, and `Set-Cookie`
- improve Linux CA trust detection
- make Google direct-tunnel routing more conservative
- add adaptive fallback from failed direct Google tunnels back to the MITM relay path
- preserve browser capability headers in `Code.gs` (`sec-ch-ua*`, `sec-fetch-*`)
- preserve multi-value response headers from Apps Script via `getAllHeaders()`
- keep signed URLs safer via `escaping: false` in Apps Script fetch options

## Motivation

The previous behavior worked for some simple/static sites, but modern sites and Google web apps were sensitive to:

- request-to-request route churn
- incorrect redirect method handling
- over-aggressive batching/cache reuse
- over-broad Google direct-tunnel shortcuts
- lost response headers such as `Set-Cookie`
- stripped browser capability headers in the Apps Script relay

This PR does not claim full compatibility for all websites. It focuses on making the existing architecture more stable and more predictable.

## Testing

Local verification:

- `python3 -m py_compile main.py proxy_server.py domain_fronter.py mitm.py h2_transport.py ws.py cert_installer.py`

Observed behavior during manual testing:

- improved: YouTube, Facebook, Gmail, Drive
- improved / partial: Gemini loads further than before
- still limited by architecture: ChatGPT / Cloudflare PAT flows, Google Meet browser-gating / unsupported-browser flow in `apps_script` mode

## Important limitation

`apps_script` mode still uses Google Apps Script `UrlFetch`, which is not a real browser transport.

That means some sites may still reject or degrade requests because of:

- TLS / transport fingerprint differences
- anti-bot / PAT / Turnstile / attestation checks
- browser capability detection
- WebRTC / media / browser-runtime assumptions

## Deployment note

If `Code.gs` changes are included, users must create a new Google Apps Script deployment and update `script_id` in `config.json`.

## Security / hygiene checklist

- no real `config.json` included
- no real deployment IDs included
- `AUTH_KEY` reset to placeholder
- no local logs included
- no local workspace files intended for commit
