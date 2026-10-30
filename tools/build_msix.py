#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Build an unsigned Microsoft Store MSIX, not a public sideload installer."""
from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
from urllib.parse import unquote
import xml.etree.ElementTree as ET
import zipfile

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[1]
FOUNDATION = 'http://schemas.microsoft.com/appx/manifest/foundation/windows10'
RESTRICTED = 'http://schemas.microsoft.com/appx/manifest/foundation/windows10/restrictedcapabilities'
BLOCKMAP = 'http://schemas.microsoft.com/appx/2010/blockmap'
IDENTITY_KEYS = ('identity_name', 'publisher', 'publisher_display_name', 'display_name')
ROOT_NOTICES = ('LICENSE', 'THIRD_PARTY_NOTICES.md', 'windows-runtime-NOTICES.txt')
PRIVATE_NAMES = {'settings.json', 'diagnostics.json', 'desktop-pet-restore.json',
                 'turmo-device-report.json', 'bridge.log', 'console.log'}


def validate_identity(identity):
    clean = {}
    for key in IDENTITY_KEYS:
        value = identity.get(key)
        if not isinstance(value, str) or not value.strip() or value != value.strip():
            raise ValueError(f'Identity field {key} must be a nonempty trimmed string')
        if any(ord(char) < 32 for char in value):
            raise ValueError(f'Identity field {key} contains control characters')
        clean[key] = value
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.\-]{2,49}', clean['identity_name']):
        raise ValueError('Package Identity Name must be 3–50 supported characters')
    if not clean['publisher'].startswith('CN='):
        raise ValueError('Use the exact Publisher distinguished name from Partner Center')
    return clean


def validate_version(version):
    if not re.fullmatch(r'\d+\.\d+\.\d+\.\d+', version):
        raise ValueError('Store package version must have four numeric components')
    parts = tuple(int(part) for part in version.split('.'))
    if not (1 <= parts[0] <= 65535) or any(part > 65535 for part in parts) or parts[3] != 0:
        raise ValueError('Store version needs first component 1–65535 and fourth component 0')
    return '.'.join(str(part) for part in parts)


def render_manifest(identity, package_version, template=None):
    identity = validate_identity(identity)
    version = validate_version(package_version)
    text = (template or ROOT/'store/AppxManifest.xml.in').read_text(encoding='utf-8')
    values = dict(identity, package_version=version)
    for key, value in values.items():
        text = text.replace('@@'+key.upper()+'@@', html.escape(value, quote=True))
    if re.search(r'@@[A-Z_]+@@', text):
        raise ValueError('Unresolved manifest token')
    parsed = ET.fromstring(text)
    assert parsed.find(f'{{{FOUNDATION}}}Identity').get('Name') == identity['identity_name']
    return text


def make_logo_assets(source, destination):
    """Resize the supplied illustration; do not draw or invent new artwork/text."""
    destination.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as opened:
        image = ImageOps.exif_transpose(opened).convert('RGBA')
        box = image.getchannel('A').getbbox()
        if box is None:
            raise ValueError('The logo source is completely transparent')
        image = image.crop(box)  # Removes only fully transparent outer padding.
        for name, size in [('Square44x44Logo.png', 44), ('Square150x150Logo.png', 150),
                           ('StoreLogo.png', 50)]:
            scaled = ImageOps.contain(image, (size, size), Image.Resampling.LANCZOS)
            canvas = Image.new('RGBA', (size, size), (0, 0, 0, 0))
            canvas.alpha_composite(scaled, ((size-scaled.width)//2, (size-scaled.height)//2))
            canvas.save(destination/name, 'PNG')


def source_commit():
    dirty = subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT, text=True).strip()
    if dirty:
        raise RuntimeError('Commit the reviewed source first so SOURCE.txt points to the exact corresponding source')
    return subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()


def prepare_stage(source_stage, stage, identity, package_version, source_ref, artwork):
    source_stage, stage = source_stage.resolve(), stage.resolve()
    if stage == source_stage or stage in source_stage.parents or source_stage in stage.parents:
        raise RuntimeError('Store stage and source stage must be separate, non-nested directories')
    for name in ('runtime/python.exe', 'runtime/pythonw.exe', 'PetDock.exe', 'LICENSE'):
        if not (source_stage/name).is_file():
            raise RuntimeError(f'Clean runtime stage is incomplete: {name}')
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    ignore = shutil.ignore_patterns('__pycache__', '*.pyc', '*.pyo', '*.log', 'tests',
                                   'pip-install-report.json', 'pip-addons-report.json')
    shutil.copytree(ROOT/'app', stage/'app', ignore=ignore)
    shutil.copytree(source_stage/'runtime', stage/'runtime', ignore=ignore)
    shutil.copy2(source_stage/'PetDock.exe', stage/'PetDock.exe')
    for name in ROOT_NOTICES:
        source = ROOT/name if (ROOT/name).is_file() else ROOT/'tools'/name
        shutil.copy2(source, stage/name)
    (stage/'store-mode.txt').write_text('Microsoft Store MSIX: keep private state outside program files.\n', encoding='utf-8')
    (stage/'AppxManifest.xml').write_text(render_manifest(identity, package_version), encoding='utf-8')
    make_logo_assets(artwork, stage/'Assets')
    (stage/'SOURCE.txt').write_text(
        'Pet Dock is GPL-3.0-or-later. Exact corresponding application and build source:\n'
        f'https://github.com/Kajdep/pet-dock-turmo/tree/{source_ref}\n'
        'Third-party components keep their original notices and source references in runtime/COMPONENTS.json.\n',
        encoding='utf-8')
    (stage/'README-STORE.txt').write_text(
        'Pet Dock — Microsoft Store build\n\n'
        'This payload is intended for Microsoft Store certification and distribution.\n'
        'The Store will sign the package after approval. This unsigned upload is not a sideload installer.\n'
        'Open Pet Dock from Start after Store installation. Windows manages installation and uninstall.\n'
        'The app needs a compatible USB display and an existing floating pet in its host app.\n'
        'Your host app controls pet behavior; this app only mirrors visuals and adds optional local stats.\n'
        'There are no paid in-app features, app accounts, API keys, automatic screenshot uploads or app telemetry.\n'
        'The separate display and host application are not included. A working setup requires supported hardware.\n'
        'The Store package adds read-only install compatibility but still needs real packaged Windows testing.\n', encoding='utf-8')
    paths = sorted(path for path in stage.rglob('*') if path.is_file())
    for path in paths:
        relative = path.relative_to(stage)
        private_app_name = relative.parts[0] != 'runtime' and path.name in PRIVATE_NAMES
        private_folder = relative.parts[0] == 'data' or relative.parts[:2] == ('app', 'data')
        if private_folder or '__pycache__' in relative.parts or private_app_name:
            raise RuntimeError(f'Private or generated state found in Store payload: {relative}')
    manifest = {path.relative_to(stage).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    (stage/'FILE-MANIFEST.json').write_text(json.dumps(manifest, indent=2)+'\n', encoding='utf-8')
    return stage


def verify_msix(package, identity, package_version):
    """Verify a real Appx block map rather than trusting an archive extension."""
    identity = validate_identity(identity)
    version = validate_version(package_version)
    with zipfile.ZipFile(package) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise RuntimeError('Duplicate MSIX archive paths')
        logical_names = {}
        for name in names:
            logical = unquote(name, errors='strict').replace('\\', '/')
            parts = Path(logical).parts
            if logical.startswith('/') or '..' in parts:
                raise RuntimeError('Unsafe package path')
            if logical in logical_names:
                raise RuntimeError('Duplicate decoded MSIX archive paths')
            logical_names[logical] = name
        required = {'AppxManifest.xml', 'AppxBlockMap.xml', '[Content_Types].xml', 'PetDock.exe',
                    'store-mode.txt', 'runtime/pythonw.exe', 'app/app_paths.py'}
        if not required.issubset(set(logical_names)):
            raise RuntimeError('MSIX is missing required payload/footprint files')
        if 'AppxSignature.p7x' in logical_names:
            raise RuntimeError('This recipe is specifically for an unsigned Store-upload package')
        if archive.testzip() is not None:
            raise RuntimeError('Archive CRC validation failed')
        app_manifest = ET.fromstring(archive.read('AppxManifest.xml'))
        actual = app_manifest.find(f'{{{FOUNDATION}}}Identity')
        expected = {'Name': identity['identity_name'], 'Publisher': identity['publisher'],
                    'Version': version, 'ProcessorArchitecture': 'x64'}
        if any(actual.get(key) != value for key, value in expected.items()):
            raise RuntimeError('Package identity does not match Store input exactly')
        block_map = ET.fromstring(archive.read('AppxBlockMap.xml'))
        if block_map.get('HashMethod') != 'http://www.w3.org/2001/04/xmlenc#sha256':
            raise RuntimeError('Store upload needs SHA-256 block hashing')
        mapped = set()
        for entry in block_map.findall(f'{{{BLOCKMAP}}}File'):
            name = entry.get('Name').replace('\\', '/')
            data = archive.read(logical_names[name])
            if len(data) != int(entry.get('Size')):
                raise RuntimeError(f'Block-map file size mismatch: {name}')
            blocks = entry.findall(f'{{{BLOCKMAP}}}Block')
            chunks = [data[offset:offset+65536] for offset in range(0, len(data), 65536)]
            # Empty files are represented by the SDK with either no blocks or one empty block.
            if not data and blocks:
                chunks = [b'']
            if len(blocks) != len(chunks):
                raise RuntimeError(f'Block count mismatch: {name}')
            for block, chunk in zip(blocks, chunks):
                expected_hash = base64.b64encode(hashlib.sha256(chunk).digest()).decode()
                if block.get('Hash') != expected_hash:
                    raise RuntimeError(f'Block hash mismatch: {name}')
            mapped.add(name)
        untracked = set(logical_names) - mapped - {'AppxBlockMap.xml', '[Content_Types].xml'}
        if untracked:
            raise RuntimeError('Payload files not covered by block map: '+', '.join(sorted(untracked)))
    return len(mapped)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--identity-file', type=Path, required=True,
                        help='Private/local JSON with exact Partner Center identity fields')
    parser.add_argument('--source-stage', type=Path, default=ROOT/'build/Pet-Dock-Turmo')
    parser.add_argument('--stage', type=Path, default=ROOT/'build/PetDock-Store')
    parser.add_argument('--output', type=Path, default=ROOT/'dist/store')
    parser.add_argument('--package-version', default='1.0.0.0')
    parser.add_argument('--artwork', type=Path, default=ROOT/'pethero.png')
    parser.add_argument('--makemsix', type=Path)
    parser.add_argument('--makeappx', type=Path)
    parser.add_argument('--prepare-only', action='store_true')
    args = parser.parse_args(argv)
    if not args.stage.resolve().is_relative_to((ROOT/'build').resolve()):
        raise RuntimeError('--stage must be a subdirectory of the project build directory')
    identity = validate_identity(json.loads(args.identity_file.read_text(encoding='utf-8')))
    if identity['publisher'] == 'CN=00000000-0000-0000-0000-000000000000':
        raise RuntimeError('Replace the example identity with values from your actual reserved Store product')
    version = validate_version(args.package_version)
    reference = source_commit()
    print('Preparing clean Store payload with exact identity and corresponding source...', flush=True)
    stage = prepare_stage(args.source_stage, args.stage, identity, version, reference, args.artwork)
    if args.prepare_only:
        print('Prepared unsigned Store payload:', stage, flush=True)
        return 0
    args.output.mkdir(parents=True, exist_ok=True)
    output = args.output/f'Pet-Dock-Store-{version}-x64-unsigned.msix'
    if output.exists():
        output.unlink()
    if args.makeappx:
        command = [str(args.makeappx), 'pack', '/d', str(stage), '/p', str(output), '/o']
    else:
        tool = str(args.makemsix) if args.makemsix else shutil.which('makemsix')
        if not tool:
            raise RuntimeError('Use a real Microsoft makemsix or Windows SDK MakeAppx; --prepare-only does not create an upload package')
        command = [tool, 'pack', '-d', str(stage), '-p', str(output)]
    print('Packing with the Microsoft MSIX tool (schema validation enabled)...', flush=True)
    subprocess.run(command, check=True)
    count = verify_msix(output, identity, version)
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix('.msix.sha256').write_text(f'{digest}  {output.name}\n', encoding='utf-8')
    print(f'Verified {count} mapped payload files, exact identity, CRC and every SHA-256 block hash.', flush=True)
    print('Store upload only:', output.resolve(), flush=True)
    print('SHA-256:', digest, flush=True)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print('MSIX build failed:', error, file=sys.stderr)
        raise SystemExit(1)
