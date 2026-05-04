// MasterHttpRelay — TCP relay for Deno Deploy.
//
// Accepts WebSocket upgrades and proxies raw TCP using Deno.connect().
// Deploy this as a SEPARATE Deno Deploy project from the HTTP relay.
//
// Endpoint:
//   GET /tcp?k=<auth_key>&host=<target_host>&port=<target_port>
//   (with Upgrade: websocket header)
//
// Health check:
//   GET /health  →  {"ok": true}
//
// Configuration:
//   Change PSK to a strong secret and redeploy.

declare const Deno: any;

const PSK = "CHANGE_ME_TO_A_STRONG_SECRET";

Deno.serve(async (req: Request): Promise<Response> => {
  const url = new URL(req.url);

  // ── Health check ──────────────────────────────────────────────────────
  if (req.method === "GET" && url.pathname === "/health") {
    return Response.json({ ok: true, service: "tcp-relay" });
  }

  // ── Only WebSocket upgrades beyond this point ─────────────────────────
  if (req.headers.get("upgrade")?.toLowerCase() !== "websocket") {
    return Response.json(
      { e: "websocket_required" },
      { status: 426, headers: { "Upgrade": "websocket" } },
    );
  }

  // ── Auth ──────────────────────────────────────────────────────────────
  const k = url.searchParams.get("k") ?? "";
  if (!PSK || k !== PSK) {
    return new Response("Unauthorized", { status: 401 });
  }

  // ── Target parsing ────────────────────────────────────────────────────
  const host = url.searchParams.get("host") ?? "";
  const portStr = url.searchParams.get("port") ?? "";
  const port = parseInt(portStr, 10);

  if (!host || !port || port < 1 || port > 65535) {
    return new Response("Bad host/port", { status: 400 });
  }

  // SSRF guard: block loopback / private ranges (Deno's sandbox is the
  // primary barrier, but defense-in-depth is worth the few lines).
  if (_isPrivateHost(host)) {
    return new Response("Forbidden", { status: 403 });
  }

  // ── WebSocket upgrade ─────────────────────────────────────────────────
  const { socket: ws, response } = Deno.upgradeWebSocket(req);

  ws.binaryType = "arraybuffer";

  let tcpConn: any = null;
  let closing = false;

  // onopen: attempt the TCP connection.
  ws.onopen = async () => {
    try {
      tcpConn = await Deno.connect({ hostname: host, port });
    } catch (err) {
      closing = true;
      ws.close(1011, `TCP connect failed: ${err}`);
      return;
    }
    // Start pumping TCP → WS in the background.
    _pumpTcpToWs(tcpConn, ws, () => { closing = true; });
  };

  // onmessage: WS → TCP.
  ws.onmessage = async (event: MessageEvent) => {
    if (closing || !tcpConn) return;
    const data: Uint8Array =
      event.data instanceof ArrayBuffer
        ? new Uint8Array(event.data)
        : new TextEncoder().encode(event.data as string);
    try {
      // Deno.Conn.write() may write fewer bytes than requested — loop.
      let offset = 0;
      while (offset < data.length) {
        const n = await tcpConn.write(data.subarray(offset));
        offset += n;
      }
    } catch (_err) {
      if (!closing) {
        closing = true;
        try { ws.close(1011, "TCP write failed"); } catch (_) {}
      }
    }
  };

  ws.onclose = () => {
    closing = true;
    if (tcpConn) {
      try { tcpConn.close(); } catch (_) {}
      tcpConn = null;
    }
  };

  ws.onerror = () => {
    closing = true;
  };

  return response;
});

// Pump bytes from TCP connection into the WebSocket.
// Runs as a fire-and-forget async task (called from onopen).
async function _pumpTcpToWs(
  conn: any,
  ws: WebSocket,
  onClose: () => void,
): Promise<void> {
  const buf = new Uint8Array(65536);
  try {
    while (true) {
      const n = await conn.read(buf);
      if (n === null) break; // EOF from TCP
      if (ws.readyState !== WebSocket.OPEN) break;
      ws.send(buf.slice(0, n));
    }
  } catch (_err) {
    // TCP read error — connection reset or timed out.
  } finally {
    onClose();
    if (ws.readyState === WebSocket.OPEN) {
      try { ws.close(1000, "TCP closed"); } catch (_) {}
    }
  }
}

function _isPrivateHost(host: string): boolean {
  const lower = host.toLowerCase();
  if (lower === "localhost" || lower.endsWith(".localhost")) return true;
  if (lower === "::1" || lower.startsWith("127.")) return true;
  if (lower.startsWith("10.") || lower.startsWith("192.168.")) return true;
  if (lower.startsWith("169.254.")) return true; // link-local
  if (lower.startsWith("172.")) {
    // 172.16.0.0/12
    const second = parseInt(lower.split(".")[1] ?? "0", 10);
    if (second >= 16 && second <= 31) return true;
  }
  return false;
}
