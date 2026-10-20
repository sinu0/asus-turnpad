"""The settings app's command line (GTK-free, unit tested).

``--add-profile APP_ID`` opens Apps & Profiles and starts adding a profile for
that application (the driver's "Create profile" notification runs it). The id
is whatever the driver saw: a desktop id ("org.freecad.FreeCAD.desktop"), the
id without ".desktop" or a program name ("blender")."""

import argparse


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)

    def exit(self, status=0, message=None):
        raise ValueError(message or "")


def parse(arguments):
    """(add_profile, error): the application id asked for ("" = none) and why the
    arguments could not be read (None = fine). ``arguments`` has no program name."""
    parser = _Parser(prog="turnpad-settings", add_help=False)
    parser.add_argument("--add-profile", metavar="APP_ID", default="")
    try:
        namespace, _rest = parser.parse_known_args(list(arguments))
    except ValueError as error:
        return "", str(error)
    return namespace.add_profile.strip()[:300], None


def resolve_app(app_id, apps, pack_name=None):
    """(desktop id, name, known) for ``app_id`` among ``apps``, a list of
    (desktop id, display name). The id itself wins, then the id with ".desktop",
    then the first application whose id has it as a whole word (any case).
    Not found: the id as given, ``pack_name`` or the id as the name, known False."""
    wanted = app_id.strip()
    lowered = wanted.lower()
    by_id = {desktop_id.lower(): (desktop_id, name) for desktop_id, name in apps}
    for candidate in (lowered, lowered + ".desktop"):
        if candidate in by_id:
            desktop_id, name = by_id[candidate]
            return desktop_id, name, True
    stem = lowered.removesuffix(".desktop")
    if stem:
        for desktop_id, name in apps:
            words = desktop_id.lower().removesuffix(".desktop").replace("-", ".").replace("_", ".").split(".")
            if stem in words:
                return desktop_id, name, True
    return wanted, pack_name or wanted, False
