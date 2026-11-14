"""Tests for the devclean web UI server. Everything runs against a fixture $HOME.

The real `xcrun` is never called; fixture mode is checked to refuse it.
"""

import contextlib
import http.client
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import devclean_core as core  # noqa: E402
import devclean_ui as ui  # noqa: E402
from test_devclean import cfg_for, fixture_sim, make_home  # noqa: E402

RUNTIME_26_5 = "runtime:91298CDE-FDEA-4F7C-8FF5-E27CFAC0C3B6"
RUNTIME_27_2_BOOTED = "runtime:8CC38006-6DBA-4D63-9F9E-A0C3B02E5B16"
BOOTED_SIM = "6EE39A1D-D323-4C36-BDCE-872C80781FA4"   # iPhone 18 Pro Max, Booted
REAL_SUBPROCESS_RUN = subprocess.run


class Recorder:
    """Runner that records argv and deletes nothing."""

    def __init__(self):
        self.calls = []

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")


class Blocking:
    """Runner that waits for `release` before deleting (via the fixture runner)."""

    def __init__(self):
        self.release = threading.Event()
        self.started = threading.Event()

    def __call__(self, argv, **kw):
        self.started.set()
        self.release.wait(20)
        return core.fixture_runner(argv, **kw)


class ServerTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="devclean-test-"))
        self.home = make_home(self.tmp)
        self.srv = None

    def tearDown(self):
        if self.srv:
            self.srv.shutdown()
        shutil.rmtree(self.tmp)

    def start(self, sim=None, cfg=None, **kw):
        kw.setdefault("xcode_check", lambda: False)
        self.srv = ui.UIServer(cfg or cfg_for(self.home), sim or fixture_sim(), fixture=True, **kw)
        self.srv.start()
        return self.srv

    def req(self, method, path, body=None, headers=None, token=True, host=None, srv=None):
        srv = srv or self.srv
        h = {"Host": host or f"127.0.0.1:{srv.port}"}
        if token:
            h["X-Devclean-Token"] = srv.token if token is True else token
        if body is not None:
            h["Content-Type"] = "application/json"
            body = json.dumps(body)
        h.update(headers or {})
        conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=20)
        try:
            conn.request(method, path, body=body, headers=h)
            r = conn.getresponse()
            data = r.read().decode()
            hdrs = dict(r.getheaders())
        finally:
            conn.close()
        try:
            data = json.loads(data)
        except ValueError:
            pass
        return r.status, data, hdrs

    def scan(self):
        status, data, _ = self.req("GET", "/api/scan")
        self.assertEqual(status, 200, data)
        return data

    def clean(self, scan_id, keys):
        status, data, _ = self.req("POST", "/api/clean", {"scan_id": scan_id, "keys": keys})
        return status, data

    def wait_job(self, job_id, timeout=15):
        end = time.time() + timeout
        while time.time() < end:
            status, data, _ = self.req("GET", f"/api/job/{job_id}")
            self.assertEqual(status, 200, data)
            if data["state"] == "done":
                return data
            time.sleep(0.05)
        self.fail("job did not finish")

    def safe_keys(self, payload):
        return [k for r in payload["rows"] if r["group"] == "safe" for k in r["keys"]]

    def P(self, *parts):
        return os.path.join(self.home, "Documents", "Projects", *parts)


class SecurityTests(ServerTestCase):
    def test_index_served_without_token(self):
        self.start()
        status, body, hdrs = self.req("GET", "/", token=False)
        self.assertEqual(status, 200)
        self.assertIn('id="app"', body)
        self.assertIn("default-src 'none'", hdrs.get("Content-Security-Policy", ""))
        self.assertEqual(hdrs.get("Referrer-Policy"), "no-referrer")

    def test_api_without_token_403(self):
        self.start()
        self.assertEqual(self.req("GET", "/api/scan", token=False)[0], 403)
        self.assertEqual(self.req("GET", "/api/ping", token=False)[0], 403)

    def test_api_wrong_token_403(self):
        self.start()
        self.assertEqual(self.req("GET", "/api/scan", token="nope")[0], 403)

    def test_wrong_host_403(self):
        srv = self.start()
        self.assertEqual(self.req("GET", "/", host=f"evil.example:{srv.port}")[0], 403)
        self.assertEqual(self.req("GET", "/api/ping", host=f"evil.example:{srv.port}")[0], 403)
        self.assertEqual(self.req("GET", "/api/ping", host=f"localhost:{srv.port}")[0], 200)

    def test_post_foreign_origin_403(self):
        srv = self.start()
        body = {"scan_id": "x", "keys": ["y"]}
        for origin in ("http://evil.example", "null", f"http://127.0.0.1:{srv.port + 1}"):
            status = self.req("POST", "/api/clean", body, headers={"Origin": origin})[0]
            self.assertEqual(status, 403, origin)
        same = self.req("POST", "/api/clean", body, headers={"Origin": f"http://127.0.0.1:{srv.port}"})
        self.assertEqual(same[0], 409)  # passes the origin check, fails on the stale scan id

    def test_post_requires_json_content_type(self):
        self.start()
        status, _, _ = self.req("POST", "/api/clean", None,
                                headers={"Content-Type": "text/plain"})
        self.assertEqual(status, 415)

    def test_unknown_route_404(self):
        self.start()
        self.assertEqual(self.req("GET", "/api/nope")[0], 404)
        self.assertEqual(self.req("GET", "/etc/passwd", token=False)[0], 404)

    def test_binds_loopback_only(self):
        srv = self.start()
        self.assertEqual(srv.httpd.server_address[0], "127.0.0.1")
        self.assertTrue(srv.url.startswith(f"http://127.0.0.1:{srv.port}/?t="))
        self.assertGreaterEqual(len(srv.token), 40)


class ApiTests(ServerTestCase):
    def test_scan_payload_shape(self):
        self.start()
        p = self.scan()
        for key in ("scan_id", "rows", "segments", "headline", "disk", "duration_s"):
            self.assertIn(key, p)
        self.assertIn("safe:Documents/Projects/flutter_app", {r["id"] for r in p["rows"]})

    def test_clean_stale_scan_id_409(self):
        self.start()
        first = self.scan()
        self.scan()
        status, _ = self.clean(first["scan_id"], self.safe_keys(first))
        self.assertEqual(status, 409)
        self.assertTrue(os.path.isdir(self.P("flutter_app", "build")))

    def test_clean_kept_or_unknown_key_refused(self):
        rec = Recorder()
        self.start(runner=rec)
        p = self.scan()
        # Task 2: the "archives" info row is gone (archives are review items now), so a
        # kept key is the general iOS 27.2 runtime, kept because a simulator on it is booted.
        for keys in ([RUNTIME_27_2_BOOTED], ["simdevice:" + BOOTED_SIM], ["archives"], ["nope"],
                     ["homebrew-cache", "downloads"]):
            status, data = self.clean(p["scan_id"], keys)
            self.assertEqual(status, 400, keys)
            self.assertIn("unknown or protected", data["error"])
        self.assertEqual(rec.calls, [])
        self.assertTrue(os.path.isdir(os.path.join(self.home, "Library/Developer/Xcode/Archives")))
        self.assertTrue(os.path.isfile(os.path.join(self.home, "Downloads/movie.mkv")))
        self.assertTrue(os.path.isdir(os.path.join(self.home, "Library/Caches/Homebrew")))

    def test_bad_keys_400(self):
        self.start()
        p = self.scan()
        for keys in ([], "homebrew-cache", [1], None):
            self.assertEqual(self.clean(p["scan_id"], keys)[0], 400, keys)

    def test_forged_key_never_reaches_rm(self):
        rec = Recorder()
        self.start(runner=rec)
        p = self.scan()
        status, _ = self.clean(p["scan_id"], ["../../etc", "/etc", "rm -rf ~"])
        self.assertEqual(status, 400)
        self.assertEqual(rec.calls, [])

    def test_clean_safe_deletes_exactly_those(self):
        self.start()
        p = self.scan()
        keys = self.safe_keys(p)
        status, data = self.clean(p["scan_id"], keys)
        self.assertEqual(status, 200, data)
        job = self.wait_job(data["job_id"])
        self.assertEqual({i["status"] for i in job["items"]}, {"done"})
        self.assertEqual(len(job["items"]), len(keys))
        self.assertFalse(os.path.exists(self.P("flutter_app", "build")))
        self.assertFalse(os.path.exists(self.P("flutter_app", ".dart_tool")))
        self.assertFalse(os.path.exists(os.path.join(self.home, "Library/Caches/Homebrew")))
        for keep in ("Documents/taxes.pdf", "Downloads/movie.mkv",
                     "Library/Developer/Xcode/Archives/2026-11-01/App.xcarchive",
                     "Documents/Projects/flutter_app/lib/main.dart",
                     "Documents/Projects/flutter_app/pubspec.yaml",
                     "Documents/Projects/web_app/node_modules",
                     "Documents/Projects/web_app/build",
                     ".gradle/caches/9.10.0", ".gradle/caches/modules-2"):
            self.assertTrue(os.path.exists(os.path.join(self.home, keep)), keep)
        self.assertIn("free_before", job)
        self.assertIn("free_now", job)

    def test_fixture_runtime_delete_never_calls_xcrun(self):
        def guard(argv, *a, **kw):
            if argv and argv[0] == "xcrun":
                raise AssertionError(f"xcrun called in fixture mode: {argv}")
            return REAL_SUBPROCESS_RUN(argv, *a, **kw)

        self.start()
        p = self.scan()
        self.assertIn(RUNTIME_26_5, {k for r in p["rows"] for k in r["keys"]})
        with mock.patch.object(core.subprocess, "run", side_effect=guard):
            status, data = self.clean(p["scan_id"], [RUNTIME_26_5])
            self.assertEqual(status, 200, data)
            job = self.wait_job(data["job_id"])
        item = job["items"][0]
        self.assertEqual(item["status"], "failed")
        self.assertIn("fixture mode", item["message"])

    def test_default_runner_follows_fixture_flag(self):
        srv = ui.UIServer(cfg_for(self.home), core.NoSimctl(), fixture=True)
        self.assertIs(srv.runner, core.fixture_runner)
        srv.shutdown()
        srv = ui.UIServer(cfg_for(self.home), core.NoSimctl(), fixture=False)
        self.assertIs(srv.runner, subprocess.run)
        srv.shutdown()

    def test_vanished_item_is_refused_others_continue(self):
        self.start()
        p = self.scan()
        shutil.rmtree(self.P("flutter_app", "build"))
        status, data = self.clean(p["scan_id"], self.safe_keys(p))
        job = self.wait_job(data["job_id"])
        by = {i["key"]: i for i in job["items"]}
        self.assertEqual(by["flutter:Documents/Projects/flutter_app/build"]["status"], "refused")
        # Task 2: the gate re-scans first, so a vanished folder is reported as gone.
        self.assertIn("is gone", by["flutter:Documents/Projects/flutter_app/build"]["message"])
        others = [i["status"] for k, i in by.items() if k != "flutter:Documents/Projects/flutter_app/build"]
        self.assertTrue(others)
        self.assertEqual(set(others), {"done"})

    def test_second_clean_same_scan_is_409(self):
        self.start()
        p = self.scan()
        status, data = self.clean(p["scan_id"], ["homebrew-cache"])
        self.assertEqual(status, 200)
        self.wait_job(data["job_id"])
        self.assertEqual(self.clean(p["scan_id"], ["npm-cache"])[0], 409)
        self.assertTrue(os.path.isdir(os.path.join(self.home, ".npm/_cacache")))

    def test_clean_while_job_running_is_409(self):
        blocking = Blocking()
        self.start(runner=blocking)
        p = self.scan()
        status, data = self.clean(p["scan_id"], ["homebrew-cache"])
        self.assertEqual(status, 200)
        self.assertTrue(blocking.started.wait(5))
        p2 = self.scan()
        status2, data2 = self.clean(p2["scan_id"], ["npm-cache"])
        self.assertEqual(status2, 409)
        self.assertIn("already running", data2["error"])
        blocking.release.set()
        self.wait_job(data["job_id"])
        self.assertTrue(os.path.isdir(os.path.join(self.home, ".npm/_cacache")))

    def test_job_runs_unavailable_sims_before_runtime(self):
        from test_devclean import orphan_sim
        rec = Recorder()
        self.start(sim=orphan_sim(), runner=rec)
        p = self.scan()
        status, data = self.clean(p["scan_id"], ["runtime:UUID-265", "simctl-unavailable"])
        self.assertEqual(status, 200, data)
        job = self.wait_job(data["job_id"])
        self.assertEqual([i["key"] for i in job["items"]], ["simctl-unavailable", "runtime:UUID-265"])
        self.assertEqual(rec.calls[0], ["xcrun", "simctl", "delete", "unavailable"])

    def test_finish_jobs_completes_current_item_and_cancels_rest(self):
        blocking = Blocking()
        srv = self.start(runner=blocking)
        p = self.scan()
        status, data = self.clean(p["scan_id"], ["homebrew-cache", "npm-cache"])
        self.assertEqual(status, 200)
        self.assertTrue(blocking.started.wait(5))
        stopper = threading.Thread(target=srv.finish_jobs)
        stopper.start()
        time.sleep(0.3)
        self.assertTrue(stopper.is_alive())          # waits for the running delete
        blocking.release.set()
        stopper.join(10)
        self.assertFalse(stopper.is_alive())
        job = self.req("GET", f"/api/job/{data['job_id']}")[1]
        self.assertEqual([i["status"] for i in job["items"]], ["done", "cancelled"])
        self.assertEqual(job["state"], "done")
        self.assertTrue(os.path.isdir(os.path.join(self.home, ".npm/_cacache")))

    def test_job_unknown_404(self):
        self.start()
        self.assertEqual(self.req("GET", "/api/job/nope")[0], 404)

    def test_concurrent_scans_share_result(self):
        def slow():
            time.sleep(0.5)
            return False

        self.start(xcode_check=slow)
        ids = []
        threads = [threading.Thread(target=lambda: ids.append(self.scan()["scan_id"]))
                   for _ in range(2)]
        for t in threads:
            t.start()
            time.sleep(0.1)
        for t in threads:
            t.join(10)
        self.assertEqual(len(ids), 2)
        self.assertEqual(ids[0], ids[1])

    def test_scan_error_returns_500_and_server_survives(self):
        def boom():
            raise RuntimeError("xcode check exploded")

        self.start(xcode_check=boom)
        status, data, _ = self.req("GET", "/api/scan")
        self.assertEqual(status, 500)
        self.assertIn("exploded", data["error"])
        self.assertEqual(self.req("GET", "/api/ping")[0], 200)

    # ------------------------------------------------------------ settings

    def settings_post(self, body):
        status, data, _ = self.req("POST", "/api/settings/project-folders", body)
        return status, data

    def make_project(self, rel):
        d = os.path.join(self.home, rel)
        os.makedirs(os.path.join(d, "build"))
        with open(os.path.join(d, "pubspec.yaml"), "w") as f:
            f.write("name: x")
        return d

    def test_settings_get(self):
        os.makedirs(os.path.join(self.home, "Developer"))
        self.start(cfg=core.Config(home=self.home))
        status, data, _ = self.req("GET", "/api/settings")
        self.assertEqual(status, 200, data)
        dev = os.path.join(self.home, "Developer")
        disk = data.pop("disk")
        self.assertEqual(data, {"project_folders": [dev], "auto_project_folders": [dev],
                                "fixed": False, "welcome": False})
        self.assertEqual(set(disk), {"free", "total"})
        self.assertEqual(self.req("GET", "/api/settings", token=False)[0], 403)

    def test_add_project_folder_then_rescan_shows_it(self):
        self.start(cfg=core.Config(home=self.home))
        work = os.path.join(self.home, "Work")
        self.make_project("Work/app")
        self.assertNotIn("safe:Work/app", {r["id"] for r in self.scan()["rows"]})
        status, data = self.settings_post({"add": work})
        self.assertEqual(status, 200, data)
        self.assertEqual(data["project_folders"], [work])
        self.assertEqual(data["auto_project_folders"], [])
        self.assertFalse(data["fixed"])
        self.assertEqual(core.load_settings(self.home)["project_folders"], [work])
        p = self.scan()
        self.assertIn("safe:Work/app", {r["id"] for r in p["rows"]})
        self.assertEqual(p["project_folders"], [work])
        self.assertTrue(os.path.isdir(os.path.join(work, "app", "build")))   # never deleted
        status, data = self.settings_post({"remove": work})
        self.assertEqual(status, 200, data)
        self.assertEqual(data["project_folders"], [])
        self.assertNotIn("safe:Work/app", {r["id"] for r in self.scan()["rows"]})
        self.assertTrue(os.path.isdir(os.path.join(work, "app", "build")))

    def test_add_bad_folder_400_with_reason(self):
        rec = Recorder()
        self.start(cfg=core.Config(home=self.home), runner=rec)
        status, data = self.settings_post({"add": self.tmp})     # outside the fixture home
        self.assertEqual(status, 400)
        self.assertIn("inside your home folder", data["error"])
        status, data = self.settings_post({"add": "/etc"})       # a symlink on macOS
        self.assertEqual(status, 400)
        self.assertTrue(data["error"])
        status, data = self.settings_post({"add": os.path.join(self.home, "Library")})
        self.assertEqual(status, 400)
        self.assertIn("Library", data["error"])
        for body in ({}, [], "x", 0, {"add": 1}, {"remove": None}, {"foo": "/x"},
                     {"add": self.home, "remove": self.home}):
            self.assertEqual(self.settings_post(body)[0], 400, body)
        trash = os.path.join(self.home, ".Trash")
        os.makedirs(os.path.join(trash, "oldapp"))
        for bad in (trash, os.path.join(trash, "oldapp")):
            status, data = self.settings_post({"add": bad})
            self.assertEqual(status, 400, bad)
            self.assertIn(".Trash", data["error"])
        self.assertEqual(core.load_settings(self.home), {})
        self.assertEqual(rec.calls, [])

    def test_settings_errors_are_400_not_500(self):
        self.start(cfg=core.Config(home=self.home))
        work = os.path.join(self.home, "Work")
        os.makedirs(work)
        core.save_settings(self.home, {"project_folders": 5})
        status, data = self.settings_post({"add": work})
        self.assertEqual(status, 200, data)
        self.assertEqual(data["project_folders"], [work])
        for exc in (TypeError("bad type"), OSError("disk full")):
            with mock.patch.object(core, "add_project_folder", side_effect=exc):
                status, data = self.settings_post({"add": work})
            self.assertEqual(status, 400, exc)
            self.assertTrue(data["error"])
        self.assertEqual(self.req("GET", "/api/settings")[0], 200)   # server still fine

    def test_settings_change_invalidates_scan_id(self):
        self.start(cfg=core.Config(home=self.home))
        p = self.scan()
        work = os.path.join(self.home, "Work")
        os.makedirs(work)
        self.assertEqual(self.settings_post({"add": work})[0], 200)
        status, _ = self.clean(p["scan_id"], ["homebrew-cache"])
        self.assertEqual(status, 409)
        self.assertTrue(os.path.isdir(os.path.join(self.home, "Library/Caches/Homebrew")))

    def test_fixed_roots_settings_409(self):
        self.start(fixed_roots=True)
        status, data, _ = self.req("GET", "/api/settings")
        self.assertEqual(status, 200)
        self.assertTrue(data["fixed"])
        self.assertEqual(data["project_folders"], [self.P()])
        work = os.path.join(self.home, "Work")
        os.makedirs(work)
        status, _ = self.settings_post({"add": work})
        self.assertEqual(status, 409)
        self.assertEqual(self.settings_post({"foo": work})[0], 400)    # shape is checked first
        self.assertEqual(core.load_settings(self.home), {})

    # ------------------------------------------------------------ Trash

    def downloads_fixture(self):
        dmg = os.path.join(self.home, "Downloads", "Setup.dmg")
        with open(dmg, "w") as f:
            f.write("d" * 2000)
        old = time.time() - 20 * 86400
        os.utime(dmg, (old, old))
        trash = os.path.join(self.home, ".Trash")
        os.makedirs(trash)
        with open(os.path.join(trash, "old.bin"), "w") as f:
            f.write("x" * 3000)
        helper = os.path.join(self.tmp, "fake-trash-helper")
        with open(helper, "w") as f:
            f.write(f'#!/bin/sh\nshift 2; mkdir -p "{trash}" && mv "$1" "{trash}/"\n')
        os.chmod(helper, 0o755)
        return dmg, trash, helper

    def test_job_runs_empty_trash_first(self):
        dmg, trash, helper = self.downloads_fixture()
        rec = Recorder()
        self.start(runner=rec)
        with mock.patch.dict(os.environ, {core.TRASH_HELPER_ENV: helper}):
            p = self.scan()
            self.assertTrue(p["trash_helper"])
            status, data = self.clean(p["scan_id"], ["download:Setup.dmg", "empty-trash"])
            self.assertEqual(status, 200, data)
            job = self.wait_job(data["job_id"])
        self.assertEqual([i["key"] for i in job["items"]], ["empty-trash", "download:Setup.dmg"])
        self.assertEqual([i["status"] for i in job["items"]], ["done", "trashed"])
        self.assertEqual(rec.calls[0], core._general().TRASH_ARGV)
        self.assertEqual(rec.calls[1], [helper, "--home", self.home, dmg])
        self.assertTrue(os.path.isfile(dmg))            # the recorder moves nothing

    def test_trash_job_status_trashed(self):
        dmg, trash, helper = self.downloads_fixture()
        self.start()
        with mock.patch.dict(os.environ, {core.TRASH_HELPER_ENV: helper}):
            p = self.scan()
            row = next(r for r in p["rows"] if r["id"] == "download:Setup.dmg")
            self.assertEqual((row["group"], row["kind"]), ("review", "trash"))
            status, data = self.clean(p["scan_id"], ["download:Setup.dmg"])
            self.assertEqual(status, 200, data)
            job = self.wait_job(data["job_id"])
        self.assertEqual(job["items"][0]["status"], "trashed", job)
        self.assertFalse(os.path.exists(dmg))
        self.assertTrue(os.path.isfile(os.path.join(trash, "Setup.dmg")))

    def test_trash_without_helper_is_refused(self):
        dmg, trash, helper = self.downloads_fixture()
        self.start()
        env = {k: v for k, v in os.environ.items() if k != core.TRASH_HELPER_ENV}
        with mock.patch.dict(os.environ, env, clear=True):
            p = self.scan()
            self.assertFalse(p["trash_helper"])
            status, data = self.clean(p["scan_id"], ["download:Setup.dmg"])
            job = self.wait_job(data["job_id"])
        self.assertEqual(job["items"][0]["status"], "refused")
        self.assertTrue(os.path.isfile(dmg))


    def test_job_batches_finder_items_and_keeps_helper_results(self):
        import dataclasses
        import plistlib
        apps_root = os.path.join(self.home, ".fixture-root", "Applications")
        paths = []
        for name, bid in (("Mine.app", "com.mine"), ('Café "Beta".app', "com.cafe"),
                          ("Root (v2).app", "com.root")):
            app = os.path.join(apps_root, name)
            os.makedirs(os.path.join(app, "Contents"))
            with open(os.path.join(app, "Contents", "Info.plist"), "wb") as f:
                plistlib.dump({"CFBundleIdentifier": bid, "CFBundleShortVersionString": "1"}, f)
            paths.append(app)
        mine, cafe, root = paths
        trash = os.path.join(self.home, ".Trash")
        helper = os.path.join(self.tmp, "fake-trash-helper")
        with open(helper, "w") as f:
            f.write(f'#!/bin/sh\nshift 2; mkdir -p "{trash}" && mv "$1" "{trash}/"\n')
        os.chmod(helper, 0o755)
        finder_calls = []

        def runner(argv, **kw):
            if argv and argv[0] == "osascript":
                finder_calls.append(list(argv))
                return subprocess.CompletedProcess(argv, 1, "",
                                                   "execution error: User canceled. (-128)")
            return REAL_SUBPROCESS_RUN(argv, **kw)

        # Two apps look root-owned (to the scan and the gate alike); none is really touched.
        real_lstat = os.lstat

        def lstat(path, *a, **kw):
            st = real_lstat(path, *a, **kw)
            if isinstance(path, str) and path in (cafe, root):
                return os.stat_result(tuple(st)[:4] + (0,) + tuple(st)[5:])
            return st

        srv = self.start(runner=runner)
        old = time.time() - 400 * 86400
        srv.env = dataclasses.replace(srv.env, app_metadata=lambda ps: {
            p: {"last_used": old, "size": 10, "archs": None} for p in ps})
        keys = ["app:" + p for p in paths]
        with mock.patch.dict(os.environ, {core.TRASH_HELPER_ENV: helper}), \
                mock.patch.object(os, "lstat", lstat):
            p = self.scan()
            kinds = {r["id"]: r["kind"] for r in p["rows"] if r["id"] in keys}
            self.assertEqual(kinds, {keys[0]: "trash", keys[1]: "finder-trash",
                                     keys[2]: "finder-trash"})
            status, data = self.clean(p["scan_id"], keys)
            self.assertEqual(status, 200, data)
            job = self.wait_job(data["job_id"])
        by_key = {i["key"]: i for i in job["items"]}
        self.assertEqual(by_key[keys[0]]["status"], "trashed", job)
        for k in keys[1:]:
            self.assertEqual((by_key[k]["status"], by_key[k]["message"]),
                             ("failed", "Cancelled: nothing was moved"))
        self.assertEqual(finder_calls, [core.FINDER_TRASH_ARGV_HEAD + ["--", cafe, root]])
        self.assertFalse(os.path.exists(mine))
        self.assertTrue(os.path.isdir(os.path.join(trash, "Mine.app")))
        self.assertTrue(os.path.isdir(cafe) and os.path.isdir(root))


class LifecycleTests(ServerTestCase):
    def test_idle_shutdown_after_timeout(self):
        srv = ui.UIServer(cfg_for(self.home), fixture_sim(), fixture=True,
                          xcode_check=lambda: False, idle_timeout=0.3)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        t.join(5)
        self.assertFalse(t.is_alive())

    def test_idle_shutdown_waits_for_job(self):
        blocking = Blocking()
        srv = ui.UIServer(cfg_for(self.home), fixture_sim(), fixture=True,
                          xcode_check=lambda: False, idle_timeout=0.3, runner=blocking)
        self.srv = srv
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        p = self.scan()
        status, data = self.clean(p["scan_id"], ["homebrew-cache"])
        self.assertEqual(status, 200)
        time.sleep(1.2)
        self.assertTrue(t.is_alive())
        blocking.release.set()
        t.join(7)
        self.assertFalse(t.is_alive())
        self.assertFalse(os.path.exists(os.path.join(self.home, "Library/Caches/Homebrew")))
        self.srv = None

    def url_file(self):
        return os.path.join(self.home, ".cache", "devclean", "ui.url")

    def run_main(self, *extra):
        out = io.StringIO()
        result = {}

        def target():
            with contextlib.redirect_stdout(out):
                result["code"] = ui.main(["--home", self.home, "--no-open", *extra])

        t = threading.Thread(target=target, daemon=True)
        t.start()
        return t, out, result

    def wait_for(self, cond, timeout=10):
        end = time.time() + timeout
        while time.time() < end:
            if cond():
                return True
            time.sleep(0.05)
        return False

    def test_reuse_live_server(self):
        t1, _, r1 = self.run_main("--idle-timeout", "1.5")
        self.assertTrue(self.wait_for(lambda: os.path.exists(self.url_file())))
        with open(self.url_file()) as f:
            url = f.read().strip()
        self.assertEqual(stat.S_IMODE(os.stat(self.url_file()).st_mode), 0o600)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = ui.main(["--home", self.home, "--no-open"])
        self.assertEqual(code, 0)
        self.assertIn(url, out.getvalue())
        t1.join(10)
        self.assertFalse(t1.is_alive())
        self.assertEqual(r1["code"], 0)
        self.assertFalse(os.path.exists(self.url_file()))

    def test_reuse_refused_when_options_differ(self):
        """A fixture-mode request must never be handed a server with real xcrun."""
        t1, _, _ = self.run_main("--idle-timeout", "1.5")
        self.assertTrue(self.wait_for(lambda: os.path.exists(self.url_file())))
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = ui.main(["--home", self.home, "--no-open",
                            "--simctl-fixture", os.path.join(HERE, "fixtures", "real_2026-11-11")])
        self.assertEqual(code, 1)
        self.assertIn("different options", out.getvalue())
        t1.join(10)

    def test_ping_reports_options(self):
        srv = self.start()
        status, data, _ = self.req("GET", "/api/ping")
        self.assertEqual(status, 200)
        self.assertEqual(data["options"]["fixture"], True)

    def test_stale_url_file_ignored(self):
        os.makedirs(os.path.dirname(self.url_file()))
        stale = "http://127.0.0.1:1/?t=stale"
        with open(self.url_file(), "w") as f:
            f.write(stale)
        t, _, _ = self.run_main("--idle-timeout", "1.5")

        def replaced():
            try:
                with open(self.url_file()) as f:
                    return f.read().strip() not in ("", stale)
            except OSError:
                return False

        self.assertTrue(self.wait_for(replaced))
        self.assertEqual(stat.S_IMODE(os.stat(self.url_file()).st_mode), 0o600)
        t.join(10)
        self.assertFalse(os.path.exists(self.url_file()))

    def test_sigterm_removes_url_file(self):
        import signal
        proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "devclean"), "ui",
                                 "--home", self.home, "--no-open"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            self.assertTrue(self.wait_for(lambda: os.path.exists(self.url_file())))
            proc.send_signal(signal.SIGTERM)
            proc.wait(10)
        finally:
            if proc.poll() is None:
                proc.kill()
        self.assertFalse(os.path.exists(self.url_file()))

    def test_two_simultaneous_starts_give_one_server(self):
        cmd = [sys.executable, os.path.join(ROOT, "devclean"), "ui", "--home", self.home,
               "--no-open", "--idle-timeout", "2"]
        procs = [subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                 for _ in range(2)]
        outs = [pr.communicate(timeout=30)[0] for pr in procs]
        started = [o for o in outs if "devclean UI: http" in o]
        reused = [o for o in outs if "already running" in o]
        self.assertEqual((len(started), len(reused)), (1, 1), outs)

    def test_fake_docker_needs_home(self):
        df = os.path.join(self.tmp, "df.json")
        with open(df, "w") as f:
            f.write('{"Type":"Build Cache","Reclaimable":"1GB"}')
        r = subprocess.run([sys.executable, os.path.join(ROOT, "devclean"), "ui", "--no-open",
                            "--fake-docker", df], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("--fake-docker needs --home", r.stderr)

    def test_fake_docker_answers_only_system_df(self):
        df = os.path.join(self.tmp, "df.json")
        with open(df, "w") as f:
            f.write('{"Type":"Build Cache","Reclaimable":"1GB"}')
        run = ui.fake_docker(df)
        ok = run(["docker", "system", "df", "--format", "json"], capture_output=True, text=True)
        self.assertEqual((ok.returncode, ok.stdout), (0, '{"Type":"Build Cache","Reclaimable":"1GB"}'))
        for argv in (["docker", "builder", "prune", "-f"], ["docker", "image", "prune", "-a", "-f"],
                     ["rm", "-rf", "/"]):
            self.assertNotEqual(run(argv).returncode, 0, argv)
        srv = ui.UIServer(cfg_for(self.home), core.NoSimctl(), fixture=True,
                          xcode_check=lambda: False, docker=run)
        self.srv = srv
        srv.start()
        p = self.scan()
        row = next(r for r in p["rows"] if r["id"] == "docker:builder")
        self.assertEqual(row["size"], 10 ** 9)
        status, data = self.clean(p["scan_id"], ["docker:builder"])
        job = self.wait_job(data["job_id"])
        self.assertEqual(job["items"][0]["status"], "refused")   # fixture mode never prunes

    def test_cli_dispatches_ui(self):
        r = subprocess.run([sys.executable, os.path.join(ROOT, "devclean"), "ui", "--help"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("--no-open", r.stdout)


# A minimal DOM for running the page script in node: every element is a stub that
# records what the script sets; fetch records each POST body and answers 503.
PAGE_HARNESS = r"""
class Node {
  constructor(tag) { this.tag = tag; this.kids = []; this.style = {}; this.dataset = {}; this.attrs = {};
    this.hidden = false; this.disabled = false; this.checked = false; this.inert = false; this.textContent = "";
    const cls = new Set(); this.classList = { add: c => cls.add(c), remove: c => cls.delete(c),
      toggle: (c, on) => on ? cls.add(c) : cls.delete(c), contains: c => cls.has(c) }; this.children = []; }
  get firstChild() { return this.kids[0] || null; }
  removeChild(k) { this.kids.splice(this.kids.indexOf(k), 1); }
  append(...k) { this.kids.push(...k); }
  setAttribute(k, v) { this.attrs[k] = v; } getAttribute(k) { return this.attrs[k]; }
  addEventListener(t, f) { (this.handlers = this.handlers || {})[t] = f; } focus() {} closest() { return null; }
  querySelector() { return new Node("x"); } querySelectorAll() { return []; }
}
const byId = {};
global.Node = Node;
global.document = { getElementById: id => byId[id] || (byId[id] = new Node(id)),
  createElement: t => new Node(t), createTextNode: t => ({ text: t }),
  querySelectorAll: () => [], addEventListener: () => {}, activeElement: null };
global.window = { scrollTo() {} };
global.location = { search: "", pathname: "/" };
global.history = { replaceState() {} };
global.sessionStorage = { getItem: () => "t", setItem() {} };
const posts = [];
global.fetch = async (path, opt) => { if (opt.method === "POST") posts.push(JSON.parse(opt.body).keys);
  return { status: 503, json: async () => ({ error: "test" }) }; };
/*PAGE*/
;(async () => {
  const row = (id, extra) => Object.assign({ id, group: "safe", kind: "path", keys: [id], size: 1, warning: "",
    members: [{ key: id, label: id, size: 1, deletes: "" }], label: id, cost: "", restore: "", deletes: "" }, extra);
  S.data = { scan_id: "s1", trash_helper: true, disk: { free: 10, total: 100 }, small_bytes: 0, segments: [],
    rows: [row("a"), row("b", { group: "check", warning: "W" }), row("c", { group: "review", kind: "trash" }),
      row("d", { group: "apps", kind: "finder-trash", category: "Applications" }),
      row("k", { group: "apps", kind: "info", keys: [], category: "Applications", size: 1000, cost: "Keep: newest version" })] };
  S.data.rows[3].size = 10; S.data.rows[3].version = "88.1"; S.data.rows[3].cost = "older copy · keep Figma 101.6";
  const res = {};
  const take = () => posts.splice(0);
  S.sel = new Set(["a"]); openModal();
  res.inert_open = [$("app").inert, $("action").inert];
  S.sel.add("b"); S.sel.add("c");                 // changed behind the dialog
  await doDelete(); res.sent_after_selection_change = take();
  closeModal(); res.inert_closed = [$("app").inert, $("action").inert];
  S.sel = new Set(["b"]); openModal(); $("mDelete").disabled = false;   // forced
  await doDelete(); res.sent_unacknowledged = take();
  closeModal();
  S.data.trash_helper = false; S.sel = new Set(["c"]); openModal(); $("mDelete").disabled = false;
  await doDelete(); res.sent_trash_without_app = take();
  closeModal();
  res.blocked_finder_without_app = blockedReason({ rows: [S.data.rows[3]] });
  S.data.trash_helper = true;
  res.blocked_finder_with_app = blockedReason({ rows: [S.data.rows[3]] });
  res.freeable_finder = freeable([S.data.rows[3]]);
  const apps = GROUPS.find(g => g.id === "apps");
  const cardOf = rows => groupCard(apps, rows);
  const find = (n, f) => { const out = []; (function w(x) { if (!x) return; if (f(x)) out.push(x); (x.kids || []).forEach(w); })(n); return out; };
  S.sel = new Set();
  const card = cardOf(rowsOf("apps"));
  res.cb_count = find(card, x => x.className === "cb").length;
  let sel = null;
  find(card, x => x.className === "selall toggle").forEach(b => { sel = b; });
  res.selall_found = !!sel;
  const texts = find(card, x => true).map(x => x.textContent || x.text).filter(Boolean);
  res.texts = texts;
  res.lock = find(card, x => x.className === "lock").map(x => [x.textContent, x.attrs.title]);
  res.gtotal = find(card, x => x.className === "gtotal").map(x => x.textContent);
  res.expect_total = human(10);
  if (sel) { sel.handlers.click(); res.selall_selected = [...S.sel].sort(); }
  S.sel = new Set(["b"]); openModal(); $("mAck").checked = true; refreshDelete();
  await doDelete(); res.sent_acknowledged = take();
  console.log(JSON.stringify(res));
})().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


class PageTests(unittest.TestCase):
    def html(self):
        with open(os.path.join(ROOT, "ui", "index.html")) as f:
            return f.read()

    def test_page_has_required_hooks(self):
        html = self.html()
        for needle in ('id="app"', "X-Devclean-Token", "/api/scan", "/api/clean", "/api/job/",
                       "sessionStorage", "replaceState", "Nothing goes to the Trash",
                       "Select stale", "Review &amp; clean", "Menu bar turns amber below 30 GB",
                       "The scan is out of date", "small item",
                       "Labyrinth Cleaner doesn't analyse system, app or personal files."):
            self.assertIn(needle, html)

    def test_page_handles_lost_server_and_cancelled_items(self):
        html = self.html()
        self.assertIn("Lost contact with Labyrinth Cleaner", html)
        self.assertIn("cancelled:", html)                 # status icon for cancelled items
        self.assertNotIn("terminal still running", html)  # also started from the menu bar

    def test_page_names_the_app_not_devclean(self):
        # The page calls itself Labyrinth Cleaner; "devclean" only appears as the Terminal
        # command (devclean ui), the token header, storage keys and code comments.
        import re
        html = self.html()
        html = re.sub(r"//[^\n]*", "", html)
        stray = [m.start() for m in re.finditer(r"(?i)devclean(?! ui)(?!-Token)(?!\.t\b)", html)]
        self.assertEqual(stray, [], [html[i - 40:i + 30] for i in stray])

    def test_page_has_new_copy(self):
        html = self.html()
        for needle in ("Review: moves to Trash", "I've read the warnings above",
                       "Deleted permanently", "Moved to Trash", "What happens",
                       "pickProjectFolder", "labyrinthAddedFolder",
                       "Moving to the Trash needs the Labyrinth Cleaner app.",
                       "Select all", "Select none", "Deletes", "Costs you", "To get it back",
                       "Moves to Trash", "found automatically", "Add project folder…",
                       "Add folders in the Labyrinth Cleaner app, or run devclean ui --projects DIR.",
                       "Moved to Trash: ", "aria-expanded", "/api/settings"):
            self.assertIn(needle, html)
        self.assertIn("S.sel = new Set()", html)
        self.assertNotIn("r.preselect", html)

    def test_no_html_injection_sinks(self):
        """Scan data (folder names!) must only reach the DOM as text."""
        html = self.html()
        for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(",
                     "new Function"):
            self.assertNotIn(sink, html)

    @unittest.skipUnless(shutil.which("node"), "needs node")
    def test_modal_sends_the_confirmed_keys_only(self):
        """Runs the page script in node against a tiny DOM stub. Changing the selection
        after the dialog opened must not change what Delete sends, and Delete must refuse
        unacknowledged warnings and Trash moves without the app."""
        script = re.search(r"<script>(.*)</script>", self.html(), re.S).group(1)
        js = PAGE_HARNESS.replace("/*PAGE*/", script)
        out = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        r = json.loads(out.stdout.strip().splitlines()[-1])
        self.assertEqual(r["sent_after_selection_change"], [["a"]])
        self.assertEqual(r["inert_open"], [True, True])
        self.assertEqual(r["inert_closed"], [False, False])
        self.assertEqual(r["sent_unacknowledged"], [])
        self.assertEqual(r["sent_trash_without_app"], [])
        self.assertEqual(r["sent_acknowledged"], [["b"]])

    @unittest.skipUnless(shutil.which("node"), "needs node")
    def test_page_applications_rows_in_node(self):
        script = re.search(r"<script>(.*)</script>", self.html(), re.S).group(1)
        js = PAGE_HARNESS.replace("/*PAGE*/", script)
        out = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        r = json.loads(out.stdout.strip().splitlines()[-1])
        self.assertEqual(r["blocked_finder_without_app"], "needs the app")
        self.assertEqual(r["blocked_finder_with_app"], "")
        self.assertEqual(r["freeable_finder"], 0)
        self.assertEqual(r["cb_count"], 1)          # the keeper row has no checkbox
        self.assertIn("older copy · keep Figma 101.6", r["texts"])   # the reason is on the row
        self.assertIn("v88.1", r["texts"])                           # the version
        self.assertEqual(r["lock"], [["Keep", "Keep: newest version"]])
        self.assertEqual(r["gtotal"], [r["expect_total"]])           # keeper size left out
        self.assertTrue(r["selall_found"])
        self.assertEqual(r["selall_selected"], ["d"])

    def test_page_has_applications_group(self):
        html = self.html()
        for needle in ('id: "apps"', "Copies and apps you haven't opened in a while. Removing moves them to the Trash.",
                       '"Ax"', "var(--apps)", 'data-f="apps"'):
            self.assertIn(needle, html)

    def test_modal_snapshot_hooks(self):
        html = self.html()
        self.assertIn("const keys = S.modalKeys;", html)
        self.assertNotIn("selectedRows().flatMap(r => r.keys)", html)
        self.assertIn('$("app").inert = true;', html)
        self.assertIn('$("action").inert = true;', html)

    @unittest.skipUnless(shutil.which("node"), "needs node")
    def test_js_human_matches_python(self):
        m = re.search(r"^function human\(n\) \{.*?^\}", self.html(), re.S | re.M)
        self.assertIsNotNone(m, "function human(n) { ... } not found")
        values = [0, 999, 1000, 1500, 145 * 1024 ** 2, int(9.9 * 1024 ** 3), 5 * 1024 ** 4, 61989425152]
        js = m.group(0) + "\nconsole.log(JSON.stringify(%s.map(human)));" % json.dumps(values)
        out = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=20)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(json.loads(out.stdout), [core.human(v) for v in values])
        self.assertIn("node", shutil.which("node"))


DEVCLEAN = os.path.join(ROOT, "devclean")


class AppModeTests(ServerTestCase):
    def setUp(self):
        super().setUp()
        self.procs = []

    def tearDown(self):
        for p in self.procs:
            if p.poll() is None:
                p.kill()
            p.wait()
            for f in (p.stdin, p.stdout, p.stderr):
                if f:
                    f.close()
        super().tearDown()

    def spawn(self, args):
        p = subprocess.Popen([sys.executable, DEVCLEAN, "ui", *args], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.procs.append(p)
        return p

    def start_app_server(self, extra=()):
        p = self.spawn(["--app", "--home", self.home, *extra])
        return p, json.loads(p.stdout.readline())

    def get(self, url, path, token=None, host=None):
        u = urlsplit(url)
        tok = parse_qs(u.query)["t"][0] if token is None else token
        conn = http.client.HTTPConnection("127.0.0.1", u.port, timeout=10)
        try:
            conn.request("GET", path, headers={"Host": host or f"127.0.0.1:{u.port}",
                                               "X-Devclean-Token": tok})
            r = conn.getresponse()
            return r.status, json.loads(r.read() or b"null")
        finally:
            conn.close()

    def url_file(self):
        return os.path.join(self.home, ".cache", "devclean", "ui.url")

    def test_begin_shutdown_ignores_further_sigterm(self):
        import signal
        old = signal.getsignal(signal.SIGTERM)
        try:
            signal.signal(signal.SIGTERM, ui._raise_interrupt)
            ui._begin_shutdown()
            self.assertEqual(signal.getsignal(signal.SIGTERM), signal.SIG_IGN)
        finally:
            signal.signal(signal.SIGTERM, old)

    def test_sigterm_handler_ignores_sigterm_before_raising(self):
        import signal
        old = signal.getsignal(signal.SIGTERM)
        try:
            signal.signal(signal.SIGTERM, ui._raise_interrupt)
            with self.assertRaises(KeyboardInterrupt):
                ui._raise_interrupt(signal.SIGTERM, None)
            self.assertEqual(signal.getsignal(signal.SIGTERM), signal.SIG_IGN)
            signal.signal(signal.SIGTERM, ui._raise_interrupt)
            with self.assertRaises(KeyboardInterrupt):
                ui._raise_interrupt(signal.SIGINT, None)
            self.assertEqual(signal.getsignal(signal.SIGTERM), ui._raise_interrupt)
        finally:
            signal.signal(signal.SIGTERM, old)

    def test_app_mode_handshake_and_stdin_eof_exit(self):
        p, hs = self.start_app_server()
        self.assertEqual(set(hs), {"labyrinth_server", "url"})
        self.assertEqual(hs["labyrinth_server"], 1)
        u = urlsplit(hs["url"])
        self.assertEqual(u.hostname, "127.0.0.1")
        status, body = self.get(hs["url"], "/api/settings")
        self.assertEqual(status, 200)
        self.assertIs(body["welcome"], True)
        p.stdin.close()
        self.assertEqual(p.wait(timeout=10), 0)
        self.assertEqual(p.stdout.read(), b"")          # nothing after the handshake line
        self.assertIn(b"fixture mode", p.stderr.read())   # the notice goes to stderr
        self.assertFalse(os.path.exists(self.url_file()))

    def test_app_mode_stdin_closed_at_start_exits_cleanly(self):
        # The stdin watcher can send its SIGTERM before serve_forever starts: that must still
        # be a clean exit (0), never an interrupt escaping as 130. Several runs: it was a race.
        for _ in range(6):
            p = subprocess.Popen([sys.executable, DEVCLEAN, "ui", "--app", "--home", self.home,
                                  "--no-simulators"], stdin=subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.procs.append(p)
            self.assertEqual(p.wait(timeout=30), 0, p.stderr.read().decode(errors="replace"))

    def test_app_mode_ignores_live_cli_server(self):
        cli = self.spawn(["--no-open", "--home", self.home, "--idle-timeout", "30"])
        cli.stdin.close()
        end = time.time() + 10
        while not os.path.exists(self.url_file()) and time.time() < end:
            time.sleep(0.05)
        with open(self.url_file()) as f:
            cli_url = f.read().strip()
        p, hs = self.start_app_server()
        self.assertNotEqual(hs["url"], cli_url)
        with open(self.url_file()) as f:
            self.assertEqual(f.read().strip(), cli_url)    # the URL file is untouched
        p.stdin.close()
        self.assertEqual(p.wait(timeout=10), 0)
        self.assertTrue(os.path.exists(self.url_file()))
        cli.terminate()
        cli.wait(10)

    def test_app_flag_hidden_from_help(self):
        r = subprocess.run([sys.executable, DEVCLEAN, "ui", "--help"],
                           capture_output=True, text=True, timeout=30)
        self.assertNotIn("--app", r.stdout)

    def test_idle_timeout_none_never_exits(self):
        srv = self.start(idle_timeout=None)
        time.sleep(0.3)
        self.assertTrue(srv._serving)

    def test_stop_mid_job_finishes_current_skips_rest(self):
        blocking = Blocking()
        srv = self.start(runner=blocking)
        p = self.scan()
        keys = self.safe_keys(p)[:3]
        self.assertEqual(len(keys), 3)
        status, data = self.clean(p["scan_id"], keys)
        self.assertEqual(status, 200, data)
        self.assertTrue(blocking.started.wait(5))
        stopper = threading.Thread(target=srv.finish_jobs)
        stopper.start()
        blocking.release.set()
        stopper.join(10)
        self.assertFalse(stopper.is_alive())
        job = self.req("GET", f"/api/job/{data['job_id']}")[1]
        self.assertEqual([i["status"] for i in job["items"]], ["done", "cancelled", "cancelled"])
        self.assertTrue(os.path.isdir(os.path.join(self.home, ".npm/_cacache")))

    def test_welcome_flag_and_done(self):
        core.save_settings(self.home, {"project_folders": [self.home], "other": 1})
        srv = self.start(app_mode=True)
        self.assertIs(self.req("GET", "/api/settings")[1]["welcome"], True)
        status, data, _ = self.req("POST", "/api/settings/welcome-done", {})
        self.assertEqual((status, data), (200, {"ok": True}))
        self.assertIs(self.req("GET", "/api/settings")[1]["welcome"], False)
        saved = core.load_settings(self.home)
        self.assertIs(saved["welcome_done"], True)
        self.assertEqual(saved["project_folders"], [self.home])   # other keys kept
        self.assertEqual(saved["other"], 1)

    def test_settings_report_disk_free_and_total(self):
        self.start(app_mode=True)
        disk = self.req("GET", "/api/settings")[1]["disk"]
        for k in ("free", "total"):
            self.assertIs(type(disk[k]), int, k)
            self.assertGreater(disk[k], 0, k)
        self.assertLessEqual(disk["free"], disk["total"])

    def test_welcome_false_for_non_app_server(self):
        self.start()
        self.assertIs(self.req("GET", "/api/settings")[1]["welcome"], False)

    def test_welcome_done_checks(self):
        self.start(app_mode=True)
        url = "/api/settings/welcome-done"
        self.assertEqual(self.req("POST", url, {}, token=False)[0], 403)
        self.assertEqual(self.req("POST", url, {}, host="evil.com")[0], 403)
        self.assertEqual(self.req("POST", url, {}, headers={"Origin": "http://evil.com"})[0], 403)
        self.assertEqual(self.req("POST", url, {}, headers={"Content-Type": "text/plain"})[0], 415)
        self.assertIs(self.req("GET", "/api/settings")[1]["welcome"], True)

    def test_app_mode_security(self):
        p, hs = self.start_app_server()
        self.assertEqual(self.get(hs["url"], "/api/settings", token="wrong")[0], 403)
        self.assertEqual(self.get(hs["url"], "/api/settings", host="evil.com")[0], 403)
        u = urlsplit(hs["url"])
        conn = http.client.HTTPConnection("127.0.0.1", u.port, timeout=10)
        try:
            conn.request("POST", "/api/settings/welcome-done", body="{}", headers={
                "Host": f"127.0.0.1:{u.port}", "Origin": "http://evil.com",
                "X-Devclean-Token": parse_qs(u.query)["t"][0],
                "Content-Type": "application/json"})
            self.assertEqual(conn.getresponse().status, 403)
        finally:
            conn.close()
        p.stdin.close()
        self.assertEqual(p.wait(timeout=10), 0)


    def test_quit_mid_clean_finishes_current_item_only(self):
        """Closing stdin (the app quitting) during a clean: the item being removed finishes,
        the rest are never started, and the server exits 0."""
        bindir = os.path.join(self.tmp, "fakebin")
        os.makedirs(bindir)
        mark = os.path.join(self.tmp, "mark.txt")
        fake = os.path.join(bindir, "rm")
        with open(fake, "w") as f:
            f.write('#!/bin/sh\necho "START $*" >> "$E2E_MARK"\nsleep 2\n/bin/rm "$@"\n'
                    'rc=$?\necho "END $*" >> "$E2E_MARK"\nexit $rc\n')
        os.chmod(fake, 0o755)
        env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
        env.update(PATH=bindir + ":/usr/bin:/bin", HOME=self.home, E2E_MARK=mark)
        p = subprocess.Popen([sys.executable, "-E", "-s", "-X", "utf8", DEVCLEAN, "ui", "--app",
                              "--home", self.home], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, env=env, cwd="/")
        self.procs.append(p)
        hs = json.loads(p.stdout.readline())
        u = urlsplit(hs["url"])
        tok = parse_qs(u.query)["t"][0]

        def req(method, path, body=None):
            headers = {"Host": f"127.0.0.1:{u.port}", "X-Devclean-Token": tok}
            if body is not None:
                headers["Content-Type"] = "application/json"
                body = json.dumps(body)
            conn = http.client.HTTPConnection("127.0.0.1", u.port, timeout=30)
            try:
                conn.request(method, path, body=body, headers=headers)
                r = conn.getresponse()
                return r.status, json.loads(r.read())
            finally:
                conn.close()

        def tree():
            return {os.path.join(d, n) for d, ds, fs in os.walk(self.home) for n in ds + fs}

        status, scan = req("GET", "/api/scan")
        self.assertEqual(status, 200)
        before = tree()
        keys = [k for r in scan["rows"] if r["group"] == "safe" for k in r["keys"]][:3]
        self.assertEqual(len(keys), 3)
        status, _ = req("POST", "/api/clean", {"scan_id": scan["scan_id"], "keys": keys})
        self.assertIn(status, (200, 202))
        deadline = time.time() + 30
        while time.time() < deadline:
            if os.path.exists(mark):
                with open(mark) as f:
                    if "START" in f.read():
                        break
            time.sleep(0.05)
        else:
            self.fail("the first removal never started")
        p.stdin.close()
        self.assertEqual(p.wait(timeout=30), 0, p.stderr.read())
        with open(mark) as f:
            log = f.read().splitlines()
        starts = [l for l in log if l.startswith("START")]
        self.assertEqual(len(starts), 1, log)
        self.assertEqual(len([l for l in log if l.startswith("END")]), 1, log)
        first = starts[0].split(" -- ", 1)[1]
        self.assertFalse(os.path.lexists(first), "item 1 should be removed")
        removed = before - tree()
        self.assertTrue(removed)
        self.assertTrue(all(r == first or r.startswith(first + os.sep) for r in removed),
                        "only item 1 may be removed: %r" % sorted(removed)[:5])
        self.assertEqual(len(keys), 3)   # items 2 and 3 were requested but never started

if __name__ == "__main__":
    unittest.main()
