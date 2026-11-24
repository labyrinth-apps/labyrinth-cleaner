# Homebrew cleanup: old versions, unneeded libraries, packages you installed

Status: draft, 2026-11-24. Extends `2026-11-13-labyrinth-cleaner-app-design.md` and follows the pattern of `2026-11-14-applications-cleanup-design.md`. Ships as version 0.3.0, before the Windows version.

## Why

The user has many Homebrew packages and isn't sure why some are there, whether they still use them, or why several look like duplicates with different versions. Homebrew has the answers spread over several commands (`brew leaves`, `brew uses`, `brew cleanup -n`, `brew autoremove -n`), which few people run. Labyrinth Cleaner already shows people where their space goes; it should show this too, in plain words, and let the user decide.

A read-only look at the user's Mac (2026-11-24) found:
- **170 formulae and 8 casks.** 21 formulae were installed on request and nothing depends on them.
- **`brew cleanup` would free about 93 MB:** an old `openssl@4` keg, two old copies of Homebrew's own portable Ruby, and logs.
- **4 libraries no installed package needs any more:** `libevent`, `libnghttp3`, `libngtcp2`, `unbound`.
- **Real duplicates the user installed:** `ffmpeg@6`, `@7` and `@8` (about 150 MB), next to plain `ffmpeg`. Also `openjdk@17` and `@21` (636 MB).
- **Pairs that only look like duplicates:** `icu4c@78/79` and `openssl@3/4`. Different packages need each version.
- **`python@3.11` shows as "needed by nothing",** yet this project's own DMG build uses it. Homebrew knows what other packages need, not what the user runs.
- **`postgresql@15` runs as a background service** (`~/Library/LaunchAgents/homebrew.mxcl.postgresql@15.plist`).
- **Cellar 2.8 GB, Caskroom 4.6 GB.** Almost all of the Caskroom is the Flutter SDK cask.

**Homebrew removes unneeded libraries by itself.** `brew cleanup` (`cleanup.rb:481`) and `brew uninstall` (`cmd/uninstall.rb:168-170`) both run an autoremove afterwards, unless `HOMEBREW_NO_AUTOREMOVE` is set. `brew uninstall --dry-run` lists what it would uninstall and autoremove, in about 1.2 s, for one or several packages.

## Goals

- **One safe item** in the existing "Safe to remove" group, category "Homebrew": **"Old Homebrew versions and unneeded libraries"**. It runs `brew cleanup`, which removes old versions and then the libraries nothing needs. Its note names those libraries.
- **A new "Homebrew packages" group** listing the formulae the user installed that no installed formula depends on. Each row shows:
  - the name and installed version;
  - its size, plus about how much the libraries only it uses add;
  - "installed Mar 2025", from Homebrew's install time;
  - a plain reason or note (see "Row notes").
- **Removing packages runs `brew uninstall --formula <name>`.** Homebrew then removes the libraries nothing needs any more, as it always does.
- **Before the user confirms, the app shows Homebrew's own list** of everything that will go, from `brew uninstall --dry-run`.
- **A "Homebrew" block on the disk map,** sized from the Cellar and the Caskroom.

## Non-goals

- Casks. Cask apps already appear under Applications. SDK casks such as Flutter are tools, not clutter.
- Taps, `brew update`, `brew upgrade`, telling the user something is outdated.
- Starting or stopping `brew services`.
- Package data outside the kegs: databases and config under `<prefix>/var` and `<prefix>/etc` are never touched.
- Other package managers (MacPorts, Nix, pip or npm globals).
- Knowing whether the user still runs a package. macOS doesn't keep reliable access dates for these programs, so there is no "last used" date.
- Changing Homebrew's own behaviour. The app doesn't set or clear `HOMEBREW_NO_AUTOREMOVE`, so the dry run and the real run follow the same rules.

## Anti-goals: if any of these happen, the feature has failed

- **A package another installed package needs is removed.** The app never passes `--ignore-dependencies`, `--force` or `--zap`, and only lists packages nothing depends on.
- **A package is removed that the user didn't tick.** Package rows are never preselected and never cleaned by the CLI's `--yes`. Libraries that go along with them are listed in the confirmation.
- **Running the app installs, upgrades or updates anything.** Every `brew` call has auto-update and install-cleanup turned off.
- **Labyrinth Cleaner uses `sudo` or asks for a password.**
- **A test, or development, runs the real `brew`.**
- **Anything under `<prefix>/var` or `<prefix>/etc` is deleted.**
- **Something that runs as a background service is removed,** whether a ticked package or a library going along with it.

## The user's decisions

1. **Ship this first,** as 0.3.0, then return to the Windows version (2026-11-24).
2. **Approach A: ask Homebrew during the scan** (2026-11-24), rather than reading Homebrew's files and re-doing its reasoning. Getting dependencies wrong breaks other packages; Homebrew's own answers can't disagree with what `brew` would do.
3. **Do what Homebrew does with unneeded libraries** (2026-11-24). Any library nothing needs may go in any Homebrew clean, as it does when the user runs `brew` themselves. The confirmation lists them, from Homebrew's dry run. The alternative, turning Homebrew's autoremove off and removing only what was ticked, was declined.
4. **Accepted with approach A** (proposed by me, not changed by the user):
   - Formulae only, no casks.
   - Libraries only a removed package used are removed with it.
   - A package that runs as a background service is not offered; its data is never touched.
   - Language runtimes get a note that projects or scripts may use them.
   - Tests and fixture mode never run the real `brew`.

## How it works

### Finding Homebrew

- **The `brew` binary:** `/opt/homebrew/bin/brew`, then `/usr/local/bin/brew`. The app runs from a GUI login, so `PATH` is not relied on.
- **It lives on the `Env`,** like Docker: `env.brew` is a runner plus the binary path, or `None`. `None` when Homebrew isn't installed, and always `None` in fixture mode and in tests. `fixture_runner` also refuses `brew` as its first argument.
- **Every call sets** `HOMEBREW_NO_AUTO_UPDATE=1`, `HOMEBREW_NO_INSTALL_CLEANUP=1`, `HOMEBREW_NO_ANALYTICS=1`, `HOMEBREW_NO_ENV_HINTS=1` and `HOMEBREW_NO_COLOR=1`.
- **A Homebrew owned by another account** (the prefix isn't writable by the current user): nothing is offered. One kept row says "Homebrew belongs to another account on this Mac".

### Scan

Three read-only calls, run alongside the rest of the scan (about 5 seconds together on the user's Mac):

1. `brew info --json=v2 --installed`: names, installed version, install time, `installed_on_request`, `runtime_dependencies`, `deprecated` and `disabled`.
2. `brew cleanup -n`: lines of the form `Would remove: <path> (<n> files, <size>)`, then its own "Would autoremove" list.
3. `brew autoremove -n`: the names after "Would autoremove … unneeded formulae:". This is the authoritative library list for the safe item.

Each call has a timeout (20 s). If any call fails or times out, Homebrew items are not offered, and the scan carries a warning: "couldn't read Homebrew; its packages are not offered". The rest of the scan is unaffected.

**Packages listed** ("Homebrew packages" group):
- installed on request, and
- not in the runtime dependencies of any other installed formula.

**Row size:** the package's own keg (`<Cellar>/<name>`), plus an estimate of the libraries only it uses. These are the installed-as-dependency formulae that are reachable from this package but not from any other installed-on-request formula, and are not already in the `autoremove -n` list. The estimate is shown as "plus about <size> of libraries only it uses". It is a display figure only. What actually goes is decided by Homebrew and shown in the confirmation.

**Kept rows** (shown in the group without a checkbox):
- **A background service:** a `homebrew.mxcl.<name>.plist` exists in `~/Library/LaunchAgents` or `/Library/LaunchDaemons`. Note: "Runs as a background service. Stop it with `brew services stop <name>` first."
- **Homebrew owned by another account** (above).

**The safe item is kept, not offered,** when one of the libraries in the `autoremove -n` list has a service plist. Its note then says: "<name> runs as a background service. Stop it with `brew services stop <name>` first."

### Row notes

In order, joined with " · ":
- **Versions of the same tool:** formulae with the same base name (the part before `@`, so `ffmpeg`, `ffmpeg@6`, `ffmpeg@7` and `ffmpeg@8` are one tool), installed on request. Rows sit next to each other. A row with a newer version of the same tool also installed says "older version · ffmpeg 8 also installed". The newest says "also installed: ffmpeg@6, ffmpeg@7". Nothing is suggested to keep, and none is ticked.
- **Language runtimes and servers:** "Projects or scripts you run may use it". Start with base names `python`, `node`, `openjdk`, `ruby`, `go`, `rust`, `php`, `perl`, `postgresql`, `mysql`, `mariadb`, `redis`, `mongodb-community`.
- **Deprecated or disabled formulae:** "Homebrew no longer maintains it".
- "plus about <size> of libraries only it uses".

### Items

| item | key | kind | tier | preselected (CLI) | `--yes` | in the headline |
|---|---|---|---|---|---|---|
| Old Homebrew versions and unneeded libraries | `brew:cleanup` | `brew` | safe | yes | yes | yes |
| one per package | `brew:pkg:<name>` | `brew` | caution | no | never | no |

- **Category:** "Homebrew". The safe item appears in "Safe to remove". Package rows appear in the new "Homebrew packages" group.
- **`argv`:**
  - `brew:cleanup` → `[<brew>, "cleanup"]`
  - `brew:pkg:<name>` → `[<brew>, "uninstall", "--formula", <name>]`
- **`<name>` must match** `^[a-z0-9][a-z0-9@+._-]*$`. No slashes, so tap-qualified names aren't accepted; formulae from taps are uninstalled by their short name.
- **The safe item's size:** the `cleanup -n` paths under the Homebrew prefix, plus the kegs of the `autoremove -n` libraries. It isn't shown when that is zero.
- **Overlap with existing rows:**
  - `brew cleanup` also deletes old downloads in `~/Library/Caches/Homebrew` and logs in `~/Library/Logs/Homebrew`. The safe item's size counts only paths under the Homebrew prefix, so the "Homebrew cache" and "Logs" rows aren't counted twice.
  - Running both is harmless: whichever runs second finds less to remove.
- **The headline ("You can get back") doesn't count package rows,** the same way it leaves out Applications. They are things the user installed, not clutter. They are still counted in the disk map's "can be cleaned" stripe.

### Confirmation (page and CLI)

- **When the user confirms a clean with package rows ticked,** the app first runs `brew uninstall --dry-run --formula <every ticked name>`. This is one call of about 1.2 s.
- **The confirmation lists what Homebrew reported:**
  - the packages to uninstall;
  - "Homebrew also removes these libraries nothing needs any more:" followed by the libraries;
  - "This can't be undone from the Trash. You can reinstall with `brew install <name>`."
- **If the dry run fails,** the clean isn't offered: "Couldn't ask Homebrew what this would remove. Scan again."
- **If a library in the dry run has a service plist,** the clean is blocked with the same "runs as a background service" wording, naming the library.
- **On the page** this is a new server request (for example `POST /api/brew-preview` with the scan id and keys), made before the confirmation opens. It uses the same token as other `/api/*` calls. It runs only the dry run and refuses anything other than ticked `brew:pkg:` keys from that scan.
- **In the CLI** the same list is printed before "Type 'yes'", and in `--dry-run` output.

### Safety gate (right before each `brew` command)

- **The `argv` is exactly** one of the two forms above, with the binary equal to `env.brew`'s path.
- **For `brew:pkg:<name>`,** a fresh `brew info --json=v2 --installed` shows the package:
  - still installed, at the same version;
  - still installed on request;
  - not a runtime dependency of any installed formula;
  - with no service plist.

  Otherwise: "Situation changed: scan again".
- **`env.brew` is `None`** (fixture mode, tests, no Homebrew): "REFUSED: Homebrew is not available".

### Running a clean

- **Order:** package uninstalls run first, one `brew uninstall` per package, so results are reported per row. Then `brew cleanup`, if ticked. Homebrew's own autoremove runs after each, as usual.
- **Timeout:** 300 s per command.
- **Messages:**
  - Homebrew busy (another `brew` holds its lock): "Homebrew is busy with another command. Try again when it's done."
  - `brew uninstall` refuses because something now depends on the package: Homebrew's own message, shown as is.
- **Restore text:** "Removed. Reinstall with `brew install <name>`."

### Page

- **A new group "Homebrew packages":** "Command-line tools and libraries you installed with Homebrew. Removing uninstalls them; you can install them again with brew." Placed after "Applications".
- **Each row:** name, version, size, "installed Mon YYYY", and the notes. Kept rows show "Keep" with their reason and no checkbox.
- **The safe item** sits in "Safe to remove", with the existing styling.
- **Icon mark:** "Hb".
- **The disk map** gets a "Homebrew" segment, sized from `<Cellar>` plus `<Caskroom>`, with a legend entry. The "System, apps & other files" estimate shrinks by the same amount. The Homebrew download cache stays in "Caches & builds".
- **"Select all" in "Homebrew packages"** ticks only offered rows.

### CLI (`devclean`)

- **Lists the Homebrew rows** like other items.
- **Prints commands** in `--dry-run`, with the full `brew` path and nothing after the command, followed by Homebrew's dry-run list when package rows are selected.
- **`--yes` runs only the safe item.**
- **In fixture mode (`--home`)** the CLI never runs `brew`; Homebrew items aren't listed.

## Constraints that already apply

- Tests never call the real `brew`, `xcrun`, `docker`, `osascript`, `lsappinfo`, `mdls`, `sysctl`, `xcode-select` or launchctl, and never read the real `/Applications`. Brew tests use a fake runner with canned `brew info`, `cleanup -n`, `autoremove -n` and `uninstall --dry-run` output, and fixture Cellar folders inside a temp dir.
- Development never runs a real `brew cleanup`, `autoremove` or `uninstall`. Read-only `brew` calls on the user's Mac are only for looking, never in tests. The user runs real cleanup themselves.
- No Full Disk Access, no admin rights.

## Approaches considered

1. **A. Ask Homebrew during the scan (chosen).** Authoritative and can't disagree with `brew`. It costs about 5 seconds, run alongside the rest of the scan, plus about 1.2 s at confirmation.
2. **B. Read Homebrew's install records** (`<Cellar>/<name>/<version>/INSTALL_RECEIPT.json`). Instant, but it re-does Homebrew's dependency reasoning, and a mistake lists something another package still needs.
3. **Moving kegs to the Trash** was ruled out: it breaks Homebrew's symlinks and records. Removal always goes through `brew`.

On unneeded libraries:

1. **Do what Homebrew does (chosen).** Libraries nothing needs go whenever `brew` cleans, and the confirmation lists them from Homebrew's dry run.
2. **Only what was ticked.** `HOMEBREW_NO_AUTOREMOVE=1` on every call, with a separate libraries item. Declined: more moving parts, and unused libraries left behind.

## Calls made after the builder check (mine, for the user to check)

1. **Package rows aren't in the headline total,** like Applications. Otherwise the main number would grow by gigabytes of things the user deliberately installed.
2. **Row sizes include an estimate** of the libraries only that package uses, labelled "about". Homebrew's exact list comes at confirmation, because a dry run per package during the scan would add about 25 seconds.
3. **The background-service rule covers libraries too.** A clean whose dry run would remove a library with a service plist is blocked, and the safe item is kept when its library list includes one.
4. **One `brew uninstall` per package,** not one for all, so each row gets its own result. Homebrew's autoremove after each one is harmless.

## Left to the builder

- The parsing of `brew cleanup -n` sizes (`34.6MB`, `64B`), and of the "Would autoremove" and "Would uninstall" lists.
- Whether the scan's three calls run in threads or one after another, as long as the scan isn't more than about 5 seconds slower.
- Which version is shown when a formula has several kegs installed (the linked one, else the newest).
- The segment colour.
- A fake-brew option for demos and the browser check, if useful. It must never be the default.
