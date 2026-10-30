# SPDX-License-Identifier: GPL-3.0-or-later
"""Application-owned paths; installed builds keep state out of program files."""
import ctypes
from pathlib import Path
import os

BASE = Path(__file__).resolve().parent.parent

# Win32 values returned by GetCurrentPackageFamilyName.
_APPMODEL_ERROR_NO_PACKAGE = 15700
_ERROR_INSUFFICIENT_BUFFER = 122
_ERROR_SUCCESS = 0


def get_package_family_name():
    """Return the current process package family name, if it has one.

    A packaged Win32 child process inherits its package identity.  The Win32
    query is deliberately made at runtime so importing this module remains
    safe on non-Windows platforms and for unpackaged builds.
    """
    if os.name != 'nt':
        return None

    try:
        query = ctypes.windll.kernel32.GetCurrentPackageFamilyName
        length = ctypes.c_uint32()
        result = query(ctypes.byref(length), None)
        if result == _APPMODEL_ERROR_NO_PACKAGE:
            return None
        if result != _ERROR_INSUFFICIENT_BUFFER or not length.value:
            return None

        package_family_name = ctypes.create_unicode_buffer(length.value)
        result = query(ctypes.byref(length), package_family_name)
        if result != _ERROR_SUCCESS:
            return None
        return package_family_name.value or None
    except (AttributeError, OSError, TypeError):
        return None


def _local_app_data_directory(environment):
    local = environment.get('LOCALAPPDATA')
    return Path(local) if local else Path.home() / 'AppData/Local'


def data_directory(base=None, environment=None):
    base = Path(base) if base is not None else BASE
    env = os.environ if environment is None else environment

    if (base / 'store-mode.txt').is_file():
        local_app_data = _local_app_data_directory(env)
        package_family_name = get_package_family_name()
        if package_family_name:
            return (local_app_data / 'Packages' / package_family_name /
                    'LocalState' / 'PetDock')
        return local_app_data / 'PetDock' / 'StorePreview'

    if (base / 'installed-mode.txt').is_file():
        return _local_app_data_directory(env) / 'PetDock'
    return base / 'data'
