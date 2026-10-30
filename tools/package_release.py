#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Package the source and optional Windows runtime into a clean portable ZIP."""
from __future__ import annotations
import argparse
import ast
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def version():
    tree = ast.parse((ROOT/'app/pet_bridge.py').read_text(encoding='utf-8'))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'VERSION' for t in node.targets):
            return str(ast.literal_eval(node.value))
    raise RuntimeError('Application VERSION is missing')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'dist')
    args = parser.parse_args()
    runtime = next((p for p in [ROOT/'portable/runtime', ROOT/'runtime'] if (p/'python.exe').is_file()), None)
    if runtime is None:
        raise RuntimeError('Build the Windows runtime first: python tools/build_portable.py')
    app_version = version()
    stage = ROOT/'build/Pet-Dock-Turmo'
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    ignore = shutil.ignore_patterns('__pycache__', '*.pyc', '*.pyo', '*.log', 'tests', 'pip-install-report.json', 'pip-addons-report.json')
    shutil.copytree(ROOT/'app', stage/'app', ignore=ignore)
    shutil.copytree(runtime, stage/'runtime', ignore=ignore)
    for name in ['Start.cmd', 'Check-runtime.cmd', 'Probe-Turmo.cmd', 'Restore-Desktop-Pet.cmd',
                 'README.md', 'START-HERE.txt', 'LICENSE', 'THIRD_PARTY_NOTICES.md', 'requirements.txt']:
        shutil.copy2(ROOT/name, stage/name)
    shutil.copy2(ROOT/'tools/windows-runtime-NOTICES.txt', stage/'windows-runtime-NOTICES.txt')
    # Keep exact dependency source/license/hash provenance, not host pip reports.
    component_file = stage/'runtime/COMPONENTS.json'
    if not component_file.is_file():
        raise RuntimeError('Runtime source/license provenance COMPONENTS.json is missing')
    components = json.loads(component_file.read_text(encoding='utf-8'))
    safe = {}
    for key in ['python', 'tcltk']:
        safe[key] = {name: value for name, value in components[key].items()
                     if name in ['name', 'version', 'architecture', 'source_url', 'sha256', 'license']}
    safe['python_packages'] = [{name: value for name, value in item.items()
                               if name in ['name', 'version', 'license', 'homepage', 'source_url', 'sha256']}
                              for item in components.get('python_packages', [])]
    component_file.write_text(json.dumps(safe, indent=2)+'\n', encoding='utf-8')
    (stage/'runtime/COMPONENTS.md').write_text(
        '# Runtime provenance\n\nSee COMPONENTS.json for exact component versions, public source URLs, declared licenses, and download hashes.\n'
        'Original component licenses remain in the runtime and installed distribution directories.\n'
        'No host pip environment reports are included.\n', encoding='utf-8')
    (stage/'SOURCE.txt').write_text(
        'Corresponding GPL source for this release:\n'
        f'https://github.com/Kajdep/pet-dock-turmo/tree/v{app_version}\n'
        'The source repository includes application code, vendored notices, tests, and build tools.\n', encoding='utf-8')
    for path in stage.glob('*.cmd'):
        text = path.read_text(encoding='utf-8')
        path.write_bytes(text.replace('\r\n','\n').replace('\n','\r\n').encode('ascii'))
    subprocess.run([sys.executable, str(ROOT/'tools/build_installer.py'), '--launcher-only',
                    '--stage', str(stage), '--output', str(args.output)], check=True)
    paths = sorted(p for p in stage.rglob('*') if p.is_file())
    manifest = {p.relative_to(stage).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    (stage/'FILE-MANIFEST.json').write_text(json.dumps(manifest, indent=2)+'\n', encoding='utf-8')
    args.output.mkdir(parents=True, exist_ok=True)
    output = args.output/f'Pet-Dock-Turmo-Windows-v{app_version}.zip'
    files = sorted(p for p in stage.rglob('*') if p.is_file())
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for i, path in enumerate(files):
            archive.write(path, 'Pet-Dock-Turmo/'+path.relative_to(stage).as_posix())
            if i % 500 == 0:
                print(f'Packing {i}/{len(files)} files...', flush=True)
    with zipfile.ZipFile(output) as archive:
        assert archive.testzip() is None
        for name in archive.namelist():
            assert not name.startswith('Pet-Dock-Turmo/data/') and '__pycache__' not in name, name
            assert not name.endswith(('bridge.log', 'settings.json', 'diagnostics.json', 'turmo-device-report.json')), name
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix('.zip.sha256').write_text(f'{digest}  {output.name}\n', encoding='utf-8')
    print(f'Complete: {output.resolve()} ({output.stat().st_size:,} bytes)', flush=True)
    print(f'SHA-256: {digest}', flush=True)


if __name__ == '__main__':
    main()
