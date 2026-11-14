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


if __name__ == "__main__":
    unittest.main()
