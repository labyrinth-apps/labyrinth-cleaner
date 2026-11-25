"""uv and pnpm: their caches are cleaned by their own commands, never by rm."""

import os
import subprocess
from typing import Optional

import devclean_core as core

TOOLS = {
    "uv": {"folder": ".cache/uv", "args": ["cache", "clean"]},
    "pnpm": {"folder": "Library/pnpm/store", "args": ["store", "prune"]},
}
# Searched in this order; "~" is the home folder.
SEARCH_DIRS = {
    "uv": ["/opt/homebrew/bin", "/usr/local/bin", "~/.local/bin", "~/.cargo/bin", "~/.volta/bin"],
    "pnpm": ["/opt/homebrew/bin", "/usr/local/bin", "~/.local/bin", "~/Library/pnpm", "~/.volta/bin"],
}
RUN_TIMEOUT = 600
CATEGORY = "Cache"
LABELS = {"uv": "uv cache", "pnpm": "pnpm store"}
COSTS = {
    "uv": ("NO COST", "uv downloads packages again when it needs them. Existing environments keep "
                      "working. May free less if your environments share files with it."),
    "pnpm": ("NO COST", "Removes packages no project uses. Projects keep working."),
}


def _command(name: str) -> str:
    return f"{name} {' '.join(TOOLS[name]['args'])}"


def MISSING_NOTE(name: str) -> str:
    return f"Couldn't find `{name}`. Run `{_command(name)}` in Terminal."


def BUSY(name: str) -> str:
    return f"{name} is busy. Try again when it's done."


def find_tool(name: str, home: str) -> Optional[str]:
    """The first executable file named `name` in SEARCH_DIRS (a symlink, like Volta's shim, counts)."""
    for d in SEARCH_DIRS.get(name, []):
        if d == "~" or d.startswith("~/"):
            d = home + d[1:]
        path = os.path.join(d, name)
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


def _has_content(path: str) -> bool:
    try:
        with os.scandir(path) as it:
            return next(it, None) is not None
    except OSError:
        return False


def scan_tools(cfg, env) -> list:
    items = []
    for name, spec in TOOLS.items():
        folder = os.path.join(cfg.home, spec["folder"])
        # A symlinked folder is not the tool's own: it is never offered.
        if os.path.islink(folder) or not os.path.isdir(folder):
            continue
        if name == "uv" and not _has_content(folder):
            continue
        key, label = f"tool:{name}", LABELS[name]
        binary = env.find_tool(name)
        if binary is None:
            items.append(core.Item(key=key, category=CATEGORY, label=label, tier=core.PROTECTED,
                                   kind="info", path=folder, note=MISSING_NOTE(name)))
            continue
        meta = {"upper_bound": True} if name == "pnpm" else {}
        items.append(core.Item(key=key, category=CATEGORY, label=label, tier=core.SAFE,
                               kind="tool", path=folder, argv=[binary, *spec["args"]],
                               preselect=True, meta=meta))
    return items


def validate(it, fresh, env) -> None:
    """The last check before a tool runs. Raises core.UnsafeTarget."""
    name = it.key[len("tool:"):] if it.key.startswith("tool:") else ""
    if (name not in TOOLS or not it.argv or it.argv[1:] != TOOLS[name]["args"]
            or it.argv[0] != env.find_tool(name)):
        raise core.UnsafeTarget(core.SITUATION_CHANGED)


def run_item(it, env, home: str) -> tuple:
    """Run a validated tool item through env.tool_runner. Returns (ok, message)."""
    name = it.key[len("tool:"):]
    binary = it.argv[0]
    try:
        r = env.tool_runner(it.argv, capture_output=True, text=True, cwd=home, timeout=RUN_TIMEOUT,
                            env={"HOME": home, "PATH": f"{os.path.dirname(binary)}:/usr/bin:/bin"})
    except subprocess.TimeoutExpired:
        return False, BUSY(name)
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"{str(e) or name + ' failed'} Run `{_command(name)}` in Terminal."
    if r.returncode != 0:
        err = (r.stderr or "").strip() or f"exit {r.returncode}"
        return False, f"{err} Run `{_command(name)}` in Terminal."
    return True, "ok"
