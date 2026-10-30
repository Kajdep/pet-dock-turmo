# SPDX-License-Identifier: GPL-3.0-or-later
"""Local USB identity inspection plus optional known-protocol image benchmark.
No firmware flashing, USB driver replacement, reset, or undocumented probing.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import statistics
import sys
import time
import traceback

from PIL import Image, ImageDraw
from serial.tools import list_ports
from display_io import DisplayConnection

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / 'data'


def _registry_device_info():
    if os.name != 'nt':
        return {'available': False, 'reason': 'Windows registry only'}
    import winreg
    result = []
    fields = ['DeviceDesc', 'FriendlyName', 'HardwareID', 'CompatibleIDs', 'Service',
              'Mfg', 'LocationInformation', 'Capabilities', 'Driver']
    root_path = r'SYSTEM\CurrentControlSet\Enum\USB'
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, root_path) as root:
            index = 0
            while True:
                try:
                    device_key = winreg.EnumKey(root, index)
                except OSError:
                    break
                index += 1
                if not device_key.upper().startswith('VID_1A86&PID_5722'):
                    continue
                with winreg.OpenKey(root, device_key) as device:
                    instance_index = 0
                    while True:
                        try:
                            instance = winreg.EnumKey(device, instance_index)
                        except OSError:
                            break
                        instance_index += 1
                        item = {'registry_id': device_key, 'instance': instance}
                        try:
                            with winreg.OpenKey(device, instance) as key:
                                for name in fields:
                                    try:
                                        item[name] = winreg.QueryValueEx(key, name)[0]
                                    except OSError:
                                        pass
                                try:
                                    with winreg.OpenKey(key, 'Device Parameters') as params:
                                        item['PortName'] = winreg.QueryValueEx(params, 'PortName')[0]
                                except OSError:
                                    pass
                            if item.get('Driver'):
                                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SYSTEM\CurrentControlSet\Control\Class'+chr(92)+item['Driver']) as key:
                                    item['driver_details'] = {}
                                    for name in ['DriverVersion', 'DriverDate', 'ProviderName', 'DriverDesc', 'InfPath', 'MatchingDeviceId']:
                                        try:
                                            item['driver_details'][name] = winreg.QueryValueEx(key, name)[0]
                                        except OSError:
                                            pass
                        except OSError as exc:
                            item['read_error'] = str(exc)
                        result.append(item)
        return result
    except OSError as exc:
        return {'error': str(exc)}


def collect_device_info():
    ports = []
    for p in list_ports.comports():
        if p.vid == 0x1A86 and p.pid == 0x5722:
            ports.append({name: getattr(p, name, None) for name in
                          ['device', 'description', 'hwid', 'vid', 'pid', 'serial_number',
                           'manufacturer', 'product', 'location', 'interface']})
    return {
        'report_version': 1,
        'created_utc': datetime.now(timezone.utc).isoformat(),
        'windows': platform.platform(),
        'python': sys.version,
        'machine_architecture': platform.machine(),
        'matching_ports': ports,
        'registry_and_driver': _registry_device_info(),
        'known_vs_unknown': {
            'usb_id': '1A86:5722 is shared by more than one compatible board/protocol revision.',
            'resolution': '320x480 is the expected 3.5-inch native portrait size; benchmark reports the selected protocol result.',
            'physical_max_fps': 'Unknown: USB descriptors do not report LCD animation throughput.',
            'baud_rate': 'The adapter configures 115200 baud. This does not by itself prove the effective USB CDC throughput.',
            'endpoint_descriptors': 'Not read: the existing Windows CDC driver is retained, with no libusb/Zadig changes.',
        },
        'privacy': 'Local report only. Contains device serial/registry IDs and driver names, but no screenshots or chat contents. Inspect before sharing.',
    }


def benchmark(port, revision, orientation):
    connection = DisplayConnection(port, revision=revision, orientation=orientation,
                                   brightness=60, efficient_updates=True)
    result = {'port': port, 'revision_selected': revision, 'orientation': orientation,
              'note': 'Host serial queue and write timing only—not direct measurement of physical LCD refresh or end-to-end pet latency.'}
    try:
        print(f'Opening {port}, protocol {revision}…', flush=True)
        connection.open()
        width, height = connection.size
        result['protocol_configured_size'] = [width, height]
        driver = connection._driver
        sub_revision = getattr(driver, 'sub_revision', None)
        result['library_hello_classification'] = str(sub_revision)
        result['resolution_note'] = 'Configured by the matched protocol/library; not a measured USB capability or LCD refresh specification.'
        handle = getattr(driver, 'lcd_serial', None)
        result['configured_serial_settings'] = {name: getattr(handle, name, None) for name in
                                                ['baudrate', 'rtscts', 'timeout', 'write_timeout']}
        result['expected_full_rgb565_payload_bytes'] = width*height*2
        base = Image.new('RGB', (width, height), '#10141b')
        samples = []
        started_all = time.monotonic()
        for index in range(10):
            if time.monotonic()-started_all > 45:
                result['ended_early'] = '45-second benchmark budget reached'
                break
            queue_start = time.perf_counter()
            if not connection.wait_ready(timeout=6):
                result['ended_early'] = 'Serial output queue did not drain within 6 seconds'
                break
            queue_ms = (time.perf_counter()-queue_start)*1000
            frame = base.copy()
            draw = ImageDraw.Draw(frame)
            # A small, changing center tile tests the documented partial-image path.
            left, top = width//2-24, height//2-60
            draw.rectangle((left, top, left+47, top+119), fill='#3b9eff' if index % 2 else '#bce2ff')
            send_start = time.perf_counter()
            connection.send(frame)
            send_ms = (time.perf_counter()-send_start)*1000
            drain_start = time.perf_counter()
            drained = connection.wait_ready(timeout=6)
            drain_ms = (time.perf_counter()-drain_start)*1000
            stats = dict(connection.last_send_stats)
            sample = {'index': index, 'queue_before_ms': queue_ms, 'host_send_ms': send_ms,
                      'queue_after_ms': drain_ms, 'queue_drained': drained, **stats}
            samples.append(sample)
            print(f'Update {index+1}/10: {stats["bytes"]} pixel bytes, {stats["regions"]} region(s), '
                  f'write {send_ms:.1f} ms, queue drain {drain_ms:.1f} ms', flush=True)
            if not drained:
                result['ended_early'] = 'Output queue timeout'
                break
        result['samples'] = samples
        deltas = [s for s in samples if not s['full_frame'] and s['bytes'] > 0]
        if deltas:
            result['median_partial_host_write_ms'] = statistics.median(s['host_send_ms'] for s in deltas)
            result['median_partial_with_drain_ms'] = statistics.median(s['host_send_ms']+s['queue_after_ms'] for s in deltas)
        if connection.wait_ready(timeout=6):
            connection.send(base)
            connection.wait_ready(timeout=6)
    except Exception as exc:
        result['error'] = str(exc)
        print('Benchmark error:', exc, flush=True)
    finally:
        try:
            connection.close()
        except Exception as exc:
            result['close_error'] = str(exc)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--benchmark', action='store_true')
    parser.add_argument('--port')
    parser.add_argument('--revision', choices=['A', 'B'], default='A')
    parser.add_argument('--orientation', choices=['Portrait', 'Landscape', 'Reverse portrait', 'Reverse landscape'], default='Portrait')
    args = parser.parse_args()
    DATA.mkdir(exist_ok=True)
    report = collect_device_info()
    print(json.dumps(report, indent=2), flush=True)
    if args.benchmark:
        print('\nClose Pet Dock and Formbig/Turmo before the benchmark. It temporarily replaces the LCD image with a changing test tile; it does not flash/reset the device.', flush=True)
        ports = report['matching_ports']
        port = args.port
        if port is None and len(ports) == 1:
            port = ports[0]['device']
        if not port:
            report['benchmark'] = {'error': 'Select --port COMxx; zero or multiple matching devices were found.'}
        else:
            report['benchmark'] = benchmark(port, args.revision, args.orientation)
    path = DATA / 'turmo-device-report.json'
    path.write_text(json.dumps(report, indent=2, default=str)+'\n', encoding='utf-8')
    print(f'\nReport saved: {path}', flush=True)
    print('Inspect before sharing. Physical LCD max FPS and firmware buffer delay are not directly measured.', flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
