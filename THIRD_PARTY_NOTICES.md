# Third-party notices

## Vendored display protocol library

A small subset of **turing-smart-screen-python** is included under `app/vendor/library`.

- Source: https://github.com/mathoudebine/turing-smart-screen-python
- Pinned commit: `2b33ab4f00a096916dd6a1174441a53a7ec33b03`
- License: GPL-3.0-or-later
- Scope/modifications: see `app/vendor/library/UPSTREAM.md`

The upstream `LICENSE`, `AUTHORS`, and `COPYRIGHT` files are retained. Public third-party author/copyright notices are legal attribution, not local user data.

## Installed runtime dependencies

The portable builder obtains CPython, Tcl/Tk, pyserial, Pillow, NumPy, windows-capture, OpenCV, psutil, sv-ttk, and any resolved dependencies from their distribution sources. These components retain their own licenses and installed notice files.

Each portable artifact includes `runtime/COMPONENTS.json` with source URLs, versions, declared licenses, and download hashes. Consult each component's included license for full terms; Pet Dock's GPL license does not replace their licenses.

## Excluded material

No host application's pet artwork, chat contents, vendor TURMO executables/drivers, proprietary sensor stack, user diagnostic reports, credentials, or account configuration is included in this repository or distributed by this project.
