// MasterHttpRelay exit node for Cloudflare Workers.
// Deploy as HTTP endpoint and set PSK to a strong secret.
//
// For TCP relay (SSH etc.) the cloudflare:sockets API is used.
// No special wrangler.toml flags needed for deployed workers.

import { connect as cfConnect } from "cloudflare:sockets";

const PSK = "CHANGE_ME_TO_A_STRONG_SECRET";

const STRIP_HEADERS = new Set([
  "host",
  "connection",
  "content-length",
  "transfer-encoding",
  "proxy-connection",
  "proxy-authorization",
  "x-forwarded-for",
  "x-forwarded-host",
  "x-forwarded-proto",
  "x-forwarded-port",
  "x-real-ip",
  "forwarded",
  "via",
]);

function decodeBase64ToBytes(input) {
  const bin = atob(input);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

function encodeBytesToBase64(bytes) {
  let bin = "";
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
  return btoa(bin);
}

function sanitizeHeaders(h) {
  const out = {};
  if (!h || typeof h !== "object") return out;
  for (const [k, v] of Object.entries(h)) {
    if (!k) continue;
    if (STRIP_HEADERS.has(k.toLowerCase())) continue;
    out[k] = String(v ?? "");
  }
  return out;
}

export default {
  async fetch(req, _env, ctx) {
    try {
      // ── WebSocket TCP relay ──────────────────────────────────────────────
      // Upgrade requests bypass the normal HTTP relay path entirely.
      // Endpoint: GET /tcp?k=<psk>&host=<host>&port=<port> + Upgrade: websocket
      if (req.headers.get("upgrade")?.toLowerCase() === "websocket") {
        return handleWsTcpRelay(req, ctx);
      }

      // Cloudflare dashboard and browsers commonly test a Worker with GET.
      // Return a friendly health response so users don't misread it as failure.
      if (req.method === "GET") {
        return Response.json(
          {
            ok: true,
            status: "healthy",
            message: "Everything is OK. Worker is deployed and reachable.",
            usage: "Send POST with relay payload for actual proxy requests.",
          },
          { status: 200 }
        );
      }

      if (req.method !== "POST") {
        return Response.json(
          {
            e: "method_not_allowed",
            message: "Use POST for relay requests. GET is only a health check.",
          },
          { status: 405 }
        );
      }

      const body = await req.json();
      if (!body || typeof body !== "object") {
        return Response.json({ e: "bad_json" }, { status: 400 });
      }

      if (!PSK) {
        return Response.json({ e: "server_psk_missing" }, { status: 500 });
      }

      const k = String(body.k ?? "");
      const u = String(body.u ?? "");
      const m = String(body.m ?? "GET").toUpperCase();
      const h = sanitizeHeaders(body.h);
      const b64 = body.b;

      if (k !== PSK) return Response.json({ e: "unauthorized" }, { status: 401 });
      if (!/^https?:\/\//i.test(u)) return Response.json({ e: "bad_url" }, { status: 400 });

      let payload;
      if (typeof b64 === "string" && b64.length > 0) payload = decodeBase64ToBytes(b64);
      const requestBody = payload ? Uint8Array.from(payload) : undefined;

      const resp = await fetch(u, {
        method: m,
        headers: h,
        body: requestBody,
        redirect: "manual",
      });

      const data = new Uint8Array(await resp.arrayBuffer());
      const respHeaders = {};
      resp.headers.forEach((value, key) => {
        respHeaders[key] = value;
      });

      return Response.json({
        s: resp.status,
        h: respHeaders,
        b: encodeBytesToBase64(data),
      });
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      return Response.json({ e: message }, { status: 500 });
    }
  },
};

// ── WebSocket TCP relay ────────────────────────────────────────────────────
// Accepts a WebSocket upgrade, opens a raw TCP socket to the target host/port
// using the cloudflare:sockets API, and pipes bytes bidirectionally.
//
// WS → TCP uses a TransformStream as a queue so that a single pipeTo() call
// owns the tcpSocket.writable lock — no per-message getWriter/releaseLock
// races, and backpressure is handled automatically.
// TCP → WS uses a second pipeTo() into a WritableStream that calls serverWs.send().
// ctx.waitUntil(Promise.all([...])) keeps the worker alive for both directions.

async function handleWsTcpRelay(req, ctx) {
  const url = new URL(req.url);

  const k = url.searchParams.get("k") ?? "";
  if (!PSK || k !== PSK) {
    return new Response("Unauthorized", { status: 401 });
  }

  const host = url.searchParams.get("host") ?? "";
  const port = parseInt(url.searchParams.get("port") ?? "", 10);

  if (!host || !port || port < 1 || port > 65535) {
    return new Response("Bad host/port", { status: 400 });
  }

  const { 0: clientWs, 1: serverWs } = new WebSocketPair();
  serverWs.accept();

  const tcpSocket = cfConnect({ hostname: host, port });

  // WS → TCP: use a TransformStream as an ordered queue so that
  // pipeTo() owns the tcpSocket.writable lock and handles backpressure.
  // Per-message getWriter/releaseLock races are avoided entirely.
  const { readable: toTcp, writable: toTcpSink } = new TransformStream();
  const toTcpWriter = toTcpSink.getWriter();

  serverWs.addEventListener("message", ({ data }) => {
    const bytes =
      data instanceof ArrayBuffer
        ? new Uint8Array(data)
        : new TextEncoder().encode(String(data));
    toTcpWriter.write(bytes).catch(() => {});
  });

  serverWs.addEventListener("close", () => {
    toTcpWriter.close().catch(() => {});
    tcpSocket.close().catch(() => {});
  });

  serverWs.addEventListener("error", () => {
    toTcpWriter.abort("ws error").catch(() => {});
    tcpSocket.close().catch(() => {});
  });

  // Drain the queue into the TCP socket.
  const wsTcpDone = toTcp.pipeTo(tcpSocket.writable).catch(() => {});

  // TCP → WS: pipe TCP readable into WebSocket sends.
  const tcpWsDone = tcpSocket.readable.pipeTo(
    new WritableStream({
      write(chunk) {
        if (serverWs.readyState === 1 /* OPEN */) serverWs.send(chunk);
      },
      close() {
        if (serverWs.readyState === 1) serverWs.close(1000, "TCP closed");
      },
      abort() {
        if (serverWs.readyState === 1) serverWs.close(1011, "TCP error");
      },
    })
  ).catch(() => {});

  // Keep the worker alive for both pump directions.
  ctx.waitUntil(Promise.all([wsTcpDone, tcpWsDone]));

  return new Response(null, { status: 101, webSocket: clientWs });
}
