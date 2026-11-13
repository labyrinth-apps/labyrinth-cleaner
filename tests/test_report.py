"""Tests for devclean_report: rows, groups, disk map and headline."""

import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

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
        r = self.rows()["safe:Documents/Projects/flutter_app"]
        self.assertEqual(r["label"], "flutter_app · build + .dart_tool")
        self.assertEqual(sorted(r["keys"]),
                         ["flutter:Documents/Projects/flutter_app/.dart_tool", "flutter:Documents/Projects/flutter_app/build"])
        self.assertEqual(r["size"], sum(m["size"] for m in r["members"]))
        self.assertEqual(r["path"], "~/Documents/Projects/flutter_app")
        self.assertEqual(r["group"], "safe")
        self.assertEqual(r["segment"], "caches")
        self.assertEqual(r["cost_tag"], "~2 MIN")
        self.assertFalse(r["preselect"])     # the page never preselects

    def test_mixed_project_one_row_per_group(self):
        rows = self.rows()
        self.assertIn("safe:Documents/Projects/labryinth-iptv", rows)
        self.assertIn("time:Documents/Projects/labryinth-iptv", rows)

    def test_nested_project_label_uses_basename(self):
        r = self.rows()["safe:Documents/Projects/labryinth-iptv/third_party/plugin"]
        self.assertEqual(r["label"], "plugin · .dart_tool")

    def test_web_row_combines_node_modules_and_next(self):
        r = self.rows()["time:Documents/Projects/web_app"]
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
        order = {"safe": 0, "time": 1, "check": 2, "review": 3, "kept": 4}
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
        self.assertEqual(rows["time:Documents/Projects/web_app"]["stale"], "3 mo unused")
        self.assertIsNone(rows["safe:Documents/Projects/flutter_app"]["stale"])
        self.assertIsNone(rows["time:Documents/Projects/labryinth-iptv"]["stale"])  # fresh project

    def test_segment_of(self):
        by = {i.key: i for i in self.res.items}
        self.assertEqual(rep.segment_of(by["derived-data"]), "xcode")
        # Task 2: the single "archives" info row became one archive: item per archive.
        arch = by["archive:2026-11-01/App.xcarchive"]
        self.assertEqual(rep.segment_of(arch), "xcode")
        self.assertEqual(rep.GROUP_OF[arch.tier], "review")
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
        # Simulator data folders are in extras["sim_devices"]; their items must not count twice.
        runtimes = sum(i.size or 0 for i in self.res.items
                       if rep.segment_of(i) == "sim" and i.kind != "simctl-unavailable"
                       and not i.key.startswith("simdevice:"))
        self.assertEqual(segs["sim"]["bytes"], runtimes + 123)

    def test_simulator_device_sizes_not_counted_twice(self):
        sim = next(i for i in self.res.items if i.key.startswith("simdevice:"))
        before = {s["id"]: s for s in rep.disk_map(self.res, ZERO)}["sim"]["bytes"]
        sim.size = 10 ** 9
        after = {s["id"]: s for s in rep.disk_map(self.res, ZERO)}["sim"]["bytes"]
        self.assertEqual(before, after)

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

    def test_rows_never_preselected(self):
        rows = rep.build_rows(self.res.items, self.home)
        self.assertTrue(any(i.preselect for i in self.res.items))   # the CLI still preselects
        self.assertTrue(rows)
        self.assertEqual({r["preselect"] for r in rows}, {False})
        p = rep.scan_payload(self.res, cfg_for(self.home), "id1", 1.0, ZERO)
        self.assertEqual({r["preselect"] for r in p["rows"]}, {False})

    def test_review_group_and_order(self):
        self.assertEqual(rep.GROUP_OF[core.REVIEW], "review")
        order = sorted(rep.GROUP_ORDER, key=rep.GROUP_ORDER.get)
        self.assertEqual(order, ["safe", "time", "check", "review", "kept"])
        rows = rep.build_rows(self.res.items, self.home)
        arch = next(r for r in rows if r["id"] == "archive:2026-11-01/App.xcarchive")
        self.assertEqual(arch["group"], "review")
        self.assertEqual(arch["kind"], "trash")
        groups = [r["group"] for r in rows]
        self.assertLess(max(i for i, g in enumerate(groups) if g == "check"),
                        groups.index("review"))
        self.assertLess(groups.index("review"), groups.index("kept"))

    def test_row_carries_warning_deletes_restore(self):
        rows = self.rows()
        arch = rows["archive:2026-11-01/App.xcarchive"]
        self.assertEqual(arch["warning"], core.ARCHIVE_WARNING)
        self.assertEqual(arch["deletes"], "~/Library/Developer/Xcode/Archives/2026-11-01/App.xcarchive")
        self.assertEqual(arch["restore"], core.TRASH_RESTORE)
        gradle = rows["gradle:9.10.0"]
        self.assertEqual(gradle["warning"], core.GRADLE_NEWEST_WARNING)
        self.assertEqual(gradle["kind"], "path")
        flutter = rows["safe:Documents/Projects/flutter_app"]
        self.assertEqual(flutter["warning"], "")
        self.assertEqual(flutter["deletes"], "~/Documents/Projects/flutter_app/build")   # the lead
        self.assertEqual([m["deletes"] for m in flutter["members"]],
                         ["~/Documents/Projects/flutter_app/build",
                          "~/Documents/Projects/flutter_app/.dart_tool"])
        self.assertEqual(flutter["restore"], "Rebuilt automatically by the next build.")
        # Member warnings are de-duplicated and joined; deletes/restore/kind come from the lead.
        d = os.path.join(self.home, "Documents", "Projects", "x")
        a = core.Item(key="web:a/node_modules", category="Web", label="a", tier=core.OPTIN,
                      path=d + "/node_modules", project="x", meta={"project_dir": d},
                      warning="W1", deletes="~/x/node_modules", restore="R1", cost_tag="~2 MIN")
        b = core.Item(key="web:a/.next", category="Web", label="b", tier=core.OPTIN,
                      path=d + "/.next", project="x", meta={"project_dir": d},
                      warning="W1", deletes="~/x/.next", restore="R2", cost_tag="~1 MIN")
        c = core.Item(key="web:a/other", category="Web", label="c", tier=core.OPTIN,
                      path=d + "/other", project="x", meta={"project_dir": d},
                      warning="W2", cost_tag="~1 MIN")
        row = rep.build_rows([b, a, c], self.home)[0]
        self.assertEqual(row["warning"], "W1 W2")
        self.assertEqual((row["deletes"], row["restore"], row["kind"]),
                         ("~/x/node_modules", "R1", "path"))

    def test_new_segments_order_and_sum(self):
        self.assertEqual(rep.SEGMENTS, [
            ("sim", "Simulators"), ("android", "Android"), ("xcode", "Xcode"),
            ("web", "node_modules"), ("caches", "Caches & builds"), ("appcache", "App caches"),
            ("docker", "Docker"), ("downloads", "Downloads (reviewable)"), ("trash", "Trash"),
            ("other", "System, apps & other files"), ("free", "Free")])
        h = self.home
        os.makedirs(f"{h}/Library/Caches/com.example.app")
        with open(f"{h}/Library/Caches/com.example.app/blob", "w") as f:
            f.write("z" * 9000)
        os.makedirs(f"{h}/.Trash")
        with open(f"{h}/.Trash/junk.bin", "w") as f:
            f.write("t" * 7000)
        os.makedirs(f"{h}/Library/Logs")
        with open(f"{h}/Library/Logs/old.log", "w") as f:
            f.write("l" * 3000)
        old = time.time() - 30 * 86400
        os.utime(f"{h}/Library/Logs/old.log", (old, old))
        with open(f"{h}/Downloads/Setup.dmg", "w") as f:
            f.write("d" * 5000)
        os.utime(f"{h}/Downloads/Setup.dmg", (old, old))
        os.makedirs(f"{h}/Library/Application Support/MobileSync/Backup/abc")
        with open(f"{h}/Library/Application Support/MobileSync/Backup/abc/m", "w") as f:
            f.write("b" * 11000)
        df = '{"Type":"Images","Reclaimable":"2GB (40%)"}\n{"Type":"Build Cache","Reclaimable":"1.5GB"}'

        def docker(argv, **kw):
            return core.subprocess.CompletedProcess(argv, 0, df, "")

        env = core.Env.fake(docker=docker)
        self.res = core.scan(cfg_for(h), fixture_sim(), xcode_check=lambda: False, env=env)
        by = {i.key: i for i in self.res.items}
        self.assertEqual(rep.segment_of(by["appcache:com.example.app"]), "appcache")
        self.assertEqual(rep.segment_of(by["docker:images"]), "docker")
        self.assertEqual(rep.segment_of(by["download:Setup.dmg"]), "downloads")
        self.assertEqual(rep.segment_of(by["empty-trash"]), "trash")
        self.assertEqual(rep.segment_of(by["archive:2026-11-01/App.xcarchive"]), "xcode")
        self.assertEqual(rep.segment_of(by["log:old.log"]), "caches")
        self.assertEqual(rep.segment_of(by["ios-backups"]), "other")
        sim = next(i for i in self.res.items if i.kind == "simdevice")
        self.assertEqual(rep.segment_of(sim), "sim")
        extras = {"sim_devices": 10, "android_sdk": 20, "android_avd": 5, "gradle_modules": 7}
        segs = rep.disk_map(self.res, extras)
        self.assertEqual([s["id"] for s in segs], [i for i, _ in rep.SEGMENTS])
        self.assertEqual(sum(s["bytes"] for s in segs), self.res.total_bytes)
        seg = {s["id"]: s for s in segs}
        self.assertEqual(seg["docker"]["bytes"], 3500 * 1000 ** 2)
        self.assertEqual(seg["docker"]["reclaimable"], 3500 * 1000 ** 2)
        self.assertEqual(seg["downloads"]["bytes"], by["download:Setup.dmg"].size)
        self.assertEqual(seg["trash"]["bytes"], by["empty-trash"].size)
        self.assertGreater(seg["trash"]["bytes"], 0)
        self.assertEqual(seg["appcache"]["bytes"], by["appcache:com.example.app"].size)

    def test_project_rows_grouped_by_dir_across_roots(self):
        h = self.home
        for root in ("Developer", "Projects"):
            os.makedirs(f"{h}/{root}/app/build")
            with open(f"{h}/{root}/app/pubspec.yaml", "w") as f:
                f.write("name: app")
        cfg = core.Config(home=h)
        res = core.scan(cfg, core.NoSimctl(), xcode_check=lambda: False, env=core.Env.fake())
        rows = {r["id"]: r for r in rep.build_rows(res.items, h)}
        self.assertIn("safe:Developer/app", rows)
        self.assertIn("safe:Projects/app", rows)
        self.assertEqual(rows["safe:Developer/app"]["keys"], ["flutter:Developer/app/build"])
        self.assertEqual(rows["safe:Projects/app"]["keys"], ["flutter:Projects/app/build"])
        self.assertEqual(rows["safe:Developer/app"]["path"], "~/Developer/app")

    def test_payload_project_folders_and_trash_helper(self):
        os.makedirs(os.path.join(self.home, "Developer"))
        extra = os.path.join(self.home, "Work")
        os.makedirs(extra)
        core.add_project_folder(self.home, extra)
        cfg = core.Config(home=self.home)
        env = {k: v for k, v in os.environ.items() if k != core.TRASH_HELPER_ENV}
        with mock.patch.dict(os.environ, env, clear=True):
            p = rep.scan_payload(self.res, cfg, "id1", 1.0, ZERO)
        self.assertEqual(p["project_folders"], [os.path.join(self.home, "Developer"), extra])
        self.assertEqual(p["auto_project_folders"], [os.path.join(self.home, "Developer")])
        self.assertIs(p["trash_helper"], False)
        with mock.patch.dict(os.environ, {core.TRASH_HELPER_ENV: "/x/helper"}):
            self.assertIs(rep.scan_payload(self.res, cfg, "id1", 1.0, ZERO)["trash_helper"], True)

    def test_measure_extras(self):
        ex = rep.measure_extras(cfg_for(self.home))
        self.assertEqual(set(ex), set(ZERO))
        self.assertEqual(ex["android_sdk"], 0)
        self.assertEqual(ex["sim_devices"], 0)
        self.assertGreater(ex["gradle_modules"], 0)


if __name__ == "__main__":
    unittest.main()
