"""Tests for devclean_report: rows, groups, disk map and headline."""

import json
import os
import shutil
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import devclean_core as core  # noqa: E402
import devclean_report as rep  # noqa: E402
from test_devclean import cfg_for, fixture_sim, make_home  # noqa: E402

ZERO = dict.fromkeys(["sim_devices", "android_sdk", "android_avd", "gradle_modules"], 0)


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="devclean-test-"))
        self.home = make_home(self.tmp)
        self.res = self.scan()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def scan(self):
        return core.scan(cfg_for(self.home), fixture_sim(), xcode_check=lambda: False)

    def rows(self):
        return {r["id"]: r for r in rep.build_rows(self.res.items, self.home)}

    def test_flutter_build_and_dart_tool_one_row(self):
        r = self.rows()["safe:flutter_app"]
        self.assertEqual(r["label"], "flutter_app · build + .dart_tool")
        self.assertEqual(sorted(r["keys"]),
                         ["flutter:Documents/Projects/flutter_app/.dart_tool", "flutter:Documents/Projects/flutter_app/build"])
        self.assertEqual(r["size"], sum(m["size"] for m in r["members"]))
        self.assertEqual(r["path"], "~/Documents/Projects/flutter_app")
        self.assertEqual(r["group"], "safe")
        self.assertEqual(r["segment"], "caches")
        self.assertEqual(r["cost_tag"], "~2 MIN")
        self.assertTrue(r["preselect"])

    def test_mixed_project_one_row_per_group(self):
        rows = self.rows()
        self.assertIn("safe:labryinth-iptv", rows)
        self.assertIn("time:labryinth-iptv", rows)

    def test_nested_project_label_uses_basename(self):
        r = self.rows()["safe:labryinth-iptv/third_party/plugin"]
        self.assertEqual(r["label"], "plugin · .dart_tool")

    def test_web_row_combines_node_modules_and_next(self):
        r = self.rows()["time:web_app"]
        self.assertEqual(len(r["keys"]), 2)
        self.assertFalse(r["preselect"])
        self.assertEqual(r["segment"], "web")
        self.assertIn("npm install", r["cost"])  # largest member is node_modules or tie → node_modules line

    def test_single_item_rows(self):
        r = self.rows()["homebrew-cache"]
        self.assertEqual(r["keys"], ["homebrew-cache"])
        self.assertEqual(r["path"], "~/Library/Caches/Homebrew")
        self.assertEqual(r["label"], "Homebrew cache")

    def test_kept_rows_have_no_keys(self):
        kept = [r for r in self.rows().values() if r["group"] == "kept"]
        self.assertTrue(kept)
        for r in kept:
            self.assertEqual(r["keys"], [])
            self.assertFalse(r["preselect"])

    def test_rows_ordered_by_group_then_size(self):
        order = {"safe": 0, "time": 1, "check": 2, "kept": 3}
        keys = [(order[r["group"]], -(r["size"] or 0))
                for r in rep.build_rows(self.res.items, self.home)]
        self.assertEqual(keys, sorted(keys))

    def test_stale_only_in_time_group(self):
        P = os.path.join(self.home, "Documents", "Projects")
        old = time.time() - 95 * 86400
        for proj in ("web_app", "flutter_app"):
            for name in os.listdir(os.path.join(P, proj)):
                os.utime(os.path.join(P, proj, name), (old, old))
        self.res = self.scan()
        rows = self.rows()
        self.assertEqual(rows["time:web_app"]["stale"], "3 mo unused")
        self.assertIsNone(rows["safe:flutter_app"]["stale"])
        self.assertIsNone(rows["time:labryinth-iptv"]["stale"])  # fresh project

    def test_segment_of(self):
        by = {i.key: i for i in self.res.items}
        self.assertEqual(rep.segment_of(by["derived-data"]), "xcode")
        self.assertEqual(rep.segment_of(by["archives"]), "xcode")
        self.assertEqual(rep.segment_of(by["downloads"]), "other")
        self.assertEqual(rep.segment_of(by["gradle:8.14"]), "caches")
        rt = [i for i in self.res.items if i.category == "Simulator"][0]
        self.assertEqual(rep.segment_of(rt), "sim")

    def test_disk_map_sums_to_total(self):
        extras = {"sim_devices": 10, "android_sdk": 20, "android_avd": 5, "gradle_modules": 7}
        segs = rep.disk_map(self.res, extras)
        self.assertEqual([s["id"] for s in segs], [i for i, _ in rep.SEGMENTS])
        self.assertEqual([s["label"] for s in segs], [lbl for _, lbl in rep.SEGMENTS])
        self.assertEqual(sum(s["bytes"] for s in segs), self.res.total_bytes)
        by = {s["id"]: s for s in segs}
        self.assertEqual(by["android"]["bytes"], 25)
        self.assertEqual(by["free"]["bytes"], self.res.free_bytes)
        self.assertEqual(by["other"]["reclaimable"], 0)

    def test_disk_map_other_never_negative(self):
        self.res.free_bytes = self.res.total_bytes  # used = 0
        extras = dict(ZERO, sim_devices=10 ** 9)
        segs = {s["id"]: s for s in rep.disk_map(self.res, extras)}
        self.assertEqual(segs["other"]["bytes"], 0)

    def test_reclaimable_overlay(self):
        segs = {s["id"]: s for s in rep.disk_map(self.res, ZERO)}
        rt = sum(i.size or 0 for i in self.res.offerable if rep.segment_of(i) == "sim")
        self.assertGreater(rt, 0)
        self.assertEqual(segs["sim"]["reclaimable"], rt)
        self.assertGreater(segs["caches"]["reclaimable"], 0)
        for s in segs.values():
            self.assertLessEqual(s["reclaimable"], max(s["bytes"], s["reclaimable"]))

    def test_simulator_segment_counts_runtimes_and_devices(self):
        segs = {s["id"]: s for s in rep.disk_map(self.res, dict(ZERO, sim_devices=123))}
        runtimes = sum(i.size or 0 for i in self.res.items
                       if rep.segment_of(i) == "sim" and i.kind != "simctl-unavailable")
        self.assertEqual(segs["sim"]["bytes"], runtimes + 123)

    def test_headline_and_payload(self):
        p = rep.scan_payload(self.res, cfg_for(self.home), "id1", 1.5, ZERO)
        self.assertEqual(p["scan_id"], "id1")
        self.assertEqual(p["duration_s"], 1.5)
        self.assertEqual(p["headline"]["reclaimable"], self.res.reclaimable())
        self.assertEqual(p["headline"]["no_cost"],
                         sum(i.size or 0 for i in self.res.offerable if i.tier == core.SAFE))
        self.assertEqual(p["disk"]["used"], self.res.total_bytes - self.res.free_bytes)
        self.assertEqual(p["small_bytes"], 20 * 1024 * 1024)
        self.assertEqual(p["home"], self.home)
        for key in ("segments", "rows", "warnings", "xcode_running", "scanned_at"):
            self.assertIn(key, p)
        json.dumps(p)

    def test_measure_extras(self):
        ex = rep.measure_extras(cfg_for(self.home))
        self.assertEqual(set(ex), set(ZERO))
        self.assertEqual(ex["android_sdk"], 0)
        self.assertEqual(ex["sim_devices"], 0)
        self.assertGreater(ex["gradle_modules"], 0)


if __name__ == "__main__":
    unittest.main()
