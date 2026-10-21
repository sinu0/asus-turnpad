"""Imports what the installed driver and settings app need, with the
system's Python, as the package tests (packaging/*/test-package.sh) run it:

    python3 -I packaging/check-imports.py /usr/share/asus-turnpad

The driver's own imports are read from dialpad.py, so a new library there
is checked without changing this file; imports inside try blocks are
optional and skipped.
"""

import ast
import sys

app = sys.argv[1]
sys.path.insert(0, app)

with open(f"{app}/dialpad.py", encoding="utf-8") as source:
    tree = ast.parse(source.read())
for node in tree.body:
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        exec(ast.unparse(node), {})
print("driver imports ok")

# Imported by the driver inside try blocks, but needed: the D-Bus service and
# the JSON configuration.
import dialpad_dbus  # noqa: E402
import config.legacy_adapter, config.migrate, config.runtime, config.schema, config.skins  # noqa: E402,E401
import desktop.active_window, desktop.color_scheme, overlay.painter  # noqa: E402,E401
import layout_detect  # noqa: E402
import integrations.bridges, integrations.solidcraft  # noqa: E402,E401

assert config.skins.load_skins(), "built-in skins not found next to the settings app"
assert layout_detect.detect_layout(f"{app}/layouts") in {"proartp16", "asusvivobook16x", "zenbookpro14"}
print("driver modules ok")

if "--driver-only" not in sys.argv:
    import config_app.main  # noqa: E402,F401
    print("settings app imports ok")
