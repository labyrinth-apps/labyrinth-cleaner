"""Application bundles: where they are and what the plist and Spotlight know about them."""

import os
import plistlib
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import devclean_core as core

# Never listed, whatever their folder: the system browser and this app itself.
NEVER_LISTED = {"com.apple.Safari", "com.labyrinth-apps.cleaner"}

# "Not opened in a long time" (days), and the note on Intel-only apps.
APP_UNUSED_DAYS = 180
INTEL_NOTE = "Intel only, stops working in macOS 28"
RUNNING_APPS_WARNING = "couldn't list running apps; applications are not offered"
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

MDLS_NAMES = ("kMDItemLastUsedDate", "kMDItemPhysicalSize", "kMDItemExecutableArchitectures")


@dataclass
class AppInfo:
    path: str
    name: str
    bundle_id: str
    version: Optional[str]
    build: Optional[str]
    uid: int
    ino: int
    mtime: float
    last_used: Optional[float]
    size: Optional[int]
    archs: Optional[list]


def _bundle_candidates(root: str):
    """.app paths directly in `root` and in its direct non-symlink subfolders."""
    try:
        entries = list(os.scandir(root))
    except OSError:
        return
    for e in entries:
        try:
            if e.is_symlink() or not e.is_dir(follow_symlinks=False):
                continue
        except OSError:
            continue
        if e.name.endswith(".app"):
            yield e.path
            continue
        try:
            subs = list(os.scandir(e.path))
        except OSError:
            continue
        for s in subs:
            try:
                if s.name.endswith(".app") and not s.is_symlink() \
                        and s.is_dir(follow_symlinks=False):
                    yield s.path
            except OSError:
                continue


def _read_app(path: str):
    """(AppInfo without metadata) or None for anything that isn't a plain, named bundle."""
    try:
        st = os.lstat(path)
        with open(os.path.join(path, "Contents", "Info.plist"), "rb") as f:
            plist = plistlib.load(f)
    except Exception:
        return None
    if not isinstance(plist, dict):
        return None
    bundle_id = plist.get("CFBundleIdentifier")
    if not isinstance(bundle_id, str) or not bundle_id or bundle_id in NEVER_LISTED:
        return None

    def text(key):
        v = plist.get(key)
        return v if isinstance(v, str) and v else None

    return AppInfo(path=path, name=os.path.basename(path)[:-4], bundle_id=bundle_id,
                   version=text("CFBundleShortVersionString"), build=text("CFBundleVersion"),
                   uid=st.st_uid, ino=st.st_ino, mtime=st.st_mtime,
                   last_used=None, size=None, archs=None)


def find_apps(cfg: "core.Config", env: "core.Env") -> list:
    seen, found = set(), []
    for root in cfg.app_roots:
        for path in _bundle_candidates(root):
            if path in seen:
                continue
            seen.add(path)
            info = _read_app(path)
            if info:
                found.append(info)
    if not found:
        return []
    meta = env.app_metadata([a.path for a in found]) or {}
    for a in found:
        m = meta.get(a.path) or {}
        a.last_used = m.get("last_used")
        a.archs = m.get("archs")
        a.size = m.get("size")
        if a.size is None:
            a.size = core.du_bytes(a.path)
    return found


def _parse_date(v: str) -> Optional[float]:
    try:
        return datetime.strptime(v, "%Y-%m-%d %H:%M:%S %z").timestamp()
    except ValueError:
        return None


def parse_mdls(text: str, paths: list) -> dict:
    """path -> {last_used, size, archs} from `mdls -name ...` output for `paths`, in order.
    Each file's block repeats the same keys; a repeated key starts the next file's block.
    `(null)` is unknown (None). Output that doesn't give exactly one block per path is
    ignored whole: it can't be matched to files safely."""
    blocks, cur, key, arch_list = [], None, None, None
    for line in text.splitlines():
        if arch_list is not None:
            s = line.strip()
            if s == ")":
                cur["archs"] = arch_list
                arch_list = None
            else:
                arch_list.append(s.strip('",'))
            continue
        m = re.match(r"^(kMDItem\w+)\s*=\s*(.*)$", line)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()
        if key not in MDLS_NAMES:
            continue
        if cur is None or key in cur["_seen"]:
            cur = {"_seen": set(), "last_used": None, "size": None, "archs": None}
            blocks.append(cur)
        cur["_seen"].add(key)
        if val == "(null)":
            continue
        if key == "kMDItemExecutableArchitectures":
            if val == "(":
                arch_list = []
        elif key == "kMDItemLastUsedDate":
            cur["last_used"] = _parse_date(val)
        elif key == "kMDItemPhysicalSize":
            cur["size"] = int(val) if val.isdigit() else None
    if arch_list is not None or not blocks or len(blocks) != len(paths):
        return {}
    return {p: {k: b[k] for k in ("last_used", "size", "archs")}
            for p, b in zip(paths, blocks)}


def mdls_metadata(paths: list) -> dict:
    """The real provider: one mdls call for all paths. {} on any failure."""
    if not paths:
        return {}
    cmd = ["mdls"]
    for n in MDLS_NAMES:
        cmd += ["-name", n]
    try:
        r = subprocess.run(cmd + list(paths), capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            return {}
        return parse_mdls(r.stdout, list(paths))
    except Exception:
        return {}


def pick_keeper(group: list, running_paths: set, root: Optional[str] = None) -> AppInfo:
    """The copy to keep, by the first rule that separates them: running now, highest version,
    highest build, a name without a " (" suffix, directly in `root` (/Applications), newest
    last_used."""
    def rank(a: AppInfo):
        return (a.path in running_paths, core.version_key(a.version),
                core.version_key(a.build), " (" not in a.name,
                root is not None and os.path.dirname(a.path) == root, a.last_used or 0)
    return max(group, key=rank)


def _month_year(ts: float) -> str:
    d = datetime.fromtimestamp(ts)
    return f"{MONTHS[d.month - 1]} {d.year}"


def _age_reason(a: AppInfo, now: float) -> Optional[str]:
    if a.last_used is not None:
        if (now - a.last_used) / 86400 > APP_UNUSED_DAYS:
            return f"last opened {_month_year(a.last_used)}"
        return None
    if (now - a.mtime) / 86400 > APP_UNUSED_DAYS:
        return "no record of being opened"
    return None


def scan_apps(cfg: "core.Config", env: "core.Env", now: float,
              warnings: Optional[list] = None) -> tuple:
    """(items, total bytes of every app found). Duplicates (same bundle ID) keep one copy and
    offer the rest; single apps are offered when not opened for APP_UNUSED_DAYS. Everything
    offered is REVIEW, never preselected. Fails closed when running apps can't be listed."""
    found = find_apps(cfg, env)
    total = sum(a.size or 0 for a in found)
    if not found:
        return [], 0
    try:
        running = set(env.running_app_paths())
    except Exception:
        if warnings is not None:
            warnings.append(RUNNING_APPS_WARNING)
        return [], total
    silicon = env.apple_silicon()
    uid = env.user_uid()
    groups = {}
    for a in found:
        groups.setdefault(a.bundle_id, []).append(a)
    root = cfg.app_roots[0] if cfg.app_roots else None

    def make(a: AppInfo, note: str, offered: bool) -> "core.Item":
        return core.Item(
            key="app:" + a.path, category="Applications", label=a.name,
            tier=core.REVIEW if offered else core.PROTECTED, note=note, path=a.path,
            size=a.size, mtime=a.last_used or a.mtime, preselect=False,
            kind=("trash" if a.uid == uid else "finder-trash") if offered else "info",
            meta={"bundle_id": a.bundle_id, "version": a.version, "uid": a.uid, "ino": a.ino})

    items = []
    for group in groups.values():
        keeper = pick_keeper(group, running, root)
        keeper_running = keeper.path in running
        if len(group) > 1:
            items.append(make(keeper, "Keep: running now" if keeper_running
                              else "Keep: newest version", False))
        for a in group:
            if len(group) > 1 and a is keeper:
                continue
            parts = []
            if len(group) > 1:
                if core.version_key(keeper.version) > core.version_key(a.version):
                    parts.append(f"older copy \u00b7 keep {keeper.name} {keeper.version}")
                elif keeper_running:
                    parts.append(f"copy of {keeper.name} \u00b7 keep the running one")
                else:
                    parts.append(f"second copy of {keeper.name} \u00b7 keep "
                                 + core.tilde(keeper.path, cfg.home))
            age = _age_reason(a, now)
            if age:
                parts.append(age)
            if not parts:
                continue
            if silicon and a.archs and "x86_64" in a.archs and "arm64" not in a.archs:
                parts.append(INTEL_NOTE)
            if a.path in running:
                items.append(make(a, f"Quit {a.name} first", False))
            else:
                items.append(make(a, " \u00b7 ".join(parts), True))
    return items, total
