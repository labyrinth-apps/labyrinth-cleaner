"""Application bundles: where they are and what the plist and Spotlight know about them."""

import os
import plistlib
import re
import subprocess
import unicodedata
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
DEVELOPER_DIR_NOTE = "Keep: used for command-line builds"
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
    # True when Spotlight answered for this path (so last_used None means a real (null)).
    spotlight: bool = False
    # A menu bar or background-only app (LSUIElement / LSBackgroundOnly): started at login
    # or by another app, so macOS may never record it as opened.
    background: bool = False


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

    def flag(key):
        v = plist.get(key)
        return v is True or (isinstance(v, int) and v == 1) or \
            (isinstance(v, str) and v.strip().lower() in ("1", "yes", "true"))

    return AppInfo(path=path, name=os.path.basename(path)[:-4], bundle_id=bundle_id,
                   version=text("CFBundleShortVersionString"), build=text("CFBundleVersion"),
                   uid=st.st_uid, ino=st.st_ino, mtime=st.st_mtime,
                   last_used=None, size=None, archs=None,
                   background=flag("LSUIElement") or flag("LSBackgroundOnly"))


def installed_names(cfg: "core.Config") -> dict:
    """{bundle ID casefolded: app name} for the bundles in the app folders, without Spotlight
    or sizes. The first bundle found for an ID wins."""
    names = {}
    for root in cfg.app_roots:
        for path in _bundle_candidates(root):
            info = _read_app(path)
            if info:
                names.setdefault(info.bundle_id.casefold(), info.name)
    return names


def find_apps(cfg: "core.Config", env: "core.Env", sizes: bool = True) -> list:
    """Every listed bundle with its Spotlight metadata. `sizes=False` leaves a size Spotlight
    doesn't know as None instead of running du (the gate's rescan never needs sizes)."""
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
    try:
        meta = env.app_metadata([a.path for a in found]) or {}
    except Exception:
        meta = {}
    for a in found:
        m = meta.get(a.path) or {}
        a.spotlight = isinstance(meta.get(a.path), dict)
        a.last_used = m.get("last_used")
        a.archs = m.get("archs")
        a.size = m.get("size")
        if a.size is None and sizes:
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
    """The real provider: one mdls call for all paths. {} on any failure. An app removed
    during the scan makes mdls fail for the whole batch, so a failed call is retried once
    with only the paths still there."""
    cmd = ["mdls"]
    for n in MDLS_NAMES:
        cmd += ["-name", n]

    def once(ps):
        try:
            r = subprocess.run(cmd + ps, capture_output=True, text=True, timeout=60)
            return parse_mdls(r.stdout, ps) if r.returncode == 0 else {}
        except Exception:
            return {}
    paths = list(paths)
    out = once(paths) if paths else {}
    if not out and paths:
        still = [p for p in paths if os.path.lexists(p)]
        if still and len(still) < len(paths):
            out = once(still)
    return out


# Pre-release markers, longest first so "alpha" isn't read as "a". Rank: dev < alpha < beta
# < pre/preview < rc, all below the release itself.
_PRE_RE = re.compile(r"(?<![a-z])(alpha|beta|preview|pre|rc|dev|a|b)(?![a-z])")
_PRE_RANK = {"dev": 0, "alpha": 1, "a": 1, "beta": 2, "b": 2, "pre": 3, "preview": 3, "rc": 4}


def app_version_key(v: Optional[str]) -> tuple:
    """Sort key for app versions: "1" == "1.0", and "1.0b2", "2.0-beta.1" or "3.1 RC 2" rank
    below the release they lead to. (core.version_key stays as it is for Gradle/runtimes.)"""
    text = (v or "").lower()
    if not re.search(r"\d", text):
        return ((), -1)          # no version at all ranks below every real one, "0.0b1" too
    m = _PRE_RE.search(text)
    release = text[:m.start()] if m else text

    def nums(t):
        n = [int(x) for x in re.findall(r"\d+", t)]
        while n and n[-1] == 0:
            n.pop()
        return tuple(n)
    if m is None:
        return (nums(release), 1)
    return (nums(release), 0, _PRE_RANK[m.group(1)], tuple(int(x) for x in
                                                         re.findall(r"\d+", text[m.end():])))


def build_key(b: Optional[str]) -> tuple:
    """Natural sort key for CFBundleVersion: "16A242d" < "16B100", "9" < "10". Build strings
    have no pre-release words, so app_version_key would read the "A" in "16A242d" as alpha."""
    parts = re.findall(r"\d+|[a-z]+", (b or "").lower())
    return tuple((0, int(x), "") if x.isdigit() else (1, 0, x) for x in parts)


def pick_keeper(group: list, running_paths: set, root: Optional[str] = None) -> AppInfo:
    """The copy to keep, by the first rule that separates them: running now (or otherwise
    pinned, see scan_apps), highest version, highest build, a name without a " (" suffix,
    directly in `root` (/Applications), newest last_used, then the first path in sort order
    (so a full tie picks the same copy on every scan)."""
    def rank(a: AppInfo):
        return (a.path in running_paths, app_version_key(a.version),
                build_key(a.build), " (" not in a.name,
                root is not None and os.path.dirname(a.path) == root, a.last_used or 0)
    return max(sorted(group, key=lambda a: a.path), key=rank)


def _nfd(p: str) -> str:
    return unicodedata.normalize("NFD", p)


def _month_year(ts: float) -> str:
    d = datetime.fromtimestamp(ts)
    return f"{MONTHS[d.month - 1]} {d.year}"


def _age_reason(a: AppInfo, now: float) -> Optional[str]:
    if a.last_used is not None:
        if (now - a.last_used) / 86400 > APP_UNUSED_DAYS:
            return f"last opened {_month_year(a.last_used)}"
        return None
    # No date: only a normal app's silence means it's unused (see AppInfo.background).
    if a.spotlight and not a.background and (now - a.mtime) / 86400 > APP_UNUSED_DAYS:
        return "no record of being opened"
    return None


def _developer_dir(env: "core.Env") -> Optional[str]:
    """The active developer directory (xcode-select -p), NFD-normalised; None if unknown."""
    try:
        d = env.active_developer_dir()
    except Exception:
        return None
    return _nfd(d.rstrip("/")) if isinstance(d, str) and d.strip("/") else None


def scan_apps(cfg: "core.Config", env: "core.Env", now: float,
              warnings: Optional[list] = None, sizes: bool = True) -> tuple:
    """(items, total bytes of every app found). Duplicates (same bundle ID) keep one copy and
    offer the rest; single apps are offered when not opened for APP_UNUSED_DAYS. Everything
    offered is REVIEW, never preselected. Fails closed when running apps can't be listed.
    The app holding the active developer directory is pinned like a running one and never
    offered. `sizes=False` skips du (see find_apps)."""
    found = find_apps(cfg, env, sizes)
    total = sum(a.size or 0 for a in found)
    if not found:
        return [], 0
    try:
        running_norm = {_nfd(p) for p in env.running_app_paths()}
        running_ids = {b for b, _ in env.running_apps()}
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
    # A copy is running when lsappinfo named its path (unicode-normalized). A bundle ID that is
    # running but matched none of its copies (say a translocated launch) makes every copy running.
    running = set()
    for bid, group in groups.items():
        hit = {a.path for a in group if _nfd(a.path) in running_norm}
        running |= hit or ({a.path for a in group} if bid in running_ids else set())
    # The bundle holding the active developer directory: xcodebuild and flutter build use it.
    dev = _developer_dir(env)
    devdir = {a.path for a in found
              if dev is not None and (dev + "/").startswith(_nfd(a.path) + "/")}

    def make(a: AppInfo, note: str, offered: bool) -> "core.Item":
        return core.Item(
            key="app:" + a.path, category="Applications", label=a.name,
            tier=core.REVIEW if offered else core.PROTECTED, note=note, path=a.path,
            size=a.size, mtime=a.last_used or a.mtime, preselect=False,
            kind=("trash" if a.uid == uid else "finder-trash") if offered else "info",
            meta={"bundle_id": a.bundle_id, "version": a.version, "uid": a.uid, "ino": a.ino})

    items = []
    for group in groups.values():
        keeper = pick_keeper(group, running | devdir, root)
        keeper_running = keeper.path in running
        keeper_dev = keeper.path in devdir
        if len(group) > 1:
            others = [app_version_key(a.version) for a in group if a is not keeper]
            if keeper_dev:
                note = DEVELOPER_DIR_NOTE
            elif keeper_running:
                note = "Keep: running now"
            elif all(app_version_key(keeper.version) > k for k in others):
                note = "Keep: newest version"
            else:
                note = "Keep: suggested copy"
            items.append(make(keeper, note, False))
        for a in group:
            if len(group) > 1 and a is keeper:
                continue
            parts = []
            if len(group) > 1:
                if keeper_dev:           # same order as the keeper's own note
                    parts.append(f"copy of {keeper.name} \u00b7 keep the one used for "
                                 "command-line builds")
                elif keeper_running:
                    parts.append(f"copy of {keeper.name} \u00b7 keep the running one")
                elif keeper.version and app_version_key(keeper.version) > app_version_key(a.version):
                    parts.append(f"older copy \u00b7 keep {keeper.name} {keeper.version}")
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
            if a.path in devdir:
                items.append(make(a, DEVELOPER_DIR_NOTE, False))
            elif a.path in running:
                items.append(make(a, f"Quit {a.name} first", False))
            else:
                items.append(make(a, " \u00b7 ".join(parts), True))
    return items, total
