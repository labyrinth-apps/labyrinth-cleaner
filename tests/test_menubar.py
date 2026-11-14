"""Tests for the retired menu-bar installer: `install` points at the app, `uninstall` still works.

Every test uses a temp $HOME: nothing touches the real ~/Applications or
~/Library/LaunchAgents, and launchctl is never really called.
"""

import io
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import devclean_menubar as mb  # noqa: E402

CLI = os.path.join(ROOT, "devclean")


class FakeRun:
    """Records launchctl calls."""

    def __init__(self):
        self.calls = []

    def __call__(self, argv, **kw):
        self.calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, "", "")

    def launchctl(self):
        return [c for c in self.calls if c[0] == "launchctl"]


def fixture_install(home):
    """What the old `devclean menubar install` left behind, written by hand."""
    app = os.path.join(home, "Applications", "DevcleanBar.app")
    os.makedirs(os.path.join(app, "Contents", "MacOS"))
    with open(os.path.join(app, "Contents", "Info.plist"), "wb") as f:
        plistlib.dump({"CFBundleIdentifier": mb.APP_ID, "CFBundleExecutable": "DevcleanBar"}, f)
    with open(os.path.join(app, "Contents", "MacOS", "DevcleanBar"), "w") as f:
        f.write("#!/bin/sh\n")
    agent = os.path.join(home, "Library", "LaunchAgents", mb.APP_ID + ".plist")
    os.makedirs(os.path.dirname(agent))
    with open(agent, "wb") as f:
        plistlib.dump({"Label": mb.APP_ID}, f)
    return app, agent


class RetiredInstallTests(unittest.TestCase):
    def setUp(self):
        self.home = os.path.realpath(tempfile.mkdtemp(prefix="devclean-mb-"))

    def tearDown(self):
        shutil.rmtree(self.home)

    def test_install_exits_1_with_pointer_to_the_app(self):
        r = subprocess.run([sys.executable, CLI, "menubar", "install", "--home", self.home],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("Labyrinth Cleaner", r.stdout + r.stderr)
        self.assertIn("devclean menubar uninstall", r.stdout + r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.home, "Applications")))
        self.assertFalse(os.path.exists(os.path.join(self.home, "Library")))

    def test_install_main_never_runs_anything(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = mb.main(["install", "--home", self.home])
        self.assertEqual(code, 1)
        self.assertIn("Labyrinth Cleaner", out.getvalue() + err.getvalue())


class UninstallTests(unittest.TestCase):
    def setUp(self):
        self.home = os.path.realpath(tempfile.mkdtemp(prefix="devclean-mb-"))

    def tearDown(self):
        shutil.rmtree(self.home)

    def test_uninstall_removes_everything(self):
        app, agent = fixture_install(self.home)
        run = FakeRun()
        mb.uninstall(self.home, run=run)
        self.assertFalse(os.path.exists(app))
        self.assertFalse(os.path.exists(agent))
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

    def test_cli_dispatches_menubar(self):
        r = subprocess.run([sys.executable, CLI, "menubar", "--help"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("install", r.stdout)


if __name__ == "__main__":
    unittest.main()
