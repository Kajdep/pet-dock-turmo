# SPDX-License-Identifier: GPL-3.0-or-later
"""Storage routing is independent of the current user's actual environment."""
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app_paths import data_directory


class DataPathTests(unittest.TestCase):
    def test_portable_state_stays_beside_application(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)/'portable'
            base.mkdir()
            self.assertEqual(data_directory(base, {'LOCALAPPDATA': str(Path(temp)/'profile')}), base/'data')

    def test_installed_state_is_not_beneath_program_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)/'program'
            base.mkdir()
            (base/'installed-mode.txt').write_text('installed')
            profile = Path(temp)/'profile'
            self.assertEqual(data_directory(base, {'LOCALAPPDATA': str(profile)}), profile/'PetDock')
            self.assertFalse((base/'data').exists())


if __name__ == '__main__':
    unittest.main()
