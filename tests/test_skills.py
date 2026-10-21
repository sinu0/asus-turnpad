"""The repository's agent skills (.claude/skills/): each has a valid header,
its scripts compile, and the bridge scaffold still finds every list it wires
a bridge into, so a change to install.sh or the package lists that breaks it
fails here rather than for the next person who adds a bridge."""

import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / ".claude" / "skills"
SCAFFOLD = SKILLS / "app-plugin" / "scaffold_bridge.py"


def header(path):
    text = path.read_text()
    match = re.match(r"---\n(.*?)\n---\n", text, re.S)
    return dict(re.findall(r"^(\w+): (.+)$", match.group(1), re.M)) if match else {}


class SkillsTest(unittest.TestCase):
    def test_headers(self):
        skills = sorted(path for path in SKILLS.iterdir() if path.is_dir())
        self.assertTrue(skills)
        for skill in skills:
            with self.subTest(skill=skill.name):
                values = header(skill / "SKILL.md")
                self.assertEqual(values.get("name"), skill.name)
                self.assertGreater(len(values.get("description", "")), 80, "say when to use it")

    def test_scripts_compile(self):
        for script in SKILLS.glob("*/*.py"):
            with self.subTest(script=script.name):
                compile(script.read_text(), str(script), "exec")

    def test_files_the_skills_name_exist(self):
        for skill in SKILLS.glob("*/SKILL.md"):
            text = skill.read_text()
            for name in sorted(set(re.findall(r"`((?:tests|integrations|config|packaging|tools)/[\w./-]+\.(?:py|sh))`", text))):
                if "NAME" in name or "<" in name:
                    continue
                with self.subTest(skill=skill.parent.name, file=name):
                    self.assertTrue((ROOT / name).exists())


class ScaffoldTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for name in ("app_link.py", "install.sh", "packaging/install-files.sh", "packaging/check-imports.py",
                     "tests/test_install.py"):
            (self.root / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(ROOT / name, self.root / name)
        for folder in ("config", "integrations"):
            shutil.copytree(ROOT / folder, self.root / folder, ignore=shutil.ignore_patterns("__pycache__"))

    def tearDown(self):
        self.tmp.cleanup()

    def scaffold(self, *args):
        return subprocess.run([sys.executable, str(SCAFFOLD), *args, "--root", str(self.root)],
                              capture_output=True, text=True)

    def test_scaffold_and_wire(self):
        done = self.scaffold("demo_app", "--title", "Demo", "--desktop-id", "org.example.DemoApp.desktop",
                             "--link-app", "demoapp", "--wire")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        for name in ("integrations/demo_app/__init__.py", "integrations/demo_app/catalog.py",
                     "integrations/demo_app/control.py", "tests/test_demo_app_bridge.py"):
            self.assertTrue((self.root / name).exists(), name)
        self.assertIn('"integrations.demo_app"', (self.root / "integrations" / "bridges.py").read_text())
        installer = (self.root / "install.sh").read_text()
        self.assertIn('"$INSTALL_DIR_PATH/integrations/demo_app"', installer)
        self.assertIn("integrations/demo_app/*.py", installer)
        self.assertRegex((self.root / "packaging" / "install-files.sh").read_text(),
                         r"for dir in [^;]*integrations/demo_app; do")
        self.assertIn('"integrations/demo_app")', (self.root / "tests" / "test_install.py").read_text())
        self.assertIn("import integrations.demo_app", (self.root / "packaging" / "check-imports.py").read_text())
        subprocess.run(["bash", "-n", str(self.root / "install.sh")], check=True)
        # The generated tests pass as they are.
        tests = subprocess.run([sys.executable, "-m", "unittest", "tests.test_demo_app_bridge"],
                               cwd=self.root, capture_output=True, text=True)
        self.assertEqual(tests.returncode, 0, tests.stderr)

    def test_refuses_a_name_that_does_not_match_the_desktop_id(self):
        done = self.scaffold("obs", "--title", "OBS Studio", "--desktop-id", "com.obsproject.Studio.desktop")
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("does not match", done.stdout + done.stderr)
        self.assertFalse((self.root / "integrations" / "obs").exists())


if __name__ == "__main__":
    unittest.main()
