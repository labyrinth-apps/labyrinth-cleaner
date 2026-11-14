# dmgbuild layout for the release DMG: the app on the left, Applications on the right,
# over background.tiff (rendered by scripts/make_art.swift; 1x and 2x in one file).
# Usage: dmgbuild -s app/dmg/dmg_settings.py -D app=<path.app> -D bg=<background.tiff> "Labyrinth Cleaner" out.dmg
import os

app = defines["app"]  # noqa: F821 (injected by dmgbuild)
app_name = os.path.basename(app)

format = "ULFO"  # lzfse: smaller than UDZO, opens on macOS 10.11+ (we need 13+)
filesystem = "HFS+"
files = [app]
symlinks = {"Applications": "/Applications"}
hide_extensions = [app_name]

background = defines["bg"]  # noqa: F821
window_rect = ((200, 120), (660, 400))
default_view = "icon-view"
show_status_bar = False
show_tab_view = False
show_toolbar = False
show_pathbar = False
show_sidebar = False
icon_size = 128
text_size = 13
icon_locations = {app_name: (165, 200), "Applications": (495, 200)}
