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
 * TELEGRAM NOTIFICATIONS (optional):
 *   1. Create a bot via @BotFather and copy the token into TELEGRAM_BOT_TOKEN
 *   2. Get your chat ID and copy it into TELEGRAM_CHAT_ID
 *   3. Set INSTANCE_NAME to identify this deployment in notifications
 *   4. To enable periodic usage checks: Triggers → Add trigger →
 *      Function: checkUsageAndNotify | Time-driven | Minutes timer |
 *      Every N minutes (set to match USAGE_CHECK_INTERVAL_MINUTES)
 *
 * CHANGE ALL PLACEHOLDER VALUES BELOW BEFORE DEPLOYING!
 */

const AUTH_KEY = "CHANGE_ME_TO_A_STRONG_SECRET";
const INSTANCE_NAME = "MyRelay";

const TELEGRAM_BOT_TOKEN = "YOUR_BOT_TOKEN_HERE";
const TELEGRAM_CHAT_ID = "YOUR_CHAT_ID_HERE";
const USAGE_CHECK_INTERVAL_MINUTES = 5;
const DAILY_EXECUTION_LIMIT = 20000;
const WARNING_THRESHOLDS = [0.5, 0.75, 0.9, 0.95, 0.99];

const SKIP_HEADERS = {
  host: 1, connection: 1, "content-length": 1,
  "transfer-encoding": 1, "proxy-connection": 1, "proxy-authorization": 1,
  "priority": 1, te: 1,
};

const USAGE_STORAGE_KEY = "daily_usage_data";

function getUsageStore() {
  const props = PropertiesService.getScriptProperties();
  let usage = props.getProperty(USAGE_STORAGE_KEY);
  const now = new Date();
  const today = now.toDateString();

  if (!usage) {
    usage = {
      date: today,
      totalRequests: 0,
      batchRequests: 0,
      singleRequests: 0,
      lastUpdated: now.toISOString(),
      warningSent: [],
    };
    props.setProperty(USAGE_STORAGE_KEY, JSON.stringify(usage));
    sendTelegramMessage(
      "🚀 Domain Front Relay initialized\n" +
      `Daily limit: ${DAILY_EXECUTION_LIMIT.toLocaleString()} requests\n` +
      `Monitoring started for ${today}`
    );
  } else {
    usage = JSON.parse(usage);
    if (usage.date !== today) {
      const previousDayTotal = usage.totalRequests;
      sendTelegramMessage(
        `📊 <b>Daily Reset - Previous Day Summary</b>\n\n` +
        `📅 Date: ${usage.date}\n` +
        `✅ Total Requests: <code>${previousDayTotal.toLocaleString()}</code>\n` +
        `📈 Daily limit usage: <b>${((previousDayTotal / DAILY_EXECUTION_LIMIT) * 100).toFixed(1)}%</b>\n` +
        `🔄 Resetting counter for ${today}`
      );
      usage = {
        date: today,
        totalRequests: 0,
        batchRequests: 0,
        singleRequests: 0,
        lastUpdated: now.toISOString(),
        warningSent: [],
      };
      props.setProperty(USAGE_STORAGE_KEY, JSON.stringify(usage));
    }
  }
  return usage;
}

function updateUsage(type, count) {
  count = count || 1;
  const usage = getUsageStore();
  usage.totalRequests += count;

  if (type === "batch") {
    usage.batchRequests += count;
  } else if (type === "single") {
    usage.singleRequests += count;
  }

  usage.lastUpdated = new Date().toISOString();
  PropertiesService.getScriptProperties().setProperty(USAGE_STORAGE_KEY, JSON.stringify(usage));

  if (usage.totalRequests > DAILY_EXECUTION_LIMIT) {
    sendTelegramMessage(
      `🚨 <b>CRITICAL: Daily execution limit EXCEEDED!</b>\n\n` +
      `📊 Current: ${usage.totalRequests.toLocaleString()}\n` +
      `⚠️ Limit: ${DAILY_EXECUTION_LIMIT.toLocaleString()}\n` +
      `🔴 Exceeded by: ${(usage.totalRequests - DAILY_EXECUTION_LIMIT).toLocaleString()}\n\n` +
      `❗ Immediate action required!`
    );
    return usage;
  }

  const percentage = usage.totalRequests / DAILY_EXECUTION_LIMIT;
  for (var i = 0; i < WARNING_THRESHOLDS.length; i++) {
    const threshold = WARNING_THRESHOLDS[i];
    if (percentage >= threshold && usage.warningSent.indexOf(threshold) === -1) {
      usage.warningSent.push(threshold);
      PropertiesService.getScriptProperties().setProperty(USAGE_STORAGE_KEY, JSON.stringify(usage));

      var emoji = "⚠️";
      var severity = "WARNING";
      if (threshold >= 0.95) {
        emoji = "🚨";
        severity = "CRITICAL";
      } else if (threshold >= 0.9) {
        emoji = "🔴";
        severity = "HIGH";
      } else if (threshold >= 0.75) {
        emoji = "🟠";
        severity = "MEDIUM";
      }

      const remaining = DAILY_EXECUTION_LIMIT - usage.totalRequests;
      const estimatedTime = estimateTimeRemaining(remaining);

      sendTelegramMessage(
        `${emoji} <b>${severity} Usage Alert - ${Math.round(percentage * 100)}% of daily limit</b>\n\n` +
        `📊 Current usage: ${usage.totalRequests.toLocaleString()} / ${DAILY_EXECUTION_LIMIT.toLocaleString()}\n` +
        `📈 Percentage: <b>${(percentage * 100).toFixed(1)}%</b>\n` +
        `✅ Remaining: ${remaining.toLocaleString()} requests\n` +
        `⏱️ Estimated time remaining: ${estimatedTime}\n\n` +
        `📋 Breakdown:\n` +
        `• Single requests: ${usage.singleRequests.toLocaleString()}\n` +
        `• Batch requests: ${usage.batchRequests.toLocaleString()}`
      );
    }
  }

  return usage;
}

function estimateTimeRemaining(remainingRequests) {
  const usage = getUsageStore();
  const now = new Date();
  const startOfDay = new Date(now);
  startOfDay.setHours(0, 0, 0, 0);

  const hoursElapsed = (now - startOfDay) / (1000 * 60 * 60);
  if (hoursElapsed < 0.1 || usage.totalRequests === 0) {
    return "Unknown (insufficient data)";
  }

  const requestsPerHour = usage.totalRequests / hoursElapsed;
  if (requestsPerHour === 0) return "Unknown";

  const hoursRemaining = remainingRequests / requestsPerHour;
  if (hoursRemaining < 1) {
    return `${Math.round(hoursRemaining * 60)} minutes`;
  }
  return `${hoursRemaining.toFixed(1)} hours`;
}

function sendTelegramMessage(message) {
  if (TELEGRAM_BOT_TOKEN === "YOUR_BOT_TOKEN_HERE") {
    console.log("Telegram not configured — skipping notification");
    return false;
  }

  const labeledMessage = `🏷️ <b>[${INSTANCE_NAME}]</b>\n${message}`;
  const url = `https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage`;
  const payload = {
    chat_id: TELEGRAM_CHAT_ID,
    text: labeledMessage,
    parse_mode: "HTML",
  };

  try {
    const options = {
      method: "post",
      contentType: "application/json",
      payload: JSON.stringify(payload),
      muteHttpExceptions: true,
    };
    const response = UrlFetchApp.fetch(url, options);
    const result = JSON.parse(response.getContentText());
    return result.ok;
  } catch (err) {
    console.error("Failed to send Telegram message:", err);
    return false;
  }
}

function sendPeriodicUpdate() {
  const usage = getUsageStore();
  const now = new Date();
  const percentage = (usage.totalRequests / DAILY_EXECUTION_LIMIT) * 100;
  const remaining = DAILY_EXECUTION_LIMIT - usage.totalRequests;

  var statusEmoji = "✅";
  if (percentage >= 90) statusEmoji = "🚨";
  else if (percentage >= 75) statusEmoji = "🔴";
  else if (percentage >= 50) statusEmoji = "🟠";
  else if (percentage >= 25) statusEmoji = "🟡";

  const message =
    `⏰ Time: ${now.toLocaleString()}\n` +
    `📊 Usage: ${usage.totalRequests.toLocaleString()} / ${DAILY_EXECUTION_LIMIT.toLocaleString()} (${percentage.toFixed(1)}%)\n` +
    `${statusEmoji} Remaining: ${remaining.toLocaleString()} requests\n` +
    `⏱️ Last update: ${new Date(usage.lastUpdated).toLocaleTimeString()}`;

  if (percentage >= 90) {
    sendTelegramMessage(`🚨 <b>URGENT: Approaching daily limit!</b>\n${message}`);
  } else {
    sendTelegramMessage(message);
  }
}

function sendDailyUsageReport() {
  const usage = getUsageStore();
  const percentage = (usage.totalRequests / DAILY_EXECUTION_LIMIT) * 100;
  const remaining = DAILY_EXECUTION_LIMIT - usage.totalRequests;

  var statusIcon = "✅";
  var statusText = "Within limit";
  if (usage.totalRequests > DAILY_EXECUTION_LIMIT) {
    statusIcon = "❌";
    statusText = "LIMIT EXCEEDED";
  } else if (percentage >= 90) {
    statusIcon = "⚠️";
    statusText = "Critical - Near limit";
  } else if (percentage >= 75) {
    statusIcon = "⚠️";
    statusText = "Warning - High usage";
  }

  const message =
    `📊 <b>Domain Front Relay - Daily Summary</b>\n\n` +
    `📅 Date: ${usage.date}\n` +
    `🕐 Last update: ${new Date(usage.lastUpdated).toLocaleString()}\n\n` +
    `<b>Statistics:</b>\n` +
    `• Total Requests: <code>${usage.totalRequests.toLocaleString()}</code>\n` +
    `• Daily Limit: <code>${DAILY_EXECUTION_LIMIT.toLocaleString()}</code>\n` +
    `• Usage: <b>${percentage.toFixed(1)}%</b>\n` +
    `• Remaining: <code>${remaining.toLocaleString()}</code>\n\n` +
    `<b>Breakdown:</b>\n` +
    `• Single Mode: <code>${usage.singleRequests.toLocaleString()}</code>\n` +
    `• Batch Mode: <code>${usage.batchRequests.toLocaleString()}</code>\n\n` +
    `${statusIcon} Status: ${statusText}`;

  return sendTelegramMessage(message);
}

function doPost(e) {
  try {
    var req = JSON.parse(e.postData.contents);
    if (req.k !== AUTH_KEY) return _json({ e: "unauthorized" });

    if (Array.isArray(req.q)) {
      updateUsage("batch", req.q.length);
      return _doBatch(req.q);
    }

    updateUsage("single");
    return _doSingle(req);
  } catch (err) {
    sendTelegramMessage(`❌ <b>Error in doPost</b>\n\n${String(err)}`);
    return _json({ e: String(err) });
  }
}

function checkUsageAndNotify() {
  const usage = getUsageStore();
  const now = new Date();
  const hour = now.getHours();
  const minute = now.getMinutes();

  if (hour === 23 && minute >= 55) {
    sendDailyUsageReport();
  } else {
    sendPeriodicUpdate();
  }
}

function resetDailyUsage() {
  const props = PropertiesService.getScriptProperties();
  const now = new Date();
  const resetUsage = {
    date: now.toDateString(),
    totalRequests: 0,
    batchRequests: 0,
    singleRequests: 0,
    lastUpdated: now.toISOString(),
    warningSent: [],
  };
  props.setProperty(USAGE_STORAGE_KEY, JSON.stringify(resetUsage));
  sendTelegramMessage(
    `🔄 <b>Daily usage manually reset</b>\n` +
    `New tracking started for ${now.toDateString()}\n` +
    `Daily limit: ${DAILY_EXECUTION_LIMIT.toLocaleString()}`
  );
}

function getCurrentUsageStats() {
  const usage = getUsageStore();
  const percentage = (usage.totalRequests / DAILY_EXECUTION_LIMIT) * 100;
  const remaining = DAILY_EXECUTION_LIMIT - usage.totalRequests;

  const stats =
    `📈 <b>Current Usage Stats</b>\n\n` +
    `📅 ${usage.date}\n` +
    `✅ Total: ${usage.totalRequests.toLocaleString()} / ${DAILY_EXECUTION_LIMIT.toLocaleString()}\n` +
    `📊 ${percentage.toFixed(1)}% used\n` +
    `💚 Remaining: ${remaining.toLocaleString()}\n` +
    `🔄 Single: ${usage.singleRequests.toLocaleString()}\n` +
    `📦 Batch: ${usage.batchRequests.toLocaleString()}`;

  sendTelegramMessage(stats);
  return usage;
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
  var errorMap = {};

  for (var i = 0; i < items.length; i++) {
    var item = items[i];
    if (!item.u || typeof item.u !== "string" || !item.u.match(/^https?:\/\//i)) {
      errorMap[i] = "bad url";
      continue;
    }
    var opts = _buildOpts(item);
    opts.url = item.u;
    fetchArgs.push({ _i: i, _o: opts });
  }

  // fetchAll() processes all requests in parallel inside Google
  var responses = [];
  if (fetchArgs.length > 0) {
    responses = UrlFetchApp.fetchAll(fetchArgs.map(function(x) { return x._o; }));
  }

  var results = [];
  var rIdx = 0;
  for (var i = 0; i < items.length; i++) {
    if (errorMap.hasOwnProperty(i)) {
      results.push({ e: errorMap[i] });
    } else {
      var resp = responses[rIdx++];
      results.push({
        s: resp.getResponseCode(),
        h: _respHeaders(resp),
        b: Utilities.base64Encode(resp.getContent()),
      });
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
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(
    ContentService.MimeType.JSON
  );
}