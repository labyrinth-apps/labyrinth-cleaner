"""devclean menubar: remove the old DevcleanBar menu-bar item.

    devclean menubar uninstall   stop it and remove the app and its login item

The menu-bar item is now part of the Labyrinth Cleaner app, so `install` is retired.
"""

from __future__ import annotations

import argparse
import os
import plistlib
import shutil
import subprocess
import sys

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
                                 description="Remove the old DevcleanBar menu-bar item.")
    ap.add_argument("action", choices=["install", "uninstall"],
                    help="install: retired (the menu bar is part of the app); uninstall: stop and remove DevcleanBar")
    ap.add_argument("--home", default=os.path.expanduser("~"), help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    try:
        if args.action == "install":
            print("The menu bar item is now part of the Labyrinth Cleaner app. "
                  "To remove the old DevcleanBar, run: devclean menubar uninstall")
            return 1
        else:
            uninstall(args.home)
            print("Removed DevcleanBar and its login item.")
    except (RuntimeError, OSError) as e:
        print(f"devclean menubar: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
