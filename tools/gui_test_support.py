# SPDX-License-Identifier: GPL-3.0-or-later
"""Temporary app state for GUI regressions; never overwrite personal settings."""
from contextlib import contextmanager
from pathlib import Path
import tempfile
from unittest.mock import patch


@contextmanager
def isolated_app_state(module):
    with tempfile.TemporaryDirectory(prefix='pet-dock-ui-test-') as folder:
        def blank_settings(app):
            app.saved = {}
        with patch.object(module, 'DATA', Path(folder)), patch.object(module.App, 'load_settings', blank_settings):
            yield Path(folder)
