"""uv and pnpm through their own commands. Never runs the real uv or pnpm."""

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

import devclean_core as core  # noqa: E402
import devclean_tools as tools  # noqa: E402
from test_devclean import cfg_for  # noqa: E402


class FakeRunner:
    def __init__(self, returncode=0, stderr="", raises=None):
        self.calls = []
        self.returncode, self.stderr, self.raises = returncode, stderr, raises

    def __call__(self, argv, **kw):
        self.calls.append((list(argv), kw))
        if self.raises:
            raise self.raises
        return subprocess.CompletedProcess(argv, self.returncode, "", self.stderr)


def make_exec(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write("#!/bin/sh\n")
    os.chmod(path, 0o755)


class ToolsTests(unittest.TestCase):
    def setUp(self):
        self.base = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.base, True)
        self.home = os.path.join(self.base, "home")
        os.makedirs(self.home)
        self.cfg = cfg_for(self.home)
        self.uv_dir = os.path.join(self.home, ".cache", "uv")
        self.pnpm_dir = os.path.join(self.home, "Library", "pnpm", "store")
        for d in (self.uv_dir, self.pnpm_dir):
            os.makedirs(os.path.join(d, "x"))
            with open(os.path.join(d, "x", "f"), "w") as fh:
                fh.write("data")
        self.uv_bin = os.path.join(self.base, "bin", "uv")
        self.pnpm_bin = os.path.join(self.base, "bin", "pnpm")

    def env(self, found=True, runner=None):
        bins = {"uv": self.uv_bin, "pnpm": self.pnpm_bin} if found else {}
        return core.Env.fake(find_tool=lambda n: bins.get(n), tool_runner=runner)

    def items(self, env):
        return {i.key: i for i in tools.scan_tools(self.cfg, env)}

    def test_find_tool_order_and_volta_symlink(self):
        brew = os.path.join(self.base, "opt-homebrew-bin")
        local = os.path.join(self.base, "usr-local-bin")
        os.makedirs(brew)
        os.makedirs(local)
        dirs = {n: [d.replace("/opt/homebrew/bin", brew).replace("/usr/local/bin", local)
                    for d in ds] for n, ds in tools.SEARCH_DIRS.items()}
        uv = os.path.join(self.home, ".local", "bin", "uv")
        make_exec(uv)
        shim = os.path.join(self.base, "volta-image", "shim")
        make_exec(shim)
        os.makedirs(os.path.join(self.home, ".volta", "bin"))
        os.symlink(shim, os.path.join(self.home, ".volta", "bin", "pnpm"))
        with mock.patch.dict(tools.SEARCH_DIRS, dirs):
            self.assertEqual(tools.find_tool("uv", self.home), uv)
            self.assertEqual(tools.find_tool("pnpm", self.home),
                             os.path.join(self.home, ".volta", "bin", "pnpm"))
            make_exec(os.path.join(local, "uv"))      # earlier in the order than ~/.local/bin
            self.assertEqual(tools.find_tool("uv", self.home), os.path.join(local, "uv"))
            os.chmod(os.path.join(local, "uv"), 0o644)   # not executable: skipped
            self.assertEqual(tools.find_tool("uv", self.home), uv)
            self.assertIsNone(tools.find_tool("nope", self.home))

    def test_search_dirs_order(self):
        self.assertEqual(tools.SEARCH_DIRS["uv"], ["/opt/homebrew/bin", "/usr/local/bin", "~/.local/bin",
                                                   "~/.cargo/bin", "~/.volta/bin"])
        self.assertEqual(tools.SEARCH_DIRS["pnpm"], ["/opt/homebrew/bin", "/usr/local/bin", "~/.local/bin",
                                                     "~/Library/pnpm", "~/.volta/bin"])

    def test_rows_when_tools_found(self):
        items = self.items(self.env())
        uv, pnpm = items["tool:uv"], items["tool:pnpm"]
        self.assertEqual(uv.argv, [self.uv_bin, "cache", "clean"])
        self.assertEqual(pnpm.argv, [self.pnpm_bin, "store", "prune"])
        for it, folder in ((uv, self.uv_dir), (pnpm, self.pnpm_dir)):
            self.assertEqual((it.tier, it.kind, it.category, it.preselect, it.path),
                             (core.SAFE, "tool", "Cache", True, folder))
        self.assertTrue(pnpm.meta.get("upper_bound"))
        self.assertFalse(uv.meta.get("upper_bound"))

    def test_missing_tool_is_kept_with_terminal_hint(self):
        items = self.items(self.env(found=False))
        self.assertEqual(items["tool:uv"].tier, core.PROTECTED)
        self.assertEqual(items["tool:uv"].kind, "info")
        self.assertIsNone(items["tool:uv"].argv)
        self.assertEqual(items["tool:uv"].note, "Couldn't find `uv`. Run `uv cache clean` in Terminal.")
        self.assertEqual(items["tool:pnpm"].note,
                         "Couldn't find `pnpm`. Run `pnpm store prune` in Terminal.")

    def test_empty_uv_folder_no_row(self):
        shutil.rmtree(self.uv_dir)
        os.makedirs(self.uv_dir)
        self.assertNotIn("tool:uv", self.items(self.env()))
        self.assertNotIn("tool:uv", self.items(self.env(found=False)))
        shutil.rmtree(self.pnpm_dir)
        self.assertNotIn("tool:pnpm", self.items(self.env()))

    def test_cost_and_restore(self):
        items = self.items(self.env())
        self.assertEqual(core.cost_for(items["tool:uv"])[0], "NO COST")
        self.assertIn("downloads packages again", core.cost_for(items["tool:uv"])[1])
        self.assertEqual(core.cost_for(items["tool:pnpm"]),
                         ("NO COST", "Removes packages no project uses. Projects keep working."))
        self.assertEqual(core.explain_for(items["tool:uv"], self.home)[1], "Downloaded again on demand.")

    def test_run_uses_minimal_env_and_home_cwd(self):
        runner = FakeRunner()
        env = self.env(runner=runner)
        it = self.items(env)["tool:uv"]
        self.assertEqual(tools.run_item(it, env, self.home), (True, "ok"))
        argv, kw = runner.calls[0]
        self.assertEqual(argv, [self.uv_bin, "cache", "clean"])
        self.assertEqual(kw["env"], {"HOME": self.home,
                                     "PATH": f"{os.path.dirname(self.uv_bin)}:/usr/bin:/bin"})
        self.assertEqual((kw["cwd"], kw["timeout"]), (self.home, 600))

    def test_timeout_message(self):
        runner = FakeRunner(raises=subprocess.TimeoutExpired("uv", 600))
        env = self.env(runner=runner)
        it = self.items(env)["tool:uv"]
        self.assertEqual(tools.run_item(it, env, self.home), (False, "uv is busy. Try again when it's done."))

    def test_nonzero_exit_message(self):
        env = self.env(runner=FakeRunner(returncode=2, stderr="boom"))
        it = self.items(env)["tool:pnpm"]
        self.assertEqual(tools.run_item(it, env, self.home),
                         (False, "boom Run `pnpm store prune` in Terminal."))
        env = self.env(runner=FakeRunner(returncode=3))
        self.assertEqual(tools.run_item(it, env, self.home),
                         (False, "exit 3 Run `pnpm store prune` in Terminal."))

    def test_execute_runs_through_tool_runner_only(self):
        runner = FakeRunner()
        env = self.env(runner=runner)
        it = self.items(env)["tool:uv"]
        general = mock.Mock(side_effect=AssertionError("general runner used"))
        self.assertEqual(core.execute_item(it, self.cfg, runner=general, env=env), (True, "ok"))
        self.assertEqual(len(runner.calls), 1)
        self.assertTrue(os.path.isdir(self.uv_dir))      # nothing was rm'd

    def test_execute_refused_without_runner(self):
        it = self.items(self.env())["tool:uv"]
        ok, msg = core.execute_item(it, self.cfg, env=self.env(runner=None))
        self.assertEqual((ok, msg), (False, "REFUSED: uv is not available"))

    def test_gate_refuses_when_binary_changed(self):
        it = self.items(self.env())["tool:uv"]
        runner = FakeRunner()
        other = core.Env.fake(find_tool=lambda n: "/b/uv" if n == "uv" else None, tool_runner=runner)
        with self.assertRaises(core.UnsafeTarget) as cm:
            core.validate_item(it, self.cfg, env=other)
        self.assertEqual(str(cm.exception), core.SITUATION_CHANGED)
        self.assertEqual(core.execute_item(it, self.cfg, env=other),
                         (False, f"REFUSED: {core.SITUATION_CHANGED}"))
        self.assertEqual(runner.calls, [])

    def test_validate_refuses_forged_args_and_names(self):
        env = self.env(runner=FakeRunner())
        it = self.items(env)["tool:uv"]
        fresh = it
        bad = core.Item(key="tool:uv", category="Cache", label="x", tier=core.SAFE, kind="tool",
                        argv=[self.uv_bin, "cache", "clean", "--all"], path=it.path)
        with self.assertRaises(core.UnsafeTarget):
            tools.validate(bad, fresh, env)
        bad = core.Item(key="tool:rm", category="Cache", label="x", tier=core.SAFE, kind="tool",
                        argv=["/bin/rm", "cache", "clean"], path=it.path)
        with self.assertRaises(core.UnsafeTarget):
            tools.validate(bad, fresh, env)
        tools.validate(it, fresh, env)

    def test_fixture_mode_never_runs_tools(self):
        runner = FakeRunner()
        env = self.env(runner=runner)
        it = self.items(env)["tool:uv"]
        self.assertEqual(core.execute_item(it, self.cfg, runner=core.fixture_runner, env=env),
                         (False, "REFUSED: fixture mode never runs uv"))
        for name in ("uv", "pnpm"):
            r = core.fixture_runner([f"/x/{name}", "cache", "clean"])
            self.assertEqual(r.returncode, 1)
        self.assertEqual(runner.calls, [])
        fenv = core.fixture_env(self.cfg, lambda: False, True)
        self.assertIsNone(fenv.tool_runner)

    def test_env_fake_defaults(self):
        env = core.Env.fake()
        self.assertIsNone(env.find_tool("uv"))
        self.assertIsNone(env.tool_runner)

    def test_scan_includes_tool_rows_and_sizes(self):
        res = core.scan(self.cfg, env=self.env(runner=FakeRunner()), measure=True)
        it = next(i for i in res.items if i.key == "tool:uv")
        self.assertGreater(it.size, 0)
        self.assertNotIn("dotcache:uv", [i.key for i in res.items])


if __name__ == "__main__":
    unittest.main()
