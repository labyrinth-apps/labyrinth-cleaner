#!/bin/bash
# Build "Labyrinth Cleaner.app" into build/. Usage: scripts/build_app.sh [--debug]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [ "${1:-}" = "--debug" ]; then
    CONFIG=debug
    ARCH_ARGS=()
else
    CONFIG=release
    ARCH_ARGS=(--arch arm64 --arch x86_64)
fi

(cd app && swift build -c "$CONFIG" ${ARCH_ARGS[@]+"${ARCH_ARGS[@]}"})
BIN_DIR="$(cd app && swift build -c "$CONFIG" ${ARCH_ARGS[@]+"${ARCH_ARGS[@]}"} --show-bin-path)"

APP="$ROOT/build/Labyrinth Cleaner.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/core/ui"

cp "$BIN_DIR/LabyrinthCleaner" "$BIN_DIR/labyrinth-trash" "$APP/Contents/MacOS/"

VERSION="$(tr -d '[:space:]' < VERSION)"
BUILD="$(git rev-list --count HEAD)"
sed -e "s/@VERSION@/$VERSION/" -e "s/@BUILD@/$BUILD/" app/Info.plist > "$APP/Contents/Info.plist"

cp devclean devclean_*.py "$APP/Contents/Resources/core/"
cp ui/index.html "$APP/Contents/Resources/core/ui/"
cp devclean-launcher "$APP/Contents/Resources/core/"
chmod 755 "$APP/Contents/Resources/core/devclean-launcher"

# Embedded interpreter. Release builds require the real universal Python (fetched and
# verified by fetch_python.sh); --debug builds use it when already cached, else a shim.
PY_CACHED="$ROOT/build/python-universal/3.12.16+20261110"
if [ "$CONFIG" = release ] || { [ -f "$PY_CACHED/.complete" ] && [ -d "$PY_CACHED/python" ]; }; then
    PY_TREE="$("$ROOT/scripts/fetch_python.sh")"
    ditto "$PY_TREE" "$APP/Contents/Resources/python"
else
    echo "warning: no cached embedded Python; using the dev shim (run scripts/fetch_python.sh to embed it)" >&2
    mkdir -p "$APP/Contents/Resources/python/bin"
    cat > "$APP/Contents/Resources/python/bin/python3" <<'SHIM'
#!/bin/sh
exec /usr/bin/python3 "$@"
SHIM
    chmod 755 "$APP/Contents/Resources/python/bin/python3"
fi

plutil -lint "$APP/Contents/Info.plist"
codesign --force --deep --sign - "$APP"

if [ "$CONFIG" = release ]; then
    "$ROOT/scripts/verify_bundle.sh" "$APP"
else
    echo "note: --debug build is native-only; skipping verify_bundle.sh (it requires universal binaries)" >&2
fi

echo "$APP"
