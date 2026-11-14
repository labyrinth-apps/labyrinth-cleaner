"""Application bundle discovery and metadata (never the real /Applications or mdls)."""

import os
import plistlib
import shutil
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import devclean_apps as apps  # noqa: E402
import devclean_core as core  # noqa: E402
import devclean_general as general  # noqa: E402


def make_app(parent, name, bundle_id="com.example.app", fmt=plistlib.FMT_XML, **extra):
    app = os.path.join(parent, name)
    os.makedirs(os.path.join(app, "Contents"), exist_ok=True)
    if bundle_id is not None:
        extra["CFBundleIdentifier"] = bundle_id
    with open(os.path.join(app, "Contents", "Info.plist"), "wb") as f:
        plistlib.dump(extra, f, fmt=fmt)
    return app


class AppsTestBase(unittest.TestCase):
    def setUp(self):
        self.home = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        self.cfg = core.Config(home=self.home)
        self.R = os.path.join(self.home, ".fixture-root", "Applications")
        self.H = os.path.join(self.home, "Applications")
        os.makedirs(self.R)
        os.makedirs(self.H)

    def find(self, **env):
        return apps.find_apps(self.cfg, core.Env.fake(**env))


class FindApps(AppsTestBase):
    def test_finds_apps_in_both_roots_and_one_subfolder(self):
        os.makedirs(os.path.join(self.R, "Vendor"))
        os.makedirs(os.path.join(self.R, "x", "y"))
        make_app(self.R, "A.app", "com.a")
        make_app(os.path.join(self.R, "Vendor"), "B.app", "com.b")
        make_app(self.H, "C.app", "com.c")
        make_app(os.path.join(self.R, "x", "y"), "D.app", "com.d")
        found = sorted(a.name for a in self.find())
        self.assertEqual(found, ["A", "B", "C"])

    def test_skips_symlinks_nested_bundles_and_bad_plists(self):
        real = make_app(self.home, "Real.app", "com.real")
        os.symlink(real, os.path.join(self.R, "Link.app"))
        vendor = os.path.join(self.home, "VendorReal")
        os.makedirs(vendor)
        make_app(vendor, "InFolder.app", "com.infolder")
        os.symlink(vendor, os.path.join(self.R, "VendorLink"))
        a = make_app(self.R, "A.app", "com.a")
        make_app(os.path.join(a, "Contents", "Helpers"), "H.app", "com.a.helper")
        make_app(self.R, "NoId.app", None, fmt=plistlib.FMT_BINARY)
        bad = make_app(self.R, "Bad.app", "com.bad")
        with open(os.path.join(bad, "Contents", "Info.plist"), "wb") as f:
            f.write(b"\x00\x01 not a plist")
        os.makedirs(os.path.join(self.R, "NoPlist.app", "Contents"))
        make_app(self.R, "Safari.app", "com.apple.Safari")
        make_app(self.R, "Labyrinth Cleaner.app", "com.labyrinth-apps.cleaner")
        make_app(self.R, "Bin.app", "com.bin", fmt=plistlib.FMT_BINARY)
        found = sorted(x.name for x in self.find())
        self.assertEqual(found, ["A", "Bin"])

    def test_names_and_versions(self):
        make_app(self.R, "Figma (v88.1).app", "com.figma",
                 CFBundleShortVersionString="88.1", CFBundleVersion="881")
        (info,) = self.find()
        self.assertEqual(info.name, "Figma (v88.1)")
        self.assertEqual(info.bundle_id, "com.figma")
        self.assertEqual(info.version, "88.1")
        self.assertEqual(info.build, "881")
        self.assertEqual(info.path, os.path.join(self.R, "Figma (v88.1).app"))
        st = os.lstat(info.path)
        self.assertEqual((info.uid, info.ino), (st.st_uid, st.st_ino))

    def test_metadata_from_env_and_du_fallback(self):
        make_app(self.R, "A.app", "com.a")
        make_app(self.R, "B.app", "com.b")
        calls = []

        def meta(paths):
            calls.append(list(paths))
            return {paths[0]: {"last_used": 1.0, "size": 5, "archs": ["x86_64"]}}

        with mock.patch.object(core, "du_bytes", return_value=42):
            out = self.find(app_metadata=meta)
        self.assertEqual(len(calls), 1)
        self.assertEqual(sorted(calls[0]), sorted(a.path for a in out))
        first = next(a for a in out if a.path == calls[0][0])
        second = next(a for a in out if a is not first)
        self.assertEqual((first.last_used, first.size, first.archs), (1.0, 5, ["x86_64"]))
        self.assertEqual((second.last_used, second.size, second.archs), (None, 42, None))


MDLS = '''kMDItemExecutableArchitectures = (
    "arm64",
    "x86_64"
)
kMDItemLastUsedDate            = 2026-11-01 10:00:00 +0000
kMDItemPhysicalSize            = 1234
kMDItemExecutableArchitectures = (
    "x86_64"
)
kMDItemLastUsedDate            = (null)
kMDItemPhysicalSize            = 99
kMDItemExecutableArchitectures = (null)
kMDItemLastUsedDate            = (null)
kMDItemPhysicalSize            = (null)
'''


class Metadata(unittest.TestCase):
    def test_parse_mdls(self):
        paths = ["/a/A.app", "/a/B.app", "/a/C.app"]
        out = apps.parse_mdls(MDLS, paths)
        self.assertEqual(out["/a/A.app"], {"last_used": 1793527200.0, "size": 1234,
                                           "archs": ["arm64", "x86_64"]})
        self.assertEqual(out["/a/B.app"], {"last_used": None, "size": 99, "archs": ["x86_64"]})
        self.assertEqual(out["/a/C.app"], {"last_used": None, "size": None, "archs": None})
        self.assertEqual(apps.parse_mdls("garbage\nnothing here", paths), {})
        self.assertEqual(apps.parse_mdls("", paths), {})

    def test_mdls_metadata_runs_once_and_fails_to_empty(self):
        ok = mock.Mock(returncode=0, stdout=MDLS)
        with mock.patch("subprocess.run", return_value=ok) as run:
            out = apps.mdls_metadata(["/a/A.app", "/a/B.app", "/a/C.app"])
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args[0][0][0], "mdls")
        self.assertEqual(out["/a/B.app"]["size"], 99)
        with mock.patch("subprocess.run", side_effect=OSError("no mdls")):
            self.assertEqual(apps.mdls_metadata(["/a/A.app"]), {})
        bad = mock.Mock(returncode=1, stdout="")
        with mock.patch("subprocess.run", return_value=bad):
            self.assertEqual(apps.mdls_metadata(["/a/A.app"]), {})
        self.assertEqual(apps.mdls_metadata([]), {})

    def test_apple_silicon_wrapper(self):
        with mock.patch("subprocess.run", return_value=mock.Mock(returncode=0, stdout="1\n")):
            self.assertTrue(core._real_apple_silicon())
        with mock.patch("subprocess.run", return_value=mock.Mock(returncode=0, stdout="0\n")):
            self.assertFalse(core._real_apple_silicon())
        with mock.patch("subprocess.run", side_effect=OSError):
            self.assertFalse(core._real_apple_silicon())


class RunningPaths(unittest.TestCase):
    def test_parse_running_bundle_paths(self):
        text = '''1) "Dia" ASN:0x0-1:
    bundleID="company.thebrowser.dia"
    executable path="/Applications/Dia.app/Contents/Frameworks/X.framework/Helpers/Helper.app/Contents/MacOS/x"
2) "Figma" ASN:0x0-2:
    bundleID="com.figma"
    executable path="/Applications/Figma (v88.1).app/Contents/MacOS/Figma"
3) "x" ASN:0x0-3:
    executable path="/usr/libexec/x"
'''
        self.assertEqual(general.parse_running_bundle_paths(text),
                         {"/Applications/Dia.app", "/Applications/Figma (v88.1).app"})

    def test_running_bundle_paths_raises_on_failure(self):
        with mock.patch("subprocess.run", return_value=mock.Mock(returncode=1, stdout="")):
            with self.assertRaises(OSError):
                general.running_bundle_paths()


class FixtureConfig(unittest.TestCase):
    def test_fixture_config_never_points_at_system_applications(self):
        tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        for root in core.Config(home=tmp).app_roots:
            self.assertTrue(root.startswith(tmp + os.sep), root)

    def test_real_home_includes_system_applications(self):
        cfg = core.Config(home="~")
        self.assertEqual(cfg.app_roots, ("/Applications", cfg.p("Applications")))


NOW = 1_800_000_000.0
DAY = 86400


def raising():
    raise OSError("no lsappinfo")


class ScanApps(AppsTestBase):
    def run_scan(self, meta=None, warnings=None, **env):
        env.setdefault("app_metadata", lambda paths: meta or {})
        return apps.scan_apps(self.cfg, core.Env.fake(**env), NOW, warnings)

    def by_name(self, items):
        return {i.label: i for i in items}

    def used(self, days):
        return NOW - days * DAY

    def test_figma_case(self):
        a = make_app(self.R, "Figma (v88.1).app", "com.figma", CFBundleShortVersionString="88.1")
        b = make_app(self.R, "Figma (v101.6).app", "com.figma", CFBundleShortVersionString="101.6")
        c = make_app(self.R, "Figma.app", "com.figma", CFBundleShortVersionString="101.6")
        meta = {p: {"last_used": self.used(1), "size": 10, "archs": None} for p in (a, b, c)}
        items, total = self.run_scan(meta)
        by = self.by_name(items)
        keeper = by["Figma"]
        self.assertEqual((keeper.tier, keeper.kind, keeper.note),
                         (core.PROTECTED, "info", "Keep: newest version"))
        self.assertEqual(by["Figma (v88.1)"].note, "older copy \u00b7 keep Figma 101.6")
        self.assertEqual(by["Figma (v101.6)"].note,
                         "second copy of Figma \u00b7 keep ~/.fixture-root/Applications/Figma.app")
        for n in ("Figma (v88.1)", "Figma (v101.6)"):
            self.assertEqual((by[n].tier, by[n].preselect, by[n].kind),
                             (core.REVIEW, False, "trash"))
            self.assertEqual(by[n].key, "app:" + by[n].path)
            self.assertEqual(by[n].category, "Applications")
        self.assertEqual(set(keeper.meta), {"bundle_id", "version", "uid", "ino"})

    def test_running_copy_is_kept(self):
        old = make_app(self.R, "Old.app", "com.x", CFBundleShortVersionString="1.0")
        new = make_app(self.R, "New.app", "com.x", CFBundleShortVersionString="2.0")
        meta = {p: {"last_used": self.used(1), "size": 1, "archs": None} for p in (old, new)}
        items, _ = self.run_scan(meta, running_app_paths=lambda: {old})
        by = self.by_name(items)
        self.assertEqual(by["Old"].note, "Keep: running now")
        self.assertEqual(by["Old"].tier, core.PROTECTED)
        self.assertEqual(by["New"].note, "copy of Old \u00b7 keep the running one")
        self.assertEqual(by["New"].tier, core.REVIEW)

    def test_unused_threshold_and_null_last_used(self):
        a = make_app(self.R, "Old181.app", "com.a")
        b = make_app(self.R, "Recent179.app", "com.b")
        c = make_app(self.R, "NullRecent.app", "com.c")
        d = make_app(self.R, "NullOld.app", "com.d")
        os.utime(d, (NOW - 400 * DAY, NOW - 400 * DAY))
        os.utime(c, (NOW - 10 * DAY, NOW - 10 * DAY))
        meta = {a: {"last_used": self.used(181), "size": 1, "archs": None},
                b: {"last_used": self.used(179), "size": 1, "archs": None}}
        items, _ = self.run_scan(meta)
        by = self.by_name(items)
        self.assertEqual(set(by), {"Old181", "NullOld"})
        stamp = core.datetime.fromtimestamp(self.used(181)).year
        self.assertRegex(by["Old181"].note, r"^last opened [A-Z][a-z]{2} %d$" % stamp)
        self.assertEqual(by["NullOld"].note, "no record of being opened")
        self.assertEqual(by["Old181"].tier, core.REVIEW)

    def test_intel_note_only_on_apple_silicon_and_listed_rows(self):
        old = make_app(self.R, "OldIntel.app", "com.a")
        new = make_app(self.R, "NewIntel.app", "com.b")
        meta = {old: {"last_used": self.used(300), "size": 1, "archs": ["x86_64"]},
                new: {"last_used": self.used(2), "size": 1, "archs": ["x86_64"]}}
        items, _ = self.run_scan(meta, apple_silicon=lambda: True)
        by = self.by_name(items)
        self.assertEqual(set(by), {"OldIntel"})
        self.assertTrue(by["OldIntel"].note.endswith(" \u00b7 Intel only, stops working in macOS 28"))
        items, _ = self.run_scan(meta, apple_silicon=lambda: False)
        self.assertNotIn("Intel", self.by_name(items)["OldIntel"].note)

    def test_running_apps_unreadable_offers_nothing(self):
        a = make_app(self.R, "A.app", "com.a")
        meta = {a: {"last_used": self.used(300), "size": 7, "archs": None}}
        warnings = []
        items, total = self.run_scan(meta, warnings, running_app_paths=raising)
        self.assertEqual([i for i in items if i.tier == core.REVIEW], [])
        self.assertIn("couldn't list running apps; applications are not offered", warnings)

    def test_running_unused_app_says_quit_first(self):
        a = make_app(self.R, "Busy.app", "com.a")
        meta = {a: {"last_used": self.used(300), "size": 1, "archs": None}}
        items, _ = self.run_scan(meta, running_app_paths=lambda: {a})
        (it,) = items
        self.assertEqual((it.note, it.tier, it.kind), ("Quit Busy first", core.PROTECTED, "info"))

    def test_kind_by_owner(self):
        a = make_app(self.R, "A.app", "com.a")
        meta = {a: {"last_used": self.used(300), "size": 1, "archs": None}}
        (it,) = self.run_scan(meta)[0]
        self.assertEqual(it.kind, "trash")
        (it,) = self.run_scan(meta, user_uid=lambda: os.getuid() + 1)[0]
        self.assertEqual((it.kind, it.tier), ("finder-trash", core.REVIEW))

    def test_keeper_never_offered_even_if_old(self):
        a = make_app(self.R, "A.app", "com.a", CFBundleShortVersionString="2")
        b = make_app(self.R, "A (1).app", "com.a", CFBundleShortVersionString="1")
        meta = {p: {"last_used": self.used(900), "size": 1, "archs": ["x86_64"]} for p in (a, b)}
        items, _ = self.run_scan(meta, apple_silicon=lambda: True)
        by = self.by_name(items)
        self.assertEqual((by["A"].tier, by["A"].kind, by["A"].note),
                         (core.PROTECTED, "info", "Keep: newest version"))
        self.assertEqual(by["A (1)"].tier, core.REVIEW)

    def test_keeper_rules_order(self):
        def mk(name, path, **kw):
            d = dict(bundle_id="x", version="1", build="1", uid=0, ino=1, mtime=0.0,
                     last_used=1.0, size=1, archs=None)
            d.update(kw)
            return apps.AppInfo(path=path, name=name, **d)
        root = "/Applications"
        self.assertEqual(apps.pick_keeper([mk("A", root + "/A.app"),
                                           mk("B", "/x/B.app", build="2")], set(), root).name, "B")
        self.assertEqual(apps.pick_keeper([mk("A (1)", root + "/A (1).app"),
                                           mk("A", "/h/A.app")], set(), root).name, "A")
        self.assertEqual(apps.pick_keeper([mk("A", "/h/A.app"), mk("A", root + "/A.app")],
                                          set(), root).path, root + "/A.app")
        self.assertEqual(apps.pick_keeper([mk("A", "/h/A.app", last_used=1.0),
                                           mk("B", "/h/B.app", last_used=5.0)], set(), root).name, "B")
        self.assertEqual(apps.pick_keeper([mk("A", "/h/A.app", version="9"),
                                           mk("B", "/h/B.app", version="1")],
                                          {"/h/B.app"}, root).name, "B")

    def test_app_bytes_counts_every_grouped_app(self):
        a = make_app(self.R, "A.app", "com.a", CFBundleShortVersionString="2")
        b = make_app(self.R, "B.app", "com.a", CFBundleShortVersionString="1")
        c = make_app(self.R, "C.app", "com.c")
        meta = {a: {"last_used": self.used(1), "size": 100, "archs": None},
                b: {"last_used": self.used(1), "size": 20, "archs": None},
                c: {"last_used": self.used(1), "size": 3, "archs": None}}
        _, total = self.run_scan(meta)
        self.assertEqual(total, 123)

    def scan_pair(self, **env):
        a = make_app(self.R, "A.app", "com.a", CFBundleShortVersionString="2")
        b = make_app(self.R, "B.app", "com.a", CFBundleShortVersionString="1")
        meta = {a: {"last_used": self.used(1), "size": 100, "archs": None},
                b: {"last_used": self.used(1), "size": 20, "archs": None}}
        e = core.Env.fake(app_metadata=lambda paths: meta, **env)
        return e, core.scan(self.cfg, env=e, now=NOW)

    def test_scan_includes_apps_and_sets_app_bytes(self):
        _, res = self.scan_pair()
        mine = [i for i in res.items if i.category == "Applications"]
        self.assertEqual(sorted(i.label for i in mine), ["A", "B"])
        self.assertEqual(res.app_bytes, 120)
        offered = next(i for i in mine if i.label == "B")
        self.assertEqual(offered.size, 20)
        self.assertEqual(offered.cost_tag, "TRASH")
        self.assertEqual(offered.deletes, "~/.fixture-root/Applications/B.app")
        self.assertEqual(offered.restore, core.DOWNLOAD_RESTORE)

    def test_finder_trash_explains_like_trash(self):
        _, res = self.scan_pair(user_uid=lambda: os.getuid() + 1)
        offered = next(i for i in res.items if i.label == "B")
        self.assertEqual((offered.kind, offered.cost_tag), ("finder-trash", "TRASH"))
        self.assertEqual(offered.deletes, "~/.fixture-root/Applications/B.app")
        self.assertEqual(offered.restore, core.DOWNLOAD_RESTORE)

    def test_rescan_finds_app_by_key(self):
        env, res = self.scan_pair()
        it = next(i for i in res.items if i.label == "B")
        fresh = core.rescan_item(it, self.cfg, env=env, now=NOW)
        self.assertEqual(fresh.key, it.key)


if __name__ == "__main__":
    unittest.main()
