"""The settings app's Plugins page without GTK (config_app/plugins.py): the
SolidCraft control port in environment.d, Turnpad's plugin in SolidCraft's data
folders and the status lines, in temporary home folders."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config_app import plugins as pl  # noqa: E402
from integrations.solidcraft import plugin  # noqa: E402


class PortTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.environ = {"HOME": self.tmp.name}

    def tearDown(self):
        self.tmp.cleanup()

    def conf(self, name, text):
        directory = pl.environment_dir(self.environ)
        os.makedirs(directory, exist_ok=True)
        with open(os.path.join(directory, name), "w") as handle:
            handle.write(text)

    def test_nothing_set(self):
        setting = pl.port_setting(self.environ)
        self.assertEqual(setting, pl.PortSetting(None, "", False, None))
        self.assertIn("Off", pl.describe_port(setting))

    def test_write_and_remove_our_file(self):
        path = pl.write_port(environ=self.environ)
        self.assertEqual(path, os.path.join(self.tmp.name, ".config", "environment.d", "solidcraft.conf"))
        with open(path) as handle:
            self.assertIn("SOLIDCRAFT_CONTROL_PORT=7878\n", handle.read())
        setting = pl.port_setting(self.environ)
        self.assertEqual((setting.port, setting.ours, setting.current), (7878, True, None))
        self.assertIn("log out and in", pl.describe_port(setting))
        now = pl.port_setting(dict(self.environ, SOLIDCRAFT_CONTROL_PORT="7878"))
        self.assertNotIn("log out", pl.describe_port(now))
        self.assertTrue(pl.remove_port(self.environ))
        self.assertFalse(pl.remove_port(self.environ))
        self.assertIsNone(pl.port_setting(self.environ).port)
        with self.assertRaises(ValueError):
            pl.write_port(70000, self.environ)

    def test_another_file_and_the_last_one_wins(self):
        self.conf("10-cad.conf", "# mine\nSOLIDCRAFT_CONTROL_PORT=\"9000\"\nOTHER=1\n")
        setting = pl.port_setting(self.environ)
        self.assertEqual((setting.port, setting.ours), (9000, False))
        self.assertIn("10-cad.conf", pl.describe_port(setting))
        self.conf("90-later.conf", "SOLIDCRAFT_CONTROL_PORT=9001\n")
        self.conf("README", "SOLIDCRAFT_CONTROL_PORT=1\n")          # not a .conf file
        self.conf("95-bad.conf", "SOLIDCRAFT_CONTROL_PORT=99999\n")  # not a port
        self.assertEqual(pl.port_setting(self.environ).port, 9001)

    def test_xdg_config_home(self):
        environ = dict(self.environ, XDG_CONFIG_HOME=os.path.join(self.tmp.name, "cfg"))
        self.assertEqual(pl.env_file(environ), os.path.join(self.tmp.name, "cfg", "environment.d", "solidcraft.conf"))


class FoldersTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.environ = {"HOME": self.tmp.name}

    def tearDown(self):
        self.tmp.cleanup()

    def test_native_always_others_when_present(self):
        folders = pl.plugin_folders(self.environ)
        self.assertEqual([folder.kind for folder in folders], ["Native build"])
        self.assertEqual(folders[0].state, plugin.MISSING)
        self.assertFalse(folders[0].exists)
        flatpak = plugin.data_roots(self.environ)[1]
        os.makedirs(flatpak)
        plugin.install(flatpak)
        folders = pl.plugin_folders(self.environ)
        self.assertEqual([(folder.kind, folder.state) for folder in folders],
                         [("Native build", plugin.MISSING), ("Flatpak", plugin.INSTALLED)])
        self.assertEqual([pl.plugin_action(folder.state) for folder in folders], ["Add", "Remove"])
        self.assertIn(flatpak, pl.describe_folder(folders[1]))
        self.assertIsNone(pl.plugin_action(plugin.FOREIGN))
        self.assertEqual(pl.plugin_action(plugin.OUTDATED), "Update")

    def test_solidcraft_present(self):
        none = lambda _name: None  # noqa: E731
        self.assertFalse(pl.solidcraft_present(self.environ, which=none, exists=lambda _path: False))
        self.assertTrue(pl.solidcraft_present(self.environ, which=lambda _name: "/usr/bin/solidcraft"))
        os.makedirs(plugin.data_roots(self.environ)[0])
        self.assertTrue(pl.solidcraft_present(self.environ, which=none))


class LinkTest(unittest.TestCase):
    installed = [pl.PluginFolder("Native build", "/r", plugin.INSTALLED, True)]
    missing = [pl.PluginFolder("Native build", "/r", plugin.MISSING, True)]
    port = pl.PortSetting(7878, "/x/solidcraft.conf", True, 7878)
    no_port = pl.PortSetting(None, "", False, None)

    def status(self, **bridge):
        return {"enabled": True, "bridges": json.dumps({"solidcraft": bridge})}

    def test_bridge_status(self):
        self.assertEqual(pl.bridge_status(self.status(state="linked")), {"state": "linked"})
        self.assertIsNone(pl.bridge_status(None))
        self.assertIsNone(pl.bridge_status({"enabled": True}), "an older driver without bridges")
        self.assertIsNone(pl.bridge_status({"bridges": "not json"}))

    def test_lines_say_what_to_do_next(self):
        linked = pl.bridge_status(self.status(state="linked", version="0.18.1", detail="port 7878, 240 commands"))
        self.assertEqual(pl.describe_link(linked, self.port, self.installed),
                         "Linked to SolidCraft 0.18.1 · port 7878, 240 commands")
        waiting = pl.bridge_status(self.status(state="waiting", detail=""))
        self.assertIn("add Turnpad", pl.describe_link(waiting, self.port, self.missing))
        self.assertIn("control port", pl.describe_link(waiting, self.no_port, self.installed))
        self.assertIn("start with its control port", pl.describe_link(waiting, self.port, self.installed))
        off = pl.bridge_status(self.status(state="waiting", detail="port 7878: turned off in SolidCraft's Preferences"))
        self.assertIn("turned off", pl.describe_link(off, self.port, self.installed))
        self.assertIn("not running", pl.describe_link(None, self.port, self.installed))


if __name__ == "__main__":
    unittest.main()
