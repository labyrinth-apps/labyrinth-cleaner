"""Homebrew: the runner every brew call goes through, and parsers for its output."""

import json
import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Optional

import devclean_core as core

BREW_ENV = {
    "HOMEBREW_NO_AUTO_UPDATE": "1",
    "HOMEBREW_NO_INSTALL_CLEANUP": "1",
    "HOMEBREW_NO_ANALYTICS": "1",
    "HOMEBREW_NO_ENV_HINTS": "1",
    "HOMEBREW_NO_COLOR": "1",
}
# \Z, not $: "$" also matches before a trailing newline, so "x\n" would pass.
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9@+._-]*\Z")
SCAN_TIMEOUT = 20
CLEAN_TIMEOUT = 300
BREW_PATHS = ("/opt/homebrew/bin/brew", "/usr/local/bin/brew")


@dataclass
class BrewRunner:
    path: str
    prefix: str
    run: Callable
    service_dirs: tuple = ()

    @property
    def cellar(self) -> str:
        return os.path.join(self.prefix, "Cellar")

    @property
    def caskroom(self) -> str:
        return os.path.join(self.prefix, "Caskroom")


def call(brew: BrewRunner, args: list, timeout: float) -> subprocess.CompletedProcess:
    return brew.run([brew.path, *args], capture_output=True, text=True, timeout=timeout,
                    env={**os.environ, **BREW_ENV})


def find_brew(home: str) -> Optional[BrewRunner]:
    for path in BREW_PATHS:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return BrewRunner(
                path=path, prefix=os.path.dirname(os.path.dirname(path)), run=subprocess.run,
                service_dirs=(os.path.join(home, "Library", "LaunchAgents"), "/Library/LaunchDaemons"))
    return None


@dataclass
class Formula:
    name: str
    version: str
    time: Optional[float]
    on_request: bool
    deps: frozenset = field(default_factory=frozenset)
    deprecated: bool = False
    pinned: bool = False


def parse_info(text: str) -> dict:
    out = {}
    for f in json.loads(text).get("formulae", []):
        kegs = f.get("installed") or []
        if not kegs:
            continue
        linked = f.get("linked_keg")
        keg = next((k for k in kegs if k.get("version") == linked), None)
        if keg is None:
            keg = max(kegs, key=lambda k: core.version_key(k.get("version", "")))
        name = f.get("full_name") or f.get("name")
        deps = frozenset(d["full_name"] for d in keg.get("runtime_dependencies") or []
                         if d.get("full_name"))
        t = keg.get("time")
        out[name] = Formula(
            name=name, version=keg.get("version", ""),
            time=float(t) if isinstance(t, (int, float)) else None,
            on_request=bool(keg.get("installed_on_request")), deps=deps,
            deprecated=bool(f.get("deprecated") or f.get("disabled")), pinned=bool(f.get("pinned")))
    return out


_UNITS = {"B": 1, "KB": 1000, "MB": 1000 ** 2, "GB": 1000 ** 3, "TB": 1000 ** 4}


def parse_size(s: str) -> Optional[int]:
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([KMGT]?B)\s*", s or "")
    return int(float(m.group(1)) * _UNITS[m.group(2)] + 0.5) if m else None


_WOULD_REMOVE = re.compile(r"^Would remove: (.+) \(([^()]*)\)\s*$")


def parse_cleanup(text: str, prefix: str) -> int:
    total = 0
    for line in text.splitlines():
        m = _WOULD_REMOVE.match(line)
        if not m or not m.group(1).startswith(prefix + os.sep):
            continue
        size = parse_size(m.group(2).rsplit(",", 1)[-1])
        total += size or 0
    return total


def parse_would_autoremove(text: str) -> list:
    names, inside = [], False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("==>"):
            inside = bool(re.match(r"==> Would autoremove \d+ unneeded formulae?:", s))
        elif inside and s:
            names.append(s)
        elif inside:
            inside = False
    return names


def parse_would_uninstall(text: str) -> list:
    return [m.group(1) for line in text.splitlines()
            if (m := re.match(r"^Would uninstall (\S+) \S+", line))]


# ---------------------------------------------------------------- scan

CATEGORY = "Homebrew"
SCAN_WARNING = "couldn't read Homebrew; its packages are not offered"
OTHER_ACCOUNT_NOTE = "Homebrew belongs to another account on this Mac"
SAFE_LABEL = "Old Homebrew versions and unneeded libraries"
RUNTIME_BASES = frozenset({"python", "node", "openjdk", "ruby", "go", "rust", "php", "perl",
                           "postgresql", "mysql", "mariadb", "redis", "mongodb-community"})


def SERVICE_NOTE(name: str) -> str:
    return f"Runs as a background service. Stop it with `brew services stop {name}` first."


def SERVICE_BLOCK(name: str) -> str:
    return f"{name} runs as a background service. Stop it with `brew services stop {name}` first."


def PINNED_NOTE(name: str) -> str:
    return f"Pinned: run `brew unpin {name}` first."


def _short(name: str) -> str:
    """A tap formula's short name (user/tap/x -> x): the name of its Cellar rack and service."""
    return name.rsplit("/", 1)[-1]


def _has_service(brew: BrewRunner, name: str) -> bool:
    return any(os.path.exists(os.path.join(d, f"homebrew.mxcl.{_short(name)}.plist"))
               for d in brew.service_dirs)


def _keg_dir(brew: BrewRunner, name: str) -> str:
    return os.path.join(brew.cellar, _short(name))


def _read_brew(brew: BrewRunner, full: bool = True):
    """(formulae, cleanup bytes, autoremove names), or None when any call fails.
    Not `full`: `info` only, and no cleanup bytes or autoremove names (a package's gate)."""
    forms = [["info", "--json=v2", "--installed"]]
    if full:
        forms += [["cleanup", "-n"], ["autoremove", "-n"]]
    try:
        with ThreadPoolExecutor(max_workers=len(forms)) as ex:
            results = list(ex.map(lambda a: call(brew, a, SCAN_TIMEOUT), forms))
        if any(r.returncode != 0 for r in results):
            return None
        if not full:
            return parse_info(results[0].stdout), 0, []
        return (parse_info(results[0].stdout), parse_cleanup(results[1].stdout, brew.prefix),
                parse_would_autoremove(results[2].stdout))
    except Exception:
        return None


def _family_note(f: Formula, listed: dict) -> Optional[str]:
    base = f.name.split("@")[0]
    others = [g for g in listed.values()
              if g.name != f.name and g.name.split("@")[0] == base]
    if not others:
        return None
    key = core.version_key
    newer = [g for g in others if key(g.version) > key(f.version)]
    if newer:
        top = max(newer, key=lambda g: key(g.version))
        major = top.version.split(".")[0]
        return f"older version · {base} {major} also installed"
    return "also installed: " + ", ".join(sorted(g.name for g in others))


def scan_brew(cfg, env, warnings: list, sizes: bool = True, full: bool = True) -> tuple:
    """(items, Cellar + Caskroom bytes). Not `full`: package rows only, from `info` alone, with
    the same listing rules (a package's gate needs no `cleanup -n` or `autoremove -n`)."""
    brew = env.brew
    if brew is None or not os.path.isdir(brew.cellar):
        return [], 0
    du = core.du_bytes

    def kegs_bytes(names):
        return sum((du(_keg_dir(brew, n)) or 0) for n in names)

    def total_bytes():
        return (du(brew.cellar) or 0) + ((du(brew.caskroom) or 0) if os.path.isdir(brew.caskroom) else 0)

    if not os.access(brew.cellar, os.W_OK):
        item = core.Item(key="brew:other-account", category=CATEGORY, label="Homebrew",
                         tier=core.PROTECTED, kind="info", note=OTHER_ACCOUNT_NOTE)
        return [item], (total_bytes() if sizes else 0)

    read = _read_brew(brew, full)
    if read is None:
        warnings.append(SCAN_WARNING)
        return [], 0
    formulae, cleanup_bytes, autoremove = read
    auto = set(autoremove)
    all_deps = set().union(*(f.deps for f in formulae.values())) if formulae else set()
    listed = {n: f for n, f in formulae.items() if f.on_request and n not in all_deps}

    items = []
    for name, f in listed.items():
        libs = [d for d in sorted(f.deps)
                if d in formulae and not formulae[d].on_request and d not in auto
                and not any(d in g.deps for n2, g in formulae.items()
                            if n2 != name and g.on_request)]
        key = f"brew:pkg:{name}"
        if _has_service(brew, name):
            items.append(core.Item(key=key, category=CATEGORY, label=name, tier=core.PROTECTED,
                                   kind="info", note=SERVICE_NOTE(name)))
            continue
        if f.pinned:
            # `brew uninstall` exits 0 for a pinned formula and removes nothing.
            items.append(core.Item(key=key, category=CATEGORY, label=name, tier=core.PROTECTED,
                                   kind="info", note=PINNED_NOTE(name)))
            continue
        if not NAME_RE.match(name):
            items.append(core.Item(
                key=key, category=CATEGORY, label=name, tier=core.PROTECTED, kind="info",
                note=f"Can't be removed from here: run brew uninstall {name} yourself."))
            continue
        lib_bytes = kegs_bytes(libs) if sizes and libs else 0
        notes = []
        if f.time is not None:
            notes.append(f"installed {core._apps()._month_year(f.time)}")
        fam = _family_note(f, listed)
        if fam:
            notes.append(fam)
        if name.split("@")[0] in RUNTIME_BASES:
            notes.append("Projects or scripts you run may use it")
        if f.deprecated:
            notes.append("Homebrew no longer maintains it")
        if libs and sizes:
            notes.append(f"plus about {core.human(lib_bytes)} of libraries only it uses")
        size = ((du(_keg_dir(brew, name)) or 0) + lib_bytes) if sizes else None
        items.append(core.Item(
            key=key, category=CATEGORY, label=name, tier=core.CAUTION, kind="brew",
            argv=[brew.path, "uninstall", "--formula", name], size=size, note=" · ".join(notes),
            preselect=False, meta={"version": f.version, "installed": f.time, "libraries": libs}))

    if full and (cleanup_bytes or autoremove):
        size = cleanup_bytes + (kegs_bytes(autoremove) if sizes else 0)
        blocked = next((n for n in autoremove if _has_service(brew, n)), None)
        if blocked:
            tier, note, kind, argv = core.PROTECTED, SERVICE_BLOCK(blocked), "info", None
        else:
            tier, kind, argv = core.SAFE, "brew", [brew.path, "cleanup"]
            note = ("Removes old versions Homebrew keeps, and libraries nothing uses: "
                    + ", ".join(autoremove)) if autoremove else "Removes old versions Homebrew keeps."
        items.append(core.Item(key="brew:cleanup", category=CATEGORY, label=SAFE_LABEL, tier=tier,
                               kind=kind, argv=argv, size=size, note=note,
                               preselect=tier == core.SAFE, meta={"libraries": list(autoremove)}))
    return items, (total_bytes() if sizes else 0)


# ---------------------------------------------------------------- gate, run, preview

BREW_BUSY = "Homebrew is busy with another command. Try again when it's done."
PREVIEW_FAILED = "Couldn't ask Homebrew what this would remove. Scan again."
BREW_UNAVAILABLE = "REFUSED: Homebrew is not available"


def validate(it, fresh, env) -> None:
    """The last check before brew runs. `fresh` is the rescanned item, or None for
    `brew:cleanup` when the rescan found nothing to clean. Raises core.UnsafeTarget."""
    brew = env.brew
    if brew is None:
        raise core.UnsafeTarget("Homebrew is not available")
    if it.kind != "brew" or not it.argv or it.argv[0] != brew.path:
        raise core.UnsafeTarget(core.SITUATION_CHANGED)
    if it.key == "brew:cleanup":
        if it.argv != [brew.path, "cleanup"]:
            raise core.UnsafeTarget(core.SITUATION_CHANGED)
        return
    if not it.key.startswith("brew:pkg:") or fresh is None:
        raise core.UnsafeTarget(core.SITUATION_CHANGED)
    name = it.key[len("brew:pkg:"):]
    if (not NAME_RE.match(name) or it.argv != [brew.path, "uninstall", "--formula", name]
            or fresh.meta.get("version") != it.meta.get("version")):
        raise core.UnsafeTarget(core.SITUATION_CHANGED)


def _refusal(text: str) -> Optional[str]:
    """Homebrew's own "Refusing to uninstall ... because ..." lines, without the advice that
    follows them (it suggests --ignore-dependencies, which this app never passes)."""
    lines = [ln.strip() for ln in (text or "").splitlines()]
    start = next((i for i, ln in enumerate(lines) if "Refusing to uninstall" in ln), None)
    if start is None:
        return None
    out = []
    for ln in lines[start:]:
        if not ln or ln.startswith("You can override"):
            break
        out.append(ln)
    out[0] = out[0][out[0].index("Refusing to uninstall"):]
    return " ".join(out)


def _still_installed(text: str) -> Optional[str]:
    """Homebrew's "<name> <versions> is still installed." line(s), if it printed any."""
    lines = [ln.strip() for ln in (text or "").splitlines() if "still installed" in ln]
    return " ".join(lines) or None


def run_item(it, env) -> tuple:
    """Run a validated brew item through env.brew. Returns (ok, message)."""
    try:
        r = call(env.brew, list(it.argv[1:]), CLEAN_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e) or "brew failed"
    out, err = (r.stdout or ""), (r.stderr or "")
    refused = _refusal(err) or _refusal(out)
    if refused:
        return False, refused
    if r.returncode != 0:
        err = err.strip()
        if "already locked" in err or "Operation already in progress" in err:
            return False, BREW_BUSY
        return False, err or f"exit {r.returncode}"
    if it.key.startswith("brew:pkg:"):
        # Exit 0 isn't proof: a pinned formula, or one of several kegs, leaves the package in place.
        name = it.key[len("brew:pkg:"):]
        if os.path.lexists(_keg_dir(env.brew, name)):
            if "is pinned" in err:
                return False, PINNED_NOTE(name)
            return False, (_still_installed(out) or _still_installed(err)
                           or f"{name} is still installed.")
    return True, "ok"


def MISSING_FROM_PREVIEW(name: str) -> str:
    return f"{name} can't be removed right now. Scan again."


def preview_uninstall(names: list, env) -> dict:
    """Ask Homebrew what uninstalling `names` would remove, without removing anything."""
    brew = env.brew
    if brew is None:
        return {"ok": False, "error": PREVIEW_FAILED}
    if not names or not all(isinstance(n, str) and NAME_RE.match(n) for n in names):
        return {"ok": False, "error": PREVIEW_FAILED}
    try:
        r = call(brew, ["uninstall", "--dry-run", "--formula", *names], SCAN_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return {"ok": False, "error": PREVIEW_FAILED}
    refused = _refusal(r.stderr) or _refusal(r.stdout)
    if refused:
        return {"ok": False, "error": refused}
    if r.returncode != 0:
        return {"ok": False, "error": PREVIEW_FAILED}
    packages = parse_would_uninstall(r.stdout)
    libraries = parse_would_autoremove(r.stdout)
    # A name Homebrew wouldn't uninstall (pinned, say) must not pass as previewed.
    missing = next((n for n in names if n not in packages), None)
    if missing:
        return {"ok": False, "error": MISSING_FROM_PREVIEW(missing)}
    for n in [*names, *packages, *libraries]:
        if _has_service(brew, n):
            return {"ok": False, "error": SERVICE_BLOCK(n)}
    return {"ok": True, "packages": packages, "libraries": libraries}


def rescan_cleanup(cfg, env) -> tuple:
    """(fresh `brew:cleanup` item or None, read cleanly), from ONE full scan. Read cleanly:
    own Cellar, every call answered, so a missing item means "nothing to clean", not
    "couldn't look"."""
    brew = env.brew
    if brew is None or not os.path.isdir(brew.cellar):
        return None, False
    warnings = []
    items, _ = scan_brew(cfg, env, warnings, sizes=False)
    fresh = next((i for i in items if i.key == "brew:cleanup"), None)
    return fresh, not warnings and not any(i.key == "brew:other-account" for i in items)


def read_cleanly(cfg, env) -> bool:
    """True only when Homebrew was read cleanly and has nothing for `brew cleanup` (no item,
    offered or kept): the case where a ticked cleanup may still run."""
    fresh, clean = rescan_cleanup(cfg, env)
    return clean and fresh is None
