# SPDX-License-Identifier: GPL-3.0-or-later
"""Emergency desktop-pet restoration; does not open or write to the USB screen."""
from pathlib import Path
import sys
import traceback
from window_guard import DesktopPetGuard
from app_paths import data_directory

if __name__ == '__main__':
    state = data_directory() / 'desktop-pet-restore.json'
    try:
        guard = DesktopPetGuard(state)
        guard.restore()
        print('Desktop-pet recovery finished. If there was no saved position, nothing was changed.')
    except Exception:
        traceback.print_exc()
        sys.exit(1)
