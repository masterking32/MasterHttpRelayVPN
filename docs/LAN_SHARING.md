# LAN Sharing

By default, MasterHttpRelayVPN listens only on `127.0.0.1`, so only the same computer can use it. LAN sharing lets phones, tablets, or other computers on your local network use your proxy.

## When To Use It

Use LAN sharing only on a trusted private network. Anyone who can reach the proxy can send traffic through it.

## Enable LAN Sharing

Set this in `config.json`:

```json
{
  "lan_sharing": true,
  "listen_host": "0.0.0.0",
  "http_port": 8085
}
```

Restart the proxy. The startup log prints LAN addresses other devices can use. it would be something like `192.168.x.x` or `10.x.x.x`.

in terminal you would see a message like `CA certificate download :  http://192.168.xxxxxx/ca.crt` in a green color, on the other device open this URL in browser to download the CA certificate and install it there as well.


## Configure Other Devices

On the other device, set the HTTP proxy to:

| Field | Value |
|-------|-------|
| Address | Your computer's LAN IP from the startup log |
| Port | `8085` |
| Type | HTTP |

Or you can create V2ray connection as HTTP or Socks5 protocol.

## Safety Checklist

- Use this only on networks you trust.
- Turn it off when you do not need it.
- Keep `auth_key` private.
- Never share the `ca/` folder with random users.
- Prefer `127.0.0.1` for normal single-computer use.
