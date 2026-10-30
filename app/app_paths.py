# SPDX-License-Identifier: GPL-3.0-or-later
"""Application-owned paths; installed builds keep state out of program files."""
from pathlib import Path
import os

BASE = Path(__file__).resolve().parent.parent


def data_directory(base=None, environment=None):
    base = Path(base) if base is not None else BASE
    env = os.environ if environment is None else environment
    if (base / 'installed-mode.txt').is_file():
        local = env.get('LOCALAPPDATA')
        return (Path(local) if local else Path.home() / 'AppData/Local') / 'PetDock'
    return base / 'data'
