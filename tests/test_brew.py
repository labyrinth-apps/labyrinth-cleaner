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


def dry_run(names):
    """`brew uninstall --dry-run --formula <names>`: each name, then uninstall.txt's autoremove part."""
    text = read_fixture("uninstall.txt")
    return ("".join(f"Would uninstall {n} {VERSIONS.get(n, '1.0')}\n" for n in names)
            + text[text.index("==>"):])


def tree_bytes(path):
    """A du stand-in: the file bytes under path, so tests don't depend on disk blocks."""
    total = 0
    for root, _dirs, files in os.walk(path):
        total += sum(os.path.getsize(os.path.join(root, f)) for f in files)
    return total


def fake_brew(prefix, answers, record=None, broken=None, uninstall_stderr=None, uninstall=None):
    """A runner for brew: answers the four read-only forms from `answers` (text with /PREFIX; the
    dry run's answer may be a function of the names), accepts the two write forms, refuses
    everything else. `uninstall --formula x` removes <temp prefix>/Cellar/x, as Homebrew would,
    unless `uninstall(argv)` answers instead."""
    assert os.path.realpath(prefix).startswith(os.path.realpath(tempfile.gettempdir()) + os.sep)
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
            text = answers[which]
            if callable(text):
                text = text(list(argv[4:]))
            return ok(argv, text.replace("/PREFIX", prefix))
        if list(argv) == [path, "cleanup"]:
            return ok(argv)
        if len(argv) == 4 and argv[:3] == [path, "uninstall", "--formula"]:
            if uninstall_stderr is not None:
                return subprocess.CompletedProcess(argv, 1, "", uninstall_stderr)
            if uninstall is not None:
                return uninstall(argv)
            assert brew.NAME_RE.match(argv[3]), argv
            shutil.rmtree(os.path.join(prefix, "Cellar", argv[3]), ignore_errors=True)
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
                "autoremove": read_fixture("autoremove.txt"), "uninstall": dry_run}

    def env(self, record=None, broken=None, uninstall_stderr=None, info_text=None, on_uninstall=None,
            **answers):
        a = self.answers()
        if info_text is not None:
            a["info"] = info_text
        a.update(answers)
        runner = brew.BrewRunner(
            path=self.brew_path, prefix=self.prefix, service_dirs=(self.services,),
            run=fake_brew(self.prefix, a, record, broken, uninstall_stderr, on_uninstall))
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


class RunBrewTests(BrewBase):
    def item(self, env, key="brew:pkg:ffmpeg@6"):
        return self.by_key(core.scan(self.cfg, core.NoSimctl(), env=env, now=self.now))[key]

    def test_uninstall_runs_through_env_brew_with_exact_argv(self):
        rec = []
        env = self.env(record=rec)
        it = self.item(env, "brew:pkg:ffmpeg@6")
        rec.clear()

        def general(argv, **kw):
            raise AssertionError("brew went through the general runner")
        ok_, msg = core.execute_item(it, self.cfg, runner=general, env=env, now=self.now)
        self.assertTrue(ok_, msg)
        self.assertEqual(rec[-1][0], [self.brew_path, "uninstall", "--formula", "ffmpeg@6"])
        self.assertEqual(rec[-1][1]["timeout"], 300)

    def test_gate_refuses_forged_argv(self):
        env = self.env()
        for key, argv in (("brew:pkg:ffmpeg@6", [self.brew_path, "uninstall", "--formula", "ffmpeg@6", "--ignore-dependencies"]),
                          ("brew:pkg:ffmpeg@6", [self.brew_path, "uninstall", "--force", "--formula", "ffmpeg@6"]),
                          ("brew:pkg:ffmpeg@6", ["/tmp/brew", "uninstall", "--formula", "ffmpeg@6"]),
                          ("brew:cleanup", [self.brew_path, "cleanup", "--prune=all"]),
                          ("brew:pkg:libshared", [self.brew_path, "uninstall", "--formula", "libshared"])):
            forged = core.Item(key=key, category="Homebrew", label="x", tier=core.CAUTION, kind="brew",
                               argv=argv, meta={"version": "6.1.6"})
            ok_, msg = core.execute_item(forged, self.cfg, env=env, now=self.now)
            self.assertFalse(ok_)
            self.assertTrue(msg.startswith("REFUSED"), msg)

    def test_version_changed_is_situation_changed(self):
        it = self.item(self.env())
        info = json.loads(read_fixture("info.json"))
        for f in info["formulae"]:
            if f["name"] == "ffmpeg@6":
                for k in f["installed"]:
                    k["version"] = "6.1.7"
                f["linked_keg"] = "6.1.7"
        rec = []
        env = self.env(record=rec, info_text=json.dumps(info))
        self.assertEqual(core.execute_item(it, self.cfg, env=env, now=self.now),
                         (False, "REFUSED: Situation changed: scan again"))
        self.assertFalse([a for a, _ in rec if "--formula" in a and "--dry-run" not in a])

    def test_service_started_after_scan_is_refused(self):
        env = self.env()
        it = self.item(env)
        write(os.path.join(self.services, "homebrew.mxcl.ffmpeg@6.plist"))
        ok_, msg = core.execute_item(it, self.cfg, env=env, now=self.now)
        self.assertFalse(ok_)
        self.assertTrue(msg.startswith("REFUSED"), msg)

    def test_fixture_runner_and_no_brew_refuse(self):
        rec = []
        env = self.env(record=rec)
        it = self.item(env)
        rec.clear()
        self.assertEqual(core.execute_item(it, self.cfg, runner=core.fixture_runner, env=env, now=self.now),
                         (False, "REFUSED: fixture mode never runs brew"))
        no_brew = dataclasses.replace(env, brew=None)
        self.assertEqual(core.execute_item(it, self.cfg, env=no_brew, now=self.now),
                         (False, "REFUSED: Homebrew is not available"))
        self.assertEqual(rec, [])

    def test_busy(self):
        env = self.env(uninstall_stderr="Error: A `brew install` process has already locked /opt/homebrew/Cellar/x")
        it = self.item(env)
        self.assertEqual(core.execute_item(it, self.cfg, env=env, now=self.now),
                         (False, "Homebrew is busy with another command. Try again when it's done."))

    def test_other_failure_returns_stderr(self):
        env = self.env(uninstall_stderr="Error: nope")
        it = self.item(env)
        self.assertEqual(core.execute_item(it, self.cfg, env=env, now=self.now), (False, "Error: nope"))

    def test_cleanup_runs_with_no_fresh_item(self):
        rec = []
        env = self.env(record=rec)
        it = self.item(env, "brew:cleanup")
        quiet = self.env(record=rec, cleanup="", autoremove="==> Would autoremove 0 unneeded formulae:\n")
        self.assertNotIn("brew:cleanup", self.by_key(core.scan(self.cfg, core.NoSimctl(), env=quiet, now=self.now)))
        rec.clear()
        ok_, msg = core.execute_item(it, self.cfg, env=quiet, now=self.now)
        self.assertTrue(ok_, msg)
        self.assertEqual(rec[-1][0], [self.brew_path, "cleanup"])

    def quiet_env(self, rec, **kw):
        return self.env(record=rec, cleanup="", autoremove="==> Would autoremove 0 unneeded formulae:\n", **kw)

    def test_no_fresh_cleanup_refused_when_homebrew_unreadable(self):
        it = self.item(self.env(), "brew:cleanup")
        for broken in ("info", "cleanup", "autoremove"):
            rec = []
            env = self.env(record=rec, broken=broken)
            ok_, msg = core.execute_item(it, self.cfg, env=env, now=self.now)
            self.assertFalse(ok_, broken)
            self.assertTrue(msg.startswith("REFUSED"), msg)
            self.assertNotIn([self.brew_path, "cleanup"], [a for a, _ in rec], broken)

    def test_no_fresh_cleanup_refused_for_other_account_and_missing_cellar(self):
        it = self.item(self.env(), "brew:cleanup")
        rec = []
        env = self.quiet_env(rec)
        with mock.patch.object(os, "access", lambda p, m, **kw: False if p == env.brew.cellar else True):
            ok_, msg = core.execute_item(it, self.cfg, env=env, now=self.now)
        self.assertFalse(ok_)
        self.assertTrue(msg.startswith("REFUSED"), msg)
        shutil.rmtree(os.path.join(self.prefix, "Cellar"))
        ok_, msg = core.execute_item(it, self.cfg, env=env, now=self.now)
        self.assertFalse(ok_)
        self.assertTrue(msg.startswith("REFUSED"), msg)
        self.assertNotIn([self.brew_path, "cleanup"], [a for a, _ in rec])

    def test_no_fresh_cleanup_refuses_forged_argv(self):
        rec = []
        env = self.quiet_env(rec)
        for argv in ([self.brew_path, "cleanup", "--prune=all"], ["/tmp/brew", "cleanup"]):
            forged = core.Item(key="brew:cleanup", category="Homebrew", label="x", tier=core.SAFE,
                               kind="brew", argv=argv)
            ok_, msg = core.execute_item(forged, self.cfg, env=env, now=self.now)
            self.assertFalse(ok_)
            self.assertTrue(msg.startswith("REFUSED"), msg)
        self.assertNotIn([self.brew_path, "cleanup"], [a for a, _ in rec])
        self.assertFalse([a for a, _ in rec if a[1:2] == ["cleanup"] and "-n" not in a])

    def test_cleanup_refused_when_a_library_became_a_service(self):
        env = self.env()
        it = self.item(env, "brew:cleanup")
        write(os.path.join(self.services, "homebrew.mxcl.orphan.plist"))
        ok_, msg = core.execute_item(it, self.cfg, env=env, now=self.now)
        self.assertFalse(ok_)
        self.assertTrue(msg.startswith("REFUSED"), msg)

    def test_order_uninstalls_before_cleanup(self):
        d = self.by_key(self.scan())
        keys = [i.key for i in core.execution_order([d["brew:cleanup"], d["brew:pkg:ffmpeg@8"], d["brew:pkg:ffmpeg@6"]])]
        self.assertEqual(keys, ["brew:pkg:ffmpeg@6", "brew:pkg:ffmpeg@8", "brew:cleanup"])

    def test_texts(self):
        d = self.by_key(self.scan())
        self.assertEqual(d["brew:pkg:ffmpeg@6"].restore, "Removed. Reinstall with `brew install ffmpeg@6`.")
        self.assertEqual(d["brew:pkg:ffmpeg@6"].cost_tag, "REINSTALL")
        self.assertEqual(d["brew:pkg:ffmpeg@6"].cost, d["brew:pkg:ffmpeg@6"].note)
        self.assertEqual((d["brew:cleanup"].cost_tag, d["brew:cleanup"].cost),
                         ("NO COST", "Homebrew doesn't use them."))
        self.assertEqual(d["brew:cleanup"].restore, "Nothing to get back: Homebrew doesn't use them.")

    def test_preview(self):
        p = brew.preview_uninstall(["ffmpeg@6"], self.env())
        self.assertEqual(p, {"ok": True, "packages": ["ffmpeg@6"], "libraries": ["libonly6", "orphan"]})
        self.assertEqual(brew.preview_uninstall(["a;b"], self.env())["ok"], False)
        self.assertEqual(brew.preview_uninstall(["ffmpeg@6"], self.env(broken="uninstall")),
                         {"ok": False, "error": "Couldn't ask Homebrew what this would remove. Scan again."})
        write(os.path.join(self.services, "homebrew.mxcl.libonly6.plist"))
        self.assertEqual(brew.preview_uninstall(["ffmpeg@6"], self.env())["error"],
                         "libonly6 runs as a background service. Stop it with `brew services stop libonly6` first.")


REFUSAL = ("Error: Refusing to uninstall libshared\n"
           "because it is required by ffmpeg@8, which is currently installed.\n"
           "You can override this and force removal with:\n"
           "  brew uninstall --ignore-dependencies libshared\n")
REFUSAL_SHOWN = ("Refusing to uninstall libshared "
                 "because it is required by ffmpeg@8, which is currently installed.")


def with_info(change):
    """info.json with `change(formula_by_name)` applied."""
    info = json.loads(read_fixture("info.json"))
    change({f["name"]: f for f in info["formulae"]})
    return json.dumps(info)


class GateRunTests(BrewBase):
    """Rows must not report success when Homebrew removed nothing, or only part."""

    def item(self, env, key="brew:pkg:ffmpeg@6"):
        return self.by_key(core.scan(self.cfg, core.NoSimctl(), env=env, now=self.now))[key]

    def uninstalls(self, rec):
        return [a for a, _ in rec if a[1:3] == ["uninstall", "--formula"]]

    def test_name_re_rejects_trailing_newline(self):
        self.assertIsNone(brew.NAME_RE.match("ffmpeg@6\n"))
        self.assertIsNotNone(brew.NAME_RE.match("ffmpeg@6"))
        self.assertEqual(brew.preview_uninstall(["ffmpeg@6\n"], self.env())["ok"], False)
        it = self.item(self.env())
        forged = dataclasses.replace(it, key="brew:pkg:ffmpeg@6\n",
                                     argv=[self.brew_path, "uninstall", "--formula", "ffmpeg@6\n"])
        with self.assertRaises(core.UnsafeTarget):
            brew.validate(forged, it, self.env())

    def test_pinned_is_kept(self):
        def pin(f):
            f["ffmpeg@6"]["pinned"] = True
        it = self.by_key(self.scan(info_text=with_info(pin)))["brew:pkg:ffmpeg@6"]
        self.assertEqual((it.tier, it.kind, it.argv), (core.PROTECTED, "info", None))
        self.assertEqual(it.note, "Pinned: run `brew unpin ffmpeg@6` first.")

    def test_pinned_after_scan_is_refused(self):
        def pin(f):
            f["ffmpeg@6"]["pinned"] = True
        it = self.item(self.env())
        rec = []
        ok_, msg = core.execute_item(it, self.cfg, env=self.env(record=rec, info_text=with_info(pin)),
                                     now=self.now)
        self.assertFalse(ok_)
        self.assertTrue(msg.startswith("REFUSED"), msg)
        self.assertEqual(self.uninstalls(rec), [])

    def test_exit_0_with_the_keg_still_there_fails(self):
        # Pinned since the scan began (the gate missed it): Homebrew exits 0, removes nothing.
        env = self.env(on_uninstall=lambda argv: subprocess.CompletedProcess(
            argv, 0, "", "Error: ffmpeg@6 is pinned. You must unpin it to uninstall.\n"))
        self.assertEqual(core.execute_item(self.item(env), self.cfg, env=env, now=self.now),
                         (False, "Pinned: run `brew unpin ffmpeg@6` first."))
        env = self.env(on_uninstall=lambda argv: ok(argv))
        self.assertEqual(core.execute_item(self.item(env), self.cfg, env=env, now=self.now),
                         (False, "ffmpeg@6 is still installed."))

    def test_one_of_several_kegs_removed_fails_with_homebrews_words(self):
        write(os.path.join(self.prefix, "Cellar", "toolx", "2.0", "bin", "x"), "x")

        def uninstall(argv):
            shutil.rmtree(os.path.join(self.prefix, "Cellar", "toolx", "1.0"))
            return ok(argv, "Uninstalling /x/Cellar/toolx/1.0... (1 file, 600B)\n"
                            "toolx 2.0 is still installed.\nTo remove all versions, run:\n"
                            "  brew uninstall --force toolx\n")
        env = self.env(on_uninstall=uninstall)
        self.assertEqual(core.execute_item(self.item(env, "brew:pkg:toolx"), self.cfg, env=env, now=self.now),
                         (False, "toolx 2.0 is still installed."))

    def test_uninstall_that_removes_the_keg_succeeds(self):
        env = self.env()
        self.assertEqual(core.execute_item(self.item(env), self.cfg, env=env, now=self.now), (True, "ok"))
        self.assertFalse(os.path.exists(os.path.join(self.prefix, "Cellar", "ffmpeg@6")))

    def test_refusal_is_shown_as_homebrew_says_it(self):
        env = self.env(uninstall_stderr=REFUSAL)
        msg = core.execute_item(self.item(env), self.cfg, env=env, now=self.now)
        self.assertEqual(msg, (False, REFUSAL_SHOWN))
        self.assertNotIn("--ignore-dependencies", msg[1])

    def test_preview_refusal_is_shown_as_homebrew_says_it(self):
        env = self.env()
        real = env.brew.run

        def run(argv, **kw):
            if "--dry-run" in argv:
                return subprocess.CompletedProcess(argv, 1, "", REFUSAL)
            return real(argv, **kw)
        env = dataclasses.replace(env, brew=dataclasses.replace(env.brew, run=run))
        self.assertEqual(brew.preview_uninstall(["ffmpeg@6"], env), {"ok": False, "error": REFUSAL_SHOWN})

    def test_preview_fails_when_a_name_is_missing(self):
        # Homebrew prints no "Would uninstall" line for a pinned formula, and exits 0.
        env = self.env(uninstall=read_fixture("uninstall.txt"))
        self.assertEqual(brew.preview_uninstall(["ffmpeg@6", "ffmpeg@8"], env),
                         {"ok": False, "error": "ffmpeg@8 can't be removed right now. Scan again."})
        self.assertTrue(brew.preview_uninstall(["ffmpeg@6"], env)["ok"])

    def test_service_check_uses_the_short_name(self):
        write(os.path.join(self.services, "homebrew.mxcl.libx.plist"))
        self.assertTrue(brew._has_service(self.env().brew, "me/tap/libx"))
        it = self.by_key(self.scan(autoremove="==> Would autoremove 1 unneeded formula:\nme/tap/libx\n"))["brew:cleanup"]
        self.assertFalse(it.offerable)
        self.assertEqual(it.note, "me/tap/libx runs as a background service. "
                                  "Stop it with `brew services stop me/tap/libx` first.")

    def test_package_gate_reads_info_only(self):
        env = self.env()
        it = self.item(env)
        rec = []
        env = self.env(record=rec, broken="cleanup")   # a slow or failing cleanup -n
        self.assertEqual(core.execute_item(it, self.cfg, env=env, now=self.now), (True, "ok"))
        reads = [tuple(a[1:]) for a, _ in rec if a[1:3] != ["uninstall", "--formula"]]
        self.assertEqual(reads, [("info", "--json=v2", "--installed")])

    def test_package_rescan_lists_the_same_packages(self):
        full = {i.key: i for i in brew.scan_brew(self.cfg, self.env(), [], sizes=False)[0]
                if i.key.startswith("brew:pkg:")}
        quick = {i.key: i for i in brew.scan_brew(self.cfg, self.env(), [], sizes=False, full=False)[0]}
        self.assertEqual(set(quick), set(full))
        for k in full:
            self.assertEqual((quick[k].tier, quick[k].argv, quick[k].note, quick[k].meta.get("version")),
                             (full[k].tier, full[k].argv, full[k].note, full[k].meta.get("version")))

    def test_read_cleanly_needs_no_cleanup_item(self):
        quiet = dict(cleanup="", autoremove="==> Would autoremove 0 unneeded formulae:\n")
        self.assertTrue(brew.read_cleanly(self.cfg, self.env(**quiet)))
        self.assertFalse(brew.read_cleanly(self.cfg, self.env()))          # offered item
        write(os.path.join(self.services, "homebrew.mxcl.orphan.plist"))
        self.assertFalse(brew.read_cleanly(self.cfg, self.env()))          # kept item

    def test_cleanup_gate_decides_from_one_read(self):
        it = self.item(self.env(), "brew:cleanup")
        # Nothing to clean on the first read; a library that runs as a service on any later one.
        calls = {"n": 0}

        def autoremove_text():
            calls["n"] += 1
            return ("==> Would autoremove 0 unneeded formulae:\n" if calls["n"] == 1
                    else "==> Would autoremove 1 unneeded formula:\norphan\n")
        write(os.path.join(self.services, "homebrew.mxcl.orphan.plist"))
        rec = []
        env = self.env(record=rec, cleanup="")
        real = env.brew.run

        def run(argv, **kw):
            if list(argv[1:]) == ["autoremove", "-n"]:
                rec.append((list(argv), kw))
                return ok(argv, autoremove_text())
            return real(argv, **kw)
        env = dataclasses.replace(env, brew=dataclasses.replace(env.brew, run=run))
        ok_, msg = core.execute_item(it, self.cfg, env=env, now=self.now)
        self.assertEqual([a for a, _ in rec if a[1:] == ["autoremove", "-n"]].__len__(), 1)
        self.assertTrue(ok_, msg)   # the one read saw nothing to clean and read cleanly
        self.assertIn([self.brew_path, "cleanup"], [a for a, _ in rec])


if __name__ == "__main__":
    unittest.main()
