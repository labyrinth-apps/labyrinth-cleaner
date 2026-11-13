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
PROTECTED = "keep"     # shown for information only; never offered

OFFERABLE_TIERS = (SAFE, OPTIN, CAUTION)

FLUTTER_ARTIFACTS = ("build", ".dart_tool")
WEB_ARTIFACTS = ("node_modules", ".next")
# Directories never descended into while looking for projects.
PRUNE_DIRS = {"node_modules", ".next", "build", ".dart_tool", ".git", "Pods",
              ".gradle", ".symlinks", ".idea", ".venv", "venv", "DerivedData"}
MAX_PROJECT_DEPTH = 6

GRADLE_VERSION_RE = re.compile(r"^\d+(?:\.\d+)+(?:-[\w.]+)?$")
DEVICE_SUPPORT_RE = re.compile(
    r"^(?:(?P<model>[A-Za-z]+\d+,\d+) )?(?P<ver>\d+(?:\.\d+)*) \((?P<build>[^)]+)\)(?: (?P<arch>\S+))?$")

# The user's daily simulator. The newest *general* runtime supporting it is
# always kept (see the 2026-11-02 incident in README).
DEFAULT_PINNED_DEVICE_TYPES = ("com.apple.CoreSimulator.SimDeviceType.iPhone-18-Pro",)
DEFAULT_PROTECTED_AVDS = ("Pixel_10_Pro", "Television_4K")

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
    kind: str = "path"                   # path | runtime | simctl-unavailable | info
    meta: dict = field(default_factory=dict)
    cost_tag: str = ""                   # NO COST, ~2 MIN, 9.9 GB DL ...
    cost: str = ""                       # plain-language cost of deleting it
    project: Optional[str] = None        # project folder relative to its project root
    stale_days: Optional[int] = None     # days since the project's own files changed

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
                "cost": self.cost, "project": self.project, "stale_days": self.stale_days}


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

    @classmethod
    def real(cls) -> "Env":
        return cls(dev_tools=_dev_tools_installed)

    @classmethod
    def fake(cls, **overrides) -> "Env":
        return cls(**overrides)


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
            it.note = "Xcode is RUNNING - quit it first"
            it.preselect = False
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
                              tier=PROTECTED, path=path, note="newest Gradle version, kept",
                              kind="info"))
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
                                  tier=PROTECTED, path=path, kind="info",
                                  note=f"newest for {model}, kept"))
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
    """Return (protected image dirs, notes, unsafe flag).

    unsafe=True means we could not read a hard-coded protected AVD, so no
    system image may be offered at all.
    """
    protected, notes, unsafe = set(), [], False
    avd_root = cfg.avd_dir
    avd_dirs = {}
    if os.path.isdir(avd_root):
        for n in os.listdir(avd_root):
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
    for name in DEFAULT_PROTECTED_AVDS:
        if name in avd_dirs and not os.path.isfile(os.path.join(avd_dirs[name], "config.ini")):
            unsafe = True
            notes.append(f"protected AVD {name} has no readable config.ini")
    for name, d in avd_dirs.items():
        cfgfile = os.path.join(d, "config.ini")
        if not os.path.isfile(cfgfile):
            continue
        try:
            ini = _read_ini(cfgfile)
        except OSError:
            if name in DEFAULT_PROTECTED_AVDS:
                unsafe = True
            continue
        for k, v in ini.items():
            if k.startswith("image.sysdir."):
                v = v.rstrip("/").rstrip(os.sep)
                full = v if os.path.isabs(v) else os.path.join(cfg.android_sdk, v)
                protected.add(os.path.realpath(full))
    return protected, notes, unsafe


def scan_android(cfg: Config) -> list:
    root = cfg.system_images_dir
    if not os.path.isdir(root):
        return []
    protected, notes, unsafe = android_protection(cfg)
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
                if os.path.realpath(path) in protected:
                    items.append(Item(key=f"android:{label}", category="Android", label=label,
                                      tier=PROTECTED, path=path, kind="info",
                                      note="used by an AVD, kept"))
                elif unsafe:
                    items.append(Item(key=f"android:{label}", category="Android", label=label,
                                      tier=PROTECTED, path=path, kind="info",
                                      note="not offered: " + "; ".join(notes)))
                else:
                    items.append(Item(key=f"android:{label}", category="Android", label=label,
                                      tier=CAUTION, path=path, note="no AVD uses it"))
    return items


# ---------------------------------------------------------------- simulator runtimes

def runtime_protection(sim_list: dict, pinned_types=DEFAULT_PINNED_DEVICE_TYPES):
    """Decide which simulator runtimes must be kept.

    Returns (reasons, warnings): reasons maps runtime identifier -> list of
    (kind, device type name) with kind in pinned|booted|only|newest. A runtime absent from `reasons`
    may be offered (caution tier).

    Rules:
      1. For every device type that has an existing simulator, the newest
         runtime(s) supporting that type are kept (ties: all kept). This also
         covers "the only runtime supporting it".
      2. A runtime with a booted simulator is kept.
      3. For each pinned device type (iPhone 18 Pro), the newest *general*
         runtime (supports more than one device type) is kept, whether or not
         a simulator exists yet.
    """
    runtimes = {r["identifier"]: r for r in sim_list.get("runtimes", []) if r.get("identifier")}
    supports = {}  # device type id -> [runtime ids]
    type_names = {}
    for rid, r in runtimes.items():
        for t in r.get("supportedDeviceTypes", []) or []:
            tid = t.get("identifier")
            if tid:
                supports.setdefault(tid, []).append(rid)
                type_names[tid] = t.get("name", tid)
    reasons, warnings = {}, []

    def add(rid, kind, name):
        lst = reasons.setdefault(rid, [])
        if (kind, name) not in lst:
            lst.append((kind, name))

    def protect_newest(cands, kind, name):
        if not cands:
            return
        top = max(version_key(runtimes[c].get("version", "")) for c in cands)
        for c in cands:
            if version_key(runtimes[c].get("version", "")) == top:
                add(c, kind, name)

    for dev_rid, devs in (sim_list.get("devices") or {}).items():
        for dev in devs:
            tid = dev.get("deviceTypeIdentifier")
            if dev.get("state") == "Booted" and dev_rid in runtimes:
                add(dev_rid, "booted", dev.get("name"))
            if not tid:
                continue
            cands = supports.get(tid, [])
            name = type_names.get(tid, dev.get("name", tid))
            protect_newest(cands, "only" if len(cands) == 1 else "newest", name)

    for tid in pinned_types:
        cands = supports.get(tid, [])
        name = type_names.get(tid, tid.rsplit(".", 1)[-1])
        general = [c for c in cands if len(runtimes[c].get("supportedDeviceTypes") or []) > 1]
        if general or cands:
            protect_newest(general or cands, "pinned", name)
        else:
            warnings.append(f"no installed runtime supports pinned device type {name}")
    return reasons, warnings


def format_reasons(rs) -> str:
    by = {}
    for kind, name in rs:
        by.setdefault(kind, []).append(name)
    parts = []
    if by.get("pinned"):
        parts.append("pinned " + ", ".join(by["pinned"]))
    if by.get("booted"):
        parts.append("booted " + ", ".join(by["booted"]))
    if by.get("only"):
        parts.append("only runtime for " + ", ".join(by["only"]))
    newest = by.get("newest", [])
    if newest:
        parts.append(("newest for " + ", ".join(newest)) if len(newest) <= 2
                     else f"newest for {len(newest)} other sim types")
    return "; ".join(parts)


def scan_runtimes(cfg: Config, sim: SimctlProvider):
    """Return (items, warnings)."""
    if not sim.available:
        return [], []
    try:
        sim_list = sim.list_all()
        images = sim.runtime_images()
    except Exception as e:  # xcrun missing, timeout, bad JSON
        return [], [f"simulator scan skipped: {e}"]
    reasons, warnings = runtime_protection(sim_list, DEFAULT_PINNED_DEVICE_TYPES)
    runtimes = {r["identifier"]: r for r in sim_list.get("runtimes", []) if r.get("identifier")}
    devices = sim_list.get("devices") or {}
    items = []
    for uuid, img in sorted(images.items(), key=lambda kv: version_key(kv[1].get("version", ""))):
        rid = img.get("runtimeIdentifier")
        r = runtimes.get(rid)
        plat = (r or {}).get("platform") or (rid or "").rsplit(".", 1)[-1].split("-")[0]
        label = f"{plat} {img.get('version', '?')} ({img.get('build', '?')})"
        n_types = len((r or {}).get("supportedDeviceTypes") or [])
        last = img.get("lastUsedAt")
        mt = None
        if last:
            try:
                mt = datetime.fromisoformat(last.replace("Z", "+00:00")).timestamp()
            except ValueError:
                pass
        base = dict(key=f"runtime:{uuid}", category="Simulator", label=label,
                    size=img.get("sizeBytes"), mtime=mt,
                    meta={"uuid": uuid, "runtime": rid, "types": n_types})
        if r is None:
            items.append(Item(tier=PROTECTED, kind="info",
                              note="disk image not matched to a runtime, not offered", **base))
        elif rid in reasons:
            items.append(Item(tier=PROTECTED, kind="info", note=format_reasons(reasons[rid]), **base))
        elif not img.get("deletable", False):
            items.append(Item(tier=PROTECTED, kind="info", note="not deletable", **base))
        else:
            sims = [d.get("name") for d in devices.get(rid, [])]
            note = f"supports {n_types} device types; no sim depends on it"
            if sims:
                note = (f"{len(sims)} sim(s) on it become unavailable "
                        f"({', '.join(sims[:3])}{'...' if len(sims) > 3 else ''}); newer runtime covers them")
            items.append(Item(tier=CAUTION, kind="runtime",
                              argv=["xcrun", "simctl", "runtime", "delete", uuid],
                              note=note, **base))
    # Unavailable simulators
    unavailable = [d for devs in devices.values() for d in devs if d.get("isAvailable") is False]
    if unavailable:
        paths = [os.path.join(cfg.sim_devices_dir, d["udid"]) for d in unavailable if d.get("udid")]
        items.append(Item(key="simctl-unavailable", category="Simulator",
                          label=f"{len(unavailable)} unavailable simulator(s)", tier=CAUTION,
                          kind="simctl-unavailable", argv=["xcrun", "simctl", "delete", "unavailable"],
                          note="sims whose runtime is gone", meta={"paths": paths}))
    return items, warnings


# ---------------------------------------------------------------- info rows

def scan_info(cfg: Config) -> list:
    items = []
    for key, label, rel, note in [
        ("archives", "Xcode Archives", "Library/Developer/Xcode/Archives",
         "shipped builds, needed for crash logs: never deleted"),
        ("downloads", "~/Downloads", "Downloads", "personal files: size only, never offered"),
    ]:
        p = cfg.p(rel)
        if os.path.isdir(p):
            items.append(Item(key=key, category="Protected", label=label, tier=PROTECTED,
                              path=p, note=note, kind="info"))
    return items


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


def cost_for(it: Item, xcode_is_running: bool) -> tuple:
    """(tag, text): what deleting this item costs the user."""
    if not it.offerable:
        return "", it.note
    if it.key in FIXED_COSTS:
        return FIXED_COSTS[it.key]
    if it.key.startswith("gradle:"):
        return "~2 MIN", f"Re-downloaded only if a project still uses Gradle {it.key[7:]}."
    if it.key == "derived-data":
        text = "Xcode re-indexes and rebuilds."
        return "~5 MIN", ("Quit Xcode first. " + text) if xcode_is_running else text
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
        return f"{human(it.size)} DL", "No AVD uses it. Re-download from the SDK Manager if needed."
    if it.kind == "simctl-unavailable":
        return "NO COST", "These simulators already can't run."
    return "?", it.note


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


TIER_ORDER = {SAFE: 0, OPTIN: 1, CAUTION: 2, PROTECTED: 3}


def scan(cfg: Config, sim: Optional[SimctlProvider] = None,
         xcode_check: Callable[[], bool] = xcode_running,
         measure: bool = True, now: Optional[float] = None,
         env: Optional[Env] = None) -> ScanResult:
    if env is None:
        # Only the real home may query the real Mac; fixture homes never do.
        env = Env.real() if cfg.home == os.path.realpath(os.path.expanduser("~")) else Env.fake()
    if sim is None:
        sim = SimctlProvider() if env.dev_tools() else NoSimctl()
    xr = xcode_check()
    items, project_warnings = [], []
    items += scan_projects(cfg, project_warnings)
    items += scan_fixed(cfg, xr)
    items += scan_gradle_versions(cfg)
    items += scan_device_support(cfg)
    items += scan_android(cfg)
    rt_items, warnings = scan_runtimes(cfg, sim)
    warnings = project_warnings + warnings
    items += rt_items
    items += scan_info(cfg)

    if measure:
        def size_of(it: Item):
            if it.kind in ("path", "info") and it.path and it.size is None:
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
        it.cost_tag, it.cost = cost_for(it, xr)
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
    out = [os.path.realpath(p) for p in cfg.static_deny()]
    protected_images, _, _ = android_protection(cfg)
    out += sorted(protected_images)
    return out


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


def validate_item(it: Item, cfg: Config, sim: Optional[SimctlProvider] = None) -> None:
    """Re-check an item right before it is deleted. Raises UnsafeTarget."""
    if not it.offerable:
        raise UnsafeTarget(f"{it.label} is not offerable ({it.tier})")
    if it.kind == "path":
        validate_path(it.path, cfg)
        return
    if it.kind == "runtime":
        sim = sim or SimctlProvider()
        uuid = it.meta.get("uuid")
        fresh, _ = scan_runtimes(cfg, sim)
        ok = {i.meta.get("uuid") for i in fresh if i.kind == "runtime" and i.offerable}
        if uuid not in ok:
            raise UnsafeTarget(f"runtime {it.label} is protected now (re-checked)")
        if it.argv != ["xcrun", "simctl", "runtime", "delete", uuid]:
            raise UnsafeTarget("unexpected runtime command")
        return
    if it.kind == "simctl-unavailable":
        if it.argv != ["xcrun", "simctl", "delete", "unavailable"]:
            raise UnsafeTarget("unexpected simctl command")
        return
    raise UnsafeTarget(f"unknown item kind {it.kind}")


def execute_item(it: Item, cfg: Config, sim: Optional[SimctlProvider] = None,
                 runner: Callable = subprocess.run):
    """Delete one item after re-validating it. Returns (ok, message)."""
    try:
        validate_item(it, cfg, sim)
    except UnsafeTarget as e:
        return False, f"REFUSED: {e}"
    argv = ["rm", "-rf", "--", it.path] if it.kind == "path" else it.argv
    r = runner(argv, capture_output=True, text=True)
    if r.returncode != 0:
        return False, (r.stderr or "").strip() or f"exit {r.returncode}"
    return True, "ok"
