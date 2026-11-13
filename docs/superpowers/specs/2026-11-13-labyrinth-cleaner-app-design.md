# Labyrinth Cleaner (Mac app): design

Date: 2026-11-13. This builds on `2026-11-11-devclean-ui-design.md` (the web page, the safety gate and the menu-bar item), which is implemented and on `main`.

## Why

devclean works, but only for someone comfortable with a terminal: you install Python files, run `devclean ui`, and get a browser tab. The user wants **any Mac user** to be able to download an app, drag it to Applications, and safely get disk space back.

They also want it to clean more than developer leftovers, so that it's useful on any Mac. Windows may follow "at some point".

**Success:**
- Someone who has never opened Terminal downloads a DMG, drags the app to Applications and opens it. There are no security warnings.
- They see what's using their disk, what they can delete, and what each delete costs.
- They free space in a few clicks.
- The app keeps itself up to date.
- Nothing it deletes is something they'd miss without being told the cost first.

## Decided by the user

- **Audience:** any Mac user. Developers get extra value from the developer categories.
- **Architecture: approach A.** A small native Swift app (menu bar plus window) with the existing **Python core packed inside**. Python is not rewritten in Swift.
- **Signing:** signed with the user's Developer ID and notarized, reusing the labyrinth-iptv setup:
  - team `X8BX4XM7S2`
  - notary keychain profile `labyrinth-notary`
  - a local "Developer ID Application" certificate (already present)
  - a DMG built with `dmgbuild`
- **New general categories:**
  - app caches
  - Docker
  - logs and crash reports
  - Trash
  - iOS device backups, shown as a size only
- **Downloads:**
  - Old installers and large untouched files are offered on a **review list**.
  - Picked files are **moved to the Trash** (Put Back works). They are never permanently deleted.
  - Downloads is the only place the app touches personal files.
- **Never touched:** system folders, Mail and Messages, app uninstalling, removing languages from apps, "memory cleaning".
- **Windows:** later. It isn't built now, but nothing should make it harder.
- **Project folders:** nothing is hard-coded to the author's setup. `~/Documents/Projects` is unconventional, so it isn't a default.
- **Simulators:** the app lists the simulators and simulator runtimes and lets the user choose which to clean, instead of deciding for them. See "Developer folders and simulators" below.
- **The user selects everything; nothing is cleaned automatically.**
  - Every item explains what deleting it means.
  - Risky items carry warnings rather than being hidden or blocked.
  - See "Choice, implications and warnings" below.

## Approaches considered

- **A. Native Swift app with a packed-in Python core (chosen).**
  - The existing Swift menu-bar item grows into an app with a window, and a WKWebView shows the existing page.
  - **Pros:** the most native feel, the smallest change from what exists, and the iptv signing pipeline is reused.
  - **Windows later:** a separate thin Windows app around the same page and the same report and payload code.
- **B. All Python with pywebview.**
  - **Pros:** one language and more direct Windows reuse.
  - **Rejected because:** the menu bar and the window compete for the main thread, it feels less native, and notarizing Python app bundles is the fiddliest of the three.
- **C. A Tauri frame with a Python sidecar.**
  - **Pros:** cross-platform from day one.
  - **Rejected because:** two new toolchains (Rust and Node) for a Windows version that doesn't exist yet, more moving parts, and a less native Mac feel.

## Goals

### Install and distribution

- **The download:** a notarized, stapled DMG with the usual layout (app on the left, Applications on the right), signed with Developer ID. It opens without Gatekeeper warnings on a Mac that has never seen it.
- **Universal:** one build runs on Apple Silicon and Intel, macOS 13 or later.
- **The Python inside:** the app carries its own Python. It never uses the Mac's `python3` and never asks the user to install anything.
- **Where it's published:** the source stays in the private repo `labyrinth-apps/labyrinth-cleaner`. Releases are published to a **public** repo, `labyrinth-apps/labyrinth-cleaner-releases`, the same pattern as iptv.
- **Updates:** Sparkle, signed with an EdDSA key. The appcast is served from the releases repo. A "Check for Updates…" menu item, plus automatic daily checks that ask before installing.

### The app

- **Name and bundle ID:** "Labyrinth Cleaner", bundle ID `com.labyrinth-apps.cleaner`. This is the builder's call; check that the ID isn't taken in the team's Developer account.
- **The menu bar:** the free-space number, unchanged from the current item: Finder's figure, amber below 30 GB, red below 15 GB, one notification below 15 GB. Clicking it opens this menu:
  - "*N GB free of M GB*" (disabled)
  - **Open Labyrinth Cleaner**
  - **Open at Login** (checkmark, through `SMAppService.mainApp`)
  - **Check for Updates…**
  - **Install Command Line Tool…** (see below)
  - **Quit**
- **The window:**
  - A normal resizable window, about 1100×800, with a WKWebView showing the existing page served by the packed-in Python server.
  - The page arrives with no URL bar and no token visible. The app passes the token itself.
  - While the window is open the app shows a Dock icon. When it closes, the Dock icon goes away and only the menu bar item remains.
- **Server lifecycle:**
  - The app starts the Python server (`devclean ui --no-open`, or an equivalent entry point) when the window is first opened.
  - It's stopped when the app quits.
  - All of the existing server security stays: `127.0.0.1` only, the token, and the Host and Origin checks.
  - The 60-minute idle exit stays for the CLI. For the app-owned server it's disabled, or the app restarts the server on demand. Builder's call.
- **The command line tool:** "Install Command Line Tool…" creates a `devclean` link in `~/.local/bin` (or `/usr/local/bin` if the user picks it) pointing at a launcher inside the app, so the CLI uses the app's Python. Builder's call: the user didn't ask for it, but devclean users have it today.
- **First run:**
  - A short welcome screen in the window, explaining that macOS will ask once for access to Documents and Downloads, and why. Then the first scan.
  - **The app never asks for Full Disk Access.** If something can't be read, it's skipped and the page shows a note.

### New cleanup categories

They live in the Python core with the same item, tier, cost and safety-gate model as today. Each needs scanners, cost text, gate rules and tests.

| Category | What | Tier | How it's deleted | Cost text, starting wording |
|---|---|---|---|---|
| **App caches** | Top-level folders in `~/Library/Caches` not already covered by a developer target. | safe | `rm -rf` | "Pages and images load a bit slower the first time. You stay logged in." |
| **Docker build cache** | `docker builder prune -f` | opt-in | `docker` command | "The next image build starts from scratch." |
| **Unused Docker images** | `docker image prune -a -f` | caution | `docker` command | "Images are downloaded again the next time a container needs them." |
| **Logs and crash reports** | `~/Library/Logs` and `~/Library/Logs/DiagnosticReports` contents older than 7 days. | safe | `rm -rf` per entry | "Old logs. Only useful when debugging a crash." |
| **Trash** | The current size of the Trash, and "Empty Trash". | caution | Through Finder | "Gone for good. You can't Put Back afterwards." |
| **iOS device backups** | `~/Library/Application Support/MobileSync/Backup` size, if readable. | keep | never | "Manage them in Finder → your iPhone → Manage Backups." If unreadable: "Size unavailable without Full Disk Access." |
| **Old installers in Downloads** | `.dmg`, `.pkg`, `.mpkg`, `.xip` older than 14 days, plus `.zip` files over 50 MB older than 14 days. | review (new) | Move to Trash | "Moved to the Trash. You can Put Back." |
| **Large untouched downloads** | Any file in Downloads over 500 MB whose last-opened date (Spotlight `kMDItemLastUsedDate`, falling back to modified time) is over 90 days old. | review (new) | Move to Trash | Same as above. |

**App caches:**
- **Exclusions:**
  - anything starting with `com.apple.`
  - this app's own cache
  - folders already offered by a developer target, such as Homebrew, CocoaPods and Playwright
- **While the owning app runs:**
  - The cache is shown but not selectable, with the note "Quit *App* first".
  - The owner is found by matching the folder name against running apps' bundle IDs and names, case-insensitively, and against the publisher folder of a known bundle ID (for example `Google` → `com.google.Chrome`).
  - If the owner can't be found, the cache is selectable, but it's never preselected.
- **Size:** caches under 20 MB collapse into the existing small-items row, as today.

**Docker:**
- Shown only if a `docker` CLI is found and its daemon answers `docker system df --format json` within 3 s.
- If the daemon isn't running, show a kept row: "Start Docker to see what can be cleaned."
- The segment size comes from `docker system df`.
- **Never** prune containers or volumes.

**Trash:**
- Size comes from `~/.Trash`. If that can't be read without Full Disk Access, show "Trash: size unavailable" and still offer "Empty Trash".
- Emptying goes through Finder (`tell application "Finder" to empty trash`). macOS asks once to let the app control Finder; the first-run screen mentions this.

**The review tier for Downloads:**
- A new UI group, **"Review: moves to Trash"**, placed after "Check first". It's never preselected, and every row shows its file name, size, and last-opened date.
- The confirmation dialog splits the list into "Deleted permanently" and "Moved to Trash".
- The safety gate gets a separate rule for these items. The path must be a regular file (not a folder, not a symlink) directly in `~/Downloads` or one level down, and it must still meet its category's criteria when re-checked just before the move.
- They are moved with `FileManager.trashItem` through a small signed helper inside the app, called from Python like today's runners. **`rm` is never used under Downloads.** The existing deny rule stays for `rm`.

### Choice, implications and warnings

These rules apply to every item and supersede the earlier spec's preselection.

- **Nothing is preselected.**
  - The page opens with nothing ticked.
  - Each group header has a **"Select all"** shortcut. "Costs time" keeps **"Select stale"**.
  - The headline still says "You can get back X GB · Y GB with no cost".
  - The action bar appears only once the user has ticked something.
- **Nothing is cleaned automatically.**
  - There are no scheduled or background cleans, and no "clean on low space".
  - The low-space notification only opens the app.
  - The CLI's `--yes` stays for scripted developer use (it's documented as such). The app never uses it.
- **Every row explains its implications.**
  - Each row has the cost tag and cost line, and expands (a click or a chevron) to a **"What happens"** panel:
    - **What gets deleted:** the path, or the command for `xcrun`, `docker` and Finder.
    - **What it costs you:** time, a download, or a re-login.
    - **How to get it back:** a rebuild, a re-download, or Put Back from the Trash.
  - All of this text comes from the core (`Item.cost`, plus new fields `Item.deletes` and `Item.restore`). It isn't written in the JavaScript.
- **Warnings replace most hard blocks.** `Item` gets a `warning` field. A row with a warning shows it in red under the name, and the item goes in **"Check first"**, never preselected. Items that used to be hidden or kept by policy are now offered with a warning:

  | Item | Was | Now | Warning, starting wording |
  |---|---|---|---|
  | Simulator runtime that leaves a simulator or device type with nothing to run on | kept | Check first | "Your *iPhone 18 Pro* simulator stops working. No other installed runtime runs it." |
  | Newest full iOS runtime | kept | Check first | "This is your newest iOS runtime. New simulators need it." |
  | Android system image used by an emulator | kept | Check first | "The *Pixel 10 Pro* emulator stops working until you download this image again (the SDK Manager does it)." |
  | Newest Gradle version folder | kept | Check first | "The version your projects probably use. It downloads again on the next build." |
  | Newest DeviceSupport for a device model | kept | Check first | "Re-copied the next time you debug on that iPhone." |
  | **Xcode Archives** | never offered | **Review: moves to Trash**, per archive | "Shipped builds. You need them to read crash reports for those versions. Moved to the Trash, so you can Put Back." |
  | Downloads: old installers, large untouched files | n/a | Review: moves to Trash | (as above) |

- **Still blocked.** Shown under "Kept" or as an unselectable row, always with the reason:
  - **Things in use right now:**
    - a runtime with a running simulator
    - a running simulator
    - an app's cache while the app is open ("Quit *App* first")
    - DerivedData while Xcode is running
  - **Can't be done safely:** a disk image not matched to a runtime, or anything the safety gate refuses (symlinks, paths outside the allow-list).
  - **Out of scope:** system folders, personal files outside the Downloads review list, Mail and Messages.
  - **iOS backups:** size only.
- **The confirmation dialog shows the implications.** It's grouped as "Deleted permanently" and "Moved to Trash", and every item with a warning shows its warning there in red. If any warning item is selected, the Delete button stays disabled until the user ticks **"I've read the warnings above"**.
- **The gate before deleting:** `validate_item` recomputes warnings from fresh data. If a warning exists that the user's scan didn't show, the item is refused with "Situation changed: scan again". Items in use are refused.

### Developer folders and simulators

**Developer extras switch on automatically.**
- Xcode (DerivedData, DeviceSupport), Gradle, npm, CocoaPods, Homebrew, Playwright, Shorebird, Docker, simulators and Android images are scanned wherever they exist.
- They all live under `~/Library`, `~/.gradle`, `~/.npm` and similar places, none of which macOS protects, so a non-developer never gets a privacy question because of them.

**Project folders:**
- **Auto-scanned when they exist:** `~/Developer`, `~/Projects`, `~/code`, `~/src`, `~/dev`, `~/workspace`, `~/git`, `~/repos`. None of these are protected by macOS. This list is the builder's call.
- **Added by hand:** an **"Add project folder…"** button on the page (and in the menu) opens a native folder picker. Choosing a folder there grants access without the macOS question, so `~/Documents/Projects` and any other location work this way.
- **Settings:** the list is saved in the app's settings and shown on the page, where each folder can be removed.
- **The scan:** looks for Flutter and web build folders in every listed folder.
- **`Config.projects` becomes a list.** Safety-gate rule 1 accepts an artifact folder inside **any** listed project folder. Everything else in rule 1 is unchanged: a `pubspec.yaml` or `package.json` must sit next to it, and anything else under `~/Documents` is refused.
- **The CLI:** reads the same list. It also has a repeatable `--projects DIR` option.
- **First run:** the welcome screen mentions "Add project folder…" for developers. It no longer warns everyone about Documents. The only macOS question most users see is for Downloads (the review list), and for Finder when they first empty the Trash.

**Nothing personal ships in the app.**
- `DEFAULT_PINNED_DEVICE_TYPES` (iPhone-18-Pro) and `DEFAULT_PROTECTED_AVDS` (Pixel_10_Pro, Television_4K) are removed.
- The general rules replace them:
  - an Android image used by **any** emulator is never offered
  - the protection below covers simulators

**Simulators: the user chooses, and the app warns.** This is the builder's reading of "let the user choose what to clean"; the user should confirm it.

- **A "Simulators" section lists every simulator:** its name, OS version, the size of its data folder, the last time it was used, and whether it's running.
  - **Deleting one:** `xcrun simctl delete <udid>`, in the "Check first" group, with the cost text "Deletes its installed apps and data. Xcode can create a fresh one."
  - Running simulators are shown but can't be selected.
- **Every runtime is listed** (the warning table above covers this). The 2026-11-02 protection rule changes from a hard block to a **warning**:
  - **When deleting a runtime would leave a simulator, or a device type, with no runtime:** the item is still offered in "Check first". It's never preselected, and it carries a red warning that names what breaks, for example "Your *iPhone 18 Pro* simulator stops working. No other installed runtime runs it."
  - **The confirmation dialog repeats that warning** next to the item.
  - **The newest full iOS runtime** (the old pinned rule) gets the same kind of warning: "This is your newest iOS runtime; new simulators need it."
  - **Still a hard block:** a runtime with a **running** simulator, and a disk image that can't be matched to a runtime. Both stay under "Kept".
  - **The gate before deleting:** `validate_item` checks the runtime again with fresh `simctl` data. It refuses if a simulator on it is now running or if the image is gone. If a new warning appeared that the user never saw, it refuses with "Situation changed: scan again".
- **Unavailable simulators** (`simctl delete unavailable`) are offered as before.

### Disk map

- New segments: **App caches**, **Docker**, **Downloads (reviewable)**, **Trash**, each with its striped reclaimable part.
- "System, apps & other files" shrinks accordingly.
- Same rule as before: only known locations are measured.

## Non-goals (this version)

- Windows. The page and `devclean_report` stay free of macOS-specific code where that costs nothing, so a Windows app can reuse them later.
- The Mac App Store. Its sandbox would block most of these scans.
- A Swift rewrite of the core.
- Full Disk Access, admin rights, a privileged helper, or system-wide caches.
- Uninstalling apps, finding duplicates, or browsing every large file on the disk.
- Payments, licensing, analytics, telemetry or crash reporting. The app sends nothing anywhere except Sparkle's update check.

## Anti-goals (a failure even if it works)

- It deletes anything the user would miss without first being told the cost. Every delete is still re-checked just before it happens.
- Downloads files are deleted permanently. They are only ever moved to the Trash.
- Gatekeeper warns at first launch, or notarization fails because something inside the app isn't signed.
- It asks for Full Disk Access or an admin password.
- It wakes the disk or uses CPU in the background. Only the free-space check runs every 60 s; scans happen only when the user asks.
- It feels like a website in a box: a page that scrolls under the title bar, a visible URL or token, or a right-click menu offering "Reload".

## Constraints

- The Python core stays the single source of truth for scanning, tiers, costs and the safety gate. The Swift side doesn't decide what's deletable.
- No dependencies outside Python's standard library in the core. Sparkle is the one third-party framework in the app.
- The existing tests (106) keep passing. New categories come with fixture tests in the same style: a fake `$HOME`, and no real `docker`, `xcrun`, Finder or Trash calls in tests.
- **Signing:** every executable file in the bundle is signed with the Developer ID and the hardened runtime: the Python binary, every `.so` and `.dylib`, Sparkle's helpers, and the trash helper. Notarization must come back "Accepted" with no warnings worth acting on.
- Built from this repo by one script, `scripts/build_app.sh`, modeled on labyrinth-iptv's `build_macos_dmg.sh`. Flags: `--no-notarize`, `--publish vX.Y.Z`.
- **Development safety (unchanged):** never run a real deletion on the developer's Mac while building or testing. Fixture homes only. The app needs a debug-only way to point at a fixture home.

## Settled after the builder check (my calls)

- **The CLI from a source folder keeps working** with the Mac's own `python3`:
  - Downloads review items are listed there, but moving them is refused without the app's trash helper: "use the app to move these to the Trash".
  - `--yes` keeps deleting only today's developer SAFE targets, never app caches or logs.
  - The README leads with the app and keeps a "Command line" section.
- **Empty Trash runs first in a clean,** so it can never permanently delete Downloads files moved to the Trash in the same clean.
- **`xcrun` is only called when `xcode-select -p` finds developer tools,** so a Mac without Xcode never gets the "install command line developer tools" pop-up.
- **The command line tool goes to `~/.local/bin` by default.** `/usr/local/bin` is offered only when it's writable without an admin password.
- **The Sparkle EdDSA private key** lives in the login keychain (Sparkle's `generate_keys` default). The README documents exporting a backup, because losing the key means existing installs can never update.
- **A Downloads file that matches both rules** (old installer and large untouched) shows once, as an installer.
- **Before planning:** check that the bundle ID is free, and create the public releases repo when the first release is published, not before.

## Left to the builder

- **How Python gets packed in.** For example, python-build-standalone `install_only` builds for arm64 and x86_64 merged with `lipo`, or a universal2 Python.framework. The requirement is that it's relocatable, universal, signable and stdlib-only.
- **How the Swift app and the Python server talk:**
  - a launch argument for the fixture home
  - how the port and token are handed over (a stdout line, or a file in the app's temp folder)
- The exact Sparkle wiring.
- The app icon and the DMG background. Something simple and clean, in the style of the page.
- Whether the old `devclean menubar install` stays. Retiring it in favor of the app is fine; if it's kept, the two must not run together.
- Layout of the Xcode or SwiftPM project for the app.

## Tests (must have)

- **New category scanners, on fixture homes:**
  - app caches, with running-app exclusion through an injected "running apps" provider
  - logs
  - the Downloads installer and large-file rules, with injected last-used dates
  - backups shown as keep only
  - Docker, with an injected runner returning canned `docker system df` JSON
- **The safety gate:**
  - review items are refused for `rm` and only ever trashed
  - folders and symlinks in Downloads are refused
  - an item that no longer matches its rules at clean time is refused
  - Docker commands are limited to exactly the two allowed argv lists
- **The server:** the existing security tests still pass for the app-launched server.
- **Project folders:** artifacts are found in several listed folders, gate rule 1 accepts any listed folder, a folder that isn't listed is refused, and `~/Documents` personal files are still refused.
- **Choice:**
  - no item in the scan payload is preselected
  - every offerable item has `cost`, `deletes` and `restore` text
  - the converted items (Android image used by an emulator, newest Gradle, newest DeviceSupport, Archives as trash items) are offered with a warning and accepted by the gate
  - in-use items are refused
  - a warning that appears between the scan and the clean is refused
  - the page keeps Delete disabled until the warnings box is ticked (a browser check)
- **Simulators:**
  - the incident fixture (27.0 general plus 27.1 Duo-only) now **offers** 27.0 with the warning naming iPhone 18 Pro, instead of hiding it
  - a runtime with a running simulator is still refused
  - "Situation changed" is refused when a warning appears between the scan and the clean
  - single simulators are offered, and running ones are refused
- **Build verification, in the build script:**
  - `codesign --verify --deep --strict`
  - every Mach-O in the bundle is Developer ID signed with the hardened runtime
  - `spctl -a -vv` accepts the app and the DMG
  - `stapler validate` passes
- **Manual:**
  - install from the DMG on a clean user account, or a second Mac, and check there are no warnings
  - the first-run prompts appear once
  - the window feels native: no reload, no URL
  - Sparkle updates from one test version to the next
  - Open at Login works
