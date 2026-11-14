#!/bin/bash
# Verify a built app bundle. Usage: scripts/verify_bundle.sh APP [--signed]
# Exits non-zero on the first failure.
set -euo pipefail

APP="${1:?usage: verify_bundle.sh APP [--signed]}"
SIGNED=0
[ "${2:-}" = "--signed" ] && SIGNED=1

fail() { echo "verify_bundle: FAIL: $*" >&2; exit 1; }
[ -d "$APP/Contents" ] || fail "not an app bundle: $APP"

count=0
while IFS= read -r -d '' f; do
    # Symlinks are skipped by -type f; the Mach-O test is on the file's own type.
    case "$(file -b "$f")" in *Mach-O*) ;; *) continue ;; esac
    count=$((count + 1))
    archs="$(lipo -archs "$f" 2>/dev/null | tr ' ' '\n' | sort | tr '\n' ' ')"
    [ "$archs" = "arm64 x86_64 " ] || fail "not universal (x86_64 arm64): $f has '${archs% }'"
    if [ "$SIGNED" = 1 ]; then
        info="$(codesign -dvv "$f" 2>&1)" || fail "unsigned: $f"
        echo "$info" | grep -q '^Authority=Developer ID Application' || fail "no Developer ID Application authority: $f"
        echo "$info" | grep -Eq '^CodeDirectory .*flags=0x[0-9a-f]+\([^)]*runtime' || fail "no hardened runtime flag: $f"
    fi
done < <(find "$APP" -type f -print0)
[ "$count" -gt 0 ] || fail "no Mach-O files found in $APP"
echo "verify_bundle: $count Mach-O files are x86_64 arm64$([ "$SIGNED" = 1 ] && echo ', Developer ID signed with runtime')"

PY="$APP/Contents/Resources/python/bin/python3"
[ -x "$PY" ] || fail "missing $PY"
IMPORTS="import json, http.server, sqlite3, ssl, fcntl, plistlib"
(cd / && "$PY" -E -s -X utf8 -c "$IMPORTS") || fail "embedded python import check (native)"
echo "verify_bundle: embedded python imports OK (native)"
if arch -x86_64 /usr/bin/true 2>/dev/null; then
    (cd / && arch -x86_64 "$PY" -E -s -X utf8 -c "$IMPORTS") || fail "embedded python import check (x86_64)"
    echo "verify_bundle: embedded python imports OK (x86_64)"
else
    echo "verify_bundle: Rosetta unavailable, x86_64 run skipped"
fi
