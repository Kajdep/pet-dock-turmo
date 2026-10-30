# Pet Dock for TURMO

<p align="center">
  <img src="https://github.com/Kajdep/pet-dock-turmo/blob/main/pethero.png?raw=true" alt="Pet Dock Hero Image" width="100%">
</p>

A small, local Windows companion app that gives your **live Codex / ChatGPT desktop pet** a home on a compatible TURMO USB screen.

Pet Dock captures the actual floating pet window and mirrors its visuals to the display. The pet's behavior stays in its original app; Pet Dock does not replace its personality, inspect conversations, or require an API key.

## TURMO display / item link

**[View the TURMO item on Temu](https://temu.to/k/ptvlxtaxo3l)**

This is an independent open-source project, not an official OpenAI, TURMO, or Temu product.

## Features

- Live native-window capture, with desktop-area capture as a fallback.
- Automatic pet/bubble framing, source-preview zoom, and manual cropping.
- Visible-content fitting to avoid wasting screen space on transparent padding.
- Live portrait, landscape, and reverse orientation changes.
- Revision-A changed-region updates, stale-frame avoidance, and full redraws when the layout changes.
- Optional CPU, RAM, disk, network, CPU-frequency, and supported NVIDIA GPU readings.
- An adjustable, persistent companion size and a one-click size reset.
- A live Design tab for local background images, palettes, custom colors/fonts, pet placement, and Cards/Bars/Rings stats indicators.
- A per-user Windows installer with Start menu shortcuts, plus a portable `PetDock.exe` launcher.
- A borderless Windows capture request, with a safe fallback if Windows retains its indicator.
- Experimental reversible off-screen pet placement, restore controls, and local crash recovery.
- Read-only device inspection and an optional known-protocol transfer benchmark.

## Requirements and compatibility

- **Windows 10/11, x64.** Native capture depends on Windows Graphics Capture support.
- The host application must support a floating pet window and continue rendering it.
- A compatible **3.5-inch USB serial display**. The confirmed path is `VID_1A86 / PID_5722`, Protocol A, with a configured portrait geometry of 320×480 and RGB565 pixels. This USB ID is not proof that every clone has identical firmware.
- Close Formbig/Turmo and other display controllers before connecting. Only one process can own the COM port.

The USB screen is not an ordinary HDMI/Windows secondary monitor. Pet Dock uploads bitmap frames; it does not run the pet on the panel, install a virtual monitor, add touch/click forwarding, change drivers, or flash firmware.

## Download and run

**[0.3.0 preview: installer and portable download](https://github.com/Kajdep/pet-dock-turmo/releases/tag/v0.3.0)** introduces the screen designer and simpler installation. The project owner has reported a successful Windows test; the downloads remain unsigned and marked pre-release. This does not establish compatibility with every device or Windows setup. The **[0.2.1 stable release](https://github.com/Kajdep/pet-dock-turmo/releases/tag/v0.2.1)** remains available.

**Easiest:** run `Pet-Dock-Setup-v0.3.0.exe`, then open **Pet Dock** from the Start menu. It installs for the current user without administrator privileges, driver changes, or startup registration. Close Pet Dock before installation or upgrades.

**No installation:** extract the **entire portable ZIP** into a writable folder and open **`PetDock.exe`**. `Start.cmd` is still available as a fallback. Do not run from inside the ZIP. Both downloads include Python and dependencies; no separate Python installation is required.

1. Install or extract the application using one of the options above.
2. Close Formbig/Turmo, including its tray process.
3. Enable the floating pet in its host app.
4. Open **Pet Dock** from its shortcut or portable `PetDock.exe`.
5. On **Companion**, choose the transparent pet window, then **Capture preview**. It may be titled ChatGPT or Codex. A large transparent canvas is normal.
6. Confirm the Source and Turmo previews. Drag on Source to override the crop if needed.
7. On **Display**, select the COM device, Protocol A, and orientation. Send the test pattern first, then **Start screen**.
8. Enable and select optional readings on **Metrics**.
9. Use **Design** to customize the screen. Changes preview live and are saved locally.

Windows may warn about unsigned downloaded software. Only use artifacts from a source you trust; do not disable system security protections just to run this program.

### Microsoft Store packaging

A [Store-only MSIX build recipe](store/README.md) is available for certification preparation. It uses separate per-package user storage and keeps the existing portable/installer behavior unchanged. The initial Store package version is `1.0.0.0`, while the app remains `0.3.0`. This is not a live Store listing or a certified release.

Microsoft signs MSIX/AppX packages distributed through the Store after approval; it does not sign the existing EXE or ZIP downloads. The unsigned MSIX upload is not a public sideload installer. See the [app privacy policy](PRIVACY.md) and test the packaged app on Windows before submission.

### Customize the screen

The **Design** tab includes background color and a local image picker for PNG, JPEG, WebP, or BMP images. **Contain** preserves the whole image; **Cover** intentionally crops its edges to fill the screen. Image dimming improves contrast behind the pet and stats. Imports are copied as metadata-free PNGs into the private assets folder; original files are not modified. Animated image playback is not implemented.

Choose **Midnight, Forest, Amber, or Violet** palettes, or pick your own background, card, border, label, value, and accent colors. Resize the pet from 10–100% of the available stage, reposition it horizontally/vertically, or reset it to centered at 90%. This never changes the source crop.

Stats can use **Cards**, horizontal **Bars**, or circular **Rings**. Place the panel at the top or bottom and adjust its opacity. CPU/RAM/GPU/disk percentages and VRAM use their reported ranges. Temperature indicators use the explicit **0 to configured maximum Celsius** scale; they do not imply a device-specific safety threshold. Network rates and CPU frequency remain text because the app does not know a meaningful maximum. Missing readings stay N/A without a fake fill.

Choose a common Windows font or import a TTF/OTF you are licensed to use, then set the preferred pixel size. Fonts and colors affect the stats, **not** the captured pet artwork or its speech bubble. Text shrinks to fit a card when necessary. Missing or invalid assets/fonts fall back safely.

A custom background is visible through **transparent native-window capture**. Desktop-area capture and **Ignore alpha** include opaque pixels that cover the background; Pet Dock does not perform background removal. Selecting more metrics leaves less pet space and can require smaller text on this 320×480 panel.

The current designer is a constrained pet-plus-stats layout, not a free-form drag-and-drop widget editor. All selected stats share an indicator style and palette; per-stat widget styles, arbitrary widget placement, and theme export/import are not included.

### Controls and sizing

Orientation applies on the USB worker after its current transfer drains, followed by a full redraw. No capture restart is required. Pause before changing protocol, port, or transport timing.

Turning metrics off restores the full pet area and requests a complete LCD redraw, even on a static capture. Metrics do not change the crop or size slider. Selecting many metrics necessarily leaves less room for the pet **while they are on**.

**Fit visible pet** removes empty alpha padding inside the current crop for output fitting only. Disable it if you intentionally want a padded animation canvas. The size slider goes up to 100%; **Reset size to 90%** returns to the default. Larger imagery can increase USB transfer work.

### Performance limits

A full 320×480 RGB565 frame contains 307,200 pixel bytes. Changed-region updates reduce unnecessary traffic for a small animated pet. The sender chooses the newest capture after host queue checks instead of accumulating old frames.

FPS is a **target**, not a guarantee of desktop animation rate. Host submission/queue timing does not measure visible panel completion. Physical maximum FPS and firmware buffer depth are not established.

TURMO-compatible timing uses a 25 ms pause after the bitmap header and 20 ms after pixel submission, based on the compatible vendor image path. Keep it enabled if faster sending causes corruption. Disable low-lag partial updates if they do not work correctly on a particular unit.

### Metrics and experimental visibility

Unsupported sensors show **N/A**. GPU readings currently use an existing local NVIDIA `nvidia-smi`, selecting the first reported adapter; AMD/Intel GPU telemetry is not implemented. CPU temperature is only available when a supported sensor provider exposes it. No sensor driver or FPS-hooking tool is installed.

Off-screen pet placement is **experimental**. Some host applications stop rendering off-screen, go blank, or repeatedly return a frozen pose. Stopped/blank capture triggers restoration, but an idle pet cannot always be distinguished from a frozen capture. Use **Restore desktop pet** or **`Restore-Desktop-Pet.cmd`** if needed. Do not delete the recovery state while the window is off-screen.

## Privacy

Normal operation is local. Pet Dock does not read chat history, extract pet assets, send screenshots or telemetry to a server, or require credentials. Optional GPU collection runs a local subprocess. Building the portable runtime downloads Python and dependencies; that is separate from app operation.

The full [privacy policy](PRIVACY.md) also covers Store-mode storage and deliberate diagnostic sharing.

Portable/source mode creates an ignored **`data/`** folder beside the application. Installed mode stores settings, logs, recovery state, and imported assets under **`%LOCALAPPDATA%\PetDock`**, outside the program directory. Updates and uninstall retain this personal data. User-requested diagnostics can include window titles, process names, USB serial/instance IDs, driver details, and local paths. **Inspect and redact these before sharing.** Nothing is uploaded automatically.

To move portable settings into an installed copy, close both apps and restore any off-screen pet first. Copy the contents of the portable `data/` folder into `%LOCALAPPDATA%\PetDock`, including `assets/`; do not transfer a live `desktop-pet-restore.json`. Imported image/font references in settings are relative hashed asset names, not original filenames or absolute paths. Assets are never included in Git or release archives.

The repository and release exclude local settings, logs, diagnostic reports, desktop screenshots, captured pet assets, account configuration, and development-session artifacts. Public upstream license/copyright notices are retained as required.

## Troubleshooting and diagnostics

- Run **`Check-runtime.cmd`** for portable-runtime import/Tk checks.
- Close Pet Dock and Formbig, then open **`Probe-Turmo.cmd`**: **I** inspects device/driver details without LCD writes; **B** temporarily shows test images to benchmark the confirmed Protocol-A path.
- Inspect `data/turmo-device-report.json` before sharing it.
- If the native preview is blank, confirm the correct pet window, try **Ignore alpha**, or use desktop-area capture.
- If Windows retains the yellow capture border, the OS rejected or did not honor the request. Desktop-area capture avoids the WGC indicator, but includes anything overlapping the selected desktop area.
- If partial images corrupt, pause, turn off low-lag updates, keep compatible timing on, and restart.

## Run from source

Install **Python 3.12 x64 with Tcl/Tk** on Windows, then in PowerShell:

```powershell
git clone https://github.com/Kajdep/pet-dock-turmo.git
cd pet-dock-turmo
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app\pet_bridge.py
```

`Start.cmd` is the **portable-release** launcher. A source checkout has no bundled `runtime/`; use the Python command above until you build a portable release.

## Build the portable Windows release

The runtime cross-builder is supported on **Linux with Python 3.12+**, pip, and `msiextract` from `msitools`. MinGW-w64 builds the launcher and NSIS builds the installer. The runtime builder downloads the official CPython embedded distribution, its Tcl/Tk installer component, and Windows x64 wheels.

```bash
# Debian/Ubuntu prerequisite:
sudo apt install msitools gcc-mingw-w64-x86-64 nsis
python3 tools/build_portable.py
python3 tools/package_release.py --output dist
python3 tools/build_installer.py --output dist
```

The ZIP and Setup.exe are written to `dist/`, with checksum sidecars, licenses, source provenance, and no existing user data. The launcher source is `tools/windows_launcher.c`; the installer source is `tools/installer.nsi`. The installer writes an `installed-mode.txt` marker only into the target installation, never into the portable ZIP. `portable/`, `build/`, and `dist/` are ignored by Git. The runtime contains third-party native binaries; a successful cross-build is not equivalent to a live Windows hardware test. Builds are unsigned, do not automatically update, and do not install sensor/display drivers.

## Tests and contributions

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q app/tests
```

Portable import-path tests skip when no runtime has been built. Linux tests require a Python installation with Tk; headless GUI regressions also require Xvfb:

```bash
xvfb-run -a python tools/ui_regression_test.py
xvfb-run -a python tools/metrics_toggle_regression.py
xvfb-run -a python tools/orientation_regression.py
xvfb-run -a python tools/design_regression.py
```

Tests cover crop/alpha composition, metrics on/off round trips, live-worker rotation, full redraws, transport errors/deltas, framing, and window/PID recovery guards. Native Windows capture and physical display behavior still require device testing.

Issues and pull requests are welcome. State the app version, Windows version, USB ID, and protocol, and use a minimal redacted report. Do not post private window titles, identifiers, chat contents, or pet assets without permission.

## License and third-party notices

Pet Dock is **GPL-3.0-or-later**. See [LICENSE](LICENSE) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

The vendored display subset comes from [turing-smart-screen-python](https://github.com/mathoudebine/turing-smart-screen-python) at the commit recorded in [`app/vendor/library/UPSTREAM.md`](app/vendor/library/UPSTREAM.md). Its copyright and author notices remain intact. Other dependencies keep their own licenses in the portable runtime.

No OpenAI pet artwork or vendor TURMO executables/drivers are redistributed.
