#!/usr/bin/env python3
"""Merge an arm64 and an x86_64 python-build-standalone tree into one universal tree.

Usage: merge_universal.py ARM_DIR X86_DIR OUT_DIR   (stdlib only)

- Mach-O files present in both trees (by magic bytes) are combined with `lipo -create`.
- Other files are copied from ARM_DIR; ones that differ are listed on stderr.
- Files present in only one tree are copied and listed on stderr.
- Symlinks stay symlinks.
- Pruning (plan 3, Decision 1) is applied: nothing pruned reaches OUT_DIR.
"""
import os
import shutil
import subprocess
import sys

LIPO = "/usr/bin/lipo"

MACHO_MAGICS = {
    b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe",  # 32-bit
    b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe",  # 64-bit
    b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca",  # fat
    b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca",  # fat 64
}

PRUNE_DIR_NAMES = {"test", "idlelib", "tkinter", "turtledemo", "ensurepip", "lib2to3",
                   "pydoc_data", "config-3.12-darwin", "__pycache__"}
PRUNE_TOP_DIRS = {"include", "share"}
KEEP_BIN = {"python3", "python3.12"}
# Tcl/Tk exists only for tkinter, which is pruned (Decision 1 follow-on), as are dev-only
# pkgconfig files and the bundled pip (nothing installs packages into the app).
PRUNE_LIB_PREFIXES = ("tcl", "tk", "itcl", "thread", "libtcl", "libtk", "pkgconfig")


def is_pruned(rel):
    """rel is a posix-style path relative to the tree root (file or directory)."""
    parts = rel.split("/")
    if parts[0] in PRUNE_TOP_DIRS:
        return True
    if parts[0] == "bin" and len(parts) >= 2 and parts[1] not in KEEP_BIN:
        return True
    if parts[0] == "lib" and len(parts) >= 2 and parts[1].startswith(PRUNE_LIB_PREFIXES):
        return True
    if "site-packages" in parts:
        i = parts.index("site-packages")
        if len(parts) > i + 1 and parts[i + 1].startswith(("pip", "setuptools")):
            return True
    if any(p in PRUNE_DIR_NAMES for p in parts):
        return True
    return parts[-1].startswith("_tkinter") and parts[-1].endswith(".so")


def is_macho(path):
    try:
        with open(path, "rb") as f:
            return f.read(4) in MACHO_MAGICS
    except OSError:
        return False


def _real_lipo(argv):
    subprocess.run([LIPO] + argv, check=True)


def _entries(root):
    """Map rel path -> kind ('link'|'file') for every non-pruned entry; dirs are implicit."""
    found = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        reldir = os.path.relpath(dirpath, root)
        reldir = "" if reldir == "." else reldir.replace(os.sep, "/")
        keep = []
        for d in dirnames:
            rel = f"{reldir}/{d}" if reldir else d
            full = os.path.join(dirpath, d)
            if is_pruned(rel):
                continue
            if os.path.islink(full):
                found[rel] = "link"
            else:
                keep.append(d)
        dirnames[:] = keep
        for fn in filenames:
            rel = f"{reldir}/{fn}" if reldir else fn
            if is_pruned(rel):
                continue
            found[rel] = "link" if os.path.islink(os.path.join(dirpath, fn)) else "file"
    return found


def _same(a, b):
    if os.path.islink(a) or os.path.islink(b):
        return os.path.islink(a) and os.path.islink(b) and os.readlink(a) == os.readlink(b)
    with open(a, "rb") as fa, open(b, "rb") as fb:
        return fa.read() == fb.read()


def _copy(src, dst):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.islink(src):
        os.symlink(os.readlink(src), dst)
    else:
        shutil.copy2(src, dst)


def merge(arm_dir, x86_dir, out_dir, lipo=_real_lipo, log=None):
    arm_dir, x86_dir, out_dir = str(arm_dir), str(x86_dir), str(out_dir)
    log = log or (lambda m: print(m, file=sys.stderr))
    arm, x86 = _entries(arm_dir), _entries(x86_dir)
    os.makedirs(out_dir, exist_ok=True)
    for rel in sorted(set(arm) | set(x86)):
        a, x, dst = (os.path.join(d, rel) for d in (arm_dir, x86_dir, out_dir))
        if rel in arm and rel in x86:
            if arm[rel] == "file" and x86[rel] == "file" and is_macho(a) and is_macho(x):
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                lipo(["-create", a, x, "-output", dst])
                shutil.copymode(a, dst)
            else:
                _copy(a, dst)
                if not _same(a, x):
                    log(f"differs (arm64 copy used): {rel}")
        elif rel in arm:
            _copy(a, dst)
            log(f"only in arm64: {rel}")
        else:
            _copy(x, dst)
            log(f"only in x86_64: {rel}")


def main(argv):
    if len(argv) != 4:
        print(__doc__, file=sys.stderr)
        return 2
    merge(argv[1], argv[2], argv[3])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
