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

    def start(self, sim=None, **kw):
        kw.setdefault("xcode_check", lambda: False)
        self.srv = ui.UIServer(cfg_for(self.home), sim or fixture_sim(), fixture=True, **kw)
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
        self.assertIn("safe:flutter_app", {r["id"] for r in p["rows"]})

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

    def test_cli_dispatches_ui(self):
        r = subprocess.run([sys.executable, os.path.join(ROOT, "devclean"), "ui", "--help"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("--no-open", r.stdout)


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
                       "devclean doesn't analyse system, app or personal files."):
            self.assertIn(needle, html)

    def test_page_handles_lost_server_and_cancelled_items(self):
        html = self.html()
        self.assertIn("Lost contact with devclean", html)
        self.assertIn("cancelled:", html)                 # status icon for cancelled items
        self.assertNotIn("terminal still running", html)  # also started from the menu bar

    def test_no_html_injection_sinks(self):
        """Scan data (folder names!) must only reach the DOM as text."""
        html = self.html()
        for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(",
                     "new Function"):
            self.assertNotIn(sink, html)

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


if __name__ == "__main__":
    unittest.main()
