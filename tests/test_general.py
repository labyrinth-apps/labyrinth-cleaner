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
        sparse(f"{self.dl}/movie.mkv", 600 * MB, 0)
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
            f.write(f'#!/bin/sh\nmkdir -p "{trash}" && mv "$1" "{trash}/"\n')
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

    def test_trash_with_helper_moves_file(self):
        it = self.downloads()["download:Setup.dmg"]
        helper, trash = self.make_helper()
        with mock.patch.dict(os.environ, {core.TRASH_HELPER_ENV: helper}):
            res = core.execute_item(it, self.cfg, core.NoSimctl(), runner=core.fixture_runner,
                                    env=self.env(), now=self.now)
        self.assertEqual(res, (True, "ok"))
        self.assertTrue(os.path.exists(f"{trash}/Setup.dmg"))
        self.assertFalse(os.path.exists(it.path))

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

    def test_real_env_last_used_parses_mdls(self):
        out = mock.Mock(returncode=0, stdout="2026-11-01 10:00:00 +0000\n")
        with mock.patch.object(core.subprocess, "run", return_value=out):
            self.assertAlmostEqual(core.Env.real().last_used("/x"), 1793527200.0, delta=1)
        out = mock.Mock(returncode=0, stdout="(null)")
        with mock.patch.object(core.subprocess, "run", return_value=out):
            self.assertIsNone(core.Env.real().last_used("/x"))
        with mock.patch.object(core.subprocess, "run", side_effect=OSError):
            self.assertIsNone(core.Env.real().last_used("/x"))


if __name__ == "__main__":
    unittest.main()
