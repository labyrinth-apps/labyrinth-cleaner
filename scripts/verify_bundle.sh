#!/bin/bash
# Verify a built app bundle. Usage: scripts/verify_bundle.sh APP [--signed] [--native]
#   --signed  every Mach-O must carry a Developer ID Application signature with the hardened runtime
#             and a secure timestamp
#   --native  skip the universal (x86_64 arm64) check and the Rosetta run (for --debug --sign builds)
# Exits non-zero on the first failure.
set -euo pipefail

APP_ARG="${1:?usage: verify_bundle.sh APP [--signed] [--native]}"
shift
SIGNED=0
NATIVE=0
for opt in "$@"; do
    case "$opt" in
        --signed) SIGNED=1 ;;
        --native) NATIVE=1 ;;
        *) echo "verify_bundle: unknown option: $opt" >&2; exit 2 ;;
    esac
done

fail() { echo "verify_bundle: FAIL: $*" >&2; exit 1; }
[ -d "$APP_ARG/Contents" ] || fail "not an app bundle: $APP_ARG"
APP="$(cd "$APP_ARG" && pwd -P)"   # absolute, symlinks resolved (/tmp -> /private/tmp)

count=0
while IFS= read -r -d '' f; do
    # Symlinks are skipped by -type f; the Mach-O test is on the file's own type.
    case "$(file -b "$f")" in *Mach-O*) ;; *) continue ;; esac
    count=$((count + 1))
    if [ "$NATIVE" = 0 ]; then
        archs="$(lipo -archs "$f" 2>/dev/null | tr ' ' '\n' | sort | tr '\n' ' ')"
        [ "$archs" = "arm64 x86_64 " ] || fail "not universal (x86_64 arm64): $f has '${archs% }'"
    fi
    if [ "$SIGNED" = 1 ]; then
        info="$(codesign -dvv "$f" 2>&1)" || fail "unsigned: $f"
        echo "$info" | grep -q '^Authority=Developer ID Application' || fail "no Developer ID Application authority: $f"
        echo "$info" | grep -Eq '^CodeDirectory .*flags=0x[0-9a-f]+\([^)]*runtime' || fail "no hardened runtime flag: $f"
        echo "$info" | grep -q '^Timestamp=' || fail "no secure timestamp (notarization needs one): $f"
    fi
done < <(find "$APP" -type f -print0)
[ "$count" -gt 0 ] || fail "no Mach-O files found in $APP"
summary="$count Mach-O files"
[ "$NATIVE" = 0 ] && summary="$summary are x86_64 arm64"
[ "$SIGNED" = 1 ] && summary="$summary, Developer ID signed with runtime"
echo "verify_bundle: $summary"

# The interpreter must be the real embedded one (a Mach-O inside the app), never the dev shim
# that execs /usr/bin/python3.
PYDIR="$APP/Contents/Resources/python"
PY="$PYDIR/bin/python3"
[ -x "$PY" ] || fail "missing $PY"
case "$(file -bL "$PY")" in *Mach-O*) ;; *) fail "$PY is not a Mach-O (dev shim?): $(file -bL "$PY")" ;; esac
PY_REAL="$(/usr/bin/readlink -f "$PY")"
case "$PY_REAL" in "$PYDIR/"*) ;; *) fail "$PY resolves outside the app: $PY_REAL" ;; esac

# -B: the check must not write __pycache__ into the bundle (that would break the signature).
# sys.executable and sys.prefix must both be inside the app's python directory.
CHECK='
import os, sys
import json, http.server, sqlite3, ssl, fcntl, plistlib
root = os.path.realpath(sys.argv[1])
print("sys.executable =", sys.executable)
print("sys.prefix =", sys.prefix)
for name, path in (("sys.executable", sys.executable), ("sys.prefix", sys.prefix)):
    if not (os.path.realpath(path) + "/").startswith(root + "/"):
        sys.exit("%s is outside the app: %s" % (name, path))
'
run_check() {   # $1 = label, rest = optional prefix command (arch -x86_64)
    local label="$1" out; shift
    out="$(cd / && "$@" "$PY" -E -s -B -X utf8 -c "$CHECK" "$PYDIR" 2>&1)" \
        || fail "embedded python import check ($label): $out"
    echo "$out" | sed "s/^/verify_bundle: ($label) /"
    echo "verify_bundle: embedded python imports OK ($label)"
}
run_check native
if [ "$NATIVE" = 1 ]; then
    echo "verify_bundle: --native, x86_64 run skipped"
elif arch -x86_64 /usr/bin/true 2>/dev/null; then
    run_check x86_64 arch -x86_64
else
    echo "verify_bundle: Rosetta unavailable, x86_64 run skipped"
fi

if find "$APP" -name __pycache__ -print -quit | grep -q .; then
    fail "__pycache__ inside the bundle: $(find "$APP" -name __pycache__ -print -quit)"
fi
