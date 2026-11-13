"""devclean core: scan, protection rules and the safety gate.

No I/O to the terminal here, so a future menu-bar app can import this module
and reuse `scan()`, `validate_item()` and `execute_item()` as-is.

Every path is derived from `Config.home`, so tests point it at a fixture tree.
Simulator data comes through a `SimctlProvider`, so tests feed JSON fixtures
instead of calling the real `xcrun`.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional

# ---------------------------------------------------------------- tiers

SAFE = "safe"          # regenerable; preselected; eligible for --yes
OPTIN = "opt-in"       # regenerable but costs a reinstall (node_modules, .next)
CAUTION = "caution"    # never preselected, never deleted by --yes
REVIEW = "review"      # moved to the Trash (never rm); never preselected
PROTECTED = "keep"     # shown for information only; never offered

OFFERABLE_TIERS = (SAFE, OPTIN, CAUTION, REVIEW)

FLUTTER_ARTIFACTS = ("build", ".dart_tool")
WEB_ARTIFACTS = ("node_modules", ".next")
# Directories never descended into while looking for projects.
PRUNE_DIRS = {"node_modules", ".next", "build", ".dart_tool", ".git", "Pods",
              ".gradle", ".symlinks", ".idea", ".venv", "venv", "DerivedData"}
MAX_PROJECT_DEPTH = 6

GRADLE_VERSION_RE = re.compile(r"^\d+(?:\.\d+)+(?:-[\w.]+)?$")
DEVICE_SUPPORT_RE = re.compile(
    r"^(?:(?P<model>[A-Za-z]+\d+,\d+) )?(?P<ver>\d+(?:\.\d+)*) \((?P<build>[^)]+)\)(?: (?P<arch>\S+))?$")

# Simulator UDIDs and runtime disk-image ids go into argv and a folder name.
SIM_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]*$")

# Warnings: an item that carries one is offered (never preselected) and the
# gate refuses it if a fresh scan shows a different warning.
NEWEST_IOS_WARNING = "This is your newest iOS runtime. New simulators need it."
GRADLE_NEWEST_WARNING = "The version your projects probably use. It downloads again on the next build."
DEVSUPPORT_NEWEST_WARNING = "Re-copied the next time you debug on that iPhone."
ARCHIVE_WARNING = "Shipped builds. You need them to read crash reports for those versions."
TRASH_RESTORE = "Moved to the Trash, so you can Put Back."
SITUATION_CHANGED = "Situation changed: scan again"

# Conventional places developers keep projects, under home. Only those that exist are scanned.
DEFAULT_PROJECT_DIRS = ("Developer", "Projects", "code", "src", "dev", "workspace", "git", "repos")


class UnsafeTarget(Exception):
    """Raised when something tries to delete a path the safety gate refuses."""


# ---------------------------------------------------------------- config

@dataclass
class Config:
    home: str
    project_roots: Optional[tuple] = None
    android_sdk: Optional[str] = None

    def __post_init__(self):
        self.home = os.path.realpath(os.path.expanduser(self.home))
        if self.project_roots is None:
            candidates = [self.p(d) for d in DEFAULT_PROJECT_DIRS]
            candidates += [f for f in load_settings(self.home).get("project_folders", [])
                           if isinstance(f, str)]
        else:
            candidates = list(self.project_roots)
        roots = []
        for c in candidates:
            r = os.path.realpath(os.path.expanduser(c))
            if os.path.isdir(r) and r not in roots:
                roots.append(r)
        self.project_roots = tuple(roots)
        self.android_sdk = os.path.realpath(self.android_sdk or self.p("Library/Android/sdk"))

    def p(self, rel: str) -> str:
        return os.path.join(self.home, rel)

    # Fixed single-folder targets: (key, label, relative path, tier, note)
    @property
    def fixed_targets(self):
        return [
            ("derived-data", "Xcode DerivedData", "Library/Developer/Xcode/DerivedData", SAFE,
             "Xcode rebuilds it"),
            ("shorebird-cache", "Shorebird cache", ".shorebird/bin/cache", SAFE,
             "re-downloaded on next shorebird run"),
            ("gradle-build-cache", "Gradle build cache", ".gradle/caches/build-cache-1", SAFE,
             "Gradle rebuilds it"),
            ("npm-cache", "npm cache", ".npm/_cacache", SAFE, "re-downloaded on demand"),
            ("cocoapods-cache", "CocoaPods cache", "Library/Caches/CocoaPods", SAFE,
             "re-downloaded on pod install"),
            ("homebrew-cache", "Homebrew cache", "Library/Caches/Homebrew", SAFE,
             "old bottles/downloads"),
            ("playwright-cache", "Playwright browsers", "Library/Caches/ms-playwright", SAFE,
             "`npx playwright install` restores"),
        ]

    @property
    def xcode_dir(self):
        return self.p("Library/Developer/Xcode")

    @property
    def device_support_dir(self):
        return self.p("Library/Developer/Xcode/iOS DeviceSupport")

    @property
    def gradle_caches(self):
        return self.p(".gradle/caches")

    @property
    def avd_dir(self):
        return self.p(".android/avd")

    @property
    def system_images_dir(self):
        return os.path.join(self.android_sdk, "system-images")

    @property
    def sim_devices_dir(self):
        return self.p("Library/Developer/CoreSimulator/Devices")

    def static_deny(self) -> list:
        """Paths that may never be deleted, nor any folder that contains them."""
        return [
            self.p("Library/Developer/Xcode/Archives"),
            self.p("Downloads"),
            self.p("Desktop"),
            self.p("Pictures"),
            self.p("Movies"),
            self.p("Music"),
            self.p(".ssh"),
            self.avd_dir,
        ]


# ---------------------------------------------------------------- settings

def settings_path(home: str) -> str:
    return os.path.join(home, "Library", "Application Support", "Labyrinth Cleaner", "settings.json")


def load_settings(home: str) -> dict:
    """Saved user settings; {} when the file is missing or broken."""
    try:
        with open(settings_path(home), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_settings(home: str, data: dict) -> None:
    """Write settings atomically (temp file + os.replace), mode 0600."""
    path = settings_path(home)
    d = os.path.dirname(path)
    os.makedirs(d, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".settings-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            os.fchmod(f.fileno(), 0o600)
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def add_project_folder(home: str, path: str) -> str:
    """Remember a folder to scan for projects. Returns the stored realpath or raises ValueError."""
    home = os.path.realpath(os.path.expanduser(home))
    if not isinstance(path, str) or not os.path.isabs(path):
        raise ValueError(f"not an absolute path: {path!r}")
    if os.path.islink(path.rstrip(os.sep) or os.sep):
        raise ValueError(f"is a symlink: {path}")
    if not os.path.isdir(path):
        raise ValueError(f"not an existing folder: {path}")
    real = os.path.realpath(path)
    if real == home or not is_within(real, home):
        raise ValueError("must be a folder inside your home folder")
    if is_within(real, os.path.join(home, "Library")):
        raise ValueError("~/Library is not a projects folder")
    downloads = os.path.join(home, "Downloads")
    for d in Config(home=home, project_roots=()).static_deny():
        d = os.path.realpath(d)
        if d == downloads:
            continue   # allowed when the user picks it; the gate still never rm's there
        if is_within(real, d) or is_within(d, real):
            raise ValueError(f"folder is or contains a protected path: {d}")
    data = load_settings(home)
    folders = [f for f in data.get("project_folders", []) if isinstance(f, str)]
    if real not in folders:
        folders.append(real)
    data["project_folders"] = folders
    save_settings(home, data)
    return real


def remove_project_folder(home: str, path: str) -> None:
    home = os.path.realpath(os.path.expanduser(home))
    data = load_settings(home)
    drop = {path, os.path.realpath(path)}
    data["project_folders"] = [f for f in data.get("project_folders", [])
                               if isinstance(f, str) and f not in drop]
    save_settings(home, data)


# ---------------------------------------------------------------- items

@dataclass
class Item:
    key: str
    category: str
    label: str
    tier: str
    note: str = ""
    path: Optional[str] = None           # for rm -rf items
    argv: Optional[list] = None          # for xcrun items
    size: Optional[int] = None           # bytes; None = unknown
    mtime: Optional[float] = None        # epoch seconds
    preselect: bool = False
    kind: str = "path"                   # path | runtime | simdevice | simctl-unavailable | trash | info
    meta: dict = field(default_factory=dict)
    cost_tag: str = ""                   # NO COST, ~2 MIN, 9.9 GB DL ...
    cost: str = ""                       # plain-language cost of deleting it
    project: Optional[str] = None        # project folder relative to its project root
    stale_days: Optional[int] = None     # days since the project's own files changed
    warning: str = ""                    # what breaks if you delete it ("" = nothing known)
    deletes: str = ""                    # what gets removed: the path with ~, or the command
    restore: str = ""                    # how to get it back

    @property
    def offerable(self) -> bool:
        return self.tier in OFFERABLE_TIERS

    @property
    def command(self) -> Optional[str]:
        if self.kind == "path" and self.path:
            return "rm -rf -- " + shlex.quote(self.path)
        if self.argv:
            return " ".join(shlex.quote(a) for a in self.argv)
        return None

    def to_dict(self) -> dict:
        return {"key": self.key, "category": self.category, "label": self.label,
                "tier": self.tier, "note": self.note, "path": self.path,
                "size": self.size, "mtime": self.mtime, "preselect": self.preselect,
                "kind": self.kind, "command": self.command, "cost_tag": self.cost_tag,
                "cost": self.cost, "project": self.project, "stale_days": self.stale_days,
                "warning": self.warning, "deletes": self.deletes, "restore": self.restore}


# ---------------------------------------------------------------- simctl provider

class SimctlProvider:
    """Real simulator data from xcrun."""

    available = True

    def _json(self, args):
        r = subprocess.run(["xcrun", "simctl", *args], capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            raise RuntimeError(f"xcrun simctl {' '.join(args)} failed: {r.stderr.strip()}")
        return json.loads(r.stdout)

    def list_all(self) -> dict:
        return self._json(["list", "-j"])

    def runtime_images(self) -> dict:
        return self._json(["runtime", "list", "-j"])


class FixtureSimctl(SimctlProvider):
    def __init__(self, list_all: dict, runtime_images: dict):
        self._list, self._images = list_all, runtime_images

    @classmethod
    def from_dir(cls, d: str):
        with open(os.path.join(d, "simctl_list.json")) as f:
            a = json.load(f)
        with open(os.path.join(d, "runtime_list.json")) as f:
            b = json.load(f)
        return cls(a, b)

    def list_all(self):
        return self._list

    def runtime_images(self):
        return self._images


class NoSimctl(SimctlProvider):
    available = False

    def list_all(self):
        return {"runtimes": [], "devices": {}}

    def runtime_images(self):
        return {}


# ---------------------------------------------------------------- helpers

def version_key(v: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", v or ""))


def du_bytes(path: str) -> Optional[int]:
    try:
        r = subprocess.run(["du", "-sk", path], capture_output=True, text=True, timeout=900)
        return int(r.stdout.split()[0]) * 1024 if r.stdout else None
    except Exception:
        return None


def mtime_of(path: str) -> Optional[float]:
    try:
        return os.stat(path).st_mtime
    except OSError:
        return None


def iso_timestamp(s) -> Optional[float]:
    """Epoch seconds from simctl's ISO-8601 dates ("2026-11-01T10:00:00Z"), else None."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return None


def tilde(path, home: str) -> str:
    """`path` with the home prefix shown as ~."""
    if not path:
        return ""
    if path == home:
        return "~"
    if path.startswith(home.rstrip(os.sep) + os.sep):
        return "~" + path[len(home.rstrip(os.sep)):]
    return path


def plural_names(names) -> tuple:
    """(", ".join(names), True when more than one)."""
    names = list(names)
    return ", ".join(names), len(names) > 1


def is_within(child: str, parent: str) -> bool:
    """True if child == parent or child is inside parent (both normalized)."""
    return child == parent or child.startswith(parent.rstrip(os.sep) + os.sep)


def xcode_running() -> bool:
    try:
        return subprocess.run(["pgrep", "-x", "Xcode"], capture_output=True).returncode == 0
    except Exception:
        return False


# ---------------------------------------------------------------- environment

def _dev_tools_installed() -> bool:
    try:
        return subprocess.run(["xcode-select", "-p"], capture_output=True,
                              timeout=10).returncode == 0
    except Exception:
        return False


@dataclass
class Env:
    """What the scan may ask the real Mac. Tests use `Env.fake()`."""
    dev_tools: Callable[[], bool] = lambda: False
    xcode_running: Callable[[], bool] = lambda: False

    @classmethod
    def real(cls) -> "Env":
        return cls(dev_tools=_dev_tools_installed, xcode_running=xcode_running)

    @classmethod
    def fake(cls, **overrides) -> "Env":
        return cls(**overrides)


def default_env(cfg: Config) -> Env:
    """Only the real home may query the real Mac; fixture homes never do."""
    return Env.real() if cfg.home == os.path.realpath(os.path.expanduser("~")) else Env.fake()


def resolve_providers(cfg: Config, sim: Optional["SimctlProvider"], env: Optional[Env]):
    """(sim, env) with defaults filled in. An explicitly passed sim is always used."""
    env = env if env is not None else default_env(cfg)
    if sim is None:
        sim = SimctlProvider() if env.dev_tools() else NoSimctl()
    return sim, env


# ---------------------------------------------------------------- projects

def find_projects(root: str, warnings: Optional[list] = None):
    """Yield (dir, has_pubspec, has_package_json) for every project dir under root.

    Folders that can't be read (macOS privacy protection, chmod) are reported
    in `warnings` instead of being skipped silently.
    """
    if not os.path.isdir(root):
        return

    def onerror(err: OSError):
        if warnings is not None and isinstance(err, PermissionError):
            warnings.append(f"couldn't read {err.filename}: permission denied; its build "
                            "folders are not listed (allow access in System Settings > "
                            "Privacy & Security > Files & Folders)")

    base_depth = root.rstrip(os.sep).count(os.sep)
    for dirpath, dirnames, filenames in os.walk(root, onerror=onerror):
        depth = dirpath.count(os.sep) - base_depth
        dirnames[:] = [d for d in dirnames
                       if d not in PRUNE_DIRS and not os.path.islink(os.path.join(dirpath, d))]
        if depth >= MAX_PROJECT_DEPTH:
            dirnames[:] = []
        pub, pkg = "pubspec.yaml" in filenames, "package.json" in filenames
        if pub or pkg:
            yield dirpath, pub, pkg


def scan_projects(cfg: Config, warnings: Optional[list] = None) -> list:
    items, seen = [], set()
    for root in cfg.project_roots:
        for d, pub, pkg in find_projects(root, warnings):
            real = os.path.realpath(d)
            if real in seen:
                continue
            seen.add(real)
            items += _project_items(cfg, root, d, pub, pkg)
    return items


def _project_items(cfg: Config, root: str, d: str, pub: bool, pkg: bool) -> list:
    items = []
    rel = os.path.relpath(d, root)
    key_rel = os.path.relpath(d, cfg.home)
    if pub:
        for name in FLUTTER_ARTIFACTS:
            t = os.path.join(d, name)
            if os.path.isdir(t) and not os.path.islink(t):
                items.append(Item(key=f"flutter:{key_rel}/{name}", category="Flutter",
                                  label=f"{rel}/{name}", tier=SAFE, path=t,
                                  note="flutter pub get / build regenerates",
                                  preselect=True, project=rel,
                                  meta={"project_dir": d}))
    if pkg:
        for name in WEB_ARTIFACTS:
            t = os.path.join(d, name)
            if os.path.isdir(t) and not os.path.islink(t):
                note = "needs `npm install` again" if name == "node_modules" else "rebuilt by next build/dev"
                items.append(Item(key=f"web:{key_rel}/{name}", category="Web",
                                  label=f"{rel}/{name}", tier=OPTIN, path=t, note=note,
                                  project=rel, meta={"project_dir": d}))
    return items


# ---------------------------------------------------------------- fixed caches

def scan_fixed(cfg: Config, xcode_is_running: bool) -> list:
    items = []
    for key, label, rel, tier, note in cfg.fixed_targets:
        path = cfg.p(rel)
        if not os.path.isdir(path) or os.path.islink(path):
            continue
        it = Item(key=key, category="Cache", label=label, tier=tier, path=path, note=note,
                  preselect=(tier == SAFE))
        if key == "derived-data" and xcode_is_running:
            # In use: never offered while Xcode runs (the gate re-checks this).
            it.tier, it.kind, it.preselect, it.note = PROTECTED, "info", False, "Quit Xcode first"
        items.append(it)
    return items


def scan_gradle_versions(cfg: Config) -> list:
    d = cfg.gradle_caches
    if not os.path.isdir(d):
        return []
    versions = [n for n in os.listdir(d)
                if GRADLE_VERSION_RE.match(n) and os.path.isdir(os.path.join(d, n))
                and not os.path.islink(os.path.join(d, n))]
    if not versions:
        return []
    newest = max(versions, key=version_key)
    items = []
    for v in sorted(versions, key=version_key):
        path = os.path.join(d, v)
        if v == newest:
            items.append(Item(key=f"gradle:{v}", category="Gradle", label=f"Gradle caches {v}",
                              tier=CAUTION, path=path, note="newest Gradle version",
                              warning=GRADLE_NEWEST_WARNING))
        else:
            items.append(Item(key=f"gradle:{v}", category="Gradle", label=f"Gradle caches {v}",
                              tier=SAFE, path=path, note=f"older than {newest}", preselect=True))
    return items


# ---------------------------------------------------------------- DeviceSupport

def _build_is_release(build: str) -> bool:
    # Beta/seed builds end in a lowercase letter (e.g. 24B5070a).
    return not re.search(r"[a-z]$", build or "")


def scan_device_support(cfg: Config) -> list:
    d = cfg.device_support_dir
    if not os.path.isdir(d):
        return []
    groups, items = {}, []
    for name in sorted(os.listdir(d)):
        path = os.path.join(d, name)
        if not os.path.isdir(path) or os.path.islink(path):
            continue
        m = DEVICE_SUPPORT_RE.match(name)
        if not m:
            items.append(Item(key=f"devsupport:{name}", category="DeviceSupport", label=name,
                              tier=PROTECTED, path=path, kind="info",
                              note="unrecognised name, not offered"))
            continue
        model = m.group("model") or "(no model)"
        groups.setdefault(model, []).append((name, path, m))
    for model, entries in groups.items():
        def k(e):
            _, path, m = e
            return (version_key(m.group("ver")), _build_is_release(m.group("build")),
                    mtime_of(path) or 0)
        entries.sort(key=k)
        newest = entries[-1]
        for name, path, m in entries:
            if name == newest[0]:
                items.append(Item(key=f"devsupport:{name}", category="DeviceSupport", label=name,
                                  tier=CAUTION, path=path, note=f"newest for {model}",
                                  warning=DEVSUPPORT_NEWEST_WARNING))
            else:
                items.append(Item(key=f"devsupport:{name}", category="DeviceSupport", label=name,
                                  tier=CAUTION, path=path,
                                  note=f"older than {newest[0]}; re-copied when device connects"))
    return items


# ---------------------------------------------------------------- Android

def _read_ini(path: str) -> dict:
    out = {}
    with open(path, errors="replace") as f:
        for line in f:
            if "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    return out


def android_protection(cfg: Config):
    """Return (used, unreadable, unlistable).

    used maps the realpath of each system image an emulator (AVD) uses to the
    sorted names of those AVDs. unreadable lists, sorted, the AVDs whose
    config.ini can't be read: any image might be theirs. unlistable is True
    when the AVD folder exists but can't be listed: then any image might be
    in use (fail closed, never "no AVD uses it").
    """
    avd_root = cfg.avd_dir
    avd_dirs = {}
    try:
        names = os.listdir(avd_root)
    except FileNotFoundError:   # no emulators at all
        names = []
    except OSError:             # exists but can't be listed (or ~/.android can't be searched)
        return {}, [], True
    for n in names:
        if n.endswith(".ini"):
            name = n[:-4]
            try:
                ini = _read_ini(os.path.join(avd_root, n))
            except OSError:
                ini = {}
            p = ini.get("path")
            if not p or not os.path.isdir(p):
                p = os.path.join(avd_root, name + ".avd")
            avd_dirs[name] = p
        elif n.endswith(".avd"):
            avd_dirs.setdefault(n[:-4], os.path.join(avd_root, n))
    used, unreadable = {}, []
    for name in sorted(avd_dirs):
        try:
            ini = _read_ini(os.path.join(avd_dirs[name], "config.ini"))
        except OSError:
            unreadable.append(name)
            continue
        for k, v in ini.items():
            if k.startswith("image.sysdir."):
                v = v.rstrip("/").rstrip(os.sep)
                full = v if os.path.isabs(v) else os.path.join(cfg.android_sdk, v)
                lst = used.setdefault(os.path.realpath(full), [])
                if name not in lst:
                    lst.append(name)
    return used, unreadable, False


AVDS_UNLISTABLE_WARNING = "Couldn't read your emulators' settings; an emulator may use this image."


def android_warning(avds, unreadable, unlistable=False) -> str:
    parts = [AVDS_UNLISTABLE_WARNING] if unlistable else []
    if avds:
        names, many = plural_names(avds)
        parts.append(f"The {names} emulator{'s stop' if many else ' stops'} working until you "
                     "download this image again (the SDK Manager does it).")
    if unreadable:
        names, many = plural_names(unreadable)
        parts.append(f"Couldn't read the settings of emulator{'s' if many else ''} {names}; "
                     f"{'they' if many else 'it'} may use this image.")
    return " ".join(parts)


def scan_android(cfg: Config) -> list:
    root = cfg.system_images_dir
    if not os.path.isdir(root):
        return []
    used, unreadable, unlistable = android_protection(cfg)
    items = []
    for api in sorted(os.listdir(root)):
        api_d = os.path.join(root, api)
        if not os.path.isdir(api_d):
            continue
        for tag in sorted(os.listdir(api_d)):
            tag_d = os.path.join(api_d, tag)
            if not os.path.isdir(tag_d):
                continue
            for abi in sorted(os.listdir(tag_d)):
                path = os.path.join(tag_d, abi)
                if not os.path.isdir(path) or os.path.islink(path):
                    continue
                label = f"{api}/{tag}/{abi}"
                avds = used.get(os.path.realpath(path), [])
                items.append(Item(key=f"android:{label}", category="Android", label=label,
                                  tier=CAUTION, path=path,
                                  note=("used by " + ", ".join(avds)) if avds else "no AVD uses it",
                                  warning=android_warning(avds, unreadable, unlistable)))
    return items


# ---------------------------------------------------------------- simulator runtimes

def _is_running(dev: dict) -> bool:
    """Anything but Shutdown (Booted, Booting, Shutting Down, ...) counts as in use."""
    return dev.get("state") != "Shutdown"


def _is_ios(r: dict) -> bool:
    plat = r.get("platform")
    return plat == "iOS" if plat else ".iOS-" in (r.get("identifier") or "")


def _runtime_name(rid: str, r: dict) -> str:
    """"iOS 27.2": platform and version, for warning text."""
    plat = r.get("platform") or rid.rsplit(".", 1)[-1].split("-")[0]
    return f"{plat} {r.get('version', '?')}"


def simulators_warning(no_other, elsewhere) -> str:
    """Text for the simulators a runtime deletion breaks.

    no_other: names whose device type no other installed runtime supports.
    elsewhere: {runtime name: names} for which Xcode can create a new one there.
    Names are sorted so the text is stable; the "no other runtime" group comes first.
    """
    parts = []
    groups = ([(sorted(no_other), None)] if no_other else []) + \
        [(sorted(elsewhere[t]), t) for t in sorted(elsewhere)]
    for names, target in groups:
        joined, many = plural_names(names)
        parts.append(f"Your {joined} simulator{'s stop' if many else ' stops'} working.")
        if target is None:
            parts.append(f"No other installed runtime runs {'them' if many else 'it'}.")
        else:
            parts.append(f"Xcode can create a new one on {target}.")
    return " ".join(parts)


def runtime_protection(sim_list: dict):
    """Decide which simulator runtimes are blocked and which carry a warning.

    Returns (blocks, warns, warnings):
      blocks   {runtime id: [reason]}: a simulator on it is running. Never offered.
      warns    {runtime id: text}: offered (never preselected) with this warning.
      warnings scan-level messages (none at present).

    A simulator lives on exactly one runtime, so deleting a runtime always
    breaks the available simulators on it. Its warning names them:
      - "... No other installed runtime runs it." when no other installed
        runtime supports the simulator's device type (the 2026-11-02 incident:
        27.1 only ran the iPhone Duo, so the iPhone 18 Pro on 27.0 had nowhere to go);
      - "... Xcode can create a new one on iOS 27.2." naming the newest other
        runtime that supports it.
    The newest general iOS runtime(s) (more than one device type; ties: all)
    also warn, whether or not a simulator exists yet.
    """
    runtimes = {r["identifier"]: r for r in sim_list.get("runtimes", []) if r.get("identifier")}
    supports = {}  # device type id -> [runtime ids]
    for rid, r in runtimes.items():
        for t in r.get("supportedDeviceTypes", []) or []:
            tid = t.get("identifier")
            if tid:
                supports.setdefault(tid, []).append(rid)

    def vkey(rid):
        return (version_key(runtimes[rid].get("version", "")), rid)

    blocks, no_other, elsewhere = {}, {}, {}
    for dev_rid, devs in (sim_list.get("devices") or {}).items():
        if dev_rid not in runtimes:
            continue
        for dev in devs:
            name = dev.get("name") or dev.get("udid") or "?"
            if _is_running(dev):
                blocks.setdefault(dev_rid, set()).add(f"{name} is running")
            if dev.get("isAvailable") is False:
                continue   # already can't run: deleting its runtime breaks nothing more
            others = [c for c in supports.get(dev.get("deviceTypeIdentifier") or "", [])
                      if c != dev_rid]
            if others:
                best = max(others, key=vkey)
                target = _runtime_name(best, runtimes[best])
                elsewhere.setdefault(dev_rid, {}).setdefault(target, set()).add(name)
            else:
                no_other.setdefault(dev_rid, set()).add(name)

    general_cands = [rid for rid, r in runtimes.items()
                     if _is_ios(r) and len(r.get("supportedDeviceTypes") or []) > 1]
    top = max((vkey(c)[0] for c in general_cands), default=None)
    general = {c for c in general_cands if vkey(c)[0] == top}
    warns = {}
    for rid in runtimes:
        parts = []
        if rid in no_other or rid in elsewhere:
            parts.append(simulators_warning(no_other.get(rid, ()), elsewhere.get(rid, {})))
        if rid in general:
            parts.append(NEWEST_IOS_WARNING)
        if parts:
            warns[rid] = " ".join(parts)
    return {rid: sorted(r) for rid, r in blocks.items()}, warns, []


SIM_WARNING_RE = re.compile(r"^Your (.+) simulators? stops? working$")


def warning_units(text: str) -> set:
    """Split a generated warning into the facts it states, for the gate.

    Sentences end with "." followed by a space (or the end). A "Your A, B
    simulators stop working." sentence and the sentence after it become one
    unit per simulator, ("sim", name, "none" | "on:<runtime>"), so a warning
    that lost a simulator is a subset of the one the user saw. Every other
    sentence is its own unit.
    """
    sentences = [x[:-1] if x.endswith(".") else x
                 for x in re.split(r"(?<=\.)\s+", (text or "").strip()) if x]
    units, i = set(), 0
    while i < len(sentences):
        m = SIM_WARNING_RE.match(sentences[i])
        nxt = sentences[i + 1] if i + 1 < len(sentences) else ""
        if m and nxt.startswith("No other installed runtime runs "):
            where = "none"
        elif m and nxt.startswith("Xcode can create a new one on "):
            where = "on:" + nxt[len("Xcode can create a new one on "):]
        else:
            units.add(sentences[i])
            i += 1
            continue
        for name in m.group(1).split(", "):
            units.add(("sim", name, where))
        i += 2
    return units


def scan_runtimes(cfg: Config, sim: SimctlProvider):
    """Return (items, warnings)."""
    if not sim.available:
        return [], []
    try:
        sim_list = sim.list_all()
        images = sim.runtime_images()
    except Exception as e:  # xcrun missing, timeout, bad JSON
        return [], [f"simulator scan skipped: {e}"]
    blocks, warns, warnings = runtime_protection(sim_list)
    runtimes = {r["identifier"]: r for r in sim_list.get("runtimes", []) if r.get("identifier")}
    devices = sim_list.get("devices") or {}
    items = []
    for uuid, img in sorted(images.items(), key=lambda kv: version_key(kv[1].get("version", ""))):
        rid = img.get("runtimeIdentifier")
        r = runtimes.get(rid)
        plat = (r or {}).get("platform") or (rid or "").rsplit(".", 1)[-1].split("-")[0]
        label = f"{plat} {img.get('version', '?')} ({img.get('build', '?')})"
        n_types = len((r or {}).get("supportedDeviceTypes") or [])
        base = dict(key=f"runtime:{uuid}", category="Simulator", label=label,
                    size=img.get("sizeBytes"), mtime=iso_timestamp(img.get("lastUsedAt")),
                    meta={"uuid": uuid, "runtime": rid, "types": n_types})
        if r is None:
            items.append(Item(tier=PROTECTED, kind="info",
                              note="disk image not matched to a runtime, not offered", **base))
        elif rid in blocks:
            items.append(Item(tier=PROTECTED, kind="info",
                              note="; ".join(blocks[rid]) + ": shut it down first", **base))
        elif not img.get("deletable", False) or not SIM_ID_RE.match(uuid):
            items.append(Item(tier=PROTECTED, kind="info", note="not deletable", **base))
        else:
            sims = sorted(d.get("name") or "?" for d in devices.get(rid, []))
            note = f"supports {n_types} device types; no sim depends on it"
            if sims:
                note = (f"{len(sims)} sim(s) on it become unavailable "
                        f"({', '.join(sims[:3])}{'...' if len(sims) > 3 else ''})")
            items.append(Item(tier=CAUTION, kind="runtime",
                              argv=["xcrun", "simctl", "runtime", "delete", uuid],
                              note=note, warning=warns.get(rid, ""), **base))
    # Unavailable simulators
    unavailable = [d for devs in devices.values() for d in devs if d.get("isAvailable") is False]
    if unavailable:
        paths = [os.path.join(cfg.sim_devices_dir, d["udid"]) for d in unavailable if d.get("udid")]
        items.append(Item(key="simctl-unavailable", category="Simulator",
                          label=f"{len(unavailable)} unavailable simulator(s)", tier=CAUTION,
                          kind="simctl-unavailable", argv=["xcrun", "simctl", "delete", "unavailable"],
                          note="sims whose runtime is gone", meta={"paths": paths}))
    return items, warnings


def scan_simulators(cfg: Config, sim: SimctlProvider) -> list:
    """One item per available simulator: `xcrun simctl delete <udid>`.

    Unavailable simulators are left to the `simctl-unavailable` item. A running
    simulator is listed but never offered.
    """
    if not sim.available:
        return []
    try:
        sim_list = sim.list_all()
    except Exception:  # scan_runtimes already reports it
        return []
    runtimes = {r["identifier"]: r for r in sim_list.get("runtimes", []) if r.get("identifier")}
    items = []
    for rid, devs in sorted((sim_list.get("devices") or {}).items()):
        version = (runtimes.get(rid) or {}).get("version") or rid.rsplit(".", 1)[-1]
        for dev in devs:
            udid = dev.get("udid") or ""
            if dev.get("isAvailable") is False or not SIM_ID_RE.match(udid):
                continue
            base = dict(key=f"simdevice:{udid}", category="Simulator",
                        label=f"{dev.get('name') or udid} ({version})",
                        path=os.path.join(cfg.sim_devices_dir, udid),
                        mtime=iso_timestamp(dev.get("lastBootedAt")),
                        meta={"udid": udid, "runtime": rid})
            if _is_running(dev):
                items.append(Item(tier=PROTECTED, kind="info", note="Running: shut it down first",
                                  **base))
            else:
                items.append(Item(tier=CAUTION, kind="simdevice",
                                  argv=["xcrun", "simctl", "delete", udid], **base))
    return items


# ---------------------------------------------------------------- Archives & info rows

def scan_archives(cfg: Config) -> list:
    """One REVIEW item per `Archives/<date>/<name>.xcarchive`. They move to the Trash, never rm."""
    root = cfg.p("Library/Developer/Xcode/Archives")
    items = []
    try:
        dates = sorted(os.listdir(root)) if os.path.isdir(root) and not os.path.islink(root) else []
    except OSError:
        return []
    for date in dates:
        dd = os.path.join(root, date)
        if not os.path.isdir(dd) or os.path.islink(dd):
            continue
        try:
            names = sorted(os.listdir(dd))
        except OSError:
            continue
        for name in names:
            path = os.path.join(dd, name)
            if not name.endswith(".xcarchive") or not os.path.isdir(path) or os.path.islink(path):
                continue
            items.append(Item(key=f"archive:{date}/{name}", category="Xcode Archives",
                              label=f"{name[:-len('.xcarchive')]} ({date})", tier=REVIEW,
                              kind="trash", path=path, note="shipped build",
                              warning=ARCHIVE_WARNING, restore=TRASH_RESTORE))
    return items


def scan_info(cfg: Config) -> list:
    p = cfg.p("Downloads")
    if not os.path.isdir(p):
        return []
    return [Item(key="downloads", category="Protected", label="~/Downloads", tier=PROTECTED,
                 path=p, note="personal files: size only, never offered", kind="info")]


# ---------------------------------------------------------------- cost & staleness

def human(n) -> str:
    """Binary units, matching `df -h`: 999 B, 2 KB, 145.0 MB, 9.9 GB."""
    if n is None:
        return "?"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1000 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024.0


STALE_AFTER_DAYS = 30
MB, GB = 1024 ** 2, 1024 ** 3

FIXED_COSTS = {
    "homebrew-cache": ("NO COST", "Old downloads of packages you already installed."),
    "npm-cache": ("NO COST", "The next install is a little slower."),
    "cocoapods-cache": ("~1 MIN", "Next `pod install` downloads pods again."),
    "playwright-cache": ("~1 MIN", "Run `npx playwright install` before the next e2e run."),
    "gradle-build-cache": ("~2 MIN", "The first Android build after this takes longer."),
    "shorebird-cache": ("~2 MIN", "Re-downloaded on the next shorebird command."),
}


def stale_label(days: Optional[int]) -> Optional[str]:
    if days is None or days <= STALE_AFTER_DAYS:
        return None
    return f"{days}d unused" if days < 60 else f"{days // 30} mo unused"


def cost_for(it: Item) -> tuple:
    """(tag, text): what deleting this item costs the user."""
    if not it.offerable:
        return "", it.note
    if it.key in FIXED_COSTS:
        return FIXED_COSTS[it.key]
    if it.key.startswith("gradle:"):
        return "~2 MIN", f"Re-downloaded only if a project still uses Gradle {it.key[7:]}."
    if it.key == "derived-data":
        return "~5 MIN", "Xcode re-indexes and rebuilds."
    if it.category == "Flutter":
        return "~2 MIN", "Next `flutter run` does a full build."
    if it.key.endswith("/node_modules"):
        size = it.size or 0
        tag = "~1 MIN" if size < 500 * MB else "~2 MIN" if size < 2 * GB else "~3 MIN"
        # The page shows `...` as code, so a backtick in a folder name must not leak in.
        name = (it.project or "").replace("`", "'")
        return tag, f"Run `npm install` before the next {name} work."
    if it.key.endswith("/.next"):
        return "~1 MIN", "Rebuilt by `next dev`/`next build`."
    if it.kind == "runtime":
        return f"{human(it.size)} DL", it.note
    if it.category == "DeviceSupport":
        return "~5 MIN", "Re-copied from the iPhone the next time you plug it in and debug."
    if it.category == "Android":
        text = "Re-download from the SDK Manager if needed."
        return f"{human(it.size)} DL", text if it.warning else "No AVD uses it. " + text
    if it.kind == "simctl-unavailable":
        return "NO COST", "These simulators already can't run."
    if it.kind == "simdevice":
        return "~1 MIN", "Deletes its installed apps and data. Xcode can create a fresh one."
    if it.kind == "trash":
        return "TRASH", "Moved to the Trash. You can Put Back."
    return "?", it.note


RESTORE_FIXED = {
    "derived-data": "Rebuilt automatically by the next Xcode build.",
    "gradle-build-cache": "Rebuilt automatically by the next Gradle build.",
}


def explain_for(it: Item, home: Optional[str] = None) -> tuple:
    """(deletes, restore): what gets removed and how to get it back. Empty for kept items.

    `home` shortens paths to ~ (defaults to the user's home).
    """
    if not it.offerable:
        return "", ""
    home = home or os.path.realpath(os.path.expanduser("~"))
    deletes = tilde(it.path, home) if it.kind in ("path", "trash") else (it.command or "")
    if it.kind == "trash":
        restore = TRASH_RESTORE
    elif it.kind == "runtime":
        restore = "Re-download the runtime in Xcode › Settings › Components."
    elif it.kind == "simdevice":
        restore = "Create a new simulator in Xcode › Window › Devices and Simulators."
    elif it.kind == "simctl-unavailable":
        restore = "Nothing to get back: they can't run any more."
    elif it.key in RESTORE_FIXED:
        restore = RESTORE_FIXED[it.key]
    elif it.category == "Cache":          # the fixed download caches
        restore = "Downloaded again on demand."
    elif it.category == "Flutter" or it.key.endswith("/.next"):
        restore = "Rebuilt automatically by the next build."
    elif it.key.endswith("/node_modules"):
        restore = "Run `npm install` to get it back."
    elif it.key.startswith("gradle:"):
        restore = "Downloaded again by the next build that needs it."
    elif it.category == "DeviceSupport":
        restore = "Re-copied from the iPhone the next time you debug on it."
    elif it.category == "Android":
        restore = "Download it again in Android Studio › SDK Manager."
    else:
        restore = ""
    return deletes, restore


def execution_order(items: list) -> list:
    """Order to delete in: `simctl delete unavailable` first.

    Deleting a runtime first would turn its simulators unavailable, and the
    second command would then remove more simulators than were listed.
    """
    return sorted(items, key=lambda i: 0 if i.kind == "simctl-unavailable" else 1)


def project_last_change(project_dir: str) -> Optional[float]:
    """Newest mtime among the project's own top-level entries (not build output, not .git)."""
    skip = set(FLUTTER_ARTIFACTS) | set(WEB_ARTIFACTS) | {".git"}
    newest = None
    try:
        names = os.listdir(project_dir)
    except OSError:
        return None
    for name in names:
        if name in skip:
            continue
        try:
            m = os.lstat(os.path.join(project_dir, name)).st_mtime
        except OSError:
            continue
        newest = m if newest is None else max(newest, m)
    return newest


def fixture_runner(argv: list, **kw) -> subprocess.CompletedProcess:
    """Runner for fixture mode: never touches the real simulators."""
    if argv and argv[0] == "xcrun":
        return subprocess.CompletedProcess(argv, 1, "", "fixture mode: xcrun disabled")
    return subprocess.run(argv, **kw)


# ---------------------------------------------------------------- scan

@dataclass
class ScanResult:
    items: list
    warnings: list
    free_bytes: int
    total_bytes: int
    xcode_running: bool

    @property
    def offerable(self):
        return [i for i in self.items if i.offerable]

    def reclaimable(self) -> int:
        return sum(i.size or 0 for i in self.offerable)


TIER_ORDER = {SAFE: 0, OPTIN: 1, CAUTION: 2, REVIEW: 3, PROTECTED: 4}


def scan(cfg: Config, sim: Optional[SimctlProvider] = None,
         xcode_check: Optional[Callable[[], bool]] = None,
         measure: bool = True, now: Optional[float] = None,
         env: Optional[Env] = None) -> ScanResult:
    """`xcode_check`, when given, wins over `env.xcode_running`."""
    sim, env = resolve_providers(cfg, sim, env)
    xr = (xcode_check or env.xcode_running)()
    items, project_warnings = [], []
    items += scan_projects(cfg, project_warnings)
    items += scan_fixed(cfg, xr)
    items += scan_gradle_versions(cfg)
    items += scan_device_support(cfg)
    items += scan_android(cfg)
    rt_items, warnings = scan_runtimes(cfg, sim)
    warnings = project_warnings + warnings
    items += rt_items
    items += scan_simulators(cfg, sim)
    items += scan_archives(cfg)
    items += scan_info(cfg)

    if measure:
        def size_of(it: Item):
            if (it.kind in ("path", "info", "simdevice", "trash") and it.path and it.size is None
                    and os.path.isdir(it.path)):
                it.size = du_bytes(it.path)
                if it.mtime is None:
                    it.mtime = mtime_of(it.path)
            elif it.kind == "simctl-unavailable":
                it.size = sum(du_bytes(p) or 0 for p in it.meta.get("paths", []) if os.path.isdir(p))
        with ThreadPoolExecutor(max_workers=8) as ex:
            list(ex.map(size_of, items))

    # Assign every offerable path item through the gate now, so the table
    # never shows something the executor would refuse.
    for it in items:
        if it.offerable and it.kind == "path":
            try:
                validate_path(it.path, cfg)
            except UnsafeTarget as e:
                it.tier, it.kind, it.preselect = PROTECTED, "info", False
                it.note = f"refused by safety gate: {e}"

    for it in items:
        it.cost_tag, it.cost = cost_for(it)
        it.deletes, it.restore = explain_for(it, cfg.home)
    now = time.time() if now is None else now
    changed = {}
    for it in items:
        d = it.meta.get("project_dir")
        if d:
            if d not in changed:
                changed[d] = project_last_change(d)
            if changed[d] is not None:
                it.stale_days = max(0, int((now - changed[d]) // 86400))

    items.sort(key=lambda i: (TIER_ORDER[i.tier], i.category, -(i.size or 0)))
    du = shutil.disk_usage(cfg.home)
    return ScanResult(items=items, warnings=warnings, free_bytes=du.free,
                      total_bytes=du.total, xcode_running=xr)


# ---------------------------------------------------------------- safety gate

def deny_paths(cfg: Config) -> list:
    """Never rm'd, nor any folder containing them. Android images used by an emulator
    are no longer here: they are offered with a warning that validate_item re-checks."""
    return [os.path.realpath(p) for p in cfg.static_deny()]


def validate_path(path: str, cfg: Config) -> str:
    """Raise UnsafeTarget unless `path` is a deletable build/cache folder.

    Checked again right before every rm, independent of how the item was found.
    """
    if not path or not os.path.isabs(path):
        raise UnsafeTarget(f"not an absolute path: {path!r}")
    norm = os.path.normpath(path)
    if not os.path.lexists(norm):
        raise UnsafeTarget(f"does not exist: {norm}")
    if os.path.islink(norm):
        raise UnsafeTarget(f"is a symlink: {norm}")
    real = os.path.realpath(norm)
    if real != norm:
        raise UnsafeTarget(f"path resolves elsewhere ({real}): {norm}")
    home = cfg.home
    if real in ("/", home) or not is_within(real, home):
        raise UnsafeTarget(f"outside home or is home itself: {real}")
    for d in deny_paths(cfg):
        if is_within(real, d):
            raise UnsafeTarget(f"inside protected path {d}")
        if is_within(d, real):
            raise UnsafeTarget(f"contains protected path {d}")

    parent, base = os.path.dirname(real), os.path.basename(real)

    # 1. Project build artifacts
    if parent not in cfg.project_roots and any(is_within(parent, r) for r in cfg.project_roots):
        if base in FLUTTER_ARTIFACTS and os.path.isfile(os.path.join(parent, "pubspec.yaml")):
            return real
        if base in WEB_ARTIFACTS and os.path.isfile(os.path.join(parent, "package.json")):
            return real
        raise UnsafeTarget(f"not a recognised build artifact folder: {real}")
    if is_within(real, cfg.p("Documents")):
        raise UnsafeTarget(f"personal files under ~/Documents: {real}")

    # 2. Fixed cache folders
    if real in {os.path.realpath(cfg.p(rel)) for _, _, rel, _, _ in cfg.fixed_targets}:
        return real
    # 3. Old Gradle version caches
    if parent == os.path.realpath(cfg.gradle_caches) and GRADLE_VERSION_RE.match(base):
        return real
    # 4. iOS DeviceSupport entries
    if parent == os.path.realpath(cfg.device_support_dir):
        return real
    # 5. Android system image (api/tag/abi) not used by any AVD
    sysimg = os.path.realpath(cfg.system_images_dir)
    if is_within(real, sysimg) and real != sysimg:
        if len(os.path.relpath(real, sysimg).split(os.sep)) == 3:
            return real
        raise UnsafeTarget(f"only whole system-image ABI folders may be deleted: {real}")
    raise UnsafeTarget(f"not on the allow-list: {real}")


# ---------------------------------------------------------------- re-scan one item

def _rescan_projects(key: str, cfg: Config, sim, env) -> list:
    """Re-check one project artifact without walking every project root."""
    rel = key.split(":", 1)[1]
    d = os.path.dirname(os.path.join(cfg.home, rel))
    if os.path.normpath(d) != d:
        return []
    for root in cfg.project_roots:
        if not is_within(d, root):
            continue
        parts = [] if d == root else os.path.relpath(d, root).split(os.sep)
        # Same limits as find_projects: no pruned or symlinked folders, bounded depth.
        if len(parts) > MAX_PROJECT_DEPTH or any(p in PRUNE_DIRS for p in parts):
            return []
        if any(os.path.islink(os.path.join(root, *parts[:i + 1])) for i in range(len(parts))):
            return []
        pub = os.path.isfile(os.path.join(d, "pubspec.yaml"))
        pkg = os.path.isfile(os.path.join(d, "package.json"))
        return _project_items(cfg, root, d, pub, pkg)
    return []


def _rescan_fixed(key: str, cfg: Config, sim, env) -> list:
    return scan_fixed(cfg, xcode_is_running=env.xcode_running())


# Key prefix -> scanner(key, cfg, sim, env) returning the items that scanner produces.
# Later tasks add their prefixes here. A key with no entry falls back to a full
# scan(measure=False): correct, but it walks every project root, so it is slow.
RESCANNERS = {
    "runtime:": lambda key, cfg, sim, env: scan_runtimes(cfg, sim)[0],
    "simctl-unavailable": lambda key, cfg, sim, env: scan_runtimes(cfg, sim)[0],
    "simdevice:": lambda key, cfg, sim, env: scan_simulators(cfg, sim),
    "gradle:": lambda key, cfg, sim, env: scan_gradle_versions(cfg),
    "devsupport:": lambda key, cfg, sim, env: scan_device_support(cfg),
    "android:": lambda key, cfg, sim, env: scan_android(cfg),
    "archive:": lambda key, cfg, sim, env: scan_archives(cfg),
    "flutter:": _rescan_projects,
    "web:": _rescan_projects,
    "derived-data": _rescan_fixed,
    "shorebird-cache": _rescan_fixed,
    "gradle-build-cache": _rescan_fixed,
    "npm-cache": _rescan_fixed,
    "cocoapods-cache": _rescan_fixed,
    "homebrew-cache": _rescan_fixed,
    "playwright-cache": _rescan_fixed,
}


def rescan_item(it: Item, cfg: Config, sim: Optional[SimctlProvider] = None,
                env: Optional[Env] = None) -> Optional[Item]:
    """Re-run the scanner that produced `it.key`; the fresh item with that key, or None."""
    sim, env = resolve_providers(cfg, sim, env)
    scanner = next((f for prefix, f in RESCANNERS.items() if it.key.startswith(prefix)), None)
    if scanner is not None:
        fresh = scanner(it.key, cfg, sim, env)
    else:
        fresh = scan(cfg, sim, measure=False, env=env).items
    return next((f for f in fresh if f.key == it.key), None)


# ---------------------------------------------------------------- gate

def validate_item(it: Item, cfg: Config, sim: Optional[SimctlProvider] = None,
                  env: Optional[Env] = None) -> None:
    """Re-check an item right before it is deleted. Raises UnsafeTarget.

    The item is scanned again from fresh data: it must still exist, still be
    offered, and carry no warning the user didn't see (an equal or shrunk
    warning passes, so a simulator deleted before its runtime is fine). Its kind, path and
    command must match the fresh item, so a forged item can't borrow another
    item's clean status.
    """
    if not it.offerable:
        raise UnsafeTarget(f"{it.label} is not offerable ({it.tier})")
    fresh = rescan_item(it, cfg, sim, env)
    if fresh is None:
        raise UnsafeTarget(f"{it.label} is gone")
    if not fresh.offerable:
        raise UnsafeTarget(f"{it.label}: {fresh.note or 'not offered now'}")
    if not warning_units(fresh.warning) <= warning_units(it.warning):
        raise UnsafeTarget(SITUATION_CHANGED)   # a warning the user never saw
    if (fresh.kind, fresh.path, fresh.argv) != (it.kind, it.path, it.argv):
        raise UnsafeTarget(SITUATION_CHANGED)
    if it.kind == "path":
        validate_path(it.path, cfg)
        return
    if it.kind == "runtime":
        uuid = it.key.split(":", 1)[1]
        if it.argv != ["xcrun", "simctl", "runtime", "delete", uuid]:
            raise UnsafeTarget("unexpected runtime command")
        return
    if it.kind == "simdevice":
        udid = it.key.split(":", 1)[1]
        if not SIM_ID_RE.match(udid) or it.argv != ["xcrun", "simctl", "delete", udid]:
            raise UnsafeTarget("unexpected simulator command")
        return
    if it.kind == "simctl-unavailable":
        if it.argv != ["xcrun", "simctl", "delete", "unavailable"]:
            raise UnsafeTarget("unexpected simctl command")
        return
    if it.kind == "trash":
        raise UnsafeTarget("moving to the Trash is not available yet")
    raise UnsafeTarget(f"unknown item kind {it.kind}")


def execute_item(it: Item, cfg: Config, sim: Optional[SimctlProvider] = None,
                 runner: Callable = subprocess.run, env: Optional[Env] = None):
    """Delete one item after re-validating it. Returns (ok, message)."""
    try:
        validate_item(it, cfg, sim, env)
    except UnsafeTarget as e:
        return False, f"REFUSED: {e}"
    argv = ["rm", "-rf", "--", it.path] if it.kind == "path" else it.argv
    r = runner(argv, capture_output=True, text=True)
    if r.returncode != 0:
        return False, (r.stderr or "").strip() or f"exit {r.returncode}"
    return True, "ok"
