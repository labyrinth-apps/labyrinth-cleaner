"""devclean ui: a local web page to scan, see where space goes, and clean.

    devclean ui              start the server on 127.0.0.1 and open the page
    devclean ui --no-open    print the URL only

Security model (see docs/superpowers/specs/2026-11-11-devclean-ui-design.md):
- binds to 127.0.0.1 on a random port; a random token in the URL is required
  as the X-Devclean-Token header on every /api/* call;
- the Host header must be 127.0.0.1:<port> or localhost:<port> (DNS rebinding);
- a POST with a foreign (or "null") Origin is refused, and POSTs must be JSON;
- the API takes only item keys from the server's own latest scan, never paths,
  and every delete goes through devclean_core.execute_item(), which re-runs the
  safety gate right before deleting;
- the one path the API takes is a project folder to remember in the settings
  (core.add_project_folder validates it); it is only ever scanned, never deleted.
"""

from __future__ import annotations

import argparse
import dataclasses
import hmac
import http.client
import fcntl
import json
import os
import secrets
import shutil
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

import devclean_core as core  # noqa: E402
import devclean_report as report  # noqa: E402

INDEX_HTML = os.path.join(os.path.dirname(os.path.realpath(__file__)), "ui", "index.html")
MAX_BODY = 64 * 1024
CSP = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
       "connect-src 'self'; img-src data:")


def url_file(cfg: core.Config) -> str:
    return cfg.p(".cache/devclean/ui.url")


class UIServer:
    def __init__(self, cfg: core.Config, sim: "core.SimctlProvider | None", *, fixture: bool,
                 xcode_check=core.xcode_running, runner=None, idle_timeout: float = 3600,
                 port: int = 0, measure=core.du_bytes, options: dict = None,
                 fixed_roots: bool = False, docker=None):
        self.cfg, self.sim, self.fixture = cfg, sim, fixture
        self.xcode_check, self.measure = xcode_check, measure
        # Project folders given with --projects: the settings can't change them.
        self.fixed_roots = fixed_roots
        # The gate re-checks "Xcode is running" with the same check the scan used.
        self.env = core.fixture_env(cfg, xcode_check, fixture)
        if docker is not None:
            # Only `devclean ui --home ... --fake-docker FILE` passes one (see fake_docker).
            self.env = dataclasses.replace(self.env, docker=docker)
        # Fixture data can carry real runtime UUIDs: never run xcrun in fixture mode.
        self.runner = runner or (core.fixture_runner if fixture else subprocess.run)
        self.idle_timeout = idle_timeout
        # What this server was started with; a second `devclean ui` reuses it only on a match.
        self.options = dict(options or {}, fixture=fixture)
        self.token = secrets.token_urlsafe(32)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.httpd.daemon_threads = True
        self.httpd.app = self
        self.port = self.httpd.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}/?t={self.token}"
        self.allowed_hosts = {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}
        self.allowed_origins = {f"http://{h}" for h in self.allowed_hosts}
        self.last_request = time.time()
        self._lock = threading.Lock()
        self._scan_inflight = None
        self._latest = None          # (scan_id, {key: Item}, cfg)
        self._settings_gen = 0       # bumped by a settings change; older scans can't be cleaned
        self._jobs = {}
        self._job_running = False
        self._job_thread = None
        self._cancel = threading.Event()
        self._serving = False
        self._closed = False

    # -------------------------------------------------------------- lifecycle

    def touch(self):
        self.last_request = time.time()

    def start(self):
        threading.Thread(target=self.serve_forever, daemon=True).start()
        while not self._serving and not self._closed:
            time.sleep(0.005)

    def serve_forever(self):
        """Serve until shutdown() or until idle for idle_timeout with no clean running."""
        check = min(5.0, max(0.05, self.idle_timeout / 4))
        stop, idle_exit = threading.Event(), threading.Event()

        def watchdog():
            while not stop.wait(check):
                idle = time.time() - self.last_request > self.idle_timeout
                if idle and not self._job_running:
                    idle_exit.set()
                    self.httpd.shutdown()
                    return

        threading.Thread(target=watchdog, daemon=True).start()
        self._serving = True
        try:
            self.httpd.serve_forever(poll_interval=0.1)
        finally:
            stop.set()
            self._serving = False
            if idle_exit.is_set():
                self.shutdown()     # closes the listening socket

    def shutdown(self):
        if self._closed:
            return
        if self._serving:
            self.httpd.shutdown()
        self._closed = True
        self.httpd.server_close()

    # -------------------------------------------------------------- scan

    def get_scan(self) -> dict:
        """Run a scan; concurrent callers wait for the running scan and share its result."""
        with self._lock:
            owner = self._scan_inflight is None
            if owner:
                self._scan_inflight = {"done": threading.Event(), "result": None, "error": None}
            flight = self._scan_inflight
        if owner:
            try:
                flight["result"] = self._do_scan()
            except Exception as e:  # reported to the page, server keeps running
                flight["error"] = e
            finally:
                with self._lock:
                    self._scan_inflight = None
                flight["done"].set()
        else:
            flight["done"].wait()
        if flight["error"] is not None:
            raise flight["error"]
        return flight["result"]

    def _do_scan(self) -> dict:
        t0 = time.time()
        with self._lock:
            cfg, gen = self.cfg, self._settings_gen
        with ThreadPoolExecutor(max_workers=1) as ex:
            extras_f = ex.submit(report.measure_extras, cfg, self.measure)
            res = core.scan(cfg, self.sim, xcode_check=self.xcode_check, env=self.env)
            extras = extras_f.result()
        scan_id = secrets.token_hex(8)
        payload = report.scan_payload(res, cfg, scan_id, round(time.time() - t0, 1), extras)
        with self._lock:
            if gen == self._settings_gen:     # a scan of the old folders is never cleanable
                self._latest = (scan_id, {i.key: i for i in res.items}, cfg)
        return payload

    # -------------------------------------------------------------- settings

    def settings(self) -> dict:
        with self._lock:
            cfg = self.cfg
        return {"project_folders": list(cfg.project_roots),
                "auto_project_folders": report.auto_project_folders(cfg),
                "fixed": self.fixed_roots}

    def change_project_folders(self, body) -> tuple:
        """{"add": path} or {"remove": path}. The path is remembered for scanning only."""
        if (not isinstance(body, dict) or len(body) != 1
                or next(iter(body)) not in ("add", "remove")
                or not isinstance(next(iter(body.values())), str)):
            return 400, {"error": 'expected {"add": path} or {"remove": path}'}
        if self.fixed_roots:
            return 409, {"error": "project folders were set with --projects for this run"}
        (op, path), = body.items()
        with self._lock:
            try:
                if op == "add":
                    core.add_project_folder(self.cfg.home, path)
                else:
                    core.remove_project_folder(self.cfg.home, path)
            except Exception as e:   # any settings error (bad value, bad file, disk full)
                return 400, {"error": str(e) or type(e).__name__}
            self.cfg = core.Config(home=self.cfg.home, android_sdk=self.cfg.android_sdk)
            self._settings_gen += 1
            self._latest = None          # the next clean needs a scan of the new folders
            # A second `devclean ui` resolves the roots from the same settings: keep matching.
            self.options["projects"] = list(self.cfg.project_roots)
        return 200, self.settings()

    # -------------------------------------------------------------- clean

    def start_clean(self, body) -> tuple:
        if not isinstance(body, dict):
            return 400, {"error": "expected a JSON object"}
        scan_id, keys = body.get("scan_id"), body.get("keys")
        with self._lock:
            if self._latest is None or scan_id != self._latest[0]:
                return 409, {"error": "stale scan"}
            if not isinstance(keys, list) or not keys or not all(isinstance(k, str) for k in keys):
                return 400, {"error": "keys must be a non-empty list of strings"}
            items, cfg = self._latest[1], self._latest[2]
            bad = [k for k in keys if k not in items or not items[k].offerable]
            if bad:
                return 400, {"error": "unknown or protected key", "keys": bad}
            if self._job_running:
                return 409, {"error": "a clean is already running"}
            chosen = core.execution_order([items[k] for k in dict.fromkeys(keys)])
            job_id = secrets.token_hex(8)
            job = {"job_id": job_id, "state": "running",
                   "free_before": shutil.disk_usage(self.cfg.home).free,
                   "items": [{"key": it.key, "label": it.label, "size": it.size,
                              "status": "pending", "message": ""} for it in chosen]}
            self._jobs[job_id] = job
            self._latest = None          # the same scan can never be cleaned twice
            self._job_running = True
            self._cancel.clear()
            self._job_thread = threading.Thread(target=self._run_job, args=(job, chosen, cfg),
                                                daemon=True)
            self._job_thread.start()
        return 200, {"job_id": job_id}

    def _run_job(self, job, chosen, cfg):
        try:
            for entry, it in zip(job["items"], chosen):
                with self._lock:
                    if self._cancel.is_set():
                        entry["status"] = "cancelled"
                        entry["message"] = "devclean was stopped before this one started"
                        continue
                    entry["status"] = "running"
                try:
                    ok, msg = core.execute_item(it, cfg, self.sim, runner=self.runner,
                                                env=self.env)
                except Exception as e:
                    ok, msg = False, str(e)
                with self._lock:
                    entry["message"] = "" if ok else msg
                    if ok:
                        entry["status"] = "trashed" if it.kind == "trash" else "done"
                    else:
                        entry["status"] = "refused" if msg.startswith("REFUSED: ") else "failed"
        finally:
            with self._lock:
                job["state"] = "done"
                self.last_request = time.time()   # before clearing the flag: no idle-exit gap
                self._job_running = False

    def finish_jobs(self):
        """Let the running delete finish, skip the rest of the job, and wait for it."""
        self._cancel.set()
        t = self._job_thread
        if t is not None:
            t.join()

    def job_status(self, job_id: str):
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            out = json.loads(json.dumps(job))
        out["free_now"] = shutil.disk_usage(self.cfg.home).free
        return out


class Handler(BaseHTTPRequestHandler):
    server_version = "devclean"
    sys_version = ""

    def log_message(self, *args):
        pass

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def _send(self, status: int, obj=None, body: bytes = None, ctype="application/json"):
        if body is None:
            body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        if ctype.startswith("text/html"):
            self.send_header("Content-Security-Policy", CSP)
        self.end_headers()
        self.wfile.write(body)

    def _dispatch(self, method: str):
        app: UIServer = self.server.app
        app.touch()
        if self.headers.get("Host", "") not in app.allowed_hosts:
            return self._send(403, {"error": "forbidden host"})
        path = urlsplit(self.path).path
        if method == "GET" and path in ("/", "/index.html"):
            with open(INDEX_HTML, "rb") as f:
                return self._send(200, body=f.read(), ctype="text/html; charset=utf-8")
        if not path.startswith("/api/"):
            return self._send(404, {"error": "not found"})
        token = self.headers.get("X-Devclean-Token", "")
        if not hmac.compare_digest(token.encode("utf-8", "replace"), app.token.encode()):
            return self._send(403, {"error": "bad token"})

        if method == "GET":
            if path == "/api/ping":
                return self._send(200, {"ok": True, "options": app.options})
            if path == "/api/scan":
                try:
                    return self._send(200, app.get_scan())
                except Exception as e:
                    return self._send(500, {"error": f"scan failed: {e}"})
            if path == "/api/settings":
                return self._send(200, app.settings())
            if path.startswith("/api/job/"):
                job = app.job_status(path[len("/api/job/"):])
                return self._send(200, job) if job else self._send(404, {"error": "no such job"})
            return self._send(404, {"error": "not found"})

        origin = self.headers.get("Origin")
        if origin is not None and origin not in app.allowed_origins:
            return self._send(403, {"error": "forbidden origin"})
        ctype = self.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if ctype != "application/json":
            return self._send(415, {"error": "expected application/json"})
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self._send(400, {"error": "bad length"})
        if length > MAX_BODY:
            return self._send(413, {"error": "body too large"})
        try:
            body = json.loads(self.rfile.read(length) or b"null")
        except ValueError:
            return self._send(400, {"error": "bad JSON"})
        if path == "/api/clean":
            status, out = app.start_clean(body)
            return self._send(status, out)
        if path == "/api/settings/project-folders":
            status, out = app.change_project_folders(body)
            return self._send(status, out)
        return self._send(404, {"error": "not found"})


# ------------------------------------------------------------------ CLI

DOCKER_DF = ["docker", "system", "df", "--format", "json"]


def fake_docker(path: str):
    """A docker runner for demos on a fixture home: answers `docker system df --format json`
    with the contents of `path` and refuses everything else, so nothing is ever pruned."""
    with open(path, encoding="utf-8") as f:
        out = f.read()

    def run(argv, **kw):
        if list(argv) == DOCKER_DF:
            return subprocess.CompletedProcess(argv, 0, out, "")
        return subprocess.CompletedProcess(argv, 1, "", "fake docker: only `docker system df` is answered")
    return run


def _live_server(path: str):
    """(url, options) if a devclean server answers at the URL in `path`, else None."""
    try:
        with open(path) as f:
            url = f.read().strip()
        u = urlsplit(url)
        token = parse_qs(u.query).get("t", [""])[0]
        if u.hostname != "127.0.0.1" or not u.port or not token:
            return None
        conn = http.client.HTTPConnection("127.0.0.1", u.port, timeout=1)
        try:
            conn.request("GET", "/api/ping", headers={"X-Devclean-Token": token,
                                                      "Host": f"127.0.0.1:{u.port}"})
            resp = conn.getresponse()
            body = resp.read()
            if resp.status != 200:
                return None
        finally:
            conn.close()
        return url, json.loads(body).get("options", {})
    except (OSError, ValueError, http.client.HTTPException):
        return None


def _startup_lock(url_path: str) -> int:
    """Serialise `devclean ui` starts, so two quick starts give one server, not two."""
    os.makedirs(os.path.dirname(url_path), mode=0o700, exist_ok=True)
    fd = os.open(url_path + ".lock", os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def _write_private(path: str, text: str):
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, text.encode())
    finally:
        os.close(fd)


def _raise_interrupt(signum, frame):
    raise KeyboardInterrupt


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="devclean ui",
                                 description="Open the devclean web UI (local only).")
    ap.add_argument("--home", help="pretend this directory is $HOME (testing/demo)")
    ap.add_argument("--projects", action="append", metavar="DIR",
                    help="projects folder to scan (repeatable; default: common folders under home plus saved ones)")
    ap.add_argument("--simctl-fixture", metavar="DIR",
                    help="read simctl_list.json/runtime_list.json from DIR instead of xcrun")
    ap.add_argument("--no-simulators", action="store_true", help="skip simulator runtimes")
    ap.add_argument("--no-open", action="store_true", help="print the URL, don't open a browser")
    ap.add_argument("--idle-timeout", type=float, default=3600, help=argparse.SUPPRESS)
    ap.add_argument("--fake-docker", metavar="FILE", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    if args.fake_docker and not args.home:
        ap.error("--fake-docker needs --home (it is for demos on a fixture home)")
    docker = None
    if args.fake_docker:
        try:
            docker = fake_docker(args.fake_docker)
        except OSError as e:
            ap.error(f"--fake-docker: {e}")

    cfg = core.Config(home=args.home or os.path.expanduser("~"), project_roots=tuple(
        os.path.abspath(os.path.expanduser(d)) for d in args.projects) if args.projects else None)
    # None for the real home: xcrun runs only when the developer tools are installed.
    sim = core.pick_simctl(args.simctl_fixture, args.no_simulators, bool(args.home))
    fixture = bool(args.home or args.simctl_fixture)
    options = {
        "home": cfg.home,
        "projects": list(cfg.project_roots),
        "simctl_fixture": os.path.realpath(args.simctl_fixture) if args.simctl_fixture else None,
        "simulators": not (args.no_simulators or args.home or args.simctl_fixture),
        "fixture": fixture,
        "fake_docker": os.path.realpath(args.fake_docker) if args.fake_docker else None,
    }

    uf = url_file(cfg)
    lock = _startup_lock(uf)
    try:
        existing = _live_server(uf)
        if existing:
            url, running = existing
            if running != options:
                # Never hand a fixture-mode request a real server (or the other way round).
                print(f"devclean ui: a devclean UI with different options is running at {url}\n"
                      "Stop it first (Ctrl-C in its terminal), or use that page.", file=sys.stderr)
                return 1
            print(f"devclean UI already running: {url}", flush=True)
            if not args.no_open:
                subprocess.run(["open", url], check=False)
            return 0
        srv = UIServer(cfg, sim, fixture=fixture,
                       xcode_check=(lambda: False) if args.home else core.xcode_running,
                       idle_timeout=args.idle_timeout, options=options,
                       fixed_roots=bool(args.projects), docker=docker)
        _write_private(uf, srv.url)
    finally:
        os.close(lock)   # releases the flock
    print(f"devclean UI: {srv.url}  (Ctrl-C to stop)", flush=True)
    if fixture:
        print("fixture mode: xcrun deletes are disabled", flush=True)
    if not args.no_open:
        subprocess.run(["open", srv.url], check=False)
    if threading.current_thread() is threading.main_thread():
        # `kill` and launchd stop with SIGTERM: clean up the URL file like Ctrl-C does.
        signal.signal(signal.SIGTERM, _raise_interrupt)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        if srv._job_running:
            print("\nFinishing the current deletion, then stopping "
                  "(Ctrl-C again to quit now)...", flush=True)
            try:
                srv.finish_jobs()
            except KeyboardInterrupt:
                pass
    finally:
        srv.shutdown()
        try:
            with open(uf) as f:
                mine = f.read().strip() == srv.url
            if mine:
                os.remove(uf)
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
