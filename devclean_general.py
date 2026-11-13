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

CACHE_COST = "The app rebuilds it as needed."
BROWSER_CACHE_COST = "Pages and images load a bit slower the first time. You stay logged in."
LOG_COST = "Old logs. Only useful when debugging a crash."
TRASH_COST = "Gone for good. You can't Put Back afterwards."
BACKUPS_NOTE = "Manage them in Finder → your iPhone → Manage Backups."
BACKUPS_UNREADABLE = "Size unavailable without Full Disk Access."
DOCKER_DOWN_NOTE = "Start Docker to see what can be cleaned."
DOCKER_BUILDER_COST = "The next image build starts from scratch."
DOCKER_IMAGES_COST = "Images are downloaded again the next time a container needs them."

TRASH_ARGV = ["osascript", "-e", 'tell application "Finder" to empty trash']
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

def parse_lsappinfo(text: str) -> list:
    """(bundle_id, name) for each app in `lsappinfo list` output.

    An app block starts with `N) "Name" ASN:...` and carries a `bundleID="..."` line.
    Raises ValueError when nothing parses: a Mac always has running apps, so an empty
    result means the output isn't what we expect.
    """
    apps, name = [], ""
    for line in text.splitlines():
        m = re.match(r'^\s*\d+\)\s+"([^"]*)"', line)
        if m:
            name = m.group(1)
        b = re.search(r'bundleID="([^"]+)"', line)
        if b:
            apps.append((b.group(1), name))
    if not apps:
        raise ValueError("no running apps found in lsappinfo output")
    return apps


def running_apps() -> list:
    """The real provider. Raises on any failure so the scan can fail closed and warn."""
    r = subprocess.run(["lsappinfo", "list"], capture_output=True, text=True, timeout=10)
    if r.returncode != 0:
        raise OSError(f"lsappinfo exit {r.returncode}")
    return parse_lsappinfo(r.stdout)


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


def scan_app_caches(cfg: core.Config, env: core.Env, warnings: Optional[list] = None) -> list:
    """One item per top-level folder of ~/Library/Caches. Fails closed (offers nothing, adds
    a warning) when the running apps can't be listed."""
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
        if (name.lower().startswith("com.apple.") or name.casefold() == OWN_CACHE.casefold()
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
        else:
            items.append(Item(label=name, tier=SAFE, kind="path", **base))
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
