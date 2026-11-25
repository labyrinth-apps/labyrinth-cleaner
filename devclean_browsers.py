"""Chromium browsers: on-device AI models and page caches in ~/Library/Application Support.

The browser table and the model and cache folder names come from MacSweep
(github.com/nihal4/macsweep, MIT licence).

This is the one place the app may delete anything inside ~/Library/Application Support, and
only the exact folder names below, never in "System Profile" or "Guest Profile", never through
a symlink, and never while that browser runs. Everything else in a browser's folder
(passwords, cookies, history, Local Storage, IndexedDB, extensions) is never touched.

The tables are plain data so a Windows port only swaps the base folder: Chromium uses the
same layout inside %LOCALAPPDATA%\\<Vendor>\\<Browser>\\User Data.

Imports devclean_core at load; the core imports this module only on first use.
"""

import os
import re
from typing import Optional

import devclean_core as core
from devclean_core import CAUTION, PROTECTED, SAFE, Item

CATEGORY = "Browser"
APP_SUPPORT = "Library/Application Support"

# (name, bundle ID, data folder relative to ~/Library/Application Support, own_profile).
# own_profile: the browser keeps its only profile in the data folder itself (Opera).
BROWSERS = [
    ("Chrome", "com.google.Chrome", "Google/Chrome", False),
    ("Chrome Beta", "com.google.Chrome.beta", "Google/Chrome Beta", False),
    ("Chrome Canary", "com.google.Chrome.canary", "Google/Chrome Canary", False),
    ("Brave", "com.brave.Browser", "BraveSoftware/Brave-Browser", False),
    ("Edge", "com.microsoft.edgemac", "Microsoft Edge", False),
    ("Arc", "company.thebrowser.Browser", "Arc/User Data", False),
    ("Vivaldi", "com.vivaldi.Vivaldi", "Vivaldi", False),
    ("Opera", "com.operasoftware.Opera", "com.operasoftware.Opera", True),
    ("Opera GX", "com.operasoftware.OperaGX", "com.operasoftware.OperaGX", True),
    ("Chromium", "org.chromium.Chromium", "Chromium", False),
]
# Browsers whose model note names where the on-device AI setting is.
SETTINGS_PATH_BROWSERS = ("Chrome", "Chrome Beta", "Chrome Canary")
SETTINGS_PATH = " (Settings › System › On-device AI)"

# (folder directly in the data folder, label, tier, what it does)
MODELS = [
    ("OptGuideOnDeviceModel", "Gemini Nano", SAFE,
     "Google's on-device language model: Help me write, page summaries, tab organising."),
    ("OptGuideOnDeviceClassifierModel", "Scam detection model", CAUTION, "Checks pages for scams."),
    ("optimization_guide_model_store", "Small feature models", SAFE,
     "Small models for features such as tab grouping and form filling."),
    ("OnDeviceHeadSuggestModel", "Address bar suggestions", SAFE, "Suggests searches as you type."),
    ("screen_ai", "Screen AI", SAFE, "Reads text in images and PDFs, for accessibility."),
    ("SODA", "Live Caption", SAFE, "Speech recognition for Live Caption."),
    ("SODALanguagePacks", "Live Caption languages", SAFE, "Language packs for Live Caption."),
]
SCAM_MODEL = "OptGuideOnDeviceClassifierModel"
# Directly in the data folder.
SHARED_CACHES = ("GrShaderCache", "ShaderCache", "GraphiteDawnCache")
# In each profile folder.
PROFILE_CACHES = ("Cache", "Code Cache", "GPUCache", "DawnGraphiteCache", "DawnWebGPUCache")
HIDDEN_PROFILES = ("System Profile", "Guest Profile")
_HIDDEN = frozenset(p.casefold() for p in HIDDEN_PROFILES)

LOCKED_NOTE = "{name}'s folder is locked by macOS. Its AI models and caches can't be measured."
RUNNING_NOTE = "Quit {name} first"
PAGE_CACHE_COST = "Pages and scripts load a bit slower the first time. You stay logged in."
SCAM_WARNING = "Scam and phishing warnings may be weaker until the browser downloads it again."
REDOWNLOAD = "{name} downloads it again unless you turn off on-device AI in its settings."
BROWSERS_UNLISTED_WARNING = "couldn't list running apps; browser caches are not offered"
MODEL_RESTORE = "The browser downloads it again unless on-device AI is off."
CACHES_RESTORE = "The browser rebuilds them as you browse."

VERSION_RE = re.compile(r"^\d+(?:\.\d+)+$")


# ---------------------------------------------------------------- folders

def _data_folder(cfg: core.Config, data_rel: str) -> str:
    return os.path.join(cfg.p(APP_SUPPORT), data_rel)


def _symlinked(cfg: core.Config, data_rel: str) -> bool:
    """True when Application Support, the data folder or any folder between them is a
    symlink. Walks the path built from the config, never a resolved one."""
    p = cfg.p(APP_SUPPORT)
    if os.path.islink(p):
        return True
    for part in data_rel.split("/"):
        p = os.path.join(p, part)
        if os.path.islink(p):
            return True
    return False


def _real_dir(path: str) -> bool:
    return os.path.isdir(path) and not os.path.islink(path)


def profiles(data: str, own_profile: bool) -> list:
    """Sorted names of the subfolders of `data` holding a Preferences file, except the hidden
    profiles and symlinks. "" (the data folder itself) comes first for an own-profile browser
    whose data folder holds Preferences. [] when the folder can't be listed."""
    try:
        names = sorted(os.listdir(data))
    except OSError:
        return []
    out = [""] if own_profile and os.path.isfile(os.path.join(data, "Preferences")) else []
    for name in names:
        p = os.path.join(data, name)
        if (name.casefold() not in _HIDDEN and _real_dir(p)
                and os.path.isfile(os.path.join(p, "Preferences"))):
            out.append(name)
    return out


def _cache_paths(data: str, own_profile: bool) -> list:
    """Every cache folder that may be deleted, shared ones first, then each profile's in order.
    Only existing folders that aren't symlinks."""
    cand = [os.path.join(data, n) for n in SHARED_CACHES]
    for prof in profiles(data, own_profile):
        cand += [os.path.join(data, prof, n) if prof else os.path.join(data, n)
                 for n in PROFILE_CACHES]
    return [p for p in cand if _real_dir(p)]


def _newest_version(path: str) -> Optional[str]:
    try:
        names = [n for n in os.listdir(path) if VERSION_RE.match(n)]
    except OSError:
        return None
    return max(names, key=core.version_key) if names else None


def _browser_of(key: str):
    """The BROWSERS row a `browser:<bundle_id>:...` key names, or None."""
    parts = key.split(":")
    if len(parts) < 3 or parts[0] != "browser":
        return None
    return next((b for b in BROWSERS if b[1] == parts[1]), None)


# ---------------------------------------------------------------- scan

def scan_browsers(cfg: core.Config, env: core.Env, warnings: Optional[list] = None,
                  only: Optional[str] = None) -> list:
    """AI model rows and one page-cache row per Chromium browser whose data folder exists.

    A folder macOS won't let us list gets one locked row. A symlinked data folder (or a
    symlinked folder above it) is skipped. A running browser's rows are kept, not offered.
    When the running apps can't be listed nothing is offered and a warning is added.
    `only` limits the scan to one bundle ID (the gate's rescan)."""
    present = [b for b in BROWSERS
               if (only is None or b[1] == only)
               and os.path.lexists(_data_folder(cfg, b[2]))]
    if not present:
        return []
    try:
        running = {bid.casefold() for bid, _ in env.running_apps()}
    except Exception:
        if warnings is not None:
            warnings.append(BROWSERS_UNLISTED_WARNING)
        return []
    items = []
    for name, bid, rel, own in present:
        data = _data_folder(cfg, rel)
        if _symlinked(cfg, rel) or not os.path.isdir(data):
            continue
        try:
            os.listdir(data)
        except PermissionError:
            note = (RUNNING_NOTE if bid.casefold() in running else LOCKED_NOTE).format(name=name)
            items.append(Item(key=f"browser:{bid}:locked", category=CATEGORY, label=name,
                              tier=PROTECTED, kind="info", note=note))
            continue
        except OSError:
            continue
        rows = []
        for folder, label, tier, what in MODELS:
            path = os.path.join(data, folder)
            if not _real_dir(path):
                continue
            size = core.du_bytes(path)
            if not size:
                continue
            rows.append(Item(key=f"browser:{bid}:model:{folder}", category=CATEGORY,
                             label=f"{name}: {label}", tier=tier, kind="path", path=path,
                             size=size, preselect=False,
                             warning=SCAM_WARNING if folder == SCAM_MODEL else "",
                             meta={"what": what, "version": _newest_version(path)}))
        existing = _cache_paths(data, own)
        if existing:
            rows.append(Item(key=f"browser:{bid}:caches", category=CATEGORY,
                             label=f"{name} page caches", tier=SAFE, kind="paths", path=data,
                             argv=["rm", "-rf", "--", *existing], preselect=False))
        if bid.casefold() in running:
            for it in rows:
                if it.kind == "paths":
                    # Measured here: the scan would otherwise size the whole browser folder.
                    it.size = sum(core.du_bytes(p) or 0 for p in it.argv[3:])
                it.tier, it.kind, it.argv, it.preselect = PROTECTED, "info", None, False
                it.note = RUNNING_NOTE.format(name=name)
        items += rows
    return items


def rescan(key: str, cfg: core.Config, env: core.Env) -> list:
    """The gate's fresh look at the one browser a key names."""
    b = _browser_of(key)
    return scan_browsers(cfg, env, only=b[1]) if b else []


# ---------------------------------------------------------------- gate

def allowed_path(real: str, cfg: core.Config) -> bool:
    """True only when `real` is exactly one of the folders a browser row may delete:
    data/<model or shared cache>, data/<profile>/<profile cache> for a listed profile, or
    data/<profile cache> for an own-profile browser whose data folder holds Preferences.
    Never through a symlinked data folder or any symlinked folder up to Application Support,
    and never a symlink itself."""
    for name, bid, rel, own in BROWSERS:
        data = _data_folder(cfg, rel)
        if not core.is_within(real, data) or real == data:
            continue                       # cheap: no disk access for paths elsewhere
        if _symlinked(cfg, rel) or os.path.islink(real) or not os.path.isdir(real):
            return False
        allowed = {os.path.join(data, n) for n in (*(m[0] for m in MODELS), *SHARED_CACHES)}
        for prof in profiles(data, own):
            allowed.update(os.path.join(data, prof, n) if prof else os.path.join(data, n)
                           for n in PROFILE_CACHES)
        return real in allowed
    return False


# ---------------------------------------------------------------- cost & restore

def redownload_note(name: str) -> str:
    note = REDOWNLOAD.format(name=name)
    if name in SETTINGS_PATH_BROWSERS:
        note = note[:-1] + SETTINGS_PATH + "."
    return note


def cost(it: Item) -> tuple:
    """(tag, text) for an offered browser row."""
    if it.kind == "paths":
        return "SLOWER", PAGE_CACHE_COST
    b = _browser_of(it.key)
    name = b[0] if b else "The browser"
    return f"{core.human(it.size)} DL", f"{it.meta.get('what', '')} {redownload_note(name)}".strip()


def restore(it: Item) -> str:
    return CACHES_RESTORE if it.kind == "paths" else MODEL_RESTORE
