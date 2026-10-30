# SPDX-License-Identifier: GPL-3.0-or-later
"""Run only when the user opens Check-runtime.cmd; no USB protocol commands."""
import sys
import traceback
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
print('Codex Pet · Turmo Bridge runtime check')
print('Python:', sys.version)
failed = False
for name in ['tkinter', 'serial', 'PIL', 'numpy', 'cv2', 'windows_capture', 'psutil', 'sv_ttk', 'capture', 'display_io', 'framing', 'metrics', 'window_guard', 'ui_layout', 'device_probe', 'screen_style', 'design_ui', 'app_paths']:
    try:
        module = __import__(name)
        print(name, 'OK', getattr(module, '__version__', ''))
    except Exception:
        failed = True
        print(name, 'FAILED')
        traceback.print_exc()
try:
    import tkinter as tk
    root = tk.Tk()
    root.withdraw()
    print('Tk interpreter:', root.tk.call('info', 'patchlevel'))
    root.destroy()
except Exception:
    failed = True
    traceback.print_exc()
try:
    from display_io import list_displays
    print('Matching COM displays (discovery only):', list_displays())
except Exception:
    failed = True
    traceback.print_exc()
print('RESULT:', 'FAILED — inspect the messages above.' if failed else 'Runtime checks passed. This does not test pet capture or physical LCD output.')
sys.exit(1 if failed else 0)
