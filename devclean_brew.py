"""Homebrew: the runner every brew call goes through, and parsers for its output."""

import json
import os
import re
import subprocess
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
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9@+._-]*$")
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
            deprecated=bool(f.get("deprecated") or f.get("disabled")))
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
