/**
 * MasterHttpRelay — Google Apps Script
 *
 * DEPLOYMENT:
 *   1. Go to https://script.google.com → New project
 *   2. Delete the default code, paste THIS entire file
 *   3. Click Deploy → New deployment
 *   4. Type: Web app  |  Execute as: Me  |  Who has access: Anyone
 *   5. Copy the Deployment ID into config.json as "script_id"
 *
 * CHANGE THE AUTH KEY BELOW TO YOUR OWN SECRET!
 */

const AUTH_KEY = "CHANGE_ME_TO_A_STRONG_SECRET";

// Keep browser capability headers (sec-ch-ua*, sec-fetch-*) intact.
// Some modern apps, notably Google Meet, use them for browser gating.
// Headers that reveal the user's real IP are also stripped here as a
// second line of defence (the Python client strips them first).
const SKIP_HEADERS = {
  host: 1, connection: 1, "content-length": 1,
  "transfer-encoding": 1, "proxy-connection": 1, "proxy-authorization": 1,
  "priority": 1, te: 1,
  // IP-leaking / proxy-metadata headers
  "x-forwarded-for": 1, "x-forwarded-host": 1, "x-forwarded-proto": 1,
  "x-forwarded-port": 1, "x-real-ip": 1, "forwarded": 1, "via": 1,
  // Internal relay hop-count header — must not be forwarded to target sites.
  "x-mhr-hop": 1,
  // UrlFetchApp does not decompress gzip/br/deflate responses — stripping
  // accept-encoding forces targets to reply with plain (uncompressed) bodies
  // so the relay never has to handle compressed content it cannot decode.
  "accept-encoding": 1,
};

// Pattern that matches any Google Apps Script execution endpoint.
// Used to detect relay loops when an exit node is misconfigured to
// point back at a GAS deployment.
var _GAS_URL_RE = /^https?:\/\/script\.google\.com\/macros\//i;

// If fetchAll fails, only retry methods that are safe to replay.
const SAFE_REPLAY_METHODS = { GET: 1, HEAD: 1, OPTIONS: 1 };

function doPost(e) {
  try {
    var req = JSON.parse(e.postData.contents);
    if (req.k !== AUTH_KEY) return _json({ e: "unauthorized" });

    // Batch mode: { k, q: [...] }
    if (Array.isArray(req.q)) return _doBatch(req.q);

    // Single mode
    return _doSingle(req);
  } catch (err) {
    return _json({ e: String(err) });
  }
}

// Compress a byte array with gzip to reduce bytes-on-wire over DPI-shaped
// connections. Returns {b: byteArray, gz: true} if compression saves space,
// otherwise {b: original, gz: false}. This can cut text payloads by 60-80%.
function _maybeGzip(bytes) {
  try {
    var compressed = Utilities.gzip(Utilities.newBlob(bytes)).getBytes();
    if (compressed.length < bytes.length) {
      return { b: compressed, gz: true };
    }
  } catch (e) {
    // Gzip failed — fall through to uncompressed
  }
  return { b: bytes, gz: false };
}

function _doSingle(req) {
  if (!req.u || typeof req.u !== "string" || !req.u.match(/^https?:\/\//i)) {
    return _json({ e: "bad url" });
  }
  // Loop guard: refuse to relay back to any Apps Script deployment.
  // This fires when an exit node URL is misconfigured to point at a GAS
  // script — without this check the script would call itself indefinitely
  // and burn through the daily UrlFetch quota in seconds.
  if (_GAS_URL_RE.test(req.u)) {
    return _json({ e: "loop detected: relay target cannot be a Google Apps Script URL" });
  }
  var opts = _buildOpts(req);
  var resp = UrlFetchApp.fetch(req.u, opts);
  var gz = _maybeGzip(resp.getContent());
  var result = {
    s: resp.getResponseCode(),
    h: _respHeaders(resp),
    b: Utilities.base64Encode(gz.b),
  };
  if (gz.gz) result.gz = 1;
  return _json(result);
}

function _doBatch(items) {
  var fetchArgs = [];
  var fetchIndex = [];
  var fetchMethods = [];
  var errorMap = {};

  for (var i = 0; i < items.length; i++) {
    var item = items[i];
    if (!item || typeof item !== "object") {
      errorMap[i] = "bad item";
      continue;
    }
    if (!item.u || typeof item.u !== "string" || !item.u.match(/^https?:\/\//i)) {
      errorMap[i] = "bad url";
      continue;
    }
    if (_GAS_URL_RE.test(item.u)) {
      errorMap[i] = "loop detected: relay target cannot be a Google Apps Script URL";
      continue;
    }
    try {
      var opts = _buildOpts(item);
      opts.url = item.u;
      fetchArgs.push(opts);
      fetchIndex.push(i);
      fetchMethods.push(String(item.m || "GET").toUpperCase());
    } catch (err) {
      errorMap[i] = String(err);
    }
  }

  // fetchAll() processes all requests in parallel inside Google
  var responses = [];
  if (fetchArgs.length > 0) {
    try {
      responses = UrlFetchApp.fetchAll(fetchArgs);
    } catch (err) {
      // If fetchAll fails as a whole, degrade to per-item fetch so one bad
      // request does not poison the full batch.
      responses = [];
      for (var j = 0; j < fetchArgs.length; j++) {
        try {
          if (!SAFE_REPLAY_METHODS[fetchMethods[j]]) {
            errorMap[fetchIndex[j]] = "batch fetchAll failed; unsafe method not replayed";
            responses[j] = null;
            continue;
          }
          var fallbackReq = fetchArgs[j];
          var fallbackUrl = fallbackReq.url;
          var fallbackOpts = {};
          for (var key in fallbackReq) {
            if (Object.prototype.hasOwnProperty.call(fallbackReq, key) && key !== "url") {
              fallbackOpts[key] = fallbackReq[key];
            }
          }
          responses[j] = UrlFetchApp.fetch(fallbackUrl, fallbackOpts);
        } catch (singleErr) {
          errorMap[fetchIndex[j]] = String(singleErr);
          responses[j] = null;
        }
      }
    }
  }

  var results = [];
  var rIdx = 0;
  for (var i = 0; i < items.length; i++) {
    if (Object.prototype.hasOwnProperty.call(errorMap, i)) {
      results.push({ e: errorMap[i] });
    } else {
      var resp = responses[rIdx++];
      if (!resp) {
        results.push({ e: "fetch failed" });
      } else {
        var gz = _maybeGzip(resp.getContent());
        var item = {
          s: resp.getResponseCode(),
          h: _respHeaders(resp),
          b: Utilities.base64Encode(gz.b),
        };
        if (gz.gz) item.gz = 1;
        results.push(item);
      }
    }
  }
  return _json({ q: results });
}

function _buildOpts(req) {
  var opts = {
    method: (req.m || "GET").toLowerCase(),
    muteHttpExceptions: true,
    followRedirects: req.r !== false,
    validateHttpsCertificates: true,
    escaping: false,
  };
  // Always mark outgoing UrlFetchApp requests with a relay hop counter.
  // Exit nodes and downstream relays can inspect this header to detect
  // loops before consuming quota or making recursive calls.
  var headers = { "x-mhr-hop": "1" };
  if (req.h && typeof req.h === "object") {
    for (var k in req.h) {
      // Use call() so a crafted req.h that overrides hasOwnProperty cannot
      // bypass the check (prototype-pollution hardening).
      if (Object.prototype.hasOwnProperty.call(req.h, k) &&
          !SKIP_HEADERS[k.toLowerCase()]) {
        headers[k] = req.h[k];
      }
    }
  }
  opts.headers = headers;
  if (req.b) {
    opts.payload = Utilities.base64Decode(req.b);
    if (req.ct) opts.contentType = req.ct;
  }
  return opts;
}

function _respHeaders(resp) {
  try {
    if (typeof resp.getAllHeaders === "function") {
      return resp.getAllHeaders();
    }
  } catch (err) {}
  return resp.getHeaders();
}

function doGet(e) {
  return HtmlService.createHtmlOutput(
    "<!DOCTYPE html><html><head><title>My App</title></head>" +
      '<body style="font-family:sans-serif;max-width:600px;margin:40px auto">' +
      "<h1>Welcome</h1><p>This application is running normally.</p>" +
      "</body></html>"
  );
}

function _json(obj) {
  // HtmlService responses can stay on script.google.com for /dev, while
  // ContentService commonly bounces through script.googleusercontent.com.
  // The Python client extracts the JSON payload from the body either way.
  return HtmlService.createHtmlOutput(JSON.stringify(obj)).setXFrameOptionsMode(
    HtmlService.XFrameOptionsMode.ALLOWALL
  );
}
