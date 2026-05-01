"""
Cross-platform trusted CA certificate installer.

Supports: Windows, macOS, Linux (Debian/Ubuntu, RHEL/Fedora/CentOS, Arch).
Also attempts to install into Firefox's NSS certificate store when found.

Usage:
    from cert_installer import install_ca, is_ca_trusted
    install_ca("/path/to/ca.crt", cert_name="MasterHttpRelayVPN")
"""

import glob
import logging
import os
import platform
import shutil
import subprocess
import sys
import tempfile

log = logging.getLogger("Cert")

CERT_NAME = "MasterHttpRelayVPN"


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _run(cmd: list[str], *, check: bool = True, capture: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        check=check,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )


def _has_cmd(name: str) -> bool:
    return shutil.which(name) is not None


# ─────────────────────────────────────────────────────────────────────────────
# Windows
# ─────────────────────────────────────────────────────────────────────────────

def _install_windows(cert_path: str, cert_name: str) -> bool:
    """
    Install into the current user's Trusted Root store (no admin required).
    Falls back to the system store if certutil fails.
    """
    # Per-user store — works without elevation
    try:
        _run(["certutil", "-addstore", "-user", "Root", cert_path])
        log.info("Certificate installed in Windows user Trusted Root store.")
        return True
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        log.warning("certutil user store failed: %s", exc)

    # Try system store (requires admin)
    try:
        _run(["certutil", "-addstore", "Root", cert_path])
        log.info("Certificate installed in Windows system Trusted Root store.")
        return True
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        log.error("certutil system store failed: %s", exc)

    # Fallback: use PowerShell
    try:
        ps_cmd = (
            f"Import-Certificate -FilePath '{cert_path}' "
            f"-CertStoreLocation Cert:\\CurrentUser\\Root"
        )
        _run(["powershell", "-NoProfile", "-Command", ps_cmd])
        log.info("Certificate installed via PowerShell.")
        return True
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        log.error("PowerShell install failed: %s", exc)

    return False


def _is_trusted_windows(cert_path: str) -> bool:
    # Check by thumbprint only — the cert name is also the folder name, so
    # a plain string search on certutil output would produce a false positive.
    thumbprint = _cert_thumbprint(cert_path)
    if not thumbprint:
        return False
    try:
        result = _run(["certutil", "-user", "-store", "Root"])
        output = result.stdout.decode(errors="replace").upper()
        return thumbprint in output
    except Exception:
        return False


def _cert_thumbprint(cert_path: str) -> str:
    """Return the SHA-1 thumbprint of a PEM cert (uppercase hex, no colons)."""
    try:
        from cryptography import x509 as _x509
        from cryptography.hazmat.primitives import hashes as _hashes
        with open(cert_path, "rb") as f:
            cert = _x509.load_pem_x509_certificate(f.read())
        return cert.fingerprint(_hashes.SHA1()).hex().upper()
    except Exception:
        return ""


# ─────────────────────────────────────────────────────────────────────────────
# macOS
# ─────────────────────────────────────────────────────────────────────────────

def _install_macos(cert_path: str, cert_name: str) -> bool:
    """Install into the login keychain (per-user, no sudo required)."""
    login_keychain = os.path.expanduser("~/Library/Keychains/login.keychain-db")
    if not os.path.exists(login_keychain):
        login_keychain = os.path.expanduser("~/Library/Keychains/login.keychain")

    try:
        _run([
            "security", "add-trusted-cert",
            "-d", "-r", "trustRoot",
            "-k", login_keychain,
            cert_path,
        ])
        log.info("Certificate installed in macOS login keychain.")
        return True
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        log.warning("login keychain install failed: %s. Trying system keychain (needs sudo)…", exc)

    try:
        _run([
            "sudo", "security", "add-trusted-cert",
            "-d", "-r", "trustRoot",
            "-k", "/Library/Keychains/System.keychain",
            cert_path,
        ])
        log.info("Certificate installed in macOS system keychain.")
        return True
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        log.error("System keychain install failed: %s", exc)

    return False


def _is_trusted_macos(cert_name: str) -> bool:
    try:
        result = _run(["security", "find-certificate", "-a", "-c", cert_name])
        return bool(result.stdout.strip())
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Linux
# ─────────────────────────────────────────────────────────────────────────────

def _detect_linux_distro() -> str:
    """Return 'debian', 'rhel', 'arch', or 'unknown'."""
    if os.path.exists("/etc/debian_version") or os.path.exists("/etc/ubuntu"):
        return "debian"
    if os.path.exists("/etc/redhat-release") or os.path.exists("/etc/fedora-release"):
        return "rhel"
    if os.path.exists("/etc/arch-release"):
        return "arch"
    # Read /etc/os-release as fallback
    try:
        with open("/etc/os-release") as f:
            content = f.read().lower()
        if "debian" in content or "ubuntu" in content or "mint" in content:
            return "debian"
        if "fedora" in content or "rhel" in content or "centos" in content or "rocky" in content or "alma" in content:
            return "rhel"
        if "arch" in content or "manjaro" in content:
            return "arch"
    except OSError:
        pass
    return "unknown"


def _install_linux(cert_path: str, cert_name: str) -> bool:
    distro = _detect_linux_distro()
    log.info("Detected Linux distro family: %s", distro)

    installed = False

    if distro == "debian":
        dest_dir = "/usr/local/share/ca-certificates"
        dest_file = os.path.join(dest_dir, f"{cert_name.replace(' ', '_')}.crt")
        try:
            os.makedirs(dest_dir, exist_ok=True)
            shutil.copy2(cert_path, dest_file)
            _run(["update-ca-certificates"])
            log.info("Certificate installed via update-ca-certificates.")
            installed = True
        except (OSError, subprocess.CalledProcessError) as exc:
            log.warning("Debian install failed (needs sudo?): %s", exc)
            # Try with sudo
            try:
                _run(["sudo", "cp", cert_path, dest_file])
                _run(["sudo", "update-ca-certificates"])
                log.info("Certificate installed via sudo update-ca-certificates.")
                installed = True
            except (subprocess.CalledProcessError, FileNotFoundError) as exc2:
                log.error("sudo Debian install failed: %s", exc2)

    elif distro == "rhel":
        dest_dir = "/etc/pki/ca-trust/source/anchors"
        dest_file = os.path.join(dest_dir, f"{cert_name.replace(' ', '_')}.crt")
        try:
            os.makedirs(dest_dir, exist_ok=True)
            shutil.copy2(cert_path, dest_file)
            _run(["update-ca-trust", "extract"])
            log.info("Certificate installed via update-ca-trust.")
            installed = True
        except (OSError, subprocess.CalledProcessError) as exc:
            log.warning("RHEL install failed (needs sudo?): %s", exc)
            try:
                _run(["sudo", "cp", cert_path, dest_file])
                _run(["sudo", "update-ca-trust", "extract"])
                log.info("Certificate installed via sudo update-ca-trust.")
                installed = True
            except (subprocess.CalledProcessError, FileNotFoundError) as exc2:
                log.error("sudo RHEL install failed: %s", exc2)

    elif distro == "arch":
        dest_dir = "/etc/ca-certificates/trust-source/anchors"
        dest_file = os.path.join(dest_dir, f"{cert_name.replace(' ', '_')}.crt")
        try:
            os.makedirs(dest_dir, exist_ok=True)
            shutil.copy2(cert_path, dest_file)
            _run(["trust", "extract-compat"])
            log.info("Certificate installed via trust extract-compat.")
            installed = True
        except (OSError, subprocess.CalledProcessError) as exc:
            log.warning("Arch install failed (needs sudo?): %s", exc)
            try:
                _run(["sudo", "cp", cert_path, dest_file])
                _run(["sudo", "trust", "extract-compat"])
                log.info("Certificate installed via sudo trust extract-compat.")
                installed = True
            except (subprocess.CalledProcessError, FileNotFoundError) as exc2:
                log.error("sudo Arch install failed: %s", exc2)

    else:
        log.warning(
            "Unknown Linux distro. Manually install %s as a trusted root CA.", cert_path
        )

    return installed


def _is_trusted_linux(cert_path: str, cert_name: str = CERT_NAME) -> bool:
    """Check whether the cert appears in common Linux trust stores."""
    try:
        from cryptography import x509 as _x509
        from cryptography.hazmat.primitives import hashes as _hashes
    except Exception:
        return False

    try:
        with open(cert_path, "rb") as f:
            target_cert = _x509.load_pem_x509_certificate(f.read())
        target_fp = target_cert.fingerprint(_hashes.SHA1())
    except Exception:
        return False

    # First check the common anchor locations used by the installer.
    expected_name = f"{cert_name.replace(' ', '_')}.crt"
    anchor_dirs = [
        "/usr/local/share/ca-certificates",
        "/etc/pki/ca-trust/source/anchors",
        "/etc/ca-certificates/trust-source/anchors",
    ]
    for d in anchor_dirs:
        try:
            if not os.path.isdir(d):
                continue
            if expected_name in os.listdir(d):
                return True
        except OSError:
            pass

    # Fall back to scanning the system bundle files directly.
    bundle_paths = [
        "/etc/ssl/certs/ca-certificates.crt",   # Debian/Ubuntu
        "/etc/pki/tls/certs/ca-bundle.crt",     # RHEL/Fedora
        "/etc/ssl/ca-bundle.pem",               # OpenSUSE
        "/etc/ca-certificates/ca-certificates.crt",
    ]

    begin = b"-----BEGIN CERTIFICATE-----"
    end = b"-----END CERTIFICATE-----"
    for bundle in bundle_paths:
        try:
            with open(bundle, "rb") as f:
                data = f.read()
        except OSError:
            continue

        for chunk in data.split(begin):
            if end not in chunk:
                continue
            pem = begin + chunk.split(end, 1)[0] + end + b"\n"
            try:
                cert = _x509.load_pem_x509_certificate(pem)
            except Exception:
                continue
            try:
                if cert.fingerprint(_hashes.SHA1()) == target_fp:
                    return True
            except Exception:
                continue

    return False


# ─────────────────────────────────────────────────────────────────────────────
# Firefox NSS (cross-platform)
# ─────────────────────────────────────────────────────────────────────────────

def _install_firefox(cert_path: str, cert_name: str):
    """Install into all detected Firefox profile NSS databases."""
    if not _has_cmd("certutil"):
        log.debug("NSS certutil not found — skipping Firefox install.")
        return

    profile_dirs: list[str] = []
    system = platform.system()

    if system == "Windows":
        appdata = os.environ.get("APPDATA", "")
        profile_dirs += glob.glob(os.path.join(appdata, r"Mozilla\Firefox\Profiles\*"))
    elif system == "Darwin":
        profile_dirs += glob.glob(os.path.expanduser("~/Library/Application Support/Firefox/Profiles/*"))
    else:
        profile_dirs += glob.glob(os.path.expanduser("~/.mozilla/firefox/*.default*"))
        profile_dirs += glob.glob(os.path.expanduser("~/.mozilla/firefox/*.release*"))

    if not profile_dirs:
        log.debug("No Firefox profiles found.")
        return

    for profile in profile_dirs:
        db = f"sql:{profile}" if os.path.exists(os.path.join(profile, "cert9.db")) else f"dbm:{profile}"
        try:
            # Remove old entry first (ignore errors)
            _run(["certutil", "-D", "-n", cert_name, "-d", db], check=False)
            _run([
                "certutil", "-A",
                "-n", cert_name,
                "-t", "CT,,",
                "-i", cert_path,
                "-d", db,
            ])
            log.info("Installed in Firefox profile: %s", os.path.basename(profile))
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            log.warning("Firefox profile %s: %s", os.path.basename(profile), exc)


def _uninstall_firefox(cert_name: str):
    """Remove certificate from all detected Firefox profile NSS databases."""
    if not _has_cmd("certutil"):
        log.debug("NSS certutil not found — skipping Firefox uninstall.")
        return

    profile_dirs: list[str] = []
    system = platform.system()

    if system == "Windows":
        appdata = os.environ.get("APPDATA", "")
        profile_dirs += glob.glob(os.path.join(appdata, r"Mozilla\Firefox\Profiles\*"))
    elif system == "Darwin":
        profile_dirs += glob.glob(os.path.expanduser("~/Library/Application Support/Firefox/Profiles/*"))
    else:
        profile_dirs += glob.glob(os.path.expanduser("~/.mozilla/firefox/*.default*"))
        profile_dirs += glob.glob(os.path.expanduser("~/.mozilla/firefox/*.release*"))

    if not profile_dirs:
        log.debug("No Firefox profiles found.")
        return

    for profile in profile_dirs:
        db = f"sql:{profile}" if os.path.exists(os.path.join(profile, "cert9.db")) else f"dbm:{profile}"
        try:
            result = _run(["certutil", "-D", "-n", cert_name, "-d", db], check=False)
            if result.returncode == 0:
                log.info("Removed from Firefox profile: %s", os.path.basename(profile))
            else:
                log.debug("Firefox profile %s: certificate not present", os.path.basename(profile))
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            log.debug("Firefox profile %s: %s", os.path.basename(profile), exc)


# ─────────────────────────────────────────────────────────────────────────────
# Uninstall functions
# ─────────────────────────────────────────────────────────────────────────────

def _uninstall_windows(cert_path: str, cert_name: str) -> bool:
    """Remove certificate from the Windows Trusted Root store."""
    thumbprint = _cert_thumbprint(cert_path)

    # Try per-user store first (no admin required)
    try:
        target = thumbprint if thumbprint else cert_name
        _run(["certutil", "-delstore", "-user", "Root", target])
        log.info("Certificate removed from Windows user Trusted Root store.")
        return True
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        log.warning("certutil user store removal failed: %s", exc)

    # Try system store (requires admin)
    try:
        target = thumbprint if thumbprint else cert_name
        _run(["certutil", "-delstore", "Root", target])
        log.info("Certificate removed from Windows system Trusted Root store.")
        return True
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        log.warning("certutil system store removal failed: %s", exc)

    # Fallback: use PowerShell
    try:
        if thumbprint:
            ps_cmd = (
                "Get-ChildItem Cert:\\CurrentUser\\Root | "
                f"Where-Object {{ $_.Thumbprint -eq '{thumbprint}' }} | "
                "Remove-Item -Force -ErrorAction SilentlyContinue"
            )
        else:
            ps_cmd = (
                "Get-ChildItem Cert:\\CurrentUser\\Root | "
                f"Where-Object {{ $_.Subject -like '*CN={cert_name}*' -or $_.FriendlyName -eq '{cert_name}' }} | "
                "Remove-Item -Force -ErrorAction SilentlyContinue"
            )
        _run(["powershell", "-NoProfile", "-Command", ps_cmd])
        log.info("Certificate removal via PowerShell completed.")
        return True
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        log.error("PowerShell removal failed: %s", exc)

    return False


def _uninstall_macos(cert_name: str) -> bool:
    """Remove certificate from the macOS keychains."""
    login_keychain = os.path.expanduser("~/Library/Keychains/login.keychain-db")
    if not os.path.exists(login_keychain):
        login_keychain = os.path.expanduser("~/Library/Keychains/login.keychain")

    try:
        _run([
            "security", "delete-certificate",
            "-c", cert_name,
            login_keychain,
        ])
        log.info("Certificate removed from macOS login keychain.")
        return True
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        log.warning("login keychain removal failed: %s", exc)

    # Try system keychain (needs sudo)
    try:
        _run([
            "sudo", "security", "delete-certificate",
            "-c", cert_name,
            "/Library/Keychains/System.keychain",
        ])
        log.info("Certificate removed from macOS system keychain.")
        return True
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        log.debug("System keychain removal failed: %s", exc)

    return False


def _uninstall_linux(cert_path: str, cert_name: str) -> bool:
    """Remove certificate from Linux trust stores."""
    distro = _detect_linux_distro()
    log.info("Detected Linux distro family: %s", distro)

    removed = False

    if distro == "debian":
        dest_file = f"/usr/local/share/ca-certificates/{cert_name.replace(' ', '_')}.crt"
        try:
            if os.path.exists(dest_file):
                os.remove(dest_file)
            _run(["update-ca-certificates"])
            log.info("Certificate removed via update-ca-certificates.")
            removed = True
        except (OSError, subprocess.CalledProcessError) as exc:
            log.warning("Debian removal failed (needs sudo?): %s", exc)
            try:
                _run(["sudo", "rm", "-f", dest_file])
                _run(["sudo", "update-ca-certificates"])
                log.info("Certificate removed via sudo update-ca-certificates.")
                removed = True
            except (subprocess.CalledProcessError, FileNotFoundError) as exc2:
                log.warning("sudo Debian removal failed: %s", exc2)

    elif distro == "rhel":
        dest_file = f"/etc/pki/ca-trust/source/anchors/{cert_name.replace(' ', '_')}.crt"
        try:
            if os.path.exists(dest_file):
                os.remove(dest_file)
            _run(["update-ca-trust", "extract"])
            log.info("Certificate removed via update-ca-trust.")
            removed = True
        except (OSError, subprocess.CalledProcessError) as exc:
            log.warning("RHEL removal failed (needs sudo?): %s", exc)
            try:
                _run(["sudo", "rm", "-f", dest_file])
                _run(["sudo", "update-ca-trust", "extract"])
                log.info("Certificate removed via sudo update-ca-trust.")
                removed = True
            except (subprocess.CalledProcessError, FileNotFoundError) as exc2:
                log.warning("sudo RHEL removal failed: %s", exc2)

    elif distro == "arch":
        dest_file = f"/etc/ca-certificates/trust-source/anchors/{cert_name.replace(' ', '_')}.crt"
        try:
            if os.path.exists(dest_file):
                os.remove(dest_file)
            _run(["trust", "extract-compat"])
            log.info("Certificate removed via trust extract-compat.")
            removed = True
        except (OSError, subprocess.CalledProcessError) as exc:
            log.warning("Arch removal failed (needs sudo?): %s", exc)
            try:
                _run(["sudo", "rm", "-f", dest_file])
                _run(["sudo", "trust", "extract-compat"])
                log.info("Certificate removed via sudo trust extract-compat.")
                removed = True
            except (subprocess.CalledProcessError, FileNotFoundError) as exc2:
                log.warning("sudo Arch removal failed: %s", exc2)

    else:
        log.warning("Unknown Linux distro. Manually remove %s from trusted CAs.", cert_name)

    return removed


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def is_ca_trusted(cert_path: str) -> bool:
    """Return True if the CA cert appears to be already installed."""
    system = platform.system()
    try:
        if system == "Windows":
            return _is_trusted_windows(cert_path)
        if system == "Darwin":
            return _is_trusted_macos(CERT_NAME)
        return _is_trusted_linux(cert_path, CERT_NAME)
    except Exception:
        return False


def install_ca(cert_path: str, cert_name: str = CERT_NAME) -> bool:
    """
    Install *cert_path* as a trusted root CA on the current platform.
    Also attempts Firefox NSS installation.

    Returns True if the system store installation succeeded.
    """
    if not os.path.exists(cert_path):
        log.error("Certificate file not found: %s", cert_path)
        return False

    system = platform.system()
    log.info("Installing CA certificate on %s…", system)

    if system == "Windows":
        ok = _install_windows(cert_path, cert_name)
    elif system == "Darwin":
        ok = _install_macos(cert_path, cert_name)
    elif system == "Linux":
        ok = _install_linux(cert_path, cert_name)
    else:
        log.error("Unsupported platform: %s", system)
        return False

    # Best-effort Firefox install on all platforms
    _install_firefox(cert_path, cert_name)

    return ok


def uninstall_ca(cert_path: str, cert_name: str = CERT_NAME) -> bool:
    """
    Remove *cert_name* from the system's trusted root CAs on the current platform.
    Also attempts Firefox NSS removal.

    Returns True if the system store removal succeeded.
    """
    system = platform.system()
    log.info("Removing CA certificate from %s…", system)

    if system == "Windows":
        ok = _uninstall_windows(cert_path, cert_name)
    elif system == "Darwin":
        ok = _uninstall_macos(cert_name)
    elif system == "Linux":
        ok = _uninstall_linux(cert_path, cert_name)
    else:
        log.error("Unsupported platform: %s", system)
        return False

    # Best-effort Firefox uninstall on all platforms
    _uninstall_firefox(cert_name)

    return ok
