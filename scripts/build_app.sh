#!/bin/bash
# Build "Labyrinth Cleaner.app" (and, for releases, the notarized DMG) into build/.
#
# Usage:
#   scripts/build_app.sh                    # release: universal build, Developer ID signing (hardened
#                                           #   runtime), notarize + staple app, DMG, sign + notarize +
#                                           #   staple DMG, Gatekeeper checks, .sha256
#   scripts/build_app.sh --no-notarize      # release build, signing and the signed DMG, no notary
#   scripts/build_app.sh --publish v0.1.0   # release build, then publish to the releases repo
#   scripts/build_app.sh --debug            # native debug build, ad-hoc signed, no DMG
#   scripts/build_app.sh --debug --sign     # native debug build, Developer ID signed inside-out with
#                                           #   the hardened runtime exactly like a release, no notary, no DMG
#
# Env: LABYRINTH_TEAM_ID (default X8BX4XM7S2)
#      LABYRINTH_NOTARY_PROFILE (default labyrinth-notary)
#      LABYRINTH_RELEASES_REPO (default labyrinth-apps/labyrinth-cleaner)
#      LABYRINTH_SKIP_BUILD=1 (with --publish: publish the DMG already in build/, don't build)
#
# One-time setup (see README "Build and release"): a local "Developer ID Application" certificate
# for the team in the login keychain, and the notary profile:
#   xcrun notarytool store-credentials labyrinth-notary --apple-id <apple-id> --team-id X8BX4XM7S2
set -euo pipefail

GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; NC='\033[0m'
step() { echo -e "${GREEN}==> $*${NC}" >&2; }
warn() { echo -e "${YELLOW}warning: $*${NC}" >&2; }
die()  { echo -e "${RED}error: $*${NC}" >&2; exit 1; }

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

DEBUG=0; SIGN_DEBUG=0; NOTARIZE=1; PUBLISH_TAG=""
while [ $# -gt 0 ]; do
    case "$1" in
        --debug) DEBUG=1 ;;
        --sign) SIGN_DEBUG=1 ;;
        --no-notarize) NOTARIZE=0 ;;
        --publish) shift; PUBLISH_TAG="${1:-}"; [ -n "$PUBLISH_TAG" ] || die "--publish needs a tag (e.g. v0.1.0)" ;;
        *) die "unknown option: $1 (usage: build_app.sh [--debug [--sign]] [--no-notarize] [--publish vX.Y.Z])" ;;
    esac
    shift
done
[ "$SIGN_DEBUG" = 0 ] || [ "$DEBUG" = 1 ] || die "--sign goes with --debug (release builds are always Developer ID signed)"
[ "$DEBUG" = 0 ] || NOTARIZE=0   # debug builds are never notarized
[ -z "$PUBLISH_TAG" ] || [ "$DEBUG" = 0 ] || die "--publish needs a release build (drop --debug)"
[ -z "$PUBLISH_TAG" ] || [ "$NOTARIZE" = 1 ] || die "--publish requires notarization (drop --no-notarize)"

TEAM_ID="${LABYRINTH_TEAM_ID:-X8BX4XM7S2}"
NOTARY_PROFILE="${LABYRINTH_NOTARY_PROFILE:-labyrinth-notary}"
DEFAULT_RELEASES_REPO="labyrinth-apps/labyrinth-cleaner"
RELEASES_REPO="${LABYRINTH_RELEASES_REPO:-$DEFAULT_RELEASES_REPO}"
SKIP_BUILD="${LABYRINTH_SKIP_BUILD:-0}"

VERSION="$(tr -d '[:space:]' < VERSION)"
BUILD_DIR="$ROOT/build"
APP="$BUILD_DIR/Labyrinth Cleaner.app"
DMG="$BUILD_DIR/Labyrinth-Cleaner-$VERSION-macOS.dmg"
VOLNAME="Labyrinth Cleaner"
ENTITLEMENTS="$ROOT/app/LabyrinthCleaner.entitlements"
SU_KEY_FILE="$ROOT/app/sparkle_public_key.txt"
FEED_URL="https://github.com/$RELEASES_REPO/releases/latest/download/appcast.xml"

# The Sparkle public key with all whitespace removed ("" when the file is missing).
SU_KEY=""
[ -f "$SU_KEY_FILE" ] && SU_KEY="$(tr -d '[:space:]' < "$SU_KEY_FILE")"
# An Ed25519 public key is 32 bytes: 44 base64 characters ending in one '='.
key_shape_ok() { [[ "$1" =~ ^[A-Za-z0-9+/]{43}=$ ]]; }

# Everything --publish needs is checked before building, so it never fails after a notarization.
if [ -n "$PUBLISH_TAG" ]; then
    [ -n "$SU_KEY" ] || die "--publish refuses to run without the Sparkle public key: app/sparkle_public_key.txt is missing or empty.
  Create the key once with Sparkle's generate_keys (from the SwiftPM artifact's bin/), then:
    generate_keys -p > app/sparkle_public_key.txt
  and run this again. A release without it could never update."
    key_shape_ok "$SU_KEY" \
        || die "app/sparkle_public_key.txt does not hold a Sparkle EdDSA public key (expected 44 base64 characters from 'generate_keys -p')"
    [[ "$PUBLISH_TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+([.-][0-9A-Za-z.-]+)?$ ]] || die "--publish tag must look like vX.Y.Z, got '$PUBLISH_TAG'"
    [ "$PUBLISH_TAG" = "v$VERSION" ] || die "--publish $PUBLISH_TAG does not match VERSION ($VERSION)"
    # The feed the app will be built with must be this repo's (a SKIP_BUILD publish checks the
    # feed inside the DMG instead, in publish()).
    if [ "$SKIP_BUILD" != 1 ]; then
        template_feed="$(plutil -extract SUFeedURL raw -o - "$ROOT/app/Info.plist" 2>/dev/null || true)"
        [ "$template_feed" = "$FEED_URL" ] || die "app/Info.plist's SUFeedURL ($template_feed) is not $FEED_URL.
  The app would look for updates somewhere other than where --publish uploads them."
    fi
    command -v gh >/dev/null || die "--publish needs the GitHub CLI (gh)"
    gh auth status >/dev/null 2>&1 || die "gh is not logged in: run 'gh auth login' first"
    # Sparkle and the README's download link fetch release assets without logging in.
    visibility="$(gh repo view "$RELEASES_REPO" --json visibility -q .visibility 2>&1)" \
        || die "cannot read $RELEASES_REPO with gh: $visibility"
    [ "$visibility" = PUBLIC ] || die "$RELEASES_REPO is $visibility, not PUBLIC. Updates and downloads need a public releases repo.
  Make it public on GitHub yourself (this script never changes visibility), then run this again."
fi
[ "$SKIP_BUILD" != 1 ] || [ -n "$PUBLISH_TAG" ] || die "LABYRINTH_SKIP_BUILD=1 only goes with --publish"

# Read-only DMG mounts; all are detached (and their mount points removed) on exit.
MOUNTS=()
TEMP_DIRS=()
cleanup() {
    local m
    for m in ${MOUNTS[@]+"${MOUNTS[@]}"}; do
        [ -d "$m" ] || continue
        hdiutil detach "$m" -quiet >/dev/null 2>&1 || hdiutil detach "$m" -force -quiet >/dev/null 2>&1 || true
        rmdir "$m" 2>/dev/null || true
    done
    for m in ${TEMP_DIRS[@]+"${TEMP_DIRS[@]}"}; do rm -rf "$m"; done
}
trap cleanup EXIT

# Attaches $1 read-only at a fresh mount point and sets MOUNTED to it.
attach_readonly() {
    MOUNTED="$(mktemp -d "${TMPDIR:-/tmp}/labyrinth-dmg.XXXXXX")"
    MOUNTS+=("$MOUNTED")
    local out
    out="$(hdiutil attach -readonly -nobrowse -noautoopen -mountpoint "$MOUNTED" "$1" 2>&1)" \
        || die "could not attach $1 read-only: $out"
    # A DMG already open in Finder can attach without showing up here.
    [ -d "$MOUNTED/$(basename "$APP")" ] || die "could not read the app inside $1 — eject it in Finder and try again"
}

# Signs with the Developer ID identity and the hardened runtime. Never --deep: callers sign inside-out.
sign() { codesign --force --sign "$DEVID_SHA" --options runtime --timestamp "$@"; }

# Submits $1, waits, fails with the notary log unless Accepted.
notarize() {
    local file="$1" json id status
    # notarytool may exit non-zero on an Invalid submission; the log is still wanted then.
    json="$(xcrun notarytool submit "$file" --keychain-profile "$NOTARY_PROFILE" --wait --output-format json)" || true
    id="$(plutil -extract id raw -o - - <<<"$json" 2>/dev/null)" || die "notarytool submit failed for $file: $json"
    status="$(plutil -extract status raw -o - - <<<"$json" 2>/dev/null || echo unknown)"
    echo "    submission $id: $status" >&2
    if [ "$status" != "Accepted" ]; then
        xcrun notarytool log "$id" --keychain-profile "$NOTARY_PROFILE" >&2 || true
        die "notarization failed for $file ($status)"
    fi
}

build_bundle() {
    local config arch_args bin_dir build su_key sparkle_fw py_cached py_tree
    if [ "$DEBUG" = 1 ]; then config=debug; arch_args=(); else config=release; arch_args=(--arch arm64 --arch x86_64); fi
    step "swift build ($config)"
    (cd app && swift build -c "$config" ${arch_args[@]+"${arch_args[@]}"})
    bin_dir="$(cd app && swift build -c "$config" ${arch_args[@]+"${arch_args[@]}"} --show-bin-path)"

    rm -rf "$APP"
    mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/core/ui"
    cp "$bin_dir/LabyrinthCleaner" "$bin_dir/labyrinth-trash" "$APP/Contents/MacOS/"

    build="$("$ROOT/scripts/build_number.sh" "$VERSION")"
    su_key="$SU_KEY"
    [ -n "$su_key" ] || [ -z "$PUBLISH_TAG" ] || die "--publish needs the Sparkle public key in app/sparkle_public_key.txt"
    if [ -n "$su_key" ] && ! key_shape_ok "$su_key"; then
        warn "!!! app/sparkle_public_key.txt is not a Sparkle EdDSA public key (expected 44 base64 characters from 'generate_keys -p'). SUPublicEDKey is left out, so UPDATES ARE OFF in this build. !!!"
        su_key=""
    fi
    sed -e "s/@VERSION@/$VERSION/" -e "s/@BUILD@/$build/" -e "s|@SU_PUBLIC_KEY@|$su_key|" app/Info.plist > "$APP/Contents/Info.plist"
    if [ -z "$su_key" ]; then
        /usr/libexec/PlistBuddy -c "Delete :SUPublicEDKey" "$APP/Contents/Info.plist"
        warn "No Sparkle key: updates are disabled in this build (put the public key in app/sparkle_public_key.txt)"
    fi

    # Sparkle.framework from the SwiftPM build products (ditto keeps the symlinks and XPC services).
    sparkle_fw="$bin_dir/Sparkle.framework"
    [ -d "$sparkle_fw" ] || sparkle_fw="$(find "$bin_dir/.." -maxdepth 3 -name Sparkle.framework -type d -print -quit)"
    [ -d "$sparkle_fw" ] || die "Sparkle.framework not found near $bin_dir"
    mkdir -p "$APP/Contents/Frameworks"
    ditto "$sparkle_fw" "$APP/Contents/Frameworks/Sparkle.framework"

    cp app/AppIcon.icns "$APP/Contents/Resources/"   # rendered by scripts/make_art.swift
    cp devclean devclean_*.py "$APP/Contents/Resources/core/"
    cp ui/index.html "$APP/Contents/Resources/core/ui/"
    cp devclean-launcher "$APP/Contents/Resources/core/"
    chmod 755 "$APP/Contents/Resources/core/devclean-launcher"

    # Embedded interpreter. Release and --debug --sign builds require the real universal Python
    # (fetched and verified by fetch_python.sh); plain --debug uses it when cached, else a shim.
    py_cached="$BUILD_DIR/python-universal/3.12.16+20261110"
    if [ "$DEBUG" = 0 ] || [ "$SIGN_DEBUG" = 1 ] || { [ -f "$py_cached/.complete" ] && [ -d "$py_cached/python" ]; }; then
        py_tree="$("$ROOT/scripts/fetch_python.sh")"
        ditto "$py_tree" "$APP/Contents/Resources/python"
    else
        warn "no cached embedded Python; using the dev shim (run scripts/fetch_python.sh to embed it)"
        mkdir -p "$APP/Contents/Resources/python/bin"
        cat > "$APP/Contents/Resources/python/bin/python3" <<'SHIM'
#!/bin/sh
exec /usr/bin/python3 "$@"
SHIM
        chmod 755 "$APP/Contents/Resources/python/bin/python3"
    fi
    # Nothing may write into the bundle; a stray __pycache__ would break the signature.
    find "$APP" -name __pycache__ -prune -exec rm -rf {} +

    plutil -lint "$APP/Contents/Info.plist" >&2
}

is_macho() { case "$(file -b "$1")" in *Mach-O*) return 0 ;; *) return 1 ;; esac; }

# Inside-out Developer ID signing, hardened runtime on everything. No --deep.
sign_inside_out() {
    local py="$APP/Contents/Resources/python" fw="$APP/Contents/Frameworks/Sparkle.framework" v f xpc
    step "Signing inside-out with $DEVID_SHA (Developer ID, hardened runtime)"
    # 1. Python: libraries and extension modules first, then the interpreter (bin/ last).
    #    -type f skips the bin/python3 symlink.
    while IFS= read -r -d '' f; do
        if is_macho "$f"; then sign "$f"; fi
    done < <(find "$py" -type f -not -path "$py/bin/*" -print0)
    while IFS= read -r -d '' f; do
        if is_macho "$f"; then sign "$f"; fi
    done < <(find "$py/bin" -type f -print0)
    # 2. Sparkle, per its "Sandboxing/Code signing" docs: XPC services, Autoupdate, Updater.app,
    #    then the framework. Only Downloader.xpc keeps its entitlements; Autoupdate's ad-hoc
    #    signature carries an application-identifier that a Developer ID signature must not.
    v="$fw/Versions/B"
    [ -d "$v" ] || die "unexpected Sparkle layout: no $v"
    for xpc in "$v"/XPCServices/*.xpc; do
        if [ "$(basename "$xpc")" = Downloader.xpc ]; then
            sign --preserve-metadata=entitlements "$xpc"
        else
            sign "$xpc"
        fi
    done
    sign "$v/Autoupdate"
    sign "$v/Updater.app"
    sign "$fw"
    # 3. The trash helper. 4. The app, with its entitlements.
    sign "$APP/Contents/MacOS/labyrinth-trash"
    sign --entitlements "$ENTITLEMENTS" "$APP"
}

verify_signed_app() {
    local native=()
    [ "$DEBUG" = 1 ] && native=(--native)
    step "Verifying the signature"
    codesign --verify --deep --strict --verbose=2 "$APP"
    "$ROOT/scripts/verify_bundle.sh" "$APP" --signed ${native[@]+"${native[@]}"}
    # verify_bundle ran the bundled Python; make sure that left the seal intact.
    codesign --verify --deep --strict "$APP"
}

make_dmg() {
    local venv="$BUILD_DIR/.dmgbuild-venv" py311 log="$BUILD_DIR/dmgbuild.log" i
    step "Building the DMG"
    # dmgbuild writes the Finder layout (.DS_Store) directly: no Finder or AppleScript needed.
    if [ ! -x "$venv/bin/dmgbuild" ]; then
        py311="$(command -v python3.11 || echo /opt/homebrew/opt/python@3.11/bin/python3.11)"
        [ -x "$py311" ] || die "dmgbuild needs Homebrew python3.11 (brew install python@3.11)"
        rm -rf "$venv"
        "$py311" -m venv "$venv" && "$venv/bin/pip" install -q --disable-pip-version-check "dmgbuild==1.6.*" || die "could not install dmgbuild"
    fi
    rm -f "$DMG"
    for i in 1 2 3; do
        if "$venv/bin/dmgbuild" -s "$ROOT/app/dmg/dmg_settings.py" \
            -D app="$APP" -D bg="$ROOT/app/dmg/background.tiff" "$VOLNAME" "$DMG" >"$log" 2>&1; then
            break
        fi
        # hdiutil (used by dmgbuild) can flake with "Resource busy"; anything else fails at once.
        if [ "$i" = 3 ] || ! grep -q "Resource busy" "$log"; then
            tail -20 "$log" >&2
            die "dmgbuild failed (log: $log)"
        fi
        warn "dmgbuild: Resource busy, retrying ($i/3)"
        sleep 5
    done
    [ -f "$DMG" ] || die "DMG not created"
    step "Signing the DMG"
    codesign --force --sign "$DEVID_SHA" --timestamp "$DMG"
}

# $1 = spctl args..., the last one the path. Requires a notarized Developer ID assessment.
gatekeeper_accepts() {
    local out
    out="$(spctl -a -vv "$@" 2>&1)" || die "Gatekeeper rejected ${*: -1}:
$out"
    echo "$out" >&2
    grep -q "source=Notarized Developer ID" <<<"$out" || die "Gatekeeper did not see a notarized Developer ID: $out"
}

gatekeeper_checks() {
    local q qapp qdmg flag
    step "Gatekeeper checks"
    gatekeeper_accepts -t exec "$APP"
    xcrun stapler validate "$APP"
    gatekeeper_accepts -t open --context context:primary-signature "$DMG"
    xcrun stapler validate "$DMG"
    # A downloaded copy carries the quarantine flag; that is what a user's first open sees.
    q="$(mktemp -d "$BUILD_DIR/.gatekeeper.XXXXXX")"
    TEMP_DIRS+=("$q")
    flag="0081;$(printf '%x' "$(date +%s)");Safari;"
    qdmg="$q/$(basename "$DMG")"
    qapp="$q/$(basename "$APP")"
    cp "$DMG" "$qdmg"
    ditto "$APP" "$qapp"
    xattr -w com.apple.quarantine "$flag" "$qdmg"
    xattr -w com.apple.quarantine "$flag" "$qapp"
    gatekeeper_accepts -t open --context context:primary-signature "$qdmg"
    gatekeeper_accepts -t exec "$qapp"
    # The app exactly as shipped: inside the quarantined DMG, with the Finder flags dmgbuild sets.
    attach_readonly "$qdmg"
    gatekeeper_accepts -t exec "$MOUNTED/$(basename "$APP")"
    hdiutil detach "$MOUNTED" -quiet >/dev/null || true   # the EXIT trap force-detaches if this fails
    rm -rf "$q"
}

write_checksum() {
    (cd "$(dirname "$DMG")" && shasum -a 256 "$(basename "$DMG")") > "$DMG.sha256"
    echo "    sha256 $(cut -d' ' -f1 "$DMG.sha256")" >&2
}

publish() {
    local tag="$1" stage="$BUILD_DIR/appcast/$1" prefix gen notes dmg_name
    prefix="https://github.com/$RELEASES_REPO/releases/download/$tag/"
    dmg_name="$(basename "$DMG")"
    step "Publishing $dmg_name to $RELEASES_REPO@$tag"
    xcrun stapler validate "$DMG" || die "$DMG has no stapled notarization ticket"

    # The app inside the DMG must carry this exact Sparkle key, or the release could never update
    # (a DMG built before the key was added would otherwise slip through LABYRINTH_SKIP_BUILD=1).
    local shipped_key shipped_feed info
    attach_readonly "$DMG"
    info="$MOUNTED/$(basename "$APP")/Contents/Info.plist"
    shipped_key="$(plutil -extract SUPublicEDKey raw -o - "$info" 2>/dev/null || true)"
    shipped_feed="$(plutil -extract SUFeedURL raw -o - "$info" 2>/dev/null || true)"
    hdiutil detach "$MOUNTED" -quiet >/dev/null || true
    [ "$shipped_feed" = "$FEED_URL" ] || die "the app in $DMG looks for updates at '$shipped_feed', not $FEED_URL: rebuild it with the matching SUFeedURL"
    [ -n "$shipped_key" ] || die "the app in $DMG has no SUPublicEDKey (built without app/sparkle_public_key.txt): rebuild it"
    [ "$shipped_key" = "$SU_KEY" ] || die "the app in $DMG has a different SUPublicEDKey than app/sparkle_public_key.txt: rebuild it"

    # The appcast is generated from a copy of this version's DMG (Sparkle reads the version
    # from the app inside it) and signed with the EdDSA key in the login keychain.
    # Decision 3: the tool comes from the SwiftPM Sparkle artifact; PATH is only a fallback.
    gen="$ROOT/app/.build/artifacts/sparkle/Sparkle/bin/generate_appcast"
    [ -x "$gen" ] || gen="$(command -v generate_appcast || true)"
    [ -n "$gen" ] && [ -x "$gen" ] || die "Sparkle's generate_appcast not found (run 'cd app && swift build' to fetch the Sparkle artifact)"
    rm -rf "$stage"
    mkdir -p "$stage"
    cp "$DMG" "$stage/"
    "$gen" --download-url-prefix "$prefix" "$stage"
    [ -f "$stage/appcast.xml" ] || die "generate_appcast wrote no appcast.xml"
    grep -qF "$prefix$dmg_name" "$stage/appcast.xml" || die "appcast.xml does not point at $prefix$dmg_name"
    grep -q "sparkle:edSignature" "$stage/appcast.xml" \
        || die "appcast.xml is not EdDSA-signed (is the Sparkle private key in the login keychain?)"

    local assets=("$DMG" "$DMG.sha256" "$stage/appcast.xml")
    notes="Download \`$dmg_name\`, open it and drag **Labyrinth Cleaner** into **Applications**. Signed and notarized by Apple.

Requires macOS 13 or later, Apple Silicon or Intel. Verify the download with \`shasum -a 256 -c $dmg_name.sha256\`."
    # Never creates the repository and never changes its visibility.
    if gh release view "$tag" -R "$RELEASES_REPO" >/dev/null 2>&1; then
        gh release upload "$tag" "${assets[@]}" -R "$RELEASES_REPO" --clobber
    else
        # gh tags the remote default branch's HEAD unless told otherwise; pin the built commit
        # when the remote has it.
        local target=() head origin_repo
        head="$(git rev-parse HEAD 2>/dev/null || true)"
        origin_repo="$(git remote get-url origin 2>/dev/null | sed -E 's#^(https://github\.com/|git@github\.com:|ssh://git@github\.com/)##; s#\.git$##; s#/$##' || true)"
        if [ "$origin_repo" != "$RELEASES_REPO" ]; then
            warn "$RELEASES_REPO is not this checkout's origin (${origin_repo:-none}): not pinning --target; $tag will tag that repo's default branch HEAD."
        elif [ -n "$head" ] && [ -n "$(git branch -r --contains HEAD 2>/dev/null)" ]; then
            target=(--target "$head")
        else
            warn "HEAD (${head:-no git}) is not on the remote: $tag will tag the default branch's HEAD instead. Push first to tag the built commit."
        fi
        gh release create "$tag" "${assets[@]}" -R "$RELEASES_REPO" ${target[@]+"${target[@]}"} \
            --title "Labyrinth Cleaner $VERSION" --notes "$notes"
    fi
    step "Published $RELEASES_REPO@$tag"
}

if [ "$SKIP_BUILD" = 1 ]; then
    warn "LABYRINTH_SKIP_BUILD=1: publishing the existing $DMG without building"
    [ -f "$DMG" ] || die "no DMG at $DMG"
    write_checksum
else
    DEVID_SHA=""
    if [ "$DEBUG" = 0 ] || [ "$SIGN_DEBUG" = 1 ]; then
        # The local Developer ID identity, by SHA-1 (unambiguous even with several certificates).
        DEVID_SHA="$(security find-identity -v -p codesigning \
            | grep "Developer ID Application" | grep "($TEAM_ID)" | head -1 | awk '{print $2}' || true)"
        [ -n "$DEVID_SHA" ] || die "no 'Developer ID Application' certificate for team $TEAM_ID in the keychain (see README: Build and release)"
    fi
    if [ "$NOTARIZE" = 1 ]; then
        xcrun notarytool history --keychain-profile "$NOTARY_PROFILE" >/dev/null 2>&1 \
            || die "no notary profile '$NOTARY_PROFILE'. Run (in a terminal):
  xcrun notarytool store-credentials $NOTARY_PROFILE --apple-id <apple-id> --team-id $TEAM_ID"
    fi

    build_bundle

    if [ "$DEBUG" = 1 ] && [ "$SIGN_DEBUG" = 0 ]; then
        codesign --force --deep --sign - "$APP"
        echo "note: --debug build is ad-hoc signed and native-only; skipping verify_bundle.sh" >&2
        echo "$APP"
        exit 0
    fi

    sign_inside_out
    verify_signed_app
    if [ "$DEBUG" = 1 ]; then
        echo "note: --debug --sign build: Developer ID signed, not notarized, no DMG" >&2
        echo "$APP"
        exit 0
    fi

    if [ "$NOTARIZE" = 1 ]; then
        step "Notarizing the app"
        ZIP="$BUILD_DIR/Labyrinth-Cleaner-notarize.zip"
        rm -f "$ZIP"
        ditto -c -k --keepParent "$APP" "$ZIP"
        notarize "$ZIP"
        rm -f "$ZIP"
        xcrun stapler staple "$APP"
    fi

    make_dmg

    if [ "$NOTARIZE" = 1 ]; then
        step "Notarizing the DMG"
        notarize "$DMG"
        xcrun stapler staple "$DMG"
        gatekeeper_checks
        write_checksum
    else
        rm -f "$DMG.sha256"
        warn "--no-notarize: the app and DMG are signed but not notarized; Gatekeeper will reject them, and no .sha256 is written"
    fi
    step "Done: $DMG ($(du -h "$DMG" | awk '{print $1}'))"
fi

[ -z "$PUBLISH_TAG" ] || publish "$PUBLISH_TAG"
echo "$DMG"
