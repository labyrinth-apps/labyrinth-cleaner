"""Turns a devclean ScanResult into what the web UI shows: rows, groups, disk map, headline.

Pure functions over core data, except `measure_extras`, which runs `du`.
"""

from __future__ import annotations

import os
import re
import time
from concurrent.futures import ThreadPoolExecutor

import devclean_core as core

GROUP_OF = {core.SAFE: "safe", core.OPTIN: "time", core.CAUTION: "check", core.PROTECTED: "kept"}
GROUP_ORDER = {"safe": 0, "time": 1, "check": 2, "kept": 3}

SEGMENTS = [
    ("sim", "Simulators"),
    ("android", "Android"),
    ("xcode", "Xcode"),
    ("web", "node_modules"),
    ("caches", "Caches & builds"),
    ("other", "System, apps & other files"),
    ("free", "Free"),
]
MEASURED = ("sim", "android", "xcode", "web", "caches")
SMALL_BYTES = 20 * 1024 * 1024
ARTIFACT_ORDER = {name: i for i, name in enumerate(core.FLUTTER_ARTIFACTS + core.WEB_ARTIFACTS)}


def _artifact_index(it: core.Item) -> int:
    return ARTIFACT_ORDER.get(os.path.basename(it.path or ""), len(ARTIFACT_ORDER))


def segment_of(it: core.Item) -> str:
    if it.category == "Simulator":
        return "sim"
    if it.category == "Android":
        return "android"
    if it.key in ("derived-data", "archives") or it.category == "DeviceSupport":
        return "xcode"
    if it.category == "Web":
        return "web"
    if it.key == "downloads":
        return "other"
    return "caches"


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
        "preselect": offer and all(m.preselect for m in members),
        "members": [{"key": m.key, "label": m.label, "size": m.size} for m in members],
    }


def build_rows(items: list, home: str) -> list:
    rows, projects = [], {}
    for it in items:
        group = GROUP_OF[it.tier]
        if it.project and it.meta.get("project_dir"):
            projects.setdefault((group, it.project), []).append(it)
        else:
            rows.append(_row(it.key, group, [it], it.label, _tilde(it.path, home)))
    for (group, project), members in projects.items():
        members.sort(key=_artifact_index)
        names = " + ".join(os.path.basename(m.path) for m in members)
        label = f"{os.path.basename(project)} · {names}"
        path = _tilde(members[0].meta["project_dir"], home)
        rows.append(_row(f"{group}:{project}", group, members, label, path))
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
        if seg in ("sim", "xcode", "web", "caches") and it.kind != "simctl-unavailable":
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
    }
