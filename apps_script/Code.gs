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
};

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

function _doSingle(req) {
  if (!req.u || typeof req.u !== "string" || !req.u.match(/^https?:\/\//i)) {
    return _json({ e: "bad url" });
  }
  var opts = _buildOpts(req);
  var resp = UrlFetchApp.fetch(req.u, opts);
  return _json({
    s: resp.getResponseCode(),
    h: _respHeaders(resp),
    b: Utilities.base64Encode(resp.getContent()),
  });
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
        results.push({
          s: resp.getResponseCode(),
          h: _respHeaders(resp),
          b: Utilities.base64Encode(resp.getContent()),
        });
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
  // HtmlService responses can stay on script.google.com for /dev, while
  // ContentService commonly bounces through script.googleusercontent.com.
  // The Python client extracts the JSON payload from the body either way.
  return HtmlService.createHtmlOutput(JSON.stringify(obj)).setXFrameOptionsMode(
    HtmlService.XFrameOptionsMode.ALLOWALL
  );
}
