# SPDX-License-Identifier: GPL-3.0-or-later
"""Storage routing is independent of the current user's actual environment."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app_paths
from app_paths import data_directory, get_package_family_name


class DataPathTests(unittest.TestCase):
    def test_portable_state_stays_beside_application(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp) / 'portable'
            base.mkdir()
            self.assertEqual(
                data_directory(base, {'LOCALAPPDATA': str(Path(temp) / 'profile')}),
                base / 'data',
            )

    def test_installed_state_is_not_beneath_program_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp) / 'program'
            base.mkdir()
            (base / 'installed-mode.txt').write_text('installed')
            profile = Path(temp) / 'profile'
            self.assertEqual(
                data_directory(base, {'LOCALAPPDATA': str(profile)}),
                profile / 'PetDock',
            )
            self.assertFalse((base / 'data').exists())

    def test_store_state_uses_actual_package_family_local_state(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp) / 'WindowsApps' / 'PetDock'
            base.mkdir(parents=True)
            (base / 'store-mode.txt').write_text('store')
            profile = Path(temp) / 'profile'
            family_name = 'PetDock_0123456789abc'

            with patch.object(app_paths, 'get_package_family_name', return_value=family_name):
                path = data_directory(str(base), {'LOCALAPPDATA': str(profile)})

            self.assertIsInstance(path, Path)
            self.assertEqual(
                path,
                profile / 'Packages' / family_name / 'LocalState' / 'PetDock',
            )
            self.assertFalse((base / 'data').exists())

    def test_store_marker_precedes_installed_marker(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp) / 'program'
            base.mkdir()
            (base / 'store-mode.txt').write_text('store')
            (base / 'installed-mode.txt').write_text('installed')
            profile = Path(temp) / 'profile'

            with patch.object(app_paths, 'get_package_family_name', return_value='PetDock_abc'):
                path = data_directory(base, {'LOCALAPPDATA': str(profile)})

            self.assertEqual(
                path,
                profile / 'Packages' / 'PetDock_abc' / 'LocalState' / 'PetDock',
            )

    def test_store_preview_without_package_identity_avoids_application_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp) / 'loose-store-payload'
            base.mkdir()
            (base / 'store-mode.txt').write_text('store')
            profile = Path(temp) / 'profile'
            environment = {
                'LOCALAPPDATA': str(profile),
                'PACKAGE_FAMILY_NAME': '..\\untrusted',
            }

            with patch.object(app_paths, 'get_package_family_name', return_value=None):
                path = data_directory(base, environment)

            self.assertEqual(path, profile / 'PetDock' / 'StorePreview')
            self.assertFalse((base / 'data').exists())


class PackageFamilyNameTests(unittest.TestCase):
    def test_non_windows_package_query_is_safe(self):
        with patch.object(app_paths.os, 'name', 'posix'):
            self.assertIsNone(get_package_family_name())

    def test_win32_no_package_identity_returns_none(self):
        query = Mock(return_value=15700)
        with patch.object(app_paths.os, 'name', 'nt'), \
             patch.object(app_paths.ctypes, 'windll', create=True) as windll:
            windll.kernel32.GetCurrentPackageFamilyName = query
            self.assertIsNone(get_package_family_name())

        query.assert_called_once()
        self.assertIsNone(query.call_args.args[1])

    def test_win32_package_query_retries_with_required_buffer(self):
        family_name = 'PetDock_0123456789abc'

        def query(length_pointer, buffer):
            if buffer is None:
                length_pointer._obj.value = len(family_name) + 1
                return 122
            self.assertEqual(length_pointer._obj.value, len(family_name) + 1)
            buffer.value = family_name
            return 0

        with patch.object(app_paths.os, 'name', 'nt'), \
             patch.object(app_paths.ctypes, 'windll', create=True) as windll:
            win32_query = Mock(side_effect=query)
            windll.kernel32.GetCurrentPackageFamilyName = win32_query
            self.assertEqual(get_package_family_name(), family_name)

        self.assertEqual(win32_query.call_count, 2)
        self.assertIsNone(win32_query.call_args_list[0].args[1])


if __name__ == '__main__':
    unittest.main()
