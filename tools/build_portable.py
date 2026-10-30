#!/usr/bin/env python3
"""Build the Windows x64 embedded Python runtime used by this package.

This cross-builder is supported on Linux with Python 3.12+ and msitools.
It downloads prebuilt Windows x64 wheels. It never modifies application source directories.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from email.parser import BytesParser
from email.policy import default
from pathlib import Path

PYTHON_VERSION = "3.12.10"
TCLTK_VERSION = "8.6.15"
ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "portable" / "runtime"
CACHE = ROOT / "tools" / "python312-downloads"
DOWNLOADS = {
    "python-3.12.10-embed-amd64.zip": (
        "https://www.python.org/ftp/python/3.12.10/"
        "python-3.12.10-embed-amd64.zip"
    ),
    "tcltk.msi": "https://www.python.org/ftp/python/3.12.10/amd64/tcltk.msi",
}
REQUIREMENTS = [
    "pyserial==3.5",
    "Pillow>=11,<13",
    "numpy>=2,<3",
    "windows-capture==2.0.1",
    "opencv-python",
    "psutil==7.2.2",
    "sv-ttk==2.6.1",
]


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def download(filename: str, url: str) -> Path:
    CACHE.mkdir(parents=True, exist_ok=True)
    output = CACHE / filename
    if output.exists() and output.stat().st_size:
        print(f"Using cached {output.name}")
        return output
    temporary = output.with_suffix(output.suffix + ".part")
    print(f"Downloading {url}")
    request = urllib.request.Request(url, headers={"User-Agent": "portable-runtime-builder/1"})
    with urllib.request.urlopen(request, timeout=60) as response, temporary.open("wb") as fh:
        shutil.copyfileobj(response, fh)
    temporary.replace(output)
    return output


def copy_tree(source: Path, destination: Path) -> None:
    if not source.is_dir():
        raise RuntimeError(f"Expected MSI component was not found: {source}")
    shutil.copytree(source, destination, dirs_exist_ok=True)


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def distribution_metadata(site_packages: Path) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for dist_info in sorted(site_packages.glob("*.dist-info")):
        metadata_path = dist_info / "METADATA"
        if not metadata_path.is_file():
            continue
        message = BytesParser(policy=default).parsebytes(metadata_path.read_bytes())
        classifiers = message.get_all("Classifier", [])
        license_value = message.get("License-Expression") or message.get("License") or "Not declared"
        if license_value.strip().upper() in {"UNKNOWN", ""}:
            license_classifiers = [c for c in classifiers if c.startswith("License ::")]
            license_value = "; ".join(license_classifiers) or "Not declared"
        records.append(
            {
                "name": message.get("Name", dist_info.name),
                "version": message.get("Version", "unknown"),
                "license": license_value.replace("\n", " ").strip(),
                "homepage": message.get("Home-page", ""),
            }
        )
    return records


def package_sources(report_path: Path, metadata: list[dict[str, str]]) -> list[dict[str, object]]:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    source_by_name: dict[str, dict[str, object]] = {}
    for item in report.get("install", []):
        package_meta = item.get("metadata", {})
        name = package_meta.get("name")
        if name:
            source_by_name[normalize(name)] = item.get("download_info", {})
    result: list[dict[str, object]] = []
    for item in metadata:
        source = source_by_name.get(normalize(item["name"]), {})
        archive = source.get("archive_info", {}) if isinstance(source, dict) else {}
        result.append(
            {
                **item,
                "source_url": source.get("url", "") if isinstance(source, dict) else "",
                "sha256": archive.get("hashes", {}).get("sha256", "") if isinstance(archive, dict) else "",
            }
        )
    return result


def tcltk_version(tcl_root: Path) -> str:
    # The frozen Python 3.12.10 x64 Tcl/Tk MSI reports 8.6.15 in tcl86t.dll.
    # Check the expected script library exists so a corrupt extraction fails early.
    if not (tcl_root / "tcl" / "tcl8.6" / "init.tcl").is_file():
        raise RuntimeError("Tcl/Tk MSI extraction is missing tcl/tcl8.6/init.tcl")
    return TCLTK_VERSION


def write_runtime_docs(stage: Path, artifact_paths: dict[str, Path], packages: list[dict[str, object]], tcl_version: str) -> None:
    source_components = {
        "python": {
            "name": "CPython embedded distribution",
            "version": PYTHON_VERSION,
            "architecture": "Windows x64",
            "source_url": DOWNLOADS["python-3.12.10-embed-amd64.zip"],
            "sha256": digest(artifact_paths["python-3.12.10-embed-amd64.zip"]),
            "license": "PSF-2.0 (Python Software Foundation License Version 2)",
        },
        "tcltk": {
            "name": "Tcl/Tk component from the CPython Windows installer",
            "version": tcl_version,
            "architecture": "Windows x64",
            "source_url": DOWNLOADS["tcltk.msi"],
            "sha256": digest(artifact_paths["tcltk.msi"]),
            "license": "Tcl/Tk BSD-style license (see tcl/tcl8.6/license.terms and tcl/tk8.6/license.terms)",
        },
        "python_packages": packages,
    }
    (stage / "COMPONENTS.json").write_text(
        json.dumps(source_components, indent=2, sort_keys=False) + "\n", encoding="utf-8"
    )

    package_rows = "\n".join(
        f"| {p['name']} | {p['version']} | {p['license']} | {p['source_url']} |"
        for p in packages
    )
    (stage / "COMPONENTS.md").write_text(
        f"""# Portable runtime components

## Core runtime

| Component | Version | Architecture | License | Official source | SHA-256 |
|---|---:|---|---|---|---|
| CPython embedded distribution | {PYTHON_VERSION} | Windows x64 | PSF-2.0 | {DOWNLOADS['python-3.12.10-embed-amd64.zip']} | `{source_components['python']['sha256']}` |
| Tcl/Tk MSI component | {tcl_version} | Windows x64 | Tcl/Tk BSD-style | {DOWNLOADS['tcltk.msi']} | `{source_components['tcltk']['sha256']}` |

The Tcl/Tk MSI contributes root-level `_tkinter.pyd`, `tcl86t.dll`, `tk86t.dll`,
`zlib1.dll`, plus `tcl/` and `Lib/tkinter/`. `lib.msi` is deliberately not included: the embedded
ZIP already provides the core standard library in `python312.zip`; Tcl/Tk is the
only missing standard component needed for this runtime.

## Installed PyPI packages and resolved dependencies

| Package | Resolved version | Declared license | Downloaded wheel source |
|---|---:|---|---|
{package_rows}

`pip-install-report.json` has the exact wheel URLs and SHA-256 values for this
build. License values above are the `License-Expression` or `License` metadata
in each installed distribution; consult each installed distribution's license
files for full text.

## Runtime behavior

`python312._pth` is deliberately isolated and enables `import site`. It contains:

```text
python312.zip
.
Lib
Lib/site-packages
../app
import site
```

`Lib/site-packages/sitecustomize.py` sets `TCL_LIBRARY` and `TK_LIBRARY` to this
runtime's local `tcl/tcl8.6` and `tcl/tk8.6` paths when callers have not already
set them. No package install, PATH change, driver installation, administrator
rights, global Python installation, or network access is needed to run the
already-bundled runtime.

## `Start.cmd` integration

In the final ZIP, `Start.cmd` is alongside `runtime` and `app`; its launch setup is:

```bat
@echo off
setlocal
set "RUNTIME=%~dp0runtime"
set "TCL_LIBRARY=%RUNTIME%\\tcl\\tcl8.6"
set "TK_LIBRARY=%RUNTIME%\\tcl\\tk8.6"
set "PYTHONPATH=%~dp0app"
rem CPython ignores PYTHONPATH while python312._pth is present; this assignment is
rem only useful to any non-embedded child tools. Python imports use ../app instead.
"%RUNTIME%\\pythonw.exe" "%~dp0app\\bootstrap.py"
```

The required `../app` entry resolves relative to the final `runtime`, i.e. to
the adjacent `app`. If the final application lives elsewhere, add its *relative*
path to `python312._pth`; do not rely on `PYTHONPATH` while the `._pth` isolation
file is enabled.

## Windows validation command

Run after packaging on a Windows x64 machine (do not run `pythonw.exe` for the
console check):

```bat
runtime\\python.exe -I -c "import sys, tkinter as tk, serial, PIL, numpy, windows_capture, cv2, psutil, sv_ttk; r=tk.Tk(); print(sys.version); print('Tk', r.tk.call('info','patchlevel')); r.destroy(); print(serial.__version__, PIL.__version__, numpy.__version__, cv2.__version__, psutil.__version__)"
```

This build was assembled and statically inspected on Linux; the Windows GUI,
imports, and capture backend have **not** been executed here.
""",
        encoding="utf-8",
    )
    (stage / "requirements-portable.txt").write_text("\n".join(REQUIREMENTS) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep-work", action="store_true", help="Keep temporary extracted MSI content")
    args = parser.parse_args()

    if not shutil.which("msiextract"):
        raise RuntimeError("msiextract is required (Debian/Ubuntu: sudo apt install msitools)")
    if sys.version_info < (3, 12):
        raise RuntimeError("Run this builder with Python 3.12 or newer for pip's cp312 resolver")

    artifacts = {name: download(name, url) for name, url in DOWNLOADS.items()}
    with tempfile.TemporaryDirectory(prefix="portable-python312-") as temp_text:
        temp = Path(temp_text)
        extracted_tcltk = temp / "tcltk"
        subprocess.run(
            ["msiextract", "-C", str(extracted_tcltk), str(artifacts["tcltk.msi"])],
            check=True,
            stdout=subprocess.DEVNULL,
        )

        stage = temp / "runtime"
        stage.mkdir()
        with zipfile.ZipFile(artifacts["python-3.12.10-embed-amd64.zip"]) as archive:
            archive.extractall(stage)

        # Components omitted by the embedded ZIP but needed for tkinter.
        for filename in ("_tkinter.pyd", "tcl86t.dll", "tk86t.dll", "zlib1.dll"):
            source = extracted_tcltk / "DLLs" / filename
            if not source.is_file():
                raise RuntimeError(f"Tcl/Tk MSI is missing {filename}")
            shutil.copy2(source, stage / filename)
        copy_tree(extracted_tcltk / "tcl", stage / "tcl")
        copy_tree(extracted_tcltk / "Lib" / "tkinter", stage / "Lib" / "tkinter")

        # Explicit paths make this embedded interpreter deterministic and let sitecustomize load.
        (stage / "python312._pth").write_text(
            "python312.zip\n.\nLib\nLib/site-packages\n../app\nimport site\n", encoding="utf-8"
        )
        site_packages = stage / "Lib" / "site-packages"
        site_packages.mkdir(parents=True, exist_ok=True)
        (site_packages / "sitecustomize.py").write_text(
            "# Local Tcl/Tk paths for the isolated embeddable interpreter.\n"
            "import os\n"
            "from pathlib import Path\n"
            "_runtime = Path(__file__).resolve().parents[2]\n"
            "os.environ.setdefault('TCL_LIBRARY', str(_runtime / 'tcl' / 'tcl8.6'))\n"
            "os.environ.setdefault('TK_LIBRARY', str(_runtime / 'tcl' / 'tk8.6'))\n",
            encoding="utf-8",
        )

        report = stage / "pip-install-report.json"
        pip_command = [
            sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
            "--only-binary=:all:", "--platform", "win_amd64", "--implementation", "cp",
            "--python-version", "3.12", "--abi", "cp312", "--target", str(site_packages),
            "--report", str(report), *REQUIREMENTS,
        ]
        print("Installing resolved Windows x64 binary wheels into the runtime...")
        subprocess.run(pip_command, check=True)

        package_data = distribution_metadata(site_packages)
        packages = package_sources(report, package_data)
        required_names = {"pyserial", "pillow", "numpy", "windows-capture", "opencv-python", "psutil", "sv-ttk"}
        found_names = {normalize(str(p["name"])) for p in packages}
        missing = required_names - found_names
        if missing:
            raise RuntimeError(f"Required packages missing after pip install: {sorted(missing)}")
        write_runtime_docs(stage, artifacts, packages, tcltk_version(extracted_tcltk))

        # Atomic-ish replacement happens only after all downloads and package installation succeed.
        backup = RUNTIME.with_name(RUNTIME.name + ".previous")
        if backup.exists():
            shutil.rmtree(backup)
        if RUNTIME.exists():
            RUNTIME.rename(backup)
        RUNTIME.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(stage), str(RUNTIME))
        if backup.exists():
            shutil.rmtree(backup)

        if args.keep_work:
            kept = ROOT / "tools" / "portable-build-work"
            if kept.exists():
                shutil.rmtree(kept)
            shutil.copytree(extracted_tcltk, kept)
            print(f"Kept MSI extraction at {kept}")

    print(f"Built {RUNTIME}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
