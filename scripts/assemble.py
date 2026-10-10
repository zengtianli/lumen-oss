"""Assemble a local source-build candidate. This does not install or publish."""
from pathlib import Path
import hashlib
import importlib.metadata
import json
import plistlib
import shutil
import subprocess
import sys
import sysconfig

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "build/Lumen.app"
if APP.is_symlink():
    raise ValueError("Refusing a linked bundle")
if APP.exists():
    shutil.rmtree(APP)
macos, resources = APP / "Contents/MacOS", APP / "Contents/Resources"
macos.mkdir(parents=True)
resources.mkdir()
shutil.copyfile(ROOT / "build/native/Lumen", macos / "Lumen")
(macos / "Lumen").chmod(0o755)
shutil.copytree(ROOT / "build/frozen/lumen-engine", resources / "engine", symlinks=True)
info = plistlib.loads((ROOT / "Resources/Info.plist").read_bytes())
info["CFBundleShortVersionString"] = (ROOT / "VERSION").read_text().strip()
info["CFBundleIconFile"] = "AppIcon"
(APP / "Contents/Info.plist").write_bytes(plistlib.dumps(info))
shutil.copyfile(ROOT / "icon/AppIcon.icns", resources / "AppIcon.icns")
shutil.copyfile(ROOT / "keys.json", resources / "keys.json")
for name in ("LICENSE", "THIRD-PARTY-NOTICES.md"):
    shutil.copyfile(ROOT / name, resources / name)
licenses = resources / "THIRD-PARTY-LICENSES"
licenses.mkdir()
inventory = []
for distribution in sorted(importlib.metadata.distributions(), key=lambda d: d.metadata["Name"].lower()):
    copied = {}
    for filename in sorted(distribution.files or [], key=str):
        relative = Path(str(filename))
        if relative.is_absolute() or ".." in relative.parts:
            continue
        if not relative.parts[0].endswith(".dist-info"):
            continue
        if not any(part.lower().startswith(("license", "copying", "notice")) for part in relative.parts):
            continue
        source = Path(distribution.locate_file(filename))
        if not source.is_file():
            continue
        target = licenses / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        copied[relative.as_posix()] = hashlib.sha256(target.read_bytes()).hexdigest()
    if not copied:
        raise ValueError("Dependency license missing: " + distribution.metadata["Name"])
    inventory.append({"name": distribution.metadata["Name"], "version": distribution.version,
                      "scope": "build environment (may include build-only dependencies)", "licenses": copied})
python_license = Path(sysconfig.get_path("stdlib")) / "LICENSE.txt"
if not python_license.is_file():
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
if not python_license.is_file():
    raise ValueError("CPython license missing")
shutil.copyfile(python_license, licenses / "CPython-LICENSE.txt")
inventory.append({"name": "CPython", "version": sys.version.split()[0], "scope": "bundled runtime",
                  "licenses": {"CPython-LICENSE.txt": hashlib.sha256(python_license.read_bytes()).hexdigest()}})
(resources / "DEPENDENCIES.json").write_text(json.dumps({"schema": 1, "dependencies": inventory}, indent=2) + "\n")
cli = resources / "lumen"
cli.write_text('''#!/bin/sh
set -eu
LUMEN_RESOURCES="$(cd "$(dirname "$0")" && pwd)"
case "${1:-}" in config|update|--help|-h|--version)
 exec "$LUMEN_RESOURCES/../MacOS/Lumen" "$@" ;;
*) exec "$LUMEN_RESOURCES/engine/lumen-engine" "$@" ;;
esac
''')
cli.chmod(0o755)
subprocess.run(["codesign", "--force", "--sign", "-", "--deep", str(APP)], check=True)
subprocess.run(["codesign", "--verify", "--deep", "--strict", str(APP)], check=True)
print(APP)
