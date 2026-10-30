"""Check embedded import paths without relying on system Python's Tkinter."""
from importlib.machinery import PathFinder
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
CANDIDATES = [ROOT / 'runtime', ROOT / 'build/Pet-Dock-Turmo/runtime', ROOT / 'portable/runtime']
RUNTIME = next((p for p in CANDIDATES if (p / 'python312._pth').is_file()), CANDIDATES[0])


def import_paths():
    return [str((RUNTIME / line.strip()).resolve())
            for line in (RUNTIME / 'python312._pth').read_text().splitlines()
            if line.strip() and not line.startswith(('#', 'import '))]


@unittest.skipUnless((RUNTIME / 'python312._pth').is_file(), 'Build the optional portable runtime for these checks')
class PortablePathTests(unittest.TestCase):
    def test_tkinter_resolves_from_embedded_paths_not_system(self):
        spec = PathFinder.find_spec('tkinter', import_paths())
        self.assertIsNotNone(spec, 'Tkinter exists but its parent Lib folder is missing from python312._pth')
        self.assertEqual(Path(spec.origin).resolve(), (RUNTIME / 'Lib/tkinter/__init__.py').resolve())

    def test_original_missing_lib_path_reproduces_failure(self):
        original = [str((RUNTIME / p).resolve()) for p in ['python312.zip', '.', 'Lib/site-packages', '../app']]
        self.assertIsNone(PathFinder.find_spec('tkinter', original))

    def test_site_packages_and_app_also_resolve(self):
        self.assertIsNotNone(PathFinder.find_spec('serial', import_paths()))
        if not (RUNTIME.parent / 'app').is_dir():
            self.skipTest('Package the intermediate runtime beside app to check release app imports')
        self.assertIsNotNone(PathFinder.find_spec('capture', import_paths()))
        self.assertIsNotNone(PathFinder.find_spec('pet_bridge', import_paths()))


if __name__ == '__main__':
    unittest.main()
