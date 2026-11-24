"""Homebrew runner and parsers. Never runs the real brew: fake runners only."""

import dataclasses
import json
import os
import shutil
import subprocess
import sys
import tempfile
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


class ParseFallbackTests(unittest.TestCase):
    def test_null_linked_keg_uses_newest_keg(self):
        info = {"formulae": [{"name": "t", "full_name": "t", "linked_keg": None,
                              "installed": [{"version": "1.9", "installed_on_request": True},
                                            {"version": "1.10", "installed_on_request": True}]}]}
        self.assertEqual(brew.parse_info(json.dumps(info))["t"].version, "1.10")


# ---------------------------------------------------------------- scan

SIZES = {"ffmpeg@6": 5000, "ffmpeg@8": 7000, "libonly6": 3000, "libshared": 1100, "orphan": 2200,
         "python@3.11": 9000, "oldtool": 400, "toolx": 600, "postgresql@15": 8000,
         "Weird_Name": 300, "openssl@3": 1500}
SIZES_CASK = 4500
VERSIONS = {"ffmpeg@6": "6.1.6", "ffmpeg@8": "8.0.1", "libonly6": "1.0", "libshared": "1.0",
            "orphan": "0.1", "python@3.11": "3.11.14", "oldtool": "1.0", "toolx": "1.0",
            "postgresql@15": "15.14", "Weird_Name": "1.0", "openssl@3": "3.5.0"}


def tree_bytes(path):
    """A du stand-in: the file bytes under path, so tests don't depend on disk blocks."""
    total = 0
    for root, _dirs, files in os.walk(path):
        total += sum(os.path.getsize(os.path.join(root, f)) for f in files)
    return total


def fake_brew(prefix, answers, record=None, broken=None, uninstall_stderr=None):
    """A runner for brew: answers the four read-only forms from `answers` (text with /PREFIX),
    accepts the two write forms without touching anything, refuses everything else."""
    path = os.path.join(prefix, "bin", "brew")
    read_only = {
        (path, "info", "--json=v2", "--installed"): "info",
        (path, "cleanup", "-n"): "cleanup",
        (path, "autoremove", "-n"): "autoremove",
    }

    def run(argv, **kw):
        if record is not None:
            record.append((list(argv), kw))
        t = tuple(argv)
        which = read_only.get(t)
        if which is None and t[:3] == (path, "uninstall", "--dry-run") and t[3:4] == ("--formula",):
            which = "uninstall"
        if which is not None:
            if which == broken:
                return subprocess.CompletedProcess(argv, 1, "", "boom")
            return ok(argv, answers[which].replace("/PREFIX", prefix))
        if list(argv) == [path, "cleanup"]:
            return ok(argv)
        if len(argv) == 4 and argv[:3] == [path, "uninstall", "--formula"]:
            if uninstall_stderr is not None:
                return subprocess.CompletedProcess(argv, 1, "", uninstall_stderr)
            return ok(argv)
        return subprocess.CompletedProcess(argv, 1, "", "unexpected: " + " ".join(argv))
    return run


class BrewBase(unittest.TestCase):
    def setUp(self):
        self.root = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.home = os.path.join(self.root, "home")
        os.makedirs(self.home)
        self.prefix = os.path.join(self.root, "hb")
        self.brew_path = os.path.join(self.prefix, "bin", "brew")
        for name, size in SIZES.items():
            write(os.path.join(self.prefix, "Cellar", name, VERSIONS[name], "bin", "x"), "x" * size)
        write(os.path.join(self.prefix, "Caskroom", "sdk", "f"), "x" * SIZES_CASK)
        self.services = os.path.join(self.root, "services")
        write(os.path.join(self.services, "homebrew.mxcl.postgresql@15.plist"))
        self.cfg = core.Config(home=self.home)
        self.now = 1730000000.0
        p = mock.patch.object(core, "du_bytes", tree_bytes)
        p.start()
        self.addCleanup(p.stop)

    def answers(self):
        return {"info": read_fixture("info.json"), "cleanup": read_fixture("cleanup.txt"),
                "autoremove": read_fixture("autoremove.txt"), "uninstall": read_fixture("uninstall.txt")}

    def env(self, record=None, broken=None, uninstall_stderr=None, info_text=None, **answers):
        a = self.answers()
        if info_text is not None:
            a["info"] = info_text
        a.update(answers)
        runner = brew.BrewRunner(
            path=self.brew_path, prefix=self.prefix, service_dirs=(self.services,),
            run=fake_brew(self.prefix, a, record, broken, uninstall_stderr))
        return dataclasses.replace(core.Env.fake(), brew=runner)

    def scan(self, broken=None, **kw):
        return core.scan(self.cfg, core.NoSimctl(), xcode_check=lambda: False,
                         env=self.env(broken=broken, **kw), now=self.now)

    def by_key(self, res):
        return {i.key: i for i in res.items if i.key.startswith("brew:")}


class ScanBrewTests(BrewBase):
    def test_listed_packages_and_tiers(self):
        d = self.by_key(self.scan())
        pkgs = {k for k in d if k.startswith("brew:pkg:")}
        self.assertEqual(pkgs, {"brew:pkg:ffmpeg@6", "brew:pkg:ffmpeg@8", "brew:pkg:python@3.11",
                                "brew:pkg:oldtool", "brew:pkg:toolx", "brew:pkg:postgresql@15",
                                "brew:pkg:Weird_Name"})
        f6 = d["brew:pkg:ffmpeg@6"]
        self.assertEqual((f6.tier, f6.kind, f6.preselect, f6.argv),
                         (core.CAUTION, "brew", False, [self.brew_path, "uninstall", "--formula", "ffmpeg@6"]))
        self.assertEqual(f6.category, "Homebrew")
        self.assertEqual(f6.size, SIZES["ffmpeg@6"] + SIZES["libonly6"])   # libshared is shared, orphan is autoremove's
        self.assertEqual(f6.meta["libraries"], ["libonly6"])
        self.assertEqual(f6.meta["version"], "6.1.6")
        self.assertNotIn("brew:pkg:libshared", d)

    def test_notes(self):
        d = self.by_key(self.scan())
        self.assertIn("older version · ffmpeg 8 also installed", d["brew:pkg:ffmpeg@6"].note)
        self.assertIn("also installed: ffmpeg@6", d["brew:pkg:ffmpeg@8"].note)
        self.assertIn("Projects or scripts you run may use it", d["brew:pkg:python@3.11"].note)
        self.assertIn("Homebrew no longer maintains it", d["brew:pkg:oldtool"].note)
        self.assertTrue(d["brew:pkg:ffmpeg@6"].note.startswith("installed "))
        self.assertIn("plus about", d["brew:pkg:ffmpeg@6"].note)

    def test_service_and_bad_name_are_kept(self):
        d = self.by_key(self.scan())
        pg = d["brew:pkg:postgresql@15"]
        self.assertEqual((pg.tier, pg.kind, pg.argv), (core.PROTECTED, "info", None))
        self.assertEqual(pg.note, "Runs as a background service. Stop it with `brew services stop postgresql@15` first.")
        w = d["brew:pkg:Weird_Name"]
        self.assertFalse(w.offerable)
        self.assertEqual(w.note, "Can't be removed from here: run brew uninstall Weird_Name yourself.")

    def test_safe_item(self):
        it = self.by_key(self.scan())["brew:cleanup"]
        self.assertEqual((it.tier, it.preselect, it.argv), (core.SAFE, True, [self.brew_path, "cleanup"]))
        self.assertEqual(it.label, "Old Homebrew versions and unneeded libraries")
        self.assertEqual(it.size, 34600000 + 64 + SIZES["orphan"])
        self.assertEqual(it.note, "Removes old versions Homebrew keeps, and libraries nothing uses: orphan")
        self.assertEqual(it.meta["libraries"], ["orphan"])

    def test_safe_item_without_libraries(self):
        it = self.by_key(self.scan(autoremove="==> Would autoremove 0 unneeded formulae:\n"))["brew:cleanup"]
        self.assertEqual(it.note, "Removes old versions Homebrew keeps.")
        self.assertEqual(it.size, 34600000 + 64)

    def test_safe_item_omitted_when_nothing_to_remove(self):
        d = self.by_key(self.scan(cleanup="", autoremove=""))
        self.assertNotIn("brew:cleanup", d)

    def test_safe_item_kept_when_a_library_is_a_service(self):
        write(os.path.join(self.services, "homebrew.mxcl.orphan.plist"))
        it = self.by_key(self.scan())["brew:cleanup"]
        self.assertFalse(it.offerable)
        self.assertEqual(it.note, "orphan runs as a background service. Stop it with `brew services stop orphan` first.")

    def test_any_call_failing_offers_nothing(self):
        for broken in ("cleanup", "autoremove", "info"):
            res = self.scan(broken=broken)
            self.assertFalse([i for i in res.items if i.key.startswith("brew:")])
            self.assertIn("couldn't read Homebrew; its packages are not offered", res.warnings)
            self.assertEqual(res.homebrew_bytes, 0)

    def test_malformed_info_offers_nothing(self):
        res = self.scan(info_text="{not json")
        self.assertFalse([i for i in res.items if i.key.startswith("brew:")])
        self.assertEqual(res.warnings.count("couldn't read Homebrew; its packages are not offered"), 1)

    def test_timeout_and_oserror_offer_nothing(self):
        for exc in (subprocess.TimeoutExpired("brew", 20), OSError("gone")):
            env = self.env()
            def boom(argv, **kw):
                raise exc
            env = dataclasses.replace(env, brew=dataclasses.replace(env.brew, run=boom))
            warnings = []
            self.assertEqual(brew.scan_brew(self.cfg, env, warnings), ([], 0))
            self.assertEqual(warnings, ["couldn't read Homebrew; its packages are not offered"])

    def test_no_brew_no_rows_no_warning(self):
        res = core.scan(self.cfg, core.NoSimctl(), xcode_check=lambda: False, env=core.Env.fake(), now=self.now)
        self.assertFalse([i for i in res.items if i.key.startswith("brew:")])
        self.assertEqual(res.homebrew_bytes, 0)
        self.assertFalse([w for w in res.warnings if "Homebrew" in w])

    def test_other_account(self):
        with mock.patch.object(brew.os, "access", return_value=False):
            d = self.by_key(self.scan())
        self.assertEqual([k for k in d if k.startswith("brew:")], ["brew:other-account"])
        self.assertEqual(d["brew:other-account"].note, "Homebrew belongs to another account on this Mac")
        self.assertEqual(d["brew:other-account"].tier, core.PROTECTED)

    def test_homebrew_bytes_and_toolx_version(self):
        res = self.scan()
        self.assertEqual(res.homebrew_bytes, sum(SIZES.values()) + SIZES_CASK)
        self.assertEqual(self.by_key(res)["brew:pkg:toolx"].meta["version"], "1.0")

    def test_formula_with_no_kegs_is_skipped(self):
        self.assertNotIn("brew:pkg:emptyf", self.by_key(self.scan()))

    def test_rescan_without_sizes(self):
        items, nbytes = brew.scan_brew(self.cfg, self.env(), [], sizes=False)
        self.assertEqual(nbytes, 0)
        self.assertIn("brew:pkg:ffmpeg@6", {i.key for i in items})
        self.assertIsNone({i.key: i for i in items}["brew:pkg:ffmpeg@6"].size)

    def test_rescanner_registered(self):
        fn = next(f for p, f in core.RESCANNERS.items() if p == "brew:")
        items = fn("brew:cleanup", self.cfg, core.NoSimctl(), self.env(), self.now)
        self.assertIn("brew:cleanup", {i.key for i in items})

    def test_every_call_is_read_only_and_listed(self):
        rec = []
        self.scan(record=rec)
        forms = {tuple(a[1:]) for a, _ in rec}
        self.assertEqual(forms, {("info", "--json=v2", "--installed"), ("cleanup", "-n"), ("autoremove", "-n")})


if __name__ == "__main__":
    unittest.main()
