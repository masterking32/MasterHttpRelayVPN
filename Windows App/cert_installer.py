import glob
import logging
import os
import platform
import shutil
import subprocess
import sys
import tempfile

log = logging.getLogger("CertInstaller")
CERT_NAME = "MasterHttpRelayVPN"

def _run(cmd: list[str], *, check: bool = True, capture: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        check=check,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )

def _has_cmd(name: str) -> bool:
    return shutil.which(name) is not None

def _install_windows(cert_path: str, cert_name: str) -> bool:
    try:
        _run(["certutil", "-addstore", "-user", "Root", cert_path])
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    try:
        _run(["certutil", "-addstore", "Root", cert_path])
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    try:
        ps_cmd = f"Import-Certificate -FilePath '{cert_path}' -CertStoreLocation Cert:\\CurrentUser\\Root"
        _run(["powershell", "-NoProfile", "-Command", ps_cmd])
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    return False

def _is_trusted_windows(cert_path: str) -> bool:
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
    try:
        from cryptography import x509 as _x509
        from cryptography.hazmat.primitives import hashes as _hashes
        with open(cert_path, "rb") as f:
            cert = _x509.load_pem_x509_certificate(f.read())
        return cert.fingerprint(_hashes.SHA1()).hex().upper()
    except Exception:
        return ""

def _install_macos(cert_path: str, cert_name: str) -> bool:
    login_keychain = os.path.expanduser("~/Library/Keychains/login.keychain-db")
    if not os.path.exists(login_keychain):
        login_keychain = os.path.expanduser("~/Library/Keychains/login.keychain")
    try:
        _run(["security", "add-trusted-cert", "-d", "-r", "trustRoot", "-k", login_keychain, cert_path])
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    try:
        _run(["sudo", "security", "add-trusted-cert", "-d", "-r", "trustRoot", "-k", "/Library/Keychains/System.keychain", cert_path])
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    return False

def _is_trusted_macos(cert_name: str) -> bool:
    try:
        result = _run(["security", "find-certificate", "-a", "-c", cert_name])
        return bool(result.stdout.strip())
    except Exception:
        return False

def _detect_linux_distro() -> str:
    if os.path.exists("/etc/debian_version") or os.path.exists("/etc/ubuntu"):
        return "debian"
    if os.path.exists("/etc/redhat-release") or os.path.exists("/etc/fedora-release"):
        return "rhel"
    if os.path.exists("/etc/arch-release"):
        return "arch"
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
    installed = False
    if distro == "debian":
        dest_dir = "/usr/local/share/ca-certificates"
        dest_file = os.path.join(dest_dir, f"{cert_name.replace(' ', '_')}.crt")
        try:
            os.makedirs(dest_dir, exist_ok=True)
            shutil.copy2(cert_path, dest_file)
            _run(["update-ca-certificates"])
            installed = True
        except (OSError, subprocess.CalledProcessError):
            try:
                _run(["sudo", "cp", cert_path, dest_file])
                _run(["sudo", "update-ca-certificates"])
                installed = True
            except (subprocess.CalledProcessError, FileNotFoundError):
                pass
    elif distro == "rhel":
        dest_dir = "/etc/pki/ca-trust/source/anchors"
        dest_file = os.path.join(dest_dir, f"{cert_name.replace(' ', '_')}.crt")
        try:
            os.makedirs(dest_dir, exist_ok=True)
            shutil.copy2(cert_path, dest_file)
            _run(["update-ca-trust", "extract"])
            installed = True
        except (OSError, subprocess.CalledProcessError):
            try:
                _run(["sudo", "cp", cert_path, dest_file])
                _run(["sudo", "update-ca-trust", "extract"])
                installed = True
            except (subprocess.CalledProcessError, FileNotFoundError):
                pass
    elif distro == "arch":
        dest_dir = "/etc/ca-certificates/trust-source/anchors"
        dest_file = os.path.join(dest_dir, f"{cert_name.replace(' ', '_')}.crt")
        try:
            os.makedirs(dest_dir, exist_ok=True)
            shutil.copy2(cert_path, dest_file)
            _run(["trust", "extract-compat"])
            installed = True
        except (OSError, subprocess.CalledProcessError):
            try:
                _run(["sudo", "cp", cert_path, dest_file])
                _run(["sudo", "trust", "extract-compat"])
                installed = True
            except (subprocess.CalledProcessError, FileNotFoundError):
                pass
    return installed

def _is_trusted_linux(cert_path: str) -> bool:
    thumbprint = _cert_thumbprint(cert_path)
    if not thumbprint:
        return False
    anchor_dirs = [
        "/usr/local/share/ca-certificates",
        "/etc/pki/ca-trust/source/anchors",
        "/etc/ca-certificates/trust-source/anchors",
    ]
    for d in anchor_dirs:
        if os.path.isdir(d):
            for f in os.listdir(d):
                if "DomainFront" in f or "domainfront" in f.lower():
                    return True
    return False

def _install_firefox(cert_path: str, cert_name: str):
    if not _has_cmd("certutil"):
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
        return
    for profile in profile_dirs:
        db = f"sql:{profile}" if os.path.exists(os.path.join(profile, "cert9.db")) else f"dbm:{profile}"
        try:
            _run(["certutil", "-D", "-n", cert_name, "-d", db], check=False)
            _run(["certutil", "-A", "-n", cert_name, "-t", "CT,,", "-i", cert_path, "-d", db])
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass

def is_ca_trusted(cert_path: str) -> bool:
    system = platform.system()
    try:
        if system == "Windows":
            return _is_trusted_windows(cert_path)
        if system == "Darwin":
            return _is_trusted_macos(CERT_NAME)
        return _is_trusted_linux(cert_path)
    except Exception:
        return False

def install_ca(cert_path: str, cert_name: str = CERT_NAME) -> bool:
    if not os.path.exists(cert_path):
        return False
    system = platform.system()
    if system == "Windows":
        ok = _install_windows(cert_path, cert_name)
    elif system == "Darwin":
        ok = _install_macos(cert_path, cert_name)
    elif system == "Linux":
        ok = _install_linux(cert_path, cert_name)
    else:
        return False
    _install_firefox(cert_path, cert_name)
    return ok
