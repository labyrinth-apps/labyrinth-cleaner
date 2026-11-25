# Package caches, browser AI models and caches, sign-in caches, cloud-only files

Status: draft for the user's review, 2026-11-25. Extends `2026-11-13-labyrinth-cleaner-app-design.md`. Ships as version 0.4.0, before the Windows version.

## Why

The user compared Labyrinth Cleaner with MacSweep (github.com/nihal4/macsweep, MIT) and picked five things to take from it. Each also brings the app closer to the later Windows version, where package caches, Chromium browsers and a general audience matter as much as Xcode does.

A read-only look at the user's Mac (2026-11-25) found:
- **`~/Library/pnpm/store`: 4.3 GB. `~/.cache/uv`: 1.7 GB.** Neither is scanned today.
- **`~/.cache` holds 4.3 GB in total,** including `puppeteer` (1.1 GB of downloaded test browsers), `codex-runtimes` (1.3 GB), `firebase` (240 MB) and the app's own `devclean` folder.
- **`~/Library/Caches/pip`: 123 MB.** It is offered today, but under "Check first" as a cache nobody can name.
- **macOS locks Chrome's, Brave's and Edge's data folders.** In `~/Library/Application Support`, the folders `Google/Chrome`, `BraveSoftware/Brave-Browser` and `Microsoft Edge` carry a `com.apple.macl` attribute. Listing them fails with "Operation not permitted", even outside Claude's sandbox. Arc's and Vivaldi's folders aren't locked. Arc's folder holds a 7.6 MB address-bar model and about 30 MB of graphics and script caches.
- **Chrome keeps some caches in the profile folder,** not in `~/Library/Caches`: `Code Cache`, `GPUCache`, the Dawn caches and the shared shader caches. Today's app-cache scan never sees them.

MacSweep reads the locked folders because it asks for Full Disk Access. Labyrinth Cleaner never does.

## Goals

1. **Package caches.**
   - **pip** becomes a named, safe developer cache: `~/Library/Caches/pip`.
   - **Every top-level folder of `~/.cache` is offered,** the way `~/Library/Caches` is today:
     - folders the app knows get a name and a cost;
     - unknown folders go under "Check first" with a warning;
     - the app's own `~/.cache/devclean` is never listed.
   - **uv's cache is cleaned with `uv cache clean`.** **pnpm's store is cleaned with `pnpm store prune`.** Each tool's own command respects its locks and its idea of what is in use.
2. **Browser AI models.** Each on-device model a Chromium browser downloaded gets its own row, with what it does. Chrome's Gemini Nano is one.
3. **Browser page caches.** One row per Chromium browser covers the caches it keeps inside its profiles, plus its shared graphics caches.
4. **Sign-in caches.** A cache folder whose name suggests passwords or sign-in moves to "Check first", with a warning that deleting it can sign you out.
5. **Cloud-only files.** A file in `~/Downloads` whose content lives only in iCloud, Dropbox or OneDrive is never listed in the Downloads review.

## Non-goals

- **Website data in browsers:** IndexedDB, Service Worker storage, File System, Local Storage. Web games keep their saves there.
- **Firefox and Safari.** Safari's data needs Full Disk Access.
- **Unknown AI model folders.** Only folders the app knows by name are listed.
- **A button that opens the browser's on-device AI setting.** The note says where it is instead.
- **Other package managers' caches:** Cargo, Go modules, Yarn, Bun, Conda. They're the next candidates, not part of 0.4.0.
- **Measuring exactly what `pnpm store prune` frees.** pnpm has no dry run.
- **Asking for Full Disk Access or admin rights.** A note may say a size is unavailable without Full Disk Access, as the iOS backups row already does; nothing ever asks for it.

## Anti-goals: if any of these happen, the feature has failed

- **Anything else in a browser's folder is deleted.** That covers passwords, cookies, history, bookmarks, Local Storage, IndexedDB, extensions, sessions and preferences. Only the exact folder names listed in this spec may be deleted there.
- **Anything in a browser's folder is deleted while that browser runs.**
- **The app asks for Full Disk Access or a password.**
- **A cloud-only file is moved to the Trash** by the Downloads review.
- **A project stops working after a clean:** a virtual environment, a `node_modules` folder, a pnpm or uv project.
- **A test runs the real `uv` or `pnpm`, or reads a real browser folder.** Development never runs a real `uv cache clean` or `pnpm store prune`.
- **The app's own cache folder is offered.**

## The user's decisions

1. **Take all five,** as one release, because they bring the app closer to the Windows version (2026-11-25).
2. **Locked browser folders: let macOS ask, and fall back to a "locked" row** (2026-11-25).
   - The app reads the browser folder as part of the scan. If macOS shows its own "would like to access data from other apps" prompt and the user allows it, the browser is scanned.
   - If access is refused, or macOS doesn't prompt, the browser shows one row saying its folder is locked by macOS. The app never asks for Full Disk Access.
3. **Accepted with the approach (proposed by me, not changed by the user):**
   - uv and pnpm are cleaned with their own commands.
   - `~/.cache` is offered like `~/Library/Caches`.
   - pip is a named cache.
   - Sign-in caches go under "Check first", not hidden.
   - Cloud-only Downloads files are left out completely.

## How it works

### 1. Package caches

**pip.** A new fixed developer target: `("pip-cache", "pip cache", "Library/Caches/pip", SAFE, "re-downloaded on demand")`. It joins the existing fixed list, so the app-cache scan skips it, as it skips the other fixed targets.

**`~/.cache`.** A new scan offers each top-level folder of `~/.cache`. Like the app-cache scan, it skips:
- symlinks;
- non-folders;
- `devclean`;
- any folder that a fixed target or a tool item below already covers.

| folder | label | tier | cost note |
|---|---|---|---|
| `uv` | uv cache | tool item, see below | |
| `puppeteer` | Puppeteer browsers | SAFE | Puppeteer downloads its browser again on the next run. |
| `huggingface` | Hugging Face models | CAUTION | Downloaded AI models. Scripts that use them download them again, which can take a long time. Only the download folders `hub`, `datasets` and `xet` are deleted; the `token` and `stored_tokens` files (your `huggingface-cli login`) are kept. |
| `torch` | PyTorch models | CAUTION | Same as Hugging Face. |
| `pip` | pip cache | SAFE | Re-downloaded on demand. (pip on Linux, or with `XDG_CACHE_HOME` set.) |
| anything else | the folder name | CAUTION | Warning: "A tool may use this cache." |

- Category: "Cache". The report shows these rows in the existing "Caches & builds" segment.
- The CLI's `--yes` covers only the SAFE developer rows (Puppeteer, pip), the same as the existing fixed targets. It never covers unknown folders or models.
- There's no running-app check: these folders don't name an app.

**Tool items: uv and pnpm.** One row each, shown when the folder exists.

| | uv | pnpm |
|---|---|---|
| folder | `~/.cache/uv` | `~/Library/pnpm/store` |
| command | `uv cache clean` | `pnpm store prune` |
| tier | SAFE | SAFE |
| size | the folder's size | "up to" the folder's size |
| cost | uv downloads packages again when it needs them. Existing environments keep working. | Removes packages no project uses. Projects keep working. |

- **The size is an upper bound.** On APFS, uv and pnpm copy files into environments as clones, which share disk blocks with the cache. Deleting the cache frees only the blocks nothing else shares. The row says "up to" for pnpm. For uv it adds: "May free less if your environments share files with it."
- **Like `brew cleanup`, both rows are preselected in the CLI and covered by `--yes`.** `brew cleanup` set the precedent for a developer tool's own command running unattended. The page never preselects them.
- **The pnpm row's size isn't counted in any total:** not the headline, "no cost", the disk-map hatch or the dialog's "free after". It stays after a prune and would otherwise inflate every scan. uv's size is counted: the whole cache goes.
- **The binary is found in a fixed list of places,** never through the user's login shell:
  - `/opt/homebrew/bin`
  - `/usr/local/bin`
  - `~/.local/bin`
  - `~/.cargo/bin`, for uv
  - `~/Library/pnpm`, for pnpm
  - `~/.volta/bin`
- **If the binary isn't found,** the row is shown but not offered, with the note "Couldn't find `uv`. Run `uv cache clean` in Terminal."
- **The command runs with a minimal environment** (`HOME`, `PATH=/usr/bin:/bin` plus the binary's own folder) and the home folder as its working folder. On the user's Mac `pnpm` is a Volta shim (`~/.volta/bin/pnpm`); a read-only `pnpm store path` through it worked from such an environment (2026-11-25). If the command fails, the row reports the error and the Terminal command.
- **The gate is exact,** like Docker's. The command must be exactly the binary found at scan time with exactly those arguments. Each command runs with a timeout. A timeout reports "uv is busy. Try again when it's done."
- **The rescan after a clean is folder-only.** The uv row disappears when the folder is gone or empty. The pnpm row stays and shows the new size.

### 2 and 3. Chromium browsers

**The table.** A data table lists each browser by display name, bundle ID and data folder (relative to `~/Library/Application Support`):
- Chrome: `com.google.Chrome`, `Google/Chrome`
- Chrome Beta: `com.google.Chrome.beta`, `Google/Chrome Beta`
- Chrome Canary: `com.google.Chrome.canary`, `Google/Chrome Canary`
- Brave: `com.brave.Browser`, `BraveSoftware/Brave-Browser`
- Edge: `com.microsoft.edgemac`, `Microsoft Edge`
- Arc: `company.thebrowser.Browser`, `Arc/User Data`
- Vivaldi: `com.vivaldi.Vivaldi`, `Vivaldi`
- Opera: `com.operasoftware.Opera`, `com.operasoftware.Opera`
- Opera GX: `com.operasoftware.OperaGX`, `com.operasoftware.OperaGX`
- Chromium: `org.chromium.Chromium`, `Chromium`

The table and the folder names below come from MacSweep (MIT). Credit it in a code comment.

**Locked folders.**
- **The scan lists the data folder.** For a browser whose folder exists but can't be listed (`PermissionError`, which covers both EPERM and EACCES), the scan adds one PROTECTED info row: "*Chrome*'s folder is locked by macOS. Its AI models and caches can't be measured."
- **The note says how to allow it, if there is a way short of Full Disk Access.** If macOS offers no pane for this permission, the note says only that the folder is locked, or "Size unavailable without Full Disk Access" as the iOS backups row does. Older macOS versions may not prompt at all; the locked row covers that.
- **The user checks the prompt on their own Mac before publishing.** A signed debug build scans the real home (scan only, no clean) with the user at the Mac. Tests and development never read the real browser folders.
- **The scan never retries in a loop or asks again.** macOS remembers the user's answer.

**AI models.** Each of these folders, directly in the data folder, gets its own row when it exists and holds more than 0 bytes:

| folder | label | tier | what it does |
|---|---|---|---|
| `OptGuideOnDeviceModel` | Gemini Nano | SAFE | Google's on-device language model: Help me write, page summaries, tab organising. |
| `OptGuideOnDeviceClassifierModel` | Scam detection model | CAUTION | Checks pages for scams. Warning: "Scam and phishing warnings may be weaker until the browser downloads it again." |
| `optimization_guide_model_store` | Small feature models | SAFE | Small models for features such as tab grouping and form filling. |
| `OnDeviceHeadSuggestModel` | Address bar suggestions | SAFE | Suggests searches as you type. |
| `screen_ai` | Screen AI | SAFE | Reads text in images and PDFs, for accessibility. |
| `SODA` | Live Caption | SAFE | Speech recognition for Live Caption. |
| `SODALanguagePacks` | Live Caption languages | SAFE | Language packs for Live Caption. |

- Each row's label is "*Browser*: *model*".
- Every model row's cost note adds: "*Browser* downloads it again unless you turn off on-device AI in its settings." For Chrome it names where the setting is. The builder confirms the current path.
- A version folder inside a model folder (such as `2026.2.12.1554`) may be shown as the version.

**Page caches.** One SAFE row per browser, "*Browser* page caches", with the cost note "Pages and scripts load a bit slower the first time. You stay logged in." It covers:
- **in each profile folder** (`Default`, `Profile N`, and Arc's and Opera's equivalents), but never `System Profile` or `Guest Profile`: `Cache`, `Code Cache`, `GPUCache`, `DawnGraphiteCache`, `DawnWebGPUCache`;
- **in the data folder itself:** `GrShaderCache`, `ShaderCache`, `GraphiteDawnCache`.

A profile is any subfolder that holds a `Preferences` file. Opera and Opera GX keep their only profile in the data folder itself; when the data folder holds `Preferences`, it counts as a profile too, so the gate also accepts `<data folder>/<profile-cache name>` for those two browsers. The names stay exactly the same.

**While the browser runs.** Every row of that browser is a PROTECTED info row, "Quit *Browser* first", using the running-app list the app-cache scan already uses. If the running apps can't be listed, no browser row is offered. That's the same fail-closed rule as app caches.

**Category and grouping.**
- New category "Browser" and a new report segment, "Browsers". The page shows these rows in the existing tier groups, as it does every other category.
- The existing app-cache rows for browsers (`~/Library/Caches/Google` and so on) are unchanged and aren't double-counted.
- No browser row is ever preselected, and the CLI's `--yes` never covers them.

**The gate, right before each delete.** A browser item is deleted with `rm` only if every target path:
- is exactly `<data folder>/<model or shared-cache name>`, or `<data folder>/<profile>/<profile-cache name>`, with names from the lists above and nothing else;
- isn't a symlink, and neither are any of its parents up to `~/Library/Application Support`;
- belongs to a browser that isn't running now.

The item is also re-scanned from fresh data, as every item is today.

This is a new, narrow exception to "Application Support is never touched". The README's protection rules say so, and list the exact folder names.

### 4. Sign-in caches

This applies to every app-cache row (`~/Library/Caches/*`) and every `~/.cache/*` row. Names are compared in lower case.

**Which names match:**
- **Substring match:** the name contains `password`, `1password`, `bitwarden`, `lastpass`, `dashlane`, `keychain` or `credential`.
- **Word match:** the name split on anything that isn't a letter or digit has a part equal to one of `auth`, `oauth`, `oauth2`, `token`, `tokens`, `account`, `accounts`, `login`, `logins`, `signin` or `sso`. That way `oauth-cache` and `com.example.login` match, and `author` doesn't.

**What a match does:**
- A matching row that would be SAFE becomes CAUTION, with the warning "It may hold sign-in data. Deleting it can sign you out."
- A matching row that would be CAUTION gets that warning instead of the generic one.
- A row kept because its app is running stays kept.

Tests pin both lists, including `author` and `authority`, which don't match.

### 5. Cloud-only files in Downloads

**During the scan, a file is skipped** before any rule is checked when either is true:
- `st_flags` has `SF_DATALESS` (0x40000000);
- `st_blocks == 0` and `st_size > 0`.

**The gate re-checks it:** a file that became cloud-only after the scan is refused with "Situation changed: scan again".

**The "~/Downloads" info row's size counts disk use,** as it does today, so cloud-only files don't inflate it.

### Page and CLI

- **New row notes and warnings** use the existing note, warning and cost fields. No new page controls.
- **The confirmation lists browser rows by browser,** like any other item.
- **The page shows "up to"** in front of the size for the pnpm row.
- **The CLI's `--dry-run` prints the uv and pnpm commands** it would run, as it does for Docker.

### Thinking ahead to Windows

Keep three things as plain data, separate from the scanning code:
- the browser table;
- the model and cache folder names;
- the `~/.cache` names.

Chromium uses the same folder layout inside `%LOCALAPPDATA%\<Vendor>\<Browser>\User Data` on Windows, so a Windows port swaps only the base folders.

## Constraints that already apply

- **Tests stay off the real system.** They never run the real `uv`, `pnpm`, `brew`, `xcrun`, `docker`, `osascript`, `lsappinfo`, `mdls`, `sysctl`, `xcode-select` or launchctl. They never read the real `/Applications` or a real browser folder.
  - Browser tests use fixture folders in a temp home.
  - The locked case is a fixture folder with permissions `000`.
  - uv and pnpm tests use a fake runner.
- **Development never runs a real clean** on this Mac. Read-only looks at folder names and sizes are allowed.
- **No Full Disk Access, no admin rights.**
- **No `innerHTML`** in `ui/index.html`.

## Approaches considered

On locked browser folders:
1. **B. Let macOS ask, fall back to a locked row (chosen).** No new permission by name. The user gets macOS's own prompt once, at most.
2. **A. Read only unlocked folders.** No prompt at all, but likely nothing for Chrome, Brave and Edge.
3. **Ask for Full Disk Access,** as MacSweep does. Ruled out by an earlier decision.

On package caches:
1. **The tool's own command for uv and pnpm (chosen).** It respects locks and what is in use, and needs the binary to be found.
2. **Delete the folders directly,** like the npm cache. It's simpler, but can race a running `uv` or `pnpm`. pnpm doesn't support removing its store by hand.

## Calls made after the builder check (mine, for the user to check)

1. **Hugging Face keeps your sign-in.** Only `hub`, `datasets` and `xet` are deleted, never the `token` files.
2. **uv and pnpm are in the CLI's `--yes`,** following `brew cleanup`. The page never preselects them.
3. **pnpm's size isn't counted in totals,** because it's an upper bound that stays after a prune. uv's is.
4. **Opera's data folder counts as its profile** when it holds `Preferences`.
5. **The locked note may say "without Full Disk Access",** as the backups row does, if that's the only way. The app still never asks for it.
6. **The user checks the macOS prompt before publishing,** with a signed debug build that only scans.
7. **uv and pnpm run with a minimal environment** from the home folder. That worked for the Volta pnpm on the user's Mac.

## Left to the builder

- The module layout. A new `devclean_browsers.py` and a `~/.cache` scan in `devclean_general.py` are the obvious places.
- The timeout for `uv cache clean` and `pnpm store prune`.
- The segment colour for "Browsers".
- Chrome's current on-device AI setting path.
- The README changes: what it can clean, the protection rules (the Application Support exception and its folder list), and the release notes for 0.4.0.
