"""Tests for the menu-bar item installer and its Swift threshold logic.

Every test uses a temp $HOME: nothing touches the real ~/Applications or
~/Library/LaunchAgents, and launchctl is never really called.
"""

import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import devclean_menubar as mb  # noqa: E402

CLI = os.path.join(ROOT, "devclean")
REAL_RUN = subprocess.run


class FakeRun:
    """Records launchctl calls; passes swiftc through when `compile_ok`."""

    def __init__(self, compile_ok=False):
        self.calls = []
        self.compile_ok = compile_ok

    def __call__(self, argv, **kw):
        self.calls.append(list(argv))
        if argv[0] == "swiftc" and self.compile_ok:
            return REAL_RUN(argv, **kw)
        if argv[0] == "swiftc":
            raise AssertionError("swiftc must not run when compile=False")
        return subprocess.CompletedProcess(argv, 0, "", "")

    def launchctl(self):
        return [c for c in self.calls if c[0] == "launchctl"]


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.home = os.path.realpath(tempfile.mkdtemp(prefix="devclean-mb-"))

    def tearDown(self):
        shutil.rmtree(self.home)

    def plist(self, path):
        with open(path, "rb") as f:
            return plistlib.load(f)

    def test_install_writes_app_and_agent(self):
        run = FakeRun()
        app = mb.install(self.home, CLI, run=run, compile=False)
        self.assertEqual(app, os.path.join(self.home, "Applications", "DevcleanBar.app"))
        info = self.plist(os.path.join(app, "Contents", "Info.plist"))
        self.assertEqual(info["CFBundleIdentifier"], mb.APP_ID)
        self.assertEqual(info["CFBundleExecutable"], "DevcleanBar")
        self.assertIs(info["LSUIElement"], True)
        binary = os.path.join(app, "Contents", "MacOS", "DevcleanBar")
        self.assertTrue(os.path.isfile(binary))
        agent_path = os.path.join(self.home, "Library", "LaunchAgents", mb.APP_ID + ".plist")
        agent = self.plist(agent_path)
        self.assertEqual(agent["Label"], mb.APP_ID)
        self.assertEqual(agent["ProgramArguments"], [binary, "--devclean", os.path.realpath(CLI)])
        self.assertIs(agent["RunAtLoad"], True)
        self.assertIn(["launchctl", "bootstrap", f"gui/{os.getuid()}", agent_path], run.launchctl())

    def test_reinstall_is_idempotent(self):
        mb.install(self.home, CLI, run=FakeRun(), compile=False)
        run = FakeRun()
        mb.install(self.home, CLI, run=run, compile=False)
        calls = run.launchctl()
        self.assertEqual(calls[0], ["launchctl", "bootout", f"gui/{os.getuid()}/{mb.APP_ID}"])
        self.assertEqual(calls[-1][:2], ["launchctl", "bootstrap"])
        apps = os.listdir(os.path.join(self.home, "Applications"))
        self.assertEqual(apps, ["DevcleanBar.app"])

    def test_uninstall_removes_everything(self):
        mb.install(self.home, CLI, run=FakeRun(), compile=False)
        run = FakeRun()
        mb.uninstall(self.home, run=run)
        self.assertFalse(os.path.exists(os.path.join(self.home, "Applications", "DevcleanBar.app")))
        self.assertFalse(os.path.exists(
            os.path.join(self.home, "Library", "LaunchAgents", mb.APP_ID + ".plist")))
        self.assertIn(["launchctl", "bootout", f"gui/{os.getuid()}/{mb.APP_ID}"], run.launchctl())

    def test_uninstall_when_not_installed_is_quiet(self):
        mb.uninstall(self.home, run=FakeRun())

    def test_uninstall_refuses_foreign_app(self):
        app = os.path.join(self.home, "Applications", "DevcleanBar.app")
        os.makedirs(os.path.join(app, "Contents"))
        with open(os.path.join(app, "Contents", "Info.plist"), "wb") as f:
            plistlib.dump({"CFBundleIdentifier": "com.someone.else"}, f)
        with self.assertRaises(RuntimeError):
            mb.uninstall(self.home, run=FakeRun())
        self.assertTrue(os.path.isfile(os.path.join(app, "Contents", "Info.plist")))

    def test_uninstall_refuses_app_symlink(self):
        target = os.path.join(self.home, "elsewhere")
        os.makedirs(target)
        os.makedirs(os.path.join(self.home, "Applications"))
        os.symlink(target, os.path.join(self.home, "Applications", "DevcleanBar.app"))
        with self.assertRaises(RuntimeError):
            mb.uninstall(self.home, run=FakeRun())
        self.assertTrue(os.path.isdir(target))

    def test_failed_compile_can_be_uninstalled_and_retried(self):
        def failing(argv, **kw):
            if argv[0] == "swiftc":
                return subprocess.CompletedProcess(argv, 1, "", "error: boom")
            return subprocess.CompletedProcess(argv, 0, "", "")
        with self.assertRaises(RuntimeError):
            mb.install(self.home, CLI, run=failing, compile=True)
        mb.uninstall(self.home, run=FakeRun())
        self.assertFalse(os.path.exists(os.path.join(self.home, "Applications", "DevcleanBar.app")))
        with self.assertRaises(RuntimeError):
            mb.install(self.home, CLI, run=failing, compile=True)
        mb.install(self.home, CLI, run=FakeRun(), compile=False)   # retry works

    def test_missing_swiftc_is_a_clear_error(self):
        def missing(argv, **kw):
            if argv[0] == "swiftc":
                raise FileNotFoundError(2, "No such file or directory", "swiftc")
            return subprocess.CompletedProcess(argv, 0, "", "")
        with self.assertRaises(RuntimeError) as cm:
            mb.install(self.home, CLI, run=missing, compile=True)
        self.assertIn("xcode-select --install", str(cm.exception))
        mb.uninstall(self.home, run=FakeRun())

    def test_cli_dispatches_menubar(self):
        r = subprocess.run([sys.executable, CLI, "menubar", "--help"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("install", r.stdout)

    @unittest.skipUnless(shutil.which("swiftc"), "needs swiftc")
    def test_swift_logic(self):
        out = os.path.join(self.home, "check")
        src = os.path.join(ROOT, "menubar")
        r = subprocess.run(["swiftc", "-parse-as-library", os.path.join(src, "Logic.swift"),
                            os.path.join(src, "logic_check.swift"), "-o", out],
                           capture_output=True, text=True, timeout=300)
        self.assertEqual(r.returncode, 0, r.stderr)
        r = subprocess.run([out], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "OK")

    @unittest.skipUnless(shutil.which("swiftc"), "needs swiftc")
    def test_app_compiles(self):
        app = mb.install(self.home, CLI, run=FakeRun(compile_ok=True), compile=True)
        binary = os.path.join(app, "Contents", "MacOS", "DevcleanBar")
        self.assertTrue(os.access(binary, os.X_OK))
        self.assertGreater(os.path.getsize(binary), 10_000)


if __name__ == "__main__":
    unittest.main()
