# Labyrinth Cleaner

Labyrinth Cleaner gets disk space back on a Mac. It shows what is using your disk, what you can delete, and what each delete costs you, then deletes only what you pick. In the page nothing is preselected, and nothing is ever cleaned automatically.

## Labyrinth Cleaner (Mac app)

The Mac app has a window and a menu-bar item. The window is the page described below, in its own window: scan, tick what you want back, review, clean. The menu bar shows your free disk space and refreshes every minute. It turns amber below 30 GB and red below 15 GB, and one notification fires when free space drops below 15 GB (again only after it has gone back above 20 GB). Its menu opens the window, toggles Open at Login and installs the command line tool. Nothing scans until you open the window.

The menu bar shows Finder's "available" number, in decimal GB. That number includes purgeable space macOS can free by itself. The window and the command line show strict free space in binary units, the same as `df -h`. So the menu bar usually reads a few GB higher.

## Install

1. Download `Labyrinth-Cleaner-<version>-macOS.dmg` from the [Releases page](https://github.com/labyrinth-apps/labyrinth-cleaner/releases/latest). It is signed with a Developer ID and notarized by Apple. It runs on macOS 13 or later, Apple Silicon or Intel.
2. Open the DMG and drag **Labyrinth Cleaner** onto **Applications**.
3. Open it from Applications. macOS asks once whether to open an app downloaded from the internet: choose **Open**.

The app asks for permissions only when it needs them:

- When you scan, macOS asks whether Labyrinth Cleaner may access your **Downloads** folder, and your **Documents** folder if you added a project folder there.
- The first time free space drops below 15 GB, macOS asks whether it may send **notifications**.
- The first time you choose **Empty Trash**, macOS asks whether it may control **Finder**.

Updates are checked once a day, and the app asks before installing one (see Updates below). To check the download, run `shasum -a 256 -c Labyrinth-Cleaner-<version>-macOS.dmg.sha256` in the folder with both files.

## Build and release

Build from source on a Mac with Xcode or the Command Line Tools. Every command runs from the repository root.

```sh
scripts/build_app.sh --debug
```

This makes a fast, native, ad-hoc signed debug build at `build/Labyrinth Cleaner.app`. Debug builds accept `--fixture-home DIR`, which runs the app against a fake home folder.

```sh
scripts/build_app.sh --debug --sign
```

This is the same debug build, signed with the Developer ID and the hardened runtime exactly like a release. Use it to check that signing doesn't stop Python or the Trash helper. It is not notarized and makes no DMG.

```sh
scripts/build_app.sh --no-notarize
```

This is a universal release build. It signs everything inside-out with the Developer ID and the hardened runtime, then checks the signature with `codesign` and `scripts/verify_bundle.sh --signed`. It builds the DMG and signs it, but nothing is notarized, so Gatekeeper rejects both. Don't launch a release build to test it: it ignores `--fixture-home` and scans your real home.

```sh
scripts/build_app.sh
```

This is the full release. It does everything `--no-notarize` does, and also:
- notarizes and staples the app;
- notarizes and staples the DMG;
- runs the Gatekeeper checks, including on a quarantined copy;
- writes the `.sha256`.

The result is `build/Labyrinth-Cleaner-<version>-macOS.dmg`. The version comes from `VERSION`.

```sh
scripts/build_app.sh --publish v0.1.0
```

This does the full release, then publishes it. It generates `appcast.xml` with Sparkle's `generate_appcast`, signed with the key in your login keychain. Then it uploads the DMG, the `.sha256` and `appcast.xml` to the GitHub release `v0.1.0`, creating the release or replacing its files. The tag must match `VERSION`. It refuses to run without `app/sparkle_public_key.txt` and never runs with `--no-notarize`. It never creates the repository or changes its visibility. It needs `gh auth login` once.

**The releases repository must be public before you publish.** Sparkle and the Install link download the release files without logging in, so a private repository breaks both. Make it public on GitHub yourself: the script checks with `gh repo view` and refuses anything else.

**`SUFeedURL` in `app/Info.plist` must point at the releases repository:** `https://github.com/<releases repo>/releases/latest/download/appcast.xml`. For the default that is `https://github.com/labyrinth-apps/labyrinth-cleaner/releases/latest/download/appcast.xml`.

Before building, `--publish` checks:
- that `gh` is logged in;
- that the releases repository is public;
- that the `app/Info.plist` feed points at it.

Before it uploads anything, it checks the DMG:
- the DMG is notarized and stapled;
- the app inside it carries exactly the key in `app/sparkle_public_key.txt`;
- the app inside it has the matching `SUFeedURL`.

A DMG that fails any of these is refused, so rebuild it.

`gh release create` tags the HEAD of the remote's default branch, not your local commit. When the releases repository is this checkout's `origin` and the built commit is already there (`git branch -r --contains HEAD` lists it), the script passes `--target <that commit>`. Otherwise it warns, and the tag lands on the default branch's HEAD. So push before publishing (see the step-by-step below).

The app's build number (`CFBundleVersion`, which Sparkle uses to order updates) comes from `VERSION`: major × 10000 + minor × 100 + patch, so `0.1.0` is `100` and `1.2.3` is `10203`. Minor and patch must stay below 100. `scripts/build_number.sh` prints it.

Environment variables:

- `LABYRINTH_TEAM_ID`: the Apple team (default `X8BX4XM7S2`).
- `LABYRINTH_NOTARY_PROFILE`: the notarytool keychain profile (default `labyrinth-notary`).
- `LABYRINTH_RELEASES_REPO`: where `--publish` uploads (default `labyrinth-apps/labyrinth-cleaner`). If you change it, change `SUFeedURL` in `app/Info.plist` to match and rebuild.
- `LABYRINTH_SKIP_BUILD=1`: with `--publish`, publishes the DMG already in `build/` without building it again.

One-time setup on the build Mac:

1. **The Developer ID certificate.** In Xcode, open **Settings → Accounts**, select the team, then **Manage Certificates… → + → Developer ID Application**. The script finds it by its SHA-1. This command must list it:

   ```sh
   security find-identity -v -p codesigning | grep "Developer ID Application"
   ```

2. **The notary profile.** Run this in Terminal. It asks for an app-specific password, which you create at [account.apple.com](https://account.apple.com) under Sign-In and Security.

   ```sh
   xcrun notarytool store-credentials labyrinth-notary --apple-id YOUR_APPLE_ID --team-id X8BX4XM7S2
   ```

3. **The DMG tool.** Install Homebrew Python 3.11 with `brew install python@3.11`. The script installs `dmgbuild` into `build/.dmgbuild-venv` by itself.
4. **The Sparkle key**, for publishing. See the step-by-step below and "Back up the Sparkle key".

### First release, step by step

Run these from the repository root, in Terminal, with the Mac unlocked.

1. Create the Sparkle key. It is stored in your login keychain. Then save its public half in the repository:

   ```sh
   app/.build/artifacts/sparkle/Sparkle/bin/generate_keys
   ```

   ```sh
   app/.build/artifacts/sparkle/Sparkle/bin/generate_keys -p > app/sparkle_public_key.txt
   ```

   Commit `app/sparkle_public_key.txt`:

   ```sh
   git add app/sparkle_public_key.txt
   ```

   ```sh
   git commit -m "app: Sparkle public key"
   ```

2. Back up the private key (see "Back up the Sparkle key"):

   ```sh
   app/.build/artifacts/sparkle/Sparkle/bin/generate_keys -x ~/Desktop/sparkle_private_key.txt
   ```

   Move `~/Desktop/sparkle_private_key.txt` into a password manager or onto an offline drive, then delete it from the Desktop. Never put it in the repository.

3. Make `labyrinth-apps/labyrinth-cleaner` public on GitHub.

4. Push the commit you are releasing, so the tag points at it. You can also merge it into `main` and push that.

   ```sh
   git push -u origin HEAD
   ```

5. Build the notarized release. Keep the Mac unlocked while it notarizes, because notarytool reads its password from the keychain.

   ```sh
   scripts/build_app.sh
   ```

6. Publish the DMG you just built, without building it again. The first time, `generate_appcast` may ask for access to the Sparkle key in your keychain. Choose **Always Allow**.

   ```sh
   LABYRINTH_SKIP_BUILD=1 scripts/build_app.sh --publish v0.1.0
   ```

## Updates

The app checks for a new version once a day and asks before installing it. You can also choose **Check for Updates…** from the menu-bar menu or the app menu. Updates are signed with a key only the maintainer holds, and the app refuses anything not signed with it. A build made without `app/sparkle_public_key.txt` has updates turned off, and so does any run with `--fixture-home`.

## Back up the Sparkle key

Updates are signed with a private key kept in the maintainer's login keychain. Create it once, then put the public half in the repository and commit it (step 1 of "First release, step by step"):

```sh
app/.build/artifacts/sparkle/Sparkle/bin/generate_keys
```

```sh
app/.build/artifacts/sparkle/Sparkle/bin/generate_keys -p > app/sparkle_public_key.txt
```

The tools are in `app/.build/` once `cd app && swift build` or `scripts/build_app.sh` has run. A build whose `app/sparkle_public_key.txt` doesn't look like a key (44 base64 characters) warns and leaves updates off.

Back the private key up:

```sh
app/.build/artifacts/sparkle/Sparkle/bin/generate_keys -x ~/Desktop/sparkle_private_key.txt
```

Then move `sparkle_private_key.txt` into a password manager or onto an offline or encrypted drive. Never put it in the repository. If the key is lost, installed copies can never update again: users would have to download a new app by hand.

## Command line

The same engine runs without the app: `devclean ui` opens the page in your browser, and `devclean` is the command-line version. It needs only Python 3 and has no dependencies. From the app, choose **Install Command Line Tool…** in the menu-bar menu. It links `devclean` into `~/.local/bin` (or `/usr/local/bin` when that is writable) and runs through the app's own Python. If Terminal says `devclean` isn't found, add this line to `~/.zshrc`:

```sh
export PATH="$HOME/.local/bin:$PATH"
```

From a source checkout, link the script yourself. `~/.local/bin` must be on your PATH, as above.

```sh
ln -sf "$PWD/devclean" ~/.local/bin/devclean
```

## Usage

- `devclean`: scan, toggle items by number, confirm with "yes", delete.
- `devclean --dry-run`: scan and print the exact commands. Deletes nothing.
- `devclean --yes`: delete the preselected SAFE items, no prompt.
- `devclean --dry-run --select all`: show the commands for every offered item.
- `devclean --select 1,4-6 --dry-run`: pick by number, non-interactively.
- `devclean --min-size 0`: also list items under 20 MB (hidden by default).
- `devclean --json`: the scan result as JSON, for scripts.

In the interactive picker, type numbers or ranges to toggle them (`3 5-7`). `s` selects the preselected safe items (the developer targets), `a` selects everything, `n` clears the selection, Enter continues and `q` quits. The confirmation screen lists every `rm -rf` / `xcrun` command and the total size, and nothing is deleted until you type `yes`.

Warnings print under their item, prefixed `! `. Items that move to the Trash are marked `(moves to Trash)` and are never printed as `rm` commands. The command line tool installed from the app moves them to the Trash. A source checkout has no trash helper, so there the command line refuses them (`REFUSED: use the app to move these to the Trash`); use the app or the page for those.

Without a terminal (for example in a pipe), `devclean` deletes only with `--yes`, and then only the safe tier. `--yes` is meant for scripted developer use. It deletes only the preselected developer SAFE targets (build folders and developer caches) and never touches app caches, logs, Downloads, the Trash, Docker or anything else added for general use.

## Project folders

Labyrinth Cleaner looks for Flutter and web build folders (`build`, `.dart_tool`, `node_modules`, `.next`) in your project folders.

- **Defaults**, when they exist under your home folder: `Developer`, `Projects`, `code`, `src`, `dev`, `workspace`, `git`, `repos`.
- **Settings:** folders you add are saved in `~/Library/Application Support/Labyrinth Cleaner/settings.json` as `{"project_folders": ["/absolute/path", ...]}`. The page lists them and can add or remove one. A project folder must be an existing folder inside your home folder: your home folder itself, anything in `~/Library`, symlinks, files, and the protected folders (`~/Downloads`, `~/Desktop`, `~/Pictures`, `~/Movies`, `~/Music`, `~/.ssh`, `~/.Trash`, `~/.android/avd`, Xcode Archives), anything inside them or containing them, are rejected, in any letter case (`~/downloads` is `~/Downloads`). The defaults and `--projects` go through the same check; a folder that fails it is skipped. Saved entries that aren't absolute paths are ignored.
- **`--projects DIR`:** scan this folder (repeatable), instead of the defaults plus the saved ones. For example `devclean --projects ~/Documents/Projects --projects ~/work`.

A build folder is only offered when a `pubspec.yaml` or `package.json` sits next to it, and two folders that overlap list each project once.

## Web UI (the Labyrinth Cleaner page)

`devclean ui` scans and opens the page in your browser. `devclean ui --no-open` prints the URL only.

**What the page shows:**
- how much you can get back, and how much of that costs nothing (items marked `NO COST`). Items that move to the Trash aren't counted: they free space only when the Trash is emptied.
- free space now, and after the clean, updated as you tick items
- a map of the disk
- every item grouped as *Safe*, *Costs you some time*, *Check first*, *Review: moves to Trash* or *Kept on purpose*, with what deleting it costs: `NO COST`, `~2 MIN`, or `9.9 GB DL` (a download to get it back)
- a "What happens" panel per item: what gets deleted, what it costs you, and how to get it back

Nothing is ticked when the page opens. Tick items, then press **Review & clean**. The confirmation lists every folder, split into what is deleted permanently and what is moved to the Trash, and repeats each warning. If you picked an item with a warning, Delete stays disabled until you tick "I've read the warnings above". After deleting, the page scans again and shows how much was freed.

**Safety:**
- **Local only:** the server listens on `127.0.0.1` only. Every API call needs the random token from the printed URL, and the page refuses other hosts and origins.
- **Same checks as the CLI:** the page sends item keys, never paths. Every delete goes through the same safety gate as the CLI, re-checked right before every `rm` and every move to the Trash.
- **Closing the server:** it stops after 60 minutes without use, or with Ctrl-C. Running `devclean ui` again while one is open reopens that page.
- **Test mode:** `--home DIR` and `--simctl-fixture DIR` work here too, for demos. In that mode `xcrun` is never run, so a demo can't delete a real simulator runtime.

The disk map measures only the folders devclean knows about. The rest of the disk is one grey block, "System, apps & other files". Measuring it would be slow and would trigger macOS privacy prompts. The first scan after a reboot can take a minute while the disk cache is cold. After that it takes about 15 s.

## Removing the old DevcleanBar

Earlier versions installed a separate menu-bar item, DevcleanBar. The app replaces it, and the app warns once if the old one is still running. To remove it:

```sh
devclean menubar uninstall
```

`devclean menubar install` no longer installs anything. It points you to the app.

## Tiers

| tier | preselected | `--yes` | what |
|---|---|---|---|
| **safe** | CLI only, developer targets only | developer targets only | Flutter `build/` and `.dart_tool/` (a `pubspec.yaml` must be next to them), Xcode DerivedData (kept while Xcode runs), old `~/.gradle/caches/<version>` folders, Gradle `build-cache-1`, `~/.shorebird/bin/cache`, `~/.npm/_cacache`, CocoaPods / Homebrew / ms-playwright caches. Also safe, but never preselected: caches of apps it can name (a bundle-ID folder such as `com.spotify.client`, or a known browser) and old logs. |
| **opt-in** | no | never | `node_modules` and `.next` in web projects (you run `npm install` again), the Docker build cache. |
| **caution** | no | never | iOS DeviceSupport builds, the newest Gradle version (older ones are safe), simulator runtimes, single simulators (`xcrun simctl delete <udid>`), Android system images, unavailable simulators, unused Docker images, emptying the Trash, app caches whose app it can't name ("macOS or a background tool may use this cache."). |
| **review** | no | never | Old installers and large untouched files in `~/Downloads`, and Xcode archives. These are only ever **moved to the Trash** (Put Back works), never deleted with `rm`. The app and the command line tool installed from it do this; a source checkout's command line refuses. |
| **keep** | - | - | Shown for information only and never offered, with the reason: for example an app's cache while the app is open, or iOS device backups (size only). |

**Warnings replaced most blocks.** Items that used to be hidden, such as a simulator runtime another simulator needs, the newest Gradle version, or an Android image an emulator uses, are now offered under *caution* with a red warning that says what breaks. The app and the page never preselect them, and the warning is repeated in the confirmation. Only things in use right now, and things that can't be done safely, stay blocked.

## What it can clean

- **Developer:** build folders and caches for Flutter, web, Xcode, Gradle, npm, CocoaPods, Homebrew, Playwright and Shorebird; simulator runtimes and single simulators; Android system images; DeviceSupport builds; Xcode archives (review tier). `xcrun` runs only when Xcode or its command-line tools are installed, so a Mac without them never sees the "install developer tools" prompt.
- **App caches:** top-level folders in `~/Library/Caches`, except macOS's own (`com.apple.*` and daemon caches such as `CloudKit`, `GeoServices` or `SiriTTS`) and this app's own. A cache is shown but not selectable while its app is running ("Quit *App* first"). A cache whose app can't be named is offered under *Check first* with a warning.
- **Logs and crash reports:** entries in `~/Library/Logs` and `~/Library/Logs/DiagnosticReports` older than 7 days.
- **Docker:** the build cache and unused images, only when Docker is installed and answers within 3 seconds. Containers and volumes are never pruned.
- **Trash:** its size, and "Empty Trash" through Finder. When you clean, emptying the Trash runs first, so it can't remove Downloads files moved there in the same clean.
- **Downloads (review):** installers (`.dmg`, `.pkg`, `.mpkg`, `.xip`, and `.zip` over 50 MB) older than 14 days, and files over 500 MB not opened for 90 days. They are moved to the Trash.
- **iOS device backups:** size only. Manage them in Finder.

**Never touched:** system folders, Mail and Messages, personal files outside the Downloads review list, uninstalling apps, removing languages from apps, and "memory cleaning". The app never asks for Full Disk Access; anything it can't read is skipped and the page says so.

## Protection rules

**Never deleted with `rm`.** These checks run again right before every deletion, not only during the scan:
- `~/Library/Developer/Xcode/Archives`, `~/Downloads`, `~/.Trash`, `~/Desktop`, `~/Pictures`, `~/Movies`, `~/Music`, `~/.ssh`, `~/.android/avd`, and any folder that *contains* one of these (for example `~/Library/Developer/Xcode`). Archives and Downloads files can only be moved to the Trash.
- `$HOME`, `/`, symlinks, and anything outside `$HOME`.
- Everything under `~/Documents`, except folders named `build`, `.dart_tool`, `node_modules` or `.next` that sit inside one of your project folders next to a `pubspec.yaml` (for `build` and `.dart_tool`) or a `package.json` (for `node_modules` and `.next`). A web project's `build/` is never touched.
- Files moved to the Trash must be regular files (not folders or symlinks) directly in `~/Downloads` or one level down, must still match their rule when re-checked just before the move, and must be the same file the scan saw (same inode, size and modification time). A file written to since it was last opened counts as used then.

**You choose; Labyrinth Cleaner warns.** Items that might break something are offered, never preselected, with a red warning that says what breaks:
- an Android system image an emulator uses (the warning names the emulator); if the emulators' `config.ini` files can't be read, every image warns that it may be in use;
- the newest Gradle version, and the newest DeviceSupport build per device model;
- Xcode archives (you need them to read crash reports for shipped versions).

**Simulator runtimes.** Every installed runtime is offered, and its warning says what stops working:
1. When no other installed runtime supports a simulator's device type: "Your *device* simulator stops working. No other installed runtime runs it."
2. When another runtime does: "Your *device* simulator stops working. Xcode can create a new one on iOS *x.y*." naming the newest other runtime that does.
3. The newest *general* iOS runtime (one that supports more than one device type) also says "This is your newest iOS runtime. New simulators need it."

Still never offered: a runtime with a running simulator, a running simulator, DerivedData while Xcode is running ("Quit Xcode first"), an app's cache while the app runs, and a disk image that can't be matched to a runtime. Runtimes are deleted with `xcrun simctl runtime delete <disk-image-id>`. Docker commands are limited to exactly the two prune commands, and the Trash is emptied only through Finder.

**The gate re-checks.** Right before each deletion the item is scanned again from fresh data (`simctl`, the AVD configs, the running apps, the folders). It is refused if it is gone, now in use, its path or command changed, or its warning now says something you didn't see ("Situation changed: scan again"). A warning that only got shorter, say because you deleted a simulator before its runtime, is fine.

## Layout

- `devclean`: the CLI (table, picker, confirmation).
- `devclean_general.py`: the general categories (app caches, logs, Trash, backups, Docker). The Downloads review list and Xcode archives live in `devclean_core.py`.
- `devclean_core.py`: scan, protection rules, the safety gate, and the cost tag and cost line for each item. It has no terminal I/O.
- `devclean_report.py`: turns a scan into what the page shows: rows, groups, the disk map and the headline.
- `devclean_ui.py` and `ui/index.html`: the local web server and the page. The page is a single HTML file with no build step.
- `app/`: the Mac app (a Swift package): the window, the menu-bar item, the Trash helper, and `LabyrinthKit` with the logic that is tested.
- `devclean-launcher`: the command-line entry point inside the app. It runs the app's own Python.
- `scripts/build_app.sh`: builds, signs, notarizes and publishes the app and its DMG (see Build and release). `scripts/verify_bundle.sh` checks a built bundle: universal binaries, signatures and the embedded Python. Its `--rosetta` option also runs the Intel half of Python under Rosetta; on macOS 27 that makes the system show "App Update Required" for the app, so it is off by default.
- `app/LabyrinthCleaner.entitlements`: the app's only entitlement, Apple Events (Empty Trash asks Finder).
- `devclean_menubar.py`: removes the old DevcleanBar (`devclean menubar uninstall`).
- `tests/`: fixture tests. Every test builds a fake `$HOME` in a temp dir, and simulator data comes from JSON fixtures (`tests/fixtures/real_2026-11-11/` is a real capture from this Mac). The tests never call the real `xcrun`.

```sh
python3 -m unittest discover -s tests -v
```

The app's tests:

```sh
cd app && swift test
```

Testing flags: `--home DIR` treats DIR as `$HOME` and turns off real simulator scanning. `--simctl-fixture DIR` reads `simctl_list.json` and `runtime_list.json` from DIR.

## If less space is freed than expected

APFS can release space lazily, and Time Machine local snapshots can hold on to deleted data. Check with `tmutil listlocalsnapshots /`.

## License

MIT. See [LICENSE](LICENSE). Labyrinth Cleaner and devclean delete folders permanently (Downloads files and archives go to the Trash), and they come with no warranty. Use `--dry-run` or the review screen first.
