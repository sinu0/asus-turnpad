"""Run inside Blender: `blender -b --factory-startup --python tests/blender_catalog_check.py`.
Prints one line `MISSING <id> <what>` per catalog entry whose operator, property,
enum value or menu does not exist in this Blender, and `CHECKED <n>`."""

import importlib.util
import os

import bpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location(
    "turnpad_catalog", os.path.join(ROOT, "integrations", "blender", "turnpad", "catalog.py"))
catalog = importlib.util.module_from_spec(spec)
import sys
sys.modules["turnpad_catalog"] = catalog     # dataclasses look the module up here
spec.loader.exec_module(catalog)

# Menus Blender registers from C when its window opens: not in bpy.types here.
C_MENUS = {"SCREEN_MT_user_menu"}

for command in catalog.CATALOG:
    if command.kind in ("op", "call"):
        category, name = command.target.split(".")
        try:
            rna = getattr(getattr(bpy.ops, category), name).get_rna_type()
        except Exception as err:
            print("MISSING", command.id, "operator", command.target, type(err).__name__)
            continue
        properties = {p.identifier: p for p in rna.properties}
        for key, value in command.props.items():
            prop = properties.get(key)
            if prop is None:
                print("MISSING", command.id, "property", key)
            elif prop.type == "ENUM" and not prop.is_enum_flag and \
                    value not in {item.identifier for item in prop.enum_items}:
                print("MISSING", command.id, "enum value", "%s=%s" % (key, value))
    elif command.target not in C_MENUS and not hasattr(bpy.types, command.target):
        print("MISSING", command.id, command.kind, command.target)
print("CHECKED", len(catalog.CATALOG))
