#!/usr/bin/env bash
# MasterHttpRelayVPN one-click launcher (Linux / macOS)
# Creates a local virtualenv, installs deps, runs the setup wizard
# if needed, then starts the proxy.

set -e
cd "$(dirname "$0")"

VENV_DIR=".venv"

find_python() {
    for cmd in python3.12 python3.11 python3.10 python3 python; do
        if command -v "$cmd" >/dev/null 2>&1; then
            ver=$("$cmd" -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null || echo "0.0")
            major=${ver%.*}; minor=${ver#*.}
            if [ "$major" -ge 3 ] && [ "$minor" -ge 10 ]; then
                echo "$cmd"
                return 0
            fi
        fi
    done
    return 1
}

PY=$(find_python) || {
    echo "[X] Python 3.10+ not found. Install it and re-run this script." >&2
    exit 1
}

if [ ! -x "$VENV_DIR/bin/python" ]; then
    echo "[*] Creating virtual environment in $VENV_DIR ..."
    "$PY" -m venv "$VENV_DIR"
fi

VPY="$VENV_DIR/bin/python"

echo "[*] Installing dependencies ..."
"$VPY" -m pip install --disable-pip-version-check -q --upgrade pip >/dev/null
if ! "$VPY" -m pip install --disable-pip-version-check -q -r requirements.txt; then
    echo "[!] PyPI install failed. Retrying via runflare mirror ..."
    "$VPY" -m pip install --disable-pip-version-check -q -r requirements.txt \
        -i https://mirror-pypi.runflare.com/simple/ \
        --trusted-host mirror-pypi.runflare.com
fi

if [ ! -f "config.json" ]; then
    echo "[*] No config.json found — launching setup wizard ..."
    "$VPY" setup.py
fi

# -------- Check for adblock update flag --------
for arg in "$@"; do
    if [ "$arg" = "--update-adblock" ]; then
        echo
        echo "[*] Force-refreshing adblock blocklists ..."
        echo
        "$VPY" main.py --update-adblock
        RC=$?
        if [ $RC -eq 0 ]; then
            echo
            echo "[+] Adblock lists updated successfully."
        else
            echo
            echo "[!] Adblock update failed. Check the output above."
        fi
        exit $RC
    fi
done

# -------- Auto-update check (skip with --skip-update) --------
_SKIP_UPDATE=0
for arg in "$@"; do
    if [ "$arg" = "--skip-update" ]; then
        _SKIP_UPDATE=1
        break
    fi
done

if [ $_SKIP_UPDATE -eq 0 ]; then
    echo "[*] Checking for updates ..."
    set +e
    "$VPY" main.py --update
    UPDATE_RC=$?
    set -e
    if [ $UPDATE_RC -eq 2 ]; then
        echo
        echo "[*] Update applied — re-installing dependencies ..."
        "$VPY" -m pip install --disable-pip-version-check -q -r requirements.txt
        echo "[+] Ready. Restarting with updated version ..."
        echo
    elif [ $UPDATE_RC -eq 1 ]; then
        echo "[!] Update failed — starting with current version."
    fi
fi

echo
echo "[*] Starting MasterHttpRelayVPN ..."
echo
# Strip --skip-update from args before passing to main.py
_ARGS=()
for arg in "$@"; do
    [ "$arg" != "--skip-update" ] && _ARGS+=("$arg")
done
exec "$VPY" main.py "${_ARGS[@]}"
