# Package caches, browser AI models and caches, sign-in caches, cloud-only files — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Labyrinth Cleaner 0.4.0. It offers `~/.cache`, pip, uv and pnpm caches, Chromium browsers' AI models and page caches, and a sign-in warning on cache folders. It also stops listing cloud-only files in the Downloads review.

**Architecture:**
- Each part follows an existing pattern:
  - `~/.cache` copies the app-cache scan;
  - uv and pnpm copy Docker's exact-argv command items, through a new `devclean_tools.py`;
  - the browsers get a new `devclean_browsers.py`, gated by `validate_path` and the fresh-rescan gate.
- A new item kind, `"paths"`, deletes several exact folders as one row: `rm -rf -- p1 p2 …`. Both the Hugging Face row and the browser page-cache rows use it.
- Sizes that are only an upper bound (pnpm) carry `meta["upper_bound"] = True` and are left out of every total.

**Tech Stack:** Python 3 standard library and `unittest`. The page is one `ui/index.html`, tested through the node harness in `tests/test_ui_server.py`.

**Spec:** `docs/superpowers/specs/2026-11-25-caches-browsers-cloud-files-design.md`

**Run all tests:** `python3 -m unittest discover -s tests`
**Run one test:** `python3 -m unittest discover -s tests -p test_general.py -k test_name -v`

## Global Constraints

- Version 0.4.0 (`VERSION`).
- No Full Disk Access and no admin rights. A note may say "Size unavailable without Full Disk Access"; nothing ever asks for it.
- **Tests stay off the real system.**
  - They never run the real `uv`, `pnpm`, `brew`, `xcrun`, `docker`, `osascript`, `lsappinfo`, `mdls`, `sysctl`, `xcode-select` or launchctl.
  - They never read the real `/Applications` or a real browser folder.
  - Temp homes are `os.path.realpath(tempfile.mkdtemp())`.
  - `Env.fake()` has no tool runner and no `find_tool` results.
- **Development never runs a real clean** on this Mac: no `uv cache clean`, no `pnpm store prune`, no `rm` outside a temp home. Read-only looks at folder names and sizes are allowed.
- **Never use `innerHTML`** in `ui/index.html` (`test_no_html_injection_sinks`). The node harness DOM has no `cloneNode`.
- **Inside a browser's data folder, only these exact names may ever be deleted:**
  - models: `OptGuideOnDeviceModel`, `OptGuideOnDeviceClassifierModel`, `optimization_guide_model_store`, `OnDeviceHeadSuggestModel`, `screen_ai`, `SODA`, `SODALanguagePacks`;
  - shared: `GrShaderCache`, `ShaderCache`, `GraphiteDawnCache`;
  - per profile: `Cache`, `Code Cache`, `GPUCache`, `DawnGraphiteCache`, `DawnWebGPUCache`.

  Never in `System Profile` or `Guest Profile`, and never while that browser runs.
- **In `~/.cache`:**
  - `~/.cache/devclean` is never listed;
  - `~/.cache/huggingface` is never deleted whole, only its `hub`, `datasets` and `xet` folders.
- **uv and pnpm:**
  - the argv is exactly `[<binary>, "cache", "clean"]` and `[<binary>, "store", "prune"]`;
  - they run with `env={"HOME": home, "PATH": f"{dirname(binary)}:/usr/bin:/bin"}`, `cwd=home`, `timeout=600`;
  - the binary is searched in this order: `/opt/homebrew/bin`, `/usr/local/bin`, `~/.local/bin`, `~/.cargo/bin` (uv), `~/Library/pnpm` (pnpm), `~/.volta/bin`.
- **Exact copy:**
  - sign-in warning: "It may hold sign-in data. Deleting it can sign you out."
  - unknown `~/.cache` folder warning: "A tool may use this cache."
  - locked browser note: "{Browser}'s folder is locked by macOS. Its AI models and caches can't be measured."
  - running browser note: "Quit {Browser} first".
  - page-cache cost: "Pages and scripts load a bit slower the first time. You stay logged in."
  - scam-model warning: "Scam and phishing warnings may be weaker until the browser downloads it again."
  - missing tool note: "Couldn't find `uv`. Run `uv cache clean` in Terminal." (pnpm: "Couldn't find `pnpm`. Run `pnpm store prune` in Terminal.")
  - tool timeout: "uv is busy. Try again when it's done." (pnpm likewise).
- **The page never preselects anything new.** The CLI preselects, and `--yes` covers, only these SAFE developer rows: `pip-cache`, `dotcache:puppeteer`, `dotcache:pip`, `tool:uv`, `tool:pnpm`. Never browser rows, unknown `~/.cache` rows, Hugging Face or torch.
- **Commits end with:**
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01U65UcZjQqUz3mkd3jHk55q
  ```

## Review Focus

1. **A browser opened between scan and clean.** The gate must refuse its rows ("Quit Chrome first"), never `rm` while it runs. Pinned in Task 5: `test_gate_refuses_when_browser_started_after_scan`.
2. **A symlinked browser data folder, profile or cache folder** (for example `Default/Code Cache -> ~/Documents`). It must never be offered or deleted. Pinned in Task 5: `test_symlinked_data_folder_or_cache_refused`.
3. **`~/.cache` itself a symlink, or a child that links elsewhere.** It must not be offered. Pinned in Task 3: `test_dot_cache_symlinks_not_offered`.
4. **The uv binary removed or swapped between scan and clean.** The gate must refuse ("Situation changed: scan again") rather than run another binary. Pinned in Task 4: `test_gate_refuses_when_binary_changed`.
5. **A Downloads file evicted to iCloud after the scan.** The Trash move must be refused, not move a cloud-only file. Pinned in Task 1: `test_gate_refuses_file_that_became_dataless`.

---

### Task 1: Cloud-only files never in the Downloads review

**Files:**
- Modify: `devclean_core.py` (`_download_item` ~L1129, `validate_item` ~L1874)
- Test: `tests/test_general.py` (`DownloadsTests`)

**Interfaces:**
- Produces: `core.SF_DATALESS = 0x40000000`; `core.is_dataless(st: os.stat_result) -> bool` (`bool(getattr(st, "st_flags", 0) & SF_DATALESS)`).

- [ ] **Step 1: Write the failing tests** in `DownloadsTests`.

```python
def test_dataless_file_not_listed(self):
    # an old .dmg that would be listed, made to look cloud-only
    path = os.path.join(self.dl, "old.dmg"); sparse(path, 10 * MB, age_days=30, now=self.now)
    real = os.lstat
    def fake(p, *a, **k):
        st = real(p, *a, **k)
        if p == path:
            return _with_flags(st, core.SF_DATALESS)
        return st
    with mock.patch.object(core.os, "lstat", fake):
        keys = {i.key for i in core.scan_downloads(self.cfg, core.Env.fake(), self.now)}
    self.assertNotIn("download:old.dmg", keys)

def test_gate_refuses_file_that_became_dataless(self):
    # scanned while local, then evicted: validate_item raises SITUATION_CHANGED
    ...  # scan → item; patch os.lstat as above; assertRaisesRegex(core.UnsafeTarget, "Situation changed")
```

Write `_with_flags(st, flags)` in the test module. It returns a `types.SimpleNamespace` copying `st_mode`, `st_size`, `st_mtime`, `st_ino`, `st_blocks` and `st_dev` from `st`, with `st_flags=flags`.

- [ ] **Step 2: Run them.** Expected: FAIL. The file is listed, and the gate says "is gone" or passes.

- [ ] **Step 3: Implement.**
  - In `_download_item`, right after the `S_ISREG` check: `if is_dataless(st): return None`.
  - In `validate_item`, extend the `fresh is None` branch: an item whose key starts with `"download:"` and whose `it.path` still `os.path.lexists` raises `UnsafeTarget(SITUATION_CHANGED)`, the same as `"app:"` does today.

- [ ] **Step 4: Run** `python3 -m unittest discover -s tests -p test_general.py -v`. Expected: all pass, including the existing sparse-file tests.

- [ ] **Step 5: Commit** with the message "Downloads review: never list cloud-only (dataless) files".

---

### Task 2: Sign-in caches go under "Check first"

**Files:**
- Modify: `devclean_general.py` (constants near L20; `scan_app_caches` ~L196)
- Test: `tests/test_general.py` (new `SignInCacheTests(GeneralBase)`)

**Interfaces:**
- Produces:
  - `general.SIGNIN_WARNING` (exact copy from Global Constraints);
  - `general.signin_like(name: str) -> bool`;
  - `general.SIGNIN_SUBSTRINGS = ("password", "1password", "bitwarden", "lastpass", "dashlane", "keychain", "credential")`;
  - `general.SIGNIN_WORDS = frozenset({"auth", "oauth", "oauth2", "token", "tokens", "account", "accounts", "login", "logins", "signin", "sso"})`.

  Names are lower-cased. A word match splits on `re.split(r"[^a-z0-9]+", name.lower())`.

- [ ] **Step 1: Write the failing tests.**

```python
def test_signin_like_lists(self):
    for n in ("com.agilebits.onepassword7", "Bitwarden", "oauth-cache", "com.example.login",
              "my_tokens", "SSO", "KeychainHelper"):
        self.assertTrue(general.signin_like(n), n)
    for n in ("author", "authority", "com.spotify.client", "Google", "accountant-ish", "loginitems"):
        self.assertFalse(general.signin_like(n), n)

def test_signin_cache_becomes_caution_with_warning(self):
    os.makedirs(os.path.join(self.caches, "com.example.login")); write(os.path.join(self.caches, "com.example.login", "f"), "x")
    os.makedirs(os.path.join(self.caches, "oauth-cache")); write(os.path.join(self.caches, "oauth-cache", "f"), "x")
    d = self.by_key(self.scan())
    self.assertEqual((d["appcache:com.example.login"].tier, d["appcache:com.example.login"].warning),
                     (core.CAUTION, general.SIGNIN_WARNING))      # would have been SAFE
    self.assertEqual(d["appcache:oauth-cache"].warning, general.SIGNIN_WARNING)  # replaces the generic one

def test_signin_cache_of_running_app_stays_kept(self):
    # running_apps=[("com.example.login", "Example")] → tier PROTECTED, note "Quit Example first"
```

Check before relying on `"accountant-ish"` and `"loginitems"`: they must not match. `accountant` isn't in the list, and `loginitems` is a single word that isn't in it either.

- [ ] **Step 2: Run them.** Expected: FAIL, because `signin_like` isn't defined.

- [ ] **Step 3: Implement `signin_like`** and apply it in `scan_app_caches`. The owner/running branch stays as it is. Otherwise, when `signin_like(name)`:
  - a would-be SAFE item becomes CAUTION with `warning=SIGNIN_WARNING`;
  - a would-be CAUTION item gets `SIGNIN_WARNING` instead of `UNKNOWN_CACHE_WARNING`.

- [ ] **Step 4: Run** `test_general.py`. Expected: all pass.

- [ ] **Step 5: Commit** with the message "App caches: sign-in and password-manager caches go under Check first".

---

### Task 3: pip cache, `~/.cache` folders, and the `"paths"` kind

**Files:**
- Modify: `devclean_core.py`:
  - `Config.fixed_targets` ~L135;
  - `FIXED_COSTS` ~L1264;
  - `RESCANNERS` ~L1690;
  - `scan()` ~L1497: call the new scanner after `scan_app_caches`, and size `"paths"` items in `size_of`;
  - `validate_path` ~L1581: rules 4d and 4e;
  - `validate_item`: the `"paths"` branch;
  - `cost_for` and `explain_for`.
- Modify: `devclean_general.py` (new scanner and table)
- Test: `tests/test_general.py` (new `DotCacheTests(GeneralBase)`); `tests/test_devclean.py` (`--yes` picks pip and puppeteer)

**Interfaces:**
- Consumes: `general.signin_like`, `general.SIGNIN_WARNING` (Task 2).
- Produces:
  - **Fixed target** `("pip-cache", "pip cache", "Library/Caches/pip", SAFE, "re-downloaded on demand")`, with `FIXED_COSTS["pip-cache"] = ("NO COST", "Re-downloaded on demand.")`.
  - **`general.DOT_CACHE_KNOWN`**, mapping `name -> (label, tier, cost_tag, cost)`:
    - `"puppeteer": ("Puppeteer browsers", SAFE, "~1 MIN", "Puppeteer downloads its browser again on the next run.")`
    - `"huggingface": ("Hugging Face models", CAUTION, None, "Downloaded AI models. Scripts that use them download them again, which can take a long time. Your huggingface-cli login is kept.")`
    - `"torch": ("PyTorch models", CAUTION, None, "Downloaded AI models. Scripts that use them download them again, which can take a long time.")`
    - `"pip": ("pip cache", SAFE, "NO COST", "Re-downloaded on demand.")`

    A `None` tag means `f"{human(size)} DL"`.
  - **`general.DOT_CACHE_SKIP = frozenset({"devclean", "uv"})`.** uv belongs to Task 4.
  - **`general.HF_DOWNLOAD_DIRS = ("hub", "datasets", "xet")`.**
  - **`general.UNKNOWN_TOOL_CACHE_WARNING = "A tool may use this cache."`**
  - **`general.scan_dot_cache(cfg, env, warnings=None) -> list`.** Items have key `dotcache:<name>`, category `"Cache"`, `preselect = (tier == SAFE)`.
    - Hugging Face is kind `"paths"` with `argv = ["rm", "-rf", "--", *existing HF_DOWNLOAD_DIRS paths]` and `path = ~/.cache/huggingface`. There's no row when none of those folders exist.
    - Every other folder is kind `"path"`.
    - Unknown folders are CAUTION, with cost `("NO COST", "The tool rebuilds it as needed.")` and warning `UNKNOWN_TOOL_CACHE_WARNING`. Run `signin_like` over the name as in Task 2.
    - Skip:
      - `~/.cache` itself when it's missing or a symlink;
      - children that are symlinks or not folders;
      - names in `DOT_CACHE_SKIP`;
      - realpaths equal to a fixed target's.
  - **New kind `"paths"`:**
    - `size_of` sums `du_bytes` over `argv[3:]` that are folders.
    - `explain_for` sets `deletes` to `", ".join(tilde(p) for p in argv[3:])`.
    - `validate_item` requires `it.argv[:3] == ["rm", "-rf", "--"]` and `len(it.argv) > 3`, then runs `validate_path` on each of `argv[3:]`.
    - The scan's own gate loop in `scan()` checks offerable `"paths"` items the same way. Today it checks only kind `"path"`. A failing path turns the item into a PROTECTED info row "refused by safety gate: …".
    - `execute_item` runs `it.argv` through `runner`, like a path item.
  - **`validate_path` rule 4d:** a direct child of `realpath(~/.cache)` whose name isn't in `DOT_CACHE_SKIP` or `"huggingface"`.
  - **`validate_path` rule 4e:** a direct child of `realpath(~/.cache/huggingface)` whose name is in `HF_DOWNLOAD_DIRS`.
  - **`RESCANNERS`:** `"dotcache:"` maps to `scan_dot_cache`; `"pip-cache"` maps to `_rescan_fixed`.

- [ ] **Step 1: Write the failing tests.**

```python
def test_dot_cache_rows(self):
    dc = os.path.join(self.home, ".cache")
    for n in ("puppeteer", "torch", "mystery", "devclean", "uv"):
        write(os.path.join(dc, n, "f.bin"), "x" * 4096)
    for n in ("hub", "datasets"):
        write(os.path.join(dc, "huggingface", n, "f.bin"), "x" * 4096)
    write(os.path.join(dc, "huggingface", "token"), "hf_secret")
    d = self.by_key(self.scan())
    self.assertEqual((d["dotcache:puppeteer"].tier, d["dotcache:puppeteer"].preselect), (core.SAFE, True))
    self.assertEqual(d["dotcache:torch"].tier, core.CAUTION)
    self.assertEqual((d["dotcache:mystery"].tier, d["dotcache:mystery"].warning),
                     (core.CAUTION, general.UNKNOWN_TOOL_CACHE_WARNING))
    hf = d["dotcache:huggingface"]
    self.assertEqual((hf.kind, hf.argv), ("paths", ["rm", "-rf", "--", os.path.join(dc, "huggingface", "hub"),
                                                    os.path.join(dc, "huggingface", "datasets")]))
    self.assertNotIn("dotcache:devclean", d); self.assertNotIn("dotcache:uv", d)

def test_huggingface_clean_keeps_token(self):
    # execute_item(hf, cfg, runner=subprocess.run, env=Env.fake()) → ok; token file still there, hub gone
def test_dot_cache_symlinks_not_offered(self):
    # (a) ~/.cache/linked -> self.home: no "dotcache:linked"
    # (b) ~/.cache itself a symlink to a folder with "puppeteer": no dotcache rows at all
def test_paths_gate_refuses_foreign_path(self):
    # an hf item whose argv gets an extra "~/Documents" path → validate_item raises UnsafeTarget
def test_pip_fixed_target(self):
    # Library/Caches/pip exists → "pip-cache" SAFE preselect; no "appcache:pip"
```

In `tests/test_devclean.py`, add `test_yes_includes_pip_and_puppeteer`. Using the existing `--yes --dry-run` pattern in that file, check that the "Would run" list includes `Library/Caches/pip` and `.cache/puppeteer`, and doesn't include `.cache/torch` or `.cache/mystery`.

- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement** to the Interfaces above.
- [ ] **Step 4: Run all tests.** Expected: all pass. Existing app-cache tests should still skip `pip` once it's a fixed target.
- [ ] **Step 5: Commit** with the message "Caches: pip as a named cache, ~/.cache folders, Hugging Face keeps its login".

---

### Task 4: uv and pnpm through their own commands

**Files:**
- Create: `devclean_tools.py`
- Modify: `devclean_core.py`:
  - `Env` adds `find_tool: Callable[[str], Optional[str]] = lambda name: None` and `tool_runner: Optional[Callable] = None`;
  - `Env.real` sets `find_tool=lambda n: tools.find_tool(n, home)` and `tool_runner=subprocess.run`;
  - `fixture_env` sets `tool_runner=None`;
  - `fixture_runner` refuses argv whose basename is `uv` or `pnpm`;
  - `scan()` calls `scan_tools`;
  - `size_of` measures kind `"tool"` by its `path`;
  - `RESCANNERS["tool:"]`;
  - `validate_item` and `execute_item` get `"tool"` branches;
  - `cost_for` and `explain_for`.
- Test: `tests/test_tools.py` (new)

**Interfaces:**
- Produces (`devclean_tools.py`):
  - `TOOLS = {"uv": {"folder": ".cache/uv", "args": ["cache", "clean"]}, "pnpm": {"folder": "Library/pnpm/store", "args": ["store", "prune"]}}`
  - `SEARCH_DIRS = {"uv": [...], "pnpm": [...]}`, in the Global Constraints order.
  - `find_tool(name: str, home: str) -> Optional[str]`: the first path that is a file and executable (`os.access(X_OK)`). It may be a symlink, as Volta's shim is.
  - `scan_tools(cfg, env) -> list`: one item per tool whose folder exists. uv also needs the folder to be non-empty.
    - Key `tool:<name>`, category `"Cache"`, kind `"tool"`, `path` = the folder, `argv = [binary, *args]`, tier SAFE, `preselect=True`.
    - pnpm adds `meta={"upper_bound": True}`.
    - When `env.find_tool(name)` is None: tier PROTECTED, kind `"info"`, `argv=None`, with the missing-tool note.
  - `validate(it, fresh, env) -> None`: raises `core.UnsafeTarget(core.SITUATION_CHANGED)` unless:
    - `it.key[5:]` is in `TOOLS`;
    - `it.argv[1:] == TOOLS[name]["args"]`;
    - `it.argv[0] == env.find_tool(name)`.
  - `run_item(it, env, home) -> (bool, str)`: runs `env.tool_runner(it.argv, capture_output=True, text=True, cwd=home, env={...}, timeout=600)`.
    - `subprocess.TimeoutExpired` returns `(False, "<name> is busy. Try again when it's done.")`.
    - A non-zero exit returns `(False, f"{stderr or 'exit N'} Run `{name} {' '.join(args)}` in Terminal.")`.
  - Cost text:
    - uv: `("NO COST", "uv downloads packages again when it needs them. Existing environments keep working. May free less if your environments share files with it.")`
    - pnpm: `("NO COST", "Removes packages no project uses. Projects keep working.")`
  - Restore text: "Downloaded again on demand." (category `"Cache"` already gives this).

- [ ] **Step 1: Write the failing tests** (`tests/test_tools.py`, temp home, `Env.fake(find_tool=..., tool_runner=FakeRunner())`).

```python
def test_find_tool_order_and_volta_symlink(self):
    # fake home with ~/.local/bin/uv (exec) and ~/.volta/bin/pnpm -> shim (exec); find_tool returns those;
    # SEARCH_DIRS' /opt/homebrew/bin and /usr/local/bin are patched to temp dirs so the real ones are never read
def test_rows_when_tools_found(self):
    # uv: argv == [<bin>, "cache", "clean"], SAFE, preselect; pnpm: meta upper_bound True
def test_missing_tool_is_kept_with_terminal_hint(self):
    # find_tool → None: tier PROTECTED, note == "Couldn't find `uv`. Run `uv cache clean` in Terminal."
def test_empty_uv_folder_no_row(self):
def test_run_uses_minimal_env_and_home_cwd(self):
    # FakeRunner records kwargs: env == {"HOME": home, "PATH": f"{dirname(bin)}:/usr/bin:/bin"}, cwd == home, timeout == 600
def test_timeout_message(self):
def test_gate_refuses_when_binary_changed(self):
    # scan with find_tool → /a/uv, then validate with find_tool → /b/uv → UnsafeTarget "Situation changed"
def test_fixture_mode_never_runs_tools(self):
    # execute_item(item, cfg, runner=core.fixture_runner, env=...) → (False, "REFUSED: fixture mode never runs uv")
    # and fixture_runner(["/x/uv", "cache", "clean"]) returns exit 1 without running
```

- [ ] **Step 2: Run** `python3 -m unittest discover -s tests -p test_tools.py -v`. Expected: FAIL, because there's no module yet.
- [ ] **Step 3: Implement.**
  - In `execute_item`, before validation:
    - kind `"tool"` with `runner is fixture_runner` returns `(False, f"REFUSED: fixture mode never runs {name}")`;
    - `env.tool_runner is None` returns `(False, "REFUSED: <name> is not available")`.
  - After validation, call `_tools().run_item(it, env, cfg.home)`. Tools never go through `runner`.
  - Import lazily (`_tools()`), like `_brew()`.
- [ ] **Step 4: Run all tests.** Expected: all pass.
- [ ] **Step 5: Commit** with the message "uv and pnpm caches, cleaned with their own commands".

---

### Task 5: Chromium browsers — AI models and page caches

**Files:**
- Create: `devclean_browsers.py`
- Modify: `devclean_core.py`:
  - `scan()` calls `scan_browsers` after `scan_app_caches`;
  - `validate_path` rule 4f: `_browsers().allowed_path(real, cfg)` returns `real`;
  - `RESCANNERS["browser:"]`;
  - `cost_for` and `explain_for` for category `"Browser"`.
- Test: `tests/test_browsers.py` (new)

**Interfaces:**
- Consumes: kind `"paths"` (Task 3).
- Produces (`devclean_browsers.py`; credit MacSweep, MIT, in the module docstring):
  - **`BROWSERS`**: a list of `(name, bundle_id, data_rel, own_profile)`. `data_rel` is relative to `~/Library/Application Support`. The rows are the spec's table; `own_profile=True` only for Opera and Opera GX.
  - **`MODELS`**: a list of `(folder, label, tier, what)`, from the spec's table, with the scam model CAUTION and the rest SAFE.
  - **`SHARED_CACHES`**, **`PROFILE_CACHES`** and **`HIDDEN_PROFILES = ("System Profile", "Guest Profile")`**.
  - **Copy:** `LOCKED_NOTE`, `RUNNING_NOTE`, `PAGE_CACHE_COST`, `SCAM_WARNING` (exact copy in Global Constraints).
  - **`REDOWNLOAD = "{name} downloads it again unless you turn off on-device AI in its settings."`** For Chrome, Chrome Beta and Chrome Canary, add " (Settings › System › On-device AI)". Check this path against Chrome's current settings before shipping; ledger note if it differs.
  - **`BROWSERS_UNLISTED_WARNING = "couldn't list running apps; browser caches are not offered"`**.
  - **`profiles(data: str, own_profile: bool) -> list`**: the subfolder names holding a `Preferences` file, excluding `HIDDEN_PROFILES` and symlinks. When `own_profile` and `data/Preferences` exists, it also includes `""`, meaning the data folder itself.
  - **`scan_browsers(cfg, env, warnings=None) -> list`.** For each browser whose data folder exists:
    - **Locked:** `os.listdir` raises `PermissionError`. Add one PROTECTED info row `browser:<bundle_id>:locked`, labelled `name`, with `LOCKED_NOTE`.
    - **Symlinked:** the data folder, or any folder between it and `Application Support`, is a symlink. Skip the browser.
    - **Models:** for each present model folder with `du_bytes > 0`, add an item `browser:<bundle_id>:model:<folder>`:
      - category `"Browser"`, label `f"{name}: {label}"`, kind `"path"`, the size set, `preselect=False`;
      - `meta={"what": what, "version": <newest dotted-digits child or None>}`;
      - the scam model's `warning=SCAM_WARNING`.
    - **Page caches:** when any listed cache folder exists, add one item `browser:<bundle_id>:caches`:
      - label `f"{name} page caches"`, kind `"paths"`, `argv=["rm", "-rf", "--", *existing]`, `path=data`, tier SAFE, `preselect=False`;
      - `existing` lists the shared caches in `SHARED_CACHES` order, then each profile from `profiles()` in sorted order with its `PROFILE_CACHES` in order;
      - skip any cache folder that is a symlink.
    - **Running:** when the browser's bundle ID is in `env.running_apps()`, every row above becomes PROTECTED kind `"info"` with `RUNNING_NOTE`, and `argv=None`.
    - **Can't tell:** when `running_apps` raises, return `[]` and append `BROWSERS_UNLISTED_WARNING`.
  - **`allowed_path(real: str, cfg) -> bool`.** True only when `real` is one of these, for some browser whose data folder isn't a symlink and has no symlinked parent up to `Application Support`:
    - `data/<MODELS folder or SHARED_CACHES name>`;
    - `data/<profile>/<PROFILE_CACHES name>`, with `profile` in `profiles(data, own)`;
    - for `own_profile` browsers whose `data/Preferences` exists, `data/<PROFILE_CACHES name>`.
  - **`cost_for` for category `"Browser"`:**
    - models: `(f"{human(size)} DL", f"{what} {REDOWNLOAD}")`;
    - caches: `("SLOWER", PAGE_CACHE_COST)`.
  - **`explain_for` restore:**
    - models: `"The browser downloads it again unless on-device AI is off."`;
    - caches: `"The browser rebuilds them as you browse."`

- [ ] **Step 1: Write the failing tests** (`tests/test_browsers.py`; a temp home with fixture folders under `Library/Application Support`).

```python
def test_models_and_page_caches(self):
    # Chrome: OptGuideOnDeviceModel/2026.2.12.1554/weights.bin, Default/{Preferences,Code Cache/x,GPUCache/x,Cookies,Local Storage/x},
    # Profile 1/{Preferences,Cache/x}, Guest Profile/{Preferences,Cache/x}, GrShaderCache/x, Login Data
    # → model row "browser:com.google.Chrome:model:OptGuideOnDeviceModel" label "Chrome: Gemini Nano", SAFE, version "2026.2.12.1554"
    # → caches row argv == ["rm","-rf","--", <GrShaderCache>, <Default/Code Cache>, <Default/GPUCache>, <Profile 1/Cache>]
    # Guest Profile, Cookies, Local Storage, Login Data appear in no argv
def test_scam_model_is_caution_with_warning(self):
def test_empty_model_folder_not_listed(self):
def test_running_browser_rows_kept(self):          # running_apps=[("com.google.Chrome","Google Chrome")] → all PROTECTED, note "Quit Chrome first"
def test_running_apps_unreadable_offers_nothing(self):
def test_locked_folder_row(self):                 # chmod 000 on the data folder (restore in addCleanup) → one row with LOCKED_NOTE.format(name="Chrome")
def test_opera_own_profile(self):                 # com.operasoftware.Opera/{Preferences, Code Cache/x} → caches row includes data/"Code Cache"
def test_clean_deletes_only_listed_folders(self):  # execute_item(caches_row, cfg, runner=subprocess.run, env=fake) → Cookies, Local Storage, Login Data, Guest Profile/Cache survive
def test_gate_refuses_when_browser_started_after_scan(self):
    # scan with running_apps=[] then validate_item with running_apps=[("com.google.Chrome","Google Chrome")] → UnsafeTarget
def test_symlinked_data_folder_or_cache_refused(self):
    # (a) Default/Code Cache -> <home>/Documents/x: not in argv, and validate_path(<that path>) raises
    # (b) "Google/Chrome" itself a symlink: no Chrome rows; allowed_path False
def test_allowed_path_rejects_everything_else(self):
    # Default/Cookies, Default/Local Storage, Default/IndexedDB, Guest Profile/Cache, data itself, data/Default → validate_path raises
```

- [ ] **Step 2: Run** `python3 -m unittest discover -s tests -p test_browsers.py -v`. Expected: FAIL, because there's no module yet.
- [ ] **Step 3: Implement** to the Interfaces above. Import lazily as `_browsers()` in core.
- [ ] **Step 4: Run all tests.** Expected: all pass.
- [ ] **Step 5: Commit** with the message "Browsers: on-device AI models and page caches for Chromium browsers".

---

### Task 6: Report, page, CLI, README and version

**Files:**
- Modify:
  - `devclean_core.py`: `counted_size(it) -> int` (0 when `it.meta.get("upper_bound")`, else `it.size or 0`), used by `ScanResult.reclaimable`;
  - `devclean_report.py`: `SEGMENTS`, `MEASURED`, `ITEM_SIZED`, `segment_of`, headline, `disk_map` reclaim, `_row`;
  - `devclean` (the CLI): `summarize` and the "Would run / Will delete" total use `counted_size`; an upper-bound item prints "up to" before its size;
  - `ui/index.html`: segment colour and abbreviation, `sum()`, the "up to" size;
  - `README.md`;
  - `VERSION`.
- Test: `tests/test_report.py`, `tests/test_ui_server.py` (node harness), `tests/test_devclean.py`

**Interfaces:**
- Consumes: the `"Browser"` category (Task 5), `meta["upper_bound"]` (Task 4).
- Produces:
  - **Report:**
    - segment `("browsers", "Browsers")`, inserted after `("appcache", "App caches")`, and added to `MEASURED` and `ITEM_SIZED`;
    - `segment_of` maps category `"Browser"` to `"browsers"`;
    - `_row` adds `"upper_bound": bool(lead.meta.get("upper_bound"))` and shows `version` for category `"Browser"` too.
  - **Page:**
    - CSS var `--browsers` (pick a colour distinct from the existing segment colours), SEG colour map `browsers: "var(--browsers)"`, abbreviation `"Browser": "Br"`;
    - `sum()` skips rows with `upper_bound`;
    - the size cell shows `"up to " + human(size)` for them.
  - **`VERSION`** = `0.4.0`.

- [ ] **Step 1: Write the failing tests.**
  - `test_report.py`:
    - `test_upper_bound_not_in_totals`: a pnpm item of 4 GB leaves the headline `reclaimable` and `no_cost` and the segment `reclaimable` unchanged, but adds to the `caches` segment `bytes`.
    - `test_browser_segment`: a `"Browser"` item lands in segment `"browsers"`.
  - `test_ui_server.py` (node harness), `test_upper_bound_row_shows_up_to_and_isnt_summed`:
    - a payload with an `upper_bound` row shows "up to" in its size cell;
    - ticking it leaves the selection total at 0.
  - `test_devclean.py`, `test_cli_total_skips_upper_bound`: with `--dry-run`, the "Would run" total excludes pnpm's size and its line shows "up to".
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement** to the Interfaces above.
- [ ] **Step 4: Update `README.md`.**
  - **"What it can clean":**
    - `~/.cache` folders, pip, uv (`uv cache clean`) and pnpm (`pnpm store prune`). pnpm's size is shown as "up to".
    - Chromium browsers' AI models and page caches. Name the browsers. A browser's rows are kept while it runs. A locked folder shows a row and is never forced.
    - Sign-in caches go under "Check first".
  - **"Downloads (review)":** cloud-only files are never listed.
  - **"Protection rules":**
    - Inside `~/Library/Application Support`, only the exact folder names in Global Constraints may be deleted, in a known browser's data folder, never while it runs.
    - Hugging Face's login is kept.
    - The uv and pnpm commands are pinned like Docker's.
  - **"Tiers" safe list:** add pip, uv, pnpm and Puppeteer.
  - **"Never touched":** replace "removing app support files" with "app support files, except the browser folders listed above".
  - Mention MacSweep (MIT) as the source of the browser folder list.
- [ ] **Step 5: Run all tests.** `python3 -m unittest discover -s tests`. Expected: all pass.
- [ ] **Step 6: Check by eye on a fixture home.**
  - Set up a temp home with a Chrome fixture (one model, two profiles), `~/.cache/{puppeteer,mystery,huggingface/hub}`, `Library/pnpm/store`, and a `com.example.login` cache. `--fake-brew` isn't needed.
  - Start the page server against it the way the README's Web UI section describes.
  - In the browser tool, check: the Browsers segment, the "up to" on pnpm, the sign-in warning row, and the locked row (`chmod 000` a second browser's folder).
  - Never press Delete or Review & clean.
- [ ] **Step 7: Commit** with the message "0.4.0: report, page and CLI for the new caches; README".

---

## After the branch merges (the user, not the agent)

Before publishing, the user checks with a signed debug build (`scripts/build_app.sh --debug --sign`). The build scans the real home: scan only, never clean, with the user at the Mac. The check answers:
- whether macOS prompts for Chrome's, Brave's or Edge's folder;
- whether Allow makes them scan;
- whether Don't Allow shows the locked row and doesn't ask again.

If macOS shows no prompt and the only way in is Full Disk Access, change the locked note to also say "Size unavailable without Full Disk Access." (spec, "Locked folders").
