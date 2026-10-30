# Pet Dock for TURMO

A small, local Windows companion app that gives your **live Codex / ChatGPT desktop pet** a home on a compatible TURMO USB screen.

Pet Dock captures the actual floating pet window and mirrors its visuals to the display. The pet's behavior stays in its original app; Pet Dock does not replace its personality, inspect conversations, or require an API key.

## TURMO display / item link

**[View the TURMO item on Temu](https://temu.to/k/ptvlxtaxo3l)**

**Coupon price [$14.98].** This promotional price was supplied for the shared link. Coupon eligibility, availability, shipping, taxes, and the final checkout price may vary. Check the listing before purchasing. The product link is an example hardware listing, not a guarantee that every unit uses the same USB protocol.

This is an independent open-source project, not an official OpenAI, TURMO, or Temu product.

## Features

- Live native-window capture, with desktop-area capture as a fallback.
- Automatic pet/bubble framing, source-preview zoom, and manual cropping.
- Visible-content fitting to avoid wasting screen space on transparent padding.
- Live portrait, landscape, and reverse orientation changes.
- Revision-A changed-region updates, stale-frame avoidance, and full redraws when the layout changes.
- Optional CPU, RAM, disk, network, CPU-frequency, and supported NVIDIA GPU readings.
- An adjustable, persistent companion size and a one-click size reset.
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

Get the portable Windows ZIP from **[GitHub Releases](https://github.com/Kajdep/pet-dock-turmo/releases/latest)**.

1. Extract the **entire ZIP** into a writable folder. Do not launch from inside the archive.
2. Close Formbig/Turmo, including its tray process.
3. Enable the floating pet in its host app.
4. Open **`Start.cmd`**. The portable release includes Python and dependencies; no separate Python installation is needed.
5. On **Companion**, choose the transparent pet window, then **Capture preview**. It may be titled ChatGPT or Codex. A large transparent canvas is normal.
6. Confirm the Source and Turmo previews. Drag on Source to override the crop if needed.
7. On **Display**, select the COM device, Protocol A, and orientation. Send the test pattern first, then **Start screen**.
8. Enable and select optional readings on **Metrics**.

Windows may warn about unsigned downloaded software. Only use artifacts from a source you trust; do not disable system security protections just to run this program.

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

The app creates an ignored **`data/`** folder for settings, logs, and recovery state. User-requested diagnostics can include window titles, process names, USB serial/instance IDs, driver details, and local paths. **Inspect and redact these before sharing.** Nothing is uploaded automatically.

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

The runtime cross-builder is supported on **Linux with Python 3.12+**, pip, and `msiextract` from `msitools`. It downloads the official CPython embedded distribution, its Tcl/Tk installer component, and Windows x64 wheels.

```bash
# Debian/Ubuntu prerequisite:
sudo apt install msitools
python3 tools/build_portable.py
python3 tools/package_release.py --output dist
```

The archive is written to `dist/`, with licenses, source provenance, file hashes, and no existing `data/` content. `portable/`, `build/`, and `dist/` are ignored by Git. The runtime contains third-party native binaries; a successful cross-build is not equivalent to a live Windows hardware test.

## Tests and contributions

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s app/tests -v
```

Portable import-path tests skip when no runtime has been built. Linux tests require a Python installation with Tk; headless GUI regressions also require Xvfb:

```bash
xvfb-run -a python tools/ui_regression_test.py
xvfb-run -a python tools/metrics_toggle_regression.py
xvfb-run -a python tools/orientation_regression.py
```

Tests cover crop/alpha composition, metrics on/off round trips, live-worker rotation, full redraws, transport errors/deltas, framing, and window/PID recovery guards. Native Windows capture and physical display behavior still require device testing.

Issues and pull requests are welcome. State the app version, Windows version, USB ID, and protocol, and use a minimal redacted report. Do not post private window titles, identifiers, chat contents, or pet assets without permission.

## License and third-party notices

Pet Dock is **GPL-3.0-or-later**. See [LICENSE](LICENSE) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

The vendored display subset comes from [turing-smart-screen-python](https://github.com/mathoudebine/turing-smart-screen-python) at the commit recorded in [`app/vendor/library/UPSTREAM.md`](app/vendor/library/UPSTREAM.md). Its copyright and author notices remain intact. Other dependencies keep their own licenses in the portable runtime.

No OpenAI pet artwork or vendor TURMO executables/drivers are redistributed.
