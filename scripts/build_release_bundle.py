#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import os
import re
import shutil
import tarfile
import zipfile
from pathlib import Path


def _read_version(root: Path) -> str:
    constants_py = (root / "src" / "constants.py").read_text(encoding="utf-8")
    m = re.search(r'__version__\s*=\s*"([^"]+)"', constants_py)
    return m.group(1) if m else "0.0.0"


def main() -> int:
    root = Path(".").resolve()
    target = os.environ.get("TARGET", "")
    if not target:
        raise SystemExit("TARGET environment variable is required")

    version = _read_version(root)
    binary_name = "MasterHttpRelayVPN.exe" if os.name == "nt" else "MasterHttpRelayVPN"
    binary_path = root / "dist" / binary_name
    if not binary_path.exists():
        raise SystemExit(f"binary not found: {binary_path}")

    bundle_name = f"MasterHttpRelayVPN-{version}-{target}"
    bundle_root = root / "package" / bundle_name
    if bundle_root.exists():
        shutil.rmtree(bundle_root)
    bundle_root.mkdir(parents=True, exist_ok=True)
    config_example = root / "config.example.json"
    if not config_example.exists():
        raise SystemExit(f"missing config.example.json: {config_example}")
    shutil.copy2(config_example, bundle_root / "config.example.json")
    shutil.copy2(binary_path, bundle_root / binary_name)

    if os.name != "nt":
        (bundle_root / binary_name).chmod(0o755)

    release_dir = root / "release-assets"
    release_dir.mkdir(parents=True, exist_ok=True)

    if target.startswith("windows"):
        archive = release_dir / f"{bundle_name}.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for path in bundle_root.rglob("*"):
                zf.write(path, path.relative_to(bundle_root.parent))
    else:
        archive = release_dir / f"{bundle_name}.tar.gz"
        with tarfile.open(archive, "w:gz") as tf:
            tf.add(bundle_root, arcname=bundle_name)

    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (release_dir / f"{archive.name}.sha256").write_text(
        f"{digest}  {archive.name}\n",
        encoding="utf-8",
    )

    print(f"Created {archive}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
