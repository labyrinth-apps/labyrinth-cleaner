"""Downloads review list and the move-to-Trash machinery (never the real Trash)."""

import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import devclean_core as core  # noqa: E402
from test_devclean import cfg_for, make_home, write  # noqa: E402

DAY = 86400
MB = 1024 ** 2
REFUSAL = "REFUSED: use the app to move these to the Trash"


def sparse(path, size, age_days=0, now=None):
    write(path, "")
    os.truncate(path, size)
    age(path, age_days, now)


def age(path, days, now=None):
    t = (now or time.time()) - days * DAY
    os.utime(path, (t, t))


class DownloadsBase(unittest.TestCase):
    def setUp(self):
        self.base = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.base, True)
        self.home = make_home(self.base)
        self.cfg = cfg_for(self.home)
        self.dl = os.path.join(self.home, "Downloads")
        self.now = time.time()
        self.last_used = {}
        sparse(f"{self.dl}/Setup.dmg", 1 * MB, 20)
        sparse(f"{self.dl}/new.pkg", 1 * MB, 2)
        sparse(f"{self.dl}/big.zip", 60 * MB, 20)
        sparse(f"{self.dl}/small.zip", 1 * MB, 20)
        sparse(f"{self.dl}/movie.mkv", 600 * MB, 200)
        self.last_used[f"{self.dl}/movie.mkv"] = self.now - 120 * DAY
        sparse(f"{self.dl}/recent.mov", 600 * MB, 0)
        self.last_used[f"{self.dl}/recent.mov"] = self.now - 10 * DAY
        os.makedirs(f"{self.dl}/folder")
        os.symlink(f"{self.dl}/Setup.dmg", f"{self.dl}/link.dmg")
        sparse(f"{self.dl}/a/b/deep.dmg", 1 * MB, 40)

    def env(self):
        return core.Env.fake(last_used=lambda p: self.last_used.get(p))

    def scan(self):
        return core.scan(self.cfg, core.NoSimctl(), xcode_check=lambda: False,
                         env=self.env(), now=self.now)

    def downloads(self):
        return {i.key: i for i in self.scan().items if i.key.startswith("download:")}

    def make_helper(self):
        trash = os.path.join(self.home, ".Trash")
        helper = os.path.join(self.base, "fake-trash-helper")
        with open(helper, "w") as f:
            f.write(f'#!/bin/sh\nshift 2; mkdir -p "{trash}" && mv "$1" "{trash}/"\n')
        os.chmod(helper, 0o755)
        return helper, trash


class DownloadsTests(DownloadsBase):
    def test_installer_rule(self):
        d = self.downloads()
        self.assertEqual(set(d), {"download:Setup.dmg", "download:big.zip", "download:movie.mkv"})
        it = d["download:Setup.dmg"]
        self.assertEqual((it.tier, it.kind, it.category, it.meta["rule"], it.preselect),
                         (core.REVIEW, "trash", "Downloads", "installer", False))
        self.assertEqual(it.path, f"{self.dl}/Setup.dmg")
        self.assertEqual(it.size, 1 * MB)
        self.assertEqual(d["download:big.zip"].meta["rule"], "installer")
        self.assertEqual(it.cost, "Moved to the Trash. You can Put Back.")
        self.assertEqual(it.restore, "Moved to the Trash. You can Put Back.")

    def test_large_untouched_rule(self):
        d = self.downloads()
        it = d["download:movie.mkv"]
        self.assertEqual(it.meta["rule"], "large")
        self.assertEqual(it.size, 600 * MB)
        self.assertAlmostEqual(it.mtime, self.now - 120 * DAY, delta=1)   # last used, not mtime
        self.assertNotIn("download:recent.mov", d)

    def test_both_rules_show_once(self):
        sparse(f"{self.dl}/huge.dmg", 600 * MB, 120)
        items = [i for i in self.scan().items if i.key == "download:huge.dmg"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].meta["rule"], "installer")

    def test_one_level_down_is_offered(self):
        sparse(f"{self.dl}/sub/old.pkg", 1 * MB, 30)
        self.assertIn("download:sub/old.pkg", self.downloads())

    def test_folders_symlinks_and_deep_files_ignored(self):
        d = self.downloads()
        for k in ("download:folder", "download:link.dmg", "download:a/b/deep.dmg"):
            self.assertNotIn(k, d)

    def test_review_items_never_rm(self):
        with self.assertRaises(core.UnsafeTarget):
            core.validate_path(f"{self.dl}/Setup.dmg", self.cfg)

    def test_trash_without_helper_refused(self):
        it = self.downloads()["download:Setup.dmg"]
        env = {k: v for k, v in os.environ.items() if k != core.TRASH_HELPER_ENV}
        with mock.patch.dict(os.environ, env, clear=True):
            res = core.execute_item(it, self.cfg, core.NoSimctl(), runner=core.fixture_runner,
                                    env=self.env(), now=self.now)
        self.assertEqual(res, (False, REFUSAL))
        self.assertTrue(os.path.exists(it.path))

    def test_trash_with_non_executable_helper_refused(self):
        it = self.downloads()["download:Setup.dmg"]
        helper = os.path.join(self.base, "not-exec")
        write(helper, "#!/bin/sh\n")
        with mock.patch.dict(os.environ, {core.TRASH_HELPER_ENV: helper}):
            res = core.execute_item(it, self.cfg, core.NoSimctl(), runner=core.fixture_runner,
                                    env=self.env(), now=self.now)
        self.assertEqual(res, (False, REFUSAL))

    def test_trash_with_relative_helper_refused(self):
        it = self.downloads()["download:Setup.dmg"]
        helper, trash = self.make_helper()
        cwd = os.getcwd()
        os.chdir(os.path.dirname(helper))
        self.addCleanup(os.chdir, cwd)
        with mock.patch.dict(os.environ, {core.TRASH_HELPER_ENV: os.path.basename(helper)}):
            res = core.execute_item(it, self.cfg, core.NoSimctl(), runner=core.fixture_runner,
                                    env=self.env(), now=self.now)
        self.assertEqual(res, (False, REFUSAL))
        self.assertTrue(os.path.isfile(it.path))

    def test_trash_with_helper_moves_file(self):
        it = self.downloads()["download:Setup.dmg"]
        helper, trash = self.make_helper()
        with mock.patch.dict(os.environ, {core.TRASH_HELPER_ENV: helper}):
            res = core.execute_item(it, self.cfg, core.NoSimctl(), runner=core.fixture_runner,
                                    env=self.env(), now=self.now)
        self.assertEqual(res, (True, "ok"))
        self.assertTrue(os.path.exists(f"{trash}/Setup.dmg"))
        self.assertFalse(os.path.exists(it.path))

    def test_trash_item_calls_helper_with_home(self):
        it = self.downloads()["download:Setup.dmg"]
        rec = os.path.join(self.base, "argv.txt")
        helper = os.path.join(self.base, "recording-helper")
        with open(helper, "w") as f:
            f.write(f'#!/bin/sh\nprintf "%s\\n" "$@" > "{rec}"\n')
        os.chmod(helper, 0o755)
        with mock.patch.dict(os.environ, {core.TRASH_HELPER_ENV: helper}):
            ok, msg = core.execute_item(it, self.cfg, core.NoSimctl(), runner=subprocess.run,
                                        env=self.env(), now=self.now)
        self.assertTrue(ok, msg)
        with open(rec) as f:
            self.assertEqual(f.read().splitlines(), ["--home", self.cfg.home, it.path])

    def test_trash_item_refused_when_rule_no_longer_matches(self):
        it = self.downloads()["download:movie.mkv"]
        helper, trash = self.make_helper()
        os.utime(it.path, (self.now, self.now))
        self.last_used[it.path] = self.now
        with mock.patch.dict(os.environ, {core.TRASH_HELPER_ENV: helper}):
            ok, msg = core.execute_item(it, self.cfg, core.NoSimctl(), runner=core.fixture_runner,
                                        env=self.env(), now=self.now)
        self.assertFalse(ok)
        self.assertTrue(msg.startswith("REFUSED"), msg)
        self.assertTrue(os.path.exists(it.path))
        self.assertFalse(os.path.exists(trash))

    def test_trash_refuses_swapped_symlink(self):
        it = self.downloads()["download:Setup.dmg"]
        helper, trash = self.make_helper()
        os.remove(it.path)
        os.symlink(f"{self.home}/Documents/taxes.pdf", it.path)
        with mock.patch.dict(os.environ, {core.TRASH_HELPER_ENV: helper}):
            ok, msg = core.execute_item(it, self.cfg, core.NoSimctl(), runner=core.fixture_runner,
                                        env=self.env(), now=self.now)
        self.assertFalse(ok)
        self.assertTrue(os.path.exists(f"{self.home}/Documents/taxes.pdf"))
        self.assertFalse(os.path.exists(trash))

    def test_forged_downloads_item_for_other_path_refused(self):
        it = self.downloads()["download:Setup.dmg"]
        forged = core.Item(key="download:Setup.dmg", category="Downloads", label="x",
                           tier=core.REVIEW, kind="trash",
                           path=f"{self.home}/Documents/taxes.pdf")
        with self.assertRaises(core.UnsafeTarget):
            core.validate_item(forged, self.cfg, core.NoSimctl(), self.env(), now=self.now)
        core.validate_item(it, self.cfg, core.NoSimctl(), self.env(), now=self.now)

    def test_archive_trash_gate(self):
        arch = {i.key: i for i in self.scan().items}["archive:2026-11-01/App.xcarchive"]
        core.validate_item(arch, self.cfg, core.NoSimctl(), self.env(), now=self.now)
        forged = core.Item(key=arch.key, category="Xcode Archives", label="x", tier=core.REVIEW,
                           kind="trash", path=f"{self.home}/Library/Developer/Xcode",
                           warning=arch.warning)
        with self.assertRaises(core.UnsafeTarget):
            core.validate_item(forged, self.cfg, core.NoSimctl(), self.env(), now=self.now)
        with self.assertRaises(core.UnsafeTarget):
            core.validate_trash(forged, self.cfg, self.env(), self.now)

    # ---- I1: the file must be the one that was scanned

    def trash_it(self, it):
        helper, trash = self.make_helper()
        with mock.patch.dict(os.environ, {core.TRASH_HELPER_ENV: helper}):
            res = core.execute_item(it, self.cfg, core.NoSimctl(), runner=core.fixture_runner,
                                    env=self.env(), now=self.now)
        return res, trash

    def test_modified_after_scan_counts_as_used(self):
        self.last_used[f"{self.dl}/Setup.dmg"] = self.now - 20 * DAY
        it = self.downloads()["download:Setup.dmg"]
        os.utime(it.path, (self.now, self.now))          # written to, never opened
        self.assertIsNone(core._download_item(self.cfg, self.env(), self.now, it.path))
        (ok, msg), trash = self.trash_it(it)
        self.assertFalse(ok)
        self.assertTrue(msg.startswith("REFUSED"), msg)
        self.assertTrue(os.path.isfile(it.path))
        self.assertFalse(os.path.exists(trash))

    def test_replaced_same_name_file_refused(self):
        it = self.downloads()["download:Setup.dmg"]
        st = os.lstat(it.path)
        os.remove(it.path)
        sparse(it.path, st.st_size)
        os.utime(it.path, (st.st_atime, st.st_mtime))   # same size and mtime: only the inode differs
        self.assertNotEqual(os.lstat(it.path).st_ino, st.st_ino)
        (ok, msg), trash = self.trash_it(it)
        self.assertEqual((ok, msg), (False, "REFUSED: " + core.SITUATION_CHANGED))
        self.assertTrue(os.path.isfile(it.path))

    def test_grown_file_refused(self):
        it = self.downloads()["download:big.zip"]
        st = os.lstat(it.path)
        os.truncate(it.path, st.st_size + 1)
        os.utime(it.path, (st.st_atime, st.st_mtime))
        (ok, msg), trash = self.trash_it(it)
        self.assertEqual((ok, msg), (False, "REFUSED: " + core.SITUATION_CHANGED))

    def test_item_without_scan_metadata_refused(self):
        it = self.downloads()["download:Setup.dmg"]
        forged = core.Item(key=it.key, category=it.category, label=it.label, tier=it.tier,
                           kind=it.kind, path=it.path)
        with self.assertRaises(core.UnsafeTarget):
            core.validate_item(forged, self.cfg, core.NoSimctl(), self.env(), now=self.now)
        core.validate_item(it, self.cfg, core.NoSimctl(), self.env(), now=self.now)

    def test_file_swapped_for_folder_refused(self):
        it = self.downloads()["download:Setup.dmg"]
        os.remove(it.path)
        os.makedirs(it.path)
        (ok, msg), trash = self.trash_it(it)
        self.assertFalse(ok)
        self.assertTrue(msg.startswith("REFUSED"), msg)
        self.assertTrue(os.path.isdir(it.path))

    def test_parent_swapped_for_symlink_refused(self):
        sparse(f"{self.dl}/sub/old.pkg", 1 * MB, 30)
        it = self.downloads()["download:sub/old.pkg"]
        elsewhere = os.path.join(self.home, "Documents", "sub")
        os.rename(f"{self.dl}/sub", elsewhere)
        os.symlink(elsewhere, f"{self.dl}/sub")
        (ok, msg), trash = self.trash_it(it)
        self.assertFalse(ok)
        self.assertTrue(msg.startswith("REFUSED"), msg)
        self.assertTrue(os.path.isfile(os.path.join(elsewhere, "old.pkg")))
        self.assertFalse(os.path.exists(trash))

    def test_real_env_last_used_parses_mdls(self):
        out = mock.Mock(returncode=0, stdout="2026-11-01 10:00:00 +0000\n")
        with mock.patch.object(core.subprocess, "run", return_value=out):
            self.assertAlmostEqual(core.Env.real().last_used("/x"), 1793527200.0, delta=1)
        out = mock.Mock(returncode=0, stdout="(null)")
        with mock.patch.object(core.subprocess, "run", return_value=out):
            self.assertIsNone(core.Env.real().last_used("/x"))
        with mock.patch.object(core.subprocess, "run", side_effect=OSError):
            self.assertIsNone(core.Env.real().last_used("/x"))


# ------------------------------------------------------------ Task 4: general categories

import json  # noqa: E402
import subprocess  # noqa: E402

import devclean_general as general  # noqa: E402

DOCKER_DF = "\n".join(json.dumps(r) for r in (
    {"Type": "Images", "Size": "3GB", "Reclaimable": "1.2GB (35%)"},
    {"Type": "Containers", "Size": "1MB", "Reclaimable": "0B (0%)"},
    {"Type": "Local Volumes", "Size": "1GB", "Reclaimable": "1GB (100%)"},
    {"Type": "Build Cache", "Size": "600MB", "Reclaimable": "512MB"},
))


class GeneralBase(unittest.TestCase):
    def setUp(self):
        self.base = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.base, True)
        self.home = make_home(self.base)
        self.cfg = cfg_for(self.home)
        self.now = time.time()
        self.caches = os.path.join(self.home, "Library/Caches")
        for name in ("Google", "com.spotify.client", "com.apple.Safari",
                     "com.labyrinth-apps.cleaner", "Homebrew"):
            os.makedirs(os.path.join(self.caches, name), exist_ok=True)
            write(os.path.join(self.caches, name, "f.bin"), "x" * 2048)
        write(os.path.join(self.caches, "loose-file"), "x")

    def env(self, **kw):
        return core.Env.fake(**kw)

    def scan(self, **kw):
        return core.scan(self.cfg, core.NoSimctl(), xcode_check=lambda: False,
                         env=self.env(**kw), now=self.now)

    def by_key(self, res):
        return {i.key: i for i in res.items}


class AppCacheTests(GeneralBase):
    def test_app_caches_offered_except_apple_and_dev_targets(self):
        d = self.by_key(self.scan())
        for k in ("appcache:Google", "appcache:com.spotify.client"):
            self.assertTrue(d[k].offerable)
            self.assertEqual((d[k].tier, d[k].category, d[k].kind, d[k].preselect),
                             (core.SAFE, "App cache", "path", False))
        for k in ("appcache:com.apple.Safari", "appcache:Homebrew", "appcache:loose-file",
                  "appcache:com.labyrinth-apps.cleaner", "appcache:CocoaPods"):
            self.assertNotIn(k, d)
        self.assertIn("homebrew-cache", d)

    def test_symlinked_cache_not_offered(self):
        os.symlink(self.home, os.path.join(self.caches, "linked"))
        self.assertNotIn("appcache:linked", self.by_key(self.scan()))

    def test_cost_text(self):
        d = self.by_key(self.scan())
        self.assertEqual(d["appcache:com.spotify.client"].cost, "The app rebuilds it as needed.")
        self.assertEqual(d["appcache:Google"].cost,
                         "Pages and images load a bit slower the first time. You stay logged in.")

    def test_cache_blocked_while_owner_runs(self):
        d = self.by_key(self.scan(running_apps=lambda: [("com.spotify.client", "Spotify")]))
        it = d["appcache:com.spotify.client"]
        self.assertEqual((it.tier, it.kind, it.note, it.label),
                         (core.PROTECTED, "info", "Quit Spotify first", "Spotify"))
        self.assertTrue(d["appcache:Google"].offerable)

    def test_cache_publisher_folder_blocked_while_app_runs(self):
        d = self.by_key(self.scan(running_apps=lambda: [("com.google.Chrome", "Google Chrome")]))
        self.assertEqual(d["appcache:Google"].tier, core.PROTECTED)
        self.assertEqual(d["appcache:Google"].note, "Quit Google Chrome first")

    def test_cache_name_match_is_case_insensitive(self):
        d = self.by_key(self.scan(running_apps=lambda: [("com.x.y", "google")]))
        self.assertEqual(d["appcache:Google"].tier, core.PROTECTED)

    def test_helper_suffix_and_publisher_aliases_blocked(self):
        for n in ("com.anthropic.claudefordesktop.ShipIt", "BraveSoftware"):
            os.makedirs(os.path.join(self.caches, n))
        apps = [("com.anthropic.claudefordesktop", "Claude"), ("com.brave.Browser", "Brave Browser")]
        d = self.by_key(self.scan(running_apps=lambda: apps))
        s = d["appcache:com.anthropic.claudefordesktop.ShipIt"]
        self.assertEqual((s.tier, s.note), (core.PROTECTED, "Quit Claude first"))
        b = d["appcache:BraveSoftware"]
        self.assertEqual((b.tier, b.note), (core.PROTECTED, "Quit Brave Browser first"))

    def test_owner_priority_alias_beats_publisher(self):
        apps = [("com.google.antigravity", "Antigravity"), ("com.google.Chrome", "Google Chrome")]
        self.assertEqual(general._owner("Google", apps), ("com.google.Chrome", "Google Chrome"))
        self.assertEqual(general._owner("Google", apps[:1]), apps[0])      # publisher fallback
        self.assertEqual(general._owner("com.google.Chrome", apps)[1], "Google Chrome")
        self.assertIsNone(general._owner("Other", apps))

    def test_cache_gate_rechecks_running_app(self):
        it = self.by_key(self.scan())["appcache:Google"]
        env = self.env(running_apps=lambda: [("com.google.Chrome", "Google Chrome")])
        ok, msg = core.execute_item(it, self.cfg, runner=self.fail_runner, env=env, now=self.now)
        self.assertFalse(ok)
        self.assertTrue(msg.startswith("REFUSED"), msg)
        self.assertTrue(os.path.isdir(it.path))

    def test_cache_cleaned_when_nothing_runs(self):
        it = self.by_key(self.scan())["appcache:Google"]
        ok, msg = core.execute_item(it, self.cfg, env=self.env(), now=self.now)
        self.assertTrue(ok, msg)
        self.assertFalse(os.path.exists(it.path))

    def fail_runner(self, argv, **kw):
        raise AssertionError(f"ran {argv}")

    def test_unlistable_running_apps_fails_closed_with_warning(self):
        def boom():
            raise OSError("lsappinfo missing")
        res = self.scan(running_apps=boom)
        self.assertFalse([i for i in res.items if i.key.startswith("appcache:") and i.offerable])
        self.assertIn("couldn't list running apps; app caches are not offered", res.warnings)
        # and the gate agrees
        ok, msg = core.execute_item(
            core.Item(key="appcache:Google", category="App cache", label="Google",
                      tier=core.SAFE, path=os.path.join(self.caches, "Google")),
            self.cfg, env=self.env(running_apps=boom), now=self.now)
        self.assertFalse(ok)
        self.assertTrue(os.path.isdir(os.path.join(self.caches, "Google")))

    def test_gate_rejects_forged_paths(self):
        for path in (self.caches, os.path.join(self.caches, "com.apple.Safari"),
                     os.path.join(self.caches, "Google", "f.bin")):
            with self.assertRaises(core.UnsafeTarget):
                core.validate_path(path, self.cfg)
        core.validate_path(os.path.join(self.caches, "Google"), self.cfg)

    # ---- I3: macOS daemon caches

    SYSTEM = ("CloudKit", "PassKit", "GeoServices", "FamilyCircle", "familycircled",
              "askpermissiond", "SiriTTS", "softwareupdate", "TemporaryItems", "ssu",
              "mbuseragent", "AMSDataMigratorTool", "Animoji", "GameKit", "EnergyKit")

    def test_macos_system_caches_never_shown(self):
        for n in self.SYSTEM:
            write(os.path.join(self.caches, n, "f.bin"))
        res = self.scan()
        shown = {i.label for i in res.items if i.key.startswith("appcache:")}
        self.assertFalse(shown & set(self.SYSTEM), shown)
        self.assertFalse([i for i in res.items if i.path and os.path.basename(i.path) in self.SYSTEM])
        for n in self.SYSTEM:
            with self.assertRaises(core.UnsafeTarget, msg=n):
                core.validate_path(os.path.join(self.caches, n), self.cfg)

    def test_system_cache_names_are_case_insensitive(self):
        write(os.path.join(self.caches, "cloudkit", "f.bin"))
        write(os.path.join(self.caches, "SIRITTS", "f.bin"))
        keys = set(self.by_key(self.scan()))
        self.assertNotIn("appcache:cloudkit", keys)
        self.assertNotIn("appcache:SIRITTS", keys)
        with self.assertRaises(core.UnsafeTarget):
            core.validate_path(os.path.join(self.caches, "cloudkit"), self.cfg)

    def test_unidentified_cache_is_check_first_with_warning(self):
        for n in ("segment", "io.sentry", "main.kts.compiled.cache", "Arc", "BraveSoftware",
                  "org.swift.swiftpm", "company.thebrowser.dia"):
            write(os.path.join(self.caches, n, "f.bin"))
        d = self.by_key(self.scan())
        for n in ("segment", "io.sentry", "main.kts.compiled.cache"):
            it = d["appcache:" + n]
            self.assertEqual((it.tier, it.preselect, it.kind, it.warning),
                             (core.CAUTION, False, "path", general.UNKNOWN_CACHE_WARNING), n)
            self.assertEqual(general.UNKNOWN_CACHE_WARNING,
                             "macOS or a background tool may use this cache.")
            core.validate_item(it, self.cfg, core.NoSimctl(), self.env(), now=self.now)
        for n in ("Google", "com.spotify.client", "Arc", "BraveSoftware", "org.swift.swiftpm",
                  "company.thebrowser.dia"):
            it = d["appcache:" + n]
            self.assertEqual((it.tier, it.warning), (core.SAFE, ""), n)

    def test_parse_lsappinfo(self):
        text = ('10) "Finder" ASN:0x0-0x1001:\n    bundleID="com.apple.finder"\n    pid = 1\n'
                ' 11) "Google Chrome" ASN:0x0-0x2:\n    bundleID="com.google.Chrome"\n'
                ' 12) "NoBundle" ASN:0x0-0x3:\n    bundleID=[ NULL ]\n')
        self.assertEqual(general.parse_lsappinfo(text),
                         [("com.apple.finder", "Finder"), ("com.google.Chrome", "Google Chrome")])
        with self.assertRaises(ValueError):
            general.parse_lsappinfo("")

    def test_real_running_apps_uses_lsappinfo_output(self):
        out = subprocess.CompletedProcess([], 0, '1) "A" ASN:x:\n bundleID="a.b.c"\n', "")
        with mock.patch.object(general.subprocess, "run", return_value=out) as run:
            self.assertEqual(general.running_apps(), [("a.b.c", "A")])
        self.assertEqual(run.call_args[0][0], ["lsappinfo", "list"])
        with mock.patch.object(general.subprocess, "run", side_effect=OSError):
            with self.assertRaises(OSError):
                general.running_apps()


class WarnedTagTests(GeneralBase):
    def test_warned_no_cost_row_shows_see_warning(self):
        write(os.path.join(self.caches, "segment", "f.bin"), "x" * 3000)
        it = self.by_key(self.scan())["appcache:segment"]
        self.assertEqual(it.warning, general.UNKNOWN_CACHE_WARNING)
        self.assertEqual(it.cost_tag, "SEE WARNING")

    def test_unwarned_no_cost_row_keeps_no_cost(self):
        it = core.Item(key="homebrew-cache", category="x", label="x", tier=core.CAUTION,
                       kind="path", path="/x")
        self.assertEqual(core.cost_for(it)[0], "NO COST")


class LogTests(GeneralBase):
    def test_logs_older_than_7_days_only(self):
        logs = os.path.join(self.home, "Library/Logs")
        write(f"{logs}/old.log")
        age(f"{logs}/old.log", 8, self.now)
        write(f"{logs}/new.log")
        write(f"{logs}/DiagnosticReports/crash.ips")
        age(f"{logs}/DiagnosticReports/crash.ips", 30, self.now)
        write(f"{logs}/DiagnosticReports/fresh.ips")
        write(f"{logs}/OldDir/a.log")
        age(f"{logs}/OldDir", 9, self.now)
        os.makedirs(f"{logs}/DiagnosticReports", exist_ok=True)
        age(f"{logs}/DiagnosticReports", 50, self.now)
        d = self.by_key(self.scan())
        got = {k for k in d if k.startswith("log:")}
        self.assertEqual(got, {"log:old.log", "log:DiagnosticReports/crash.ips", "log:OldDir"})
        it = d["log:old.log"]
        self.assertEqual((it.tier, it.preselect, it.cost, it.category),
                         (core.SAFE, False, "Old logs. Only useful when debugging a crash.", "Logs"))
        self.assertEqual(it.size, 100)
        ok, msg = core.execute_item(it, self.cfg, env=self.env(), now=self.now)
        self.assertTrue(ok, msg)
        self.assertFalse(os.path.exists(it.path))

    def test_log_gate_refuses_diagnostic_reports_folder_itself(self):
        write(f"{self.home}/Library/Logs/DiagnosticReports/c.ips")
        with self.assertRaises(core.UnsafeTarget):
            core.validate_path(f"{self.home}/Library/Logs/DiagnosticReports", self.cfg)
        core.validate_path(f"{self.home}/Library/Logs/DiagnosticReports/c.ips", self.cfg)


class TrashBackupTests(GeneralBase):
    def test_empty_trash_item_and_exact_argv(self):
        write(f"{self.home}/.Trash/junk.bin", "x" * 5000)
        it = self.by_key(self.scan())["empty-trash"]
        self.assertEqual((it.tier, it.kind, it.preselect), (core.CAUTION, "empty-trash", False))
        self.assertEqual(it.argv, general.TRASH_ARGV)
        self.assertEqual(it.cost, "Gone for good. You can't Put Back afterwards.")
        self.assertGreater(it.size, 0)
        ran = []

        def runner(argv, **kw):
            ran.append(argv)
            return subprocess.CompletedProcess(argv, 0, "", "")
        ok, msg = core.execute_item(it, self.cfg, runner=runner, env=self.env(), now=self.now)
        self.assertTrue(ok, msg)
        self.assertEqual(ran, [it.argv])
        forged = core.Item(key="empty-trash", category="Trash", label="Trash", tier=core.CAUTION,
                           kind="empty-trash", argv=["osascript", "-e", "do shell script \"x\""])
        ok, msg = core.execute_item(forged, self.cfg, runner=runner, env=self.env(), now=self.now)
        self.assertFalse(ok)
        self.assertEqual(len(ran), 1)

    def test_empty_trash_argv_has_long_timeout(self):
        self.assertEqual(general.TRASH_ARGV[2], "with timeout of 3600 seconds")
        self.assertEqual(general.TRASH_ARGV[-1], "end timeout")

    def failing_empty_trash(self, stderr):
        write(f"{self.home}/.Trash/junk.bin", "x" * 5000)
        it = self.by_key(self.scan())["empty-trash"]

        def runner(argv, **kw):
            return subprocess.CompletedProcess(argv, 1, "", stderr)
        return core.execute_item(it, self.cfg, runner=runner, env=self.env(), now=self.now)

    def test_empty_trash_automation_denied_message(self):
        ok, msg = self.failing_empty_trash("execution error: Not authorized (-1743)")
        self.assertFalse(ok)
        self.assertIn("Privacy & Security \u2192 Automation", msg)

    def test_empty_trash_timeout_message(self):
        ok, msg = self.failing_empty_trash("execution error: AppleEvent timed out. (-1712)")
        self.assertFalse(ok)
        self.assertIn("still emptying", msg)

    def test_empty_trash_other_error_passes_through(self):
        ok, msg = self.failing_empty_trash("boom")
        self.assertEqual((ok, msg), (False, "boom"))

    def test_empty_trash_not_offered_when_empty_or_missing(self):
        self.assertNotIn("empty-trash", self.by_key(self.scan()))
        os.makedirs(f"{self.home}/.Trash")
        self.assertNotIn("empty-trash", self.by_key(self.scan()))

    @unittest.skipIf(os.geteuid() == 0, "root reads everything")
    def test_trash_size_unreadable_still_offered(self):
        os.makedirs(f"{self.home}/.Trash")
        write(f"{self.home}/.Trash/a")
        os.chmod(f"{self.home}/.Trash", 0)
        self.addCleanup(os.chmod, f"{self.home}/.Trash", 0o755)
        it = self.by_key(self.scan())["empty-trash"]
        self.assertIsNone(it.size)
        self.assertTrue(it.offerable)
        self.assertEqual(it.note, "Trash: size unavailable")

    def test_backups_info_only(self):
        self.assertNotIn("ios-backups", self.by_key(self.scan()))
        b = f"{self.home}/Library/Application Support/MobileSync/Backup"
        write(f"{b}/UDID/Manifest.db", "x" * 3000)
        it = self.by_key(self.scan())["ios-backups"]
        self.assertEqual((it.tier, it.kind, it.offerable), (core.PROTECTED, "info", False))
        self.assertEqual(it.note, "Manage them in Finder → your iPhone → Manage Backups.")
        self.assertGreater(it.size, 0)

    @unittest.skipIf(os.geteuid() == 0, "root reads everything")
    def test_backups_unreadable(self):
        b = f"{self.home}/Library/Application Support/MobileSync/Backup"
        write(f"{b}/UDID/Manifest.db")
        os.chmod(b, 0)
        self.addCleanup(os.chmod, b, 0o755)
        it = self.by_key(self.scan())["ios-backups"]
        self.assertIsNone(it.size)
        self.assertEqual(it.note, "Size unavailable without Full Disk Access.")


class DockerTests(GeneralBase):
    def docker(self, out=DOCKER_DF, code=0, record=None):
        def run(argv, **kw):
            if record is not None:
                record.append((argv, kw))
            return subprocess.CompletedProcess(argv, code, out, "")
        return run

    def test_docker_items_from_df_json(self):
        rec = []
        d = self.by_key(self.scan(docker=self.docker(record=rec)))
        b, i = d["docker:builder"], d["docker:images"]
        self.assertEqual((b.tier, b.kind, b.size, b.argv),
                         (core.OPTIN, "docker", 512 * 1000 ** 2, ["docker", "builder", "prune", "-f"]))
        self.assertEqual((i.tier, i.kind, i.size, i.argv),
                         (core.CAUTION, "docker", int(1.2 * 1000 ** 3),
                          ["docker", "image", "prune", "-a", "-f"]))
        self.assertEqual(b.cost, "The next image build starts from scratch.")
        self.assertEqual(i.cost, "Images are downloaded again the next time a container needs them.")
        self.assertFalse(b.preselect or i.preselect)
        self.assertEqual(rec[0][0], ["docker", "system", "df", "--format", "json"])
        self.assertEqual(rec[0][1]["timeout"], 3)

    def test_docker_size_parser(self):
        p = general.parse_docker_size
        self.assertEqual(p("1.2GB (35%)"), 1200000000)
        self.assertEqual(p("512MB"), 512000000)
        self.assertEqual(p("0B"), 0)
        self.assertEqual(p("3.5kB"), 3500)
        self.assertIsNone(p("lots"))

    def test_docker_down_or_slow_shows_info_row(self):
        def slow(argv, **kw):
            raise subprocess.TimeoutExpired(argv, 3)
        for runner in (slow, self.docker(code=1), self.docker(out="not json")):
            res = self.scan(docker=runner)
            row = self.by_key(res)["docker"]
            self.assertEqual((row.tier, row.kind, row.note),
                             (core.PROTECTED, "info", "Start Docker to see what can be cleaned."))
            self.assertFalse([i for i in res.items if i.key.startswith("docker:")])

    def test_no_docker_no_rows(self):
        d = self.by_key(self.scan())
        self.assertFalse([k for k in d if k.startswith("docker")])

    def test_docker_run_goes_through_env_docker_not_runner(self):
        rec = []
        env = self.env(docker=self.docker(record=rec))
        it = self.by_key(core.scan(self.cfg, core.NoSimctl(), env=env, now=self.now))["docker:builder"]
        rec.clear()

        def general_runner(argv, **kw):
            raise AssertionError("docker went through the general runner")
        ok, msg = core.execute_item(it, self.cfg, runner=general_runner, env=env, now=self.now)
        self.assertTrue(ok, msg)
        self.assertEqual(rec[-1][0], it.argv)
        self.assertEqual(len(rec), 2)   # the gate's re-scan, then the prune

    def test_docker_gate_exact_argv(self):
        env = self.env(docker=self.docker())
        for argv in (["docker", "system", "prune", "-a", "--volumes"],
                     ["docker", "builder", "prune", "-f", "-a"]):
            forged = core.Item(key="docker:builder", category="Docker", label="x",
                               tier=core.OPTIN, kind="docker", argv=argv)
            ok, msg = core.execute_item(forged, self.cfg, env=env, now=self.now)
            self.assertFalse(ok)
            self.assertTrue(msg.startswith("REFUSED"), msg)
        forged = core.Item(key="docker:images", category="Docker", label="x", tier=core.CAUTION,
                           kind="docker", argv=["docker", "builder", "prune", "-f"])
        self.assertFalse(core.execute_item(forged, self.cfg, env=env, now=self.now)[0])

    def test_docker_refused_without_env_docker(self):
        it = self.by_key(self.scan(docker=self.docker()))["docker:builder"]
        ok, msg = core.execute_item(it, self.cfg, env=self.env(), now=self.now)
        self.assertFalse(ok)

    def test_fixture_mode_refuses_docker_and_trash_even_with_live_env(self):
        rec = []
        env = self.env(docker=self.docker(record=rec))
        write(f"{self.home}/.Trash/x")
        res = core.scan(self.cfg, core.NoSimctl(), env=env, now=self.now)
        d = self.by_key(res)
        rec.clear()
        for k in ("docker:builder", "empty-trash"):
            ok, msg = core.execute_item(d[k], self.cfg, runner=core.fixture_runner, env=env,
                                        now=self.now)
            self.assertFalse(ok)
            self.assertIn("fixture mode", msg)
        self.assertEqual(rec, [])

    def test_fixture_env_has_no_docker_even_for_real_home(self):
        real = core.Config(home=os.path.expanduser("~"), project_roots=())
        with mock.patch.object(core.shutil, "which", return_value="/usr/bin/docker"):
            self.assertIsNotNone(core.default_env(real).docker)
            env = core.fixture_env(real, lambda: False, fixture=True)
            self.assertIsNone(env.docker)
            self.assertIsNotNone(core.fixture_env(real, lambda: False, fixture=False).docker)

    def test_fixture_runner_refuses_osascript_and_docker(self):
        for argv in (["osascript", "-e", "x"], ["docker", "ps"], ["xcrun", "simctl"]):
            r = core.fixture_runner(argv, capture_output=True, text=True)
            self.assertNotEqual(r.returncode, 0)


class OrderingTests(unittest.TestCase):
    def it(self, key, kind="path", **meta):
        return core.Item(key=key, category="c", label=key, tier=core.CAUTION, kind=kind, meta=meta)

    def keys(self, items):
        return [i.key for i in core.execution_order(items)]

    def test_empty_trash_runs_before_moves(self):
        dl = self.it("download:a.dmg", "trash")
        et = self.it("empty-trash", "empty-trash")
        rt = self.it("runtime:U1", "runtime", version="17.0")
        un = self.it("simctl-unavailable", "simctl-unavailable")
        self.assertEqual(core.execution_order([dl, et, rt, un]), [et, un, dl, rt])

    def test_simulators_before_runtimes(self):
        rt = self.it("runtime:U1", "runtime", version="17.0")
        sd = self.it("simdevice:D1", "simdevice")
        self.assertEqual(self.keys([rt, sd]), ["simdevice:D1", "runtime:U1"])

    def test_runtimes_by_version_ascending(self):
        a = self.it("runtime:A", "runtime", version="18.10")
        b = self.it("runtime:B", "runtime", version="18.2")
        c = self.it("runtime:C", "runtime", version="17.5")
        self.assertEqual(self.keys([a, b, c]), ["runtime:C", "runtime:B", "runtime:A"])

    def test_gradle_versions_ascending(self):
        items = [self.it(f"gradle:{v}") for v in ("9.10.0", "8.14", "9.8.0")]
        self.assertEqual(self.keys(items), ["gradle:8.14", "gradle:9.8.0", "gradle:9.10.0"])

    def test_devsupport_oldest_first_per_model(self):
        names = ["iPhone15,2 17.5 (21F79)", "iPhone15,2 16.4 (20E247)",
                 "iPhone14,5 17.0 (21A329)", "iPhone14,5 17.0 (21A5248v)"]
        got = self.keys([self.it(f"devsupport:{n}") for n in names])
        order = [g[len("devsupport:"):] for g in got]
        self.assertLess(order.index(names[1]), order.index(names[0]))
        self.assertLess(order.index(names[3]), order.index(names[2]))   # beta before release

    def test_other_items_keep_given_order(self):
        items = [self.it("web:b/node_modules"), self.it("gradle:9.0"), self.it("npm-cache"),
                 self.it("gradle:8.0")]
        self.assertEqual(self.keys(items),
                         ["web:b/node_modules", "gradle:8.0", "npm-cache", "gradle:9.0"])


class NewCategoriesTests(GeneralBase):
    def test_new_categories_never_preselected(self):
        write(f"{self.home}/Library/Logs/old.log")
        age(f"{self.home}/Library/Logs/old.log", 20, self.now)
        write(f"{self.home}/.Trash/x")
        res = self.scan(docker=lambda argv, **kw: subprocess.CompletedProcess(argv, 0, DOCKER_DF, ""))
        new = [i for i in res.items if i.key.startswith(
            ("appcache:", "log:", "empty-trash", "docker", "ios-backups"))]
        self.assertGreaterEqual(len(new), 5)
        self.assertFalse(any(i.preselect for i in new))
        self.assertFalse(any(i.to_dict()["preselect"] for i in new))


if __name__ == "__main__":
    unittest.main()
