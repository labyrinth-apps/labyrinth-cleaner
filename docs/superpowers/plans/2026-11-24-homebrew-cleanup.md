# Homebrew Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Homebrew section: one safe "old versions and unneeded libraries" item, a reviewable list of packages the user installed, a dry-run confirmation, a disk-map block, and the CLI equivalents.

**Architecture:** A new module `devclean_brew.py` holds everything Homebrew-specific: finding `brew`, parsing its output, building items, the confirm-time preview. It plugs into the existing engine the way Docker does: a runner on `Env` that is `None` in fixture mode, a new item kind `"brew"` checked by `validate_item` and run by `execute_item`, a `RESCANNERS` entry. The report, the UI server, the page and the CLI each get a small addition.

**Tech Stack:** Python 3.11+ stdlib only (`unittest`, `subprocess`, `json`, `concurrent.futures`), vanilla JS in `ui/index.html`, node for the page tests.

**Spec:** `docs/superpowers/specs/2026-11-24-homebrew-cleanup-design.md`

## Global Constraints

- Tests never run the real `brew` (nor `xcrun`, `docker`, `osascript`, `lsappinfo`, `mdls`, `sysctl`, `xcode-select`, launchctl), never read the real `/Applications`, the real Cellar/Caskroom or `/Library/LaunchDaemons` (`find_brew` may stat the two `brew` paths, as `Env.real` already runs `shutil.which("docker")`). Brew tests use a fake runner and fixture folders in `os.path.realpath(tempfile.mkdtemp())`.
- Development never runs a real `brew cleanup`, `autoremove` or `uninstall`, and never presses Delete on a server that isn't pointed at a fixture home.
- `env.brew` is `None` in fixture mode and in `Env.fake()` unless a test passes one. `fixture_runner` refuses argv whose first element's basename is `brew`.
- Every `brew` call sets `HOMEBREW_NO_AUTO_UPDATE=1`, `HOMEBREW_NO_INSTALL_CLEANUP=1`, `HOMEBREW_NO_ANALYTICS=1`, `HOMEBREW_NO_ENV_HINTS=1`, `HOMEBREW_NO_COLOR=1`, on top of `os.environ`. The app never sets or clears `HOMEBREW_NO_AUTOREMOVE`.
- Never pass `--ignore-dependencies`, `--force` or `--zap`. The only commands that run are `[<brew>, "cleanup"]` and `[<brew>, "uninstall", "--formula", <name>]`. The only read-only calls are `info --json=v2 --installed`, `cleanup -n`, `autoremove -n` and `uninstall --dry-run --formula <names…>`.
- Formula names must match `^[a-z0-9][a-z0-9@+._-]*$`.
- Scan timeouts are 20 s per call. Clean timeout is 300 s per command.
- Exact copy (verbatim from the spec):
  - safe item label: `Old Homebrew versions and unneeded libraries`
  - group title: `Homebrew packages`; group description: `Command-line tools and libraries you installed with Homebrew. Removing uninstalls them; you can install them again with brew.`
  - scan warning: `couldn't read Homebrew; its packages are not offered`
  - other account: `Homebrew belongs to another account on this Mac`
  - service: `Runs as a background service. Stop it with `brew services stop <name>` first.` (package row) and `<name> runs as a background service. Stop it with `brew services stop <name>` first.` (library / safe item / confirmation)
  - notes: `older version · <base> <major> also installed`, `also installed: <names joined with ", ">`, `Projects or scripts you run may use it`, `Homebrew no longer maintains it`, `plus about <size> of libraries only it uses`, `installed <Mon YYYY>`; joined with ` · `
  - confirmation: `Homebrew also removes these libraries nothing needs any more:` and `This can't be undone from the Trash. You can reinstall with `brew install <name>`.`
  - preview failure: `Couldn't ask Homebrew what this would remove. Scan again.`
  - busy: `Homebrew is busy with another command. Try again when it's done.`
  - restore: `Removed. Reinstall with `brew install <name>`.`
  - not available: `REFUSED: Homebrew is not available`
- Runtime base names: `python`, `node`, `openjdk`, `ruby`, `go`, `rust`, `php`, `perl`, `postgresql`, `mysql`, `mariadb`, `redis`, `mongodb-community`.
- Package rows (`brew:pkg:<name>`): tier CAUTION, `preselect=False`, never in the headline. Safe item (`brew:cleanup`): tier SAFE, `preselect=True`.
- The page never uses `innerHTML` (guarded by `test_no_html_injection_sinks`). The node harness DOM has no `cloneNode`.
- Commits end with:
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01U65UcZjQqUz3mkd3jHk55q
  ```
- Full suite: `python3 -m unittest discover -s tests -q`. It must stay green after every task.

## Review Focus

1. **A formula with several installed kegs, or none:** shows the linked keg's version, else the newest; a formula whose `installed` list is empty is skipped, not a crash. Test in Task 2.
2. **Homebrew not installed:** no Homebrew rows, no warning, no disk-map bytes. Test in Task 2.
3. **`brew info` works but `cleanup -n` fails or times out:** no Homebrew items at all, one warning. Test in Task 2.
4. **The user runs `brew install` while a clean runs:** the row fails with the busy message, not a raw lock trace. Test in Task 3.
5. **A formula name the regex rejects** (from a tap, or odd characters): shown as kept with no checkbox, never offered. Test in Task 2.

---

### Task 1: Homebrew runner, parsers and fixture safety

**Files:**
- Create: `devclean_brew.py`
- Modify: `devclean_core.py` (`Env`, `Env.real`, `fixture_env`, `fixture_runner`)
- Create: `tests/test_brew.py`, `tests/fixtures/brew/info.json`, `tests/fixtures/brew/cleanup.txt`, `tests/fixtures/brew/autoremove.txt`, `tests/fixtures/brew/uninstall.txt`

**Interfaces:**
- Produces:
  - `devclean_brew.BrewRunner` dataclass: `path: str`, `prefix: str`, `run: Callable`, `service_dirs: tuple`. `cellar` and `caskroom` properties (`<prefix>/Cellar`, `<prefix>/Caskroom`).
  - `BREW_ENV: dict` (the five variables), `NAME_RE`, `SCAN_TIMEOUT = 20`, `CLEAN_TIMEOUT = 300`.
  - `call(brew: BrewRunner, args: list, timeout: float) -> subprocess.CompletedProcess`: runs `brew.run([brew.path, *args], capture_output=True, text=True, timeout=timeout, env={**os.environ, **BREW_ENV})`.
  - `find_brew(home: str) -> Optional[BrewRunner]`: first of `/opt/homebrew/bin/brew`, `/usr/local/bin/brew` that is an executable file; prefix is `dirname(dirname(path))`; `run=subprocess.run`; `service_dirs=(<home>/Library/LaunchAgents, "/Library/LaunchDaemons")`.
  - `Formula` dataclass: `name`, `version`, `time: Optional[float]`, `on_request: bool`, `deps: frozenset` (runtime dependency full names), `deprecated: bool`.
  - `parse_info(text: str) -> dict[str, Formula]`
  - `parse_size(s: str) -> Optional[int]` (`"34.6MB"`, `"64B"`, `"1.2GB"`, `"3KB"`; decimal units, 1000-based)
  - `parse_cleanup(text: str, prefix: str) -> int`: bytes of `Would remove: <path> (…, <size>)` lines whose path is under `prefix + os.sep`
  - `parse_would_autoremove(text: str) -> list[str]`: names listed after a `==> Would autoremove N unneeded formulae:` line
  - `parse_would_uninstall(text: str) -> list[str]`: names from `Would uninstall <name> <version>` lines
  - `Env.brew: Optional[BrewRunner] = None` in `devclean_core`

- [ ] **Step 1: Write the fixtures.** `info.json` is a `brew info --json=v2 --installed` document (`{"formulae": [...], "casks": []}`). Each formula carries the real keys `name`, `full_name`, `installed` (a list of `{version, time, installed_on_request, runtime_dependencies: [{full_name}]}`), `linked_keg`, `deprecated`, `disabled`. It describes:
  - `ffmpeg@6` 6.1.6 and `ffmpeg@8` 8.0.1: on request; deps `libonly6` + `libshared` and `libshared` respectively.
  - `python@3.11` 3.11.14: on request; dep `openssl@3`.
  - `postgresql@15` 15.14: on request.
  - `oldtool` 1.0: on request, `deprecated: true`.
  - `toolx` with two installed kegs, 1.0 and 2.0, `linked_keg` "1.0".
  - `emptyf` with `installed: []`.
  - `Weird_Name`: on request.
  - `libonly6`, `libshared`, `openssl@3`, `orphan`: not on request.

  `cleanup.txt`: two `Would remove:` lines under `/PREFIX/Cellar/…` (`(12 files, 34.6MB)`, `(1 file, 64B)`), one under `~/Library/Caches/Homebrew/…`, one `==> This operation would free approximately …` line, then a `==> Would autoremove 1 unneeded formulae:` block with `orphan`. `autoremove.txt`: that block alone. `uninstall.txt`: `Would uninstall ffmpeg@6 6.1.6`, then `==> Would autoremove 2 unneeded formulae:` with `libonly6` and `orphan`. Tests replace `/PREFIX` with the temp prefix.

- [ ] **Step 2: Write the failing tests** in `tests/test_brew.py`, class `ParseTests`:

```python
def test_parse_size(self):
    p = brew.parse_size
    self.assertEqual((p("34.6MB"), p("64B"), p("1.2GB"), p("3KB")), (34600000, 64, 1200000000, 3000))
    self.assertIsNone(p("lots"))

def test_parse_info(self):
    f = brew.parse_info(read_fixture("info.json"))
    self.assertEqual((f["ffmpeg@6"].version, f["ffmpeg@6"].on_request), ("6.1.6", True))
    self.assertEqual(f["ffmpeg@6"].deps, frozenset({"libonly6", "libshared"}))
    self.assertEqual(f["toolx"].version, "1.0")          # the linked keg
    self.assertNotIn("emptyf", f)                         # nothing installed: skipped
    self.assertTrue(f["oldtool"].deprecated)

def test_parse_cleanup_counts_only_prefix(self):
    text = read_fixture("cleanup.txt").replace("/PREFIX", "/opt/hb")
    self.assertEqual(brew.parse_cleanup(text, "/opt/hb"), 34600000 + 64)

def test_parse_lists(self):
    self.assertEqual(brew.parse_would_autoremove(read_fixture("autoremove.txt")), ["orphan"])
    u = read_fixture("uninstall.txt")
    self.assertEqual(brew.parse_would_uninstall(u), ["ffmpeg@6"])
    self.assertEqual(brew.parse_would_autoremove(u), ["libonly6", "orphan"])
```

  Class `SafetyTests`:

```python
def test_call_sets_brew_env(self):
    rec = []
    b = brew.BrewRunner(path="/x/bin/brew", prefix="/x", run=lambda a, **kw: rec.append((a, kw)) or ok(a), service_dirs=())
    brew.call(b, ["cleanup", "-n"], 20)
    argv, kw = rec[0]
    self.assertEqual(argv, ["/x/bin/brew", "cleanup", "-n"])
    for k in ("HOMEBREW_NO_AUTO_UPDATE", "HOMEBREW_NO_INSTALL_CLEANUP", "HOMEBREW_NO_ANALYTICS",
              "HOMEBREW_NO_ENV_HINTS", "HOMEBREW_NO_COLOR"):
        self.assertEqual(kw["env"][k], "1")
    self.assertNotIn("HOMEBREW_NO_AUTOREMOVE", brew.BREW_ENV)
    self.assertEqual(kw["timeout"], 20)

def test_fixture_runner_refuses_brew(self):
    for argv in (["brew", "cleanup"], ["/opt/homebrew/bin/brew", "uninstall", "--formula", "x"]):
        self.assertNotEqual(core.fixture_runner(argv, capture_output=True, text=True).returncode, 0)

def test_fixture_env_has_no_brew_even_for_real_home(self):
    real = core.Config(home=os.path.expanduser("~"), project_roots=())
    fake = brew.BrewRunner(path="/x/bin/brew", prefix="/x", run=None, service_dirs=())
    with mock.patch.object(brew, "find_brew", return_value=fake):
        self.assertIsNotNone(core.fixture_env(real, lambda: False, fixture=False).brew)
        self.assertIsNone(core.fixture_env(real, lambda: False, fixture=True).brew)
    self.assertIsNone(core.Env.fake().brew)
```

- [ ] **Step 3: Run them and confirm they fail.** Run `python3 -m unittest tests.test_brew -q`. Expected: ImportError / AttributeError.

- [ ] **Step 4: Implement** `devclean_brew.py` with the Interfaces above. Then in `devclean_core.py`:
  - add `brew` to `Env`;
  - `Env.real()` sets `brew=_brew().find_brew(home)`, where `Env.real` gains an optional `home: Optional[str] = None` argument (default `~`; existing tests call `Env.real()` bare), passed from `default_env(cfg)`, and `_brew()` is a lazy import like `_general()`;
  - `fixture_env` also clears `brew`;
  - `fixture_runner` refuses `os.path.basename(argv[0]) == "brew"` with `fixture mode: brew disabled`.

- [ ] **Step 5: Run** `python3 -m unittest tests.test_brew -q` and then the full suite. Expected: all pass.

- [ ] **Step 6: Commit:** `Homebrew: runner, parsers, never in fixture mode`.

---

### Task 2: Scan: packages, notes, sizes, the safe item

**Files:**
- Modify: `devclean_brew.py`, `devclean_core.py` (`ScanResult`, `scan`, `RESCANNERS`)
- Test: `tests/test_brew.py`

**Interfaces:**
- Consumes: Task 1.
- Produces:
  - `scan_brew(cfg, env, warnings: list, sizes: bool = True) -> tuple[list[Item], int]`: items, plus the bytes of Cellar + Caskroom (0 when `sizes` is False or there is no brew).
  - `ScanResult.homebrew_bytes: int = 0`.
  - Item shapes:
    - `brew:cleanup`: category `"Homebrew"`, kind `"brew"`, argv `[path, "cleanup"]`, tier SAFE, `preselect=True`, `meta={"libraries": [...]}`.
    - `brew:pkg:<name>`: category `"Homebrew"`, kind `"brew"`, argv `[path, "uninstall", "--formula", name]`, tier CAUTION, `preselect=False`, `meta={"version", "installed", "libraries"}`.
    - Kept rows: tier PROTECTED, kind `"info"`, same key, no argv.
    - `brew:other-account`: PROTECTED info.
  - `SERVICE_NOTE(name)` and `SERVICE_BLOCK(name)` helpers producing the two service texts.
  - `RESCANNERS["brew:"] = lambda key, cfg, sim, env, now: _brew().scan_brew(cfg, env, [], sizes=False)[0]`

**Rules the tests pin:**
- **Listed:** on request, and not in any installed formula's `deps`.
- **Kept:** a service plist (`homebrew.mxcl.<name>.plist` in any `service_dirs`), or a name failing `NAME_RE`. A failing name gets the note `Can't be removed from here: run brew uninstall <name> yourself.`
- **Exclusive libraries:** the deps of P that are not on request, are not deps of any other on-request formula, and are not in the `autoremove -n` list.
- **Package size:** `du_bytes(<cellar>/<name>)` plus the exclusive libraries' kegs.
- **Notes,** in order:
  - `installed <Mon YYYY>`, using `devclean_apps._month_year`;
  - the version-family note;
  - the runtime note;
  - `Homebrew no longer maintains it` (deprecated or disabled);
  - `plus about <human(lib bytes)> of libraries only it uses`.
- **Version family:** base = name before `@`. Family members are listed formulae with the same base. "Newer" compares `version_key` of the version.
- **Safe item size:** `parse_cleanup` plus the kegs of the `autoremove -n` libraries. It is omitted when 0. Its note:
  - `Removes old versions Homebrew keeps, and libraries nothing uses: a, b`;
  - or `Removes old versions Homebrew keeps.` when there are no libraries;
  - or, when one of those libraries has a service plist, PROTECTED with `SERVICE_BLOCK(lib)`.
- **The three read-only calls** run in a `ThreadPoolExecutor(max_workers=3)`. Any non-zero exit, timeout or `OSError` gives `([], 0)` and appends the exact warning.
- **Not writable:** if `os.access(cellar, os.W_OK)` is false, the only item is `brew:other-account`.

- [ ] **Step 1: Write the failing tests** (class `ScanBrewTests`). `setUp` makes a temp prefix with `Cellar/<name>/<version>/` dirs, a file of known size each for `ffmpeg@6`, `ffmpeg@8`, `libonly6`, `libshared`, `orphan`, `python@3.11`, `oldtool`, `toolx`, `postgresql@15`, `Weird_Name`. It also makes a `Caskroom/sdk/` file and a service dir holding `homebrew.mxcl.postgresql@15.plist`. Then a `fake_brew(prefix, answers, record)` runner that answers the four read-only argv forms from the fixtures and returns exit 1 for anything else.

```python
def test_listed_packages_and_tiers(self):
    d = self.by_key(self.scan())
    pkgs = {k for k in d if k.startswith("brew:pkg:")}
    self.assertEqual(pkgs, {"brew:pkg:ffmpeg@6", "brew:pkg:ffmpeg@8", "brew:pkg:python@3.11",
                            "brew:pkg:oldtool", "brew:pkg:toolx", "brew:pkg:postgresql@15",
                            "brew:pkg:Weird_Name"})
    f6 = d["brew:pkg:ffmpeg@6"]
    self.assertEqual((f6.tier, f6.kind, f6.preselect, f6.argv),
                     (core.CAUTION, "brew", False, [self.brew_path, "uninstall", "--formula", "ffmpeg@6"]))
    self.assertEqual(f6.size, SIZES["ffmpeg@6"] + SIZES["libonly6"])   # libshared is shared, orphan is autoremove's
    self.assertNotIn("brew:pkg:libshared", d)

def test_notes(self):
    d = self.by_key(self.scan())
    self.assertIn("older version · ffmpeg 8 also installed", d["brew:pkg:ffmpeg@6"].note)
    self.assertIn("also installed: ffmpeg@6", d["brew:pkg:ffmpeg@8"].note)
    self.assertIn("Projects or scripts you run may use it", d["brew:pkg:python@3.11"].note)
    self.assertIn("Homebrew no longer maintains it", d["brew:pkg:oldtool"].note)
    self.assertTrue(d["brew:pkg:ffmpeg@6"].note.startswith("installed "))
    self.assertIn("plus about", d["brew:pkg:ffmpeg@6"].note)

def test_service_and_bad_name_are_kept(self):
    d = self.by_key(self.scan())
    pg = d["brew:pkg:postgresql@15"]
    self.assertEqual((pg.tier, pg.kind, pg.argv), (core.PROTECTED, "info", None))
    self.assertEqual(pg.note, "Runs as a background service. Stop it with `brew services stop postgresql@15` first.")
    self.assertFalse(d["brew:pkg:Weird_Name"].offerable)

def test_safe_item(self):
    it = self.by_key(self.scan())["brew:cleanup"]
    self.assertEqual((it.tier, it.preselect, it.argv), (core.SAFE, True, [self.brew_path, "cleanup"]))
    self.assertEqual(it.size, 34600000 + 64 + SIZES["orphan"])
    self.assertEqual(it.note, "Removes old versions Homebrew keeps, and libraries nothing uses: orphan")

def test_safe_item_kept_when_a_library_is_a_service(self):
    write(os.path.join(self.services, "homebrew.mxcl.orphan.plist"))
    it = self.by_key(self.scan())["brew:cleanup"]
    self.assertFalse(it.offerable)
    self.assertEqual(it.note, "orphan runs as a background service. Stop it with `brew services stop orphan` first.")

def test_any_call_failing_offers_nothing(self):
    for broken in ("cleanup", "autoremove", "info"):
        res = self.scan(broken=broken)
        self.assertFalse([i for i in res.items if i.key.startswith("brew:")])
        self.assertIn("couldn't read Homebrew; its packages are not offered", res.warnings)

def test_no_brew_no_rows_no_warning(self):
    res = core.scan(self.cfg, core.NoSimctl(), xcode_check=lambda: False, env=core.Env.fake(), now=self.now)
    self.assertFalse([i for i in res.items if i.key.startswith("brew:")])
    self.assertEqual(res.homebrew_bytes, 0)
    self.assertFalse([w for w in res.warnings if "Homebrew" in w])

def test_other_account(self):
    with mock.patch.object(brew.os, "access", return_value=False):
        d = self.by_key(self.scan())
    self.assertEqual([k for k in d if k.startswith("brew:")], ["brew:other-account"])
    self.assertEqual(d["brew:other-account"].note, "Homebrew belongs to another account on this Mac")

def test_homebrew_bytes_and_toolx_version(self):
    res = self.scan()
    self.assertEqual(res.homebrew_bytes, sum(SIZES.values()) + SIZES_CASK)
    self.assertEqual(self.by_key(res)["brew:pkg:toolx"].meta["version"], "1.0")
```

- [ ] **Step 2: Run them and confirm they fail.** Run `python3 -m unittest tests.test_brew -q`.
- [ ] **Step 3: Implement** `scan_brew` and the helpers. Hook them into `core.scan` after `scan_docker`: `brew_items, brew_bytes = _brew().scan_brew(cfg, env, warnings)`, with `ScanResult(..., homebrew_bytes=brew_bytes)`. Add the `RESCANNERS` entry. In `scan()`'s `size_of`, skip `brew:` keys: sizes are set by `scan_brew`.
- [ ] **Step 4: Run** `tests.test_brew`, then the full suite. Expected: all pass.
- [ ] **Step 5: Commit:** `Homebrew: scan packages, notes and the safe cleanup item`.

---

### Task 3: Gate, running, order, texts, preview

**Files:**
- Modify: `devclean_core.py` (`validate_item`, `execute_item`, `execution_order`, `cost_for`, `explain_for`), `devclean_brew.py`
- Test: `tests/test_brew.py`

**Interfaces:**
- Consumes: Tasks 1-2.
- Produces:
  - `devclean_brew.validate(it, fresh, env) -> None`, which raises `core.UnsafeTarget`. It is called from `validate_item` for kind `"brew"`, after the generic fresh/offerable/argv checks.
  - `devclean_brew.run_item(it, env) -> tuple[bool, str]`.
  - `devclean_brew.preview_uninstall(names: list, env) -> dict`: `{"ok": True, "packages": [...], "libraries": [...]}`, or `{"ok": False, "error": str}`.
  - `BREW_BUSY`, `PREVIEW_FAILED`, `BREW_UNAVAILABLE = "REFUSED: Homebrew is not available"` constants.

**Rules:**
- **The gate:**
  - `env.brew` must not be `None`;
  - `argv[0] == env.brew.path`;
  - `brew:cleanup` must have exactly `[path, "cleanup"]`;
  - `brew:pkg:<n>` must have `NAME_RE.match(n)`, exactly `[path, "uninstall", "--formula", n]`, and `fresh.meta["version"] == it.meta["version"]`. Otherwise `SITUATION_CHANGED`.
  - A `brew:` key whose fresh item is missing raises `SITUATION_CHANGED`, not "is gone".
- **`execute_item`:**
  - kind `"brew"` with `runner is fixture_runner` → `REFUSED: fixture mode never runs brew`;
  - `env.brew is None` → `BREW_UNAVAILABLE`;
  - otherwise `run_item` after `validate_item`.
  - It never goes through the general `runner`.
- **`run_item`:** `call(env.brew, argv[1:], CLEAN_TIMEOUT)`.
  - stderr containing `already locked` or `Operation already in progress` → `(False, BREW_BUSY)`;
  - other failures → Homebrew's stderr;
  - timeout or `OSError` → its message.
- **`execution_order`:** kind `"brew"` keeps head 2. A `_refill` puts `brew:pkg:` keys (by key) before `brew:cleanup`.
- **`cost_for`:**
  - `brew:cleanup` → `("NO COST", "Homebrew doesn't use them.")`;
  - `brew:pkg:` → `("REINSTALL", it.note)`.
- **`explain_for` restore:**
  - `brew:pkg:<n>` → `Removed. Reinstall with `brew install <n>`.`;
  - `brew:cleanup` → `Nothing to get back: Homebrew doesn't use them.`
- **`preview_uninstall`:**
  - names must all match `NAME_RE`, otherwise `ok False`;
  - it runs `uninstall --dry-run --formula *names` with `SCAN_TIMEOUT`;
  - a failure gives `PREVIEW_FAILED`;
  - any package or library with a service plist gives `ok False` with `SERVICE_BLOCK(name)`.

- [ ] **Step 1: Write the failing tests** (class `RunBrewTests`, reusing `ScanBrewTests`' setUp through a shared base):

```python
def test_uninstall_runs_through_env_brew_with_exact_argv(self):
    rec = []
    env = self.env(record=rec)
    it = self.by_key(core.scan(self.cfg, core.NoSimctl(), env=env, now=self.now))["brew:pkg:ffmpeg@6"]
    rec.clear()
    def general(argv, **kw): raise AssertionError("brew went through the general runner")
    ok, msg = core.execute_item(it, self.cfg, runner=general, env=env, now=self.now)
    self.assertTrue(ok, msg)
    self.assertEqual(rec[-1][0], [self.brew_path, "uninstall", "--formula", "ffmpeg@6"])
    self.assertEqual(rec[-1][1]["timeout"], 300)

def test_gate_refuses_forged_argv(self):
    env = self.env()
    for key, argv in (("brew:pkg:ffmpeg@6", [self.brew_path, "uninstall", "--formula", "ffmpeg@6", "--ignore-dependencies"]),
                      ("brew:pkg:ffmpeg@6", [self.brew_path, "uninstall", "--force", "--formula", "ffmpeg@6"]),
                      ("brew:pkg:ffmpeg@6", ["/tmp/brew", "uninstall", "--formula", "ffmpeg@6"]),
                      ("brew:cleanup", [self.brew_path, "cleanup", "--prune=all"]),
                      ("brew:pkg:libshared", [self.brew_path, "uninstall", "--formula", "libshared"])):
        forged = core.Item(key=key, category="Homebrew", label="x", tier=core.CAUTION, kind="brew",
                           argv=argv, meta={"version": "6.1.6"})
        ok, msg = core.execute_item(forged, self.cfg, env=env, now=self.now)
        self.assertFalse(ok)
        self.assertTrue(msg.startswith("REFUSED"), msg)

def test_version_changed_is_situation_changed(self): ...   # re-answer info.json with ffmpeg@6 6.1.7 → "REFUSED: Situation changed: scan again"
def test_service_started_after_scan_is_refused(self): ...  # write the plist after scanning → REFUSED
def test_fixture_runner_and_no_brew_refuse(self): ...      # runner=core.fixture_runner → "fixture mode never runs brew"; env without brew → "REFUSED: Homebrew is not available"; no brew argv recorded

def test_busy(self):
    env = self.env(uninstall_stderr="Error: A `brew install` process has already locked /opt/homebrew/Cellar/x")
    it = self.by_key(core.scan(self.cfg, core.NoSimctl(), env=env, now=self.now))["brew:pkg:ffmpeg@6"]
    self.assertEqual(core.execute_item(it, self.cfg, env=env, now=self.now),
                     (False, "Homebrew is busy with another command. Try again when it's done."))

def test_order_uninstalls_before_cleanup(self):
    d = self.by_key(self.scan())
    keys = [i.key for i in core.execution_order([d["brew:cleanup"], d["brew:pkg:ffmpeg@8"], d["brew:pkg:ffmpeg@6"]])]
    self.assertEqual(keys, ["brew:pkg:ffmpeg@6", "brew:pkg:ffmpeg@8", "brew:cleanup"])

def test_texts(self):
    d = self.by_key(self.scan())
    self.assertEqual(d["brew:pkg:ffmpeg@6"].restore, "Removed. Reinstall with `brew install ffmpeg@6`.")
    self.assertEqual(d["brew:pkg:ffmpeg@6"].cost_tag, "REINSTALL")

def test_preview(self):
    p = brew.preview_uninstall(["ffmpeg@6"], self.env())
    self.assertEqual(p, {"ok": True, "packages": ["ffmpeg@6"], "libraries": ["libonly6", "orphan"]})
    self.assertEqual(brew.preview_uninstall(["a;b"], self.env())["ok"], False)
    self.assertEqual(brew.preview_uninstall(["ffmpeg@6"], self.env(broken="uninstall")),
                     {"ok": False, "error": "Couldn't ask Homebrew what this would remove. Scan again."})
    write(os.path.join(self.services, "homebrew.mxcl.libonly6.plist"))
    self.assertEqual(brew.preview_uninstall(["ffmpeg@6"], self.env())["error"],
                     "libonly6 runs as a background service. Stop it with `brew services stop libonly6` first.")
```

  Write the three `...` tests in full, with the assertions in their comments.
- [ ] **Step 2: Run them and confirm they fail.**
- [ ] **Step 3: Implement** to the rules above.
- [ ] **Step 4: Run** `tests.test_brew` and the full suite. Expected: all pass.
- [ ] **Step 5: Commit:** `Homebrew: safety gate, uninstall/cleanup runs, dry-run preview`.

---

### Task 4: Report rows, disk map, headline, preview endpoint, fake brew for demos

**Files:**
- Modify: `devclean_report.py` (`SEGMENTS`, `MEASURED`, `segment_of`, `build_rows`, `_row`, `GROUP_ORDER`, `disk_map`, `scan_payload`), `devclean_ui.py` (`UIServer`, `Handler._dispatch`, `main`, new `fake_brew`)
- Test: `tests/test_report.py`, `tests/test_ui_server.py`

**Interfaces:**
- Consumes: Tasks 1-3 (`ScanResult.homebrew_bytes`, `preview_uninstall`).
- Produces:
  - **Segment** `("homebrew", "Homebrew")`, placed before `"other"` and in `MEASURED`. `size["homebrew"] = res.homebrew_bytes`. `segment_of` returns `"homebrew"` for category `"Homebrew"`. The `homebrew-cache` row stays in `"caches"`, because its category is `"Cache"`.
  - **Group** `"brew"`, with `GROUP_ORDER["brew"] = 3.6`. `build_rows` puts every `brew:pkg:` key and `brew:other-account` there. `brew:cleanup` follows its tier, so it lands in `"safe"`.
  - **Row fields:** `_row` adds `"version"` for category `"Homebrew"` too.
  - **Headline:** `scan_payload` leaves out `brew:pkg:` keys from `headline.reclaimable`.
  - **`UIServer.brew_preview(body) -> tuple[int, dict]`,** routed from `POST /api/brew-preview`. The body is `{scan_id, keys}`, and every key must be an offerable `brew:pkg:` key of the latest scan; otherwise 400. A stale scan gives 409. On success it returns 200 with the `preview_uninstall` dict; with `env.brew is None` it returns 200 with `{"ok": False, "error": PREVIEW_FAILED}`.
  - **`fake_brew(dir: str) -> BrewRunner`** and the hidden `devclean ui --fake-brew DIR` flag, which needs `--home`. `DIR` holds `info.json`, `cleanup.txt`, `autoremove.txt`, `uninstall.txt` and a `prefix/` folder. It answers only the four read-only forms, and gives exit 1 with `fake brew: only read-only commands are answered` for anything else. `prefix` is `DIR/prefix`, and `service_dirs` is `(DIR/services,)`. Passed the way `docker=` is.

- [ ] **Step 1: Write the failing tests.** In `test_report.py`:
  - `test_homebrew_segment_and_group`: build a `ScanResult` with a `brew:pkg:x` CAUTION item of 5 MB, a `brew:cleanup` SAFE item of 1 MB and `homebrew_bytes=50_000_000`. Assert:
    - the `homebrew` segment `bytes == 50_000_000` and `reclaimable == 6_000_000`;
    - the `brew:pkg:x` row's `group == "brew"` and `brew:cleanup`'s `group == "safe"`;
    - `headline.reclaimable` counts 1 MB, not 6 MB;
    - `"other"` shrank by 50 MB.

  In `test_ui_server.py`:
  - `test_brew_preview_endpoint`, against a fixture server started with a fake brew dir built in a temp folder:
    - a 200 response carrying `libraries` for a `brew:pkg:` key;
    - 400 for `brew:cleanup` or an unknown key;
    - 409 for an old scan id;
    - 403 without the token.
  - `test_fake_brew_refuses_writes`: `fake_brew(dir).run([...,"uninstall","--formula","x"])` exits 1.
  - `test_fake_brew_needs_home`: `main(["--fake-brew", d])` errors.

- [ ] **Step 2: Run them and confirm they fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the full suite. Expected: all pass.
- [ ] **Step 5: Commit:** `Homebrew: disk map block, packages group, preview endpoint, demo fake brew`.

---

### Task 5: Page: group, segment, confirmation with Homebrew's list

**Files:**
- Modify: `ui/index.html` (`GROUPS`, `SEG_COLOR`, `ICON`, CSS `--brew` variable and legend, `openModal`, `blockedReason`)
- Test: `tests/test_ui_server.py` (`PageTests`, plus `PAGE_HARNESS` data)

**Interfaces:**
- Consumes: `POST /api/brew-preview` (Task 4), the row fields (`group: "brew"`, `version`, `cost` = the notes).
- Produces:
  - `GROUPS` entry `{id: "brew", title: "Homebrew packages", desc: <exact copy>, color: "var(--brew)", tier: ""}` after `apps`.
  - `ICON.Homebrew = "Hb"`.
  - `SEG_COLOR.homebrew = "var(--brew)"`.
  - The `--brew` colour is the builder's choice. It must differ from the other segment colours and pass the page's existing contrast on its background.

**Confirmation:**
- `openModal` becomes `async`.
- When any selected key starts with `brew:pkg:`, it first calls `api("POST", "/api/brew-preview", {scan_id, keys})` and keeps the result in `S.modal.brew`.
- The modal then lists:
  - the packages, in the existing "Deleted permanently" section;
  - a `msec` titled `Homebrew also removes these libraries nothing needs any more:`, with one row per library and no size;
  - the line `This can't be undone from the Trash. You can reinstall with `brew install <name>`.`, using the first package's name. It goes in `mSub` after the existing text.
- If the preview fails, `ok` is false, or the request throws, `mwarn` shows the error text, or `PREVIEW_FAILED` when there is none.
- `blockedReason` returns `"homebrew"` while `S.modal.brew` is missing or not ok, and only then.
- A row with no keys in group `brew` shows "Keep" with its note, like Applications keepers. "Select all" ticks only offered rows.

- [ ] **Step 1: Write the failing tests.**
  - `test_page_has_homebrew_group`: the HTML contains the exact title and description, `Hb`, `homebrew:`.
  - `test_page_homebrew_confirm_in_node`: extend `PAGE_HARNESS` data with a `brew` group row with keys, a kept `brew` row, and a stubbed `api` that answers `/api/brew-preview` once with `{ok:true, packages:["ffmpeg@6"], libraries:["libonly6","orphan"]}` and once with `{ok:false, error:"x runs as a background service. Stop it with `brew services stop x` first."}`. Assert:
    - the modal texts include the libraries heading and both names;
    - `blockedReason` is `""` after the ok preview and `"homebrew"` after the failed one;
    - the failed one's error is in `mwarn`;
    - the kept row has no checkbox;
    - nothing was sent to `/api/clean` while blocked.
  - `test_no_html_injection_sinks` still passes unchanged.
- [ ] **Step 2: Run them and confirm they fail.** Run `python3 -m unittest tests.test_ui_server -q`.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the full suite. Expected: all pass.
- [ ] **Step 5: Browser check on a fixture server only.**
  1. Build a demo dir from the test fixtures in the scratchpad, then run `./devclean ui --home <tmp home> --fake-brew <demo dir> --no-open`.
  2. Screenshot the Homebrew packages group, the disk map legend, and the confirmation for ffmpeg@6 with its libraries, using the browser tools.
  3. Press Cancel. Do not press Delete: the fake brew would refuse it anyway, but the rule stands.
- [ ] **Step 6: Commit:** `ui: Homebrew packages group, disk map colour, confirmation lists Homebrew's own dry run`.

---

### Task 6: CLI, README, version

**Files:**
- Modify: `devclean` (`main`, `print_table`), `README.md`, `VERSION`, `docs/superpowers/specs/2026-11-24-homebrew-cleanup-design.md` (Status line only, if needed)
- Test: `tests/test_devclean.py`

**Interfaces:**
- Consumes: `preview_uninstall` (Task 3), items (Task 2).

**Rules:**
- **When chosen items include `brew:pkg:` keys,** the CLI runs `preview_uninstall(names, env)` after "Will delete"/"Would run" and before the commands list. It prints `Homebrew also removes these libraries nothing needs any more:` followed by one indented name per line.
- **If the preview fails,** it prints the error in red. `--dry-run` still exits 0; a real run prints `Nothing deleted.` and returns 1.
- **`--yes`** already keeps only `SAFE` + `preselect`, which is `brew:cleanup`. Pin that with a test.
- **README:**
  - tiers table: safe gains "old Homebrew versions and unneeded libraries (`brew cleanup`)"; caution gains "Homebrew packages you installed (`brew uninstall`)";
  - "What it can clean" gets a **Homebrew** bullet;
  - the constraints list says tests never run `brew`.
- **`VERSION`:** `0.3.0`. That's the only file the 0.2.1 bump touched; the build number comes from it.

- [ ] **Step 1: Write the failing tests** in `test_devclean.py`. Run `main()` with a patched `core.fixture_env`/`default_env` that returns an env carrying the fake brew runner from `tests/test_brew.py`, on a temp home, so no real `brew` can run.
  - `test_cli_dry_run_prints_homebrew_preview`: `--dry-run --select all` prints the libraries heading, `libonly6`, and the exact uninstall command line.
  - `test_cli_yes_runs_only_brew_cleanup`: the recorded brew argv under `--yes` contains `[path, "cleanup"]` and no `uninstall`.
  - `test_cli_preview_failure_deletes_nothing`.
- [ ] **Step 2: Run them and confirm they fail.**
- [ ] **Step 3: Implement,** and update README and VERSION.
- [ ] **Step 4: Run** the full suite. Expected: all pass.
- [ ] **Step 5: Commit:** `Version 0.3.0: Homebrew cleanup`.

---

## Finish

- Final whole-branch review on the most capable model.
- A read-only real check:
  - Run `./devclean --dry-run --select none` on the real home. It lists Homebrew rows and runs only read-only `brew` calls; `--dry-run` never deletes.
  - Confirm the 21 packages, `postgresql@15` kept, and the safe item's libraries.
  - Nothing is cleaned.
- The user builds, installs and publishes 0.3.0 themselves.
