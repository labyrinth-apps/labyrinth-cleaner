"""Chromium browsers: on-device AI models and page caches. Fixture folders in a temp home
only: never the real ~/Library/Application Support or any real browser folder."""

import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import devclean_core as core  # noqa: E402
import devclean_browsers as browsers  # noqa: E402
from test_devclean import cfg_for, write  # noqa: E402

CHROME = "com.google.Chrome"
RUNNING_CHROME = [(CHROME, "Google Chrome")]


class BrowserBase(unittest.TestCase):
    def setUp(self):
        self.base = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.base, True)
        self.home = os.path.join(self.base, "home")
        os.makedirs(self.home)
        self.cfg = cfg_for(self.home)
        self.appsup = os.path.join(self.home, "Library", "Application Support")
        self.chrome = os.path.join(self.appsup, "Google", "Chrome")

    def c(self, *parts):
        return os.path.join(self.chrome, *parts)

    def make_chrome(self):
        write(self.c("OptGuideOnDeviceModel", "2026.2.12.1554", "weights.bin"), "w" * 8192)
        write(self.c("OptGuideOnDeviceModel", "2025.1.1.1", "old.bin"), "w" * 10)
        write(self.c("Default", "Preferences"), "{}")
        write(self.c("Default", "Code Cache", "x"), "x" * 4096)
        write(self.c("Default", "GPUCache", "x"), "x" * 4096)
        write(self.c("Default", "Cookies"), "secret")
        write(self.c("Default", "Local Storage", "x"), "secret")
        write(self.c("Default", "IndexedDB", "x"), "secret")
        write(self.c("Profile 1", "Preferences"), "{}")
        write(self.c("Profile 1", "Cache", "x"), "x" * 4096)
        write(self.c("Guest Profile", "Preferences"), "{}")
        write(self.c("Guest Profile", "Cache", "x"), "x" * 4096)
        write(self.c("System Profile", "Preferences"), "{}")
        write(self.c("System Profile", "Cache", "x"), "x" * 4096)
        write(self.c("GrShaderCache", "x"), "x" * 4096)
        write(self.c("Login Data"), "secret")

    def env(self, running=()):
        return core.Env.fake(running_apps=lambda: list(running))

    def rows(self, running=(), warnings=None):
        return {i.key: i for i in browsers.scan_browsers(self.cfg, self.env(running), warnings)}

    def scan(self, running=()):
        res = core.scan(self.cfg, core.NoSimctl(), xcode_check=lambda: False, env=self.env(running))
        return {i.key: i for i in res.items}, res.warnings


class ModelAndCacheTests(BrowserBase):
    def test_models_and_page_caches(self):
        self.make_chrome()
        d = self.rows()
        m = d[f"browser:{CHROME}:model:OptGuideOnDeviceModel"]
        self.assertEqual((m.label, m.tier, m.kind, m.category, m.preselect),
                         ("Chrome: Gemini Nano", core.SAFE, "path", "Browser", False))
        self.assertEqual(m.path, self.c("OptGuideOnDeviceModel"))
        self.assertEqual(m.meta["version"], "2026.2.12.1554")
        self.assertIn("Help me write", m.meta["what"])
        self.assertGreater(m.size, 0)
        caches = d[f"browser:{CHROME}:caches"]
        self.assertEqual((caches.label, caches.tier, caches.kind, caches.path, caches.preselect),
                         ("Chrome page caches", core.SAFE, "paths", self.chrome, False))
        self.assertEqual(caches.argv, ["rm", "-rf", "--", self.c("GrShaderCache"),
                                       self.c("Default", "Code Cache"), self.c("Default", "GPUCache"),
                                       self.c("Profile 1", "Cache")])
        for name in ("Guest Profile", "System Profile", "Cookies", "Local Storage", "IndexedDB",
                     "Login Data"):
            self.assertFalse([a for a in caches.argv if name in a], name)
        self.assertEqual(set(d), {f"browser:{CHROME}:model:OptGuideOnDeviceModel",
                                  f"browser:{CHROME}:caches"})

    def test_rows_survive_full_scan_gate_with_costs(self):
        self.make_chrome()
        d, _ = self.scan()
        m = d[f"browser:{CHROME}:model:OptGuideOnDeviceModel"]
        caches = d[f"browser:{CHROME}:caches"]
        self.assertTrue(m.offerable, m.note)
        self.assertTrue(caches.offerable, caches.note)
        self.assertTrue(m.cost_tag.endswith(" DL"), m.cost_tag)
        self.assertEqual(m.cost, "Google's on-device language model: Help me write, page summaries, "
                                 "tab organising. Chrome downloads it again unless you turn off "
                                 "on-device AI in its settings (Settings › System › On-device AI).")
        self.assertEqual((caches.cost_tag, caches.cost), ("SLOWER", browsers.PAGE_CACHE_COST))
        self.assertEqual(m.restore, "The browser downloads it again unless on-device AI is off.")
        self.assertEqual(caches.restore, "The browser rebuilds them as you browse.")
        self.assertIn("Default/Code Cache", caches.deletes)
        self.assertGreater(caches.size, 0)

    def test_non_chrome_redownload_note_has_no_settings_path(self):
        write(os.path.join(self.appsup, "Vivaldi", "SODA", "1.0", "f"), "x" * 4096)
        d, _ = self.scan()
        m = d["browser:com.vivaldi.Vivaldi:model:SODA"]
        self.assertEqual(m.label, "Vivaldi: Live Caption")
        self.assertEqual(m.cost, "Speech recognition for Live Caption. Vivaldi downloads it again "
                                 "unless you turn off on-device AI in its settings.")

    def test_scam_model_is_caution_with_warning(self):
        write(self.c("OptGuideOnDeviceClassifierModel", "f"), "x" * 4096)
        m = self.rows()[f"browser:{CHROME}:model:OptGuideOnDeviceClassifierModel"]
        self.assertEqual((m.tier, m.label, m.warning, m.preselect),
                         (core.CAUTION, "Chrome: Scam detection model", browsers.SCAM_WARNING, False))
        self.assertEqual(browsers.SCAM_WARNING,
                         "Scam and phishing warnings may be weaker until the browser downloads it again.")
        self.assertIsNone(m.meta["version"])

    def test_empty_model_folder_not_listed(self):
        os.makedirs(self.c("screen_ai"))
        os.makedirs(self.c("SODA", "1.2.3"))
        self.assertEqual(self.rows(), {})

    def test_symlinked_or_file_model_folder_not_listed(self):
        write(os.path.join(self.home, "elsewhere", "f"), "x" * 4096)
        os.makedirs(self.chrome)
        os.symlink(os.path.join(self.home, "elsewhere"), self.c("screen_ai"))
        write(self.c("SODA"), "x" * 4096)
        self.assertEqual(self.rows(), {})

    def test_tables(self):
        self.assertEqual(len(browsers.BROWSERS), 10)
        own = {b[0] for b in browsers.BROWSERS if b[3]}
        self.assertEqual(own, {"Opera", "Opera GX"})
        self.assertEqual({m[0] for m in browsers.MODELS},
                         {"OptGuideOnDeviceModel", "OptGuideOnDeviceClassifierModel",
                          "optimization_guide_model_store", "OnDeviceHeadSuggestModel", "screen_ai",
                          "SODA", "SODALanguagePacks"})
        self.assertEqual([m[2] for m in browsers.MODELS if m[2] != core.SAFE], [core.CAUTION])
        self.assertEqual(tuple(browsers.SHARED_CACHES), ("GrShaderCache", "ShaderCache",
                                                         "GraphiteDawnCache"))
        self.assertEqual(tuple(browsers.PROFILE_CACHES), ("Cache", "Code Cache", "GPUCache",
                                                          "DawnGraphiteCache", "DawnWebGPUCache"))
        self.assertEqual(browsers.HIDDEN_PROFILES, ("System Profile", "Guest Profile"))

    def test_profiles(self):
        self.make_chrome()
        write(self.c("NoPrefs", "Cache", "x"), "x")
        os.symlink(self.c("Default"), self.c("Linked"))
        write(self.c("guest profile", "Preferences"), "{}")
        self.assertEqual(browsers.profiles(self.chrome, False), ["Default", "Profile 1"])
        self.assertEqual(browsers.profiles(os.path.join(self.base, "missing"), False), [])

    def test_hidden_profiles_compared_ignoring_case(self):
        # On a case-sensitive volume the listing could spell them differently.
        self.make_chrome()
        real_listdir = os.listdir

        def listdir(path):
            names = real_listdir(path)
            if path == self.chrome:
                names = [n.lower() if n.endswith(" Profile") else n for n in names]
            return names
        with mock.patch.object(browsers.os, "listdir", listdir):
            self.assertEqual(browsers.profiles(self.chrome, False), ["Default", "Profile 1"])


class RunningTests(BrowserBase):
    def test_running_browser_rows_kept(self):
        self.make_chrome()
        d, _ = self.scan(running=RUNNING_CHROME)
        rows = [i for k, i in d.items() if k.startswith("browser:")]
        self.assertEqual(len(rows), 2)
        for it in rows:
            self.assertEqual((it.tier, it.kind, it.note, it.argv, it.preselect),
                             (core.PROTECTED, "info", "Quit Chrome first", None, False))
        caches = d[f"browser:{CHROME}:caches"]
        # The caches' own size, never the whole browser folder.
        self.assertLess(caches.size, core.du_bytes(self.chrome) - 4096)

    def test_other_chrome_channel_running_does_not_block_chrome(self):
        self.make_chrome()
        d = self.rows(running=[("com.google.Chrome.beta", "Google Chrome Beta"),
                               ("com.google.chrome.canary", "Google Chrome Canary")])
        self.assertTrue(all(i.offerable for i in d.values()))
        d = self.rows(running=[("COM.GOOGLE.CHROME", "Google Chrome")])
        self.assertFalse(any(i.offerable for i in d.values()))

    def test_running_apps_unreadable_offers_nothing(self):
        self.make_chrome()

        def boom():
            raise OSError("lsappinfo failed")
        warnings = []
        items = browsers.scan_browsers(self.cfg, core.Env.fake(running_apps=boom), warnings)
        self.assertEqual(items, [])
        self.assertIn(browsers.BROWSERS_UNLISTED_WARNING, warnings)

    def test_no_browser_folders_no_warning(self):
        def boom():
            raise OSError("lsappinfo failed")
        warnings = []
        self.assertEqual(browsers.scan_browsers(self.cfg, core.Env.fake(running_apps=boom), warnings), [])
        self.assertNotIn(browsers.BROWSERS_UNLISTED_WARNING, warnings)

    def test_gate_refuses_when_browser_started_after_scan(self):
        self.make_chrome()
        d, _ = self.scan()
        for key in (f"browser:{CHROME}:caches", f"browser:{CHROME}:model:OptGuideOnDeviceModel"):
            it = d[key]
            core.validate_item(it, self.cfg, env=self.env())       # passes while Chrome is quit
            with self.assertRaises(core.UnsafeTarget, msg=key):
                core.validate_item(it, self.cfg, env=self.env(RUNNING_CHROME))
            ok, msg = core.execute_item(it, self.cfg, runner=self.fail_runner,
                                        env=self.env(RUNNING_CHROME))
            self.assertFalse(ok)
            self.assertIn("Quit Chrome first", msg)
        self.assertTrue(os.path.isdir(self.c("Default", "Code Cache")))

    def test_gate_refuses_when_running_apps_unreadable_at_clean(self):
        self.make_chrome()
        it = self.scan()[0][f"browser:{CHROME}:caches"]

        def boom():
            raise OSError("lsappinfo failed")
        with self.assertRaises(core.UnsafeTarget) as cm:
            core.validate_item(it, self.cfg, env=core.Env.fake(running_apps=boom))
        self.assertEqual(str(cm.exception), core.SITUATION_CHANGED)

    def fail_runner(self, argv, **kw):
        raise AssertionError(f"must not run {argv}")


class LockedTests(BrowserBase):
    def test_locked_folder_row(self):
        self.make_chrome()
        os.chmod(self.chrome, 0)
        self.addCleanup(os.chmod, self.chrome, stat.S_IRWXU)
        d, _ = self.scan()
        rows = {k: i for k, i in d.items() if k.startswith("browser:")}
        self.assertEqual(list(rows), [f"browser:{CHROME}:locked"])
        it = rows[f"browser:{CHROME}:locked"]
        self.assertEqual((it.label, it.tier, it.kind, it.category, it.note),
                         ("Chrome", core.PROTECTED, "info", "Browser", browsers.LOCKED_NOTE.format(name="Chrome")))
        self.assertEqual(browsers.LOCKED_NOTE.format(name="Chrome"),
                         "Chrome's folder is locked by macOS. Its AI models and caches can't be measured.")
        self.assertIsNone(it.path)

    def test_locked_and_running_says_quit_first(self):
        self.make_chrome()
        os.chmod(self.chrome, 0)
        self.addCleanup(os.chmod, self.chrome, stat.S_IRWXU)
        d = self.rows(running=RUNNING_CHROME)
        self.assertEqual(list(d), [f"browser:{CHROME}:locked"])
        it = d[f"browser:{CHROME}:locked"]
        self.assertEqual((it.tier, it.kind, it.note, it.path),
                         (core.PROTECTED, "info", "Quit Chrome first", None))


class OperaTests(BrowserBase):
    def test_opera_own_profile(self):
        opera = os.path.join(self.appsup, "com.operasoftware.Opera")
        write(os.path.join(opera, "Preferences"), "{}")
        write(os.path.join(opera, "Code Cache", "x"), "x" * 4096)
        write(os.path.join(opera, "Cookies"), "secret")
        self.assertEqual(browsers.profiles(opera, True), [""])
        d, _ = self.scan()
        caches = d["browser:com.operasoftware.Opera:caches"]
        self.assertTrue(caches.offerable, caches.note)
        self.assertEqual((caches.label, caches.argv),
                         ("Opera page caches", ["rm", "-rf", "--", os.path.join(opera, "Code Cache")]))
        self.assertNotIn("browser:com.operasoftware.OperaGX:caches", d)

    def test_own_profile_without_preferences_and_chrome_data_cache_refused(self):
        opera = os.path.join(self.appsup, "com.operasoftware.OperaGX")
        write(os.path.join(opera, "Cache", "x"), "x")
        self.assertEqual(self.rows(), {})
        with self.assertRaises(core.UnsafeTarget):
            core.validate_path(os.path.join(opera, "Cache"), self.cfg)
        self.make_chrome()
        write(self.c("Preferences"), "{}")        # Chrome never keeps a profile in its data folder
        write(self.c("Cache", "x"), "x")
        self.assertFalse([a for a in self.rows()[f"browser:{CHROME}:caches"].argv
                          if a == self.c("Cache")])
        with self.assertRaises(core.UnsafeTarget):
            core.validate_path(self.c("Cache"), self.cfg)


class CleanTests(BrowserBase):
    def test_clean_deletes_only_listed_folders(self):
        self.make_chrome()
        d, _ = self.scan()
        for key in (f"browser:{CHROME}:caches", f"browser:{CHROME}:model:OptGuideOnDeviceModel"):
            ok, msg = core.execute_item(d[key], self.cfg, runner=subprocess.run, env=self.env())
            self.assertTrue(ok, msg)
        for gone in (("GrShaderCache",), ("Default", "Code Cache"), ("Default", "GPUCache"),
                     ("Profile 1", "Cache"), ("OptGuideOnDeviceModel",)):
            self.assertFalse(os.path.exists(self.c(*gone)), gone)
        for kept in (("Default", "Cookies"), ("Default", "Local Storage", "x"),
                     ("Default", "IndexedDB", "x"), ("Default", "Preferences"), ("Login Data",),
                     ("Guest Profile", "Cache", "x"), ("System Profile", "Cache", "x"),
                     ("Profile 1", "Preferences")):
            self.assertTrue(os.path.exists(self.c(*kept)), kept)


class GateTests(BrowserBase):
    def test_symlinked_data_folder_or_cache_refused(self):
        # (a) a profile cache that links elsewhere
        self.make_chrome()
        target = os.path.join(self.home, "Documents", "x")
        write(os.path.join(target, "precious"), "keep me")
        shutil.rmtree(self.c("Default", "Code Cache"))
        os.symlink(target, self.c("Default", "Code Cache"))
        caches = self.rows()[f"browser:{CHROME}:caches"]
        self.assertNotIn(self.c("Default", "Code Cache"), caches.argv)
        with self.assertRaises(core.UnsafeTarget):
            core.validate_path(self.c("Default", "Code Cache"), self.cfg)
        self.assertFalse(browsers.allowed_path(self.c("Default", "Code Cache"), self.cfg))
        # a symlinked profile folder
        os.symlink(self.c("Default"), self.c("Profile 2"))
        self.assertNotIn(self.c("Profile 2", "GPUCache"), self.rows()[f"browser:{CHROME}:caches"].argv)
        self.assertFalse(browsers.allowed_path(self.c("Profile 2", "GPUCache"), self.cfg))
        self.assertTrue(os.path.isfile(os.path.join(target, "precious")))
        # (b) "Google/Chrome" itself a symlink
        shutil.rmtree(self.appsup)
        self._check_symlinked_data_folder()

    def _check_symlinked_data_folder(self):
        real = os.path.join(self.home, "elsewhere", "Chrome")
        write(os.path.join(real, "GrShaderCache", "x"), "x" * 4096)
        write(os.path.join(real, "SODA", "x"), "x" * 4096)
        os.makedirs(os.path.dirname(self.chrome))
        os.symlink(real, self.chrome)
        self.assertEqual(self.rows(), {})
        d, _ = self.scan()
        self.assertFalse([k for k in d if k.startswith("browser:")])
        for p in (self.c("GrShaderCache"), self.c("SODA"), os.path.join(real, "SODA")):
            self.assertFalse(browsers.allowed_path(p, self.cfg), p)
            with self.assertRaises(core.UnsafeTarget, msg=p):
                core.validate_path(p, self.cfg)

    def test_symlinked_parent_folder_skipped(self):
        # "Google" (between the data folder and Application Support) is a symlink
        real = os.path.join(self.home, "elsewhere", "Google")
        write(os.path.join(real, "Chrome", "GrShaderCache", "x"), "x" * 4096)
        os.makedirs(self.appsup)
        os.symlink(real, os.path.join(self.appsup, "Google"))
        self.assertEqual(self.rows(), {})
        self.assertFalse(browsers.allowed_path(self.c("GrShaderCache"), self.cfg))
        self.assertFalse(browsers.allowed_path(os.path.join(real, "Chrome", "GrShaderCache"),
                                               self.cfg))

    def test_allowed_path_rejects_everything_else(self):
        self.make_chrome()
        write(self.c("Default", "Service Worker", "CacheStorage", "x"), "x")
        write(self.c("Default", "Cache", "Cache_Data", "x"), "x")
        write(self.c("Extensions", "x"), "x")
        write(self.c("Default", "Preferences"), "{}")
        write(self.c("guest profile", "Cache", "x"), "x")
        for p in (self.c("GrShaderCache"), self.c("OptGuideOnDeviceModel"),
                  self.c("Default", "Code Cache"), self.c("Profile 1", "Cache")):
            self.assertTrue(browsers.allowed_path(p, self.cfg), p)
            self.assertEqual(core.validate_path(p, self.cfg), p)
        bad = [self.c("Default", "Cookies"), self.c("Default", "Local Storage"),
               self.c("Default", "IndexedDB"), self.c("Guest Profile", "Cache"),
               self.c("System Profile", "Cache"), self.c("guest profile", "Cache"),
               self.chrome, self.c("Default"), self.c("Default", "Preferences"),
               self.c("Login Data"), self.c("Extensions"),
               self.c("Default", "Service Worker", "CacheStorage"),
               self.c("Default", "Cache", "Cache_Data"),
               os.path.join(self.appsup, "Google"), self.appsup]
        for p in bad:
            self.assertFalse(browsers.allowed_path(p, self.cfg), p)
            with self.assertRaises(core.UnsafeTarget, msg=p):
                core.validate_path(p, self.cfg)

    def test_forged_argv_refused(self):
        self.make_chrome()
        caches = self.scan()[0][f"browser:{CHROME}:caches"]
        caches.argv = caches.argv + [self.c("Default", "Cookies")]
        ok, msg = core.execute_item(caches, self.cfg, runner=self.fail_runner, env=self.env())
        self.assertFalse(ok)
        self.assertTrue(msg.startswith("REFUSED"), msg)
        self.assertTrue(os.path.isfile(self.c("Default", "Cookies")))

    def fail_runner(self, argv, **kw):
        raise AssertionError(f"must not run {argv}")


if __name__ == "__main__":
    unittest.main()
