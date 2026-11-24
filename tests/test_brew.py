"""Homebrew runner and parsers. Never runs the real brew: fake runners only."""

import os
import subprocess
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import devclean_brew as brew  # noqa: E402
import devclean_core as core  # noqa: E402

FIXTURES = os.path.join(HERE, "fixtures", "brew")


def read_fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as fh:
        return fh.read()


def ok(argv, out="", err=""):
    return subprocess.CompletedProcess(argv, 0, out, err)


def write(path, text=""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


class ParseTests(unittest.TestCase):
    def test_parse_size(self):
        p = brew.parse_size
        self.assertEqual((p("34.6MB"), p("64B"), p("1.2GB"), p("3KB")), (34600000, 64, 1200000000, 3000))
        self.assertIsNone(p("lots"))

    def test_parse_info(self):
        f = brew.parse_info(read_fixture("info.json"))
        self.assertEqual((f["ffmpeg@6"].version, f["ffmpeg@6"].on_request), ("6.1.6", True))
        self.assertEqual(f["ffmpeg@6"].deps, frozenset({"libonly6", "libshared"}))
        self.assertEqual(f["toolx"].version, "1.0")          # the linked keg
        self.assertNotIn("emptyf", f)                         # nothing installed: skipped
        self.assertTrue(f["oldtool"].deprecated)

    def test_parse_cleanup_counts_only_prefix(self):
        text = read_fixture("cleanup.txt").replace("/PREFIX", "/opt/hb")
        self.assertEqual(brew.parse_cleanup(text, "/opt/hb"), 34600000 + 64)

    def test_parse_lists(self):
        self.assertEqual(brew.parse_would_autoremove(read_fixture("autoremove.txt")), ["orphan"])
        u = read_fixture("uninstall.txt")
        self.assertEqual(brew.parse_would_uninstall(u), ["ffmpeg@6"])
        self.assertEqual(brew.parse_would_autoremove(u), ["libonly6", "orphan"])


class SafetyTests(unittest.TestCase):
    def test_call_sets_brew_env(self):
        rec = []
        b = brew.BrewRunner(path="/x/bin/brew", prefix="/x", run=lambda a, **kw: rec.append((a, kw)) or ok(a), service_dirs=())
        brew.call(b, ["cleanup", "-n"], 20)
        argv, kw = rec[0]
        self.assertEqual(argv, ["/x/bin/brew", "cleanup", "-n"])
        for k in ("HOMEBREW_NO_AUTO_UPDATE", "HOMEBREW_NO_INSTALL_CLEANUP", "HOMEBREW_NO_ANALYTICS",
                  "HOMEBREW_NO_ENV_HINTS", "HOMEBREW_NO_COLOR"):
            self.assertEqual(kw["env"][k], "1")
        self.assertNotIn("HOMEBREW_NO_AUTOREMOVE", brew.BREW_ENV)
        self.assertEqual(kw["timeout"], 20)

    def test_fixture_runner_refuses_brew(self):
        for argv in (["brew", "cleanup"], ["/opt/homebrew/bin/brew", "uninstall", "--formula", "x"]):
            self.assertNotEqual(core.fixture_runner(argv, capture_output=True, text=True).returncode, 0)

    def test_fixture_env_has_no_brew_even_for_real_home(self):
        real = core.Config(home=os.path.expanduser("~"), project_roots=())
        fake = brew.BrewRunner(path="/x/bin/brew", prefix="/x", run=None, service_dirs=())
        with mock.patch.object(brew, "find_brew", return_value=fake):
            self.assertIsNotNone(core.fixture_env(real, lambda: False, fixture=False).brew)
            self.assertIsNone(core.fixture_env(real, lambda: False, fixture=True).brew)
        self.assertIsNone(core.Env.fake().brew)


if __name__ == "__main__":
    unittest.main()
