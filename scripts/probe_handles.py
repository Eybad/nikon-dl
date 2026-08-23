#!/usr/bin/env python3
"""Sonda de enumeracion: investiga por que no aparecen videos en list.

Prueba, contra la camara real y sin modificar nada:
  1. GetObjectHandles con parent=0 vs parent=0xFFFFFFFF (jerarquia)
  2. GetObjectHandles con format code especifico (AVI/MPEG/QT/JPEG/undefined)
  3. El mecanismo vendor de WMU/Airnef: GetTransferList (0x9408), que la
     S3700 anuncia en su OperationsSupported

Uso:
    python scripts/probe_handles.py [--ip IP]
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nikon_dl import ptp                                    # noqa: E402
from nikon_dl.netdetect import detect_camera_ip             # noqa: E402
from nikon_dl.objects import get_object_info_retry, parse_u32_array, list_storages  # noqa: E402
from nikon_dl.session import CameraSession                  # noqa: E402

OP_GetTransferList = 0x9408   # vendor Nikon (mtpdef.py de airnef)

FORMATS_A_PROBAR = [
    ('todos (0x0000)', 0x0000),
    ('undefined (0x3000)', 0x3000),
    ('AVI (0x300A)', ptp.FMT_AVI),
    ('MPEG (0x300B)', ptp.FMT_MPEG),
    ('QuickTime (0x300D)', ptp.FMT_QT),
    ('JPEG (0x3801)', ptp.FMT_JPEG_EXIF),
]


def sample_info(session, handles, max_items=3):
    lines = []
    for handle in handles[:max_items]:
        try:
            obj = get_object_info_retry(session, handle, max_wait=1.0)
            lines.append('      0x%08x fmt=0x%04x %10d  %s'
                         % (obj.handle, obj.format_code, obj.size,
                            obj.filename))
        except Exception as exc:  # noqa: BLE001 - diagnostico
            lines.append('      0x%08x <info fallo: %s>' % (handle, exc))
    if len(handles) > max_items:
        lines.append('      ... y %d mas' % (len(handles) - max_items))
    return lines


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ip', default=None)
    args = parser.parse_args()

    ip = args.ip or detect_camera_ip() or '192.168.1.1'
    session = CameraSession(ip=ip, rw_timeout=10.0)
    try:
        session.connect()
        print('Conectado a %s (sesion 0x%X)' % (ip, session.session_id))
        storages = list_storages(session)
        print('storages: %s' % ', '.join('0x%08X' % s for s in storages))

        for storage_id in storages:
            print('\n===== storage 0x%08X =====' % storage_id)
            for parent_label, parent in (('parent=0', 0x00000000),
                                         ('parent=0xFFFFFFFF', 0xFFFFFFFF)):
                for label, fmt in FORMATS_A_PROBAR:
                    res = session.transaction(ptp.OP_GetObjectHandles,
                                              [storage_id, fmt, parent])
                    if not res.ok():
                        print('  %-18s %-20s -> %s'
                              % (parent_label, label,
                                 ptp.response_name(res.code)))
                        continue
                    handles, _ = parse_u32_array(res.data, 0)
                    print('  %-18s %-20s -> %d objetos'
                          % (parent_label, label, len(handles)))
                    for line in sample_info(session, handles):
                        print(line)

            print('\n  --- GetTransferList (vendor 0x9408, mecanismo WMU) ---')
            res = session.transaction(OP_GetTransferList)
            if res.ok():
                handles, _ = parse_u32_array(res.data, 0)
                print('  OK: %d handles marcados para transferencia'
                      % len(handles))
                for line in sample_info(session, handles):
                    print(line)
            else:
                print('  rechazado con %s (normal si no marcaste nada '
                      'en la camara)' % ptp.response_name(res.code))
    finally:
        session.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
