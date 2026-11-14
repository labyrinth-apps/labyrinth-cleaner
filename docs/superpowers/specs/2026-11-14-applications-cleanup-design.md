# Applications cleanup: duplicate, old and unused apps

Status: approved by the user, 2026-11-14. Extends `2026-11-13-labyrinth-cleaner-app-design.md`.

## Why

Many Macs carry copies of applications their owners don't know about. On a friend's MacBook, the user found:
- `Figma`, `Figma (v88.1)` and `Figma (v101.6)` side by side.
- `Nucleo` and `Nucleo (v2.6.1)`, both last opened in January 2020.
- Apps nobody had opened in years (Tor Browser, Acrobat Distiller).

macOS's own Storage Management view lists these, but it is buried in System Settings and most people never look. Labyrinth Cleaner already shows people where their space goes. It should point at forgotten apps too, and let the user decide what to remove.

**This reverses an earlier rule.** The app design listed "app uninstalling" under "Never touched", and "finding duplicates" under non-goals. The user decided on 2026-11-14 to add this feature. Both lines change as described under "Changes to the existing design".

## Goals

- A new **Applications** section. It lists, for the user to decide:
  - **Extra copies:** two or more apps with the same bundle ID. One copy is suggested to keep; the others are listed.
  - **Apps not opened in 6 months or more,** by the date macOS records when an app is opened.
- **Each row says why it's listed,** in plain words:
  - "older copy · keep Figma 101.6"
  - "second copy of Nucleo · keep the one in Applications"
  - "last opened Sep 2020"
- **Intel-only apps get an extra note** on Apple Silicon Macs: "Intel only, stops working in macOS 28". This connects to the "App Update Required" notice macOS 27 shows.
- **Removing moves the app to the Trash,** so Put Back works. This holds for every app, including ones the system owns, such as App Store apps.
- **A new "Applications" block on the disk map,** sized from all scanned apps and striped by the listed ones.

## Non-goals

- Removing an app's support files, preferences or containers ("full uninstall").
- App copies outside the application folders. Copies in Downloads are already covered by the Downloads review.
- Updating apps, or telling the user an update exists.
- Apps that are part of macOS: anything under `/System`, and Safari.

## Anti-goals: if any of these happen, the feature has failed

- **An app is removed the user didn't tick.** Nothing in this section is ever preselected, and nothing is cleaned by the CLI's `--yes`.
- **The suggested-to-keep copy is offered for removal.** It is shown as kept, never as a checkbox.
- **Labyrinth Cleaner asks for, sees or stores an admin password,** or requests admin rights.
- **A running app is offered.**
- **Something is deleted with `rm`.** Apps only ever go to the Trash.
- **The user can't tell which copy is which.** Every row shows its full path and version.

## The user's decisions

1. **Removal: option 3, mixed.**
   - **Apps the user owns** go to the Trash through the existing `labyrinth-trash` helper. They get its safety checks, and there is no prompt.
   - **Apps the user doesn't own** (root-owned, such as App Store and installer apps) go to the Trash by asking Finder, through the same Apple Events permission Empty Trash already uses. If moving the app needs a password, Finder shows its own macOS prompt. Labyrinth Cleaner never sees the password.
   - **Target:** at most one password prompt per clean, by sending all such apps in one Finder request.
2. **The user decides** what to remove, especially old apps. Every item is in the Review tier: never preselected, moved to the Trash.
3. **Which copy to keep** is suggested, using these rules in order:
   1. Highest version: `CFBundleShortVersionString`, compared as dotted numbers, then `CFBundleVersion`.
   2. Same version: the copy whose file name has no suffix (`Figma.app` over `Figma (v88.1).app`), and `/Applications` over its subfolders and `~/Applications`.
   3. Still tied: the copy opened most recently.
   4. A copy that is running right now is always the one kept, whatever the rules above say.
4. **"Not opened in a long time" means 180 days.**

## Changes to the existing design

- **"Never touched":** "app uninstalling" becomes "removing app support files, or apps that are part of macOS".
- **Non-goals:** "finding duplicates" now refers to duplicate *files*, not applications.
- **The `labyrinth-trash` helper's allow-list grows to:**
  - `/Applications/<name>.app`
  - `/Applications/<one folder>/<name>.app`
  - `~/Applications/<name>.app`
  - `~/Applications/<one folder>/<name>.app`

  The target must be a real directory ending in `.app`, not a symlink at any level. It must be owned by the current user; root-owned apps never go through the helper. All the helper's existing checks still apply: byte-wise path, no symlink following, `realpath == path`.

## How it works

### Scan

- **Folders scanned:** `/Applications`, its direct subfolders (e.g. `/Applications/Utilities`, vendor folders), `~/Applications` and its direct subfolders. Bundles inside other bundles are ignored.
- **Fixture runs:** the folders come from the `Config`, and fixture runs point them at folders inside the fixture home. Tests never read the real `/Applications`.
- **For each `.app`:**
  - Bundle ID, short version and build come from `Contents/Info.plist`.
  - Owner and mtime come from `lstat`.
  - Spotlight attributes come from one `mdls` call for all apps: `kMDItemLastUsedDate`, `kMDItemPhysicalSize` and `kMDItemExecutableArchitectures`.
  - If Spotlight has no size, fall back to the existing directory sizing.
- **Skipped:**
  - bundles without a bundle ID
  - bundles that are symlinks
  - Labyrinth Cleaner itself (`com.labyrinth-apps.cleaner`)
  - Safari
  - running apps, which appear as kept with "Quit <name> first"
- **Listing an app with no recorded "last opened" date:** it is listed as unused only when its bundle mtime is also over 180 days old. The row then says "no record of being opened". Otherwise it isn't listed for age. Menu bar and background-only apps (`LSUIElement`, `LSBackgroundOnly`) with no date are never listed for age: they are started at login or by other apps, so macOS often never records them as opened (added 2026-11-14 after a real scan listed Ice and a URL handler). A date they do have still counts, and their copies are still listed.
- **Intel only:** the architectures include `x86_64` and not `arm64`. The note appears only on Apple Silicon Macs, and only on rows that are listed anyway; an Intel-only app is not listed for that alone.
- **Duplicate groups** are by bundle ID. The suggested keeper is a kept row: "Keep: newest version" when its version is strictly the highest, otherwise "Keep: suggested copy". The others are Review items whose note names the keeper's version and path. Versions compare with trailing zeros ignored ("1" equals "1.0") and pre-releases ("1.0b2", "2.0-beta.1", "3.1 RC 2") below their release.
- **The Xcode in use for command-line builds** (the app holding `xcode-select -p`) is treated like a running copy: it is the keeper and is never offered. Its row says "Keep: used for command-line builds".

### Items

- **Category:** "Applications". **Tier:** REVIEW. **`preselect`:** false.
- **`kind`:**
  - `trash` for apps owned by the user (the existing helper route)
  - a new `finder-trash` for root-owned apps
- **The safety gate re-checks each item right before moving it:**
  - same bundle ID
  - same version
  - same owner
  - not running
  - still the same inode

  Otherwise the result is "Situation changed: scan again".
- **The `finder-trash` route:**
  - It runs `osascript` with a `tell application "Finder" to delete {POSIX file …}` for the batch, inside the same `with timeout` pattern and the same plain-language messages for -1743 (not allowed to control Finder) and -1712 (timed out) as Empty Trash.
  - A cancelled password prompt reports "Cancelled: nothing was moved" for those apps.
  - After the call, each app is confirmed gone from its path before being reported as moved.
- **Restore text:** "Moved to the Trash. You can Put Back."
- **In the CLI, without the app,** `finder-trash` items are shown but refused, with the same wording as other Trash items.

### Page

- **A new group** in the existing list styling, titled "Applications": "Copies and apps you haven't opened in a while. Removing moves them to the Trash."
- **Each row:** app name, version, full path with `~`, size, last opened date, and the reason note. Kept keepers sit in the group, visibly marked "Keep", with no checkbox.
- **The disk map** gets an "Applications" segment and colour, with a legend entry.
- **Nothing new is preselected.** "Select all" in this group selects only the offered rows.

## Constraints that already apply

- Tests never call the real `osascript`, `mdls`, `lsappinfo`, Finder or the Trash, and never read `/Applications`.
- Development never moves a real app. The user checks the Finder password prompt themselves (see Open questions).
- No Full Disk Access, no admin rights.

## Approaches considered

1. **Only apps the user owns** (Trash helper). Simple, but about 40% of apps on the user's Mac (12 of 31) could only be shown.
2. **Everything through Finder.** One route, but the user's own apps lose the helper's checks, and every removal needs the Finder permission.
3. **Mixed (chosen).** The helper with full checks where possible, Finder with its own password prompt only for root-owned apps.

## Calls made after the builder check (mine, for the user to check)

1. **A keeper is always kept.** A copy suggested to keep is never offered, even if it hasn't been opened in 180 days. Its row may still show "last opened Jan 2020" as information. To remove every copy, the user drags the last one to the Trash in Finder.
2. **Running and skipped copies:**
   - Running copies still count as members of their duplicate group.
   - A running copy is always the keeper. If several copies are running, the normal keeper rules pick among them.
   - Idle copies are offered with "keep the running one".
   - Safari, Labyrinth Cleaner itself and bundles without a bundle ID are not group members.
3. **The disk map block counts every scanned `.app` bundle,** running ones included, minus the skipped kinds above. No existing category covers application bundles (the Xcode category is `~/Library/Developer`), so nothing is counted twice. The "System, apps & other files" estimate shrinks by the same amount.
4. **Mixed groups:**
   - "Select all" ticks both kinds of rows.
   - Results are reported per app. Every app in a Finder request gets that request's outcome.
   - Apps the helper already moved stay reported as moved, even if a later Finder request fails or its prompt is cancelled.
5. **Order:** application moves run after Empty Trash, as other Trash moves already do, so Put Back keeps working.
6. **"Same owner" means the owner uid is unchanged since the scan.** An item's `kind` is fixed at scan time. A changed owner gives "Situation changed: scan again", never a switch of route.
7. **CLI:**
   - It lists Applications rows like the Downloads review.
   - `--yes` never includes them, because Review is never preselected.
   - Without the app it refuses both `trash` and `finder-trash` items, with the existing wording.
   - The CLI scan runs the same single `mdls` call.

## Left to the builder

- The exact `mdls` invocation and its parsing (one call for many paths).
- The version comparison details for odd strings ("2024.1 (beta)").
- The segment colour, and the exact styling of the "Keep" row.
- Whether the job runner batches `finder-trash` items into one Finder call, or calls Finder per item (the target is one prompt per clean).

## Verified by hand (2026-11-14)

The user had Finder delete a root-owned dummy app (`sudo mkdir "/Applications/Labyrinth Test.app/Contents"`) through `osascript -e 'tell application "Finder" to delete POSIX file …'`. Finder showed its own password prompt, and the bundle went to the Trash. Root-owned apps are therefore offered through `finder-trash`. No "Show in Finder" fallback is built.
