# Applications Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** List duplicate copies of applications and apps not opened in 180 days, suggest which copy to keep, and move the ones the user ticks to the Trash. Apps the user owns go through the Trash helper; root-owned apps go through Finder, which shows its own password prompt.

**Architecture:**
- **New module:** `devclean_apps.py` holds discovery (Info.plist plus one `mdls` call), the keeper rules, item building and the Finder batch.
- **Core changes:** `devclean_core` gains config and env hooks, a scan step, a rescan entry, a gate branch and a `finder-trash` kind.
- **Swift:** the Swift Trash helper's allow-list grows to cover application bundles.
- **Report and page:** the report puts every "Applications" item in a new `apps` group and adds an `apps` disk segment. The page renders both.

**Tech Stack:** Python 3 stdlib (`plistlib`, `subprocess`), unittest; Swift 5 / XCTest (`app/`); vanilla JS in `ui/index.html`, with the node page harness in `tests/test_ui_server.py`.

**Spec:** `docs/superpowers/specs/2026-11-14-applications-cleanup-design.md` (approved 2026-11-14). Read it first, including "Calls made after the builder check" and "Verified by hand".

## Global Constraints

- **Removal rules:**
  - Every Applications item is tier `REVIEW` with `preselect=False`. Nothing is ever preselected, and `--yes` never includes these items.
  - A suggested keeper is never offered: it is tier `PROTECTED`, kind `info`.
  - Apps only ever go to the Trash, never `rm`. Labyrinth Cleaner never asks for, sees or stores a password, and never requests admin rights.
- **Never listed or grouped:**
  - Safari (`com.apple.Safari`)
  - Labyrinth Cleaner itself (`com.labyrinth-apps.cleaner`)
  - bundles with no bundle ID
  - symlinked bundles
  - bundles inside other bundles
  - anything under `/System`
- **Thresholds:**
  - "Not opened in a long time" means `APP_UNUSED_DAYS = 180`.
  - "Intel only" means the architectures include `x86_64` and not `arm64`. The note appears only when the Mac is Apple Silicon, and only on rows already listed for another reason.
- **Removal routes:**
  - Owned by the current uid: kind `trash` (the helper).
  - Any other owner: kind `finder-trash`.
  - The kind is fixed at scan time. A changed owner, bundle ID, version or inode gives `SITUATION_CHANGED` ("Situation changed: scan again").
- **Tests never touch the real Mac:** no real `osascript`, `mdls`, `lsappinfo`, `sysctl`, Finder or Trash, no reading `/Applications`, no `SMAppService`, no real defaults. Development never moves a real app.
- **Fixture homes never see the real `/Applications`:** fixture homes use `<home>/.fixture-root/Applications` and `<home>/Applications`.
- **Exact copy:**
  - Group title: "Applications"
  - Group description: "Copies and apps you haven't opened in a while. Removing moves them to the Trash."
  - Intel note: "Intel only, stops working in macOS 28"
  - Restore: `core.DOWNLOAD_RESTORE` ("Moved to the Trash. You can Put Back.")
  - Cancelled prompt: "Cancelled: nothing was moved"
- **Commits** end with:
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01U65UcZjQqUz3mkd3jHk55q
  ```
- **Test commands:**
  - Python: `python3 -m unittest discover -s tests` must stay green.
  - Swift: `swift test --package-path app`.

## Review Focus

1. **App names with spaces, parentheses, quotes or non-ASCII** (`Figma (v88.1).app`, `Café "Beta".app`) must survive every route: scan keys, helper argv, and the Finder batch. The Finder batch passes paths as `osascript` arguments, never spliced into AppleScript source. Covered by tests in Tasks 1, 2 and 4.
2. **A broken, binary or missing `Info.plist`** must be skipped silently, and the scan must still finish (Task 2).
3. **Spotlight unavailable or partial**, meaning `mdls` fails or returns `(null)`: the app is never listed for age on that basis, and its size falls back to `du`. Covered by Tasks 2 and 3.
4. **A symlinked app, a symlinked vendor subfolder, or an app nested deeper than one subfolder** is never offered, by the scanner (Task 2), the Python gate (Task 4) or the Swift helper (Task 1).
5. **The user cancels Finder's password prompt, or the Finder call fails mid-clean:** every Finder item reports the failure, and apps the helper already moved stay reported as moved (Task 4).

---

### Task 1: Trash helper accepts application bundles

**Files:**
- Modify: `app/Sources/LabyrinthKit/TrashCheck.swift` (`TrashTarget`, `checkTrashable`, `runTrashHelper`)
- Test: `app/Tests/LabyrinthKitTests/TrashCheckTests.swift`

**Interfaces:**
- Produces: `TrashTarget.application`.
- `checkTrashable(path:home:isRealHome:uid:)`: the new parameters are `isRealHome: Bool` and `uid: uid_t = getuid()`.
- `runTrashHelper` passes `isRealHome: home == realHome`.
- Python (Task 4) calls the helper unchanged: `[helper, "--home", home, path]`.

**Allowed application paths** (every component checked with `lstat`, no symlinks, final `realpath == path`; existing checks unchanged):

| home | allowed roots | depth below root |
|---|---|---|
| real home | `/Applications`, `<home>/Applications` | `<name>.app` or `<folder>/<name>.app` |
| fixture home | `<home>/.fixture-root/Applications`, `<home>/Applications` | same |

The last component ends in `.app` and is a directory, and its owner uid must equal `uid`. Anything else under those roots is refused with "not an application bundle: <path>".

- [ ] **Step 1: Write the failing tests** in `TrashCheckTests.swift`, using the existing temp-dir helpers there:
  - `testApplicationInHomeApplications`: `H/Applications/Figma (v88.1).app` gives `.success(.application)`.
  - `testApplicationOneFolderDeep`: `H/Applications/Vendor/Tool.app` gives `.success(.application)`.
  - `testApplicationTwoFoldersDeepRefused`: `H/Applications/a/b/Tool.app` gives a failure.
  - `testApplicationSymlinkRefused` and `testApplicationInSymlinkedFolderRefused`.
  - `testApplicationNotDotAppRefused` (`H/Applications/Tool`) and `testApplicationFileRefused` (a regular file named `X.app`).
  - `testApplicationOtherOwnerRefused`: pass `uid: getuid() + 1`, which gives failure "not owned by you".
  - `testFixtureRootOnlyForFixtureHomes`: `H/.fixture-root/Applications/A.app` succeeds with `isRealHome: false` and fails with `isRealHome: true`.
  - `testSystemApplicationsOnlyForRealHome`: calling `checkTrashable(path: "/Applications/Nope.app", home: H, isRealHome: false)` fails without touching the disk.
  - Existing Downloads and Archive tests keep passing with `isRealHome: false`.
- [ ] **Step 2: Run** `swift test --package-path app --filter TrashCheckTests`. Expected: the new tests fail (no `.application`, no `isRealHome`).
- [ ] **Step 3: Implement.** Paths under `/Applications` (real home only) have no home prefix: walk from `/`. Reuse the byte-wise `cleanComponents` and `lstatMode`. Read the owner uid with `lstat` on the final component.
- [ ] **Step 4: Run** `swift test --package-path app`. Expected: all pass (75 plus the new ones).
- [ ] **Step 5: Commit** with message "labyrinth-trash: allow application bundles (owned by the user) in the Applications folders".

---

### Task 2: Discover application bundles and their metadata

**Files:**
- Create: `devclean_apps.py`
- Modify: `devclean_core.py` (`Config.app_roots`, `Env.app_metadata`, `Env.apple_silicon`, `Env.user_uid`, `Env.running_app_paths`, `Env.real`), `devclean_general.py` (`parse_running_bundle_paths`, `running_bundle_paths`)
- Test: `tests/test_apps.py` (new)

**Interfaces:**
- `Config.app_roots: Optional[tuple] = None`. `__post_init__` sets it as follows:
  - real home: `("/Applications", cfg.p("Applications"))`
  - otherwise: `(cfg.p(".fixture-root/Applications"), cfg.p("Applications"))`
  - The real-home test is the same one `default_env` uses.
- `Env.app_metadata: Callable[[list], dict] = lambda paths: {}` maps each path to `{"last_used": float|None, "size": int|None, "archs": list|None}`. A missing path or field means unknown. The real implementation is `devclean_apps.mdls_metadata`.
- `Env.apple_silicon: Callable[[], bool] = lambda: False`. The real one reads `sysctl -n hw.optional.arm64` and returns `== "1"`.
- `Env.user_uid: Callable[[], int] = os.getuid`.
- `Env.running_app_paths: Callable[[], set] = lambda: set()`. These are the outermost `.app` paths of running processes. The real implementation is `devclean_general.running_bundle_paths()`: it runs `lsappinfo list` and parses it with `parse_running_bundle_paths(text) -> set`, taking each `executable path` up to its first `.app`. It raises on failure, like `running_apps`.
- `devclean_apps.AppInfo`, a dataclass with: `path, name, bundle_id, version, build, uid, ino, mtime, last_used, size, archs`.
- `devclean_apps.find_apps(cfg, env) -> list[AppInfo]`.
- `devclean_apps.parse_mdls(text: str, paths: list) -> dict`, the parser behind `mdls_metadata`.

- [ ] **Step 1: Write the failing tests** in `tests/test_apps.py`. Build fixture homes with `tempfile.mkdtemp` and `os.path.realpath`, and write `Contents/Info.plist` with `plistlib`.
  - `test_finds_apps_in_both_roots_and_one_subfolder`: four apps, at `R/A.app`, `R/Vendor/B.app`, `H/Applications/C.app` and `R/x/y/D.app`. `D` is not found. Here `R = H/.fixture-root/Applications`.
  - `test_skips_symlinks_nested_bundles_and_bad_plists`:
    - not found: a symlinked app, an app in a symlinked folder, `A.app/Contents/Helpers/H.app`
    - not found: a binary plist without `CFBundleIdentifier`, a corrupt plist, a missing plist
    - not found: Safari (`com.apple.Safari`) and `com.labyrinth-apps.cleaner`
    - a binary plist *with* an ID is found
  - `test_names_and_versions`: `Figma (v88.1).app` gives `name == "Figma (v88.1)"`, plus `version` and `build` from the plist.
  - `test_metadata_from_env_and_du_fallback`:
    - `Env.fake(app_metadata=lambda ps: {ps[0]: {"last_used": 1.0, "size": 5, "archs": ["x86_64"]}})` is used for the first app.
    - The second app gets `size` from `core.du_bytes`, with `last_used is None` and `archs is None`.
    - `app_metadata` is called once, with all paths.
  - `test_parse_mdls`: three paths and an `mdls -name kMDItemLastUsedDate -name kMDItemPhysicalSize -name kMDItemExecutableArchitectures` style output, including `(null)` values and a multi-line architecture list. The parsed dict has the right floats, ints and lists. Garbage text gives `{}`.
  - `test_parse_running_bundle_paths`:
    - a Dia helper path `/Applications/Dia.app/Contents/Frameworks/…/Helper.app/Contents/MacOS/x` gives `/Applications/Dia.app`
    - a plain `/Applications/Figma (v88.1).app/Contents/MacOS/Figma` gives its bundle
    - a non-app executable (`/usr/libexec/x`) is ignored
  - `test_fixture_config_never_points_at_system_applications`: `Config(home=tmp).app_roots` contains no path outside `tmp`.
- [ ] **Step 2: Run** `python3 -m unittest tests.test_apps -v`. Expected: ImportError or failures.
- [ ] **Step 3: Implement.**
  - **`find_apps`:** `os.scandir` over each root and its direct non-symlink subfolders. A subfolder that itself ends in `.app` is an app, not a folder.
  - **`mdls_metadata(paths)`:** one `subprocess.run(["mdls", "-name", ..., *paths], timeout=60)`. Parse the per-file blocks in path order. On any failure, return `{}`.
- [ ] **Step 4: Run** `python3 -m unittest discover -s tests`. Expected: all pass.
- [ ] **Step 5: Commit** with message "Apps: find application bundles with their plist and Spotlight metadata".

---

### Task 3: Keepers, reasons and Applications items in the scan

**Files:**
- Modify: `devclean_apps.py`, `devclean_core.py` (`scan`, `ScanResult.app_bytes`, `RESCANNERS["app:"]`, `explain_for`/`cost_for` for the new kind)
- Test: `tests/test_apps.py`

**Interfaces:**
- Consumes: `find_apps`, `AppInfo` (Task 2), and `env.running_app_paths()` (Task 2). A copy is running when its path is in that set. If the set can't be read (it raises), `scan_apps` fails closed: it offers no Applications items and adds the warning `"couldn't list running apps; applications are not offered"`.
- `devclean_apps.pick_keeper(group: list[AppInfo], running_paths: set) -> AppInfo`.
- `devclean_apps.scan_apps(cfg, env, now) -> tuple[list[Item], int]`: the items, and the total bytes of every grouped app.
- `ScanResult.app_bytes: int = 0`.
- **Item fields:**
  - `key="app:" + path`, `category="Applications"`, `label=info.name`, `path=info.path`, `size`, `mtime=info.last_used or info.mtime`
  - offered rows: `kind` is `"trash"` or `"finder-trash"`, `tier=REVIEW`
  - keepers and running rows: `kind="info"`, `tier=PROTECTED`
  - `meta={"bundle_id", "version", "uid", "ino"}`
  - `note` is the reason text below

**Keeper rules** (spec, decision 3), applied in order:
1. A copy running now.
2. The highest `version_key(version)`, then the highest `version_key(build)`.
3. A name without a ` (` suffix.
4. A root of `/Applications` (`cfg.app_roots[0]`) directly, over its subfolders and `~/Applications`.
5. The newest `last_used`.

**Exact notes** (`name` is the keeper's name, `v` the keeper's version, `~path` the keeper's path with `~`):

| row | note |
|---|---|
| keeper, not running | `Keep: newest version` |
| keeper, running | `Keep: running now` |
| other copy, keeper version higher | `older copy · keep {name} {v}` |
| other copy, same version | `second copy of {name} · keep {~path}` |
| other copy, keeper is running | `copy of {name} · keep the running one` |
| unused, no copies | `last opened {Mon YYYY}`, or `no record of being opened` when `last_used` is None and mtime is over 180 days old |
| running and would be listed | `Quit {name} first` (PROTECTED, info) |

Joining rules:
- Age and the Intel note are appended with ` · ` after the duplicate reason.
- An app with `last_used` None and mtime under 180 days is not listed for age.
- A keeper never gets an age reason.

- [ ] **Step 1: Write the failing tests:**
  - `test_figma_case`: three Figma copies with versions `88.1`, `101.6` and `101.6`; the second is named `Figma (v101.6)`. The keeper is the `101.6` copy named `Figma`. The other notes are `"older copy · keep Figma 101.6"` and `"second copy of Figma · keep ~/…/Figma.app"`, both REVIEW, `preselect` False.
  - `test_running_copy_is_kept`: `env.running_app_paths` returns the older copy's path. That copy is kept with "Keep: running now". The newer copy gets "copy of … · keep the running one".
  - `test_unused_threshold_and_null_last_used`:
    - last used 181 days ago: listed, "last opened Mon YYYY"
    - 179 days: not listed
    - None with a recent mtime: not listed
    - None with an old mtime: listed, "no record of being opened"
  - `test_intel_note_only_on_apple_silicon_and_listed_rows`: an old Intel app has the note when `apple_silicon` is True and not when False. A recent Intel app is not listed.
  - `test_running_apps_unreadable_offers_nothing`: `running_app_paths` raises. There are no REVIEW Applications items, and the warning is present.
  - `test_kind_by_owner`: `Env.fake(user_uid=lambda: os.getuid() + 1)` makes every offered row `finder-trash`.
  - `test_keeper_never_offered_even_if_old`.
  - `test_app_bytes_counts_every_grouped_app`: the sum of all app sizes, keepers included.
  - `test_scan_includes_apps_and_sets_app_bytes` through `core.scan` with a fixture `Config` and `Env.fake`.
  - `test_rescan_finds_app_by_key`: `core.rescan_item` returns the same key.
- [ ] **Step 2: Run** `python3 -m unittest tests.test_apps -v`. Expected: the new tests fail.
- [ ] **Step 3: Implement.**
  - **`explain_for`:** `finder-trash` behaves like `trash` (deletes the path with `~`, restore is `DOWNLOAD_RESTORE`).
  - **`cost_for`:** same as `trash`.
- [ ] **Step 4: Run** `python3 -m unittest discover -s tests`. Expected: all pass.
- [ ] **Step 5: Commit** with message "Apps: keeper rules, reasons and Applications items in the scan".

---

### Task 4: Gate and removal (helper route and Finder batch)

**Files:**
- Modify: `devclean_core.py` (`validate_item`/`validate_trash` app branch, `execute_item`, `execution_order`, new `execute_finder_batch`, `FINDER_TRASH_ARGV_HEAD`)
- Modify: `devclean_ui.py` (`_run_job` batches `finder-trash`)
- Modify: `devclean` (CLI listing and refusal, as for `trash`)
- Test: `tests/test_apps.py`, `tests/test_ui_server.py`

**Interfaces:**
- **App gate**, the app branch inside `validate_trash`:
  - The path is a clean absolute path ending in `.app`, a directory, not a symlink at any level, and `realpath == path`.
  - It is directly in one of `cfg.app_roots`, or one subfolder below one.
  - A fresh `os.lstat` gives `(st_uid, st_ino)` equal to `meta`, and the fresh rescan's `meta["bundle_id"]` and `meta["version"]` are equal to the item's. Otherwise: `UnsafeTarget(SITUATION_CHANGED)`.
  - `validate_item` accepts kind `finder-trash` through the same branch.
- `execute_finder_batch(items, cfg, runner=subprocess.run, env=None, now=None) -> list[tuple[bool, str]]`. It returns one result per item, in order:
  - Each item is validated first. Refused items get `(False, "REFUSED: …")` and are left out of the call.
  - The rest go in one call: `argv = FINDER_TRASH_ARGV_HEAD + ["--", *paths]`. `FINDER_TRASH_ARGV_HEAD` is an `osascript` `on run argv` script that runs `tell application "Finder" to delete` on each `POSIX file` item, inside `with timeout of 3600 seconds`. Paths are passed as arguments only.
  - A non-zero exit maps to:
    - `-128`: "Cancelled: nothing was moved"
    - `-1743`: `FINDER_APPS_DENIED` = "macOS didn't let Labyrinth Cleaner ask Finder to move apps to the Trash. Allow it in System Settings → Privacy & Security → Automation, then try again."
    - `-1712`: "Finder took too long. Scan again to see what moved."
    - otherwise: the stderr text
  - After the call, each path that no longer exists (`os.path.lexists` is False) is `(True, "ok")`. A path still there is `(False, <mapped error>)`, or `(False, "Still in place: <path>")` if the call returned 0.
- `execute_item` for a `finder-trash` item:
  - No helper (CLI without the app): `(False, TRASH_REFUSAL)`.
  - `runner is fixture_runner`: `(False, "REFUSED: fixture mode never runs osascript")`.
  - Otherwise it calls `execute_finder_batch([it], …)[0]`.
- `execution_order`: `head()` gives `finder-trash` 3. They run last, after `empty-trash` and after helper moves.
- `UIServer._run_job`: on reaching the first pending `finder-trash` entry (if not cancelled), it runs `execute_finder_batch` once for all remaining `finder-trash` items. Each entry's status is `"trashed"` on success and `failed`/`refused` otherwise, as for `trash` today.

- [ ] **Step 1: Write the failing tests:**
  - `test_gate_refuses_changed_owner_inode_bundle_or_version`: change each one after the scan; each gives `SITUATION_CHANGED`.
  - `test_gate_refuses_symlink_and_too_deep`.
  - `test_helper_route_for_owned_app`: `LABYRINTH_TRASH_HELPER` points at a fake script that records its argv and moves into `H/.Trash`. The argv is `[helper, "--home", H, path]`, the result is `(True, "ok")`, and the app has moved.
  - `test_finder_batch_one_call_args_not_source`: a fake runner records argv, and two apps (one named `Café "Beta".app`) both appear only after `"--"`, with exactly one runner call. The runner deletes them, which gives `[(True, "ok"), (True, "ok")]`.
  - `test_finder_batch_cancelled_maps_message`: the runner returns 1 with stderr `execution error: User canceled. (-128)`. Both items give `(False, "Cancelled: nothing was moved")`, and the apps are still there.
  - `test_finder_batch_partial`: the runner deletes one of two. That gives `[(True, …), (False, "Still in place: …")]`.
  - `test_finder_trash_refused_without_helper_and_in_fixture_mode`.
  - `test_execution_order_finder_last`: `[finder, empty-trash, trash]` orders as `[empty-trash, trash, finder]`.
  - In `tests/test_ui_server.py`, `test_job_batches_finder_items_and_keeps_helper_results`:
    - The job has one helper app and two Finder apps, and a fake runner returns -128.
    - The helper entry is `trashed`, and both Finder entries are `failed` with "Cancelled: nothing was moved".
    - The runner was called once for Finder.
- [ ] **Step 2: Run** `python3 -m unittest tests.test_apps tests.test_ui_server -v`. Expected: the new tests fail.
- [ ] **Step 3: Implement.** In the CLI (`devclean`), the existing `kind == "trash"` checks become `kind in ("trash", "finder-trash")`: the "(moves to Trash)" tag, leaving them out of `commands`, and the "Moved to Trash:" section.
- [ ] **Step 4: Run** `python3 -m unittest discover -s tests`. Expected: all pass.
- [ ] **Step 5: Commit** with message "Apps: gate, helper route and one Finder request per clean for root-owned apps".

---

### Task 5: Applications group, disk segment, docs

**Files:**
- Modify: `devclean_report.py` (`segment_of`, `SEGMENTS`, `MEASURED`, `disk_map`, `build_rows`, `GROUP_ORDER`)
- Modify: `ui/index.html` (`GROUPS`, `SEG_COLOR`, `ICON`, a CSS var `--apps`, the filter chip, rows without a checkbox when `keys` is empty)
- Modify: `README.md` (what it cleans), `docs/superpowers/specs/2026-11-13-labyrinth-cleaner-app-design.md` (lines 37 and 224, as in the new spec's "Changes to the existing design")
- Test: `tests/test_report.py`, `tests/test_ui_server.py`

**Interfaces:**
- **Report segment and group:**
  - `segment_of(it)` returns `"apps"` for `category == "Applications"`.
  - `SEGMENTS` gets `("apps", "Applications")` before `other`. `MEASURED` gets `"apps"`; it is not in `ITEM_SIZED`.
  - `disk_map` sets `size["apps"] = res.app_bytes`, and reclaim is the sum of offerable app items.
  - `build_rows`: an item with `category == "Applications"` goes to group `"apps"` whatever its tier. Its row `keys` is empty when the item is not offerable. `GROUP_ORDER["apps"] = 3.5`, between review and kept.
- **Page:**
  - `GROUPS` gets `{id: "apps", title: "Applications", desc: "Copies and apps you haven't opened in a while. Removing moves them to the Trash.", color: "var(--apps)", tier: ""}`.
  - `SEG_COLOR.apps = "var(--apps)"`. `--apps` is a muted colour distinct from the existing segment colours (builder's choice).
  - `ICON.Applications = "Ax"`.
  - A filter chip "Apps".
  - A row with an empty `keys` renders no checkbox, and "Select all" in the group ticks only rows that have keys.

- [ ] **Step 1: Write the failing tests:**
  - `test_report.py::test_apps_group_and_segment`: a `ScanResult` with one REVIEW app (100 bytes), one keeper (50 bytes) and `app_bytes=150`.
    - Both rows are in group `apps`, and the keeper's `keys == []`.
    - The `apps` segment has 150 bytes and 100 reclaimable.
    - `other` shrinks by 150 compared with the same result with `app_bytes=0`.
  - `test_ui_server.py::test_page_has_applications_group`: the page has `id: "apps"`, the exact description text, `"Ax"` and `"var(--apps)"`.
  - `test_ui_server.py` node harness case: a row in group `apps` with `keys: []` renders without a `.cb` input, and "Select all" for `apps` selects only keyed rows. Extend the existing harness pattern near `test_page_*` that runs the page in node.
- [ ] **Step 2: Run** `python3 -m unittest tests.test_report tests.test_ui_server -v`. Expected: the new tests fail.
- [ ] **Step 3: Implement**, including the README and old-spec edits.
- [ ] **Step 4: Run** `python3 -m unittest discover -s tests` and `swift test --package-path app`. Expected: all pass.
- [ ] **Step 5: Check the page in a browser.**
  - Start `./devclean ui --home <tmp fixture> --no-simulators --no-open` with three Figma copies and one old app under `<tmp>/.fixture-root/Applications`, then open the URL with the browser tool.
  - The Applications group shows the keeper without a checkbox, the reasons read as specified, and the disk map has an Applications block.
  - Stop the server and delete the fixture.
- [ ] **Step 6: Commit** with message "Apps: Applications group and disk-map block; README and design updated".
