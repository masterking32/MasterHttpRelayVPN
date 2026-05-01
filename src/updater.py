"""
Automatic update checker and applier.

Queries the GitHub Releases API for a newer version, downloads the
release zip, and copies changed files into the project root.
Protected files (config.json, .venv, ca/, adblock_cache/) are never
touched, so user settings and certificates are preserved.
"""

import json
import logging
import os
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path

log = logging.getLogger("Updater")

GITHUB_REPO  = "masterking32/MasterHttpRelayVPN"
API_URL      = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
CHECK_TIMEOUT    = 10   # seconds — kept short so startup isn't blocked
DOWNLOAD_TIMEOUT = 60   # seconds

# These are never overwritten during an update
_PROTECTED = frozenset({
    "config.json",
    ".venv",
    "venv",
    "env",
    "ca",
    "adblock_cache",
    "routing_cache.json",
    "ideas.md",
    ".env",
    ".git",
    "__pycache__",
})


# ── Version helpers ───────────────────────────────────────────────────────────

def parse_version(v: str) -> tuple[int, ...]:
    """'v1.2.3' or '1.2.3' → (1, 2, 3). Unknown formats → (0,)."""
    try:
        return tuple(int(x) for x in v.lstrip("v").strip().split("."))
    except ValueError:
        return (0,)


def is_newer(remote: str, current: str) -> bool:
    return parse_version(remote) > parse_version(current)


# ── GitHub API ────────────────────────────────────────────────────────────────

def fetch_latest_release() -> dict | None:
    """
    Query GitHub Releases API and return the parsed JSON, or None on error.
    Uses a short timeout so a missing network connection doesn't stall startup.
    """
    try:
        req = urllib.request.Request(
            API_URL,
            headers={"User-Agent": "MasterHttpRelayVPN-updater"},
        )
        with urllib.request.urlopen(req, timeout=CHECK_TIMEOUT) as resp:
            return json.loads(resp.read().decode())
    except Exception as exc:
        log.debug("Update check failed: %s", exc)
        return None


def check_update(current_version: str) -> dict | None:
    """
    Return a release-info dict if a newer version exists, else None.

    Returned dict keys:
        version     — bare version string, e.g. "1.2.0"
        tag         — git tag, e.g. "v1.2.0"
        zipball_url — URL to download the source zip
        html_url    — release page URL (for display)
        body        — release notes (may be empty)
    """
    data = fetch_latest_release()
    if not data:
        return None
    tag = data.get("tag_name", "")
    if not tag or not is_newer(tag, current_version):
        return None
    return {
        "version":     tag.lstrip("v"),
        "tag":         tag,
        "zipball_url": data.get("zipball_url", ""),
        "html_url":    data.get("html_url", ""),
        "body":        (data.get("body") or "").strip(),
    }


# ── Updater ───────────────────────────────────────────────────────────────────

def apply_update(release: dict, project_root: Path) -> bool:
    """
    Download release zip, extract it, and copy files into project_root.
    Protected paths are skipped. Returns True on success.
    """
    zipball_url = release["zipball_url"]
    log.info("Downloading v%s from GitHub …", release["version"])

    with tempfile.TemporaryDirectory() as tmp:
        zip_path = os.path.join(tmp, "update.zip")

        # ── Download ──────────────────────────────────────────────────────────
        try:
            req = urllib.request.Request(
                zipball_url,
                headers={"User-Agent": "MasterHttpRelayVPN-updater"},
            )
            with urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT) as resp:
                with open(zip_path, "wb") as fh:
                    shutil.copyfileobj(resp, fh)
        except Exception as exc:
            log.error("Download failed: %s", exc)
            return False

        # ── Extract ───────────────────────────────────────────────────────────
        extract_dir = os.path.join(tmp, "src")
        try:
            with zipfile.ZipFile(zip_path) as zf:
                zf.extractall(extract_dir)
        except Exception as exc:
            log.error("Extraction failed: %s", exc)
            return False

        # GitHub zipball always has one top-level dir (user-repo-commitsha/)
        entries = os.listdir(extract_dir)
        source_dir = Path(extract_dir) / entries[0] if len(entries) == 1 else Path(extract_dir)

        # ── Copy into project, skipping protected paths ───────────────────────
        _copy_tree(source_dir, project_root)

    log.info("Updated to v%s successfully.", release["version"])
    return True


def _copy_tree(src: Path, dst: Path) -> None:
    for item in src.iterdir():
        if item.name in _PROTECTED or item.name.startswith("."):
            log.debug("Skipping protected path: %s", item.name)
            continue
        target = dst / item.name
        if item.is_dir():
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(item, target)
            log.debug("Updated dir : %s/", item.name)
        else:
            shutil.copy2(item, target)
            log.debug("Updated file: %s", item.name)
