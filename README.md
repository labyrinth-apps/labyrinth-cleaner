# devclean

Finds regenerable developer build output and caches on this Mac, shows what each one costs, and deletes the ones you pick. It needs only Python 3 and has no dependencies.

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

Without a terminal (for example in a pipe), `devclean` deletes only with `--yes`, and then only the safe tier.

## Web UI

```sh
devclean ui               # scan, open the page in your browser
devclean ui --no-open     # print the URL only
```

**What the page shows:**
- how much you can get back, and how much of that costs nothing
- free space now, and after the clean, updated as you tick items
- a map of the disk
- every item grouped as *Safe*, *Costs you some time*, *Check first* or *Kept on purpose*, with what deleting it costs: `NO COST`, `~2 MIN`, or `9.9 GB DL` (a download to get it back)

Tick items, then press **Review & clean**. The confirmation lists every folder. After deleting, the page scans again and shows how much was freed.

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
| **safe** | yes | yes | Flutter `build/` and `.dart_tool/` (a `pubspec.yaml` must be next to them), Xcode DerivedData (not preselected while Xcode runs), old `~/.gradle/caches/<version>` folders (the newest version is kept), Gradle `build-cache-1`, `~/.shorebird/bin/cache`, `~/.npm/_cacache`, CocoaPods / Homebrew / ms-playwright caches |
| **opt-in** | no | never | `node_modules` and `.next` in web projects. You have to run `npm install` again afterwards. |
| **caution** | no | never | older iOS DeviceSupport builds (the newest per device model is kept), simulator runtimes that pass the protection rule, Android system images that no AVD uses, `xcrun simctl delete unavailable` |
| **keep** | - | - | Shown for information only and never offered. The reason is in the note column. |

## Protection rules

**Never deleted.** These checks run again right before every `rm`, not only during the scan:
- `~/Library/Developer/Xcode/Archives`, `~/Downloads`, `~/Desktop`, `~/Pictures`, `~/Movies`, `~/Music`, `~/.ssh`, `~/.android/avd`, and any folder that *contains* one of these (for example `~/Library/Developer/Xcode`).
- `$HOME`, `/`, symlinks, and anything outside `$HOME`.
- Everything under `~/Documents`, except folders named `build`, `.dart_tool`, `node_modules` or `.next` that sit next to a `pubspec.yaml` (for `build` and `.dart_tool`) or a `package.json` (for `node_modules` and `.next`). A web project's `build/` is never touched.
- Android system images used by any AVD. Pixel_10_Pro and Television_4K are hard-coded: if their `config.ini` can't be read, no image is offered at all.

**Simulator runtimes.** On 2026-11-02 the iOS 27.0 runtime was deleted because 27.1 looked like its replacement. But 27.1 only ran the iPhone Duo, so the iPhone 18 Pro simulator disappeared. Since then a runtime is kept if any of these is true:
1. It is the newest runtime (or the only one) that supports the device type of an existing simulator. If several runtimes tie for newest, all of them are kept.
2. A simulator on it is booted.
3. It is the newest *general* runtime (one that supports more than one device type) for `iPhone-18-Pro`. This holds even when no 18 Pro simulator exists. `DEFAULT_PINNED_DEVICE_TYPES` in `devclean_core.py` sets this.

Runtimes are deleted with `xcrun simctl runtime delete <disk-image-id>`. A disk image that can't be matched to a runtime is never offered. Before each deletion the rule is checked again against fresh `simctl` output.

## Layout

- `devclean`: the CLI (table, picker, confirmation).
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
