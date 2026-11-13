# devclean UI: design

Date: 2026-11-11. Approved mockup: `2026-11-11-devclean-ui-mockup.html`, in this folder. Open it in a browser. It uses real scan data from today.

## Why

The Mac (460 GB) fills up every 1–2 weeks. `devclean` (CLI, this repo) already knows what's safe to delete. The user finds the terminal table hard to work with. They want to:

1. scan,
2. see where the space goes,
3. see what they can safely remove,
4. see **what removing each thing costs them**,

and then clean, all in one screen.

The IDE's built-in disk manager was checked and isn't enough. It's built around repos and worktrees: it found 2.6 GB "safe", its list rendered empty, and it doesn't know about simulators, Gradle or Android.

**Success:** on a low-disk day the user runs one command, sees the answer in about 15 s, ticks things and frees the space without reading terminal output. It never deletes anything the CLI wouldn't.

## Goals

- `devclean ui` starts a local web UI and opens it in the default browser.
- The screen matches the approved mockup, from top to bottom:
  1. **Header:** app name, scan time and duration, a "Scan again" button.
  2. **Hero:**
     - Left: "You can get back X GB · Y GB with no cost", plus three clickable tier cards (Safe / Costs reinstall time / Check first) that filter the list.
     - Right: a ring showing free space now, and "After this clean" updating live with the selection. Below it: "Menu bar turns amber below 30 GB".
  3. **Disk map:** one segmented bar for the whole disk. Each segment shows a striped overlay for the part that can be cleaned. There's a legend with sizes. Clicking a segment filters the list.
  4. **Filter chips:** All / Safe / Costs time / Check first / Kept.
  5. **Group cards:**
     - **Safe to remove** (preselected, with a "Select none/all" toggle)
     - **Costs you some time** (a "Select stale" shortcut)
     - **Check first**
     - **Kept on purpose** (read-only, with the reason for each)
  6. **Rows** show: checkbox, icon, name, path, size with a proportional mini bar, last used, a **cost tag** (`NO COST`, `~N MIN`, `N GB DL`), and a plain-language cost line. Opt-in rows unused for more than 30 days get a "Nd/Nmo unused" badge.
  7. **Floating action bar:** selected total, item count, free space after, a warning when a "Check first" item is selected, and a **Review & clean** button.
  8. **Confirmation modal:** the exact list with sizes, "Free space: A → B", Cancel / Delete. It says the deletion is permanent, not to the Trash.
  9. **After deleting:** per-item progress, then an automatic rescan, then "Freed X GB" with real before/after free space.
- Items under 20 MB collapse into a "+ N small items · X MB" row that expands.

## Non-goals (v1)

- A full native Mac app. The menu-bar item (part 2 below) only shows free space and opens the page. It never scans or deletes.
- Breaking down "everything else" (system, apps, Mail, Photos…). See the disk map rules below.
- New cleanup targets beyond what `devclean_core.scan()` already finds.
- Remote access, multiple users, authentication beyond the local token.

## Anti-goals (a failure even if it works)

- The UI can delete something the CLI's safety gate would refuse. **Every delete goes through `devclean_core.execute_item()`**, which re-runs `validate_item()`.
- Any web page or process other than the one the user opened can trigger a delete (CSRF, DNS rebinding, LAN exposure).
- macOS privacy prompts ("Terminal wants to access your Photos/Mail…") are triggered by the scan.
- It's slower than the CLI scan (about 12 s today) by a noticeable margin.
- It's ugly or cramped. The mockup's visual quality is the bar.

## Constraints

- Python 3 standard library only (`http.server`, `json`, `threading`), like the CLI. No npm, no build step. The page is one static HTML file with inline CSS and JS, served by the script.
- Reuse `devclean_core.py` (`scan`, `Item`, `validate_item`, `execute_item`, `Config`, `SimctlProvider`). Logic shared by the UI and CLI (cost text, groups, the disk map) goes in the core, not in the JS. The CLI may show the cost line too, as a bonus.
- Deleting is permanent: `rm -rf` or the `xcrun simctl` command, as today. Nothing goes to the Trash.
- While developing, real deletions are never run on this Mac. Test against fixture homes (`--home`, `--simctl-fixture`), like the existing tests.
- `--home` and `--simctl-fixture` work with `devclean ui` too, so the UI can be demoed and tested on a fixture.

## Decided by the user

- Layout: a mix of mockup A and B, in the polished style of the approved mockup.
- Delivery: a UI on top of the existing tool. The CLI stays.

## Builder's calls (made in this doc; change them if they're wrong)

### Server and security

- Bind to `127.0.0.1` on a random free port. Generate a random 32-byte token at startup.
- The opened URL is `http://127.0.0.1:<port>/?t=<token>`. The page reads the token and sends it as the `X-Devclean-Token` header on every API call.
- Reject any request when:
  - the `Host` isn't `127.0.0.1:<port>` or `localhost:<port>` (DNS rebinding)
  - a state-changing request has a missing or wrong token
  - a state-changing request has an `Origin` that, when present, isn't the same origin
- GET `/` serves the HTML without the token check, so the page can load. The HTML holds no data.
- The server prints the URL, opens it with `open`, and runs until Ctrl-C. It also exits after 60 minutes with no requests.

### API

- `GET /api/scan`: runs `scan()` and returns JSON with:
  - disk: total, free
  - `items`: each with key, group, label, path, size, mtime, cost_tag, cost, preselect, stale, project, kind
  - the disk map segments
  - warnings
  - `scan_id`

  The server keeps the latest scan's items, keyed by `scan_id`. Only one scan runs at a time. A second request waits for the running scan and gets its result.
- `POST /api/clean {scan_id, keys}`:
  - Returns 409 if `scan_id` isn't the latest scan.
  - Looks up the keys in that scan. Unknown keys are rejected.
  - Starts a background job that runs `execute_item()` on each item in order and returns `job_id`.
  - Only one clean job runs at a time.
- `GET /api/job/<id>`: returns per-item status (pending, running, done, failed with message, refused with reason), plus free space before and now. The page polls it about every 500 ms.
- The API never accepts paths or commands from the client, only item keys from the server's own scan.

### Cost text

Add `cost_tag` and `cost` to `Item`, set during the scan. Starting wording, mostly taken from the mockup:

| Target | Tag | Cost line |
|---|---|---|
| Homebrew cache | NO COST | Old downloads of packages you already installed. |
| npm cache | NO COST | The next install is a little slower. |
| CocoaPods cache | ~1 MIN | Next `pod install` downloads pods again. |
| Playwright browsers | ~1 MIN | Run `npx playwright install` before the next e2e run. |
| Gradle build cache | ~2 MIN | The first Android build after this takes longer. |
| Old Gradle versions | ~2 MIN | Re-downloaded only if a project still uses Gradle <v>. |
| Shorebird cache | ~2 MIN | Re-downloaded on the next shorebird command. |
| DerivedData | ~5 MIN | Xcode re-indexes and rebuilds. If Xcode is running: "Quit Xcode first". |
| Flutter build/.dart_tool | ~2 MIN | Next `flutter run` does a full build. |
| node_modules | ~1–3 MIN by size | Run `npm install` before the next <project> work. |
| .next | ~1 MIN | Rebuilt by `next dev`/`next build`. |
| Simulator runtime | `<size> DL` | Which simulators stop working, and what still covers their device types. |
| DeviceSupport | ~5 MIN | Re-copied from the iPhone the next time you plug it in and debug. |
| Android image | `<size> DL` | No AVD uses it. Re-download from the SDK Manager if needed. |
| `simctl delete unavailable` | NO COST | These simulators already can't run. |

### Groups

The existing tiers map to the UI groups: `safe` → Safe, `opt-in` → Costs time, `caution` → Check first, `keep` → Kept.

### Project grouping

A Flutter project's `build` and `.dart_tool` show as **one row** (for example "hitpoint-app · build + .dart_tool"), and so do a web project's `node_modules` and `.next`. In the UI the row's checkbox selects all of its item keys. The core keeps them as separate items, so the safety gate checks each path on its own. Rows group by a new `project` field.

### Stale

For opt-in items, stale means the newest mtime among the project folder's top-level entries, excluding the artifact folders themselves, is more than 30 days ago. The badge shows "Nd unused" for under 60 days, otherwise "N mo unused". "Select stale" ticks exactly the stale rows in the Costs-time group.

### Disk map

The map measures only what's already known, so it stays fast and triggers no privacy prompts. Segments:

| Segment | Sources |
|---|---|
| Simulators | runtime disk images from simctl `sizeBytes` + `du` of `~/Library/Developer/CoreSimulator/Devices` |
| Android | `~/Library/Android/sdk` + `~/.android/avd` |
| Xcode | `~/Library/Developer/Xcode`: DerivedData + DeviceSupport + Archives |
| node_modules | the sum of the web artifact items |
| Caches & builds | the sum of the other safe items + kept Gradle versions + Gradle `modules-2` |
| Everything else | used − all the above |
| Free | free space |

- The striped overlay on a segment equals the reclaimable size inside it.
- "Everything else" is solid gray with the label "System, apps & other files". Clicking it shows a note that devclean doesn't analyse it.
- The mockup's split into "System & apps" and "Other files" (≈ estimates) is dropped. There's no honest, fast way to measure it.

### Low-space warning

The menu-bar item (part 2) provides the warning. The hero text reads "Menu bar turns amber below 30 GB".

### Fixture mode never touches the real simulators

When `--home` or `--simctl-fixture` is set, the UI server, and the CLI too, passes `execute_item()` a runner that refuses `xcrun` items. Without it, a fixture demo would run the real `xcrun simctl runtime delete <uuid>`. The fixture `real_2026-11-11` offers the real iOS 26.5 UUID. A test covers this.

### Settled after the builder check

- **Headline:** X = Safe + Costs time + Check first. "Y GB with no cost" = the Safe total.
- **Token:** required on every `/api/*` call, GET included.
- **Runtime `<size> DL` tag:** uses the runtime's on-disk `sizeBytes`.
- **Stale detection:** ignores `.git` as well as the artifact folders.
- **"Everything else":** shown as 0 if the calculation goes negative.
- **Mixed projects:** a project with both Flutter and web output gets one row per group.

## Part 2: menu-bar free-space item

Built after the page works. Agreed with the user on 2026-11-12, after weighing it against a notification only: the disk fills gradually, so a number that's always visible lets them clean up before it becomes urgent.

- **The menu bar:** shows free space only, for example `85 GB`.
  - Normal colour at 30 GB or more.
  - Amber below 30 GB.
  - Red below 15 GB.
- **Measuring:** free space is read every 60 s with `volumeAvailableCapacityForImportantUsage`, the same number Finder shows. That's cheap: no scanning, no `du`.
- **The click menu:**
  - The free and total line, for example "85 GB free of 460 GB".
  - "Clean up…", which runs `devclean ui`. That starts the server and opens the page. If a server is already running, it opens the same page.
  - "Quit".
- **Notification:** one macOS notification when free space first drops below 15 GB. It doesn't repeat until space has gone back above 20 GB.
- **How it's made:**
  - One Swift file (`menubar/DevcleanBar.swift`), compiled with `swiftc`. No Xcode project.
  - An `NSStatusItem` with no Dock icon.
  - `devclean menubar install` builds it into `~/Applications/DevcleanBar.app` and adds a LaunchAgent so it starts at login.
  - `devclean menubar uninstall` removes both.
- **Safety:** it never deletes anything and never calls the core. Deleting happens only in the page, through `execute_item()`.
- **Builder's call:** to make "Clean up…" reuse a running server, `devclean ui` writes its URL to `~/.cache/devclean/ui.url`, readable only by the user. It removes the file when it exits. The menu-bar item opens that URL if the server answers, and otherwise runs `devclean ui`.
- **Tests:**
  - A colour/threshold function, unit-tested with a tiny Swift test or checked by hand.
  - Install/uninstall leave nothing behind.
  - Manual check that the number matches Finder.

## Left to the builder

- **File layout:** e.g. `devclean_ui.py` (server) + `ui/index.html`, or the HTML embedded as a string.
- Exact icons per category. The mockup uses emoji. Inline SVGs are fine if they look better.
- Responsive behaviour below 820 px. The mockup already has a breakpoint. Keep it usable in a narrow panel.
- How progress is animated during the clean.

## Tests (must have)

- API on a fixture home, with the server running in a thread:
  - wrong or missing token → 403
  - wrong `Host` → 403
  - a clean with a stale `scan_id` → 409
  - a clean with a key for a kept item, or an unknown key → refused, and nothing deleted
  - a clean of safe keys deletes exactly those folders and leaves source, Archives and Downloads intact
  - a forged key never reaches `rm`
- Core: cost tags and texts exist for every offerable item. Disk map segments add up to the total. Stale detection works.
- The existing 26 CLI tests still pass.
- Manual check: `devclean ui --home <fixture>` renders like the mockup at laptop width and at 390 px. `devclean ui` on the real machine shows the real scan. Don't click Delete on the real machine during development.
