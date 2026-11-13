"""devclean menubar: install or remove the free-space menu-bar item.

    devclean menubar install     build DevcleanBar.app into ~/Applications, start it at login
    devclean menubar uninstall   stop it and remove the app and its login item

The item only shows free space and runs `devclean ui`. It never deletes anything.
"""

from __future__ import annotations

import argparse
import os
import plistlib
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.realpath(__file__))
SRC = os.path.join(HERE, "menubar")
DEVCLEAN = os.path.join(HERE, "devclean")
APP_ID = "local.devclean.bar"
APP_NAME = "DevcleanBar"


def app_path(home: str) -> str:
    return os.path.join(home, "Applications", APP_NAME + ".app")


def agent_path(home: str) -> str:
    return os.path.join(home, "Library", "LaunchAgents", APP_ID + ".plist")


def _domain() -> str:
    return f"gui/{os.getuid()}"


def _is_ours(app: str) -> bool:
    try:
        with open(os.path.join(app, "Contents", "Info.plist"), "rb") as f:
            return plistlib.load(f).get("CFBundleIdentifier") == APP_ID
    except (OSError, plistlib.InvalidFileException, ValueError):
        return False


def _check_app_dir(app: str) -> None:
    """Refuse to touch an app folder that isn't ours."""
    if os.path.islink(app):
        raise RuntimeError(f"{app} is a symlink; not touching it")
    if os.path.exists(app) and not _is_ours(app):
        raise RuntimeError(f"{app} exists but isn't devclean's ({APP_ID}); not touching it")


def _write_plist(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        plistlib.dump(data, f)


def install(home: str, devclean_path: str, run=subprocess.run, compile: bool = True) -> str:
    app = app_path(home)
    _check_app_dir(app)
    macos = os.path.join(app, "Contents", "MacOS")
    binary = os.path.join(macos, APP_NAME)
    os.makedirs(macos, exist_ok=True)
    # Info.plist first: even if the compile fails, the folder is recognisably
    # ours, so `install` can retry and `uninstall` can remove it.
    _write_plist(os.path.join(app, "Contents", "Info.plist"), {
        "CFBundleIdentifier": APP_ID,
        "CFBundleName": APP_NAME,
        "CFBundleExecutable": APP_NAME,
        "CFBundlePackageType": "APPL",
        "CFBundleVersion": "1",
        "LSUIElement": True,
    })
    if compile:
        tmp = binary + ".new"
        try:
            r = run(["swiftc", "-O", os.path.join(SRC, "Logic.swift"),
                     os.path.join(SRC, "main.swift"), "-o", tmp], capture_output=True, text=True)
        except OSError as e:
            raise RuntimeError(f"can't run swiftc ({e}); install the Command Line Tools "
                               "with: xcode-select --install") from e
        if r.returncode != 0:
            raise RuntimeError("swiftc failed:\n" + (r.stderr or ""))
        os.replace(tmp, binary)
    else:
        with open(binary, "w") as f:
            f.write("#!/bin/sh\n")
        os.chmod(binary, 0o755)
    agent = agent_path(home)
    # launchd starts agents with a bare PATH; put this Python first so the
    # `#!/usr/bin/env python3` in devclean finds the same interpreter.
    path = os.pathsep.join([os.path.dirname(sys.executable), "/opt/homebrew/bin", "/usr/local/bin",
                            "/usr/bin", "/bin", "/usr/sbin", "/sbin"])
    _write_plist(agent, {
        "Label": APP_ID,
        "ProgramArguments": [binary, "--devclean", os.path.realpath(devclean_path)],
        "RunAtLoad": True,
        "ProcessType": "Interactive",
        "EnvironmentVariables": {"PATH": path},
    })
    run(["launchctl", "bootout", f"{_domain()}/{APP_ID}"], capture_output=True, text=True)
    for attempt in range(3):
        r = run(["launchctl", "bootstrap", _domain(), agent], capture_output=True, text=True)
        if r.returncode == 0:
            break
        time.sleep(0.5)   # bootout can take a moment to finish
    else:
        raise RuntimeError("launchctl bootstrap failed: " + (r.stderr or "").strip())
    return app


def uninstall(home: str, run=subprocess.run) -> None:
    app = app_path(home)
    _check_app_dir(app)
    run(["launchctl", "bootout", f"{_domain()}/{APP_ID}"], capture_output=True, text=True)
    agent = agent_path(home)
    if os.path.isfile(agent):
        os.remove(agent)
    if os.path.isdir(app):
        shutil.rmtree(app)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="devclean menubar",
                                 description="Show free disk space in the menu bar.")
    ap.add_argument("action", choices=["install", "uninstall"],
                    help="install: build and start at login; uninstall: stop and remove")
    ap.add_argument("--home", default=os.path.expanduser("~"), help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    try:
        if args.action == "install":
            app = install(args.home, DEVCLEAN)
            print(f"Installed {app}.\nFree space now shows in the menu bar and it starts at login. "
                  "Remove it with: devclean menubar uninstall")
        else:
            uninstall(args.home)
            print("Removed DevcleanBar and its login item.")
    except (RuntimeError, OSError) as e:
        print(f"devclean menubar: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
