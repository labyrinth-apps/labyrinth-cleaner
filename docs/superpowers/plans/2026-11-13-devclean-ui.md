# devclean UI + menu-bar item: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task by task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `devclean ui` opens a local web page to scan, see where disk space goes, see what each delete costs, and clean. A small menu-bar item always shows free space and opens that page.

**Architecture:**
- **Python side:**
  - `devclean_core.py` gains per-item cost, project and staleness data.
  - A new `devclean_report.py` turns a `ScanResult` into the page's JSON: rows, groups, disk map and headline.
  - A new `devclean_ui.py` is a stdlib HTTP server. It serves one static `ui/index.html` and a token-protected JSON API. Every delete goes through `devclean_core.execute_item()`.
- **Menu-bar side:**
  - The menu-bar item is a small Swift app. It reads only free space and runs `devclean ui`.
  - `devclean_menubar.py` builds it and installs or removes it, along with a LaunchAgent.

**Tech stack:** Python 3 standard library only, for the server and tests. One HTML file with inline CSS and JS, no build step. Swift compiled with `swiftc` (AppKit, no Xcode project).

**Spec:** `docs/superpowers/specs/2026-11-11-devclean-ui-design.md`. The visual reference is `docs/superpowers/specs/2026-11-11-devclean-ui-mockup.html`. Read both.

## Global constraints

- **No git:** this folder is not a git repo. Do not `git init` or commit. Each task ends with a full test run instead of a commit.
- **Dependencies:** Python 3 stdlib only, with no new dependencies. No npm, no build step. The page is one static HTML file with inline CSS and JS.
- **No real deletes:** never run a real deletion on this Mac while developing. Test against fixture homes in temp dirs: `tempfile.mkdtemp` then `os.path.realpath`, as `tests/test_devclean.py` does. You may open `devclean ui` on the real machine for a visual check, but never press Delete there.
- **One delete path:** every delete goes through `devclean_core.execute_item()`. The API accepts only item keys from the server's own latest scan, never paths or commands.
- **Deleting is permanent:** `rm -rf`, or the item's `xcrun simctl` argv. Nothing goes to the Trash.
- **Fixture mode:** `--home` or `--simctl-fixture` set means fixture mode. In fixture mode both the CLI and the UI pass `core.fixture_runner`, which refuses every `xcrun` argv.
- **Size format:** binary units, formatted exactly like the CLI's `human()`, for example `9.9 GB` or `145.0 MB`.
- **Commands printed for pasting:** no leading `!` and no trailing `# comment`.
- **Groups:**

  | Tier | Group id | Label |
  |---|---|---|
  | `safe` | `safe` | Safe to remove |
  | `opt-in` | `time` | Costs you some time |
  | `caution` | `check` | Check first |
  | `keep` | `kept` | Kept on purpose |
- **Small items:** rows under 20 MB (`20 * 1024 * 1024` bytes) collapse into a "+ N small items · X MB" row.
- **Stale:** an opt-in item is stale after 30 days. The badge reads `Nd unused` under 60 days, otherwise `N mo unused`, where N = days // 30.
- **Server:**
  - Binds to `127.0.0.1` on a random port.
  - A 32-byte token comes in the URL as `?t=`.
  - The `X-Devclean-Token` header is required on every `/api/*` call.
  - `Host` must be `127.0.0.1:<port>` or `localhost:<port>`.
  - A POST `Origin`, when present, must be the same origin.
  - The server exits after 60 minutes without a request.
- **Menu bar:**
  - Normal colour at 30 GB or more, amber below 30 GB, red below 15 GB.
  - One notification when free space first drops below 15 GB. It fires again only after free space has gone back above 20 GB.
  - Checks every 60 s.
  - Reads free space with `volumeAvailableCapacityForImportantUsage`.
- **Run the tests with** `python3 -m unittest discover -s tests -v`. The existing 26 tests must keep passing.

## Review focus

These are the inputs the spec doesn't mention that are most likely to hurt a real user. Each one has a test in the task named in brackets.

1. **Folder names with `<`, `"`, `&`, spaces or emoji**, for example a project named `a<b>"c`. The page shows them as plain text and never injects HTML. [Task 4: fixture project `x<img src=x onerror=alert(1)>`, checked in the browser, plus a grep test that `innerHTML` is never given scan data]
2. **A folder disappears or changes between the scan and the clean.** Examples: `flutter clean` ran, or a build re-created a folder. That item shows as refused with its reason, and the other items still run. [Task 3: `test_vanished_item_is_refused_others_continue`]
3. **Delete pressed twice, or two tabs open.** The second clean is rejected with 409 and nothing is deleted twice. [Task 3: `test_second_clean_same_scan_is_409`, `test_clean_while_job_running_is_409`]
4. **A long delete, like 6 GB of `node_modules`, outlasts the idle timeout, or the user closes the tab mid-clean.** The job finishes, and the server doesn't exit while a job is running. [Task 3: `test_idle_shutdown_waits_for_job`]
5. **`devclean ui` started a second time, or a stale `ui.url` left by a crashed server.** The second start reuses a live server and ignores or overwrites a dead one. [Task 3: `test_reuse_live_server`, `test_stale_url_file_ignored`]

---

## File structure

| File | Responsibility |
|---|---|
| `devclean_core.py` (modify) | Add to `Item`: `cost_tag`, `cost`, `project`, `stale_days`. Add `human()`, `stale_label()`, `cost_for()`, `project_last_change()`, `fixture_runner()`. `scan()` fills the new fields. |
| `devclean` (modify) | Use `core.human`. Use `core.fixture_runner` in fixture mode. Dispatch `devclean ui …` and `devclean menubar …`. |
| `devclean_report.py` (create) | Pure functions from a `ScanResult` to the page payload: groups, rows, segments, headline. |
| `devclean_ui.py` (create) | HTTP server, security checks, scan cache, clean jobs, idle exit, `ui.url` file, `main(argv)`. |
| `ui/index.html` (create) | The page: inline CSS/JS, built in the mockup's style. |
| `menubar/Logic.swift` (create) | Pure threshold and notification logic. |
| `menubar/main.swift` (create) | `NSStatusItem` app. |
| `menubar/logic_check.swift` (create) | Assertion runner for `Logic.swift`. |
| `devclean_menubar.py` (create) | `install(...)`, `uninstall(...)`, `main(argv)`. |
| `tests/test_report.py`, `tests/test_ui_server.py`, `tests/test_menubar.py` (create) | New tests. Import the helpers (`make_home`, `cfg_for`, `fixture_sim`, `REAL_FIXTURE`, `mkdir`, `write`) from `tests/test_devclean.py`. |
| `README.md` (modify) | Document `devclean ui` and `devclean menubar`. |

---

### Task 1: Core: cost, project, staleness and the fixture runner

**Files:**
- Modify: `devclean_core.py`: `Item` (around line 132), `scan_projects` (265), `scan()` (635)
- Modify: `devclean`: `human()` (33), `main()` (143), the `execute_item` call (around line 245)
- Test: `tests/test_devclean.py`, adding a `class CostAndStaleTests`

**Interfaces:**
- **New `Item` fields:**
  - `cost_tag: str = ""`
  - `cost: str = ""`
  - `project: Optional[str] = None` is the project folder relative to `cfg.projects`, for example `"flutter_app"` or `"labryinth-iptv/third_party/plugin"`.
  - `stale_days: Optional[int] = None`
  - `to_dict()` adds all four.
- `human(n: Optional[int]) -> str` moves from `devclean` into core, unchanged. The CLI calls `core.human`.
- `stale_label(days: Optional[int]) -> Optional[str]`:
  - `None` if `days` is `None` or `days <= 30`
  - `f"{days}d unused"` if `days < 60`
  - otherwise `f"{days // 30} mo unused"`
- `cost_for(it: Item, xcode_is_running: bool) -> tuple[str, str]` returns `(tag, text)`. See step 3.
- `project_last_change(project_dir: str) -> Optional[float]` returns the newest mtime among the top-level entries of `project_dir`. It skips `FLUTTER_ARTIFACTS`, `WEB_ARTIFACTS` and `.git`, and returns `None` if there are no other entries.
- `fixture_runner(argv: list, **kw) -> subprocess.CompletedProcess`:
  - If `argv[0] == "xcrun"`, it returns `CompletedProcess(argv, 1, "", "fixture mode: xcrun disabled")` without running anything.
  - Otherwise it returns `subprocess.run(argv, **kw)`.
- `scan(cfg, sim=None, xcode_check=xcode_running, measure=True, now: Optional[float] = None)`. After measuring sizes and running the gate:
  - every item gets `cost_tag` and `cost` from `cost_for`
  - project items get `stale_days = int((now - project_last_change(dir)) // 86400)`, where `now` defaults to `time.time()`

- [ ] **Step 1: Write the failing tests**

```python
class CostAndStaleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="devclean-test-"))
        self.home = make_home(self.tmp)
    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_every_offerable_item_has_cost(self):
        res = core.scan(cfg_for(self.home), fixture_sim(), xcode_check=lambda: False)
        for it in res.offerable:
            self.assertTrue(it.cost_tag, it.key)
            self.assertTrue(it.cost, it.key)

    def test_cost_table_values(self):
        res = scan(self.home)
        by = {i.key: i for i in res.items}
        self.assertEqual(by["homebrew-cache"].cost_tag, "NO COST")
        self.assertEqual(by["cocoapods-cache"].cost_tag, "~1 MIN")
        self.assertEqual(by["gradle-build-cache"].cost_tag, "~2 MIN")
        self.assertIn("8.14", by["gradle:8.14"].cost)
        self.assertEqual(by["derived-data"].cost_tag, "~5 MIN")
        self.assertEqual(by["flutter:flutter_app/build"].cost_tag, "~2 MIN")
        self.assertIn("web_app", by["web:web_app/node_modules"].cost)

    def test_runtime_tag_is_download_size(self):
        res = core.scan(cfg_for(self.home), fixture_sim(), xcode_check=lambda: False)
        rt = [i for i in res.offerable if i.kind == "runtime"][0]
        self.assertEqual(rt.cost_tag, core.human(rt.size) + " DL")

    def test_derived_data_cost_when_xcode_running(self):
        res = scan(self.home, xcode=True)
        dd = [i for i in res.items if i.key == "derived-data"][0]
        self.assertIn("Quit Xcode first", dd.cost)

    def test_project_field(self):
        res = scan(self.home)
        by = {i.key: i for i in res.items}
        self.assertEqual(by["flutter:flutter_app/build"].project, "flutter_app")
        self.assertEqual(by["web:web_app/.next"].project, "web_app")

    def test_stale_ignores_artifacts_and_git(self):
        P = os.path.join(self.home, "Documents", "Projects", "web_app")
        old = time.time() - 90 * 86400
        for name in os.listdir(P):
            os.utime(os.path.join(P, name), (old, old))
        os.makedirs(os.path.join(P, ".git"), exist_ok=True)   # fresh .git must not count
        os.utime(os.path.join(P, "node_modules"))             # fresh artifact must not count
        res = scan(self.home)
        nm = [i for i in res.items if i.key == "web:web_app/node_modules"][0]
        self.assertGreaterEqual(nm.stale_days, 89)

    def test_stale_label(self):
        self.assertIsNone(core.stale_label(None))
        self.assertIsNone(core.stale_label(30))
        self.assertEqual(core.stale_label(31), "31d unused")
        self.assertEqual(core.stale_label(59), "59d unused")
        self.assertEqual(core.stale_label(95), "3 mo unused")

    def test_fixture_runner_never_runs_xcrun(self):
        cfg = cfg_for(self.home)
        sim = fixture_sim()
        res = core.scan(cfg, sim, xcode_check=lambda: False)
        rt = [i for i in res.offerable if i.kind == "runtime"][0]
        from unittest import mock
        with mock.patch.object(core.subprocess, "run") as run:
            ok, msg = core.execute_item(rt, cfg, sim, runner=core.fixture_runner)
        self.assertFalse(ok)
        self.assertIn("fixture mode", msg)
        run.assert_not_called()

    def test_cli_fixture_mode_uses_fixture_runner(self):
        # CLI deletes a caution runtime only interactively; check wiring statically instead
        src = open(CLI).read()
        self.assertIn("core.fixture_runner", src)
```

Add `import time` to the test file's imports.

- [ ] **Step 2: Run the tests to make sure they fail**

Run: `python3 -m unittest tests.test_devclean.CostAndStaleTests -v` (from the repo root)

Expected: errors such as `AttributeError: 'Item' object has no attribute 'cost_tag'` and `module 'devclean_core' has no attribute 'stale_label'`.

- [ ] **Step 3: Implement the core changes**

`cost_for(it, xcode_is_running)` returns `(tag, text)`. Match the cases in this order. `human()` is the moved function.

| Match | Tag | Text |
|---|---|---|
| `not it.offerable` | `""` | `it.note` |
| key `homebrew-cache` | `NO COST` | `Old downloads of packages you already installed.` |
| key `npm-cache` | `NO COST` | `The next install is a little slower.` |
| key `cocoapods-cache` | `~1 MIN` | ``Next `pod install` downloads pods again.`` |
| key `playwright-cache` | `~1 MIN` | ``Run `npx playwright install` before the next e2e run.`` |
| key `gradle-build-cache` | `~2 MIN` | `The first Android build after this takes longer.` |
| key starts with `gradle:` | `~2 MIN` | `Re-downloaded only if a project still uses Gradle {version}.` |
| key `shorebird-cache` | `~2 MIN` | `Re-downloaded on the next shorebird command.` |
| key `derived-data` | `~5 MIN` | `Xcode re-indexes and rebuilds.` When Xcode is running, prefix `Quit Xcode first. ` |
| category `Flutter` | `~2 MIN` | ``Next `flutter run` does a full build.`` |
| key ends with `/node_modules` | `~1 MIN` if size < 500 MB, `~2 MIN` if < 2 GB, else `~3 MIN` (binary) | ``Run `npm install` before the next {project} work.`` |
| key ends with `/.next` | `~1 MIN` | ``Rebuilt by `next dev`/`next build`.`` |
| kind `runtime` | `f"{human(size)} DL"` | `it.note` |
| category `DeviceSupport` | `~5 MIN` | `Re-copied from the iPhone the next time you plug it in and debug.` |
| category `Android` | `f"{human(size)} DL"` | `No AVD uses it. Re-download from the SDK Manager if needed.` |
| kind `simctl-unavailable` | `NO COST` | `These simulators already can't run.` |
| anything else | `"?"` | `it.note` (shouldn't happen; the test catches it) |

Other changes:
- **`scan_projects`:** set `project=rel` and `meta={"project_dir": d}` on each item.
- **`scan()`:** after the gate loop, set the cost fields on every item, then the stale fields on items with a `project_dir`.
- **`devclean`:**
  - Delete the local `human()` and use `human = core.human`.
  - Compute `fixture = bool(args.home or args.simctl_fixture)`.
  - Call `core.execute_item(it, cfg, sim, runner=core.fixture_runner if fixture else core.subprocess.run)`.

- [ ] **Step 4: Run all tests**

Run: `python3 -m unittest discover -s tests -v`

Expected: every test passes, 26 old and 9 new.

---

### Task 2: Report: rows, disk map and headline

**Files:**
- Create: `devclean_report.py`
- Test: `tests/test_report.py`

**Interfaces:**
- **Consumes (Task 1):**
  - `Item.cost_tag`, `.cost`, `.project`, `.stale_days`
  - `core.stale_label`, `core.human`, `core.du_bytes`
  - `ScanResult`
- **Produces:**
  - `GROUP_OF = {SAFE: "safe", OPTIN: "time", CAUTION: "check", PROTECTED: "kept"}`
  - `SEGMENTS = [("sim","Simulators"), ("android","Android"), ("xcode","Xcode"), ("web","node_modules"), ("caches","Caches & builds"), ("other","System, apps & other files"), ("free","Free")]`
  - `segment_of(it: Item) -> str`:
    - category `Simulator` → `sim`
    - `Android` → `android`
    - key `derived-data` or `archives`, or category `DeviceSupport` → `xcode`
    - `Web` → `web`
    - key `downloads` → `other`
    - everything else → `caches`
  - `build_rows(items: list[Item], home: str) -> list[dict]`. Each row:

    ```
    {"id": str, "group": str, "segment": str, "category": str, "label": str, "path": str,
     "keys": [str], "size": int|None, "mtime": float|None, "cost_tag": str, "cost": str,
     "stale": str|None, "preselect": bool,
     "members": [{"key": str, "label": str, "size": int|None}]}
    ```
  - `measure_extras(cfg, measure=core.du_bytes) -> dict` with keys `sim_devices`, `android_sdk`, `android_avd`, `gradle_modules`. Each is a byte count, 0 if the folder is missing. The four `du` calls run in a `ThreadPoolExecutor(4)`.
  - `disk_map(res: ScanResult, extras: dict) -> list[dict]`, where each entry is `{"id", "label", "bytes", "reclaimable"}`, in `SEGMENTS` order.
  - `scan_payload(res, cfg, scan_id: str, duration_s: float, extras: dict) -> dict`:

    ```
    {"scan_id", "scanned_at": epoch, "duration_s", "home": cfg.home,
     "disk": {"total", "free", "used"},
     "headline": {"reclaimable", "no_cost"},
     "segments", "rows", "warnings", "xcode_running",
     "small_bytes": 20*1024*1024}
    ```

**Rules the tests pin:**
- **Project rows:** items with the same `project` and the same group make **one row**.
  - `id = f"{group}:{project}"`
  - `label = f"{project_basename} · {' + '.join(member basenames)}"`, for example `"flutter_app · build + .dart_tool"`
  - `path` = the project folder with `home` shown as `~`
  - `size` = the sum of the members; `mtime` = the max
  - `cost_tag` and `cost` come from the largest member
  - `stale = stale_label(stale_days)` only when the group is `time`
  - `preselect` = all members preselected
- **Other rows:** every other item is its own row, with `id = it.key` and `path` = the item path shown with `~`, or `""`.
- **Kept rows:** `keys == []` and `preselect` is False.
- **Ordering:** rows are sorted by group order safe, time, check, kept, then by size, largest first.
- **Simulators segment:** sizes of the `sim` items, excluding `simctl-unavailable` (those folders are already inside Devices), plus `extras["sim_devices"]`.
- **Android segment:** `android_sdk + android_avd`. Item sizes aren't added, because they're inside the SDK.
- **Xcode, node_modules and Caches & builds:** the sums of their items' sizes. Caches & builds also gets `+ gradle_modules`.
- **Reclaimable per segment:** the sum of offerable item sizes in that segment.
- **Everything else:** `other = max(0, used − sum of the five measured segments)`, with `reclaimable` 0.
- **Free:** `free = res.free_bytes`.
- **Headline:**
  - `reclaimable` = the sum of offerable sizes
  - `no_cost` = the sum of SAFE sizes

- [ ] **Step 1: Write the failing tests** in `tests/test_report.py`

```python
from test_devclean import make_home, cfg_for, fixture_sim, write, mkdir  # tests/ is on sys.path under discover
import devclean_report as rep

class ReportTests(unittest.TestCase):
    # setUp: tmp + make_home; self.res = core.scan(cfg_for(home), fixture_sim(), xcode_check=lambda: False)

    def test_flutter_build_and_dart_tool_one_row(self):
        rows = rep.build_rows(self.res.items, self.home)
        r = [r for r in rows if r["id"] == "safe:flutter_app"][0]
        self.assertEqual(r["label"], "flutter_app · build + .dart_tool")
        self.assertEqual(sorted(r["keys"]), ["flutter:flutter_app/.dart_tool", "flutter:flutter_app/build"])
        self.assertEqual(r["size"], sum(m["size"] for m in r["members"]))
        self.assertTrue(r["path"].startswith("~/Documents/Projects/"))

    def test_mixed_project_one_row_per_group(self):
        rows = rep.build_rows(self.res.items, self.home)
        ids = {r["id"] for r in rows}
        self.assertIn("safe:labryinth-iptv", ids)
        self.assertIn("time:labryinth-iptv", ids)

    def test_web_row_combines_node_modules_and_next(self):
        r = [r for r in rep.build_rows(self.res.items, self.home) if r["id"] == "time:web_app"][0]
        self.assertEqual(len(r["keys"]), 2)
        self.assertFalse(r["preselect"])

    def test_kept_rows_have_no_keys(self):
        for r in rep.build_rows(self.res.items, self.home):
            if r["group"] == "kept":
                self.assertEqual(r["keys"], [])

    def test_rows_ordered_by_group_then_size(self):
        rows = rep.build_rows(self.res.items, self.home)
        order = {"safe": 0, "time": 1, "check": 2, "kept": 3}
        keys = [(order[r["group"]], -(r["size"] or 0)) for r in rows]
        self.assertEqual(keys, sorted(keys))

    def test_stale_only_in_time_group(self):
        # age web_app sources 90 days; flutter_app too → flutter row must still have stale None
        ...
        self.assertEqual(web_row["stale"], "3 mo unused")
        self.assertIsNone(flutter_row["stale"])

    def test_disk_map_sums_to_total(self):
        extras = {"sim_devices": 10, "android_sdk": 20, "android_avd": 5, "gradle_modules": 7}
        segs = rep.disk_map(self.res, extras)
        self.assertEqual([s["id"] for s in segs], [i for i, _ in rep.SEGMENTS])
        self.assertEqual(sum(s["bytes"] for s in segs), self.res.total_bytes)

    def test_disk_map_other_never_negative(self):
        self.res.free_bytes = self.res.total_bytes  # used = 0
        segs = {s["id"]: s for s in rep.disk_map(self.res, {"sim_devices": 10**9, "android_sdk": 0, "android_avd": 0, "gradle_modules": 0})}
        self.assertEqual(segs["other"]["bytes"], 0)

    def test_reclaimable_overlay(self):
        segs = {s["id"]: s for s in rep.disk_map(self.res, dict.fromkeys(["sim_devices","android_sdk","android_avd","gradle_modules"], 0))}
        rt = sum(i.size or 0 for i in self.res.offerable if rep.segment_of(i) == "sim")
        self.assertEqual(segs["sim"]["reclaimable"], rt)
        self.assertGreater(segs["caches"]["reclaimable"], 0)

    def test_headline(self):
        p = rep.scan_payload(self.res, cfg_for(self.home), "id1", 1.0, dict.fromkeys(["sim_devices","android_sdk","android_avd","gradle_modules"], 0))
        self.assertEqual(p["headline"]["reclaimable"], self.res.reclaimable())
        self.assertEqual(p["headline"]["no_cost"], sum(i.size or 0 for i in self.res.offerable if i.tier == core.SAFE))
        json.dumps(p)  # serialisable

    def test_measure_extras_missing_dirs_are_zero(self):
        ex = rep.measure_extras(cfg_for(self.home))
        self.assertEqual(ex["android_sdk"], 0)
        self.assertGreater(ex["gradle_modules"], 0)
```

The tests add `ROOT` to `sys.path` the same way `tests/test_devclean.py` does. Fill in `test_stale_only_in_time_group` the same way as Task 1's `test_stale_ignores_artifacts_and_git`.

- [ ] **Step 2: Run the tests to make sure they fail**

Run: `python3 -m unittest tests.test_report -v`

Expected: `ModuleNotFoundError: No module named 'devclean_report'`.

- [ ] **Step 3: Implement `devclean_report.py`** with the interfaces and rules above. It has no I/O except `measure_extras`.

- [ ] **Step 4: Run all tests**

Run: `python3 -m unittest discover -s tests -v`

Expected: everything passes.

---

### Task 3: The server: `devclean_ui.py` and `devclean ui`

**Files:**
- Create: `devclean_ui.py`
- Modify: `devclean`. At the top of `main()`: if `argv[0] == "ui"`, return `devclean_ui.main(argv[1:])`. Read `argv` from `sys.argv[1:]` when it's `None`.
- Test: `tests/test_ui_server.py`

**Interfaces:**
- **Consumes:**
  - `core.scan`, `core.execute_item`, `core.fixture_runner`, `core.Config`, the providers
  - `devclean_report.scan_payload`, `devclean_report.measure_extras`
- **Produces:**
  - `class UIServer`:

    ```python
    UIServer(cfg, sim, *, fixture: bool, xcode_check=core.xcode_running,
             runner=None, idle_timeout=3600, port=0, measure=core.du_bytes)
    ```

    - `runner` defaults to `core.fixture_runner` if `fixture`, else `subprocess.run`.
    - Attributes: `.port`, `.token` (`secrets.token_urlsafe(32)`), `.url` (`f"http://127.0.0.1:{port}/?t={token}"`).
    - Methods: `.start()` (serve in a daemon thread), `.serve_forever()` (blocking, with idle exit), `.shutdown()`.
    - Built on `http.server.ThreadingHTTPServer` bound to `127.0.0.1`.
  - **Routes:**
    - **`GET /`:** returns `ui/index.html` with these headers:
      - `Content-Type: text/html; charset=utf-8`
      - `Cache-Control: no-store`
      - `Referrer-Policy: no-referrer`
      - `Content-Security-Policy: default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; img-src data:`
    - **`GET /api/ping`:** `{"ok": true}`.
    - **`GET /api/scan`:** the `scan_payload` dict.
      - Only one scan runs at a time. A concurrent call waits on the running scan and gets the same `scan_id`.
      - `scan_id = secrets.token_hex(8)`.
      - The server keeps `self._latest = (scan_id, {item.key: item})`.
      - On an exception it returns 500 `{"error": str(e)}` and keeps serving.
    - **`POST /api/clean`:** body `{"scan_id": str, "keys": [str]}`, with `Content-Type: application/json` required and a body of at most 64 KB.
      - 409 `{"error": "stale scan"}` if `scan_id` isn't the latest.
      - 400 if `keys` is empty or not a list of strings.
      - 400 `{"error": "unknown or protected key", "keys": [...]}` if any key isn't in the latest scan or isn't `offerable`. Nothing runs in that case.
      - 409 `{"error": "a clean is already running"}` if a job is running.
      - Otherwise it starts a daemon thread and returns 200 `{"job_id": str}`.
      - The latest scan is cleared as soon as the job starts, so the same `scan_id` can never be cleaned twice.
    - **`GET /api/job/<id>`:**

      ```
      {"job_id", "state": "running"|"done", "free_before", "free_now",
       "items": [{"key", "label", "size", "status": "pending"|"running"|"done"|"failed"|"refused", "message"}]}
      ```

      - `refused` means `execute_item` returned a message that starts with `REFUSED: `.
      - Unknown ids get 404.
  - **Checks, in order, on every request:**
    1. `Host` is in `{f"127.0.0.1:{port}", f"localhost:{port}"}`, otherwise 403.
    2. For `/api/*`: `hmac.compare_digest(header X-Devclean-Token, token)`, otherwise 403.
    3. For POST: if `Origin` is present, it must be in `{f"http://127.0.0.1:{port}", f"http://localhost:{port}"}`, otherwise 403. This also rejects `Origin: null`.
    - Anything else gets 404. Silence the default request logging.
  - **Idle exit:** `serve_forever()` checks every 5 s. It shuts down when `time.time() - last_request > idle_timeout` and no job is running.
  - **URL file:** `url_file(cfg) -> str` is `cfg.p(".cache/devclean/ui.url")`. `main` writes it with mode `0o600` after binding and removes it in `finally`.
  - **`main(argv) -> int`:**
    - Flags: `--home`, `--projects`, `--simctl-fixture`, `--no-simulators`, `--no-open`. They pick the provider the same way as `devclean` `main()` does.
    - `fixture = bool(home or simctl_fixture)`.
    - Before starting, if the URL file exists and `GET /api/ping` with its token answers 200 within 1 s, it opens that URL (`open <url>` unless `--no-open`), prints it and returns 0. Otherwise it starts a new server, overwriting the file.
    - It prints `devclean UI: <url>  (Ctrl-C to stop)` and opens the URL unless `--no-open`.

- [ ] **Step 1: Write the failing tests** in `tests/test_ui_server.py`

Helper: `self.srv = UIServer(cfg_for(home), fixture_sim(), fixture=True, xcode_check=lambda: False); self.srv.start()`.

Write `req(method, path, body=None, headers=None, token=True, host=None)` with `http.client.HTTPConnection("127.0.0.1", port)`. It sets `Host` to `host or f"127.0.0.1:{port}"` and returns `(status, json_or_text)`. `tearDown` calls `self.srv.shutdown()` and removes the tmp dir.

```python
def test_index_served_without_token(self):          # 200, contains 'id="app"', CSP header present
def test_api_without_token_403(self):               # GET /api/scan token=False → 403
def test_api_wrong_token_403(self):
def test_wrong_host_403(self):                      # host="evil.example:PORT" → 403 even for "/"
def test_post_foreign_origin_403(self):             # Origin: http://evil.example → 403; Origin: null → 403
def test_scan_payload_shape(self):                  # keys scan_id, rows, segments, headline; json
def test_clean_stale_scan_id_409(self):             # scan twice, clean with first id → 409
def test_clean_kept_or_unknown_key_refused(self):
    # pick a kept row's item key (e.g. "archives") and "nope"; 400; Archives + Downloads still exist
def test_forged_key_never_reaches_rm(self):
    # runner = recording fake; post keys=["../../etc"] → 400; recorder empty
def test_clean_safe_deletes_exactly_those(self):
    # scan; keys = all keys of rows with group == "safe"; poll job until done (timeout 10 s)
    # those paths are gone; Documents/taxes.pdf, Downloads/movie.mkv, Archives, flutter_app/lib/main.dart,
    # web_app/node_modules, web_app/build still exist; every job item status == "done"
def test_fixture_runtime_delete_never_calls_xcrun(self):
    # with mock.patch("subprocess.run") wrapping a guard that fails the test if argv[0]=="xcrun";
    # clean the iOS 26.5 runtime key "runtime:91298CDE-FDEA-4F7C-8FF5-E27CFAC0C3B6" → job item status "failed",
    # message contains "fixture mode"
def test_vanished_item_is_refused_others_continue(self):
    # scan; rmtree flutter_app/build behind the server's back; clean safe keys →
    # that item status "refused", the others "done"
def test_second_clean_same_scan_is_409(self):
def test_clean_while_job_running_is_409(self):
    # runner that blocks on a threading.Event; second POST after a fresh scan → 409; release event
def test_concurrent_scans_share_result(self):       # two threads GET /api/scan → same scan_id
def test_scan_error_returns_500_and_server_survives(self):
    # xcode_check raises RuntimeError → 500 {"error"}; then /api/ping → 200
def test_idle_shutdown_waits_for_job(self):
    # idle_timeout=0.2 with a blocking runner; serve_forever in a thread; after 1 s the server is still up;
    # release → it exits within 7 s
def test_reuse_live_server(self):
    # main(["--home", home, "--no-open"]) in a thread → url file appears; a second main(...) returns 0
    # quickly and prints the same URL
def test_stale_url_file_ignored(self):
    # write url file pointing at a closed port; main starts a new server and overwrites the file (mode 0o600)
```

For the blocking-runner tests, construct `UIServer(..., runner=blocking)` where `blocking(argv, **kw)` waits on an `Event` and then calls `core.fixture_runner(argv, **kw)`.

- [ ] **Step 2: Run the tests to make sure they fail**

Run: `python3 -m unittest tests.test_ui_server -v`

Expected: `ModuleNotFoundError: No module named 'devclean_ui'`.

- [ ] **Step 3: Implement `devclean_ui.py` and the `devclean ui` dispatch.** Create a placeholder `ui/index.html` containing `<div id="app"></div>` so the index test passes. Task 4 replaces it.

- [ ] **Step 4: Run all tests**

Run: `python3 -m unittest discover -s tests -v`

Expected: everything passes, and the suite finishes in under about 30 s.

- [ ] **Step 5: Smoke test on a fixture**

Run: `./devclean ui --home "$(python3 -c 'import tempfile,sys;sys.path.insert(0,"tests");from test_devclean import make_home;import os;print(make_home(os.path.realpath(tempfile.mkdtemp())))')" --no-open`

Expected: it prints `devclean UI: http://127.0.0.1:<port>/?t=…`. `curl -s -H "X-Devclean-Token: <t>" http://127.0.0.1:<port>/api/scan | head -c 300` shows JSON. Stop it with Ctrl-C, and the URL file is removed.

---

### Task 4: The page: `ui/index.html`

**Files:**
- Create: `ui/index.html`, replacing the placeholder
- Test: `tests/test_ui_server.py`, adding a `class PageTests`

**Interfaces:**
- **Consumes:** the routes and JSON from Task 3, and the payload and row shape from Task 2.
- **Token handling:** the page reads `t` from `location.search`, stores it in `sessionStorage`, and removes it from the address bar with `history.replaceState`. It sends `X-Devclean-Token` on every `fetch`. POSTs use `Content-Type: application/json`.

**What to build.** Start from the mockup's CSS and markup, in `docs/superpowers/specs/2026-11-11-devclean-ui-mockup.html`, and drive it from data.

1. **On load:** a skeleton or loading state that says "Scanning… (about 15 s)", then `GET /api/scan`. On a non-200 response it shows the error and a "Try again" button.
2. **Header:** "devclean", "Scanned HH:MM · took N s", and a "Scan again" button.
3. **Hero, left side:**
   - "You can get back **{human(headline.reclaimable)}** · {human(headline.no_cost)} with no cost".
   - Three tier cards (Safe / Costs reinstall time / Check first) showing each group's total and row count. Clicking a card sets the filter.
4. **Hero, right side:**
   - An SVG ring showing free/total and "{free} free of {total}".
   - "After this clean: {free + selected}", updated live.
   - Below it: "Menu bar turns amber below 30 GB".
5. **Disk map:**
   - Segments with `flex` set to their bytes.
   - A striped overlay whose width is `reclaimable/bytes`.
   - A legend with sizes.
   - Clicking a segment filters rows by `segment`. Clicking `other` shows the note "devclean doesn't analyse system, app or personal files." Clicking `free` does nothing.
6. **Filter chips:** All / Safe / Costs time / Check first / Kept. The active chip is highlighted. Chips and segment filters combine; clicking the active filter again clears it.
7. **Group cards** in the order safe, time, check, kept, each with its total. Group headers:
   - **Safe:** "Select none / Select all".
   - **Costs time:** "Select stale". It ticks exactly the rows in that group whose `stale` is non-null.
   - **Kept:** read-only, with no checkboxes. Shows each row's `cost` (the reason) and a lock icon.
8. **A row** shows:
   - the checkbox, which selects all of `row.keys`
   - an icon by `category`: Flutter 🐦, Web 📦, Cache 🗄️, Gradle 🐘, DeviceSupport 📱, Android 🤖, Simulator 📲, Protected 🔒, other 📁
   - the label and the path
   - the size, with a mini bar scaled to the largest row in its group
   - "last used" from `mtime`: `today`, `Nd ago`, or `YYYY-MM-DD`, the same rules as the CLI `age()`
   - the `cost_tag` pill and the `cost` line
   - the `stale` badge
   - Preselected rows start ticked.
9. **Small rows:** rows under `small_bytes` in each group collapse into "+ N small items · X" and expand when clicked.
10. **Floating action bar,** shown when anything is selected:
    - selected total, item count, and "Free after: X"
    - a warning when any `check` row is selected: "Includes items to check first"
    - a **Review & clean** button
11. **Confirmation modal:**
    - every selected member's label and size
    - "Free space: A → B"
    - the line "Deleting is permanent. Nothing goes to the Trash."
    - Cancel, and a red **Delete** button. Delete is disabled after the first click.
    - It `POST`s `/api/clean` with `{scan_id, keys}`.
    - On 409 it shows "The scan is out of date. Scanning again…" and rescans.
12. **Progress:** the modal turns into a per-item list with status icons (pending, running, done, failed or refused with the message), polling `GET /api/job/<id>` every 500 ms. When the job is `done`, the page rescans automatically, then shows a banner: "Freed {human(newFree - free_before)} · {free_before} → {newFree}". The rescanned page shows any failures listed in the banner.
13. **Escaping:** every piece of scan data goes into the DOM through `textContent` or `setAttribute`. Never use `innerHTML` with data; it's allowed only for static template strings with no interpolation. Never use `eval`.
14. **Narrow screens:** below 820 px, follow the mockup's breakpoint. At 390 px nothing scrolls sideways, the action bar stays usable, and the path wraps or truncates with an ellipsis.

`human()` in JS must match `core.human` exactly: binary units, `B`/`KB` with 0 decimals, `MB`/`GB`/`TB` with 1 decimal, switching unit at ≥ 1000.

- [ ] **Step 1: Write the failing tests** in `PageTests`

```python
def test_page_has_required_hooks(self):
    html = open(os.path.join(ROOT, "ui", "index.html")).read()
    for needle in ('X-Devclean-Token', '/api/scan', '/api/clean', '/api/job/', 'sessionStorage',
                   'replaceState', 'Nothing goes to the Trash', 'Select stale', 'Review &amp; clean',
                   'Menu bar turns amber below 30 GB'):
        self.assertIn(needle, html)

def test_no_innerhtml_with_interpolation(self):
    html = open(os.path.join(ROOT, "ui", "index.html")).read()
    import re
    bad = re.findall(r"innerHTML\s*[+]?=\s*`[^`]*\$\{", html) + re.findall(r"innerHTML\s*[+]?=\s*[^`'\";]*\+", html)
    self.assertEqual(bad, [])
    self.assertNotIn("eval(", html)

def test_js_human_matches_python(self):
    # skipUnless(shutil.which("node")); extract the `function human(` source by regex, run it in node
    # for [0, 999, 1000, 1536, 145*1024**2, 9.9*1024**3, 5*1024**4] and compare to core.human
```

- [ ] **Step 2: Run them to make sure they fail**

Run: `python3 -m unittest tests.test_ui_server.PageTests -v`

Expected: FAIL, because needles are missing from the placeholder.

- [ ] **Step 3: Build `ui/index.html`** as described above.

- [ ] **Step 4: Run all tests**

Run: `python3 -m unittest discover -s tests -v`

Expected: everything passes.

- [ ] **Step 5: Browser check on a fixture with a hostile project name**
  - Make a fixture home with `make_home`.
  - Add `write(f"{P}/x<img src=x onerror=alert(1)>/package.json", "{}")` and `mkdir(...node_modules)`. Also add one 30 MB folder, for example `Library/Caches/Homebrew/big.bin`, written with `os.urandom` in chunks, so not everything collapses.
  - Run `./devclean ui --home <fixture> --no-open`.
  - Open the URL in the IDE browser panel at 1280×800, then at 390×844.
  - **Check, with screenshots:**
    - the layout matches the mockup's look
    - the hostile name shows as literal text and no alert fires
    - tier cards, chips and segment clicks filter the list
    - Select stale works, and the small-items row expands
  - **Clean the Safe group in the fixture:** progress runs, the page rescans, and "Freed …" appears.
  - Clean the runtime row with `--simctl-fixture tests/fixtures/real_2026-11-11`. It must show as failed with "fixture mode: xcrun disabled".

- [ ] **Step 6: Visual check on the real machine (no deleting)**

Run `devclean ui --no-open` and open it at 1280×800. Check that the real scan renders (free space about 85 GB, Safe about 5.5 GB, iOS 26.5 in Check first). **Do not press Review & clean or Delete.** Stop it with Ctrl-C.

---

### Task 5: The menu-bar item

**Files:**
- Create: `menubar/Logic.swift`, `menubar/main.swift`, `menubar/logic_check.swift`, `devclean_menubar.py`
- Modify: `devclean`, so that if `argv[0] == "menubar"` it returns `devclean_menubar.main(argv[1:])`
- Test: `tests/test_menubar.py`

**Interfaces:**
- **`Logic.swift` (pure, Foundation only):**
  - `enum Level { case normal, amber, red }`
  - `func level(freeBytes: Int64) -> Level`, with GB = 1024³:
    - `red` if under 15 GB
    - `amber` if under 30 GB
    - `normal` otherwise
  - `struct NotifyState { var notified = false }`
  - `func step(_ s: inout NotifyState, freeBytes: Int64) -> Bool` returns true exactly when it should notify:
    - if not notified and free < 15 GB, set notified and return true
    - if notified and free > 20 GB, reset notified and return false
  - `func formatFree(_ bytes: Int64) -> String`: `ByteCountFormatter` with `countStyle = .file`, the same as Finder.
- **`main.swift`:**
  - `NSApplication` with `.accessory` activation policy, so there's no Dock icon.
  - An `NSStatusItem` whose title is `formatFree(free)`. The title is an attributed string, `systemOrange` for amber and `systemRed` for red.
  - Free and total come from `URL(fileURLWithPath: "/")`, `resourceValues(forKeys: [.volumeAvailableCapacityForImportantUsageKey, .volumeTotalCapacityKey])`.
  - A `Timer` every 60 s, plus a refresh when the menu opens.
  - **Menu:**
    - "{free} free of {total}" (disabled)
    - a separator
    - "Clean up…"
    - "Quit"
  - "Clean up…" runs `Process` with `executableURL` set to the `--devclean <path>` argument and arguments `["ui"]`, with stdout and stderr going to `/dev/null`.
  - When `step()` returns true, it notifies by running `/usr/bin/osascript -e 'display notification "Only {free} free. Click the menu bar item → Clean up…" with title "Disk almost full"'`. This uses osascript to avoid the signing and permission setup that UNUserNotificationCenter needs.
- **`logic_check.swift`:** top-level code that uses `precondition` on the threshold edges (15 GB − 1 byte → red, exactly 15 GB → amber, exactly 30 GB → normal) and on the notify sequence `[40, 14, 10, 18, 21, 14]` GB → `[F, T, F, F, F, T]`. It prints `OK`.
- **`devclean_menubar.py`:**
  - `APP_ID = "local.devclean.bar"`
  - `install(home: str, devclean_path: str, run=subprocess.run, compile=True) -> str` returns the app path.
    - It builds `~/Applications/DevcleanBar.app/Contents/MacOS/DevcleanBar` with `swiftc -O menubar/Logic.swift menubar/main.swift -o …`. It skips the compile step when `compile=False` and writes an empty executable placeholder instead.
    - It writes `Contents/Info.plist` with `plistlib`, setting `CFBundleIdentifier=APP_ID`, `CFBundleName=DevcleanBar`, `CFBundleExecutable=DevcleanBar` and `LSUIElement=True`.
    - It writes `~/Library/LaunchAgents/local.devclean.bar.plist` with `Label=APP_ID`, `ProgramArguments=[binary, "--devclean", realpath(devclean_path)]` and `RunAtLoad=True`.
    - It runs `launchctl bootout gui/<uid>/local.devclean.bar` and ignores failure, then `launchctl bootstrap gui/<uid> <plist>`.
    - Re-running it updates everything in place.
  - `uninstall(home: str, run=subprocess.run) -> None`:
    - Runs `launchctl bootout` and ignores failure, then removes the plist.
    - Removes the app with `shutil.rmtree`, **only if** its `Info.plist` has `CFBundleIdentifier == APP_ID`. Otherwise it raises `RuntimeError` and touches nothing.
  - `main(argv)`: `install | uninstall` with `--home`, which defaults to `~`. It prints what it did.

- [ ] **Step 1: Write the failing tests** in `tests/test_menubar.py`

```python
def test_install_writes_app_and_agent(self):        # compile=False, run=recorder; Info.plist LSUIElement True,
                                                     # agent ProgramArguments[1:] == ["--devclean", realpath(CLI)],
                                                     # recorder saw ["launchctl", "bootstrap", f"gui/{os.getuid()}", plist]
def test_reinstall_is_idempotent(self):
def test_uninstall_removes_everything(self):        # app dir and plist gone; bootout called
def test_uninstall_refuses_foreign_app(self):       # Info.plist with another bundle id → RuntimeError, dir intact
@unittest.skipUnless(shutil.which("swiftc"), "needs swiftc")
def test_swift_logic(self):                         # swiftc Logic.swift logic_check.swift -o <tmp>/check; run → "OK"
@unittest.skipUnless(shutil.which("swiftc"), "needs swiftc")
def test_app_compiles(self):                        # install(..., compile=True, run=fake launchctl that passes swiftc through)
                                                     # → the binary exists and is executable
```

Every test uses a temp `home`, so nothing touches the real `~/Applications` or `~/Library/LaunchAgents`, and `launchctl` is never really called.

- [ ] **Step 2: Run them to make sure they fail**

Run: `python3 -m unittest tests.test_menubar -v`

Expected: `ModuleNotFoundError: No module named 'devclean_menubar'`.

- [ ] **Step 3: Implement the Swift files and `devclean_menubar.py`.**

- [ ] **Step 4: Run all tests**

Run: `python3 -m unittest discover -s tests -v`

Expected: everything passes, including both swiftc tests.

- [ ] **Step 5: Manual check** (it installs only into the user's own `~/Applications` and LaunchAgents, and deletes nothing)
  - Run `devclean menubar install`.
  - Check that the number appears in the menu bar and matches Finder's "available" for Macintosh HD.
  - "Clean up…" opens the page. A second click reuses the same server.
  - Leave it installed. Tell the user how to remove it: `devclean menubar uninstall`.

---

### Task 6: README and final check

**Files:**
- Modify: `README.md`, adding a "Web UI" section after Usage and a "Menu bar" section

**What to add to the README:**
- **Web UI:**
  - `devclean ui`: what it shows, that it's local only, that it closes after 60 min idle, and that it uses the same safety gate.
  - `--home` and `--simctl-fixture` work for demos, and never run `xcrun` in that mode.
- **Menu bar:**
  - `devclean menubar install` / `uninstall`, and the colour thresholds.
  - Its number is Finder's "available", which includes purgeable space, so it can be higher than the page's free figure.
- **Layout:** add the new files.

- [ ] **Step 1: Update the README.**
- [ ] **Step 2: Run all tests**

Run: `python3 -m unittest discover -s tests -v`

Expected: everything passes. Report the count.
- [ ] **Step 3: Spec check.** Go through every bullet in the spec's Goals and Tests sections and name the task or test that covers it. Report any gaps.
