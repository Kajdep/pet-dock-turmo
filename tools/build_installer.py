#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Build the Pet Dock x64 GUI launcher and per-user Windows installer.

The portable stage is treated as input. This tool never writes installed-mode.txt
there; the NSIS installer writes that marker only to its installation directory.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER_SOURCE = ROOT / "tools" / "windows_launcher.c"
INSTALLER_SCRIPT = ROOT / "tools" / "installer.nsi"
DEFAULT_STAGE = ROOT / "build" / "Pet-Dock-Turmo"
DEFAULT_OUTPUT = ROOT / "dist"
FULL_INSTALLER_ROOT_FILES = (
    "PetDock.exe",
    "Check-runtime.cmd",
    "Probe-Turmo.cmd",
    "Restore-Desktop-Pet.cmd",
    "Start.cmd",
    "README.md",
    "START-HERE.txt",
    "LICENSE",
    "THIRD_PARTY_NOTICES.md",
    "windows-runtime-NOTICES.txt",
    "requirements.txt",
    "SOURCE.txt",
    "FILE-MANIFEST.json",
)


def version_from_source(source: Path = ROOT / "app" / "pet_bridge.py") -> str:
    """Read VERSION as a literal from source without importing application code."""
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if any(isinstance(target, ast.Name) and target.id == "VERSION" for target in node.targets):
            value = ast.literal_eval(node.value)
            if not isinstance(value, str) or not value:
                raise RuntimeError("VERSION must be a non-empty string literal")
            return value
    raise RuntimeError(f"VERSION assignment is missing from {source}")


def executable(name: str) -> str:
    value = shutil.which(name)
    if value is None:
        raise RuntimeError(f"Required build tool was not found on PATH: {name}")
    return value


def run(command: list[str]) -> None:
    print("+", " ".join(repr(item) for item in command), flush=True)
    subprocess.run(command, check=True)


def require_stage(stage: Path) -> None:
    if not stage.is_dir():
        raise RuntimeError(f"Portable stage is missing: {stage}")
    if (stage / "installed-mode.txt").exists():
        raise RuntimeError(
            "Portable stage must not contain installed-mode.txt; only the installed target receives it"
        )
    required = ("app/bootstrap.py", "runtime/pythonw.exe", "Restore-Desktop-Pet.cmd", "LICENSE")
    missing = [name for name in required if not (stage / name).is_file()]
    if missing:
        raise RuntimeError(f"Portable stage is incomplete; missing: {', '.join(missing)}")


def require_full_installer_stage(stage: Path) -> None:
    """Require the owned root files explicitly copied by installer.nsi."""
    require_stage(stage)
    missing = [name for name in FULL_INSTALLER_ROOT_FILES if not (stage / name).is_file()]
    if missing:
        raise RuntimeError(
            "Portable stage is incomplete for the installer; missing: " + ", ".join(missing)
        )


def assert_x64_pe(path: Path) -> None:
    """Verify the generated launcher is a PE32+ AMD64 executable without extra modules."""
    data = path.read_bytes()
    if data[:2] != b"MZ" or len(data) < 0x40:
        raise RuntimeError(f"Launcher is not a PE file: {path}")
    pe_offset = struct.unpack_from("<I", data, 0x3C)[0]
    if data[pe_offset : pe_offset + 4] != b"PE\0\0":
        raise RuntimeError(f"Launcher has no PE signature: {path}")
    machine, sections = struct.unpack_from("<HH", data, pe_offset + 4)
    optional_magic = struct.unpack_from("<H", data, pe_offset + 24)[0]
    if machine != 0x8664 or optional_magic != 0x20B or sections == 0:
        raise RuntimeError(f"Launcher is not a Windows x64 PE32+ executable: {path}")


def build_launcher(stage: Path) -> Path:
    """Compile the Unicode GUI launcher directly into the portable stage."""
    require_stage(stage)
    compiler = executable("x86_64-w64-mingw32-gcc")
    destination = stage / "PetDock.exe"
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix="PetDock-", suffix=".exe", dir=destination.parent, delete=False
    ) as temporary:
        temporary_path = Path(temporary.name)
    try:
        run(
            [
                compiler,
                "-std=c11",
                "-O2",
                "-s",
                "-Wall",
                "-Wextra",
                "-Werror",
                "-DUNICODE",
                "-D_UNICODE",
                "-municode",
                "-mwindows",
                "-static",
                "-static-libgcc",
                str(LAUNCHER_SOURCE),
                "-o",
                str(temporary_path),
                "-luser32",
            ]
        )
        assert_x64_pe(temporary_path)
        os.replace(temporary_path, destination)
    finally:
        temporary_path.unlink(missing_ok=True)
    print(f"Built launcher: {destination.resolve()} ({destination.stat().st_size:,} bytes)")
    return destination


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build_installer(stage: Path, output: Path, app_version: str) -> Path:
    """Compile NSIS with explicit defines; no shell command construction is used."""
    require_full_installer_stage(stage)
    launcher = stage / "PetDock.exe"
    if not launcher.is_file():
        raise RuntimeError("PetDock.exe is missing; build the launcher before compiling the installer")
    assert_x64_pe(launcher)
    makensis = executable("makensis")
    output.mkdir(parents=True, exist_ok=True)
    installer = output / f"Pet-Dock-Setup-v{app_version}.exe"
    run(
        [
            makensis,
            f"-DVERSION={app_version}",
            f"-DSTAGE={stage.resolve().as_posix()}",
            f"-DOUTPUT={output.resolve().as_posix()}",
            str(INSTALLER_SCRIPT),
        ]
    )
    if not installer.is_file() or installer.stat().st_size == 0:
        raise RuntimeError(f"NSIS did not create the expected installer: {installer}")
    digest = sha256(installer)
    checksum = installer.with_suffix(installer.suffix + ".sha256")
    checksum.write_text(f"{digest}  {installer.name}\n", encoding="utf-8")
    print(f"Built installer: {installer.resolve()} ({installer.stat().st_size:,} bytes)")
    print(f"SHA-256: {digest}")
    print(f"Checksum: {checksum.resolve()}")
    return installer


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--stage", type=Path, default=DEFAULT_STAGE, help="Portable package staging directory"
    )
    parser.add_argument(
        "--output", type=Path, default=DEFAULT_OUTPUT, help="Installer output directory"
    )
    parser.add_argument(
        "--launcher-only",
        action="store_true",
        help="Compile only PetDock.exe into --stage (for the portable ZIP packaging step)",
    )
    args = parser.parse_args(argv)
    stage = args.stage.resolve()
    output = args.output.resolve()

    print("[1/3] Reading application version from source AST...", flush=True)
    app_version = version_from_source()
    print(f"Version: {app_version}", flush=True)
    print("[2/3] Building x64 Unicode GUI launcher...", flush=True)
    if args.launcher_only:
        # The portable package producer requests a fresh launcher before it
        # writes its manifest and ZIP, so force a recompilation in this mode.
        build_launcher(stage)
        print("Launcher-only build complete; the portable stage was not marked as installed.")
        return 0
    launcher = stage / "PetDock.exe"
    try:
        assert_x64_pe(launcher)
        print(f"Reusing valid staged launcher: {launcher.resolve()}")
    except (FileNotFoundError, RuntimeError):
        build_launcher(stage)
    print("[3/3] Building per-user NSIS installer...", flush=True)
    build_installer(stage, output, app_version)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, subprocess.CalledProcessError) as error:
        print(f"Build failed: {error}", file=sys.stderr)
        raise SystemExit(1)
