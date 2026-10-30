# SPDX-License-Identifier: GPL-3.0-or-later
"""Static layout checks plus an optional cross-build of a minimal installer payload."""
from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
import struct
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
BUILD_SCRIPT = ROOT / "tools" / "build_installer.py"
NSIS_SCRIPT = ROOT / "tools" / "installer.nsi"
LAUNCHER_SOURCE = ROOT / "tools" / "windows_launcher.c"
RUNTIME_NOTICES = ROOT / "tools" / "windows-runtime-NOTICES.txt"


def pe_machine(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    assert data[:2] == b"MZ"
    offset = struct.unpack_from("<I", data, 0x3C)[0]
    assert data[offset : offset + 4] == b"PE\0\0"
    machine = struct.unpack_from("<H", data, offset + 4)[0]
    optional_magic = struct.unpack_from("<H", data, offset + 24)[0]
    return machine, optional_magic


def test_windows_installer_lifecycle_is_explicit() -> None:
    launcher = LAUNCHER_SOURCE.read_text(encoding="utf-8")
    installer = NSIS_SCRIPT.read_text(encoding="utf-8")
    builder = BUILD_SCRIPT.read_text(encoding="utf-8")
    notices = RUNTIME_NOTICES.read_text(encoding="utf-8")

    assert "SPDX-License-Identifier: GPL-3.0-or-later" in launcher
    assert "int WINAPI wWinMain" in launcher
    assert "CreateProcessW(" in launcher
    assert "runtime\\\\pythonw.exe" in launcher
    assert "app\\\\bootstrap.py" in launcher
    assert "MessageBoxW" in launcher
    assert "-municode" in builder
    assert "-mwindows" in builder
    assert "ast.parse" in builder
    assert "import pet_bridge" not in builder
    assert "--launcher-only" in builder
    assert "installed-mode.txt" in builder
    assert "Reusing valid staged launcher" in builder

    assert "Unicode true" in installer
    assert "RequestExecutionLevel user" in installer
    assert 'InstallDir "$LOCALAPPDATA\\Programs\\PetDock"' in installer
    assert "${RunningX64}" in installer
    assert 'File /r "${STAGE}/app/*.*"' in installer
    assert 'File /r "${STAGE}/runtime/*.*"' in installer
    assert 'File "${STAGE}/windows-runtime-NOTICES.txt"' in installer
    assert 'File /r /x' not in installer
    assert 'FileOpen $0 "$INSTDIR\\installed-mode.txt" w' in installer
    assert "CreateShortCut" in installer
    assert "Restore-Desktop-Pet.cmd" in installer
    assert "WriteRegStr HKCU \"Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\PetDock\"" in installer
    assert "DeleteRegKey HKCU \"Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\PetDock\"" in installer
    assert "$LOCALAPPDATA\\PetDock" in installer
    assert "\"Run\"" not in installer
    assert 'RMDir /r "$INSTDIR"' not in installer
    assert 'RMDir /r "$INSTDIR\\app"' in installer
    assert 'RMDir /r "$INSTDIR\\runtime"' in installer
    assert "MUI_PAGE_LICENSE" in installer
    assert "Close Pet Dock before continuing" in installer
    assert "MB_RETRYCANCEL" not in installer
    assert installer.count("IDOK confirm_ok IDCANCEL confirm_cancel") == 2
    assert installer.count("confirm_ok:\n  Return") == 2

    assert "GCC Runtime Library Exception" in notices
    assert "mingw-w64 project" in notices
    assert "NSIS Contributors" in notices


@pytest.mark.skipif(
    not (shutil.which("x86_64-w64-mingw32-gcc") and shutil.which("makensis")),
    reason="MinGW x64 and makensis are required for the cross-build artifact check",
)
def test_minimal_payload_cross_builds_installer(tmp_path: Path) -> None:
    """Compile a synthetic payload; this proves build wiring, not Windows GUI execution."""
    stage = tmp_path / "payload with spaces"
    output = tmp_path / "output with spaces"
    (stage / "app").mkdir(parents=True)
    (stage / "runtime").mkdir()
    (stage / "app" / "bootstrap.py").write_text("# fake bootstrap\n", encoding="utf-8")
    (stage / "runtime" / "pythonw.exe").write_bytes(b"fake runtime placeholder")
    (stage / "Restore-Desktop-Pet.cmd").write_text("@echo off\n", encoding="ascii")
    (stage / "LICENSE").write_text("GPL-3.0-or-later\n", encoding="ascii")
    for filename in (
        "Check-runtime.cmd",
        "Probe-Turmo.cmd",
        "Start.cmd",
        "README.md",
        "START-HERE.txt",
        "THIRD_PARTY_NOTICES.md",
        "windows-runtime-NOTICES.txt",
        "requirements.txt",
        "SOURCE.txt",
        "FILE-MANIFEST.json",
    ):
        (stage / filename).write_text(f"test payload: {filename}\n", encoding="utf-8")
    (stage / "data").mkdir()
    (stage / "data" / "private.txt").write_text("must not be payload input", encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, str(BUILD_SCRIPT), "--stage", str(stage), "--output", str(output)],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "Version:" in completed.stdout
    launcher = stage / "PetDock.exe"
    assert launcher.is_file()
    assert pe_machine(launcher) == (0x8664, 0x20B)

    version = _source_version()
    installer = output / f"Pet-Dock-Setup-v{version}.exe"
    checksum = installer.with_suffix(".exe.sha256")
    assert installer.is_file() and installer.stat().st_size > 0
    assert checksum.read_text(encoding="utf-8") == (
        f"{hashlib.sha256(installer.read_bytes()).hexdigest()}  {installer.name}\n"
    )


def _source_version() -> str:
    # Deliberately only parse the literal assignment; importing the GUI module is not test-safe.
    import ast

    tree = ast.parse((ROOT / "app" / "pet_bridge.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "VERSION" for target in node.targets
        ):
            value = ast.literal_eval(node.value)
            assert isinstance(value, str)
            return value
    raise AssertionError("VERSION assignment missing")
