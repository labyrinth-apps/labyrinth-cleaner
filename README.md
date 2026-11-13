# Labyrinth Cleaner

Labyrinth Cleaner gets disk space back on a Mac. It shows what is using your disk, what you can delete, and what each delete costs you, then deletes only what you pick. In the page nothing is preselected, and nothing is ever cleaned automatically.

**The Mac app is coming soon** (a signed, notarized download). Until then, the same engine runs from this repository: `devclean ui` opens the app's page in your browser, and `devclean` is the command-line version. It needs only Python 3 and has no dependencies. The command-line tool is still called `devclean`.

## Install

```sh
ln -sf "$PWD/devclean" ~/.local/bin/devclean    # ~/.local/bin is already on PATH
```

## Usage

```sh
devclean                  # scan, toggle items by number, confirm with "yes", delete
devclean --dry-run        # scan and print the exact commands; deletes nothing
devclean --yes            # delete the preselected SAFE items, no prompt
devclean --dry-run --select all      # show the commands for every offered item
devclean --select 1,4-6 --dry-run    # pick by number, non-interactively
devclean --min-size 0     # also list items under 20 MB (hidden by default)
devclean --json           # scan result as JSON (for scripts / a future menu-bar app)
```

In the interactive picker, type numbers or ranges to toggle them (`3 5-7`). `s` selects the safe items only, `a` selects everything, `n` clears the selection, Enter continues and `q` quits. The confirmation screen lists every `rm -rf` / `xcrun` command and the total size, and nothing is deleted until you type `yes`.

Warnings print under their item, prefixed `! `. Items that move to the Trash are marked `(moves to Trash)` and are never printed as `rm` commands: the command line has no way to move files to the Trash, so it refuses them (`REFUSED: use the app to move these to the Trash`). Use the app or the page for those.

Without a terminal (for example in a pipe), `devclean` deletes only with `--yes`, and then only the safe tier. `--yes` is meant for scripted developer use. It deletes only the preselected developer SAFE targets (build folders and developer caches) and never touches app caches, logs, Downloads, the Trash, Docker or anything else added for general use.

## Project folders

Labyrinth Cleaner looks for Flutter and web build folders (`build`, `.dart_tool`, `node_modules`, `.next`) in your project folders.

- **Defaults**, when they exist under your home folder: `Developer`, `Projects`, `code`, `src`, `dev`, `workspace`, `git`, `repos`.
- **Settings:** folders you add are saved in `~/Library/Application Support/Labyrinth Cleaner/settings.json` as `{"project_folders": ["/absolute/path", ...]}`. The page lists them and can add or remove one. Your home folder, `~/Library`, symlinks, files and system folders are rejected as project folders.
- **`--projects DIR`:** scan this folder (repeatable), instead of the defaults plus the saved ones. For example `devclean --projects ~/Documents/Projects --projects ~/work`.

A build folder is only offered when a `pubspec.yaml` or `package.json` sits next to it, and two folders that overlap list each project once.

## Web UI (the Labyrinth Cleaner page)

```sh
devclean ui               # scan, open the page in your browser
devclean ui --no-open     # print the URL only
```

**What the page shows:**
- how much you can get back, and how much of that costs nothing
- free space now, and after the clean, updated as you tick items
- a map of the disk
- every item grouped as *Safe*, *Costs you some time*, *Check first*, *Review: moves to Trash* or *Kept on purpose*, with what deleting it costs: `NO COST`, `~2 MIN`, or `9.9 GB DL` (a download to get it back)
- a "What happens" panel per item: what gets deleted, what it costs you, and how to get it back

Nothing is ticked when the page opens. Tick items, then press **Review & clean**. The confirmation lists every folder, split into what is deleted permanently and what is moved to the Trash, and repeats each warning. If you picked an item with a warning, Delete stays disabled until you tick "I've read the warnings above". After deleting, the page scans again and shows how much was freed.

**Safety:**
- **Local only:** the server listens on `127.0.0.1` only. Every API call needs the random token from the printed URL, and the page refuses other hosts and origins.
- **Same checks as the CLI:** the page sends item keys, never paths. Every delete goes through the same safety gate as the CLI, re-checked right before `rm`.
- **Closing the server:** it stops after 60 minutes without use, or with Ctrl-C. Running `devclean ui` again while one is open reopens that page.
- **Test mode:** `--home DIR` and `--simctl-fixture DIR` work here too, for demos. In that mode `xcrun` is never run, so a demo can't delete a real simulator runtime.

The disk map measures only the folders devclean knows about. The rest of the disk is one grey block, "System, apps & other files". Measuring it would be slow and would trigger macOS privacy prompts. The first scan after a reboot can take a minute while the disk cache is cold. After that it takes about 15 s.

## Menu bar

```sh
devclean menubar install     # builds ~/Applications/DevcleanBar.app and starts it at login
devclean menubar uninstall   # stops it and removes the app and its login item
```

- **The number:** free space, refreshed every minute. It's amber below 30 GB and red below 15 GB.
- **The alert:** a notification fires once when free space drops below 15 GB. It fires again only after free space has gone back above 20 GB.
- **"Clean up…":** opens the devclean page. The item itself never deletes anything.

The menu bar shows Finder's "available" number, in decimal GB. That number includes purgeable space macOS can free by itself. The page and the CLI show strict free space in binary units, the same as `df -h`. So the menu bar usually reads a few GB higher.

Building it needs `swiftc`, which comes with Xcode or the Command Line Tools.

When "Clean up…" starts a scan, macOS may ask once whether **DevcleanBar** may access your Documents and Downloads folders. The scan reads `~/Documents/Projects` and measures `~/Downloads`. The question can come back after a reinstall. If you say no, the page warns that it couldn't read the projects folder, and project build folders are missing from the list. Running `devclean ui` from Terminal uses Terminal's own access instead.

## Tiers

| tier | preselected | `--yes` | what |
|---|---|---|---|
| **safe** | CLI only, developer targets only | developer targets only | Flutter `build/` and `.dart_tool/` (a `pubspec.yaml` must be next to them), Xcode DerivedData (kept while Xcode runs), old `~/.gradle/caches/<version>` folders, Gradle `build-cache-1`, `~/.shorebird/bin/cache`, `~/.npm/_cacache`, CocoaPods / Homebrew / ms-playwright caches. Also safe, but never preselected: app caches and old logs. |
| **opt-in** | no | never | `node_modules` and `.next` in web projects (you run `npm install` again), the Docker build cache. |
| **caution** | no | never | iOS DeviceSupport builds, Gradle versions, simulator runtimes, single simulators (`xcrun simctl delete <udid>`), Android system images, unavailable simulators, unused Docker images, emptying the Trash. |
| **review** | no | never | Old installers and large untouched files in `~/Downloads`, and Xcode archives. These are only ever **moved to the Trash** (Put Back works), never deleted with `rm`. The app does this; the command line refuses. |
| **keep** | - | - | Shown for information only and never offered, with the reason: for example an app's cache while the app is open, or iOS device backups (size only). |

**Warnings replaced most blocks.** Items that used to be hidden, such as a simulator runtime another simulator needs, the newest Gradle version, or an Android image an emulator uses, are now offered under *caution* with a red warning that says what breaks. The app and the page never preselect them, and the warning is repeated in the confirmation. Only things in use right now, and things that can't be done safely, stay blocked.

## What it can clean

- **Developer:** build folders and caches for Flutter, web, Xcode, Gradle, npm, CocoaPods, Homebrew, Playwright and Shorebird; simulator runtimes and single simulators; Android system images; DeviceSupport builds; Xcode archives (review tier). Xcode tools are only queried when Xcode's command-line tools are installed.
- **App caches:** top-level folders in `~/Library/Caches`, except `com.apple.*` and this app's own. A cache is shown but not selectable while its app is running ("Quit *App* first").
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
- Files moved to the Trash must be regular files (not folders or symlinks) directly in `~/Downloads` or one level down, and must still match their rule when re-checked just before the move.

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
- `devclean_general.py`: the general categories (app caches, logs, Downloads, Trash, backups, Docker).
- `devclean_core.py`: scan, protection rules, the safety gate, and the cost tag and cost line for each item. It has no terminal I/O.
- `devclean_report.py`: turns a scan into what the page shows: rows, groups, the disk map and the headline.
- `devclean_ui.py` and `ui/index.html`: the local web server and the page. The page is a single HTML file with no build step.
- `devclean_menubar.py` and `menubar/`: the menu-bar item (Swift) and its installer.
- `tests/`: fixture tests. Every test builds a fake `$HOME` in a temp dir, and simulator data comes from JSON fixtures (`tests/fixtures/real_2026-11-11/` is a real capture from this Mac). The tests never call the real `xcrun`.

```sh
python3 -m unittest discover -s tests -v
```

Testing flags: `--home DIR` treats DIR as `$HOME` and turns off real simulator scanning. `--simctl-fixture DIR` reads `simctl_list.json` and `runtime_list.json` from DIR.

## If less space is freed than expected

APFS can release space lazily, and Time Machine local snapshots can hold on to deleted data. Check with `tmutil listlocalsnapshots /`.

## License

MIT. See [LICENSE](LICENSE). Labyrinth Cleaner and devclean delete folders permanently (Downloads files and archives go to the Trash), and they come with no warranty. Use `--dry-run` or the review screen first.
