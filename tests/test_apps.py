"""Application bundle discovery and metadata (never the real /Applications or mdls)."""

import dataclasses
import os
import plistlib
import re
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
        # Figma and Figma (v101.6) tie on version: the keeper isn't strictly the newest.
        self.assertEqual((keeper.tier, keeper.kind, keeper.note),
                         (core.PROTECTED, "info", "Keep: suggested copy"))
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
                b: {"last_used": self.used(179), "size": 1, "archs": None},
                c: {"last_used": None, "size": 1, "archs": None},
                d: {"last_used": None, "size": 1, "archs": None}}
        items, _ = self.run_scan(meta)
        by = self.by_name(items)
        self.assertEqual(set(by), {"Old181", "NullOld"})
        stamp = core.datetime.fromtimestamp(self.used(181)).year
        self.assertRegex(by["Old181"].note, r"^last opened [A-Z][a-z]{2} %d$" % stamp)
        self.assertEqual(by["NullOld"].note, "no record of being opened")
        self.assertEqual(by["Old181"].tier, core.REVIEW)

    def test_background_and_menu_bar_apps_without_a_date_are_not_listed_for_age(self):
        # Started at login or by another app, they're used without ever being "opened",
        # so a missing date says nothing. A date they do have still counts.
        old = NOW - 400 * DAY
        handler = make_app(self.R, "URL Handler.app", "com.h", LSBackgroundOnly=True)
        menubar = make_app(self.R, "Ice.app", "com.i", LSUIElement=True)
        as_text = make_app(self.R, "Bar.app", "com.t", LSUIElement="1")
        dated = make_app(self.R, "OldBar.app", "com.d", LSUIElement=True)
        for p in (handler, menubar, as_text, dated):
            os.utime(p, (old, old))
        meta = {p: {"last_used": None, "size": 1, "archs": None} for p in (handler, menubar, as_text)}
        meta[dated] = {"last_used": self.used(300), "size": 1, "archs": None}
        items, _ = self.run_scan(meta)
        self.assertEqual(set(self.by_name(items)), {"OldBar"})

    def test_background_app_copies_are_still_listed(self):
        old = make_app(self.R, "Helper.app", "com.h", LSBackgroundOnly=True, CFBundleShortVersionString="1.0")
        new = make_app(self.H, "Helper.app", "com.h", LSBackgroundOnly=True, CFBundleShortVersionString="2.0")
        meta = {p: {"last_used": None, "size": 1, "archs": None} for p in (old, new)}
        items, _ = self.run_scan(meta)
        offered = [i for i in items if i.tier == core.REVIEW]
        self.assertEqual([i.path for i in offered], [old])

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

    def test_missing_spotlight_data_never_lists_for_age(self):
        a = make_app(self.R, "OldBundle.app", "com.a")
        os.utime(a, (NOW - 900 * DAY, NOW - 900 * DAY))
        self.assertEqual(self.run_scan({})[0], [])

        def boom(paths):
            raise OSError("mdls exploded")
        items, total = self.run_scan(app_metadata=boom)
        self.assertEqual(items, [])

    def test_running_copy_with_higher_version_keeps_running_note(self):
        old = make_app(self.R, "Old.app", "com.x", CFBundleShortVersionString="1.0")
        new = make_app(self.R, "New.app", "com.x", CFBundleShortVersionString="2.0")
        meta = {p: {"last_used": self.used(1), "size": 1, "archs": None} for p in (old, new)}
        by = self.by_name(self.run_scan(meta, running_app_paths=lambda: {new})[0])
        self.assertEqual(by["New"].note, "Keep: running now")
        self.assertEqual(by["Old"].note, "copy of New \u00b7 keep the running one")

    def test_scan_does_not_resize_app_rows(self):
        a = make_app(self.R, "A.app", "com.a", CFBundleShortVersionString="2")
        b = make_app(self.R, "B.app", "com.a", CFBundleShortVersionString="1")
        meta = {p: {"last_used": self.used(1), "size": None, "archs": None} for p in (a, b)}
        env = core.Env.fake(app_metadata=lambda paths: meta)
        with mock.patch.object(core, "du_bytes", return_value=7) as du:
            res = core.scan(self.cfg, env=env, now=NOW)
        mine = [i for i in res.items if i.category == "Applications"]
        self.assertEqual([i.size for i in mine], [7, 7])
        self.assertEqual(res.app_bytes, 14)
        self.assertEqual([c for c in du.call_args_list if "Applications" in str(c)].__len__(), 2)

    def test_nfc_nfd_mismatch_still_running(self):
        import unicodedata
        old = make_app(self.R, "Caf\u00e9.app", "com.x", CFBundleShortVersionString="1")
        new = make_app(self.R, "Other.app", "com.x", CFBundleShortVersionString="2")
        meta = {p: {"last_used": self.used(1), "size": 1, "archs": None} for p in (old, new)}
        nfd = unicodedata.normalize("NFD", old)
        self.assertNotEqual(nfd, old)
        by = self.by_name(self.run_scan(meta, running_app_paths=lambda: {nfd})[0])
        self.assertEqual(by["Caf\u00e9"].note, "Keep: running now")

    def test_running_bundle_from_unmatched_path_offers_no_copy(self):
        old = make_app(self.R, "Old.app", "com.x", CFBundleShortVersionString="1")
        new = make_app(self.R, "New.app", "com.x", CFBundleShortVersionString="2")
        meta = {p: {"last_used": self.used(900), "size": 1, "archs": None} for p in (old, new)}
        items, _ = self.run_scan(meta, running_apps=lambda: [("com.x", "X")],
                                 running_app_paths=lambda: {"/private/var/AppTranslocation/X.app"})
        self.assertEqual([i for i in items if i.tier == core.REVIEW], [])
        warnings = []
        items, _ = self.run_scan(meta, warnings, running_apps=raising)
        self.assertEqual(items, [])
        self.assertIn("couldn't list running apps; applications are not offered", warnings)

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

    def test_app_version_key(self):
        k = apps.app_version_key
        self.assertEqual(k("1"), k("1.0"))
        self.assertEqual(k("2.0.0"), k("2"))
        self.assertLess(k("1.0b2"), k("1.0"))
        self.assertLess(k("2.0-beta.1"), k("2.0"))
        self.assertLess(k("3.1 RC 2"), k("3.1"))
        self.assertLess(k("3.1 beta 5"), k("3.1 rc 2"))
        self.assertLess(k("1.0a1"), k("1.0b1"))
        self.assertLess(k("1.0 alpha 3"), k("1.0 Beta 1"))
        self.assertLess(k("1.0dev"), k("1.0a1"))
        self.assertLess(k("1.0b2"), k("1.0b10"))
        self.assertGreater(k("1.0b2"), k("0.9"))
        self.assertGreater(k("101.6"), k("88.1"))
        self.assertGreater(k("1.0.1"), k("1.0"))
        self.assertEqual(k(None), k(""))
        self.assertLess(k(None), k("0.1"))

    def test_equal_versions_are_a_second_copy(self):
        a = make_app(self.R, "A.app", "com.x", CFBundleShortVersionString="1")
        b = make_app(self.R, "B.app", "com.x", CFBundleShortVersionString="1.0")
        meta = {a: {"last_used": self.used(1), "size": 1, "archs": None},
                b: {"last_used": self.used(2), "size": 1, "archs": None}}
        by = self.by_name(self.run_scan(meta)[0])
        self.assertEqual(by["A"].note, "Keep: suggested copy")
        self.assertEqual(by["B"].note,
                         "second copy of A \u00b7 keep ~/.fixture-root/Applications/A.app")

    def test_beta_is_older_than_release(self):
        beta = make_app(self.R, "Beta.app", "com.x", CFBundleShortVersionString="1.0b2")
        rel = make_app(self.R, "Rel.app", "com.x", CFBundleShortVersionString="1.0")
        meta = {p: {"last_used": self.used(1), "size": 1, "archs": None} for p in (beta, rel)}
        by = self.by_name(self.run_scan(meta)[0])
        self.assertEqual((by["Rel"].tier, by["Rel"].note), (core.PROTECTED, "Keep: newest version"))
        self.assertEqual(by["Beta"].note, "older copy \u00b7 keep Rel 1.0")
        self.assertEqual(by["Beta"].tier, core.REVIEW)

    def test_rescan_never_runs_du(self):
        a = make_app(self.R, "A.app", "com.a", CFBundleShortVersionString="2")
        b = make_app(self.R, "B.app", "com.a", CFBundleShortVersionString="1")
        meta = {p: {"last_used": self.used(1), "size": None, "archs": None} for p in (a, b)}
        env = core.Env.fake(app_metadata=lambda paths: meta)
        with mock.patch.object(core, "du_bytes", return_value=7):
            items, _ = apps.scan_apps(self.cfg, env, NOW)
        offered = next(i for i in items if i.label == "B")
        self.assertEqual(offered.size, 7)
        with mock.patch.object(core, "du_bytes", side_effect=AssertionError("du ran")) as du:
            items, _ = apps.scan_apps(self.cfg, env, NOW, sizes=False)
            self.assertEqual(sorted(i.label for i in items), ["A", "B"])
            fresh = core.RESCANNERS["app:"](offered.key, self.cfg, None, env, NOW)
            self.assertIn(offered.key, [i.key for i in fresh])
            core.validate_item(offered, self.cfg, env=env, now=NOW)
        du.assert_not_called()

    # The Xcode holding the active developer directory (xcode-select -p).
    def xcodes(self, **env):
        x = make_app(self.R, "Xcode.app", "com.apple.dt.Xcode", CFBundleShortVersionString="16.0")
        beta = make_app(self.R, "Xcode-beta.app", "com.apple.dt.Xcode",
                        CFBundleShortVersionString="26.0")
        meta = {p: {"last_used": self.used(1), "size": 1, "archs": None} for p in (x, beta)}
        return self.by_name(self.run_scan(meta, **env)[0])

    @property
    def X(self):
        return os.path.join(self.R, "Xcode.app")

    @property
    def BETA(self):
        return os.path.join(self.R, "Xcode-beta.app")

    def test_developer_dir_xcode_is_the_keeper(self):
        by = self.xcodes(active_developer_dir=lambda: self.X + "/Contents/Developer")
        self.assertEqual((by["Xcode"].tier, by["Xcode"].kind, by["Xcode"].note),
                         (core.PROTECTED, "info", "Keep: used for command-line builds"))
        self.assertEqual(by["Xcode-beta"].tier, core.REVIEW)
        self.assertEqual(by["Xcode-beta"].note,
                         "copy of Xcode \u00b7 keep the one used for command-line builds")
        # Without it, the newer beta is kept.
        by = self.xcodes()
        self.assertEqual(by["Xcode-beta"].note, "Keep: newest version")
        self.assertEqual(by["Xcode"].tier, core.REVIEW)

    def test_developer_dir_match_is_by_bundle_prefix(self):
        by = self.xcodes(active_developer_dir=lambda: self.X + "-old/Contents/Developer")
        self.assertEqual(by["Xcode-beta"].note, "Keep: newest version")
        by = self.xcodes(active_developer_dir=lambda: self.X + "/")
        self.assertEqual(by["Xcode"].note, "Keep: used for command-line builds")

    def test_developer_dir_nfd(self):
        import unicodedata
        a = make_app(self.R, "Caf\u00e9.app", "com.x", CFBundleShortVersionString="1")
        b = make_app(self.R, "Other.app", "com.x", CFBundleShortVersionString="2")
        meta = {p: {"last_used": self.used(1), "size": 1, "archs": None} for p in (a, b)}
        dev = unicodedata.normalize("NFD", a) + "/Contents/Developer"
        by = self.by_name(self.run_scan(meta, active_developer_dir=lambda: dev)[0])
        self.assertEqual(by["Caf\u00e9"].note, "Keep: used for command-line builds")

    def test_developer_dir_app_listed_for_age_is_never_offered(self):
        x = make_app(self.R, "Xcode.app", "com.apple.dt.Xcode")
        meta = {x: {"last_used": self.used(900), "size": 1, "archs": None}}
        (it,) = self.run_scan(meta, active_developer_dir=lambda: x + "/Contents/Developer")[0]
        self.assertEqual((it.tier, it.kind, it.note),
                         (core.PROTECTED, "info", "Keep: used for command-line builds"))
        self.assertFalse(it.offerable)

    def test_developer_dir_copy_is_never_offered_when_another_copy_runs(self):
        by = self.xcodes(active_developer_dir=lambda: self.X + "/Contents/Developer",
                         running_app_paths=lambda: {self.BETA})
        self.assertEqual(by["Xcode-beta"].note, "Keep: running now")
        self.assertEqual((by["Xcode"].tier, by["Xcode"].kind, by["Xcode"].note),
                         (core.PROTECTED, "info", "Keep: used for command-line builds"))

    def test_developer_dir_note_wins_over_running(self):
        by = self.xcodes(active_developer_dir=lambda: self.X + "/Contents/Developer",
                         running_app_paths=lambda: {self.X})
        self.assertEqual(by["Xcode"].note, "Keep: used for command-line builds")
        self.assertEqual(by["Xcode-beta"].note, "copy of Xcode \u00b7 keep the running one")

    def test_developer_dir_failure_is_none(self):
        by = self.xcodes(active_developer_dir=raising)
        self.assertEqual(by["Xcode-beta"].note, "Keep: newest version")

    def test_real_developer_dir_wrapper(self):
        self.assertIs(core.Env.real().active_developer_dir, core._real_active_developer_dir)
        self.assertIsNone(core.Env.fake().active_developer_dir())
        dev = os.path.join(self.home, "Xcode.app", "Contents", "Developer")   # temp home only
        os.makedirs(dev)
        ok = mock.Mock(returncode=0, stdout=dev + "\n")
        with mock.patch("subprocess.run", return_value=ok) as run:
            self.assertEqual(core._real_active_developer_dir(), dev)
        self.assertEqual(run.call_args[0][0], ["xcode-select", "-p"])
        self.assertIn("timeout", run.call_args[1])
        for result in (mock.Mock(returncode=2, stdout=dev), mock.Mock(returncode=0, stdout="\n"),
                       mock.Mock(returncode=0, stdout="relative/path")):
            with mock.patch("subprocess.run", return_value=result):
                self.assertIsNone(core._real_active_developer_dir())
        with mock.patch("subprocess.run", side_effect=OSError):
            self.assertIsNone(core._real_active_developer_dir())

    def test_symlinked_developer_dir_pins_the_real_bundle(self):
        # xcode-select -s /Applications/Xcode.app where Xcode.app links to Xcode-16.0.app:
        # the scanner lists only the real bundle, so the dev dir is resolved first.
        real = make_app(self.R, "Xcode-16.0.app", "com.apple.dt.Xcode",
                        CFBundleShortVersionString="16.0")
        beta = make_app(self.R, "Xcode-beta.app", "com.apple.dt.Xcode",
                        CFBundleShortVersionString="26.0")
        os.makedirs(os.path.join(real, "Contents", "Developer"))
        link = os.path.join(self.home, "Xcode.app")
        os.symlink(real, link)
        out = mock.Mock(returncode=0, stdout=link + "/Contents/Developer\n")
        meta = {p: {"last_used": self.used(1), "size": 1, "archs": None} for p in (real, beta)}
        with mock.patch("subprocess.run", return_value=out):
            by = self.by_name(self.run_scan(
                meta, active_developer_dir=core._real_active_developer_dir)[0])
        self.assertEqual(by["Xcode-16.0"].note, "Keep: used for command-line builds")
        self.assertEqual(by["Xcode-beta"].tier, core.REVIEW)

    def test_rescan_finds_app_by_key(self):
        env, res = self.scan_pair()
        it = next(i for i in res.items if i.label == "B")
        fresh = core.rescan_item(it, self.cfg, env=env, now=NOW)
        self.assertEqual(fresh.key, it.key)



OTHER_UID = os.getuid() + 1


def fake_lstat(uids):
    """os.lstat that reports the owner in `uids` (path -> uid) instead of the real one."""
    real = os.lstat

    def lstat(path, *a, **kw):
        st = real(path, *a, **kw)
        uid = uids.get(os.fspath(path)) if isinstance(path, (str, os.PathLike)) else None
        if uid is None:
            return st
        return os.stat_result(tuple(st)[:4] + (uid,) + tuple(st)[5:])
    return lstat


class FinderRunner:
    """Fake osascript: records argv; `delete` removes the given paths (no Trash involved)."""

    def __init__(self, rc=0, stderr="", delete=None):
        self.calls, self.rc, self.stderr, self.delete = [], rc, stderr, delete

    def __call__(self, argv, **kw):
        self.calls.append(list(argv))
        paths = argv[argv.index("--") + 1:] if "--" in argv else []
        for p in paths if self.delete is None else self.delete:
            shutil.rmtree(p)
        return core.subprocess.CompletedProcess(argv, self.rc, "", self.stderr)


class AppGate(AppsTestBase):
    """The gate and the two removal routes, on fixture homes only."""

    def setUp(self):
        super().setUp()
        self.base = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.meta = {}
        self.uid = os.getuid()
        self.running = set()
        self.trash = os.path.join(self.home, ".Trash")
        self.helper_log = os.path.join(self.base, "helper.log")
        self.helper = os.path.join(self.base, "fake-trash-helper")
        with open(self.helper, "w") as f:
            f.write("#!/bin/sh\n"
                    f'for a in "$@"; do printf "%s\\n" "$a" >> "{self.helper_log}"; done\n'
                    f'shift 2; mkdir -p "{self.trash}" && mv "$1" "{self.trash}/"\n')
        os.chmod(self.helper, 0o755)
        patcher = mock.patch.dict(os.environ, {core.TRASH_HELPER_ENV: self.helper})
        patcher.start()
        self.addCleanup(patcher.stop)

    def env(self):
        return core.Env.fake(app_metadata=lambda paths: {
            p: self.meta.get(p, {"last_used": NOW - 300 * DAY, "size": 10, "archs": None})
            for p in paths}, user_uid=lambda: self.uid,
            running_app_paths=lambda: set(self.running))

    def old_app(self, parent, name, bundle_id, version="1"):
        return make_app(parent, name, bundle_id, CFBundleShortVersionString=version)

    def item(self, path):
        items, _ = apps.scan_apps(self.cfg, self.env(), NOW)
        return next(i for i in items if i.key == "app:" + path)

    def validate(self, it):
        core.validate_item(it, self.cfg, env=self.env(), now=NOW)

    def assert_changed(self, it):
        with self.assertRaises(core.UnsafeTarget) as cm:
            self.validate(it)
        self.assertEqual(str(cm.exception), core.SITUATION_CHANGED)

    def test_gate_accepts_unchanged_app(self):
        a = self.old_app(self.R, "A.app", "com.a")
        self.validate(self.item(a))
        self.uid = OTHER_UID
        it = self.item(a)
        self.assertEqual(it.kind, "finder-trash")
        self.validate(it)

    def test_gate_refuses_changed_owner_inode_bundle_or_version(self):
        self.uid = OTHER_UID          # Finder items: an owner change keeps the kind
        a = self.old_app(self.R, "A.app", "com.a")
        it = self.item(a)
        with mock.patch.object(os, "lstat", fake_lstat({a: 0})):
            self.assert_changed(it)
        forged = core.Item(**{**it.__dict__, "meta": {**it.meta, "uid": 0}})
        self.assert_changed(forged)
        # Owner changed under a helper item: the kind would change, never the route.
        self.uid = os.getuid()
        mine = self.item(a)
        with mock.patch.object(os, "lstat", fake_lstat({a: 0})):
            self.assert_changed(mine)

        self.uid = OTHER_UID
        b = self.old_app(self.R, "B.app", "com.b")
        it = self.item(b)
        os.rename(b, os.path.join(self.base, "B-old.app"))
        self.old_app(self.R, "B.app", "com.b")
        self.assertNotEqual(os.lstat(b).st_ino, it.meta["ino"])
        self.assert_changed(it)

        c = self.old_app(self.R, "C.app", "com.c")
        it = self.item(c)
        self.old_app(self.R, "C.app", "com.c.other")
        self.assert_changed(it)

        d = self.old_app(self.R, "D.app", "com.d", version="1")
        it = self.item(d)
        self.old_app(self.R, "D.app", "com.d", version="2")
        self.assert_changed(it)

    def test_gate_refuses_symlink_and_too_deep(self):
        real = self.old_app(self.base, "Real.app", "com.real")
        link = os.path.join(self.R, "Link.app")
        os.symlink(real, link)
        vendor = os.path.join(self.base, "Vendor")
        os.makedirs(vendor)
        in_vendor = self.old_app(vendor, "V.app", "com.v")
        os.symlink(vendor, os.path.join(self.R, "VendorLink"))
        via_link = os.path.join(self.R, "VendorLink", "V.app")
        os.makedirs(os.path.join(self.R, "x", "y"))
        deep = self.old_app(os.path.join(self.R, "x", "y"), "D.app", "com.deep")
        outside = self.old_app(self.home, "Out.app", "com.out")
        not_app = os.path.join(self.R, "Folder")
        os.makedirs(not_app)
        outer = self.old_app(self.R, "Foo.app", "com.foo")
        nested = self.old_app(outer, "Bar.app", "com.bar")
        # Each case fails on the gate's own check, not on the rescan that follows it.
        cases = ((link, real, "is a symlink or resolves elsewhere"),
                 (via_link, in_vendor, "is a symlink or resolves elsewhere"),
                 (deep, deep, "too deep inside"),
                 (outside, outside, "not in an Applications folder"),
                 (not_app, not_app, "not an .app bundle"),
                 (nested, nested, "inside another application"))
        for path, st_path, reason in cases:
            st = os.lstat(st_path)
            for kind in ("trash", "finder-trash"):
                it = core.Item(key="app:" + path, category="Applications", label="x",
                               tier=core.REVIEW, kind=kind, path=path,
                               meta={"bundle_id": "com.x", "version": "1",
                                     "uid": st.st_uid, "ino": st.st_ino})
                with self.subTest(path=path, kind=kind):
                    with self.assertRaisesRegex(core.UnsafeTarget, re.escape(reason)):
                        core.validate_trash(it, self.cfg, self.env(), NOW)
                    # Through validate_item the rescan refuses first: never listed.
                    with self.assertRaisesRegex(core.UnsafeTarget,
                                                "^" + re.escape(core.SITUATION_CHANGED) + "$"):
                        self.validate(it)
        self.assertTrue(all(os.path.isdir(p) for p in (real, in_vendor, deep, outside, nested)))

    # The anti-goal "a running app or the keeper is offered", checked again at delete time.
    def test_gate_refuses_copy_that_started_running(self):
        a = self.old_app(self.R, "A.app", "com.a")
        it = self.item(a)
        self.assertEqual(it.tier, core.REVIEW)
        self.running = {a}
        with self.assertRaisesRegex(core.UnsafeTarget, "^A: Quit A first$"):
            self.validate(it)
        res = core.execute_item(it, self.cfg, runner=core.subprocess.run, env=self.env(), now=NOW)
        self.assertEqual(res, (False, "REFUSED: A: Quit A first"))
        self.assertFalse(os.path.exists(self.helper_log))
        # The Finder route too: nothing reaches osascript.
        self.running = set()
        self.uid = OTHER_UID
        it = self.item(a)
        self.assertEqual(it.kind, "finder-trash")
        self.running = {a}
        run = FinderRunner()
        res = core.execute_finder_batch([it], self.cfg, runner=run, env=self.env(), now=NOW)
        self.assertEqual(res, [(False, "REFUSED: A: Quit A first")])
        self.assertEqual(run.calls, [])
        self.assertTrue(os.path.isdir(a))

    def test_gate_refuses_copy_that_became_the_keeper(self):
        for p in ("A.app", "B.app", "C.app"):
            self.meta[os.path.join(self.R, p)] = {"last_used": NOW - DAY, "size": 1, "archs": None}
        a = self.old_app(self.R, "A.app", "com.x", version="3")
        b = self.old_app(self.R, "B.app", "com.x", version="2")
        self.old_app(self.R, "C.app", "com.x", version="1")
        it = self.item(b)
        self.assertEqual((it.tier, it.note), (core.REVIEW, "older copy \u00b7 keep A 3"))
        os.rename(a, os.path.join(self.base, "A.app"))      # the old keeper is removed
        with self.assertRaisesRegex(core.UnsafeTarget, "^B: Keep: newest version$"):
            self.validate(it)
        run = FinderRunner()
        self.uid = OTHER_UID
        finder_item = core.Item(**{**it.__dict__, "kind": "finder-trash"})
        res = core.execute_finder_batch([finder_item], self.cfg, runner=run, env=self.env(),
                                        now=NOW)
        self.assertFalse(res[0][0])
        self.assertEqual(run.calls, [])
        self.assertTrue(os.path.isdir(b))

    def test_gate_refuses_copy_whose_reason_changed(self):
        # Two old copies: B is offered as the older copy. Once A is gone, B is offered only
        # for its age, which the user never saw: scan again.
        a = self.old_app(self.R, "A.app", "com.x", version="2")
        b = self.old_app(self.R, "B.app", "com.x", version="1")
        it = self.item(b)
        self.assertTrue(it.note.startswith("older copy"))
        os.rename(a, os.path.join(self.base, "A.app"))
        self.assertTrue(self.item(b).offerable)
        self.assert_changed(it)

    def test_gate_refuses_developer_dir_app(self):
        a = self.old_app(self.R, "Xcode.app", "com.apple.dt.Xcode")
        it = self.item(a)
        env = dataclasses.replace(self.env(), active_developer_dir=lambda: a + "/Contents/Developer")
        with self.assertRaisesRegex(core.UnsafeTarget, "command-line builds"):
            core.validate_item(it, self.cfg, env=env, now=NOW)

    def test_unreadable_running_apps_is_situation_changed_not_gone(self):
        a = self.old_app(self.R, "A.app", "com.a")
        it = self.item(a)
        env = dataclasses.replace(self.env(), running_app_paths=raising)
        with self.assertRaises(core.UnsafeTarget) as cm:
            core.validate_item(it, self.cfg, env=env, now=NOW)
        self.assertEqual(str(cm.exception), core.SITUATION_CHANGED)
        with self.assertRaises(core.UnsafeTarget) as cm:
            core.validate_trash(it, self.cfg, env, NOW)
        self.assertEqual(str(cm.exception), core.SITUATION_CHANGED)
        # Really gone: still says so.
        os.rename(a, os.path.join(self.base, "A.app"))
        with self.assertRaisesRegex(core.UnsafeTarget, "is gone$"):
            self.validate(it)

    def test_fixture_mode_never_moves_apps_outside_the_fixture_home(self):
        outside = os.path.join(self.base, "Applications")
        os.makedirs(outside)
        cfg = core.Config(home=self.home, app_roots=(outside, self.H))
        a = self.old_app(outside, "Real.app", "com.real")
        items, _ = apps.scan_apps(cfg, self.env(), NOW)
        it = next(i for i in items if i.key == "app:" + a)
        self.assertEqual(it.kind, "trash")
        core.validate_item(it, cfg, env=self.env(), now=NOW)   # otherwise fine
        refusal = "REFUSED: fixture mode never moves apps outside the fixture home"
        self.assertEqual(core.execute_item(it, cfg, runner=core.fixture_runner, env=self.env(),
                                           now=NOW), (False, refusal))
        finder = core.Item(**{**it.__dict__, "kind": "finder-trash"})
        self.assertEqual(core.execute_item(finder, cfg, runner=core.fixture_runner,
                                           env=self.env(), now=NOW), (False, refusal))
        self.assertEqual(core.execute_finder_batch([finder], cfg, runner=core.fixture_runner,
                                                   env=self.env(), now=NOW), [(False, refusal)])
        self.assertFalse(os.path.exists(self.helper_log))
        self.assertTrue(os.path.isdir(a))
        # Inside the fixture home the helper still runs in fixture mode.
        mine = self.old_app(self.H, "Mine.app", "com.mine")
        items, _ = apps.scan_apps(cfg, self.env(), NOW)
        it = next(i for i in items if i.key == "app:" + mine)
        self.assertEqual(core.execute_item(it, cfg, runner=core.fixture_runner, env=self.env(),
                                           now=NOW), (True, "ok"))

    def test_fixture_mode_never_moves_anything_from_the_real_home(self):
        # `--simctl-fixture` without `--home`: fixture runner, real home. Pretend this
        # temp home is the real one; nothing may reach the helper or Finder.
        mine = self.old_app(self.H, "Mine.app", "com.mine")
        items, _ = apps.scan_apps(self.cfg, self.env(), NOW)
        it = next(i for i in items if i.key == "app:" + mine)
        dl = core.Item(key="download:x", category="Downloads", label="x", tier=core.REVIEW,
                       path=os.path.join(self.home, "Downloads", "x.dmg"), kind="trash")
        finder = core.Item(**{**it.__dict__, "kind": "finder-trash"})
        refusal = core.FIXTURE_REAL_HOME_REFUSAL
        with mock.patch.object(core, "is_real_home", return_value=True):
            for item in (it, dl, finder):
                self.assertEqual(core.execute_item(item, self.cfg, runner=core.fixture_runner,
                                                   env=self.env(), now=NOW), (False, refusal))
            self.assertEqual(core.execute_finder_batch([finder], self.cfg, runner=core.fixture_runner,
                                                       env=self.env(), now=NOW), [(False, refusal)])
        self.assertFalse(os.path.exists(self.helper_log))
        self.assertTrue(os.path.isdir(mine))

    def test_is_real_home(self):
        self.assertTrue(core.is_real_home("~"))
        self.assertFalse(core.is_real_home(self.home))

    def test_finder_trash_needs_app_key(self):
        a = self.old_app(self.R, "A.app", "com.a")
        it = self.item(a)
        forged = core.Item(**{**it.__dict__, "key": "download:A.app", "kind": "finder-trash"})
        with self.assertRaises(core.UnsafeTarget):
            core.validate_trash(forged, self.cfg, self.env(), NOW)

    def test_helper_route_for_owned_app(self):
        a = self.old_app(os.path.join(self.H), "Figma (v88.1).app", "com.figma")
        it = self.item(a)
        self.assertEqual(it.kind, "trash")
        res = core.execute_item(it, self.cfg, runner=core.subprocess.run, env=self.env(), now=NOW)
        self.assertEqual(res, (True, "ok"))
        with open(self.helper_log) as f:
            self.assertEqual(f.read().splitlines(), ["--home", self.home, a])
        self.assertFalse(os.path.exists(a))
        self.assertTrue(os.path.isdir(os.path.join(self.trash, "Figma (v88.1).app")))

    def finder_pair(self):
        self.uid = OTHER_UID
        os.makedirs(os.path.join(self.R, "Vendor"))
        a = self.old_app(self.R, 'Café "Beta".app', "com.cafe")
        b = self.old_app(os.path.join(self.R, "Vendor"), "B (v2).app", "com.b")
        items = [self.item(a), self.item(b)]
        self.assertEqual([i.kind for i in items], ["finder-trash", "finder-trash"])
        return a, b, items

    def test_finder_batch_one_call_args_not_source(self):
        a, b, items = self.finder_pair()
        run = FinderRunner()
        res = core.execute_finder_batch(items, self.cfg, runner=run, env=self.env(), now=NOW)
        self.assertEqual(res, [(True, "ok"), (True, "ok")])
        self.assertEqual(len(run.calls), 1)
        argv = run.calls[0]
        self.assertEqual(argv, core.FINDER_TRASH_ARGV_HEAD + ["--", a, b])
        self.assertEqual(argv[0], "osascript")
        head = "\n".join(core.FINDER_TRASH_ARGV_HEAD)
        for name in ("Café", "Beta", "Vendor", self.home):
            self.assertNotIn(name, head)
        self.assertIn('tell application "Finder" to delete', head)
        self.assertIn("with timeout of 3600 seconds", head)
        self.assertIn("on run argv", head)

    def test_finder_batch_cancelled_maps_message(self):
        a, b, items = self.finder_pair()
        run = FinderRunner(rc=1, stderr="execution error: User canceled. (-128)", delete=[])
        res = core.execute_finder_batch(items, self.cfg, runner=run, env=self.env(), now=NOW)
        self.assertEqual(res, [(False, "Cancelled: nothing was moved")] * 2)
        self.assertTrue(os.path.isdir(a) and os.path.isdir(b))

    def test_finder_batch_error_messages(self):
        for err, msg in (("(-1743)", core.FINDER_APPS_DENIED),
                         ("(-1712)", "Finder took too long. Scan again to see what moved."),
                         ("execution error: odd (-10000)", "execution error: odd (-10000)")):
            with self.subTest(err=err):
                a = self.old_app(self.R, "E.app", "com.e")
                self.uid = OTHER_UID
                run = FinderRunner(rc=1, stderr=err + "\n", delete=[])
                res = core.execute_finder_batch([self.item(a)], self.cfg, runner=run,
                                                env=self.env(), now=NOW)
                self.assertEqual(res, [(False, msg)])
        self.assertTrue(core.FINDER_APPS_DENIED.startswith(
            "macOS didn't let Labyrinth Cleaner ask Finder to move apps to the Trash."))

    def test_finder_batch_partial(self):
        a, b, items = self.finder_pair()
        run = FinderRunner(delete=[a])
        res = core.execute_finder_batch(items, self.cfg, runner=run, env=self.env(), now=NOW)
        self.assertEqual(res, [(True, "ok"), (False, core.FINDER_NOT_MOVED)])

    def test_finder_batch_leaves_refused_items_out(self):
        a, b, items = self.finder_pair()
        self.old_app(os.path.join(self.R, "Vendor"), "B (v2).app", "com.b", version="9")
        run = FinderRunner()
        res = core.execute_finder_batch(items, self.cfg, runner=run, env=self.env(), now=NOW)
        self.assertEqual(res, [(True, "ok"), (False, "REFUSED: " + core.SITUATION_CHANGED)])
        self.assertEqual(run.calls, [core.FINDER_TRASH_ARGV_HEAD + ["--", a]])
        self.assertTrue(os.path.isdir(b))
        # A helper item never goes to Finder, and nothing valid means no call at all.
        self.uid = os.getuid()
        mine = self.item(b)
        run = FinderRunner()
        res = core.execute_finder_batch([mine], self.cfg, runner=run, env=self.env(), now=NOW)
        self.assertFalse(res[0][0])
        self.assertTrue(res[0][1].startswith("REFUSED: "))
        self.assertEqual(run.calls, [])

    def test_finder_trash_refused_without_helper_and_in_fixture_mode(self):
        a, b, items = self.finder_pair()
        run = FinderRunner()
        env = {k: v for k, v in os.environ.items() if k != core.TRASH_HELPER_ENV}
        with mock.patch.dict(os.environ, env, clear=True):
            res = core.execute_item(items[0], self.cfg, runner=run, env=self.env(), now=NOW)
            self.assertEqual(res, (False, core.TRASH_REFUSAL))
            self.assertEqual(core.execute_finder_batch(items, self.cfg, runner=run,
                                                       env=self.env(), now=NOW),
                             [(False, core.TRASH_REFUSAL)] * 2)
        res = core.execute_item(items[0], self.cfg, runner=core.fixture_runner, env=self.env(),
                                now=NOW)
        self.assertEqual(res, (False, "REFUSED: fixture mode never runs osascript"))
        self.assertEqual(run.calls, [])
        self.assertTrue(os.path.isdir(a) and os.path.isdir(b))

    def test_execute_item_finder_trash_uses_batch(self):
        a, b, items = self.finder_pair()
        run = FinderRunner()
        res = core.execute_item(items[1], self.cfg, runner=run, env=self.env(), now=NOW)
        self.assertEqual(res, (True, "ok"))
        self.assertEqual(run.calls, [core.FINDER_TRASH_ARGV_HEAD + ["--", b]])

    def test_execution_order_finder_last(self):
        def it(key, kind):
            return core.Item(key=key, category="x", label=key, tier=core.REVIEW, kind=kind)
        finder, empty, trash = (it("app:/F.app", "finder-trash"), it("empty-trash", "empty-trash"),
                                it("download:a", "trash"))
        self.assertEqual(core.execution_order([finder, empty, trash]), [empty, trash, finder])


if __name__ == "__main__":
    unittest.main()
