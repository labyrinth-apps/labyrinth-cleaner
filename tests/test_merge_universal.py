import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import merge_universal  # noqa: E402

ARM = b"\xcf\xfa\xed\xfe\x0c\x00\x00\x01 arm"
X86 = b"\xcf\xfa\xed\xfe\x07\x00\x00\x01 x86"


class FakeLipo:
    def __init__(self):
        self.calls = []

    def __call__(self, argv):
        self.calls.append(list(argv))
        out = argv[argv.index("-output") + 1]
        Path(out).write_bytes(b"\xca\xfe\xba\xbe fat")


class MergeUniversalTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.arm, self.x86, self.out = root / "arm", root / "x86", root / "out"
        self.arm.mkdir()
        self.x86.mkdir()
        self.lipo = FakeLipo()
        self.stderr = []

    def put(self, tree, rel, data):
        p = tree / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data if isinstance(data, bytes) else data.encode())

    def merge(self):
        merge_universal.merge(self.arm, self.x86, self.out, lipo=self.lipo,
                              log=self.stderr.append)

    def test_macho_in_both_is_lipoed(self):
        self.put(self.arm, "lib/a.so", ARM)
        self.put(self.x86, "lib/a.so", X86)
        self.merge()
        self.assertEqual(len(self.lipo.calls), 1)
        argv = self.lipo.calls[0]
        self.assertIn("-create", argv)
        self.assertIn(str(self.arm / "lib/a.so"), argv)
        self.assertIn(str(self.x86 / "lib/a.so"), argv)
        self.assertEqual((self.out / "lib/a.so").read_bytes()[:4], b"\xca\xfe\xba\xbe")

    def test_text_file_copied_from_arm_and_difference_reported(self):
        self.put(self.arm, "lib/x.txt", "arm")
        self.put(self.x86, "lib/x.txt", "x86")
        self.put(self.arm, "lib/same.txt", "same")
        self.put(self.x86, "lib/same.txt", "same")
        self.merge()
        self.assertEqual((self.out / "lib/x.txt").read_text(), "arm")
        self.assertTrue(any("lib/x.txt" in m for m in self.stderr))
        self.assertFalse(any("same.txt" in m for m in self.stderr))
        self.assertEqual(self.lipo.calls, [])

    def test_symlink_preserved(self):
        self.put(self.arm, "bin/python3.12", ARM)
        self.put(self.x86, "bin/python3.12", X86)
        os.symlink("python3.12", self.arm / "bin/python3")
        os.symlink("python3.12", self.x86 / "bin/python3")
        self.merge()
        link = self.out / "bin/python3"
        self.assertTrue(link.is_symlink())
        self.assertEqual(os.readlink(link), "python3.12")

    def test_pruned_dirs_absent(self):
        for tree in (self.arm, self.x86):
            for rel in ("lib/python3.12/test/t.py", "lib/python3.12/idlelib/i.py",
                        "lib/python3.12/json/__pycache__/j.pyc",
                        "include/python3.12/Python.h", "lib/python3.12/json/__init__.py",
                        "lib/python3.12/tkinter/__init__.py", "share/man/m",
                        "lib/python3.12/ensurepip/e.py",
                        "lib/python3.12/config-3.12-darwin/Makefile",
                        "lib/python3.12/lib-dynload/_tkinter.cpython-312-darwin.so",
                        "bin/pip3", "bin/python3-config", "bin/python3", "bin/python3.12",
                        "lib/tcl9.0/init.tcl", "lib/tk9.0/tk.tcl", "lib/libtcl9.0.dylib",
                        "lib/libpython3.12.dylib",
                        "lib/python3.12/site-packages/pip/__init__.py"):
                self.put(tree, rel, "x")
        self.merge()
        out = self.out
        for gone in ("lib/python3.12/test", "lib/python3.12/idlelib",
                     "lib/python3.12/json/__pycache__", "include", "share",
                     "lib/python3.12/tkinter", "lib/python3.12/ensurepip",
                     "lib/python3.12/config-3.12-darwin",
                     "lib/python3.12/lib-dynload/_tkinter.cpython-312-darwin.so",
                     "bin/pip3", "bin/python3-config", "lib/tcl9.0", "lib/tk9.0",
                     "lib/libtcl9.0.dylib", "lib/python3.12/site-packages/pip"):
            self.assertFalse((out / gone).exists(), gone)
        self.assertTrue((out / "lib/python3.12/json/__init__.py").exists())
        self.assertTrue((out / "lib/libpython3.12.dylib").exists())
        self.assertTrue((out / "bin/python3").exists())
        self.assertTrue((out / "bin/python3.12").exists())

    def test_only_in_one_tree_copied_and_reported(self):
        self.put(self.arm, "lib/only_arm.txt", "a")
        self.put(self.x86, "lib/only_x86.txt", "b")
        self.merge()
        self.assertEqual((self.out / "lib/only_arm.txt").read_text(), "a")
        self.assertEqual((self.out / "lib/only_x86.txt").read_text(), "b")
        self.assertTrue(any("only_arm.txt" in m for m in self.stderr))
        self.assertTrue(any("only_x86.txt" in m for m in self.stderr))


if __name__ == "__main__":
    unittest.main()
