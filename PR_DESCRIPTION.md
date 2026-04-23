# Suggested PR Title

Improve relay stability and add streamed parallel downloads for large files

## Summary

This PR focuses on making the Apps Script relay safer, more stable, and significantly more usable for large downloads.

The biggest change is a new streamed parallel download path for likely large files. Instead of buffering the entire file and only handing it to the browser at the end, the proxy can now start sending data to the client immediately while fetching the remaining ranges in parallel. This gives users real browser download progress and fixes the old behavior where the browser looked stuck on loading until the full file had already finished downloading in the background.

Alongside that, this PR hardens request handling, improves shutdown behavior, adds safer defaults, reduces quota pressure for large downloads, and makes HTTP/2 fallback behavior much more defensive when the transport becomes unstable.

## What Changed

### Large-file downloads

- Added a streamed parallel download path for likely large downloads
- Started sending response headers/body to the client immediately instead of waiting for full buffering
- Added disk-backed spooling for ordered chunk delivery during streaming downloads
- Added per-range validation using `Content-Range` and expected body length checks
- Added initial range-probe retries before falling back
- Added host cooldown for flaky streaming download targets so repeatedly failing hosts do not keep producing broken partial downloads
- Moved range-download traffic off the shared H2 relay path and onto the H1 pool to avoid destabilizing normal relay traffic
- Added progress logging in a more readable terminal format:
  - elapsed time
  - progress bar
  - bytes downloaded / total
  - current download speed
- Added `.bin` to default large-download extension heuristics

### Stability and relay behavior

- Added configurable relay and connect timeouts
- Added configurable response body cap enforcement across buffered relay paths
- Made retry behavior safer by limiting retries for non-idempotent requests
- Improved GET request coalescing by including representation-relevant headers in the coalescing key
- Added bounded chunk/request tuning for large downloads to reduce quota pressure
- Added safer defaults for:
  - `chunked_download_chunk_size`
  - `chunked_download_max_parallel`
  - `chunked_download_max_chunks`
- Added explicit handling for unsupported request `Transfer-Encoding`

### HTTP/2 hardening

- Fixed H2 reconnect/reader lifecycle issues that could cause reconnect storms or stale-reader interference
- Added temporary H2 cooldown/circuit-breaker behavior after repeated consecutive H2 failures
- Reduced noisy close-notify behavior and improved connection lifecycle handling
- Prevented large parallel downloads from destabilizing the shared H2 relay path used by normal traffic

### Security / defaults / config hygiene

- Removed permissive CORS behavior that effectively bypassed browser CORS protections
- Changed `lan_sharing` to be opt-in by default
- Updated setup flow so LAN sharing is explicitly prompted instead of silently inheriting insecure defaults
- Synced docs and config examples with actual runtime behavior

### Shutdown / cleanup

- Improved shutdown cleanup so active client tasks are tracked, cancelled, and awaited during stop
- Reduced `Task was destroyed but it is pending` noise on normal shutdown

## Config Additions / Changes

Added or documented the following config options:

- `relay_timeout`
- `tls_connect_timeout`
- `tcp_connect_timeout`
- `max_response_body_bytes`
- `chunked_download_extensions`
- `chunked_download_min_size`
- `chunked_download_chunk_size`
- `chunked_download_max_parallel`
- `chunked_download_max_chunks`

Default download tuning was also made more conservative to improve stability.

## Bugs Fixed

- Large downloads appearing only after full buffering instead of progressing in-browser
- Browser download UX looking stalled while the proxy was still downloading in the background
- Frequent range chunk corruption/acceptance without validating `Content-Range`
- H2 reconnect/reader race behavior causing repeated `H2 reader loop ended` / `Connection lost` cascades
- Insecure default LAN exposure
- Dangerous CORS response injection behavior
- Inconsistent response size-cap enforcement
- Pending asyncio task noise during shutdown
- Silent mishandling of unsupported `Transfer-Encoding` requests

## Manual Testing

### Download tests

- `https://ash-speed.hetzner.com/100MB.bin`
  - Download completed successfully
  - Browser showed progressive download behavior
  - Terminal progress output updated correctly
- `https://fsn1-speed.hetzner.com/100MB.bin`
  - Exposed host-specific flakiness during range streaming
  - Added host cooldown / fallback protection after repeated streaming failures
- `https://fsn1-speed.hetzner.com/1GB.bin`
  - Download completed successfully
  - Completed in under 5 minutes in real-world testing
  - Browser showed real incremental progress instead of waiting for full buffering

### Runtime / startup / shutdown checks

- Verified startup with the current config
- Verified graceful `Ctrl+C` shutdown behavior after cleanup changes
- Ran compile validation:
  - `python3 -m compileall main.py src setup.py`

## Result / Impact

This PR substantially improves the user experience and resilience of the proxy:

- Large downloads now behave like real downloads in the browser
- 100MB and 1GB files can complete successfully through the relay
- The system no longer feels stuck during large transfers
- Large-download traffic is isolated from the shared H2 relay path, reducing collateral instability
- Repeated H2 failures now degrade more safely instead of spamming reconnect errors
- Defaults are safer, especially for LAN exposure and browser security behavior
- Shutdown is cleaner and less noisy

## Notes

- Some hosts are still inherently flakier than others for parallel range downloading through Apps Script, so the host cooldown/fallback behavior is intentional and defensive
- This PR prioritizes correctness, safer degradation, and usable large-file behavior over forcing aggressive parallelism on every host

## Screenshot

Use the attached screenshot in the PR to show both browser-side progress and terminal-side progress:

- Local file: `/Users/pouriarc/Downloads/photo_2026-04-23 14.55.20.jpeg`

Recommended caption:

> 1GB download progressing normally in the browser while the relay reports live chunk progress and throughput in the terminal.

