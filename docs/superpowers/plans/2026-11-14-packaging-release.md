# Labyrinth Cleaner Packaging & Release Implementation Plan (plan 3 of 3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the dev bundle from plan 2 into a downloadable app.

**What it adds:**
- a universal embedded Python
- Sparkle updates
- an app icon and DMG art
- Developer ID signing with the hardened runtime
- notarization and stapling
- a DMG
- a `--publish` path to GitHub releases

**Architecture:**
- `scripts/build_app.sh` stays the single entry point. It fetches and caches a pinned python-build-standalone 3.12 for both architectures, merges the two into one universal tree, prunes it, and embeds it at `Contents/Resources/python`.
- It embeds `Sparkle.framework` from SwiftPM.
- It signs every Mach-O inside-out with Developer ID and the hardened runtime, notarizes and staples the app, builds a DMG with `dmgbuild`, then signs, notarizes and staples the DMG.
- The release flow is modelled on labyrinth-iptv's `scripts/build_macos_dmg.sh`.

**Tech Stack:** bash, python-build-standalone `3.12.16+20261110` install_only (aarch64 + x86_64), `lipo`, Sparkle 2.10.1 (SwiftPM), `codesign`/`notarytool`/`stapler`/`spctl`, `dmgbuild` 1.6.x in a venv on Homebrew python3.11, `iconutil`, and a Swift script for the art.

**Spec:** `docs/superpowers/specs/2026-11-13-labyrinth-cleaner-app-design.md` ("Install and distribution", "Constraints: Signing", "Tests: Build verification"). Plan 2 (`docs/superpowers/plans/2026-11-13-mac-app.md`) is done on branch `feat/mac-app`. This plan runs on the same branch.

## Global Constraints

- **Development safety is unchanged.** No real deletions. The built app is launched only with `--fixture-home <temp dir>` (debug builds) or not at all; release builds are verified with static checks.
  - Tests never call real `xcrun simctl`, `docker`, `osascript`, the Trash or `SMAppService`.
  - Nothing touches the real `~/.local/bin`, `/usr/local/bin`, login items or defaults.
- **Releases go to `labyrinth-apps/labyrinth-cleaner`** (env `LABYRINTH_RELEASES_REPO` overrides). Do not create repositories, do not publish a release, do not change repo visibility, do not push. `--publish` is implemented and tested only with a fake `gh` on PATH. The user publishes in the morning.
- **The Sparkle feed** is `https://github.com/labyrinth-apps/labyrinth-cleaner/releases/latest/download/appcast.xml`.
- **Signing:**
  - team `X8BX4XM7S2` (env `LABYRINTH_TEAM_ID`)
  - identity = the local "Developer ID Application … (X8BX4XM7S2)" certificate, found by SHA-1 as iptv does
  - notary profile `labyrinth-notary` (env `LABYRINTH_NOTARY_PROFILE`)
  - every executable Mach-O gets `--options runtime --timestamp`
  - no `--deep` signing; sign inside-out
  - submitting to Apple's notary service with the existing profile is allowed
- **No prompts.** If any command shows a keychain prompt or GUI dialog, or blocks waiting for a human, stop that step, record it, and leave it for the user. Never type passwords and never change keychain settings.
- **Python:**
  - Exactly `cpython-3.12.16+20261110-{aarch64,x86_64}-apple-darwin-install_only.tar.gz` from `astral-sh/python-build-standalone` release `20261110`.
  - Verify each download against the release's `SHA256SUMS` before extracting.
  - Downloads go to their own empty directory under `build/python-cache/`. Extract with `tar` into a fresh directory. Never execute anything from the archive before verifying it.
  - The app keeps running Python with `-E -s -X utf8`.
- **Bundle:**
  - `Contents/Frameworks/Sparkle.framework`
  - `Contents/Resources/python/` (universal)
  - `Contents/Resources/core/`
  - `Contents/Resources/AppIcon.icns`
  - `Contents/MacOS/{LabyrinthCleaner,labyrinth-trash}`
  - Minimum macOS 13.0; arm64 + x86_64 everywhere.
- **Entitlements** (`app/LabyrinthCleaner.entitlements`): `com.apple.security.automation.apple-events` = true, and nothing else unless a check proves it is needed. Python and `labyrinth-trash` get no entitlements.
- **Sparkle settings:**
  - `SUEnableAutomaticChecks` true
  - `SUScheduledCheckInterval` 86400
  - `SUAutomaticallyUpdate` false (it asks before installing)
  - `SUPublicEDKey` = the public key from Sparkle's `generate_keys`, whose private key lives in the login keychain. The user creates the key; nobody else ever runs `generate_keys`.
  - The updater is not started in fixture runs.
- **Suites:**
  - `/usr/bin/python3 -W error::ResourceWarning -m unittest discover -s tests`
  - `cd app && swift test`
  - Both must stay green. Task 1 adds a run of the Python suite under the embedded interpreter.
- **Commits** end with:
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01U65UcZjQqUz3mkd3jHk55q
  ```

## Decisions in this plan (builder's calls the spec left open)

1. **The embedded Python:**
   - python-build-standalone 3.12 install_only for both architectures, merged with `lipo` (every Mach-O; other files must be byte-identical or the arm64 copy wins, with a logged list of differences).
   - Pruned: `test/`, `idlelib/`, `tkinter/`, `turtledemo/`, `ensurepip/`, `lib2to3/`, `pydoc_data/`, `config-3.12-darwin/`, `include/`, `share/`, every `__pycache__`, `_tkinter*.so`, and `bin/` except `python3` and `python3.12`.
2. **Dev builds still work offline:**
   - `--debug` embeds the same cached Python when it exists, else falls back to the plan 2 shim with a warning.
   - Release builds require the real Python and fail without it.
3. **Sparkle:**
   - SwiftPM dependency `exact: "2.10.1"`.
   - The `generate_appcast` and `sign_update` tools come from the SwiftPM artifact's `bin/`.
   - The appcast and the DMG are published as release assets.
4. **The art:**
   - A Swift script renders the icon (brass labyrinth rings on ink, matching the page) and the DMG background, at 1x and 2x.
   - The generated files are committed, so builds don't need to render them.
5. **The DMG:**
   - Volume name "Labyrinth Cleaner"; file `Labyrinth-Cleaner-<version>-macOS.dmg`.
   - App on the left and Applications on the right, as iptv does.
6. **Quit feedback:** while Quit waits for the server to stop, the menu bar title reads "Quitting…" (from plan 2's final review).

## Review Focus

1. **The x86_64 slice of the embedded Python and the binaries is broken** (an Intel Mac or Rosetta can't launch it).
   - Task 1: `lipo -archs` on every Mach-O, plus `arch -x86_64 …/python3 -c 'import json'` when Rosetta is installed (skipped with a note otherwise).
2. **One unsigned or ad-hoc nested `.so` makes notarization fail, or the app gets killed at launch.**
   - Task 4: the verification loop over every Mach-O (Developer ID authority + runtime flag), and launching the signed debug-config app once on a fixture home.
3. **A fixture or debug run phones home** (Sparkle checking a feed).
   - Task 2: the updater is not created when fixture options are present. A unit-level check in `LaunchOptions`/AppDelegate wiring, and the README notes it.
4. **A downloaded, quarantined DMG warns at first open.**
   - Task 4: `xattr -w com.apple.quarantine` on a copy, then `spctl -a -vv -t open --context context:primary-signature` on the DMG and `spctl -a -vv -t exec` on the app.
5. **The app run straight from the DMG** (App Translocation, read-only volume, a path with spaces):
   - The server still starts, because paths come from `Bundle.main`, and nothing writes into the bundle.
   - Task 4 checks that the code never writes inside `Bundle.main` (a grep plus a review item).

---

### Task 1: Embedded universal Python

**Files:**
- Create: `scripts/fetch_python.sh`, `scripts/merge_universal.py`, `scripts/verify_bundle.sh`
- Modify: `scripts/build_app.sh`, `.gitignore` (`build/` is already ignored)
- Test: `tests/test_merge_universal.py`

**Interfaces:**
- `scripts/fetch_python.sh` prints the path of the merged universal tree, `build/python-universal/3.12.16+20261110/python`. It is idempotent: if the tree exists and its `.complete` stamp matches the version, it does nothing.
- `merge_universal.py ARM_DIR X86_DIR OUT_DIR` (stdlib only):
  - walks both trees
  - Mach-O files present in both (detected by magic bytes) → `lipo -create`
  - other files → copied from ARM_DIR, with differing ones listed on stderr
  - files present in only one tree → copied, and listed
  - symlinks are preserved as symlinks
  - then the Decision 1 pruning is applied
- `scripts/verify_bundle.sh APP [--signed]` exits non-zero on the first failure. It checks:
  - every Mach-O under the bundle is `x86_64 arm64`
  - with `--signed`: every Mach-O shows `Authority=Developer ID Application` and the runtime flag
  - `Contents/Resources/python/bin/python3 -E -s -X utf8 -c "import json, http.server, sqlite3, ssl, fcntl, plistlib"` succeeds
  - under `arch -x86_64` too, when `arch -x86_64 /usr/bin/true` works

- [ ] **Step 1: Write `tests/test_merge_universal.py`** on temp trees. Fake Mach-O files are small files starting with `\xcf\xfa\xed\xfe` (arm and x86 variants). `lipo` is injected as a fake runner that records argv.
  - `test_macho_in_both_is_lipoed`
  - `test_text_file_copied_from_arm_and_difference_reported`
  - `test_symlink_preserved`
  - `test_pruned_dirs_absent` (`test/`, `idlelib/`, `__pycache__`, `include/`)
  - `test_only_in_one_tree_copied_and_reported`
- [ ] **Step 2: Run them and check they fail.** Then implement `merge_universal.py` (taking a `lipo` callable for tests; `main()` uses the real `/usr/bin/lipo`) and run them until they pass.
- [ ] **Step 3: Implement `fetch_python.sh`.**
  1. Download both tarballs and `SHA256SUMS` with `curl -fL --retry 3` into `build/python-cache/downloads/`.
  2. `shasum -a 256 -c` against the matching lines.
  3. Extract each into `build/python-cache/<arch>/`.
  4. Run `merge_universal.py`.
  5. Write the `.complete` stamp.
- [ ] **Step 4: Wire it into `build_app.sh`.**
  - Release builds embed `$(scripts/fetch_python.sh)` at `Contents/Resources/python`.
  - `--debug` builds embed it when it is already cached, else use the shim with a warning.
  - Then run `scripts/verify_bundle.sh "$APP"`.
- [ ] **Step 5: Verify.**
  - `scripts/build_app.sh` succeeds and `verify_bundle.sh` passes.
  - Run the whole Python test suite with the embedded interpreter: `"build/Labyrinth Cleaner.app/Contents/Resources/python/bin/python3" -W error::ResourceWarning -m unittest discover -s tests`. Expected: the same count as `/usr/bin/python3`, all passing. If any test depends on system-Python specifics, fix the test, not the core.
  - `du -sh` of the python dir is recorded in the report (target under 80 MB universal).
- [ ] **Step 6: Commit** with the message `build: embed a universal python-build-standalone 3.12 (verified, merged, pruned)`.

---

### Task 2: Sparkle updates

**Files:**
- Modify: `app/Package.swift` (Sparkle `exact: "2.10.1"`, linked into `LabyrinthCleaner`; linker rpath `@executable_path/../Frameworks`), `app/Info.plist`, `app/Sources/LabyrinthCleaner/{AppDelegate.swift,StatusMenu.swift,MainMenu.swift}`, `scripts/build_app.sh`, `README.md`
- Create: `app/Sources/LabyrinthCleaner/Updater.swift`

**Interfaces:**
- `final class Updater { init(enabled: Bool); func checkForUpdates(); var canCheck: Bool }` wraps `SPUStandardUpdaterController(startingUpdater: enabled, …)`.
  - `enabled` is false when fixture options are present.
- The menu item "Check for Updates…" goes in the status menu (after "Open at Login") and in the app menu (after About). It is disabled when `!canCheck`.
- `Info.plist` gains:
  - `SUFeedURL`
  - `SUPublicEDKey` (`@SU_PUBLIC_KEY@`, substituted by `build_app.sh` from `app/sparkle_public_key.txt`)
  - `SUEnableAutomaticChecks`
  - `SUScheduledCheckInterval`
  - `SUAutomaticallyUpdate`
- `build_app.sh` copies `Sparkle.framework` from the SwiftPM build products into `Contents/Frameworks/`, preserving symlinks (`ditto`).

- [ ] **Step 1: The key is the user's step; never run `generate_keys`.**
  - The user runs `generate_keys` once in the morning. It creates the private key in their keychain.
  - Then they run `generate_keys -p > app/sparkle_public_key.txt` and rebuild.
  - Until that file exists, `build_app.sh` omits `SUPublicEDKey` and prints a loud warning: "No Sparkle key: updates are disabled in this build". `Updater` is created with `enabled: false` when `Bundle.main` has no `SUPublicEDKey`, so "Check for Updates…" is disabled and the updater never starts.
  - `--publish` refuses to run without the key file.
- [ ] **Step 2: Build.**
  - Add the dependency and `Updater.swift`, and wire the menus.
  - `cd app && swift build` and `swift test` stay green.
  - Update `build_app.sh`.
- [ ] **Step 3: Verify.**
  - `scripts/build_app.sh --debug` succeeds.
  - `otool -L Contents/MacOS/LabyrinthCleaner` shows `@rpath/Sparkle.framework/…`.
  - `otool -l` shows the `@executable_path/../Frameworks` rpath.
  - `plutil -p` shows `SUFeedURL` and the three schedule keys. `SUPublicEDKey` is absent while there's no key file.
  - Test the key path with a throwaway key file holding a dummy 44-character base64 string: the key is substituted. Then delete the throwaway file and never commit it.
  - Launch the debug app with `--fixture-home <tmp>` for 5 s: no crash, and `lsof -i -a -p <pid>` shows no outbound connection except the local server's.
  - Kill it, and check no `devclean ui --app` is left.
- [ ] **Step 4: README.** Add "Updates" (daily check, asks before installing) and "Back up the Sparkle key". The backup section gives:
  - the command `generate_keys -x sparkle_private_key.txt`, run from the folder the tools are in, and where to keep the file
  - the warning that losing the key means existing installs can never update
- [ ] **Step 5: Commit** with the message `app: Sparkle updates (daily check, asks before installing; off in fixture runs)`.

---

### Task 3: App icon and DMG art

**Files:**
- Create: `scripts/make_art.swift`, `app/AppIcon.icns`, `app/dmg/background.tiff`, `app/dmg/dmg_settings.py`
- Modify: `app/Info.plist` (`CFBundleIconFile` `AppIcon`), `scripts/build_app.sh` (copy the icns into Resources)

**Interfaces:**
- `swift scripts/make_art.swift OUT_DIR` writes `AppIcon.iconset/` (all 10 sizes 16–1024) and `background.png` / `background@2x.png` (660×400 / 1320×800).
- `iconutil -c icns` builds the icns, and `tiffutil -cathidpicheck` builds the tiff.
- `dmg_settings.py` follows iptv's `macos/dmg/dmg_settings.py` shape:
  - takes `-D app=…` and `-D bg=…`
  - window 660×400
  - icon size 128
  - the app at (165, 200) and Applications at (495, 200)
  - format `UDZO`

**The design:**
- The icon is a macOS squircle, Big Sur style with the standard inset, in ink `#121110`.
- On it, concentric labyrinth rings with gaps in parchment `#ece5d8`, at about 70% opacity, and one brass `#d4a24c` path threading to the centre.
- It must read at 16 px: simplify the ring count at small sizes.
- The background is ink, with a faint maze arc on the left behind the app and a thin brass arrow from the app to Applications. It carries a small serif caption, "Drag to Applications".

- [ ] **Step 1: Write the script** and render the art. Look at every PNG yourself (the Read tool on the files) and iterate until it is clean at 16, 32 and 1024 px.
- [ ] **Step 2: Build the icns and tiff,** commit them, and wire `Info.plist` and the build.
- [ ] **Step 3: Verify.** `scripts/build_app.sh --debug`, then `plutil -p` shows `CFBundleIconFile`. `sips -g pixelWidth app/AppIcon.icns` works.
- [ ] **Step 4: Commit** with the message `art: app icon and DMG background in the labyrinth style`.

---

### Task 4: Signing, notarization, DMG, verification, publish path

**Files:**
- Create: `app/LabyrinthCleaner.entitlements`, `tests/test_build_publish.py` (a fake `gh` and fake signing tools; it checks the publish argv only)
- Modify: `scripts/build_app.sh`, `scripts/verify_bundle.sh`, `app/Sources/LabyrinthCleaner/{AppDelegate.swift,StatusMenu.swift}` (Decision 6), `README.md`

**Interfaces.** `scripts/build_app.sh [--debug [--sign]] [--no-notarize] [--publish vX.Y.Z]`:
- **`--debug`:** ad-hoc signing as today, no notarization, no DMG. **`--debug --sign`:** Developer ID signing inside-out (step 1 below), no notarization, no DMG.
- **Default:** a release build:
  1. **Sign inside-out with the Developer ID SHA**, `--options runtime --timestamp`, in this order:
     1. every Mach-O under `Resources/python` (the `.so` and `.dylib` files, then `bin/python3.12`)
     2. Sparkle: `XPCServices/*.xpc`, `Autoupdate`, `Updater.app`, then the framework
     3. `MacOS/labyrinth-trash`
     4. the app, with `--entitlements app/LabyrinthCleaner.entitlements`
  2. **Verify the app:** `codesign --verify --deep --strict`, then `verify_bundle.sh --signed`.
  3. **Notarize the app:** zip it with `ditto`, `notarytool submit --wait`, fail with the log unless Accepted, then `stapler staple`.
  4. **Build the DMG** with `dmgbuild`. Make the venv at `build/.dmgbuild-venv` from Homebrew `python3.11` with `dmgbuild==1.6.*`, and retry 3 times on "Resource busy".
  5. **Notarize the DMG:** sign it, notarize, staple.
  6. **Gatekeeper checks** (Review Focus 4): `spctl` on the app and on the DMG, including a quarantined copy, then `stapler validate` on both.
  7. **Write the checksum:** `.sha256`.
- **`--no-notarize`:** steps 1, 2, 4 and the DMG signing only.
- **`--publish vX.Y.Z`** (requires notarization):
  1. Write the version into a copy of the appcast directory.
  2. Run Sparkle's `generate_appcast` with `--download-url-prefix https://github.com/labyrinth-apps/labyrinth-cleaner/releases/download/vX.Y.Z/`, then upload the DMG, the `.sha256` and `appcast.xml`.
  3. Use `gh release create`, or `upload --clobber` when the release exists, on `$LABYRINTH_RELEASES_REPO`.
  4. Never create the repo.

**Decision 6 (quit feedback):** `StatusMenu.showQuitting()` sets the button title to "Quitting…" while `stop(grace:)` runs.

- [ ] **Step 1: Write the entitlements file and the signing, notarization and DMG steps.** Then write `tests/test_build_publish.py`:
  - it runs `build_app.sh --publish v0.0.0-test` in a mode that skips the build (`LABYRINTH_SKIP_BUILD=1` with a fake prebuilt DMG)
  - fake `gh`, `xcrun` and `generate_appcast` sit first on PATH and record their argv
  - it asserts the repo is `labyrinth-apps/labyrinth-cleaner`, the assets are DMG + sha256 + appcast.xml, and `gh repo create` is never called
- [ ] **Step 2: Release build without notarization first.**
  - Run `scripts/build_app.sh --no-notarize`.
  - `verify_bundle.sh --signed` passes.
  - **Never launch a release build.** It ignores `--fixture-home`, so it would scan the real home.
  - To prove the hardened runtime doesn't kill Python or the helper, add `--debug --sign`. That builds the debug configuration, signs it inside-out with Developer ID and the runtime exactly like a release, and does not notarize.
  - Launch that bundle with `--fixture-home <tmp>` for 5 s, confirm a `devclean ui --app --home <tmp>` child is running, kill it, and check no child is left.
- [ ] **Step 3: The full notarized build** (the controller runs this, not the implementer, because it takes long): `scripts/build_app.sh`. Expected:
  - "Accepted" twice
  - `spctl` "accepted … source=Notarized Developer ID"
  - `stapler validate` "The validate action worked!"
  - a DMG at `build/Labyrinth-Cleaner-0.1.0-macOS.dmg`
- [ ] **Step 4: README.**
  - "Install": download the DMG from Releases, drag to Applications, and what the first launch asks.
  - "Build and release": `scripts/build_app.sh` with each flag and its env vars, plus the one-time setup of the notary profile and the Developer ID certificate.
  - Keep commands pasteable.
- [ ] **Step 5: Commit** with the message `build: Developer ID signing, notarization, DMG, Gatekeeper checks, publish path; quit feedback`.

---

## Left for the user (morning)

- **Sparkle key:** run `generate_keys` once, then `generate_keys -p > app/sparkle_public_key.txt`, and rebuild.
- **Publishing:** decide public vs private for `labyrinth-apps/labyrinth-cleaner`, then run `scripts/build_app.sh --publish v0.1.0`.
- **Checks only a person can do:**
  - Install from the DMG on a clean user account; check there are no warnings.
  - The GUI checklist: right-click, swipe, ⌘W and the Dock icon, the folder picker, Install Command Line Tool, Open at Login, a login launch opening no window, the first-run prompts.
  - Sparkle updating from one test version to the next (needs two published versions).
- **Sign-offs:** the product copy, and confirming bundle ID `com.labyrinth-apps.cleaner` in the developer account.
