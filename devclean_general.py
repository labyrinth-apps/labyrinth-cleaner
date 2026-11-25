"""General-Mac categories for devclean: app caches, logs, Trash, iOS backups, Docker.

Imports devclean_core at load. The core never imports this module at load: it imports
it inside scan(), the rescanners and Env.real(), so there is no import cycle.
"""

import json
import os
import re
import stat
import subprocess
from typing import Optional

import devclean_core as core
from devclean_core import CAUTION, OPTIN, PROTECTED, SAFE, Item

OWN_CACHE = "com.labyrinth-apps.cleaner"
LOG_MIN_AGE_DAYS = 7
DOCKER_TIMEOUT = 3
RUNNING_APPS_WARNING = "couldn't list running apps; app caches are not offered"
UNKNOWN_CACHE_WARNING = "macOS or a background tool may use this cache."
SIGNIN_WARNING = "It may hold sign-in data. Deleting it can sign you out."

# Names that look like password or sign-in storage. Substrings match anywhere in the
# lower-cased name; words must match a whole piece of the name split on non-alphanumerics
# (so "author" and "loginitems" don't match).
SIGNIN_SUBSTRINGS = ("password", "1password", "bitwarden", "lastpass", "dashlane", "keychain",
                     "credential")
SIGNIN_WORDS = frozenset({"auth", "oauth", "oauth2", "token", "tokens", "account", "accounts",
                          "login", "logins", "signin", "sso"})

# Caches of macOS daemons and frameworks that aren't named com.apple.*: never shown.
# Compared casefolded.
SYSTEM_CACHE_NAMES = frozenset(n.casefold() for n in (
    "CloudKit", "PassKit", "GeoServices", "FamilyCircle", "familycircled", "askpermissiond",
    "SiriTTS", "softwareupdate", "TemporaryItems", "ssu", "mbuseragent", "AMSDataMigratorTool",
    "Animoji", "GameKit", "EnergyKit",
))

# A folder named like a bundle ID (com.spotify.client) names its app. Three or more
# dot-separated labels, the first a common top-level domain, so `main.kts.compiled.cache`
# is not one; two-label names (io.sentry) aren't either, and land in "Check first".
REVERSE_DNS_RE = re.compile(r"^[a-z0-9-]+(?:\.[a-z0-9_-]+){2,}$", re.IGNORECASE)
BUNDLE_TLDS = frozenset((
    "com", "org", "net", "io", "dev", "app", "co", "us", "uk", "de", "fr", "nl", "se", "ch",
    "at", "be", "ca", "au", "jp", "cn", "kr", "in", "it", "es", "pl", "cz", "ru", "br", "eu",
    "me", "tv", "ai", "sh", "so", "cc", "is", "ly", "gg", "to", "xyz", "tech", "company",
    "edu", "gov", "info", "biz",
))

CACHE_COST = "The app rebuilds it as needed."
BROWSER_CACHE_COST = "Pages and images load a bit slower the first time. You stay logged in."
LOG_COST = "Old logs. Only useful when debugging a crash."
TRASH_COST = "Gone for good. You can't Put Back afterwards."
BACKUPS_NOTE = "Manage them in Finder → your iPhone → Manage Backups."
BACKUPS_UNREADABLE = "Size unavailable without Full Disk Access."
DOCKER_DOWN_NOTE = "Start Docker to see what can be cleaned."
DOCKER_BUILDER_COST = "The next image build starts from scratch."
DOCKER_IMAGES_COST = "Images are downloaded again the next time a container needs them."

TRASH_ARGV = ["osascript", "-e", "with timeout of 3600 seconds",
              "-e", 'tell application "Finder" to empty trash', "-e", "end timeout"]
DOCKER_ARGV = {
    "docker:builder": ["docker", "builder", "prune", "-f"],
    "docker:images": ["docker", "image", "prune", "-a", "-f"],
}

# Cache folder names (lowercase) of the browsers: folder names and bundle IDs.
BROWSER_NAMES = {
    "google", "com.google.chrome", "arc", "company.thebrowser.browser", "firefox",
    "org.mozilla.firefox", "mozilla", "dia", "company.thebrowser.dia", "zen",
    "app.zen-browser.zen", "brave", "bravesoftware", "com.brave.browser", "microsoft edge",
    "com.microsoft.edgemac", "com.microsoft.edge", "opera", "com.operasoftware.opera",
    "vivaldi", "com.vivaldi.vivaldi",
}


# ---------------------------------------------------------------- running apps

def outer_app(exe: str) -> str:
    """The outermost .app bundle an executable path is inside, else ''."""
    i = exe.find(".app/")
    return exe[:i + 4] if i >= 0 else ""


def parse_lsappinfo(text: str) -> list:
    """(bundle_id, name) for each app in `lsappinfo list` output.

    An app block starts with `N) "Name" ASN:...` and carries a `bundleID="..."` line and an
    `executable path="..."` line. A helper running from inside another app's bundle
    (`Dia.app/Contents/.../Helper.app`) is named after that outer app: Dia's helpers call
    themselves "Arc", and must not make Arc look like it's running. The outer app's name is
    its own lsappinfo name when it runs, else the bundle's file name.
    Raises ValueError when nothing parses: a Mac always has running apps, so an empty
    result means the output isn't what we expect.
    """
    blocks = []                                   # [name, bundle_id or None, executable path]
    for line in text.splitlines():
        m = re.match(r'^\s*\d+\)\s+"([^"]*)"', line)
        if m:
            blocks.append([m.group(1), None, ""])
            continue
        if not blocks:
            continue
        b = re.search(r'bundleID="([^"]+)"', line)
        if b:
            blocks[-1][1] = b.group(1)
        e = re.search(r'executable path="([^"]+)"', line)
        if e:
            blocks[-1][2] = e.group(1)

    top_names = {}                                # outer .app path -> name, for top-level apps
    for name, _, exe in blocks:
        host = outer_app(exe)
        if host and exe.find(".app/", len(host)) < 0:
            top_names.setdefault(host, name)
    apps = []
    for name, bundle, exe in blocks:
        if not bundle:
            continue
        host = outer_app(exe)
        if host and exe.find(".app/", len(host)) >= 0:    # nested inside another app
            name = top_names.get(host) or os.path.basename(host)[:-4]
        apps.append((bundle, name))
    if not apps:
        raise ValueError("no running apps found in lsappinfo output")
    return apps


def running_apps() -> list:
    """The real provider. Raises on any failure so the scan can fail closed and warn."""
    r = subprocess.run(["lsappinfo", "list"], capture_output=True, text=True, timeout=10)
    if r.returncode != 0:
        raise OSError(f"lsappinfo exit {r.returncode}")
    return parse_lsappinfo(r.stdout)


def parse_running_bundle_paths(text: str) -> set:
    """The outermost .app path of every running process in `lsappinfo list` output.
    Helpers nested inside another app count for that outer app. Non-app executables
    are ignored. Raises ValueError when no `executable path` line is found at all."""
    exes = re.findall(r'executable path="([^"]+)"', text)
    if not exes:
        raise ValueError("no executable paths found in lsappinfo output")
    return {host for host in (outer_app(e) for e in exes) if host}


def running_bundle_paths() -> set:
    """The real provider. Raises on any failure so the scan can fail closed and warn."""
    r = subprocess.run(["lsappinfo", "list"], capture_output=True, text=True, timeout=10)
    if r.returncode != 0:
        raise OSError(f"lsappinfo exit {r.returncode}")
    return parse_running_bundle_paths(r.stdout)


# ---------------------------------------------------------------- app caches

# Cache folders named after a publisher, not a bundle ID: folder (lowercase) -> bundle ID.
PUBLISHER_ALIASES = {
    "bravesoftware": "com.brave.browser",
    "google": "com.google.chrome",
    "mozilla": "org.mozilla.firefox",
    "microsoft edge": "com.microsoft.edgemac",
}


def _owner(name: str, apps: list):
    """The running (bundle_id, app name) that owns cache folder `name`, or None.

    Most specific rule wins: exact bundle ID; dotted prefix either way (`...ShipIt`
    helpers); app name; publisher alias; second bundle component (`Google` for any
    com.google.*). Ties go to the bundle ID that sorts first."""
    n = name.lower()
    alias = PUBLISHER_ALIASES.get(n)
    rules = (
        lambda b, a: n == b,
        lambda b, a: b.startswith(n + ".") or n.startswith(b + "."),
        lambda b, a: n == a,
        lambda b, a: alias is not None and b == alias,
        lambda b, a: len(b.split(".")) > 2 and n == b.split(".")[1],
    )
    cands = sorted(apps, key=lambda x: x[0].lower())
    for rule in rules:
        for bundle, app in cands:
            if rule(bundle.lower(), app.lower()):
                return bundle, app
    return None


def is_system_cache(name: str) -> bool:
    """A cache folder macOS itself owns: com.apple.* or a known daemon name."""
    n = name.casefold()
    return n.startswith("com.apple.") or n in SYSTEM_CACHE_NAMES


def owner_identified(name: str) -> bool:
    """True when the folder name says which app it belongs to: a bundle ID, or a
    browser/publisher name we know."""
    n = name.lower()
    if n in BROWSER_NAMES or n in PUBLISHER_ALIASES:
        return True
    return bool(REVERSE_DNS_RE.match(n)) and n.split(".", 1)[0] in BUNDLE_TLDS


def signin_like(name: str) -> bool:
    """True when a cache folder's name looks like password or sign-in storage."""
    low = name.lower()
    if any(sub in low for sub in SIGNIN_SUBSTRINGS):
        return True
    return any(w in SIGNIN_WORDS for w in re.split(r"[^a-z0-9]+", low))


def scan_app_caches(cfg: core.Config, env: core.Env, warnings: Optional[list] = None) -> list:
    """One item per top-level folder of ~/Library/Caches, except macOS's own. Safe when the
    folder names its app, "Check first" with a warning when it doesn't. Fails closed (offers
    nothing, adds a warning) when the running apps can't be listed."""
    root = cfg.p("Library/Caches")
    if not os.path.isdir(root) or os.path.islink(root):
        return []
    try:
        apps = list(env.running_apps())
    except Exception:
        if warnings is not None:
            warnings.append(RUNNING_APPS_WARNING)
        return []
    fixed = {os.path.realpath(cfg.p(rel)) for _, _, rel, _, _ in cfg.fixed_targets}
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return []
    items = []
    for name in names:
        path = os.path.join(root, name)
        if (is_system_cache(name) or name.casefold() == OWN_CACHE.casefold()
                or os.path.realpath(path) in fixed or os.path.islink(path)
                or not os.path.isdir(path)):
            continue
        owner = _owner(name, apps)
        browser = name.lower() in BROWSER_NAMES or bool(owner and owner[0].lower() in BROWSER_NAMES)
        base = dict(key=f"appcache:{name}", category="App cache", path=path,
                    meta={"browser": browser})
        if owner:
            items.append(Item(label=owner[1], tier=PROTECTED, kind="info",
                              note=f"Quit {owner[1]} first", **base))
        elif signin_like(name):
            # Looks like sign-in storage: never no-cost, with its own warning.
            items.append(Item(label=name, tier=CAUTION, kind="path",
                              warning=SIGNIN_WARNING, **base))
        elif owner_identified(name):
            items.append(Item(label=name, tier=SAFE, kind="path", **base))
        else:
            # Nobody we can name owns it: offered, never as no-cost, with a warning.
            items.append(Item(label=name, tier=CAUTION, kind="path",
                              warning=UNKNOWN_CACHE_WARNING, **base))
    return items


# ---------------------------------------------------------------- ~/.cache

UNKNOWN_TOOL_CACHE_WARNING = "A tool may use this cache."
UNKNOWN_TOOL_CACHE_COST = ("NO COST", "The tool rebuilds it as needed.")
HF_DOWNLOAD_DIRS = ("hub", "datasets", "xet")
# uv belongs to its own item (a tool command), devclean is this app's own folder.
DOT_CACHE_SKIP = frozenset({"devclean", "uv"})   # compared casefolded
# name -> (label, tier, cost tag or None for "<size> DL", cost text)
DOT_CACHE_KNOWN = {
    "puppeteer": ("Puppeteer browsers", SAFE, "~1 MIN",
                  "Puppeteer downloads its browser again on the next run."),
    "huggingface": ("Hugging Face models", CAUTION, None,
                    "Downloaded AI models. Scripts that use them download them again, which can "
                    "take a long time. Your huggingface-cli login is kept."),
    "torch": ("PyTorch models", CAUTION, None,
              "Downloaded AI models. Scripts that use them download them again, which can "
              "take a long time."),
    "pip": ("pip cache", SAFE, "NO COST", "Re-downloaded on demand."),
}


def scan_dot_cache(cfg: core.Config, env: core.Env, warnings: Optional[list] = None) -> list:
    """One item per folder of ~/.cache. Known tools get a label and a cost; unknown folders are
    "Check first". Hugging Face is only ever its download folders (the login token stays).
    Nothing is offered through a symlinked ~/.cache or a symlinked child."""
    root = cfg.p(".cache")
    if not os.path.isdir(root) or os.path.islink(root):
        return []
    fixed = {os.path.realpath(cfg.p(rel)) for _, _, rel, _, _ in cfg.fixed_targets}
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return []
    items = []
    for name in names:
        path = os.path.join(root, name)
        low = name.casefold()
        if (low in DOT_CACHE_SKIP or os.path.islink(path) or not os.path.isdir(path)
                or os.path.realpath(path) in fixed):
            continue
        known = DOT_CACHE_KNOWN.get(low)
        label, tier, tag, cost = known or (name, CAUTION, *UNKNOWN_TOOL_CACHE_COST)
        warning = "" if known else UNKNOWN_TOOL_CACHE_WARNING
        if signin_like(name):
            tier, warning = CAUTION, SIGNIN_WARNING
        kw = dict(key=f"dotcache:{name}", category="Cache", label=label, tier=tier, path=path,
                  preselect=(tier == SAFE), warning=warning, meta={"dot_cost": (tag, cost)})
        if low == "huggingface":
            try:
                kids = sorted(os.listdir(path))
            except OSError:
                continue
            dirs = [os.path.join(path, d) for want in HF_DOWNLOAD_DIRS for d in kids
                    if d.casefold() == want and os.path.isdir(os.path.join(path, d))
                    and not os.path.islink(os.path.join(path, d))]
            if not dirs:
                continue
            items.append(Item(kind="paths", argv=["rm", "-rf", "--", *dirs], **kw))
        else:
            items.append(Item(kind="path", **kw))
    return items


# ---------------------------------------------------------------- logs

def scan_logs(cfg: core.Config, now: float) -> list:
    """Entries of ~/Library/Logs and its DiagnosticReports older than 7 days."""
    logs = cfg.p("Library/Logs")
    if not os.path.isdir(logs) or os.path.islink(logs):
        return []
    items = []
    for d in (logs, os.path.join(logs, "DiagnosticReports")):
        if not os.path.isdir(d) or os.path.islink(d):
            continue
        try:
            names = sorted(os.listdir(d))
        except OSError:
            continue
        for name in names:
            path = os.path.join(d, name)
            if d == logs and name == "DiagnosticReports":
                continue
            try:
                st = os.lstat(path)
            except OSError:
                continue
            if stat.S_ISLNK(st.st_mode) or (now - st.st_mtime) / 86400 <= LOG_MIN_AGE_DAYS:
                continue
            rel = os.path.relpath(path, logs)
            items.append(Item(key=f"log:{rel}", category="Logs", label=rel, tier=SAFE, path=path,
                              mtime=st.st_mtime, note="older than 7 days",
                              size=st.st_size if stat.S_ISREG(st.st_mode) else None))
    return items


# ---------------------------------------------------------------- Trash & backups

def _readable_size(path: str) -> Optional[int]:
    """Size of a folder, or None when it can't be read (du would quietly report 0)."""
    try:
        os.listdir(path)
    except OSError:
        return None
    return core.du_bytes(path)


def scan_trash(cfg: core.Config) -> list:
    trash = cfg.p(".Trash")
    if not os.path.isdir(trash) or os.path.islink(trash):
        return []
    size = _readable_size(trash)
    if size is not None and size <= 0:
        return []
    return [Item(key="empty-trash", category="Trash", label="Empty the Trash", tier=CAUTION,
                 kind="empty-trash", argv=list(TRASH_ARGV), size=size,
                 note="" if size is not None else "Trash: size unavailable")]


def scan_backups(cfg: core.Config) -> list:
    root = cfg.p("Library/Application Support/MobileSync/Backup")
    if not os.path.isdir(root) or os.path.islink(root):
        return []
    size = _readable_size(root)
    return [Item(key="ios-backups", category="iOS backups", label="iPhone and iPad backups",
                 tier=PROTECTED, kind="info", size=size,
                 note=BACKUPS_NOTE if size is not None else BACKUPS_UNREADABLE)]


# ---------------------------------------------------------------- Docker

DOCKER_UNITS = {"B": 1, "KB": 1000, "MB": 1000 ** 2, "GB": 1000 ** 3, "TB": 1000 ** 4}


def parse_docker_size(text) -> Optional[int]:
    """"1.2GB (35%)", "512MB", "0B" -> bytes, with decimal units. None if unparseable."""
    m = re.match(r"^\s*([\d.]+)\s*([A-Za-z]+)", text or "")
    if not m or m.group(2).upper() not in DOCKER_UNITS:
        return None
    try:
        return int(float(m.group(1)) * DOCKER_UNITS[m.group(2).upper()])
    except ValueError:
        return None


def _docker_rows(out: str) -> dict:
    """Type -> Reclaimable string, from JSON lines (or one JSON array)."""
    out = out.strip()
    rows = json.loads(out) if out.startswith("[") else [json.loads(l) for l in out.splitlines() if l.strip()]
    return {r["Type"]: r.get("Reclaimable", "") for r in rows}


def scan_docker(cfg: core.Config, env: core.Env) -> list:
    if env.docker is None:
        return []
    down = [Item(key="docker", category="Docker", label="Docker", tier=PROTECTED, kind="info",
                 note=DOCKER_DOWN_NOTE)]
    try:
        r = env.docker(["docker", "system", "df", "--format", "json"], capture_output=True,
                       text=True, timeout=DOCKER_TIMEOUT)
        if r.returncode != 0:
            return down
        rows = _docker_rows(r.stdout)
    except Exception:
        return down
    if "Build Cache" not in rows and "Images" not in rows:
        return down
    items = []
    if "Build Cache" in rows:
        items.append(Item(key="docker:builder", category="Docker", label="Docker build cache",
                          tier=OPTIN, kind="docker", argv=list(DOCKER_ARGV["docker:builder"]),
                          size=parse_docker_size(rows["Build Cache"])))
    if "Images" in rows:
        items.append(Item(key="docker:images", category="Docker", label="Unused Docker images",
                          tier=CAUTION, kind="docker", argv=list(DOCKER_ARGV["docker:images"]),
                          size=parse_docker_size(rows["Images"])))
    return items
