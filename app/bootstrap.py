# SPDX-License-Identifier: GPL-3.0-or-later
"""Portable startup wrapper: local logging and a clear Windows error dialog."""
from pathlib import Path
import ctypes
import os
import sys
import traceback
from app_paths import data_directory

base = Path(__file__).resolve().parent.parent
data = data_directory()
try:
    data.mkdir(parents=True, exist_ok=True)
    os.chdir(base)
    log = open(data / 'console.log', 'a', encoding='utf-8', buffering=1)
    sys.stdout = log
    sys.stderr = log
    if str(base / 'app') not in sys.path:
        sys.path.insert(0, str(base / 'app'))
    import pet_bridge
    pet_bridge.main()
except Exception:
    error = traceback.format_exc()
    path = data / 'startup-error.txt'
    try:
        path.write_text(error, encoding='utf-8')
    except OSError:
        pass
    if os.name == 'nt':
        ctypes.windll.user32.MessageBoxW(None,
            f'The bridge could not start.\n\n{error[-1500:]}\n\nDetails: {path}',
            'Codex Pet · Turmo Bridge', 0x10)
    else:
        raise
