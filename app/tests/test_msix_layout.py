# SPDX-License-Identifier: GPL-3.0-or-later
"""Store contract tests; synthetic ZIPs here are fixtures, not real MSIX deliverables."""
from __future__ import annotations

import base64
import hashlib
import importlib.util
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile

from PIL import Image
import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('build_msix', ROOT/'tools/build_msix.py')
msix = importlib.util.module_from_spec(spec)
spec.loader.exec_module(msix)
FAKE_IDENTITY = {
    'identity_name': 'ExampleTests.PetDock',
    'publisher': 'CN=11111111-1111-1111-1111-111111111111',
    'publisher_display_name': 'Example Tests',
    'display_name': 'Pet Dock',
}


def test_manifest_preserves_identity_and_least_capability():
    identity = dict(FAKE_IDENTITY, display_name='Pet & Dock', publisher_display_name='Tests & Co')
    text = msix.render_manifest(identity, '1.0.0.0')
    document = ET.fromstring(text)
    package = document.find(f'{{{msix.FOUNDATION}}}Identity')
    assert package.attrib == {'Name':identity['identity_name'], 'Publisher':identity['publisher'],
                              'Version':'1.0.0.0','ProcessorArchitecture':'x64'}
    assert document.find(f'{{{msix.FOUNDATION}}}Properties').find(f'{{{msix.FOUNDATION}}}DisplayName').text == 'Pet & Dock'
    capabilities = document.find(f'{{{msix.FOUNDATION}}}Capabilities')
    assert [entry.attrib for entry in capabilities] == [{'Name':'runFullTrust'}]
    application = document.find(f'{{{msix.FOUNDATION}}}Applications')[0]
    assert application.get('Executable') == 'PetDock.exe'
    assert application.get('EntryPoint') == 'Windows.FullTrustApplication'
    assert '@@' not in text
    assert 'internetClient' not in text and 'allowElevation' not in text


@pytest.mark.parametrize('value', ['0.3.0.0','1.0.0.1','1.0.0','65536.0.0.0','1.-1.0.0'])
def test_store_version_constraints(value):
    with pytest.raises(ValueError):
        msix.validate_version(value)


def test_identity_rejects_unsafe_or_missing_values():
    for replacement in [{'identity_name':'../PetDock'}, {'publisher':''}, {'display_name':'bad\x00name'}]:
        with pytest.raises(ValueError):
            msix.validate_identity(dict(FAKE_IDENTITY, **replacement))


def test_logo_resize_preserves_full_artwork_and_required_dimensions(tmp_path):
    image = Image.new('RGBA',(200,100),(80,170,100,255))
    source = tmp_path/'source.png'
    image.save(source)
    destination = tmp_path/'assets'
    msix.make_logo_assets(source,destination)
    for filename,size in [('Square44x44Logo.png',44),('Square150x150Logo.png',150),('StoreLogo.png',50)]:
        with Image.open(destination/filename) as icon:
            assert icon.size == (size,size)
            box=icon.getchannel('A').getbbox()
            assert box[2]-box[0] == size
            assert abs((box[3]-box[1])*2-size) <= 1
    with Image.open(source) as original:
        assert original.size == (200,100)


def fixture_package(path, corrupt_hash=False):
    files = {
        'AppxManifest.xml':msix.render_manifest(FAKE_IDENTITY,'1.0.0.0').encode(),
        'PetDock.exe':b'test fixture, not a runnable PE',
        'store-mode.txt':b'Store mode',
        'runtime/pythonw.exe':b'test runtime fixture',
        'app/app_paths.py':b'# sample data path logic\n',
        'large+data.bin':b'x'*70000,
    }
    tree = ET.Element(f'{{{msix.BLOCKMAP}}}BlockMap', HashMethod='http://www.w3.org/2001/04/xmlenc#sha256')
    for name,data in files.items():
        entry=ET.SubElement(tree,f'{{{msix.BLOCKMAP}}}File',Name=name.replace('/','\\'),Size=str(len(data)))
        for offset in range(0,len(data),65536):
            digest=base64.b64encode(hashlib.sha256(data[offset:offset+65536]).digest()).decode()
            if corrupt_hash and name=='large+data.bin':digest=base64.b64encode(b'0'*32).decode()
            ET.SubElement(entry,f'{{{msix.BLOCKMAP}}}Block',Hash=digest)
    with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED) as archive:
        for name,data in files.items():archive.writestr(name.replace('+','%2B'),data)
        archive.writestr('AppxBlockMap.xml',ET.tostring(tree))
        archive.writestr('[Content_Types].xml',b'<Types/>')


def test_block_map_verifies_every_payload_chunk(tmp_path):
    package=tmp_path/'fixture.zip'
    fixture_package(package)
    assert msix.verify_msix(package,FAKE_IDENTITY,'1.0.0.0')==6


def test_block_map_tampering_is_rejected(tmp_path):
    package=tmp_path/'tampered.zip'
    fixture_package(package,True)
    with pytest.raises(RuntimeError,match='Block hash mismatch'):
        msix.verify_msix(package,FAKE_IDENTITY,'1.0.0.0')


def test_stage_keeps_dependency_data_but_rejects_private_app_state(tmp_path, monkeypatch):
    project=tmp_path/'project'
    (project/'app').mkdir(parents=True)
    (project/'store').mkdir()
    (project/'app/app_paths.py').write_text('# isolated test app\n')
    (project/'store/AppxManifest.xml.in').write_text((ROOT/'store/AppxManifest.xml.in').read_text())
    for name in msix.ROOT_NOTICES:
        (project/name).write_text('test notice\n')
    clean=tmp_path/'clean'
    (clean/'runtime/Lib/site-packages/cv2/data').mkdir(parents=True)
    (clean/'runtime/python.exe').write_bytes(b'test runtime')
    (clean/'runtime/pythonw.exe').write_bytes(b'test runtime')
    (clean/'runtime/Lib/site-packages/cv2/data/__init__.py').write_text('# legitimate package data\n')
    (clean/'PetDock.exe').write_bytes(b'test launcher')
    (clean/'LICENSE').write_text('test license')
    artwork=tmp_path/'art.png'
    Image.new('RGBA',(100,100),(80,170,100,255)).save(artwork)
    monkeypatch.setattr(msix,'ROOT',project)
    stage=tmp_path/'store-stage'
    msix.prepare_stage(clean,stage,FAKE_IDENTITY,'1.0.0.0','a'*40,artwork)
    assert (stage/'runtime/Lib/site-packages/cv2/data/__init__.py').is_file()
    assert (stage/'store-mode.txt').is_file()
    assert not (stage/'installed-mode.txt').exists()
    assert not (clean/'store-mode.txt').exists()
    (project/'app/settings.json').write_text('{"private":true}')
    with pytest.raises(RuntimeError,match='Private or generated state'):
        msix.prepare_stage(clean,stage,FAKE_IDENTITY,'1.0.0.0','a'*40,artwork)
