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
