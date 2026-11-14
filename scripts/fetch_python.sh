#!/bin/bash
# Fetch, verify, merge and prune python-build-standalone into one universal tree.
# Prints the path of the merged tree. Idempotent via a .complete stamp.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY_VERSION="3.12.16"
PBS_RELEASE="20261110"
STAMP_VALUE="$PY_VERSION+$PBS_RELEASE"
BASE_URL="https://github.com/astral-sh/python-build-standalone/releases/download/$PBS_RELEASE"
CACHE="$ROOT/build/python-cache"
OUT_PARENT="$ROOT/build/python-universal/$STAMP_VALUE"
OUT="$OUT_PARENT/python"
STAMP="$OUT_PARENT/.complete"

if [ -d "$OUT" ] && [ -f "$STAMP" ] && [ "$(cat "$STAMP")" = "$STAMP_VALUE" ]; then
    echo "$OUT"
    exit 0
fi

# Everything below logs to stderr; stdout carries only the final path.
exec 3>&1 1>&2

DL="$CACHE/downloads"
rm -rf "$DL" "$CACHE/aarch64" "$CACHE/x86_64" "$OUT_PARENT"
mkdir -p "$DL"

NAMES=(
    "cpython-$STAMP_VALUE-aarch64-apple-darwin-install_only.tar.gz"
    "cpython-$STAMP_VALUE-x86_64-apple-darwin-install_only.tar.gz"
)
for f in "${NAMES[@]}" SHA256SUMS; do
    curl -fsSL --retry 3 -o "$DL/$f" "$BASE_URL/$f"
done

# Verify before extracting anything: only the lines for our two archives, and both must be present.
(
    cd "$DL"
    : > expected.sha256
    for f in "${NAMES[@]}"; do
        line="$(awk -v n="$f" '$2 == n || $2 == "*" n { print $1 "  " n; exit }' SHA256SUMS)"
        [ -n "$line" ] || { echo "no SHA256SUMS entry for $f" >&2; exit 1; }
        echo "$line" >> expected.sha256
    done
    shasum -a 256 -c expected.sha256
)

for pair in "aarch64:${NAMES[0]}" "x86_64:${NAMES[1]}"; do
    arch="${pair%%:*}"
    mkdir -p "$CACHE/$arch"
    tar -xzf "$DL/${pair#*:}" -C "$CACHE/$arch"
done

mkdir -p "$OUT_PARENT"
# Run our own script from scripts/, with an isolated interpreter.
/usr/bin/python3 -I "$ROOT/scripts/merge_universal.py" \
    "$CACHE/aarch64/python" "$CACHE/x86_64/python" "$OUT"

printf '%s\n' "$STAMP_VALUE" > "$STAMP"
echo "$OUT" >&3
