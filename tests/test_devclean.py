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

    def test_trash_is_never_rm_target_even_as_project_root(self):
        """~/.Trash is only ever emptied through Finder: nothing under it may be rm'd,
        even when it is passed as a project root."""
        write(self.p(".Trash/oldapp/pubspec.yaml"), "name: old")
        mkdir(self.p(".Trash/oldapp/build"))
        cfg = core.Config(home=self.home, project_roots=(self.p(".Trash"),))
        self.assertEqual(cfg.project_roots, ())        # Config drops it as a root
        cfg.project_roots = (self.p(".Trash"),)        # the gate refuses even when forced
        for rel in (".Trash", ".Trash/oldapp", ".Trash/oldapp/build"):
            with self.assertRaises(core.UnsafeTarget, msg=rel):
                core.validate_path(self.p(rel), cfg)
        res = core.scan(cfg, core.NoSimctl(), xcode_check=lambda: False, env=core.Env.fake())
        under = [i for i in res.items if i.path and core.is_within(i.path, self.p(".Trash"))]
        self.assertTrue(under)                         # found, but only as kept rows
        for it in under:
            self.assertFalse(it.offerable, it.key)
            self.assertIn("refused by safety gate", it.note)
            ok, msg = core.execute_item(core.Item(key=it.key, category="Flutter", label="x",
                                                  tier=core.SAFE, path=it.path), cfg, core.NoSimctl())
            self.assertFalse(ok)
            self.assertIn("REFUSED", msg)
        self.assertTrue(os.path.isdir(self.p(".Trash/oldapp/build")))

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
        """Archives are offered only as REVIEW items that move to the Trash (never rm),
        and the newest Gradle version is offered with a warning (Task 2: user choice)."""
        res = scan(self.home)
        rm_offered = {i.path for i in res.items if i.offerable and i.kind == "path"}
        for path in rm_offered:
            self.assertNotIn("Archives", path)
            self.assertNotIn("Downloads", path)
            self.assertIn(os.path.basename(path),
                          {"build", ".dart_tool", "node_modules", ".next", "DerivedData", "cache",
                           "_cacache", "CocoaPods", "Homebrew", "ms-playwright", "build-cache-1",
                           "8.14", "9.8.0", "9.10.0"}, path)
        self.assertNotIn(self.p("Documents/Projects/web_app/build"), rm_offered)
        self.assertNotIn(self.p("Documents/Projects/web_app/.next/node_modules"), rm_offered)
        info = {i.key: i for i in res.items}
        self.assertNotIn("archives", info)
        arch = info["archive:2026-11-01/App.xcarchive"]
        self.assertEqual((arch.tier, arch.kind), (core.REVIEW, "trash"))
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

    def test_xcode_running_keeps_derived_data(self):
        """Fix round 1: DerivedData is blocked (not just unselected) while Xcode runs."""
        res = scan(self.home, xcode=True)
        dd = next(i for i in res.items if i.key == "derived-data")
        self.assertEqual((dd.tier, dd.kind, dd.note), (core.PROTECTED, "info", "Quit Xcode first"))
        self.assertFalse(dd.offerable)
        self.assertFalse(dd.preselect)

    def test_env_xcode_running_used_when_no_xcode_check(self):
        self.assertIs(core.Env.real().xcode_running, core.xcode_running)
        self.assertFalse(core.Env.fake().xcode_running())
        res = core.scan(self.cfg, core.NoSimctl(), env=core.Env.fake(xcode_running=lambda: True))
        self.assertEqual(next(i for i in res.items if i.key == "derived-data").tier, core.PROTECTED)
        res = core.scan(self.cfg, core.NoSimctl(), xcode_check=lambda: False,
                        env=core.Env.fake(xcode_running=lambda: True))   # xcode_check wins
        self.assertEqual(next(i for i in res.items if i.key == "derived-data").tier, core.SAFE)

    def test_gate_refuses_derived_data_once_xcode_runs(self):
        dd = next(i for i in scan(self.home).items if i.key == "derived-data")
        self.assertTrue(dd.offerable)
        core.validate_item(dd, self.cfg, core.NoSimctl(), env=core.Env.fake())
        calls = []
        ok, msg = core.execute_item(dd, self.cfg, core.NoSimctl(), runner=lambda *a, **k: calls.append(a),
                                    env=core.Env.fake(xcode_running=lambda: True))
        self.assertFalse(ok)
        self.assertIn("Quit Xcode first", msg)
        self.assertEqual(calls, [])
        self.assertTrue(os.path.isdir(dd.path))


# ============================================================ gradle

class GradleTests(unittest.TestCase):
    def test_keeps_newest_by_version_not_string(self):
        tmp = os.path.realpath(tempfile.mkdtemp(prefix="devclean-test-"))
        try:
            home = make_home(tmp)
            items = {i.label: i for i in core.scan_gradle_versions(cfg_for(home))}
            # Task 2: the newest version is offered with a warning instead of kept.
            self.assertEqual(items["Gradle caches 9.10.0"].tier, core.CAUTION)
            self.assertTrue(items["Gradle caches 9.10.0"].warning)
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
            # Task 2: the newest per model is offered with a warning instead of kept.
            newest = core.DEVSUPPORT_NEWEST_WARNING
            self.assertEqual(items["iPhone16,1 27.2 (24B119)"].warning, newest)
            self.assertEqual(items["iPhone16,1 27.2 (24B5100b)"].warning, "")
            self.assertEqual(items["iPhone16,1 27.1 (24A5070a)"].warning, "")
            self.assertEqual(items["garbage folder"].tier, core.PROTECTED)
            warned17 = [n for n in names if n.startswith("iPhone17,3") and items[n].warning]
            self.assertEqual(len(warned17), 1)
            # caution items are never preselected and pass the gate (spaces/parens ok)
            for it in items.values():
                if it.tier == core.CAUTION:
                    self.assertFalse(it.preselect)
                    core.validate_path(it.path, cfg)
                    self.assertIn("'", it.command)  # shell-quoted for pasting
        finally:
            shutil.rmtree(tmp)


# ============================================================ Android

def make_android(tmp, home):
    """Three images used by emulators (one AVD stored outside ~/.android/avd) and one unused.

    Returns {"Pixel": path, "TV": path, "Other": path, "unused": path}.
    """
    sdk = os.path.join(home, "Library/Android/sdk/system-images")
    paths = {"Pixel": mkdir(f"{sdk}/android-37.1/google_apis_playstore_ps16k/arm64-v8a"),
             "TV": mkdir(f"{sdk}/android-34/google-tv/arm64-v8a"),
             "Other": mkdir(f"{sdk}/android-35/google_apis/arm64-v8a"),
             "unused": mkdir(f"{sdk}/android-33/google_apis/arm64-v8a")}
    avd = os.path.join(home, ".android/avd")
    write(f"{avd}/Pixel.avd/config.ini",
          "hw.lcd.density=420\nimage.sysdir.1=system-images/android-37.1/google_apis_playstore_ps16k/arm64-v8a/\n")
    write(f"{avd}/Pixel.ini", f"path={avd}/Pixel.avd\n")
    write(f"{avd}/TV.avd/config.ini",
          "image.sysdir.1=system-images/android-34/google-tv/arm64-v8a/\n")
    # An AVD stored outside ~/.android/avd, found via its .ini path=
    elsewhere = os.path.join(tmp, "elsewhere", "Other.avd")
    write(f"{elsewhere}/config.ini", "image.sysdir.1=system-images/android-35/google_apis/arm64-v8a/\n")
    write(f"{avd}/Other.ini", f"path={elsewhere}\n")
    return paths


class AndroidTests(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="devclean-test-"))
        self.home = os.path.join(self.tmp, "home")
        imgs = make_android(self.tmp, self.home)
        self.used_pixel, self.used_tv = imgs["Pixel"], imgs["TV"]
        self.used_other, self.unused = imgs["Other"], imgs["unused"]
        self.cfg = cfg_for(self.home)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def tiers(self):
        return {i.path: i.tier for i in core.scan_android(self.cfg)}

    def test_used_images_offered_with_warning(self):
        """Task 2: images used by an emulator are offered with a warning (was: kept and
        refused by validate_path). The warning, re-checked by validate_item, is the guard."""
        by = {i.path: i for i in core.scan_android(self.cfg)}
        self.assertEqual((by[self.unused].tier, by[self.unused].warning), (core.CAUTION, ""))
        for used, name in ((self.used_pixel, "Pixel"), (self.used_tv, "TV"), (self.used_other, "Other")):
            self.assertEqual(by[used].tier, core.CAUTION, used)
            self.assertIn(f"The {name} emulator stops working", by[used].warning)
            core.validate_path(used, self.cfg)
        core.validate_path(self.unused, self.cfg)

    def test_unlistable_avd_folder_warns_every_image(self):
        """Fix round 1: an AVD folder that exists but can't be listed must not fail open."""
        avd = os.path.join(self.home, ".android/avd")
        os.chmod(avd, 0)
        try:
            items = core.scan_android(self.cfg)
        finally:
            os.chmod(avd, 0o755)
        self.assertEqual(len(items), 4)
        for it in items:
            self.assertEqual(it.tier, core.CAUTION, it.path)
            self.assertEqual(it.warning,
                             "Couldn't read your emulators' settings; an emulator may use this image.")

    def test_unsearchable_android_folder_warns_every_image(self):
        dot_android = os.path.join(self.home, ".android")
        os.chmod(dot_android, 0)
        try:
            items = core.scan_android(self.cfg)
        finally:
            os.chmod(dot_android, 0o755)
        self.assertTrue(items)
        self.assertTrue(all(it.warning == core.AVDS_UNLISTABLE_WARNING for it in items))

    def test_no_avd_folder_means_no_warning(self):
        shutil.rmtree(os.path.join(self.home, ".android"))
        self.assertTrue(all(it.warning == "" for it in core.scan_android(self.cfg)))

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

    def test_duo_only_runtime_does_not_count_as_general(self):
        """No sims at all: the Duo runtime is newer but not general, so 27.0 carries the
        newest-iOS warning (Task 2: was kept) and 27.1 carries none."""
        sim = incident_sim(False)
        sim._list["devices"] = {}
        items, _ = core.scan_runtimes(self.cfg, sim)
        self.assertEqual(offered_runtime_uuids(items), {"UUID-270", "UUID-271"})
        by = {i.meta.get("uuid"): i for i in items}
        self.assertEqual(by["UUID-270"].warning, core.NEWEST_IOS_WARNING)
        self.assertEqual(by["UUID-271"].warning, "")

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
        # Fix round 1: the simulator on it still stops working, so the runtime warns.
        self.assertEqual(it.warning, "Your iPad (A16) simulator stops working. "
                                     "Xcode can create a new one on iOS 27.2.")

    def test_only_runtime_for_old_device_type_warns(self):
        """Task 2: was kept; now offered with a warning naming the simulator."""
        lst = {"runtimes": [runtime("iOS-26-5", "26.5", "23F77", GENERAL + ["iPhone 15"]),
                            runtime("iOS-27-2", "27.2", "24B8117", GENERAL)],
               "devices": {R + "iOS-26-5": [device("iPhone 15", "A")]}}
        imgs = {"U265": image("iOS-26-5", "26.5", "23F77"), "U272": image("iOS-27-2", "27.2", "24B8117")}
        items, _ = core.scan_runtimes(self.cfg, core.FixtureSimctl(lst, imgs))
        by = {i.meta.get("uuid"): i for i in items}
        self.assertEqual(by["U265"].warning,
                         "Your iPhone 15 simulator stops working. No other installed runtime runs it.")
        self.assertEqual(by["U272"].warning, core.NEWEST_IOS_WARNING)
        self.assertTrue(all(not i.preselect for i in items))

    def test_same_version_tie_warns_both(self):
        """Task 2: was kept; both tied runtimes are now offered, each with a warning."""
        lst = {"runtimes": [runtime("iOS-27-2", "27.2", "24B8117", GENERAL),
                            runtime("iOS-27-2-b", "27.2", "24B9000", GENERAL)],
               "devices": {R + "iOS-27-2": [device("iPhone 17", "A")]}}
        imgs = {"A": image("iOS-27-2", "27.2", "24B8117"), "B": image("iOS-27-2-b", "27.2", "24B9000")}
        items, _ = core.scan_runtimes(self.cfg, core.FixtureSimctl(lst, imgs))
        self.assertEqual(offered_runtime_uuids(items), {"A", "B"})
        by = {i.meta.get("uuid"): i for i in items}
        # The iPhone 17 simulator lives on A; deleting B never stops it. B can host a new one.
        self.assertEqual(by["A"].warning, "Your iPhone 17 simulator stops working. Xcode can create "
                                          "a new one on iOS 27.2. " + core.NEWEST_IOS_WARNING)
        self.assertEqual(by["B"].warning, core.NEWEST_IOS_WARNING)

    def test_unmatched_image_not_offered(self):
        lst = {"runtimes": [], "devices": {}}
        imgs = {"X": image("iOS-99-0", "99.0", "Z1")}
        items, _ = core.scan_runtimes(self.cfg, core.FixtureSimctl(lst, imgs))
        self.assertEqual(offered_runtime_uuids(items), set())

    def test_real_machine_fixture(self):
        """simctl output captured on 2026-11-11.

        Task 2 (user choice): iOS 26.5 is offered with no warning; the general 27.2 runtime
        stays kept only because a booted simulator runs on it; the Duo-only 27.2 and the
        watchOS runtime are offered with a warning naming their simulators (were kept)."""
        items, warnings = core.scan_runtimes(self.cfg, fixture_sim())
        self.assertEqual(warnings, [])
        by_uuid = {i.meta.get("uuid"): i for i in items}
        ios265 = by_uuid["91298CDE-FDEA-4F7C-8FF5-E27CFAC0C3B6"]
        self.assertEqual(ios265.tier, core.CAUTION)
        # Fix round 1 (controller ruling, overrides the brief's "no warning"): its simulators break.
        self.assertEqual(ios265.warning,
                         "Your iPad (A16), iPad Air 11-inch (M4), iPad mini (A17 Pro) simulators stop "
                         "working. Xcode can create a new one on iOS 27.2.")
        general = by_uuid["8CC38006-6DBA-4D63-9F9E-A0C3B02E5B16"]  # iOS 27.2 24B8117
        self.assertEqual(general.tier, core.PROTECTED)
        self.assertIn("iPhone 18 Pro Max", general.note)
        duo = by_uuid["6FCD9931-A1D9-47D9-9C2F-BF4C3355AEDE"]
        self.assertEqual(duo.tier, core.CAUTION)
        self.assertFalse(duo.preselect)
        self.assertEqual(duo.warning,
                         "Your iPhone Duo simulator stops working. No other installed runtime runs it.")
        watch = by_uuid["0D0A92AB-83AF-4E8B-BCCD-21DABFA6AECC"]
        self.assertEqual(watch.tier, core.CAUTION)
        self.assertIn("Apple Watch Series 12 (46mm)", watch.warning)
        self.assertIn("simulators stop working", watch.warning)

    def test_execute_rechecks_runtime_protection(self):
        """A forged item without the warning the fresh scan shows is refused (Task 2)."""
        sim = incident_sim()
        forged = core.Item(key="runtime:UUID-270", category="Simulator", label="iOS 27.0",
                           tier=core.CAUTION, kind="runtime",
                           argv=["xcrun", "simctl", "runtime", "delete", "UUID-270"],
                           meta={"uuid": "UUID-270"})
        calls = []
        ok, msg = core.execute_item(forged, self.cfg, sim, runner=lambda *a, **k: calls.append(a))
        self.assertFalse(ok)
        self.assertIn("REFUSED", msg)
        self.assertIn("Situation changed: scan again", msg)
        self.assertEqual(calls, [])

    def test_forged_argv_on_clean_key_refused(self):
        """An item keyed as the clean 27.1 runtime but whose argv deletes 27.0 is refused."""
        sim = incident_sim(False)
        sim._list["devices"] = {}
        items, _ = core.scan_runtimes(self.cfg, sim)
        it = next(i for i in items if i.key == "runtime:UUID-271")
        it.argv = ["xcrun", "simctl", "runtime", "delete", "UUID-270"]
        it.meta["uuid"] = "UUID-270"
        with self.assertRaises(core.UnsafeTarget):
            core.validate_item(it, self.cfg, sim)


# ============================================================ choice, warnings, simulators

def booted(sim, udid):
    for devs in sim._list["devices"].values():
        for d in devs:
            if d["udid"] == udid:
                d["state"] = "Booted"
    return sim


class ChoiceAndSimulatorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="devclean-test-"))
        self.home = make_home(self.tmp)
        self.cfg = cfg_for(self.home)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def by_key(self, sim=None):
        res = core.scan(self.cfg, sim or core.NoSimctl(), xcode_check=lambda: False,
                        env=core.Env.fake())
        return {i.key: i for i in res.items}

    def test_incident_fixture_offers_27_0_with_warning(self):
        """Was test_incident_2026_11_02_never_offers_27_0: the runtime the 2026-11-02
        incident deleted is now offered, but never preselected and with a clear warning."""
        it = self.by_key(incident_sim())["runtime:UUID-270"]
        self.assertTrue(it.offerable)
        self.assertEqual(it.tier, core.CAUTION)
        self.assertFalse(it.preselect)
        self.assertTrue(it.warning.startswith(
            "Your iPhone 18 Pro simulator stops working. No other installed runtime runs it."),
            it.warning)
        # 27.0 is also the newest general iOS runtime: both texts, joined by a space.
        self.assertEqual(it.warning, "Your iPhone 18 Pro simulator stops working. No other installed "
                                     "runtime runs it. This is your newest iOS runtime. New simulators need it.")
        self.assertEqual(it.to_dict()["warning"], it.warning)

    def test_newest_ios_runtime_warning(self):
        by = self.by_key(incident_sim(with_18pro_sim=False))
        self.assertEqual(by["runtime:UUID-270"].warning,
                         "This is your newest iOS runtime. New simulators need it.")
        self.assertEqual(by["runtime:UUID-271"].warning,
                         "Your iPhone Duo simulator stops working. No other installed runtime runs it.")

    def test_booted_runtime_still_kept(self):
        it = self.by_key(booted(incident_sim(), "P1"))["runtime:UUID-270"]
        self.assertEqual(it.tier, core.PROTECTED)
        self.assertFalse(it.offerable)
        self.assertIn("iPhone 18 Pro", it.note)

    def test_simulator_devices_listed(self):
        by = self.by_key(incident_sim())
        p1 = by["simdevice:P1"]
        self.assertEqual((p1.tier, p1.kind, p1.category), (core.CAUTION, "simdevice", "Simulator"))
        self.assertEqual(p1.argv, ["xcrun", "simctl", "delete", "P1"])
        self.assertEqual(p1.label, "iPhone 18 Pro (27.0)")
        self.assertFalse(p1.preselect)
        self.assertEqual(p1.cost, "Deletes its installed apps and data. Xcode can create a fresh one.")
        self.assertIn("simdevice:D1", by)
        b = self.by_key(booted(incident_sim(), "P1"))["simdevice:P1"]
        self.assertEqual((b.tier, b.kind), (core.PROTECTED, "info"))
        self.assertEqual(b.note, "Running: shut it down first")

    def test_simulator_mtime_and_size(self):
        sim = incident_sim()
        sim._list["devices"][R + "iOS-27-0"][0]["lastBootedAt"] = "2026-11-01T10:00:00Z"
        mkdir(os.path.join(self.cfg.sim_devices_dir, "P1"))
        p1 = self.by_key(sim)["simdevice:P1"]
        self.assertEqual(p1.mtime, 1793527200.0)
        self.assertGreater(p1.size, 0)

    def test_unavailable_sims_not_listed_twice(self):
        by = self.by_key(orphan_sim())
        self.assertIn("simctl-unavailable", by)
        self.assertNotIn("simdevice:GONE", by)

    def test_unavailable_sim_not_named_in_runtime_warning(self):
        """A simulator whose runtime is already gone can't 'stop working'."""
        by = self.by_key(orphan_sim())
        self.assertNotIn("iPhone 17", by["runtime:UUID-272"].warning)
        self.assertIn("iPhone 18 Pro", by["runtime:UUID-272"].warning)

    def test_runtime_warning_names_only_its_own_simulators(self):
        """The iPad (A16) simulator lives on 26.5: deleting 27.2 doesn't stop it."""
        by = self.by_key(orphan_sim())
        self.assertNotIn("iPad (A16)", by["runtime:UUID-272"].warning)
        self.assertEqual(by["runtime:UUID-265"].warning,
                         "Your iPad (A16) simulator stops working. Xcode can create a new one on iOS 27.2.")
        self.assertEqual(by["runtime:UUID-272"].warning,
                         "Your iPhone 18 Pro simulator stops working. Xcode can create a new one on "
                         "iOS 26.5. " + core.NEWEST_IOS_WARNING)

    def test_runtime_warning_groups_by_wording(self):
        """A runtime hosting both kinds of simulator: 'no other runtime' group first."""
        lst = {"runtimes": [runtime("iOS-26-5", "26.5", "23F77", ["iPhone 15", "iPhone 17"]),
                            runtime("iOS-27-2", "27.2", "24B8117", ["iPhone 17", "iPhone 18 Pro"])],
               "devices": {R + "iOS-26-5": [device("iPhone 17", "B"), device("iPhone 15", "A"),
                                            device("iPhone 15 Work", "C")]}}
        lst["devices"][R + "iOS-26-5"][2]["deviceTypeIdentifier"] = T + "iPhone-15"
        imgs = {"U265": image("iOS-26-5", "26.5", "23F77"), "U272": image("iOS-27-2", "27.2", "24B8117")}
        items, _ = core.scan_runtimes(self.cfg, core.FixtureSimctl(lst, imgs))
        it = next(i for i in items if i.key == "runtime:U265")
        self.assertEqual(it.warning,
                         "Your iPhone 15, iPhone 15 Work simulators stop working. No other installed "
                         "runtime runs them. Your iPhone 17 simulator stops working. Xcode can create "
                         "a new one on iOS 27.2.")

    def test_gate_accepts_shrunk_runtime_warning(self):
        """Fix round 1: deleting a simulator before its runtime only shrinks the runtime's
        warning; the gate accepts that (it refuses only sentences the user never saw)."""
        cfg = self.cfg
        it = next(i for i in core.scan_runtimes(cfg, fixture_sim())[0]
                  if i.key == "runtime:91298CDE-FDEA-4F7C-8FF5-E27CFAC0C3B6")
        sim = fixture_sim()
        devs = sim._list["devices"][R + "iOS-26-5"]
        devs[:] = [d for d in devs if d["name"] != "iPad (A16)"]
        core.validate_item(it, cfg, sim)            # one simulator gone
        devs[:] = []
        core.validate_item(it, cfg, sim)            # all gone: no warning at all

    def test_gate_refuses_new_simulator_on_runtime(self):
        cfg = self.cfg
        it = next(i for i in core.scan_runtimes(cfg, fixture_sim())[0]
                  if i.key == "runtime:91298CDE-FDEA-4F7C-8FF5-E27CFAC0C3B6")
        sim = fixture_sim()
        new = dict(sim._list["devices"][R + "iOS-26-5"][0], name="Kids iPad", udid="NEW1")
        sim._list["devices"][R + "iOS-26-5"].append(new)
        with self.assertRaises(core.UnsafeTarget) as cm:
            core.validate_item(it, cfg, sim)
        self.assertEqual(str(cm.exception), "Situation changed: scan again")

    def test_warning_units(self):
        u = core.warning_units("Your A, B simulators stop working. Xcode can create a new one on "
                               "iOS 27.2. " + core.NEWEST_IOS_WARNING)
        self.assertEqual(u, {("sim", "A", "on:iOS 27.2"), ("sim", "B", "on:iOS 27.2"),
                             "This is your newest iOS runtime", "New simulators need it"})
        self.assertEqual(core.warning_units(""), set())
        self.assertLessEqual(core.warning_units("Your A simulator stops working. No other installed "
                                                "runtime runs it."),
                             core.warning_units("Your A, B simulators stop working. No other "
                                                "installed runtime runs them."))

    def test_newest_gradle_offered_with_warning(self):
        it = self.by_key()["gradle:9.10.0"]
        self.assertEqual(it.tier, core.CAUTION)
        self.assertFalse(it.preselect)
        self.assertEqual(it.warning,
                         "The version your projects probably use. It downloads again on the next build.")
        self.assertEqual(self.by_key()["gradle:9.8.0"].warning, "")

    def test_newest_devicesupport_offered_with_warning(self):
        ds = self.cfg.device_support_dir
        mkdir(os.path.join(ds, "iPhone16,1 27.1 (24A5070a)"))
        mkdir(os.path.join(ds, "iPhone16,1 27.2 (24B119)"))
        by = self.by_key()
        new = by["devsupport:iPhone16,1 27.2 (24B119)"]
        self.assertEqual((new.tier, new.preselect), (core.CAUTION, False))
        self.assertEqual(new.warning, "Re-copied the next time you debug on that iPhone.")
        self.assertEqual(by["devsupport:iPhone16,1 27.1 (24A5070a)"].warning, "")

    def test_android_used_image_offered_with_avd_name(self):
        imgs = make_android(self.tmp, self.home)
        by = {i.path: i for i in self.by_key().values() if i.category == "Android"}
        used = by[imgs["Pixel"]]
        self.assertEqual((used.tier, used.preselect, used.kind), (core.CAUTION, False, "path"))
        self.assertEqual(used.warning, "The Pixel emulator stops working until you download this "
                                       "image again (the SDK Manager does it).")
        unused = by[imgs["unused"]]
        self.assertEqual((unused.tier, unused.warning), (core.CAUTION, ""))

    def test_unreadable_avd_config_warns_every_image(self):
        """Was test_unreadable_protected_avd_blocks_all: an unreadable AVD config now warns on
        every image instead of hiding them all (no AVD names are hard-coded any more)."""
        imgs = make_android(self.tmp, self.home)
        os.remove(os.path.join(self.home, ".android/avd/TV.avd/config.ini"))
        items = core.scan_android(self.cfg)
        self.assertEqual(len(items), 4)
        text = "Couldn't read the settings of emulator TV; it may use this image."
        for it in items:
            self.assertEqual(it.tier, core.CAUTION, it.path)
            self.assertIn(text, it.warning, it.path)
        by = {i.path: i for i in items}
        self.assertEqual(by[imgs["unused"]].warning, text)

    def test_archives_are_review_trash_items(self):
        mkdir(os.path.join(self.home, "Library/Developer/Xcode/Archives/2026-11-01/notes"))
        by = self.by_key()
        it = by["archive:2026-11-01/App.xcarchive"]
        self.assertEqual((it.tier, it.kind, it.preselect), (core.REVIEW, "trash", False))
        self.assertTrue(it.offerable)
        self.assertEqual(it.warning, "Shipped builds. You need them to read crash reports for those versions.")
        self.assertEqual(it.restore, "Moved to the Trash, so you can Put Back.")
        self.assertNotIn("archives", by)
        self.assertNotIn("archive:2026-11-01/notes", by)
        self.assertIsNone(it.command)               # never an rm
        with self.assertRaises(core.UnsafeTarget):  # Archives stay on the rm deny list
            core.validate_path(it.path, self.cfg)
        ok, msg = core.execute_item(it, self.cfg, core.NoSimctl(), runner=lambda *a, **k: 1 / 0)
        self.assertFalse(ok)
        self.assertTrue(os.path.isdir(it.path))

    def test_review_tier_order(self):
        self.assertEqual(core.REVIEW, "review")
        self.assertEqual(core.OFFERABLE_TIERS, (core.SAFE, core.OPTIN, core.CAUTION, core.REVIEW))
        self.assertEqual(core.TIER_ORDER, {core.SAFE: 0, core.OPTIN: 1, core.CAUTION: 2,
                                           core.REVIEW: 3, core.PROTECTED: 4})

    def test_no_personal_constants(self):
        with open(core.__file__) as f:
            src = f.read()
        for name in ("iPhone-18-Pro", "Pixel_10_Pro", "Television_4K"):
            self.assertNotIn(name, src)

    def test_gate_refuses_when_warning_changed(self):
        it = self.by_key(incident_sim(with_18pro_sim=False))["runtime:UUID-270"]
        self.assertNotIn("stops working", it.warning)
        with self.assertRaises(core.UnsafeTarget) as cm:
            core.validate_item(it, self.cfg, incident_sim(True))
        self.assertEqual(str(cm.exception), "Situation changed: scan again")
        core.validate_item(it, self.cfg, incident_sim(False))   # unchanged → accepted

    def test_gate_refuses_in_use(self):
        it = self.by_key(incident_sim())["simdevice:P1"]
        core.validate_item(it, self.cfg, incident_sim())
        calls = []
        ok, msg = core.execute_item(it, self.cfg, booted(incident_sim(), "P1"),
                                    runner=lambda *a, **k: calls.append(a))
        self.assertFalse(ok)
        self.assertIn("Running: shut it down first", msg)
        self.assertEqual(calls, [])

    def test_gate_refuses_vanished_simulator_and_forged_udid(self):
        it = self.by_key(incident_sim())["simdevice:P1"]
        sim = incident_sim()
        sim._list["devices"][R + "iOS-27-0"] = []
        with self.assertRaises(core.UnsafeTarget):
            core.validate_item(it, self.cfg, sim)
        it.argv = ["xcrun", "simctl", "delete", "D1"]
        with self.assertRaises(core.UnsafeTarget):
            core.validate_item(it, self.cfg, incident_sim())

    def test_gate_accepts_warned_items(self):
        imgs = make_android(self.tmp, self.home)
        by = self.by_key(incident_sim())
        for key in ("runtime:UUID-270", "gradle:9.10.0", "simdevice:P1"):
            core.validate_item(by[key], self.cfg, incident_sim())
        used = next(i for i in by.values() if i.path == imgs["Pixel"])
        core.validate_item(used, self.cfg, incident_sim())

    def test_gate_refuses_android_image_whose_warning_appeared(self):
        imgs = make_android(self.tmp, self.home)
        it = next(i for i in self.by_key().values() if i.path == imgs["unused"])
        self.assertEqual(it.warning, "")
        write(os.path.join(self.home, ".android/avd/New.avd/config.ini"),
              "image.sysdir.1=system-images/android-33/google_apis/arm64-v8a/\n")
        with self.assertRaises(core.UnsafeTarget) as cm:
            core.validate_item(it, self.cfg, core.NoSimctl())
        self.assertEqual(str(cm.exception), "Situation changed: scan again")

    def test_rescan_item_unknown_prefix_falls_back_to_full_scan(self):
        by = self.by_key()
        fresh = core.rescan_item(by["homebrew-cache"], self.cfg, core.NoSimctl(), core.Env.fake())
        self.assertEqual(fresh.path, by["homebrew-cache"].path)
        forged = core.Item(key="nope:x", category="x", label="x", tier=core.SAFE, path=self.home)
        self.assertIsNone(core.rescan_item(forged, self.cfg, core.NoSimctl(), core.Env.fake()))

    def test_every_offerable_item_explains_itself(self):
        make_android(self.tmp, self.home)
        mkdir(os.path.join(self.cfg.device_support_dir, "iPhone16,1 27.2 (24B119)"))
        by = self.by_key(orphan_sim())
        by.update(self.by_key(incident_sim()))
        kinds = {i.kind for i in by.values() if i.offerable}
        self.assertTrue({"path", "runtime", "simdevice", "simctl-unavailable", "trash"} <= kinds, kinds)
        for it in by.values():
            if not it.offerable:
                continue
            self.assertTrue(it.cost, it.key)
            self.assertTrue(it.cost_tag and it.cost_tag != "?", it.key)
            self.assertTrue(it.deletes, it.key)
            self.assertTrue(it.restore, it.key)
            d = it.to_dict()
            self.assertEqual((d["deletes"], d["restore"]), (it.deletes, it.restore))
        self.assertEqual(by["homebrew-cache"].deletes, "~/Library/Caches/Homebrew")
        self.assertEqual(by["simdevice:P1"].deletes, "xcrun simctl delete P1")
        self.assertEqual(by["runtime:UUID-270"].restore,
                         "Re-download the runtime in Xcode › Settings › Components.")


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
        """Uses the downloads row: the archives info row no longer exists (Task 2)."""
        by = {i.key: i for i in scan(self.home).items}
        self.assertEqual(by["downloads"].cost_tag, "")
        self.assertEqual(by["downloads"].cost, by["downloads"].note)

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
        # Task 2: the incident runtime is offered, so the CLI must show its warning.
        self.assertIn("runtime delete UUID-270", out)
        self.assertIn("Your iPhone 18 Pro simulator stops working", out)
        self.assertNotIn("None", out.split("Commands:")[1])

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

    def test_cli_yes_never_deletes_new_categories(self):
        cache = mkdir(f"{self.home}/Library/Caches/com.example.tool")
        write(f"{cache}/blob", "x" * 100)
        log = f"{self.home}/Library/Logs/old.log"
        write(log, "x" * 100)
        old = time.time() - 30 * 86400
        os.utime(log, (old, old))
        code, out = self.run_cli("--yes", "--select", "all")
        self.assertEqual(code, 0, out)
        self.assertTrue(os.path.exists(cache), out)
        self.assertTrue(os.path.exists(log), out)
        self.assertFalse(os.path.exists(f"{self.home}/Library/Developer/Xcode/DerivedData"))
        self.assertFalse(os.path.exists(f"{self.home}/Documents/Projects/flutter_app/build"))

    def test_cli_prints_warnings(self):
        fx = os.path.join(self.tmp, "fx")
        os.makedirs(fx)
        sim = incident_sim()
        for name, data in (("simctl_list.json", sim._list), ("runtime_list.json", sim._images)):
            with open(os.path.join(fx, name), "w") as f:
                json.dump(data, f)
        code, out = self.run_cli("--dry-run", "--select", "all", "--simctl-fixture", fx)
        self.assertEqual(code, 0, out)
        self.assertIn("! Your iPhone 18 Pro simulator stops working", out)
        self.assertNotIn("warning: Your iPhone", out)

    def test_cli_trash_item_listed_not_as_rm_and_refused_without_helper(self):
        arch = f"{self.home}/Library/Developer/Xcode/Archives/2026-11-01/App.xcarchive"
        code, out = self.run_cli("--dry-run", "--select", "all")
        self.assertEqual(code, 0, out)
        self.assertIn("(moves to Trash)", out)
        self.assertIn("Moved to Trash (needs the app):", out)
        self.assertFalse([l for l in out.splitlines() if l.startswith("rm ") and "Archives" in l])
        # Executing it: the CLI has no helper, so the core refuses.
        import importlib.machinery, importlib.util, io, contextlib
        loader = importlib.machinery.SourceFileLoader("devclean_cli_under_test", CLI)
        spec = importlib.util.spec_from_loader(loader.name, loader)
        mod = importlib.util.module_from_spec(spec)
        loader.exec_module(mod)
        buf = io.StringIO()
        env = {k: v for k, v in os.environ.items() if k != core.TRASH_HELPER_ENV}
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(sys.stdin, "isatty", return_value=True), \
                mock.patch("builtins.input", return_value="yes"), \
                contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            rc = mod.main(["--home", self.home, "--min-size", "0", "--projects",
                           os.path.join(self.home, "Documents", "Projects"),
                           "--select", "all"])
        self.assertEqual(rc, 1, buf.getvalue())
        self.assertIn("REFUSED: use the app to move these to the Trash", buf.getvalue())
        self.assertTrue(os.path.exists(arch))

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

    def test_add_project_folder_rejects_trash(self):
        os.makedirs(os.path.join(self.home, ".Trash", "oldapp"))
        for bad in (os.path.join(self.home, ".Trash"), os.path.join(self.home, ".Trash", "oldapp")):
            with self.assertRaises(ValueError, msg=bad) as cm:
                core.add_project_folder(self.home, bad)
            self.assertIn(".Trash", str(cm.exception))
        self.assertEqual(core.load_settings(self.home), {})

    def test_add_project_folder_rejects_folder_containing_deny_path(self):
        os.makedirs(os.path.join(self.home, ".ssh"), exist_ok=True)
        with self.assertRaises(ValueError):
            core.add_project_folder(self.home, os.path.join(self.home, ".ssh"))

    def test_downloads_rejected_as_project_root(self):
        d = os.path.join(self.home, "Downloads")
        os.makedirs(os.path.join(d, "proj"), exist_ok=True)
        for bad in (d, os.path.join(d, "proj")):
            with self.assertRaises(ValueError, msg=bad):
                core.add_project_folder(self.home, bad)
        self.assertEqual(core.load_settings(self.home), {})

    # ---- C1: case variants and unchecked roots

    def case_insensitive(self):
        if not os.path.exists(self.home.upper()):
            self.skipTest("temp dir filesystem is case-sensitive")

    def test_case_variant_adds_rejected(self):
        self.case_insensitive()
        for d in ("Desktop", ".Trash", "Downloads"):
            os.makedirs(os.path.join(self.home, d), exist_ok=True)
        for rel in ("downloads", "DOWNLOADS", "library", "LIBRARY/caches", ".trash",
                    "desktop", "downloads/../Library"):
            with self.assertRaises(ValueError, msg=rel):
                core.add_project_folder(self.home, os.path.join(self.home, rel))
        self.assertEqual(core.load_settings(self.home), {})

    def test_check_project_root_returns_canonical_case(self):
        self.case_insensitive()
        P = os.path.join(self.home, "Documents", "Projects")
        self.assertEqual(core.check_project_root(self.home, os.path.join(self.home, "documents", "PROJECTS")), P)
        self.assertEqual(core.check_project_root(self.home, P), P)
        with self.assertRaises(ValueError):
            core.check_project_root(self.home, self.home.upper())

    def test_unchecked_roots_dropped_by_config(self):
        lib_nm = os.path.join(self.home, "Library/Application Support/App/extensions/ext")
        write(os.path.join(lib_nm, "package.json"), "{}")
        mkdir(os.path.join(lib_nm, "node_modules"))
        vs = os.path.join(self.home, ".vscode/extensions/ext")
        write(os.path.join(vs, "package.json"), "{}")
        mkdir(os.path.join(vs, "node_modules"))
        os.makedirs(os.path.join(self.home, "Desktop"), exist_ok=True)
        os.makedirs(os.path.join(self.tmp, "outside"), exist_ok=True)
        for roots in ((self.home,), (os.path.join(self.home, "Library"),),
                      (os.path.join(self.home, "Library/Application Support"),),
                      (os.path.join(self.home, "Downloads"),), (os.path.join(self.home, "Desktop"),),
                      (os.path.join(self.tmp, "outside"),), ("relative",), ("/",)):
            cfg = core.Config(home=self.home, project_roots=roots)
            self.assertEqual(cfg.project_roots, (), roots)
        cfg = core.Config(home=self.home, project_roots=(os.path.join(self.home, "Library"),))
        with self.assertRaises(core.UnsafeTarget):
            core.validate_path(os.path.join(lib_nm, "node_modules"), cfg)

    def test_cli_projects_home_offers_nothing_under_dot_dirs(self):
        vs = os.path.join(self.home, ".vscode/extensions/ext")
        write(os.path.join(vs, "package.json"), "{}")
        mkdir(os.path.join(vs, "node_modules"))
        r = subprocess.run([sys.executable, CLI, "--home", self.home, "--json",
                            "--projects", self.home], capture_output=True, text=True,
                           stdin=subprocess.DEVNULL)
        keys = {i["key"] for i in json.loads(r.stdout)["items"]}
        self.assertFalse([k for k in keys if k.startswith(("web:", "flutter:"))], keys)

    def test_cli_relative_projects_resolve_against_cwd(self):
        r = subprocess.run([sys.executable, CLI, "--home", self.home, "--json", "--projects", "Projects"],
                           capture_output=True, text=True, stdin=subprocess.DEVNULL,
                           cwd=os.path.join(self.home, "Documents"))
        keys = {i["key"] for i in json.loads(r.stdout)["items"]}
        self.assertIn("flutter:Documents/Projects/flutter_app/build", keys)

    def test_validate_path_refuses_case_variant_of_deny_path(self):
        self.case_insensitive()
        proj = os.path.join(self.home, "Downloads", "x")
        write(os.path.join(proj, "package.json"), "{}")
        mkdir(os.path.join(proj, "node_modules"))
        cfg = core.Config(home=self.home, project_roots=())
        cfg.project_roots = (os.path.join(self.home, "downloads"),)   # bypass Config's check
        with self.assertRaises(core.UnsafeTarget):
            core.validate_path(os.path.join(self.home, "downloads", "x", "node_modules"), cfg)
        res = core.scan(cfg, core.NoSimctl(), xcode_check=lambda: False, env=core.Env.fake())
        self.assertFalse([i for i in res.offerable if i.category == "Web"])

    def test_case_variant_roots_dont_duplicate_projects(self):
        self.case_insensitive()
        P = os.path.join(self.home, "Documents", "Projects")
        one = self.scan(core.Config(home=self.home, project_roots=(P,)))
        cfg = core.Config(home=self.home, project_roots=(P, os.path.join(self.home, "documents", "projects"),
                                                         os.path.join(self.home, "DOCUMENTS", "Projects", "web_app")))
        self.assertEqual(cfg.project_roots, (P, os.path.join(P, "web_app")))
        keys = [i.key for i in self.scan(cfg).items if i.category in ("Flutter", "Web")]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(sorted(keys), sorted(i.key for i in one.items if i.category in ("Flutter", "Web")))

    def test_default_dir_and_saved_case_variant_are_one_root(self):
        self.case_insensitive()
        code = os.path.join(self.home, "Code")
        write(os.path.join(code, "app", "package.json"), "{}")
        mkdir(os.path.join(code, "app", "node_modules"))
        core.save_settings(self.home, {"project_folders": [code, os.path.join(self.home, "CODE")]})
        cfg = core.Config(home=self.home)
        self.assertEqual(cfg.project_roots, (code,))
        # Already saved under another case: no new entry.
        self.assertEqual(core.add_project_folder(self.home, os.path.join(self.home, "code")), code)
        self.assertEqual(len(core.load_settings(self.home)["project_folders"]), 2)
        core.remove_project_folder(self.home, os.path.join(self.home, "cOdE"))
        self.assertEqual(core.load_settings(self.home)["project_folders"], [])
        import devclean_report
        self.assertEqual(devclean_report.auto_project_folders(cfg), [code])   # same spelling as the root
        os.symlink(code, os.path.join(self.home, "Developer"))
        self.assertEqual(devclean_report.auto_project_folders(core.Config(home=self.home)), [code])

    def test_own_cache_name_is_case_insensitive(self):
        c = os.path.join(self.home, "Library/Caches/COM.Labyrinth-Apps.Cleaner")
        mkdir(c)
        cfg = cfg_for(self.home)
        res = core.scan(cfg, core.NoSimctl(), xcode_check=lambda: False, env=core.Env.fake())
        self.assertFalse([i for i in res.items if i.path == c])
        with self.assertRaises(core.UnsafeTarget):
            core.validate_path(c, cfg)

    def test_settings_path_and_content_pinned(self):
        P = os.path.join(self.home, "Documents", "Projects")
        core.add_project_folder(self.home, P)
        path = os.path.join(self.home, "Library", "Application Support",
                            "Labyrinth Cleaner", "settings.json")
        self.assertEqual(core.settings_path(self.home), path)
        with open(path) as f:
            self.assertEqual(json.load(f), {"project_folders": [P]})

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

    def test_entry_points_let_dev_tools_decide_on_real_home(self):
        """I2: no explicit SimctlProvider for the real home, so a Mac without developer
        tools never runs xcrun (which would pop the install dialog)."""
        self.assertIsNone(core.pick_simctl(None, no_simulators=False, fake_home=False))
        self.assertIsInstance(core.pick_simctl(None, no_simulators=True, fake_home=False), core.NoSimctl)
        self.assertIsInstance(core.pick_simctl(None, no_simulators=False, fake_home=True), core.NoSimctl)
        self.assertIsInstance(core.pick_simctl(REAL_FIXTURE, no_simulators=False, fake_home=True),
                              core.FixtureSimctl)
        real = core.Config(home=os.path.expanduser("~"), project_roots=())
        with mock.patch.object(core.SimctlProvider, "_json", side_effect=AssertionError("real xcrun")):
            sim, _ = core.resolve_providers(real, core.pick_simctl(None, False, False),
                                            core.Env.fake(dev_tools=lambda: False))
        self.assertIsInstance(sim, core.NoSimctl)
        sim, _ = core.resolve_providers(real, None, core.Env.fake(dev_tools=lambda: True))
        self.assertIs(type(sim), core.SimctlProvider)
        for path in (CLI, os.path.join(ROOT, "devclean_ui.py")):
            with open(path) as f:
                src = f.read()
            self.assertIn("core.pick_simctl(", src, path)
            self.assertNotIn("core.SimctlProvider()", src, path)

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
