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
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/core/ui" "$APP/Contents/Resources/python/bin"

cp "$BIN_DIR/LabyrinthCleaner" "$BIN_DIR/labyrinth-trash" "$APP/Contents/MacOS/"

VERSION="$(tr -d '[:space:]' < VERSION)"
BUILD="$(git rev-list --count HEAD)"
sed -e "s/@VERSION@/$VERSION/" -e "s/@BUILD@/$BUILD/" app/Info.plist > "$APP/Contents/Info.plist"

cp devclean devclean_*.py "$APP/Contents/Resources/core/"
cp ui/index.html "$APP/Contents/Resources/core/ui/"
cp devclean-launcher "$APP/Contents/Resources/core/"
chmod 755 "$APP/Contents/Resources/core/devclean-launcher"

# Dev shim. Plan 3 replaces it with the embedded interpreter.
cat > "$APP/Contents/Resources/python/bin/python3" <<'SHIM'
#!/bin/sh
exec /usr/bin/python3 "$@"
SHIM
chmod 755 "$APP/Contents/Resources/python/bin/python3"

plutil -lint "$APP/Contents/Info.plist"
codesign --force --deep --sign - "$APP"

echo "$APP"
