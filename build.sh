#!/bin/bash
set -euo pipefail
LUMEN_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$LUMEN_ROOT"
LUMEN_PYTHON="${LUMEN_BUILD_PYTHON:-$LUMEN_ROOT/.venv/bin/python}"
if [ ! -x "$LUMEN_PYTHON" ]; then
  echo 'Create .venv and install requirements.txt first (see README).' >&2; exit 1
fi
mkdir -p build/native
xcrun swiftc -j "${LUMEN_BUILD_JOBS:-$(sysctl -n hw.logicalcpu)}" -O -swift-version 5 -parse-as-library \
  -target "$(uname -m)-apple-macos14.0" -sdk "$(xcrun --sdk macosx --show-sdk-path)" \
  -file-prefix-map "$LUMEN_ROOT"=. Sources/*.swift Sources/Shared/*.swift -o build/native/Lumen
if [ "${1:-}" = --native-only ]; then exit 0; fi
"$LUMEN_PYTHON" -m PyInstaller --noconfirm --clean --onedir --name lumen-engine --paths engine \
  --additional-hooks-dir scripts/hooks \
  --hidden-import task_runtime --hidden-import llm_client --hidden-import deliverables_index \
  --hidden-import docx_extract --hidden-import pdf_cli \
  --add-data "$LUMEN_ROOT/engine/lumen/assets:lumen/assets" \
  --distpath build/frozen --workpath build/pyinstaller --specpath build engine/entry.py
"$LUMEN_PYTHON" scripts/assemble.py
echo "$LUMEN_ROOT/build/Lumen.app"
