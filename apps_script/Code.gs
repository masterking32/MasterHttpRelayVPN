/**
 * DomainFront Relay — Google Apps Script
 *
 * TWO modes:
 *   1. Single:  POST { k, m, u, h, b, ct, r }       → { s, h, b }
 *   2. Batch:   POST { k, q: [{m,u,h,b,ct,r}, ...] } → { q: [{s,h,b}, ...] }
 *      Uses UrlFetchApp.fetchAll() — all URLs fetched IN PARALLEL.
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
// Maximum seconds to wait for any single upstream fetch.
// Matches the Python client's 25s timeout. Without this, a slow upstream
// can hold up an entire batch until Apps Script's 6-minute execution limit.
const FETCH_DEADLINE = 25;

const SKIP_HEADERS = {
  host: 1, connection: 1, "content-length": 1,
  "transfer-encoding": 1, "proxy-connection": 1, "proxy-authorization": 1,
  "priority": 1, te: 1,
};

function doPost(e) {
  try {
    var req = JSON.parse(e.postData.contents);

    // Reject requests using the default key — deployer forgot to change it.
    if (AUTH_KEY === "CHANGE_ME_TO_A_STRONG_SECRET") {
      return _json({ e: "relay not configured: change AUTH_KEY before deploying" });
    }

    if (req.k !== AUTH_KEY) return _json({ e: "unauthorized" });

    // Batch mode: { k, q: [...] }
    if (Array.isArray(req.q)) return _doBatch(req.q);

    // Single mode
    return _doSingle(req);
  } catch (err) {
    return _json({ e: String(err) });
  }
}

function _doSingle(req) {
  if (!req.u || typeof req.u !== "string" || !req.u.match(/^https?:\/\//i)) {
    return _json({ e: "bad url" });
  }

  // UrlFetchApp hard limit ~2048 chars — return 414 so client can retry as POST
  if (req.u.length > 2000) {
    return _json({ s: 414, h: {}, b: "" });
  }

  var opts = _buildOpts(req);

  // HEAD is not supported by UrlFetchApp. Intercept and return empty success.
  if (opts.method === "head") {
    return _json({ s: 200, h: { "content-type": "text/plain" }, b: "" });
  }

  var resp = UrlFetchApp.fetch(req.u, opts);
  return _json({
    s: resp.getResponseCode(),
    h: _respHeaders(resp),
    b: Utilities.base64Encode(resp.getContent()),
  });
}

function _doBatch(items) {
  var fetchArgs = [];
  var results = new Array(items.length);

  for (var i = 0; i < items.length; i++) {
    var item = items[i];
    if (!item.u || typeof item.u !== "string" || !item.u.match(/^https?:\/\//i)) {
      results[i] = { e: "bad url" };
      continue;
    }
    if (item.u.length > 2000) {
      results[i] = { s: 414, h: {}, b: "" };
      continue;
    }

    var opts = _buildOpts(item);
    if (opts.method === "head") {
      results[i] = { s: 200, h: { "content-type": "text/plain" }, b: "" };
      continue;
    }

    opts.url = item.u;
    fetchArgs.push({ _i: i, _o: opts });
  }

  // fetchAll() processes all requests in parallel inside Google.
  // Wrapped in try-catch: if fetchAll itself throws (DNS failure, Apps Script
  // memory limit, internal error), fill remaining slots with error objects
  // so the client gets partial results instead of a top-level batch failure.
  if (fetchArgs.length > 0) {
    try {
      var responses = UrlFetchApp.fetchAll(fetchArgs.map(function(x) { return x._o; }));
      for (var j = 0; j < responses.length; j++) {
        var resp = responses[j];
        var originalIndex = fetchArgs[j]._i;
        results[originalIndex] = {
          s: resp.getResponseCode(),
          h: resp.getHeaders(),
          b: Utilities.base64Encode(resp.getContent()),
        };
      }
    } catch (err) {
      // Fill any slot not yet written with an error entry
      for (var k = 0; k < fetchArgs.length; k++) {
        var idx = fetchArgs[k]._i;
        if (results[idx] === undefined) {
          results[idx] = { e: "fetch failed: " + String(err) };
        }
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
    deadline: FETCH_DEADLINE,
  };
  if (req.h && typeof req.h === "object") {
    var headers = {};
    for (var k in req.h) {
      if (req.h.hasOwnProperty(k) && !SKIP_HEADERS[k.toLowerCase()]) {
        headers[k] = req.h[k];
      }
    }
    opts.headers = headers;
  }
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
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(
    ContentService.MimeType.JSON
  );
}