"""Tests for devclean-launcher: the CLI entry point inside the app bundle.

A fake bundle is built in a temp dir; the real launcher script is copied into it.
"""

import os
import shutil
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LAUNCHER = os.path.join(ROOT, "devclean-launcher")


def write(path, text, mode=0o755):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)
    os.chmod(path, mode)


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="devclean-launcher-"))
        self.core = os.path.join(self.tmp, "Labyrinth Cleaner.app", "Contents", "Resources", "core")
        contents = os.path.dirname(os.path.dirname(self.core))
        self.launcher = os.path.join(self.core, "devclean-launcher")
        os.makedirs(self.core)
        shutil.copy(LAUNCHER, self.launcher)
        os.chmod(self.launcher, 0o755)
        write(os.path.join(self.core, "devclean"),
              "import os, sys\n"
              "print('FLAGS', sys.flags.ignore_environment, sys.flags.no_user_site, sys.flags.utf8_mode)\n"
              "print('ARGV', sys.argv[1:])\n"
              "print('HELPER', os.environ.get('LABYRINTH_TRASH_HELPER'))\n", 0o644)
        self.py = os.path.join(contents, "Resources", "python", "bin", "python3")
        write(self.py, '#!/bin/sh\necho "PYARGS $*"\nexec /usr/bin/python3 "$@"\n')
        self.helper = os.path.join(contents, "MacOS", "labyrinth-trash")
        write(self.helper, "")
        os.makedirs(os.path.join(self.tmp, "bin"))
        self.link = os.path.join(self.tmp, "bin", "devclean")
        os.symlink(self.launcher, self.link)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def run_link(self, *args):
        return subprocess.run([self.link, *args], capture_output=True, text=True)

    def test_args_flags_and_helper(self):
        r = self.run_link("--dry-run", "x")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("ARGV ['--dry-run', 'x']", r.stdout)
        self.assertIn("HELPER " + self.helper, r.stdout)
        self.assertIn("FLAGS 1 1 1", r.stdout)
        # -B: never write bytecode into the (signed) bundle.
        self.assertIn("PYARGS -E -s -B -X utf8 ", r.stdout)

    def test_helper_path_is_normalised(self):
        r = self.run_link()
        helper = [l for l in r.stdout.splitlines() if l.startswith("HELPER ")][0][7:]
        self.assertNotIn("..", helper)
        self.assertEqual(helper, os.path.realpath(helper))

    def test_missing_python_exits_1_without_fallback(self):
        os.remove(self.py)
        r = self.run_link("--dry-run")
        self.assertEqual(r.returncode, 1)
        self.assertIn("Labyrinth Cleaner's Python is missing. Reinstall the app.", r.stderr)
        self.assertEqual(r.stdout, "")

    def test_non_executable_python_counts_as_missing(self):
        os.chmod(self.py, 0o644)
        r = self.run_link()
        self.assertEqual(r.returncode, 1)
        self.assertIn("Python is missing", r.stderr)


if __name__ == "__main__":
    unittest.main()
