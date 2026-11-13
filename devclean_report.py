"""Turns a devclean ScanResult into what the web UI shows: rows, groups, disk map, headline.

Pure functions over core data, except `measure_extras`, which runs `du`.
"""

from __future__ import annotations

import os
import re
import time
from concurrent.futures import ThreadPoolExecutor

import devclean_core as core

GROUP_OF = {core.SAFE: "safe", core.OPTIN: "time", core.CAUTION: "check", core.REVIEW: "review",
            core.PROTECTED: "kept"}
GROUP_ORDER = {"safe": 0, "time": 1, "check": 2, "review": 3, "kept": 4}

SEGMENTS = [
    ("sim", "Simulators"),
    ("android", "Android"),
    ("xcode", "Xcode"),
    ("web", "node_modules"),
    ("caches", "Caches & builds"),
    ("appcache", "App caches"),
    ("docker", "Docker"),
    ("downloads", "Downloads (reviewable)"),
    ("trash", "Trash"),
    ("other", "System, apps & other files"),
    ("free", "Free"),
]
MEASURED = ("sim", "android", "xcode", "web", "caches", "appcache", "docker", "downloads", "trash")
# Segments sized from their scan items (Android is sized from extras only).
ITEM_SIZED = ("sim", "xcode", "web", "caches", "appcache", "docker", "downloads", "trash")
SMALL_BYTES = 20 * 1024 * 1024
ARTIFACT_ORDER = {name: i for i, name in enumerate(core.FLUTTER_ARTIFACTS + core.WEB_ARTIFACTS)}


def _artifact_index(it: core.Item) -> int:
    return ARTIFACT_ORDER.get(os.path.basename(it.path or ""), len(ARTIFACT_ORDER))


def segment_of(it: core.Item) -> str:
    if it.category == "Simulator" or it.kind == "simdevice":
        return "sim"
    if it.category == "Android":
        return "android"
    if it.key == "derived-data" or it.key.startswith("archive:") or it.category == "DeviceSupport":
        return "xcode"
    if it.category == "Web":
        return "web"
    if it.category == "App cache":
        return "appcache"
    if it.category == "Docker":
        return "docker"
    if it.category == "Downloads":
        return "downloads"
    if it.kind == "empty-trash":
        return "trash"
    # The whole ~/Downloads info row and iOS backups are personal files, not caches.
    if it.key == "downloads" or it.category == "iOS backups":
        return "other"
    return "caches"           # fixed caches, Gradle, Flutter builds, logs


def _tilde(path, home: str) -> str:
    if not path:
        return ""
    if path == home:
        return "~"
    if path.startswith(home + os.sep):
        return "~" + path[len(home):]
    return path


def _cost_rank(tag: str) -> int:
    """NO COST < ~1 MIN < ~2 MIN < ... < downloads."""
    if tag.endswith(" DL"):
        return 1000
    m = re.match(r"~(\d+) MIN", tag)
    return int(m.group(1)) if m else 0


def _row(rid, group, members, label, path):
    sizes = [m.size for m in members if m.size is not None]
    mtimes = [m.mtime for m in members if m.mtime is not None]
    # The row shows its most expensive member's cost; ties go to node_modules/build.
    lead = min(members, key=lambda m: (-_cost_rank(m.cost_tag), _artifact_index(m)))
    stale_days = max((m.stale_days for m in members if m.stale_days is not None), default=None)
    offer = group != "kept"
    warnings = list(dict.fromkeys(m.warning for m in members if m.warning))
    return {
        "id": rid,
        "group": group,
        "segment": segment_of(lead),
        "category": lead.category,
        "label": label,
        "path": path,
        "keys": [m.key for m in members] if offer else [],
        "size": sum(sizes) if sizes else None,
        "mtime": max(mtimes) if mtimes else None,
        "cost_tag": lead.cost_tag,
        "cost": lead.cost,
        "stale": core.stale_label(stale_days) if group == "time" else None,
        "warning": " ".join(warnings),
        "deletes": lead.deletes,
        "restore": lead.restore,
        "kind": lead.kind,
        "preselect": False,          # the page never ticks anything for the user
        "members": [{"key": m.key, "label": m.label, "size": m.size, "deletes": m.deletes}
                    for m in members],
    }


def build_rows(items: list, home: str) -> list:
    rows, projects = [], {}
    for it in items:
        group = GROUP_OF[it.tier]
        if it.project and it.meta.get("project_dir"):
            projects.setdefault((group, it.meta["project_dir"]), []).append(it)
        else:
            rows.append(_row(it.key, group, [it], it.label, _tilde(it.path, home)))
    # One row per project folder and group: two roots can each hold a project named "app".
    for (group, project_dir), members in projects.items():
        members.sort(key=_artifact_index)
        names = " + ".join(os.path.basename(m.path) for m in members)
        label = f"{os.path.basename(members[0].project)} · {names}"
        rid = f"{group}:{os.path.relpath(project_dir, home)}"
        rows.append(_row(rid, group, members, label, _tilde(project_dir, home)))
    rows.sort(key=lambda r: (GROUP_ORDER[r["group"]], -(r["size"] or 0), r["label"]))
    return rows


def measure_extras(cfg: core.Config, measure=core.du_bytes) -> dict:
    paths = {
        "sim_devices": cfg.sim_devices_dir,
        "android_sdk": cfg.android_sdk,
        "android_avd": cfg.avd_dir,
        "gradle_modules": os.path.join(cfg.gradle_caches, "modules-2"),
    }

    def one(p):
        return (measure(p) or 0) if os.path.isdir(p) else 0

    with ThreadPoolExecutor(max_workers=4) as ex:
        sizes = list(ex.map(one, paths.values()))
    return dict(zip(paths, sizes))


def disk_map(res: core.ScanResult, extras: dict) -> list:
    size = dict.fromkeys(MEASURED, 0)
    reclaim = {sid: 0 for sid, _ in SEGMENTS}
    for it in res.items:
        seg = segment_of(it)
        if it.offerable:
            reclaim[seg] += it.size or 0
        # Simulator data folders are measured whole in extras["sim_devices"].
        # Docker has no separate total: its segment is the sum of its items.
        if (seg in ITEM_SIZED and it.kind != "simctl-unavailable"
                and not it.key.startswith("simdevice:")):
            size[seg] += it.size or 0
    size["sim"] += extras.get("sim_devices", 0)
    size["android"] = extras.get("android_sdk", 0) + extras.get("android_avd", 0)
    size["caches"] += extras.get("gradle_modules", 0)
    used = res.total_bytes - res.free_bytes
    size["other"] = max(0, used - sum(size[s] for s in MEASURED))
    size["free"] = res.free_bytes
    reclaim["other"] = reclaim["free"] = 0
    return [{"id": sid, "label": label, "bytes": size[sid], "reclaimable": reclaim[sid]}
            for sid, label in SEGMENTS]


def auto_project_folders(cfg: core.Config) -> list:
    """The default project folders (~/Developer, ~/Projects, ...) that pass the project-root
    check, spelled as Config stores them."""
    found = []
    for d in core.DEFAULT_PROJECT_DIRS:
        try:
            r = core.check_project_root(cfg.home, cfg.p(d))
        except ValueError:
            continue
        if r.casefold() not in {f.casefold() for f in found}:
            found.append(r)
    return found


def scan_payload(res: core.ScanResult, cfg: core.Config, scan_id: str, duration_s: float,
                 extras: dict) -> dict:
    return {
        "scan_id": scan_id,
        "scanned_at": time.time(),
        "duration_s": duration_s,
        "home": cfg.home,
        "disk": {"total": res.total_bytes, "free": res.free_bytes,
                 "used": res.total_bytes - res.free_bytes},
        "headline": {
            "reclaimable": res.reclaimable(),
            "no_cost": sum(i.size or 0 for i in res.offerable if i.tier == core.SAFE),
        },
        "segments": disk_map(res, extras),
        "rows": build_rows(res.items, cfg.home),
        "warnings": list(res.warnings),
        "xcode_running": res.xcode_running,
        "small_bytes": SMALL_BYTES,
        "project_folders": list(cfg.project_roots),
        "auto_project_folders": auto_project_folders(cfg),
        "trash_helper": bool(os.environ.get(core.TRASH_HELPER_ENV)),
    }
