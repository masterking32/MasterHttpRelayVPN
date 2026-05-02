# Exit Node Deployment Guide (Val Town / Cloudflare / Deno)

This guide explains how to deploy an exit node for MasterHttpRelayVPN on free platforms.

Traffic path:

Browser -> Local Proxy -> Apps Script -> Exit Node -> Target Website

Use this when destinations block Google datacenter egress.

## 1) Choose One Provider

- Val Town
- Cloudflare Workers
- Deno Deploy

You only need one provider.

## 2) Set PSK In Code

Each template includes:

const PSK = "CHANGE_ME_TO_A_STRONG_SECRET";

Replace that value with a long random secret.

Important:
- Use the same PSK in your local config under exit_node.psk.
- Never share your deployed URL together with a valid PSK.

## 3) Deploy On Val Town

Source file: apps_script/valtown.ts

Steps:
1. Sign in at https://www.val.town
2. Create a new Val (TypeScript HTTP endpoint).
3. Paste content from apps_script/valtown.ts.
4. Set the PSK constant in the code.
5. Save and Add HTTP trigger.
6. Copy your public URL, usually like https://YOUR-NAME.web.val.run

## 4) Deploy On Cloudflare Workers

Source file: apps_script/cloudflare_worker.js

Steps:
1. Sign in at https://dash.cloudflare.com
2. Go to Compute -> Workers & Pages.
3. Create Application -> Start with Hello World -> Deploy -> Edit Code.
4. Replace code with apps_script/cloudflare_worker.js content.
5. Set PSK constant in code.
6. Deploy.
7. Copy URL, usually like https://YOUR-WORKER.YOUR-SUBDOMAIN.workers.dev

## 5) Deploy On Deno Deploy (It's not tested Yet)

Source file: apps_script/deno_deploy.ts

Steps:
1. Sign in at https://dash.deno.com
2. Select new playground.
3. Paste apps_script/deno_deploy.ts.
4. Set PSK constant in code.
5. Deploy.
6. Copy URL, usually like https://YOUR-PROJECT.deno.net

## 6) Configure MasterHttpRelayVPN

Update `config.json`:

```json
"exit_node": {
  "enabled": true,
  "provider": "valtown",
  "url": "https://YOUR-NAME.web.val.run",
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

Provider values:
- `valtown`
- `cloudflare`
- `deno`

If `mode` is `selective`, only hosts listed in `hosts` use the exit node.
If `mode` is `full`, all relayed traffic uses the exit node.
