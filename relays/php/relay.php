<?php

declare(strict_types=1);

/*
 * MasterHttpRelayVPN - Simple PHP Relay
 * Copyright (c) 2026 MasterkinG32.
 * Github: https://github.com/masterking32/MasterHttpRelayVPN
 *
 * Test relay endpoint:
 * - Accepts the incoming HTTP request
 * - Forwards the raw request body to the configured upstream URL
 * - Returns the upstream response status/body to the caller
 *
 * Configuration:
 * 1. Edit UPSTREAM_URL below
 * 2. Or set environment variable RELAY_UPSTREAM_URL
 */

const UPSTREAM_URL = 'https://example.com/relay';
const CONNECT_TIMEOUT_SECONDS = 10;
const REQUEST_TIMEOUT_SECONDS = 30;

$upstreamUrl = getenv('RELAY_UPSTREAM_URL');
if (!is_string($upstreamUrl) || trim($upstreamUrl) === '') {
    $upstreamUrl = UPSTREAM_URL;
}

if (trim($upstreamUrl) === '' || $upstreamUrl === 'https://example.com/relay') {
    http_response_code(500);
    header('Content-Type: text/plain; charset=utf-8');
    echo "PHP relay is not configured. Set RELAY_UPSTREAM_URL or edit UPSTREAM_URL.\n";
    exit;
}

$method = $_SERVER['REQUEST_METHOD'] ?? 'POST';
$rawBody = file_get_contents('php://input');
if ($rawBody === false) {
    http_response_code(400);
    header('Content-Type: text/plain; charset=utf-8');
    echo "Failed to read request body.\n";
    exit;
}

$incomingHeaders = function_exists('getallheaders') ? getallheaders() : [];
$forwardHeaders = [];

foreach ($incomingHeaders as $name => $value) {
    $lower = strtolower((string) $name);
    if (in_array($lower, ['host', 'content-length', 'connection'], true)) {
        continue;
    }

    $forwardHeaders[] = $name . ': ' . $value;
}

if (!hasHeader($forwardHeaders, 'Content-Type')) {
    $contentType = $_SERVER['CONTENT_TYPE'] ?? 'application/octet-stream';
    $forwardHeaders[] = 'Content-Type: ' . $contentType;
}

$forwardHeaders[] = 'X-Relay-By: MasterHttpRelayVPN-PHP';
$forwardHeaders[] = 'X-Forwarded-Method: ' . $method;

$ch = curl_init($upstreamUrl);
if ($ch === false) {
    http_response_code(500);
    header('Content-Type: text/plain; charset=utf-8');
    echo "Failed to initialize cURL.\n";
    exit;
}

$responseHeaders = [];

curl_setopt_array($ch, [
    CURLOPT_CUSTOMREQUEST => $method,
    CURLOPT_POSTFIELDS => $rawBody,
    CURLOPT_HTTPHEADER => $forwardHeaders,
    CURLOPT_RETURNTRANSFER => true,
    CURLOPT_FOLLOWLOCATION => false,
    CURLOPT_CONNECTTIMEOUT => CONNECT_TIMEOUT_SECONDS,
    CURLOPT_TIMEOUT => REQUEST_TIMEOUT_SECONDS,
    CURLOPT_HEADERFUNCTION => static function ($curl, string $headerLine) use (&$responseHeaders): int {
        $trimmed = trim($headerLine);
        if ($trimmed !== '') {
            $responseHeaders[] = $trimmed;
        }
        return strlen($headerLine);
    },
]);

$responseBody = curl_exec($ch);
$curlError = curl_error($ch);
$statusCode = (int) curl_getinfo($ch, CURLINFO_RESPONSE_CODE);
$responseContentType = (string) curl_getinfo($ch, CURLINFO_CONTENT_TYPE);
curl_close($ch);

if ($responseBody === false) {
    http_response_code(502);
    header('Content-Type: text/plain; charset=utf-8');
    echo "Upstream relay failed: {$curlError}\n";
    exit;
}

if ($statusCode > 0) {
    http_response_code($statusCode);
}

if ($responseContentType !== '') {
    header('Content-Type: ' . $responseContentType);
} else {
    header('Content-Type: application/octet-stream');
}

echo $responseBody;

function hasHeader(array $headers, string $headerName): bool
{
    $needle = strtolower($headerName) . ':';
    foreach ($headers as $header) {
        if (str_starts_with(strtolower($header), $needle)) {
            return true;
        }
    }

    return false;
}
