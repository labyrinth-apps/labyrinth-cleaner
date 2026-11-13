# Labyrinth Cleaner, plan 1 of 3: core and page

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task by task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** the Python core and the web page gain everything the Mac app design needs that doesn't depend on the app itself:
- project folders that aren't tied to one setup
- user choice with warnings instead of blocks
- a "review, then move to Trash" tier
- the new general categories
- per-simulator cleanup

**Architecture:**
- All decisions stay in `devclean_core.py`. The new scanners use the same `Item` model, plus `warning`, `deletes` and `restore` text.
- **One gate mechanism:** `validate_item` re-runs the item's own scanner and refuses if the item is gone, not offerable, or carries a different warning.
- **Injected providers:** outside-world queries (running apps, Spotlight last-used dates, `docker`, developer tools) come through an injectable `Env`, so tests never touch the real Mac.
- **Moving to the Trash** goes through an external helper named by an environment variable. Plan 2 ships the helper inside the app. Without it, these items are refused.

**Tech stack:** Python 3 standard library and plain HTML/CSS/JS, same as today.

**Spec:** `docs/superpowers/specs/2026-11-13-labyrinth-cleaner-app-design.md`. It builds on `2026-11-11-devclean-ui-design.md`.

**Plans 2 and 3 come later:** plan 2 is the native app (window, menu bar, folder picker, trash helper); plan 3 is packaging, signing, notarization, the DMG and Sparkle. Every task here can be tested through the CLI and `devclean ui`.

## Global constraints

- **Dependencies:** Python stdlib only.
- **No real effects in tests:** tests use fixture homes (`tempfile.mkdtemp` then `os.path.realpath`) and never call the real `xcrun`, `docker`, `osascript`, `lsappinfo`, `mdls` or Trash.
- **No real deletions** on the developer's Mac while building, ever.
- **Commits:** on a feature branch. End each commit message with:
  - `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`
  - `Claude-Session: https://claude.ai/code/session_01U65UcZjQqUz3mkd3jHk55q`
- **Tests:** run with `python3 -m unittest discover -s tests`. The suite must stay green and free of warnings after every task.
- **Never preselected by the app:** the scan payload's `preselect` is always `false`. `Item.preselect` stays in the core only for the CLI's `--yes`.
- **Never `rm` under these:** Downloads, Xcode Archives and the Trash. Downloads files and Archives are only ever moved to the Trash, through the helper.
- **Nothing personal:** no pinned device types and no protected AVD names anywhere in the code.
- **Copy (exact strings from the spec):**
  - review group: `Review: moves to Trash`
  - refusal when a warning changed: `Situation changed: scan again`
  - helper missing: `use the app to move these to the Trash`
  - acknowledgment checkbox: `I've read the warnings above`
- **Thresholds:**

  | What | Rule |
  |---|---|
  | Old installers | `.dmg` `.pkg` `.mpkg` `.xip` older than 14 days, plus `.zip` over 50 MB (`50 * 1024**2`) older than 14 days |
  | Large untouched downloads | over 500 MB (`500 * 1024**2`), last used over 90 days ago |
  | Logs | entries older than 7 days |
  | Small items | under 20 MB |
  | Docker check | timeout 3 s |
- **Default project folders** (relative to home): `Developer`, `Projects`, `code`, `src`, `dev`, `workspace`, `git`, `repos`.
- **Settings file:** `<home>/Library/Application Support/Labyrinth Cleaner/settings.json`, holding `{"project_folders": [abs paths]}`.

## Review focus

1. **A running app owns a cache whose folder name differs from its bundle ID** (for example `Caches/Google` while Chrome runs). It must not be selectable. → Task 4, `test_cache_publisher_folder_blocked_while_app_runs`.
2. **A Downloads file is opened or changed between the scan and the clean.** It no longer matches its rule, so it must be refused, not trashed. → Task 3, `test_trash_item_refused_when_rule_no_longer_matches`.
3. **The user adds home, `~/Library`, a symlink or a file as a project folder.** It must be rejected with a reason. → Task 1, `test_add_project_folder_rejects_bad_paths`.
4. **Two listed folders overlap** (`~/Projects` and `~/Projects/sub`). Each project appears once. → Task 1, `test_overlapping_roots_list_each_project_once`.
5. **Empty Trash is selected together with Downloads files.** The Trash must be emptied first, so the files just moved there survive. → Task 4, `test_empty_trash_runs_before_moves`, plus the server job-order test in Task 5.

---

## File structure

| File | Change |
|---|---|
| `devclean_core.py` | Config `project_roots` and settings; `Env` providers; `REVIEW` tier; `Item.warning/deletes/restore`; warning-based protection; simulators; trash items; app caches, logs, Trash, backups, Docker; gate re-check; execution order. If the file passes about 1400 lines, move the new general categories into `devclean_general.py`, which imports the core and is imported by `scan()`. Builder's call. |
| `devclean_report.py` | `review` group, new segments, row `warning/deletes/restore`, `preselect` always false. |
| `devclean_ui.py` | Settings API for project folders, and a rebuilt `cfg` after a change. |
| `ui/index.html` | No preselection, "Select all" per group, "What happens" panel, warnings, review group, modal sections plus the acknowledgment, project-folder list. |
| `devclean` | `--projects` repeatable, settings-aware. |
| `tests/test_devclean.py`, `tests/test_report.py`, `tests/test_ui_server.py` | Updated for the new keys and the new behaviour. |
| `tests/test_general.py` (new) | Tests for the new general categories. |
| `README.md` | Updated. |

---

### Task 1: Project folders instead of one hard-coded folder

**Files:** `devclean_core.py` (Config, find/scan_projects, `validate_path` rule 1, settings helpers), `devclean`, `devclean_ui.py` (`main` options), `tests/test_devclean.py`

**Interfaces:**
- **Produces:**
  - `DEFAULT_PROJECT_DIRS = ("Developer", "Projects", "code", "src", "dev", "workspace", "git", "repos")`
  - `Config(home: str, project_roots: Optional[tuple] = None, android_sdk: Optional[str] = None)`
    - The fields `projects`, `pinned_device_types` and `protected_avds` are removed.
    - When `project_roots` is None, it becomes the existing `DEFAULT_PROJECT_DIRS` under home, plus the settings `project_folders`.
    - Every entry is realpath'd, only existing directories are kept, duplicates are removed, and the order is stable.
  - `settings_path(home: str) -> str`
  - `load_settings(home: str) -> dict`: returns `{}` when the file is missing or broken.
  - `save_settings(home: str, data: dict) -> None`: writes atomically (temp file plus `os.replace`), mode 0600.
  - `add_project_folder(home: str, path: str) -> str`: returns the stored realpath, or raises `ValueError` with the reason.
  - `remove_project_folder(home: str, path: str) -> None`
  - **Item keys for project artifacts:** `flutter:<path relative to home>`, `web:<path relative to home>`. For example, `flutter:Documents/Projects/flutter_app/build`.
    - `Item.project` is the project folder's path relative to its root, used for display.
    - `meta["project_dir"]` stays the absolute path.
- **Rules `add_project_folder` enforces:**
  - The path must be absolute, an existing directory and not a symlink.
  - It must be inside home and not home itself.
  - It must not be inside `~/Library`, and must not be or contain any `static_deny` path. Downloads is allowed only if the user picks it; the gate still never `rm`s there.
- **`validate_path` rule 1:** an artifact is accepted if its parent is inside **any** `cfg.project_roots` entry (and isn't a root itself). The `pubspec.yaml` and `package.json` checks are unchanged, and so is "anything else under `~/Documents` is refused".

- [ ] **Step 1: Update `tests/test_devclean.py` and add the new tests**
  - `cfg_for(home)` becomes `core.Config(home=home, project_roots=(os.path.join(home, "Documents", "Projects"),))`.
  - Rewrite every literal key `flutter:<x>` and `web:<x>` in all test files to `flutter:Documents/Projects/<x>` and `web:Documents/Projects/<x>`. Report row ids follow from Task 5.

```python
class ProjectRootTests(unittest.TestCase):   # setUp/tearDown: tmp + make_home
    def test_default_roots_only_existing_conventional_dirs(self):
        os.makedirs(os.path.join(self.home, "Developer"))
        cfg = core.Config(home=self.home)
        self.assertEqual(cfg.project_roots, (os.path.join(self.home, "Developer"),))
    def test_documents_projects_not_scanned_unless_added(self):
        cfg = core.Config(home=self.home)
        res = core.scan(cfg, core.NoSimctl(), xcode_check=lambda: False, env=core.Env.fake())
        self.assertFalse([i for i in res.items if i.category in ("Flutter", "Web")])
    def test_settings_folder_is_scanned(self):
        core.add_project_folder(self.home, os.path.join(self.home, "Documents", "Projects"))
        res = core.scan(core.Config(home=self.home), core.NoSimctl(), xcode_check=lambda: False, env=core.Env.fake())
        self.assertIn("flutter:Documents/Projects/flutter_app/build", {i.key for i in res.items})
        self.assertEqual(stat.S_IMODE(os.stat(core.settings_path(self.home)).st_mode), 0o600)
    def test_add_project_folder_rejects_bad_paths(self):
        os.symlink(os.path.join(self.home, "Documents"), os.path.join(self.home, "link"))
        for bad in (self.home, os.path.join(self.home, "Library"), os.path.join(self.home, "link"),
                    os.path.join(self.home, "Documents/taxes.pdf"), "relative/path", "/etc",
                    os.path.join(self.home, "nope")):
            with self.assertRaises(ValueError, msg=bad):
                core.add_project_folder(self.home, bad)
    def test_remove_project_folder(self): ...   # add, then remove → load_settings has no such entry
    def test_overlapping_roots_list_each_project_once(self):
        P = os.path.join(self.home, "Documents", "Projects")
        cfg = core.Config(home=self.home, project_roots=(P, os.path.join(P, "labryinth-iptv")))
        keys = [i.key for i in core.scan(cfg, core.NoSimctl(), xcode_check=lambda: False, env=core.Env.fake()).items]
        self.assertEqual(len(keys), len(set(keys)))
    def test_gate_accepts_artifact_in_any_root_and_refuses_outside(self):
        dev = os.path.join(self.home, "Developer", "app")
        write(os.path.join(dev, "package.json"), "{}"); mkdir(os.path.join(dev, "node_modules"))
        cfg = core.Config(home=self.home, project_roots=(os.path.join(self.home, "Developer"),))
        self.assertTrue(core.validate_path(os.path.join(dev, "node_modules"), cfg))
        with self.assertRaises(core.UnsafeTarget):
            core.validate_path(os.path.join(self.home, "Documents/Projects/web_app/node_modules"), cfg)
    def test_cli_projects_flag_repeatable(self): ...  # run CLI with two --projects → both projects' rows in --json
```

`core.Env.fake()` comes from Task 2. To keep Task 1 self-contained, add `Env` here with only a `dev_tools` field (default `lambda: False` in `fake()`), and give `scan()` the `env: Optional[Env] = None` parameter now. Tasks 2 to 4 extend `Env`. Add `import stat` to the test file.

- [ ] **Step 2: Run the tests**

Run: `python3 -m unittest discover -s tests`

Expected: the new tests fail (`Config` has no `project_roots`, `add_project_folder` is missing).

- [ ] **Step 3: Implement the Interfaces above**
  - `find_projects` walks each root and removes duplicates by realpath across roots.
  - `scan_projects` builds keys relative to home.
  - **`devclean` and `devclean ui`:**
    - `--projects DIR` uses `action="append"` and maps to `project_roots`.
    - Without the flag, `Config` uses the defaults plus settings.
    - In `devclean_ui.main`, `options["projects"]` becomes the list of roots.

- [ ] **Step 4: Run the tests**

Run: `python3 -m unittest discover -s tests`

Expected: every test passes, with no warnings.

- [ ] **Step 5: Commit** with the message `feat(core): project folders from defaults + settings, not one hard-coded path`.

---

### Task 2: Choice, warnings and simulators

**Files:** `devclean_core.py` (Item, `runtime_protection` → warnings, `scan_runtimes`, new `scan_simulators`, `scan_gradle_versions`, `scan_device_support`, `android_protection`/`scan_android`, `scan_info` Archives, `validate_item`), `tests/test_devclean.py`

**Interfaces:**
- **New `Item` fields:**
  - `warning: str = ""`
  - `deletes: str = ""`: what gets removed (the path with `~`, or the command)
  - `restore: str = ""`: how to get it back
  - `to_dict()` includes all three.
- `Env` gains `dev_tools: Callable[[], bool]`.
  - `Env.real()` uses `xcode-select -p` (exit 0 means tools are present).
  - `Env.fake(**overrides)` makes every provider inert.
  - `scan()` takes `env: Optional[Env] = None` (default `Env.real()`). The real `SimctlProvider` is consulted only when `env.dev_tools()` is true.
- `runtime_protection(sim_list) -> (blocks, warns, warnings)`:
  - `blocks`: `{rid: [reason]}`, used only for runtimes with a **booted** simulator. Unmatched images stay blocked in `scan_runtimes`.
  - `warns`: `{rid: text}` for "only/newest for an existing sim's device type" and "newest general iOS runtime".
  - **Text, exact formats:**
    - `"Your {names} simulator stops working. No other installed runtime runs it."`, with names joined by ", ". Use "simulators" when there's more than one.
    - `"This is your newest iOS runtime. New simulators need it."`
    - When both apply, join them with a space.
- **Items with warnings** are `CAUTION`, `preselect=False`, and carry the warning:
  - **Runtimes:** offered unless blocked.
  - **Newest Gradle version:** `"The version your projects probably use. It downloads again on the next build."`
  - **Newest DeviceSupport per model:** `"Re-copied the next time you debug on that iPhone."`
  - **Android images used by an emulator:** `"The {avd names} emulator stops working until you download this image again (the SDK Manager does it)."`
  - **Any AVD config unreadable:** every image gets `"Couldn't read the settings of emulator {name}; it may use this image."`. That replaces the old "offer nothing" rule.
- `scan_simulators(cfg, sim) -> list[Item]`, one item per simulator:
  - key `simdevice:<udid>`, category `Simulator`
  - label `"{name} ({runtime version})"`
  - size: `du` of `cfg.sim_devices_dir/<udid>`
  - mtime from `lastBootedAt` if present
  - `kind="simdevice"`, argv `["xcrun","simctl","delete",udid]`, `CAUTION`
  - cost `"Deletes its installed apps and data. Xcode can create a fresh one."`
  - **Booted:** `PROTECTED`, `kind="info"`, note `"Running: shut it down first"`.
- **Xcode Archives:** `scan_info` no longer adds the single `archives` row. A new `scan_archives(cfg)` returns one item per `*.xcarchive` folder under `Archives/<date>/`:
  - key `archive:<date>/<name>`, `tier=REVIEW`, `kind="trash"`
  - warning `"Shipped builds. You need them to read crash reports for those versions."`
  - restore `"Moved to the Trash, so you can Put Back."`
  - Archives stays in `static_deny` for `rm`. Trash items have their own gate (Task 3).
- `REVIEW = "review"`. `OFFERABLE_TIERS = (SAFE, OPTIN, CAUTION, REVIEW)`. `TIER_ORDER`: safe 0, opt-in 1, caution 2, review 3, keep 4.
- `rescan_item(it, cfg, sim, env) -> Optional[Item]` re-runs the scanner that produced `it.key`, chosen by key prefix, and returns the item with the same key (or None). Use a dispatch dict: `{"runtime:": ..., "simdevice:": ..., "gradle:": ..., "devsupport:": ..., "android:": ..., "archive:": ..., ...}`. Later tasks add their prefixes.
- **`validate_item(it, cfg, sim=None, env=None)`** begins:

```python
fresh = rescan_item(it, cfg, sim, env)
if fresh is None: raise UnsafeTarget(f"{it.label} is gone")
if not fresh.offerable: raise UnsafeTarget(f"{it.label}: {fresh.note or 'not offered now'}")
if fresh.warning != it.warning: raise UnsafeTarget("Situation changed: scan again")
```

  It then runs the existing per-kind checks (exact argv, `validate_path`).
  - `simdevice` argv must equal `["xcrun","simctl","delete",udid]`.
  - `execute_item(it, cfg, sim=None, runner=subprocess.run, env=None)` passes `env` through.
- **Every offerable item gets `deletes` and `restore`:**
  - `deletes` is the path with `~`, or the command.
  - `restore` is a short phrase per category, for example "Rebuilt automatically", "Downloaded again on demand", "Re-download the runtime in Xcode › Settings › Components", or "Put Back from the Trash".
  - Put it in `cost_for`'s companion, `explain_for(it) -> tuple[str, str]`.

- [ ] **Step 1: Write the failing tests** in a new `class ChoiceAndSimulatorTests`

```python
def test_incident_fixture_offers_27_0_with_warning(self):      # incident_sim(): 27.0 offered, CAUTION, preselect False,
    # warning starts with "Your iPhone 18 Pro simulator stops working. No other installed runtime runs it."
def test_newest_ios_runtime_warning(self):                     # incident_sim(with_18pro_sim=False): 27.0 warning ==
    # "This is your newest iOS runtime. New simulators need it."  (27.1 is Duo-only, so not "general")
def test_booted_runtime_still_kept(self):                      # device state Booted → runtime PROTECTED
def test_simulator_devices_listed(self):                       # incident_sim → simdevice:P1 offered CAUTION,
    # argv ["xcrun","simctl","delete","P1"]; booted device → PROTECTED "Running: shut it down first"
def test_newest_gradle_offered_with_warning(self):             # gradle:9.10.0 CAUTION with warning
def test_newest_devicesupport_offered_with_warning(self):
def test_android_used_image_offered_with_avd_name(self):       # AndroidTests fixture: used image CAUTION,
    # warning contains the AVD name; unused image CAUTION with no warning
def test_unreadable_avd_config_warns_every_image(self):
def test_archives_are_review_trash_items(self):                # archive:2026-11-01/App.xcarchive REVIEW kind trash
def test_no_personal_constants(self):                          # "iPhone-18-Pro", "Pixel_10_Pro", "Television_4K"
    # not in open(core.__file__).read()
def test_gate_refuses_when_warning_changed(self):              # scan incident_sim(with_18pro_sim=False) → 27.0 no
    # "stops working" warning; validate with incident_sim(True) → UnsafeTarget "Situation changed: scan again"
def test_gate_refuses_in_use(self):                            # simdevice scanned Shutdown, validate with Booted
def test_every_offerable_item_explains_itself(self):           # cost, deletes, restore non-empty for all offerable
```

  Also update the existing tests whose meaning changed, and say why in each docstring:
  - `test_incident_2026_11_02_never_offers_27_0` becomes the warning test above.
  - The "unreadable protected AVD blocks all" test becomes the "warns every image" test.
  - `test_real_machine_fixture` changes too:
    - iOS 26.5 is offered with no warning.
    - The general 27.2 runtime (24B8117) stays Kept, because a booted simulator runs on it.
    - The 27.2 Duo runtime is offered with a warning naming iPhone Duo.

- [ ] **Step 2: Run the tests**

Run: `python3 -m unittest discover -s tests`

Expected: the new tests fail.

- [ ] **Step 3: Implement the Interfaces above**

- [ ] **Step 4: Run the tests**

Run: `python3 -m unittest discover -s tests`

Expected: every test passes, with no warnings.

- [ ] **Step 5: Commit** with the message `feat(core): user choice with warnings; per-simulator cleanup; archives as review items`.

---

### Task 3: Review items and moving to the Trash (Downloads)

**Files:** `devclean_core.py`, `tests/test_general.py` (new; import helpers from `test_devclean`)

**Interfaces:**
- `Env` gains `last_used: Callable[[str], Optional[float]]`.
  - `Env.real()` reads `mdls -raw -name kMDItemLastUsedDate <path>` and returns None on "(null)" or an error.
  - Callers fall back to `st_mtime`.
- `scan_downloads(cfg, env, now) -> list[Item]`. It looks at regular files, not symlinks, directly in `~/Downloads` or one level down.
  - **Old installers:** key `download:<relpath>`, category `Downloads`, `tier=REVIEW`, `kind="trash"`, `meta={"rule": "installer"}`.
  - **Large untouched files:** the same, with `meta={"rule": "large"}`.
  - **Matches both:** the file shows once, as an installer.
  - **Fields:** size is `st_size`, mtime is the last-used time, and cost/restore are `"Moved to the Trash. You can Put Back."`.
- `scan()` adds `scan_downloads` and `scan_archives` (Task 2). The deny rule for Downloads under `rm` is unchanged.
- **The trash gate,** `validate_trash(it, cfg, env, now)`, runs inside `validate_item` for `kind == "trash"` after the `rescan_item` check:
  - **Downloads items:** the path is a regular file (not a symlink, not a folder), its `realpath` equals the path, and it's inside `~/Downloads` at most two levels deep.
  - **Archives items:** the path is a folder ending in `.xcarchive`, exactly at `Archives/<date>/<name>.xcarchive`.
- `TRASH_HELPER_ENV = "LABYRINTH_TRASH_HELPER"`.
  - `execute_item` for `kind == "trash"`: if the variable is unset, or not an executable file, it returns `(False, "REFUSED: use the app to move these to the Trash")`.
  - Otherwise it calls `runner([helper, path], capture_output=True, text=True)`.
  - `fixture_runner` allows it. Tests point the variable at a tiny fake helper script that `mv`s into `<home>/.Trash/`.

- [ ] **Step 1: Write the failing tests** in `tests/test_general.py`, `class DownloadsTests`

```python
# fixture: Downloads/Setup.dmg (mtime 20 d ago), Downloads/new.pkg (2 d ago), Downloads/big.zip
# (os.truncate to 60 MB, 20 d ago), Downloads/small.zip (1 MB, 20 d), Downloads/movie.mkv (truncate
# 600 MB, last_used 120 d via Env.fake(last_used=...)), Downloads/recent.mov (600 MB, last used 10 d),
# Downloads/folder/ (a dir), Downloads/link.dmg -> symlink, Downloads/a/b/deep.dmg (too deep)
def test_installer_rule(self):           # Setup.dmg, big.zip offered; new.pkg, small.zip not
def test_large_untouched_rule(self):     # movie.mkv offered; recent.mov not; uses last_used, not mtime
def test_both_rules_show_once(self):     # 600 MB .dmg 120 d old → one item, meta rule "installer"
def test_folders_symlinks_and_deep_files_ignored(self):
def test_review_items_never_rm(self):    # core.validate_path(<Downloads file>) still raises UnsafeTarget
def test_trash_without_helper_refused(self):   # env var unset → (False, "REFUSED: use the app to move these to the Trash")
def test_trash_with_helper_moves_file(self):   # fake helper → file now in <home>/.Trash, gone from Downloads
def test_trash_item_refused_when_rule_no_longer_matches(self):  # scan, then os.utime(movie.mkv, now) and
    # last_used → now → validate refuses; file still in Downloads
def test_trash_refuses_swapped_symlink(self):  # replace file with symlink after scan → refused
def test_archive_trash_gate(self):       # archive item validates; a forged item with path ~/Library/Developer/Xcode → refused
```

- [ ] **Step 2: Run the tests**

Run: `python3 -m unittest discover -s tests`

Expected: the new tests fail.

- [ ] **Step 3: Implement the Interfaces above**

- [ ] **Step 4: Run the tests**

Run: `python3 -m unittest discover -s tests`

Expected: every test passes, with no warnings.

- [ ] **Step 5: Commit** with the message `feat(core): Downloads review list and move-to-Trash items behind a helper`.

---

### Task 4: General categories: app caches, logs, Trash, backups, Docker

**Files:** `devclean_core.py` (or `devclean_general.py`, see File structure), `tests/test_general.py`

**Interfaces:**
- **`Env` gains:**
  - `running_apps: Callable[[], list[tuple[str, str]]]`, a list of (bundle_id, name). `Env.real()` parses `lsappinfo list` lines containing `bundleID="…"` and the quoted app name. On failure it returns `[]` and the scan adds a warning.
  - `docker: Optional[Callable]`, a runner. `Env.real()` uses `subprocess.run` when `shutil.which("docker")` is found, else None.
- **`scan_app_caches(cfg, env) -> list[Item]`.** One item per top-level entry of `~/Library/Caches`.
  - **Skipped:**
    - names starting with `com.apple.`
    - this app's own `com.labyrinth-apps.cleaner`
    - any path already offered by `cfg.fixed_targets`
    - anything that isn't a directory, or is a symlink
  - **Key and fields:** key `appcache:<name>`, category `App cache`, label = the owning app's name, or else the folder name. `tier=SAFE`, `preselect=False`.
  - **Cost:** `"Pages and images load a bit slower the first time. You stay logged in."` for browser bundle IDs (Chrome, Arc, Firefox, Dia, Zen, Brave, Edge, Opera, Vivaldi; Safari's caches are `com.apple.*` and already skipped). For everything else: `"The app rebuilds it as needed."`.
  - **Owner match:** case-insensitive.
    - the folder name equals a running bundle ID, or
    - the folder name equals a running app's name, or
    - the folder name equals the second component of a running bundle ID (`Google` ↔ `com.google.Chrome`).
  - **When the owner is running:** `tier=PROTECTED`, `kind="info"`, note `"Quit {App} first"`.
- **`scan_logs(cfg, now)`:** one item per entry (file or folder) in `~/Library/Logs` and `~/Library/Logs/DiagnosticReports` older than 7 days.
  - key `log:<relpath>`, `SAFE`
  - cost `"Old logs. Only useful when debugging a crash."`
- **`scan_trash(cfg)`:** one item, key `empty-trash`, `kind="empty-trash"`, `CAUTION`.
  - argv `["osascript", "-e", 'tell application "Finder" to empty trash']`
  - cost `"Gone for good. You can't Put Back afterwards."`
  - size: `du` of `~/.Trash`; if that can't be read, `size=None` with note `"Trash: size unavailable"`. Offered only if it has a size above 0 or its size is unknown.
- **`scan_backups(cfg)`:** key `ios-backups`, `PROTECTED`, `kind="info"`.
  - Size from `du` if readable.
  - note `"Manage them in Finder → your iPhone → Manage Backups."`, or `"Size unavailable without Full Disk Access."` if unreadable.
- **`scan_docker(cfg, env)`:** when `env.docker` is set, run `["docker", "system", "df", "--format", "json"]` with `timeout=3`.
  - **Success:** two items.
    - `docker:builder`: `OPTIN`, `kind="docker"`, argv `["docker","builder","prune","-f"]`, size from the "Build Cache" `Reclaimable` field, cost `"The next image build starts from scratch."`
    - `docker:images`: `CAUTION`, argv `["docker","image","prune","-a","-f"]`, size from the "Images" `Reclaimable` field, cost `"Images are downloaded again the next time a container needs them."`
    - `Reclaimable` strings like `"1.2GB (35%)"` are parsed with decimal units.
  - **Failure or timeout:** one `PROTECTED` row, `docker`, note `"Start Docker to see what can be cleaned."`.
- **The gate per kind:** `docker` argv must be exactly one of the two lists; `empty-trash` argv exactly; `appcache`/`log` paths through `validate_path` with a new allow rule:
  - **App caches:** a direct child of `~/Library/Caches`, not `com.apple.*`.
  - **Logs:** a direct child of `~/Library/Logs` or `~/Library/Logs/DiagnosticReports`.
- **`rescan_item`** gets the prefixes `appcache:`, `log:`, `empty-trash`, `docker:`, `download:`, `archive:`.
- **`execution_order(items)`:** `empty-trash` first, then `simctl-unavailable`, then everything else in the given order.
- **Docker runs** go through `env.docker` as the runner, never through the general runner (both are injectable in tests).

- [ ] **Step 1: Write the failing tests** in `tests/test_general.py`

```python
def test_app_caches_offered_except_apple_and_dev_targets(self):  # Caches/Google, Caches/com.spotify.client offered;
    # com.apple.Safari, Homebrew (dev target, not duplicated) not as appcache
def test_cache_blocked_while_owner_runs(self):            # running [("com.spotify.client","Spotify")] → appcache:com.spotify.client PROTECTED "Quit Spotify first"
def test_cache_publisher_folder_blocked_while_app_runs(self):  # running [("com.google.Chrome","Google Chrome")] → appcache:Google PROTECTED
def test_cache_gate_rechecks_running_app(self):           # scanned with nothing running; validate with Chrome running → refused
def test_logs_older_than_7_days_only(self):
def test_empty_trash_item_and_exact_argv(self):
def test_trash_size_unreadable_still_offered(self):       # chmod 0 ~/.Trash → size None, offerable
def test_backups_info_only(self):
def test_docker_items_from_df_json(self):                  # fake docker runner returns canned df JSON → 2 items, sizes parsed
def test_docker_down_or_slow_shows_info_row(self):         # runner raises subprocess.TimeoutExpired → one PROTECTED row, scan still completes
def test_docker_gate_exact_argv(self):                     # forged argv ["docker","system","prune","-a","--volumes"] refused
def test_empty_trash_runs_before_moves(self):              # execution_order([download, empty-trash, runtime, simctl-unavailable])
    # == [empty-trash, simctl-unavailable, download, runtime]
def test_new_categories_never_preselected(self):
```

- [ ] **Step 2: Run the tests**

Run: `python3 -m unittest discover -s tests`

Expected: the new tests fail.

- [ ] **Step 3: Implement the Interfaces above**

- [ ] **Step 4: Run the tests**

Run: `python3 -m unittest discover -s tests`

Expected: every test passes, with no warnings.

- [ ] **Step 5: Commit** with the message `feat(core): app caches, logs, Trash, iOS backups, Docker`.

---

### Task 5: Report, server and page

**Files:** `devclean_report.py`, `devclean_ui.py`, `ui/index.html`, `tests/test_report.py`, `tests/test_ui_server.py`

**Interfaces:**
- **`devclean_report`:**
  - `GROUP_OF[REVIEW] = "review"`, and `GROUP_ORDER` puts review after check, before kept.
  - **`SEGMENTS`** become, in this order:
    - `sim` "Simulators"
    - `android` "Android"
    - `xcode` "Xcode"
    - `web` "node_modules"
    - `caches` "Caches & builds"
    - `appcache` "App caches"
    - `docker` "Docker"
    - `downloads` "Downloads (reviewable)"
    - `trash` "Trash"
    - `other` "System, apps & other files"
    - `free` "Free"
  - **`segment_of`:**
    - `App cache` → `appcache`
    - `Docker` → `docker`
    - `Downloads` → `downloads`
    - `empty-trash` → `trash`
    - `archive:` → `xcode`
    - logs → `caches`
    - simdevice → `sim`
  - **`docker` bytes** are the sum of the Docker items' sizes, since `docker system df` totals aren't measured separately.
  - **Rows** gain `warning` (member warnings, de-duplicated, joined by " "), `deletes` and `restore` (from the lead member), and `kind` (the lead member's kind). Every row's `preselect` is `False`.
  - **Project rows** are grouped by `meta["project_dir"]`. Their id is `f"{group}:{relpath(project_dir, home)}"`.
  - **Payload additions:**
    - `"project_folders": list(cfg.project_roots)`
    - `"auto_project_folders"`: the defaults that exist
    - `"trash_helper": bool(os.environ.get(TRASH_HELPER_ENV))`
- **`devclean_ui`:**
  - **`GET /api/settings`** returns `{"project_folders": [...], "auto_project_folders": [...], "fixed": bool}`. `fixed` is true when `--projects` was given.
  - **`POST /api/settings/project-folders`** takes `{"add": path}` or `{"remove": path}`.
    - It calls `core.add_project_folder` or `core.remove_project_folder`.
    - A `ValueError` gives 400 with the reason. A server started with fixed roots gives 409.
    - On success it rebuilds `self.cfg` and clears `_latest`, so the next clean needs a fresh scan. It returns the settings body.
  - **Jobs** use `core.execution_order` (unchanged call), so `empty-trash` goes first.
  - **Job item statuses** gain `"trashed"` for successful `kind == "trash"` items, so the page can say "Moved to Trash".
- **`ui/index.html`:**
  - **Selection:** nothing is ticked after a scan. `S.sel` starts empty.
  - **Group headers:** each offerable group has "Select all" / "Select none". The time group also keeps "Select stale". The review group's header text is `Review: moves to Trash`.
  - **Rows:**
    - A row with a warning shows it in red under the name.
    - A chevron expands a "What happens" panel with three labelled lines: "Deletes", "Costs you", "To get it back". They show `deletes`, `cost` and `restore`, all as text nodes.
    - Rows with `kind == "trash"` show "Moves to Trash" instead of a size bar label.
  - **The modal:**
    - two sections, "Deleted permanently" and "Moved to Trash", each listing its members
    - every warning of a selected row, in red
    - when any selected row has a warning: a checkbox labelled `I've read the warnings above`, with Delete disabled until it's ticked
    - when `trash_helper` is false and a trash row is selected: Delete disabled, and the note "Moving to the Trash needs the Labyrinth Cleaner app."
  - **A "Project folders" card** below the groups:
    - lists `project_folders`, with automatic ones marked "found automatically" and settings ones with a "Remove" button that calls the API and then rescans
    - an "Add project folder…" button, shown only when `window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.labyrinth` exists; it posts `{"type": "pickProjectFolder"}`. Plan 2 implements the native side, which calls back `window.labyrinthAddedFolder(path)`; the page then POSTs `{"add": path}` and rescans.
    - without the bridge: the note "Add folders in the app, or run devclean ui --projects DIR."
  - **The banner** after a clean counts "Moved to Trash: N".

- [ ] **Step 1: Write the failing tests**
  - `tests/test_report.py`:
    - `test_rows_never_preselected`
    - `test_review_group_and_order`
    - `test_row_carries_warning_deletes_restore`
    - `test_new_segments_order_and_sum`: the segments still add up to the total.
    - `test_project_rows_grouped_by_dir_across_roots`
  - `tests/test_ui_server.py`:
    - `test_settings_get`
    - `test_add_project_folder_then_rescan_shows_it`
    - `test_add_bad_folder_400_with_reason`
    - `test_settings_change_invalidates_scan_id`: a clean with the old `scan_id` gets 409.
    - `test_fixed_roots_settings_409`
    - `test_job_runs_empty_trash_first`: use a `Recorder`, with keys `["download:Setup.dmg", "empty-trash"]`, and `LABYRINTH_TRASH_HELPER` pointing at the fake helper.
    - `test_trash_job_status_trashed`
  - `PageTests`:
    - `test_page_has_new_copy`: checks for `Review: moves to Trash`, `I've read the warnings above`, `Deleted permanently`, `Moved to Trash`, `What happens`, `pickProjectFolder`, `labyrinthAddedFolder`, `Moving to the Trash needs the Labyrinth Cleaner app.`
    - The existing ban on HTML sinks stays.

- [ ] **Step 2: Run the tests**

Run: `python3 -m unittest discover -s tests`

Expected: the new tests fail.

- [ ] **Step 3: Implement the Interfaces above**

- [ ] **Step 4: Run the tests**

Run: `python3 -m unittest discover -s tests`

Expected: every test passes, with no warnings.

- [ ] **Step 5: Browser check on a fixture home**
  - The fixture home has every category, including fake Docker through an env override (builder's call: a hidden `--fake-docker FILE` flag that is honoured **only** together with `--home`), plus a project in `~/Developer`.
  - Check at 1280 and 390 px:
    - nothing is ticked
    - "Select all" works per group
    - warnings show in red
    - "What happens" expands
    - the modal sections appear, and Delete stays disabled until the warnings box is ticked
    - "Moving to the Trash needs the Labyrinth Cleaner app." shows without the helper
    - with `LABYRINTH_TRASH_HELPER` set to the fake helper, a review item ends up in the fixture's `.Trash`, and the banner says "Moved to Trash: 1"
    - the project-folders card lists `~/Developer` as found automatically
  - Then view the real machine with `devclean ui`, **view only, no Delete**, and check the new categories render with real data.

- [ ] **Step 6: Commit** with the message `feat(ui): choice-first page: warnings, what-happens, review group, project folders`.

---

### Task 6: CLI and README

**Files:** `devclean`, `README.md`, `tests/test_devclean.py`

**Interfaces:**
- **CLI behaviour:**
  - The CLI lists the new categories.
  - Its default selection stays `safe`, meaning preselected SAFE items: today's developer targets only, because the new categories are never preselected.
  - `--yes` behaves exactly as today.
  - Warnings are printed under each numbered item, prefixed `! `.
  - Trash items show `(moves to Trash)`. Without the helper they appear in the plan but are refused at execution with the exact message.
- **README:**
  - It leads with "Labyrinth Cleaner". The app download arrives in plan 3; until then, say "coming soon" and how to run `devclean ui`.
  - Update the tiers table: add review, and note that warnings replaced blocks.
  - Update the protection rules: the general rules only, with no device names.
  - Describe project folders: the defaults, settings and `--projects`.
  - List the new categories and say what is never touched.

- [ ] **Step 1: Write the failing tests**
  - `test_cli_yes_never_deletes_new_categories`: a fixture home with an app cache and an old log, `--yes`. Both still exist afterwards, and the dev SAFE targets are gone.
  - `test_cli_prints_warnings`: `--dry-run --select all` with the incident fixture output contains `! Your iPhone 18 Pro simulator stops working`.
  - `test_cli_trash_item_refused_without_helper`

- [ ] **Step 2: Run the tests**

Run: `python3 -m unittest discover -s tests`

Expected: the new tests fail.

- [ ] **Step 3: Implement**, and update the README.

- [ ] **Step 4: Run the tests**

Run: `python3 -m unittest discover -s tests`

Expected: every test passes, with no warnings. Report the count.

- [ ] **Step 5: Spec check.** Go through the spec's "Choice, implications and warnings", "Developer folders and simulators", "New cleanup categories" and "Tests" sections line by line, and name the task or test that covers each. Anything about the app window, menu, picker, helper binary, packaging or Sparkle belongs to plans 2 and 3; list those as handed on.

- [ ] **Step 6: Commit** with the message `feat(cli): warnings and new categories in the CLI; README for Labyrinth Cleaner`.
