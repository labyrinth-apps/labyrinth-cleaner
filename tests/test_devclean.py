"""Fixture tests for devclean. Run: python3 -m unittest discover -s tests -v

Every test builds a fake $HOME in a temp dir. Simulator data comes from JSON
fixtures; the real xcrun is never called and nothing outside the temp dir is
touched.
"""

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import devclean_core as core  # noqa: E402

CLI = os.path.join(ROOT, "devclean")
REAL_FIXTURE = os.path.join(HERE, "fixtures", "real_2026-11-11")
T = "com.apple.CoreSimulator.SimDeviceType."
R = "com.apple.CoreSimulator.SimRuntime."


def write(path, content="x" * 100):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)


def mkdir(path):
    os.makedirs(path, exist_ok=True)
    write(os.path.join(path, "payload.bin"), "y" * 4096)
    return path


def make_home(base):
    """A fake $HOME with one of everything devclean knows about."""
    h = os.path.join(base, "home")
    P = os.path.join(h, "Documents", "Projects")
    # Flutter project
    write(f"{P}/flutter_app/pubspec.yaml", "name: app")
    write(f"{P}/flutter_app/lib/main.dart", "void main() {}")
    mkdir(f"{P}/flutter_app/build")
    mkdir(f"{P}/flutter_app/.dart_tool")
    # Flutter project that also has a package.json (like labryinth-iptv)
    write(f"{P}/labryinth-iptv/pubspec.yaml")
    write(f"{P}/labryinth-iptv/package.json", "{}")
    mkdir(f"{P}/labryinth-iptv/build")
    mkdir(f"{P}/labryinth-iptv/node_modules")
    write(f"{P}/labryinth-iptv/third_party/plugin/pubspec.yaml")
    mkdir(f"{P}/labryinth-iptv/third_party/plugin/.dart_tool")
    # Web project with a build/ folder that must NOT be offered (no pubspec)
    write(f"{P}/web_app/package.json", "{}")
    write(f"{P}/web_app/src/index.ts", "export {}")
    mkdir(f"{P}/web_app/node_modules")
    mkdir(f"{P}/web_app/.next")
    mkdir(f"{P}/web_app/build")
    write(f"{P}/web_app/.next/package.json", "{}")  # must not count as a project
    # Personal files
    write(f"{h}/Documents/taxes.pdf")
    write(f"{h}/Downloads/movie.mkv")
    # Xcode
    mkdir(f"{h}/Library/Developer/Xcode/DerivedData/App-abc")
    mkdir(f"{h}/Library/Developer/Xcode/Archives/2026-11-01/App.xcarchive")
    # Caches
    for rel in (".shorebird/bin/cache", ".npm/_cacache", "Library/Caches/CocoaPods",
                "Library/Caches/Homebrew", "Library/Caches/ms-playwright",
                ".gradle/caches/build-cache-1", ".gradle/caches/8.14",
                ".gradle/caches/9.8.0", ".gradle/caches/9.10.0", ".gradle/caches/modules-2"):
        mkdir(os.path.join(h, rel))
    return h


def cfg_for(home):
    return core.Config(home=home, project_roots=(os.path.join(home, "Documents", "Projects"),))


def fixture_sim(path=REAL_FIXTURE):
    return core.FixtureSimctl.from_dir(path)


def scan(home, sim=None, xcode=False):
    return core.scan(cfg_for(home), sim or core.NoSimctl(), xcode_check=lambda: xcode,
                     env=core.Env.fake())


def offered_paths(res):
    return {i.path for i in res.items if i.offerable and i.path}


# ------------------------------------------------------------ simctl fixtures

def dtype(name):
    return {"identifier": T + name.replace(" ", "-"), "name": name}


def runtime(rid, version, build, types):
    return {"identifier": R + rid, "version": version, "buildversion": build,
            "platform": "iOS", "isAvailable": True,
            "supportedDeviceTypes": [dtype(t) for t in types]}


def device(name, udid, state="Shutdown", available=True):
    return {"name": name, "udid": udid, "state": state, "isAvailable": available,
            "deviceTypeIdentifier": T + name.replace(" ", "-")}


def image(rid, version, build, size=8_000_000_000):
    return {"runtimeIdentifier": R + rid, "version": version, "build": build,
            "deletable": True, "sizeBytes": size, "state": "Ready"}


GENERAL = ["iPhone 17", "iPhone 17 Pro", "iPhone 18 Pro", "iPhone 18 Pro Max", "iPad (A16)"]


def incident_sim(with_18pro_sim=True):
    """2026-11-02: 27.0 general runtime + newer 27.1 that only runs iPhone Duo."""
    devices = {R + "iOS-27-1": [device("iPhone Duo", "D1")]}
    devices[R + "iOS-27-0"] = [device("iPhone 18 Pro", "P1")] if with_18pro_sim else []
    lst = {"runtimes": [runtime("iOS-27-0", "27.0", "24A335", GENERAL),
                        runtime("iOS-27-1", "27.1", "24A94425", ["iPhone Duo"])],
           "devices": devices}
    imgs = {"UUID-270": image("iOS-27-0", "27.0", "24A335"),
            "UUID-271": image("iOS-27-1", "27.1", "24A94425")}
    return core.FixtureSimctl(lst, imgs)


def offered_runtime_uuids(items):
    return {i.meta["uuid"] for i in items if i.kind == "runtime" and i.offerable}


# ============================================================ deny list

class DenyListTests(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="devclean-test-"))
        self.home = make_home(self.tmp)
        self.cfg = cfg_for(self.home)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def p(self, rel):
        return os.path.join(self.cfg.home, rel)

    def assertRefused(self, rel):
        with self.assertRaises(core.UnsafeTarget, msg=rel):
            core.validate_path(self.p(rel) if not os.path.isabs(rel) else rel, self.cfg)

    def test_protected_paths_are_refused(self):
        for rel in ["Library/Developer/Xcode/Archives",
                    "Library/Developer/Xcode/Archives/2026-11-01",
                    "Library/Developer/Xcode",            # contains Archives
                    "Library/Developer",
                    "Library",
                    "Downloads",
                    "Documents",
                    "Documents/taxes.pdf",
                    "Documents/Projects",
                    "Documents/Projects/flutter_app",      # project root
                    "Documents/Projects/flutter_app/lib",  # source
                    "Documents/Projects/web_app/src",
                    "Documents/Projects/web_app/build",    # build/ without pubspec.yaml
                    ".gradle/caches",
                    ".gradle/caches/modules-2",
                    ".android/avd",
                    ""]:                                   # $HOME itself
            self.assertRefused(rel)
        self.assertRefused("/")
        self.assertRefused(os.path.dirname(self.cfg.home))  # outside home

    def test_symlinked_target_refused(self):
        os.symlink(self.p("Documents"), self.p("Documents/Projects/flutter_app/build2"))
        os.rename(self.p("Documents/Projects/flutter_app/build"), self.p("Documents/Projects/flutter_app/old"))
        os.symlink(self.p("Downloads"), self.p("Documents/Projects/flutter_app/build"))
        self.assertRefused("Documents/Projects/flutter_app/build")

    def test_allowed_artifacts_pass(self):
        for rel in ["Documents/Projects/flutter_app/build",
                    "Documents/Projects/flutter_app/.dart_tool",
                    "Documents/Projects/labryinth-iptv/build",
                    "Documents/Projects/labryinth-iptv/node_modules",
                    "Documents/Projects/labryinth-iptv/third_party/plugin/.dart_tool",
                    "Documents/Projects/web_app/node_modules",
                    "Documents/Projects/web_app/.next",
                    "Library/Developer/Xcode/DerivedData",
                    ".gradle/caches/8.14",
                    ".shorebird/bin/cache"]:
            self.assertEqual(core.validate_path(self.p(rel), self.cfg), os.path.realpath(self.p(rel)))

    def test_scan_never_offers_protected_or_source(self):
        res = scan(self.home)
        offered = offered_paths(res)
        for path in offered:
            self.assertNotIn("Archives", path)
            self.assertNotIn("Downloads", path)
            self.assertIn(os.path.basename(path),
                          {"build", ".dart_tool", "node_modules", ".next", "DerivedData", "cache",
                           "_cacache", "CocoaPods", "Homebrew", "ms-playwright", "build-cache-1",
                           "8.14", "9.8.0"}, path)
        self.assertNotIn(self.p("Documents/Projects/web_app/build"), offered)
        self.assertNotIn(self.p("Documents/Projects/web_app/.next/node_modules"), offered)
        # Archives and Downloads appear only as info rows
        info = {i.key: i for i in res.items}
        self.assertEqual(info["archives"].tier, core.PROTECTED)
        self.assertEqual(info["downloads"].tier, core.PROTECTED)
        self.assertGreater(info["downloads"].size, 0)

    def test_forged_item_refused_at_execution(self):
        archives = self.p("Library/Developer/Xcode/Archives")
        for target in (archives, self.p("Library/Developer/Xcode"), self.p("Downloads"),
                       self.p("Documents/Projects/flutter_app")):
            it = core.Item(key="evil", category="x", label="evil", tier=core.SAFE, path=target)
            ok, msg = core.execute_item(it, self.cfg, core.NoSimctl())
            self.assertFalse(ok)
            self.assertIn("REFUSED", msg)
            self.assertTrue(os.path.isdir(target))

    def test_tiers(self):
        res = scan(self.home)
        by_path = {i.path: i for i in res.items if i.path}
        self.assertEqual(by_path[self.p("Documents/Projects/flutter_app/build")].tier, core.SAFE)
        self.assertTrue(by_path[self.p("Documents/Projects/flutter_app/build")].preselect)
        nm = by_path[self.p("Documents/Projects/web_app/node_modules")]
        self.assertEqual(nm.tier, core.OPTIN)
        self.assertFalse(nm.preselect)

    def test_xcode_running_unselects_derived_data(self):
        res = scan(self.home, xcode=True)
        dd = next(i for i in res.items if i.key == "derived-data")
        self.assertFalse(dd.preselect)
        self.assertIn("RUNNING", dd.note)


# ============================================================ gradle

class GradleTests(unittest.TestCase):
    def test_keeps_newest_by_version_not_string(self):
        tmp = os.path.realpath(tempfile.mkdtemp(prefix="devclean-test-"))
        try:
            home = make_home(tmp)
            items = {i.label: i for i in core.scan_gradle_versions(cfg_for(home))}
            self.assertEqual(items["Gradle caches 9.10.0"].tier, core.PROTECTED)
            self.assertEqual(items["Gradle caches 9.8.0"].tier, core.SAFE)
            self.assertEqual(items["Gradle caches 8.14"].tier, core.SAFE)
            self.assertNotIn("Gradle caches modules-2", items)
        finally:
            shutil.rmtree(tmp)


# ============================================================ DeviceSupport

class DeviceSupportTests(unittest.TestCase):
    def test_keep_newest_per_model(self):
        tmp = os.path.realpath(tempfile.mkdtemp(prefix="devclean-test-"))
        try:
            home = os.path.join(tmp, "home")
            ds = os.path.join(home, "Library/Developer/Xcode/iOS DeviceSupport")
            names = ["iPhone16,1 27.1 (24A5070a)",       # older version
                     "iPhone16,1 27.2 (24B5100b)",       # beta of newest version
                     "iPhone16,1 27.2 (24B119)",         # newest release -> keep
                     "iPhone17,3 26.4 (23E100)",         # only one for this model -> keep
                     "iPhone17,3 26.4 (23E100) arm64e",  # same, arch suffix variant
                     "garbage folder"]
            for n in names:
                mkdir(os.path.join(ds, n))
            cfg = cfg_for(home)
            items = {i.label: i for i in core.scan_device_support(cfg)}
            self.assertEqual(items["iPhone16,1 27.2 (24B119)"].tier, core.PROTECTED)
            self.assertEqual(items["iPhone16,1 27.2 (24B5100b)"].tier, core.CAUTION)
            self.assertEqual(items["iPhone16,1 27.1 (24A5070a)"].tier, core.CAUTION)
            self.assertEqual(items["garbage folder"].tier, core.PROTECTED)
            kept17 = [n for n in names if n.startswith("iPhone17,3") and items[n].tier == core.PROTECTED]
            self.assertEqual(len(kept17), 1)
            # caution items are never preselected and pass the gate (spaces/parens ok)
            for it in items.values():
                if it.tier == core.CAUTION:
                    self.assertFalse(it.preselect)
                    core.validate_path(it.path, cfg)
                    self.assertIn("'", it.command)  # shell-quoted for pasting
        finally:
            shutil.rmtree(tmp)


# ============================================================ Android

class AndroidTests(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="devclean-test-"))
        self.home = os.path.join(self.tmp, "home")
        sdk = os.path.join(self.home, "Library/Android/sdk/system-images")
        self.used_pixel = mkdir(f"{sdk}/android-37.1/google_apis_playstore_ps16k/arm64-v8a")
        self.used_tv = mkdir(f"{sdk}/android-34/google-tv/arm64-v8a")
        self.used_other = mkdir(f"{sdk}/android-35/google_apis/arm64-v8a")
        self.unused = mkdir(f"{sdk}/android-33/google_apis/arm64-v8a")
        avd = os.path.join(self.home, ".android/avd")
        write(f"{avd}/Pixel_10_Pro.avd/config.ini",
              "hw.lcd.density=420\nimage.sysdir.1=system-images/android-37.1/google_apis_playstore_ps16k/arm64-v8a/\n")
        write(f"{avd}/Pixel_10_Pro.ini", f"path={avd}/Pixel_10_Pro.avd\n")
        write(f"{avd}/Television_4K.avd/config.ini",
              "image.sysdir.1=system-images/android-34/google-tv/arm64-v8a/\n")
        # An AVD stored outside ~/.android/avd, found via its .ini path=
        elsewhere = os.path.join(self.tmp, "elsewhere", "Other.avd")
        write(f"{elsewhere}/config.ini", "image.sysdir.1=system-images/android-35/google_apis/arm64-v8a/\n")
        write(f"{avd}/Other.ini", f"path={elsewhere}\n")
        self.cfg = cfg_for(self.home)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def tiers(self):
        return {i.path: i.tier for i in core.scan_android(self.cfg)}

    def test_only_unused_image_offered(self):
        t = self.tiers()
        self.assertEqual(t[self.unused], core.CAUTION)
        for used in (self.used_pixel, self.used_tv, self.used_other):
            self.assertEqual(t[used], core.PROTECTED, used)
            with self.assertRaises(core.UnsafeTarget):
                core.validate_path(used, self.cfg)
        core.validate_path(self.unused, self.cfg)

    def test_unreadable_protected_avd_blocks_all(self):
        os.remove(os.path.join(self.home, ".android/avd/Television_4K.avd/config.ini"))
        t = self.tiers()
        self.assertTrue(all(v == core.PROTECTED for v in t.values()), t)

    def test_partial_system_image_paths_refused(self):
        sdk = os.path.join(self.home, "Library/Android/sdk/system-images")
        for p in (sdk, f"{sdk}/android-33", f"{sdk}/android-33/google_apis"):
            with self.assertRaises(core.UnsafeTarget):
                core.validate_path(p, self.cfg)


# ============================================================ simulator runtimes

class RuntimeProtectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="devclean-test-"))
        self.cfg = cfg_for(os.path.join(self.tmp, "home"))
        os.makedirs(self.cfg.home)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_incident_2026_11_02_never_offers_27_0(self):
        for with_sim in (True, False):  # pinned rule protects it even with no 18 Pro sim
            items, _ = core.scan_runtimes(self.cfg, incident_sim(with_sim))
            self.assertNotIn("UUID-270", offered_runtime_uuids(items), with_sim)
            self.assertNotIn("UUID-271", offered_runtime_uuids(items))  # only runtime for Duo sim
            kept = next(i for i in items if i.meta.get("uuid") == "UUID-270")
            self.assertIn("iPhone 18 Pro", kept.note)

    def test_duo_only_runtime_does_not_count_as_general(self):
        # No sims at all: the Duo runtime is newer but not general, so 27.0 is kept
        sim = incident_sim(False)
        sim._list["devices"] = {}
        items, _ = core.scan_runtimes(self.cfg, sim)
        self.assertEqual(offered_runtime_uuids(items), {"UUID-271"})

    def test_older_general_runtime_offered_when_newer_covers_it(self):
        lst = {"runtimes": [runtime("iOS-26-5", "26.5", "23F77", GENERAL),
                            runtime("iOS-27-2", "27.2", "24B8117", GENERAL)],
               "devices": {R + "iOS-26-5": [device("iPad (A16)", "A")],
                           R + "iOS-27-2": [device("iPhone 18 Pro", "B", state="Booted")]}}
        imgs = {"U265": image("iOS-26-5", "26.5", "23F77"), "U272": image("iOS-27-2", "27.2", "24B8117")}
        items, _ = core.scan_runtimes(self.cfg, core.FixtureSimctl(lst, imgs))
        self.assertEqual(offered_runtime_uuids(items), {"U265"})
        it = next(i for i in items if i.meta.get("uuid") == "U265")
        self.assertEqual(it.tier, core.CAUTION)
        self.assertFalse(it.preselect)
        self.assertEqual(it.argv, ["xcrun", "simctl", "runtime", "delete", "U265"])
        self.assertIn("iPad (A16)", it.note)

    def test_only_runtime_for_old_device_type_kept(self):
        lst = {"runtimes": [runtime("iOS-26-5", "26.5", "23F77", GENERAL + ["iPhone 15"]),
                            runtime("iOS-27-2", "27.2", "24B8117", GENERAL)],
               "devices": {R + "iOS-26-5": [device("iPhone 15", "A")]}}
        imgs = {"U265": image("iOS-26-5", "26.5", "23F77"), "U272": image("iOS-27-2", "27.2", "24B8117")}
        items, _ = core.scan_runtimes(self.cfg, core.FixtureSimctl(lst, imgs))
        self.assertEqual(offered_runtime_uuids(items), set())

    def test_same_version_tie_protects_both(self):
        lst = {"runtimes": [runtime("iOS-27-2", "27.2", "24B8117", GENERAL),
                            runtime("iOS-27-2-b", "27.2", "24B9000", GENERAL)],
               "devices": {R + "iOS-27-2": [device("iPhone 17", "A")]}}
        imgs = {"A": image("iOS-27-2", "27.2", "24B8117"), "B": image("iOS-27-2-b", "27.2", "24B9000")}
        items, _ = core.scan_runtimes(self.cfg, core.FixtureSimctl(lst, imgs))
        self.assertEqual(offered_runtime_uuids(items), set())

    def test_unmatched_image_not_offered(self):
        lst = {"runtimes": [], "devices": {}}
        imgs = {"X": image("iOS-99-0", "99.0", "Z1")}
        items, _ = core.scan_runtimes(self.cfg, core.FixtureSimctl(lst, imgs))
        self.assertEqual(offered_runtime_uuids(items), set())

    def test_real_machine_fixture(self):
        """simctl output captured on 2026-11-11: only iOS 26.5 may be offered."""
        items, warnings = core.scan_runtimes(self.cfg, fixture_sim())
        self.assertEqual(warnings, [])
        self.assertEqual(offered_runtime_uuids(items), {"91298CDE-FDEA-4F7C-8FF5-E27CFAC0C3B6"})
        by_uuid = {i.meta.get("uuid"): i for i in items}
        general = by_uuid["8CC38006-6DBA-4D63-9F9E-A0C3B02E5B16"]  # iOS 27.2 24B8117
        self.assertEqual(general.tier, core.PROTECTED)
        self.assertIn("pinned iPhone 18 Pro", general.note)
        duo = by_uuid["6FCD9931-A1D9-47D9-9C2F-BF4C3355AEDE"]
        self.assertEqual(duo.tier, core.PROTECTED)
        self.assertIn("iPhone Duo", duo.note)
        self.assertEqual(by_uuid["0D0A92AB-83AF-4E8B-BCCD-21DABFA6AECC"].tier, core.PROTECTED)

    def test_execute_rechecks_runtime_protection(self):
        sim = incident_sim()
        forged = core.Item(key="r", category="Simulator", label="iOS 27.0", tier=core.CAUTION,
                           kind="runtime", argv=["xcrun", "simctl", "runtime", "delete", "UUID-270"],
                           meta={"uuid": "UUID-270"})
        calls = []
        ok, msg = core.execute_item(forged, self.cfg, sim, runner=lambda *a, **k: calls.append(a))
        self.assertFalse(ok)
        self.assertIn("REFUSED", msg)
        self.assertEqual(calls, [])


# ============================================================ CLI end-to-end

class CostAndStaleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="devclean-test-"))
        self.home = make_home(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_every_offerable_item_has_cost(self):
        res = core.scan(cfg_for(self.home), fixture_sim(), xcode_check=lambda: False)
        self.assertTrue(res.offerable)
        for it in res.offerable:
            self.assertTrue(it.cost_tag, it.key)
            self.assertNotEqual(it.cost_tag, "?", it.key)
            self.assertTrue(it.cost, it.key)

    def test_cost_table_values(self):
        by = {i.key: i for i in scan(self.home).items}
        self.assertEqual(by["homebrew-cache"].cost_tag, "NO COST")
        self.assertEqual(by["npm-cache"].cost_tag, "NO COST")
        self.assertEqual(by["cocoapods-cache"].cost_tag, "~1 MIN")
        self.assertEqual(by["gradle-build-cache"].cost_tag, "~2 MIN")
        self.assertIn("8.14", by["gradle:8.14"].cost)
        self.assertEqual(by["derived-data"].cost_tag, "~5 MIN")
        self.assertEqual(by["flutter:Documents/Projects/flutter_app/build"].cost_tag, "~2 MIN")
        self.assertEqual(by["web:Documents/Projects/web_app/node_modules"].cost_tag, "~1 MIN")
        self.assertIn("web_app", by["web:Documents/Projects/web_app/node_modules"].cost)
        self.assertEqual(by["web:Documents/Projects/web_app/.next"].cost_tag, "~1 MIN")

    def test_kept_items_cost_is_reason(self):
        by = {i.key: i for i in scan(self.home).items}
        self.assertEqual(by["archives"].cost_tag, "")
        self.assertEqual(by["archives"].cost, by["archives"].note)

    def test_runtime_tag_is_download_size(self):
        res = core.scan(cfg_for(self.home), fixture_sim(), xcode_check=lambda: False)
        rt = [i for i in res.offerable if i.kind == "runtime"][0]
        self.assertEqual(rt.cost_tag, core.human(rt.size) + " DL")

    def test_derived_data_cost_when_xcode_running(self):
        dd = [i for i in scan(self.home, xcode=True).items if i.key == "derived-data"][0]
        self.assertIn("Quit Xcode first", dd.cost)

    def test_project_field(self):
        by = {i.key: i for i in scan(self.home).items}
        self.assertEqual(by["flutter:Documents/Projects/flutter_app/build"].project, "flutter_app")
        self.assertEqual(by["web:Documents/Projects/web_app/.next"].project, "web_app")
        self.assertEqual(by["flutter:Documents/Projects/labryinth-iptv/third_party/plugin/.dart_tool"].project,
                         "labryinth-iptv/third_party/plugin")
        self.assertIsNone(by["homebrew-cache"].project)

    def test_stale_ignores_artifacts_and_git(self):
        P = os.path.join(self.home, "Documents", "Projects", "web_app")
        old = time.time() - 90 * 86400
        for name in os.listdir(P):
            os.utime(os.path.join(P, name), (old, old))
        os.makedirs(os.path.join(P, ".git"), exist_ok=True)   # fresh .git must not count
        os.utime(os.path.join(P, "node_modules"))             # fresh artifact must not count
        nm = [i for i in scan(self.home).items if i.key == "web:Documents/Projects/web_app/node_modules"][0]
        self.assertGreaterEqual(nm.stale_days, 89)
        self.assertEqual(nm.to_dict()["stale_days"], nm.stale_days)

    def test_fresh_project_not_stale(self):
        fl = [i for i in scan(self.home).items if i.key == "flutter:Documents/Projects/flutter_app/build"][0]
        self.assertEqual(fl.stale_days, 0)

    def test_stale_label(self):
        self.assertIsNone(core.stale_label(None))
        self.assertIsNone(core.stale_label(30))
        self.assertEqual(core.stale_label(31), "31d unused")
        self.assertEqual(core.stale_label(59), "59d unused")
        self.assertEqual(core.stale_label(95), "3 mo unused")

    def test_human_moved_to_core(self):
        self.assertEqual(core.human(None), "?")
        self.assertEqual(core.human(999), "999 B")
        self.assertEqual(core.human(1536), "2 KB")
        self.assertEqual(core.human(int(9.9 * 1024 ** 3)), "9.9 GB")

    def test_unreadable_project_folder_becomes_warning(self):
        P = os.path.join(self.home, "Documents", "Projects", "web_app")
        os.chmod(P, 0)
        try:
            res = scan(self.home)
        finally:
            os.chmod(P, 0o755)
        self.assertTrue(any("permission denied" in w and "web_app" in w for w in res.warnings),
                        res.warnings)

    def test_fixture_runner_never_runs_xcrun(self):
        cfg = cfg_for(self.home)
        sim = fixture_sim()
        rt = [i for i in core.scan(cfg, sim, xcode_check=lambda: False).offerable
              if i.kind == "runtime"][0]
        with mock.patch.object(core.subprocess, "run") as run:
            ok, msg = core.execute_item(rt, cfg, sim, runner=core.fixture_runner)
        self.assertFalse(ok)
        self.assertIn("fixture mode", msg)
        run.assert_not_called()

    def test_fixture_runner_runs_rm(self):
        d = mkdir(os.path.join(self.tmp, "gone"))
        r = core.fixture_runner(["rm", "-rf", "--", d], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertFalse(os.path.exists(d))

    def test_cli_fixture_mode_uses_fixture_runner(self):
        with open(CLI) as f:
            src = f.read()
        self.assertIn("core.fixture_runner", src)
        self.assertNotIn("def human(", src)


def orphan_sim():
    """iOS 26.5 offerable (27.2 covers the same types) + one sim whose runtime is gone."""
    lst = {"runtimes": [runtime("iOS-26-5", "26.5", "23F77", GENERAL),
                        runtime("iOS-27-2", "27.2", "24B8117", GENERAL)],
           "devices": {R + "iOS-26-5": [device("iPad (A16)", "A1")],
                       R + "iOS-27-2": [device("iPhone 18 Pro", "P1")],
                       R + "iOS-25-0": [device("iPhone 17", "GONE", available=False)]}}
    imgs = {"UUID-265": image("iOS-26-5", "26.5", "23F77"),
            "UUID-272": image("iOS-27-2", "27.2", "24B8117")}
    return core.FixtureSimctl(lst, imgs)


class MinorFixTests(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="devclean-test-"))
        self.home = make_home(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_execution_order_runs_unavailable_sims_first(self):
        res = core.scan(cfg_for(self.home), orphan_sim(), xcode_check=lambda: False)
        by = {i.key: i for i in res.items}
        rt, un, hb = by["runtime:UUID-265"], by["simctl-unavailable"], by["homebrew-cache"]
        self.assertEqual(core.execution_order([rt, hb, un]), [un, rt, hb])
        self.assertEqual(core.execution_order([hb, rt]), [hb, rt])

    def test_cli_commands_list_unavailable_before_runtime(self):
        fx = os.path.join(self.tmp, "fx")
        os.makedirs(fx)
        sim = orphan_sim()
        for name, data in (("simctl_list.json", sim._list), ("runtime_list.json", sim._images)):
            with open(os.path.join(fx, name), "w") as f:
                json.dump(data, f)
        r = subprocess.run([sys.executable, CLI, "--home", self.home, "--min-size", "0",
                            "--projects", os.path.join(self.home, "Documents", "Projects"),
                            "--dry-run", "--select", "all", "--simctl-fixture", fx],
                           capture_output=True, text=True, stdin=subprocess.DEVNULL,
                           env={**os.environ, "NO_COLOR": "1"})
        cmds = r.stdout.split("Commands:")[1].splitlines()
        i_un = next(i for i, l in enumerate(cmds) if "simctl delete unavailable" in l)
        i_rt = next(i for i, l in enumerate(cmds) if "simctl runtime delete" in l)
        self.assertLess(i_un, i_rt)

    def test_backtick_in_project_name_keeps_cost_code_spans_balanced(self):
        P = os.path.join(self.home, "Documents", "Projects", "odd`name")
        write(os.path.join(P, "package.json"), "{}")
        mkdir(os.path.join(P, "node_modules"))
        it = [i for i in scan(self.home).items if i.key == "web:Documents/Projects/odd`name/node_modules"][0]
        self.assertEqual(it.cost.count("`") % 2, 0, it.cost)
        self.assertIn("odd'name", it.cost)
        self.assertEqual(it.project, "odd`name")   # the name itself is untouched


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="devclean-test-"))
        self.home = make_home(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def run_cli(self, *args):
        r = subprocess.run([sys.executable, CLI, "--home", self.home, "--min-size", "0",
                            "--projects", os.path.join(self.home, "Documents", "Projects"), *args],
                           capture_output=True, text=True, stdin=subprocess.DEVNULL,
                           env={**os.environ, "NO_COLOR": "1"})
        return r.returncode, r.stdout + r.stderr

    def snapshot(self):
        out = set()
        for d, _, files in os.walk(self.home):
            for f in files:
                out.add(os.path.join(d, f))
        return out

    def test_dry_run_deletes_nothing(self):
        before = self.snapshot()
        code, out = self.run_cli("--dry-run", "--select", "all")
        self.assertEqual(code, 0, out)
        self.assertIn("Dry run: nothing deleted.", out)
        self.assertIn("rm -rf -- ", out)
        self.assertEqual(before, self.snapshot())
        self.assertNotIn("Archives'", out.split("Would run")[1])

    def test_command_lines_are_paste_safe(self):
        """Each printed command line must parse to exactly the intended argv.

        zsh has INTERACTIVE_COMMENTS off by default, so any trailing "# note"
        would turn into extra paths for rm.
        """
        import shlex
        code, out = self.run_cli("--dry-run", "--select", "all")
        self.assertEqual(code, 0, out)
        cmds = [l for l in out.splitlines() if l.startswith(("rm ", "xcrun "))]
        self.assertGreater(len(cmds), 5)
        for line in cmds:
            argv = shlex.split(line)
            self.assertNotIn("#", line, line)
            self.assertEqual(argv[:3], ["rm", "-rf", "--"], line)
            self.assertEqual(len(argv), 4, line)
            core.validate_path(argv[3], cfg_for(self.home))
        # no other line contains an rm/xcrun command a paste could pick up
        others = [l for l in out.splitlines() if ("rm -rf" in l or "xcrun simctl" in l) and l not in cmds]
        self.assertEqual(others, [])

    def test_dry_run_with_incident_fixture(self):
        fx = os.path.join(self.tmp, "fx")
        os.makedirs(fx)
        sim = incident_sim()
        for name, data in (("simctl_list.json", sim._list), ("runtime_list.json", sim._images)):
            with open(os.path.join(fx, name), "w") as f:
                json.dump(data, f)
        code, out = self.run_cli("--dry-run", "--select", "all", "--simctl-fixture", fx)
        self.assertEqual(code, 0, out)
        self.assertNotIn("runtime delete UUID-270", out)
        self.assertNotIn("runtime delete UUID-271", out)

    def test_yes_deletes_only_safe_items(self):
        P = os.path.join(self.home, "Documents/Projects")
        code, out = self.run_cli("--yes", "--select", "all")
        self.assertEqual(code, 0, out)
        self.assertIn("skipping", out)  # opt-in items dropped
        gone = [f"{P}/flutter_app/build", f"{P}/flutter_app/.dart_tool",
                f"{P}/labryinth-iptv/build", f"{P}/labryinth-iptv/third_party/plugin/.dart_tool",
                f"{self.home}/Library/Developer/Xcode/DerivedData",
                f"{self.home}/.gradle/caches/8.14", f"{self.home}/.gradle/caches/9.8.0",
                f"{self.home}/.shorebird/bin/cache"]
        kept = [f"{P}/web_app/node_modules", f"{P}/web_app/.next", f"{P}/web_app/build",
                f"{P}/labryinth-iptv/node_modules", f"{P}/flutter_app/lib/main.dart",
                f"{P}/flutter_app/pubspec.yaml", f"{P}/web_app/src/index.ts",
                f"{self.home}/Library/Developer/Xcode/Archives/2026-11-01/App.xcarchive",
                f"{self.home}/Downloads/movie.mkv", f"{self.home}/Documents/taxes.pdf",
                f"{self.home}/.gradle/caches/9.10.0", f"{self.home}/.gradle/caches/modules-2"]
        for g in gone:
            self.assertFalse(os.path.exists(g), g)
        for k in kept:
            self.assertTrue(os.path.exists(k), k)

    def test_refuses_to_delete_without_tty_or_yes(self):
        before = self.snapshot()
        code, out = self.run_cli()
        self.assertEqual(code, 1)
        self.assertIn("Refusing", out)
        self.assertEqual(before, self.snapshot())

    def test_bad_selection(self):
        code, out = self.run_cli("--dry-run", "--select", "999")
        self.assertEqual(code, 2)


class ProjectRootTests(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="devclean-test-"))
        self.home = make_home(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def scan(self, cfg):
        return core.scan(cfg, core.NoSimctl(), xcode_check=lambda: False, env=core.Env.fake())

    def test_default_roots_only_existing_conventional_dirs(self):
        os.makedirs(os.path.join(self.home, "Developer"))
        cfg = core.Config(home=self.home)
        self.assertEqual(cfg.project_roots, (os.path.join(self.home, "Developer"),))

    def test_documents_projects_not_scanned_unless_added(self):
        res = self.scan(core.Config(home=self.home))
        self.assertFalse([i for i in res.items if i.category in ("Flutter", "Web")])

    def test_settings_folder_is_scanned(self):
        core.add_project_folder(self.home, os.path.join(self.home, "Documents", "Projects"))
        res = self.scan(core.Config(home=self.home))
        self.assertIn("flutter:Documents/Projects/flutter_app/build", {i.key for i in res.items})
        self.assertEqual(stat.S_IMODE(os.stat(core.settings_path(self.home)).st_mode), 0o600)

    def test_add_project_folder_rejects_bad_paths(self):
        os.symlink(os.path.join(self.home, "Documents"), os.path.join(self.home, "link"))
        for bad in (self.home, os.path.join(self.home, "Library"), os.path.join(self.home, "link"),
                    os.path.join(self.home, "Documents/taxes.pdf"), "relative/path", "/etc",
                    os.path.join(self.home, "nope")):
            with self.assertRaises(ValueError, msg=bad):
                core.add_project_folder(self.home, bad)

    def test_add_project_folder_rejects_folder_containing_deny_path(self):
        os.makedirs(os.path.join(self.home, ".ssh"), exist_ok=True)
        with self.assertRaises(ValueError):
            core.add_project_folder(self.home, os.path.join(self.home, ".ssh"))

    def test_downloads_allowed_when_picked(self):
        d = os.path.join(self.home, "Downloads")
        os.makedirs(d, exist_ok=True)
        self.assertEqual(core.add_project_folder(self.home, d), d)

    def test_remove_project_folder(self):
        P = os.path.join(self.home, "Documents", "Projects")
        core.add_project_folder(self.home, P)
        self.assertIn(P, core.load_settings(self.home)["project_folders"])
        core.remove_project_folder(self.home, P)
        self.assertNotIn(P, core.load_settings(self.home)["project_folders"])

    def test_load_settings_survives_broken_file(self):
        core.save_settings(self.home, {"a": 1})
        with open(core.settings_path(self.home), "w") as f:
            f.write("{nope")
        self.assertEqual(core.load_settings(self.home), {})

    def test_overlapping_roots_list_each_project_once(self):
        P = os.path.join(self.home, "Documents", "Projects")
        cfg = core.Config(home=self.home, project_roots=(P, os.path.join(P, "labryinth-iptv")))
        keys = [i.key for i in self.scan(cfg).items]
        self.assertEqual(len(keys), len(set(keys)))

    def test_gate_accepts_artifact_in_any_root_and_refuses_outside(self):
        dev = os.path.join(self.home, "Developer", "app")
        write(os.path.join(dev, "package.json"), "{}")
        mkdir(os.path.join(dev, "node_modules"))
        cfg = core.Config(home=self.home, project_roots=(os.path.join(self.home, "Developer"),))
        self.assertTrue(core.validate_path(os.path.join(dev, "node_modules"), cfg))
        with self.assertRaises(core.UnsafeTarget):
            core.validate_path(os.path.join(self.home, "Documents/Projects/web_app/node_modules"), cfg)

    def test_scan_never_uses_real_simctl_for_fixture_home(self):
        with mock.patch.object(core.SimctlProvider, "_json", side_effect=AssertionError("real xcrun")):
            core.scan(core.Config(home=self.home), xcode_check=lambda: False)

    def test_real_simctl_only_when_dev_tools(self):
        calls = []
        with mock.patch.object(core.SimctlProvider, "_json",
                               side_effect=lambda a: calls.append(a) or {}):
            core.scan(core.Config(home=self.home), xcode_check=lambda: False,
                      env=core.Env.fake(dev_tools=lambda: False))
            self.assertEqual(calls, [])
            core.scan(core.Config(home=self.home), xcode_check=lambda: False,
                      env=core.Env.fake(dev_tools=lambda: True))
            self.assertTrue(calls)

    def test_cli_projects_flag_repeatable(self):
        mkdir(os.path.join(self.home, "Developer", "app", "node_modules"))
        write(os.path.join(self.home, "Developer", "app", "package.json"), "{}")
        r = subprocess.run([sys.executable, CLI, "--home", self.home, "--json",
                            "--projects", os.path.join(self.home, "Documents", "Projects"),
                            "--projects", os.path.join(self.home, "Developer")],
                           capture_output=True, text=True, stdin=subprocess.DEVNULL)
        keys = {i["key"] for i in json.loads(r.stdout)["items"]}
        self.assertIn("flutter:Documents/Projects/flutter_app/build", keys)
        self.assertIn("web:Developer/app/node_modules", keys)


if __name__ == "__main__":
    unittest.main()
