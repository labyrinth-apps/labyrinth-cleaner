"""The --publish path of scripts/build_app.sh, run against fakes only.

The script is copied into a temporary root (it derives ROOT from its own path), with a fake
VERSION, Sparkle key and prebuilt DMG, and LABYRINTH_SKIP_BUILD=1. Fake gh, xcrun and
generate_appcast sit first on PATH and record their argv; fake signing and build tools fail
loudly if anything reaches them. Nothing here talks to GitHub or Apple.
"""
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUILD_APP = REPO / "scripts" / "build_app.sh"
TAG = "v0.0.0-test"
DMG_NAME = "Labyrinth-Cleaner-0.0.0-test-macOS.dmg"
DEFAULT_REPO = "labyrinth-apps/labyrinth-cleaner"
KEY = "A" * 43 + "="          # the shape of an Ed25519 public key from generate_keys -p
OTHER_KEY = "B" * 43 + "="
SCRIPTS = REPO / "scripts"


def feed(repo):
    return f"https://github.com/{repo}/releases/latest/download/appcast.xml"

RECORD = """#!/usr/bin/python3
import json, os, sys
name = os.path.basename(sys.argv[0])
with open(os.environ["FAKE_LOG"], "a") as f:
    f.write(json.dumps([name] + sys.argv[1:]) + "\\n")
"""

FAKES = {
    # gh release view exits 0 only when FAKE_RELEASE_EXISTS=1.
    "gh": RECORD + """
if sys.argv[1:3] == ["release", "view"]:
    sys.exit(0 if os.environ.get("FAKE_RELEASE_EXISTS") == "1" else 1)
if sys.argv[1:3] == ["auth", "status"]:
    sys.exit(0 if os.environ.get("FAKE_GH_AUTH", "1") == "1" else 1)
if sys.argv[1:3] == ["repo", "view"]:
    print(os.environ.get("FAKE_VISIBILITY", "PUBLIC"))
""",
    "xcrun": RECORD + """
sys.exit(1 if os.environ.get("FAKE_XCRUN_FAIL") == "1" else 0)
""",
    # Writes appcast.xml into the directory it is given, like Sparkle's tool.
    "generate_appcast": RECORD + """
args = sys.argv[1:]
prefix = args[args.index("--download-url-prefix") + 1]
folder = args[-1]
dmgs = [n for n in os.listdir(folder) if n.endswith(".dmg")]
items = "".join('<item><enclosure url="%s%s" sparkle:edSignature="FAKE"/></item>' % (prefix, n) for n in dmgs)
with open(os.path.join(folder, "appcast.xml"), "w") as f:
    f.write('<rss xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle"><channel>%s</channel></rss>' % items)
""",
    # attach -mountpoint M: "mounts" an app whose Info.plist has SUPublicEDKey=$FAKE_DMG_KEY
    # (no key at all when it is "<none>") and SUFeedURL=$FAKE_DMG_FEED; with FAKE_DMG_EMPTY=1
    # nothing shows up (a DMG already open in Finder). detach M empties M again.
    "hdiutil": RECORD + """
import plistlib, shutil
args = sys.argv[1:]
if args[0] == "attach" and os.environ.get("FAKE_DMG_EMPTY") == "1":
    pass
elif args[0] == "attach":
    mnt = args[args.index("-mountpoint") + 1]
    contents = os.path.join(mnt, "Labyrinth Cleaner.app", "Contents")
    os.makedirs(contents)
    info = {"CFBundleIdentifier": "com.labyrinth-apps.cleaner", "SUFeedURL": os.environ["FAKE_DMG_FEED"]}
    if os.environ["FAKE_DMG_KEY"] != "<none>":
        info["SUPublicEDKey"] = os.environ["FAKE_DMG_KEY"]
    with open(os.path.join(contents, "Info.plist"), "wb") as f:
        plistlib.dump(info, f)
elif args[0] == "detach":
    for name in os.listdir(args[1]):
        shutil.rmtree(os.path.join(args[1], name))
""",
}
# Publishing with LABYRINTH_SKIP_BUILD=1 must never build or sign anything.
FORBIDDEN = ["codesign", "security", "spctl", "swift", "notarytool", "dmgbuild"]


class BuildPublishTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = Path(self._tmp.name)
        self.root = tmp / "root"
        (self.root / "scripts").mkdir(parents=True)
        (self.root / "app").mkdir()
        (self.root / "build").mkdir()
        shutil.copy(BUILD_APP, self.root / "scripts" / "build_app.sh")
        (self.root / "VERSION").write_text("0.0.0-test\n")
        self.key = self.root / "app" / "sparkle_public_key.txt"
        self.key.write_text(KEY + "\n")
        self.dmg = self.root / "build" / DMG_NAME
        self.dmg.write_bytes(b"fake dmg bytes")

        self.bin = tmp / "bin"
        self.bin.mkdir()
        for name, body in FAKES.items():
            self._fake(name, body)
        for name in FORBIDDEN:
            self._fake(name, RECORD + "sys.exit(99)\n")
        self.log = tmp / "calls.jsonl"
        self.log.write_text("")

    def _fake(self, name, body):
        p = self.bin / name
        p.write_text(body)
        p.chmod(0o755)

    def run_build(self, *args, **env_overrides):
        env = {k: v for k, v in os.environ.items() if not k.startswith("LABYRINTH_")}
        env.update(PATH=f"{self.bin}:/usr/bin:/bin:/usr/sbin:/sbin", FAKE_LOG=str(self.log),
                   LABYRINTH_SKIP_BUILD="1", FAKE_DMG_KEY=KEY, FAKE_DMG_FEED=feed(DEFAULT_REPO),
                   GIT_CEILING_DIRECTORIES=str(self.root.parent))
        env.update(env_overrides)
        return subprocess.run(["/bin/bash", str(self.root / "scripts" / "build_app.sh"), *args],
                              cwd=self.root, env=env, capture_output=True, text=True, timeout=60)

    def calls(self, name=None):
        with open(self.log) as f:
            rows = [json.loads(line) for line in f if line.strip()]
        return [r[1:] for r in rows if name is None or r[0] == name]

    def gh_release(self, sub):
        return [c for c in self.calls("gh") if c[:2] == ["release", sub]]

    @staticmethod
    def assets(argv, tag=TAG):
        rest = argv[argv.index(tag) + 1:]
        return [os.path.basename(a) for a in rest[:rest.index("-R")]]

    def assert_nothing_built_or_signed(self):
        for name in FORBIDDEN:
            self.assertEqual(self.calls(name), [], f"{name} must not run when publishing a prebuilt DMG")
        # Read-only `gh repo view` (the visibility preflight) is the only repo subcommand allowed.
        self.assertEqual([c for c in self.calls("gh") if c[:1] == ["repo"] and c[:2] != ["repo", "view"]], [],
                         "gh repo create/edit must never run")

    def test_publish_creates_the_release_with_dmg_checksum_and_appcast(self):
        r = self.run_build("--publish", TAG)
        self.assertEqual(r.returncode, 0, r.stderr)
        creates = self.gh_release("create")
        self.assertEqual(len(creates), 1, self.calls("gh"))
        argv = creates[0]
        self.assertEqual(argv[2], TAG)
        self.assertEqual(argv[argv.index("-R") + 1], DEFAULT_REPO)
        self.assertEqual(self.assets(argv), [DMG_NAME, DMG_NAME + ".sha256", "appcast.xml"])
        self.assertEqual(self.gh_release("upload"), [])
        self.assertEqual(self.gh_release("view"), [["release", "view", TAG, "-R", DEFAULT_REPO]])
        # The shipped app's key was read from the DMG, attached read-only, and detached again.
        (attach,) = [c for c in self.calls("hdiutil") if c[0] == "attach"]
        self.assertIn("-readonly", attach)
        self.assertEqual(attach[-1], str(self.dmg))
        mnt = attach[attach.index("-mountpoint") + 1]
        self.assertIn(["detach", mnt, "-quiet"], self.calls("hdiutil"))
        self.assertFalse(os.path.exists(mnt), "the mount point is removed on exit")
        self.assert_nothing_built_or_signed()

    def test_appcast_uses_the_release_download_prefix(self):
        r = self.run_build("--publish", TAG)
        self.assertEqual(r.returncode, 0, r.stderr)
        (gen,) = self.calls("generate_appcast")
        prefix = gen[gen.index("--download-url-prefix") + 1]
        self.assertEqual(prefix, f"https://github.com/{DEFAULT_REPO}/releases/download/{TAG}/")
        appcast = Path(self.assets_path(self.gh_release("create")[0], "appcast.xml"))
        self.assertIn(prefix + DMG_NAME, appcast.read_text())
        self.assert_nothing_built_or_signed()

    def assets_path(self, argv, basename):
        rest = argv[argv.index(TAG) + 1:]
        return next(a for a in rest[:rest.index("-R")] if os.path.basename(a) == basename)

    def test_checksum_matches_the_dmg(self):
        r = self.run_build("--publish", TAG)
        self.assertEqual(r.returncode, 0, r.stderr)
        line = (self.root / "build" / (DMG_NAME + ".sha256")).read_text()
        self.assertEqual(line, f"{hashlib.sha256(b'fake dmg bytes').hexdigest()}  {DMG_NAME}\n")
        self.assert_nothing_built_or_signed()

    def test_existing_release_gets_upload_clobber(self):
        r = self.run_build("--publish", TAG, FAKE_RELEASE_EXISTS="1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.gh_release("create"), [])
        (argv,) = self.gh_release("upload")
        self.assertIn("--clobber", argv)
        self.assertEqual(argv[argv.index("-R") + 1], DEFAULT_REPO)
        self.assertEqual(self.assets(argv), [DMG_NAME, DMG_NAME + ".sha256", "appcast.xml"])
        self.assert_nothing_built_or_signed()

    def test_dmg_must_be_stapled_before_anything_is_uploaded(self):
        r = self.run_build("--publish", TAG, FAKE_XCRUN_FAIL="1")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn(["stapler", "validate", str(self.dmg)], self.calls("xcrun"))
        self.assertEqual([c for c in self.calls("gh") if c[:1] == ["release"]], [])  # nothing reached a release
        self.assert_nothing_built_or_signed()

    def test_releases_repo_env_overrides_repo_and_prefix(self):
        r = self.run_build("--publish", TAG, LABYRINTH_RELEASES_REPO="someone/elsewhere",
                           FAKE_DMG_FEED=feed("someone/elsewhere"))
        self.assertEqual(r.returncode, 0, r.stderr)
        (argv,) = self.gh_release("create")
        self.assertEqual(argv[argv.index("-R") + 1], "someone/elsewhere")
        (gen,) = self.calls("generate_appcast")
        self.assertIn("https://github.com/someone/elsewhere/releases/download/v0.0.0-test/", gen)
        self.assert_nothing_built_or_signed()

    def test_releases_repo_override_needs_a_matching_feed_and_never_pins_target(self):
        r = self.run_build("--publish", TAG, LABYRINTH_RELEASES_REPO="someone/elsewhere",
                           FAKE_DMG_FEED=feed("someone/elsewhere"))
        self.assertEqual(r.returncode, 0, r.stderr)
        (argv,) = self.gh_release("create")
        self.assertNotIn("--target", argv)
        self.assertIn("is not this checkout's origin", r.stderr)

    def test_preflight_checks_gh_login_and_public_visibility(self):
        r = self.run_build("--publish", TAG)
        self.assertEqual(r.returncode, 0, r.stderr)
        gh = self.calls("gh")
        self.assertEqual(gh[0], ["auth", "status"])
        self.assertEqual(gh[1], ["repo", "view", DEFAULT_REPO, "--json", "visibility", "-q", ".visibility"])

    def test_refuses_a_private_releases_repo(self):
        r = self.run_build("--publish", TAG, FAKE_VISIBILITY="PRIVATE")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn(f"{DEFAULT_REPO} is PRIVATE, not PUBLIC", r.stderr)
        self.assertEqual(self.gh_release("create") + self.gh_release("upload") + self.gh_release("view"), [])
        self.assertEqual(self.calls("hdiutil") + self.calls("xcrun") + self.calls("generate_appcast"), [])
        self.assert_nothing_built_or_signed()

    def test_refuses_when_gh_is_not_logged_in(self):
        r = self.run_build("--publish", TAG, FAKE_GH_AUTH="0")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("gh auth login", r.stderr)
        self.assertEqual(self.calls("gh"), [["auth", "status"]])

    def test_refuses_a_dmg_whose_feed_is_another_repo(self):
        r = self.run_build("--publish", TAG, FAKE_DMG_FEED=feed("someone/elsewhere"))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("looks for updates at", r.stderr)
        self.assertEqual(self.gh_release("create") + self.gh_release("upload"), [])
        self.assertEqual(self.calls("generate_appcast"), [])

    def write_template_feed(self, url):
        import plistlib
        with open(self.root / "app" / "Info.plist", "wb") as f:
            plistlib.dump({"SUFeedURL": url}, f)

    def test_building_publish_refuses_a_template_feed_for_another_repo(self):
        self.write_template_feed(feed("someone/elsewhere"))
        r = self.run_build("--publish", TAG, LABYRINTH_SKIP_BUILD="0")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("app/Info.plist's SUFeedURL", r.stderr)
        self.assertEqual(self.calls(), [])

    def test_building_publish_accepts_the_matching_template_feed(self):
        self.write_template_feed(feed(DEFAULT_REPO))
        r = self.run_build("--publish", TAG, LABYRINTH_SKIP_BUILD="0")
        # It gets past the feed and gh preflights; the fake `security` then finds no identity.
        self.assertNotIn("app/Info.plist's SUFeedURL", r.stderr)
        self.assertEqual(self.calls("gh")[:2], [["auth", "status"],
                         ["repo", "view", DEFAULT_REPO, "--json", "visibility", "-q", ".visibility"]])
        self.assertIn("Developer ID Application", r.stderr)
        self.assertEqual(self.calls("swift"), [])

    def test_refuses_when_the_app_does_not_show_up_in_the_mounted_dmg(self):
        r = self.run_build("--publish", TAG, FAKE_DMG_EMPTY="1")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("eject it in Finder and try again", r.stderr)
        self.assertEqual(self.gh_release("create") + self.gh_release("upload"), [])
        self.assertEqual(self.calls("generate_appcast"), [])

    def test_refuses_without_sparkle_public_key(self):
        self.key.unlink()
        r = self.run_build("--publish", TAG)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("app/sparkle_public_key.txt", r.stderr)
        self.assertIn("generate_keys -p > app/sparkle_public_key.txt", r.stderr)
        self.assertEqual(self.calls(), [])

    def test_refuses_whitespace_only_key(self):
        self.key.write_text("  \n\t\n")
        r = self.run_build("--publish", TAG)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("app/sparkle_public_key.txt is missing or empty", r.stderr)
        self.assertEqual(self.calls(), [])

    def test_refuses_a_key_of_the_wrong_shape(self):
        self.key.write_text("FAKEPUBLICKEY=\n")
        r = self.run_build("--publish", TAG)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("does not hold a Sparkle EdDSA public key", r.stderr)
        self.assertEqual(self.calls(), [])

    def test_refuses_a_dmg_whose_app_has_no_key(self):
        r = self.run_build("--publish", TAG, FAKE_DMG_KEY="<none>")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("has no SUPublicEDKey", r.stderr)
        self.assertEqual([c for c in self.calls("gh") if c[:1] == ["release"]], [])  # nothing reached a release
        self.assertEqual(self.calls("generate_appcast"), [])
        self.assertTrue(any(c[0] == "detach" for c in self.calls("hdiutil")))

    def test_refuses_a_dmg_whose_app_has_another_key(self):
        r = self.run_build("--publish", TAG, FAKE_DMG_KEY=OTHER_KEY)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("different SUPublicEDKey", r.stderr)
        self.assertEqual([c for c in self.calls("gh") if c[:1] == ["release"]], [])  # nothing reached a release
        self.assertEqual(self.calls("generate_appcast"), [])

    def git_repo(self, pushed):
        """A checkout whose origin is the releases repo; `pushed` adds a remote-tracking ref at HEAD."""
        git = ["git", "-C", str(self.root), "-c", "user.name=t", "-c", "user.email=t@example.com"]
        steps = [["init", "-q"], ["remote", "add", "origin", f"https://github.com/{DEFAULT_REPO}.git"],
                 ["add", "VERSION"], ["commit", "-q", "-m", "x"]]
        if pushed:
            steps.append(["update-ref", "refs/remotes/origin/main", "HEAD"])
        for argv in steps:
            subprocess.run(git + argv, check=True, capture_output=True)
        return subprocess.run(git + ["rev-parse", "HEAD"], check=True, capture_output=True,
                              text=True).stdout.strip()

    def test_create_pins_the_built_commit_when_the_remote_has_it(self):
        head = self.git_repo(pushed=True)
        r = self.run_build("--publish", TAG)
        self.assertEqual(r.returncode, 0, r.stderr)
        (argv,) = self.gh_release("create")
        self.assertEqual(argv[argv.index("--target") + 1], head)

    def test_create_without_the_commit_on_the_remote_warns_and_does_not_pin(self):
        self.git_repo(pushed=False)
        r = self.run_build("--publish", TAG)
        self.assertEqual(r.returncode, 0, r.stderr)
        (argv,) = self.gh_release("create")
        self.assertNotIn("--target", argv)
        self.assertIn("is not on the remote", r.stderr)

    def test_refuses_without_notarization(self):
        r = self.run_build("--publish", TAG, "--no-notarize")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("requires notarization", r.stderr)
        self.assertEqual(self.calls(), [])

    def test_refuses_debug_publish(self):
        r = self.run_build("--debug", "--publish", TAG)
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.calls(), [])

    def test_tag_must_match_version(self):
        r = self.run_build("--publish", "v9.9.9")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("does not match VERSION", r.stderr)
        self.assertEqual(self.calls(), [])

    def test_skip_build_needs_publish(self):
        r = self.run_build()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("LABYRINTH_SKIP_BUILD=1 only goes with --publish", r.stderr)
        self.assertEqual(self.calls(), [])

    def test_sign_needs_debug(self):
        r = self.run_build("--sign", LABYRINTH_SKIP_BUILD="0")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("--sign goes with --debug", r.stderr)
        self.assertEqual(self.calls(), [])


class BuildNumberTest(unittest.TestCase):
    """CFBundleVersion = major*10000 + minor*100 + patch, from VERSION (scripts/build_number.sh)."""

    def number(self, *args):
        return subprocess.run(["/bin/bash", str(SCRIPTS / "build_number.sh"), *args],
                              capture_output=True, text=True, timeout=30)

    def test_versions(self):
        for version, expected in (("0.1.0", "100"), ("1.2.3", "10203"), ("10.0.99", "100099"),
                                  ("2.0.0-beta.1", "20000")):
            r = self.number(version)
            self.assertEqual((r.returncode, r.stdout.strip()), (0, expected), version)

    def test_default_is_the_version_file(self):
        version = (REPO / "VERSION").read_text().strip()
        self.assertEqual(self.number().stdout.strip(), self.number(version).stdout.strip())

    def test_rejects_what_would_break_ordering(self):
        for bad in ("1.100.0", "1.2", "abc"):
            self.assertNotEqual(self.number(bad).returncode, 0, bad)

    def test_build_app_uses_it(self):
        text = BUILD_APP.read_text()
        self.assertIn('build="$("$ROOT/scripts/build_number.sh" "$VERSION")"', text)
        self.assertNotIn("rev-list", text)


if __name__ == "__main__":
    unittest.main()
