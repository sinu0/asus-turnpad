"""Installing the Turnpad add-on into Blender (the settings app's SpaceMouse page).

The add-on lives in ``integrations/blender/turnpad`` next to the program. With
Blender on the PATH its own extension commands build and install it:

    blender --command extension build --source-dir SRC --output-dir OUT
    blender --command extension install-file -r user_default -e OUT/turnpad-X.zip

Without Blender (or when it refuses) a zip of the same layout is built with
Python's zipfile and the steps to install it by hand are given. Free of GTK,
unit tested; the commands run in a thread of the caller.
"""

import os
import shutil
import subprocess
import tomllib
import zipfile
from dataclasses import dataclass
from pathlib import Path

from config.identity import SLUG

ADDON_ID = "turnpad"
# The most a Blender command may take (a first start of Blender builds its cache).
COMMAND_TIMEOUT = 180

MANUAL_STEPS = (
    "Install the zip by hand: in Blender open Edit › Preferences › Get Extensions, "
    "open the menu at the top right (the arrow) and choose Install from Disk…, pick the zip, "
    "then switch “Turnpad” on in the Add-ons list."
)
RESTART_HINT = "Restart Blender if it is running; the add-on connects to Turnpad when Blender starts."


@dataclass
class InstallResult:
    ok: bool  # installed through Blender
    method: str  # "blender" or "zip"
    zip_path: Path | None
    log: str
    manual: str = ""


def addon_source():
    """The add-on's folder next to this program, None when it is not there."""
    source = Path(__file__).resolve().parents[1] / "integrations" / "blender" / ADDON_ID
    return source if (source / "blender_manifest.toml").is_file() else None


def find_blender():
    return shutil.which("blender")


def blender_wanted(profiles, which=shutil.which):
    """Whether the Blender group is worth showing: a Blender profile exists or Blender is installed."""
    return any(is_blender_profile(profile) for profile in profiles) or which("blender") is not None


def is_blender_profile(profile):
    return "blender" in str(getattr(profile, "app_id", None) or "").lower()


def cache_dir():
    """Where the zip is built: ~/.cache/asus-turnpad/blender."""
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return Path(base) / SLUG / "blender"


def manifest_version(source):
    try:
        with open(Path(source) / "blender_manifest.toml", "rb") as manifest:
            return str(tomllib.load(manifest).get("version") or "0")
    except (OSError, tomllib.TOMLDecodeError):
        return "0"


def build_zip(source, out_dir):
    """A zip of the add-on with its files at the root (what Blender expects),
    without caches; returns its path."""
    source, out_dir = Path(source), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{ADDON_ID}-{manifest_version(source)}.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(source.rglob("*")):
            relative = path.relative_to(source)
            if path.is_dir() or "__pycache__" in relative.parts or path.suffix == ".pyc":
                continue
            archive.write(path, relative.as_posix())
    return target


def _run(command, runner, env, timeout):
    """(returncode, output) of a command; a program that cannot run is code 127."""
    try:
        done = runner(command, capture_output=True, text=True, env=env, timeout=timeout, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as error:
        return 127, str(error)
    return done.returncode, ((done.stdout or "") + (done.stderr or "")).strip()


def _newest_zip(directory):
    zips = sorted(Path(directory).glob("*.zip"), key=lambda path: path.stat().st_mtime)
    return zips[-1] if zips else None


def install(source=None, blender=None, out_dir=None, env=None, runner=subprocess.run, timeout=COMMAND_TIMEOUT):
    """Build the add-on's zip and install it with Blender when it can. ``env``
    is the environment of Blender's commands (a test points BLENDER_USER_RESOURCES
    into a temporary folder)."""
    source = Path(source) if source is not None else addon_source()
    if source is None or not (source / "blender_manifest.toml").is_file():
        return InstallResult(False, "zip", None, "The add-on files are not part of this installation.")
    out_dir = Path(out_dir) if out_dir is not None else cache_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    log = []
    if blender:
        build = [blender, "--command", "extension", "build", "--source-dir", str(source), "--output-dir", str(out_dir)]
        log.append("$ " + " ".join(build))
        code, output = _run(build, runner, env, timeout)
        log.append(output)
        built = _newest_zip(out_dir) if code == 0 else None
        if built is not None:
            install_command = [blender, "--command", "extension", "install-file", "-r", "user_default", "-e", str(built)]
            log.append("$ " + " ".join(install_command))
            code, output = _run(install_command, runner, env, timeout)
            log.append(output)
            if code == 0:
                return InstallResult(True, "blender", built, "\n".join(part for part in log if part))
        log.append(f"Blender could not do it (exit code {code}); building the zip here instead.")
    else:
        log.append("Blender was not found on the PATH; building the zip here.")
    try:
        built = build_zip(source, out_dir)
    except OSError as error:
        return InstallResult(False, "zip", None, "\n".join([*log, f"The zip could not be built: {error}"]), MANUAL_STEPS)
    log.append(f"Built {built}")
    return InstallResult(False, "zip", built, "\n".join(part for part in log if part), MANUAL_STEPS)

