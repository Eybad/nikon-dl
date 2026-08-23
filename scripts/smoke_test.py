#!/usr/bin/env python3
"""Smoke test graduado contra la camara real.

Valida por etapas, reportando la ultima alcanzada si algo falla:
  A. TCP connect a IP:15740
  B. INIT_CMD_REQ -> ACK con session ID
  C. Sesion completa: eventos + probe + GetDeviceInfo + OpenSession
     (+ fallback de session ID) + storages + conteo de objetos

Cada etapa que pasa se imprime; la primera que falla incluye una pista.
Exit code 0 solo si todo pasa. Correr con la camara conectada al Wi-Fi:

    python scripts/smoke_test.py [--ip 192.168.1.1]
"""

import argparse
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nikon_dl import ptp                                    # noqa: E402
from nikon_dl.netdetect import detect_camera_ip             # noqa: E402
from nikon_dl.objects import list_objects, list_storages, parse_deviceinfo  # noqa: E402
from nikon_dl.session import CameraSession                  # noqa: E402


def fail(stage, exc, hint):
    print('FALLA [%s]: %s' % (stage, exc))
    if hint:
        print('PISTA: %s' % hint)
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ip', default=None,
                        help='IP de la camara (default: autodetectada)')
    parser.add_argument('--rw-timeout', type=float, default=5.0,
                        help='timeout lectura/escritura en s (default 5; '
                             'subilo a 15 si el INIT tarda en responder)')
    parser.add_argument('--debug', action='store_true',
                        help='vuelca cada frame MTP-IP en hexadecimal')
    args = parser.parse_args()

    ip = args.ip or detect_camera_ip() or '192.168.1.1'
    if args.ip is None:
        print('IP autodetectada: %s (--ip para forzar otra)' % ip)

    if args.debug:
        from nikon_dl.transport import configure_debug, hexdump
        configure_debug(lambda d, t, b: sys.stderr.write(
            '--- %s %s (%d bytes)\n%s\n'
            % (d, ptp.CONTAINER_NAMES.get(t, '0x%02x' % t), len(b), hexdump(b))))

    session = CameraSession(ip=ip, rw_timeout=args.rw_timeout)
    ok = True

    # --- Etapa A: TCP ---
    try:
        sock = session._open_socket()
        session._cmd_sock = sock
        print('PASA [A] TCP connect a %s:%d' % (ip, session.port))
    except OSError as exc:
        return fail('A/TCP', exc,
                    'el telefono no esta conectado al AP Wi-Fi de la camara '
                    '(SSID Nikon_...) o Android cambio a datos moviles')

    # --- Etapa B: INIT_CMD_REQ ---
    try:
        session.session_id = session._init_command_channel()
        print('PASA [B] INIT_CMD_REQ/ACK | session ID devuelto: 0x%08X'
              % session.session_id)
    except Exception as exc:  # noqa: BLE001 - diagnostico, re-raise innecesario
        session.close(force=True)
        return fail('B/INIT', exc,
                    'si el TCP paso pero no hay ACK: cicla el Wi-Fi de la '
                    'camara o apagala y prendela (estado colgado conocido)')

    # --- Etapa C: sesion completa ---
    try:
        session._evt_sock = session._open_socket()
        session._init_event_channel()
        session._probe()
        print('PASA [C1] eventos + probe')
        res = session.transaction(ptp.OP_GetDeviceInfo)
        res.expect_ok()
        info = parse_deviceinfo(res.data)
        print('PASA [C2] GetDeviceInfo | modelo: %s'
              % (info.get('model') or '(desconocido)'))
        requested = session.session_id
        session._open_session(requested)
        if session.session_id != requested:
            print('PASA [C3] OpenSession con FALLBACK a session ID 0x1 '
                  '(la camara rechazo 0x%08X)' % requested)
        else:
            print('PASA [C3] OpenSession(0x%08X)' % session.session_id)
        storages = list_storages(session)
        print('PASA [C4] storages: %s'
              % ', '.join('0x%08X' % s for s in storages))
        total = 0
        for storage_id in storages:
            total += len(list_objects(session, storage_id))
        print('PASA [C5] objetos visibles: %d' % total)
    except Exception as exc:  # noqa: BLE001
        ok = False
        hint = ('revisar --debug para ver el frame exacto donde corto; '
                'si OpenSession rechaza, el fallback ya se probo automaticamente')
        fail('C/SESION', exc, hint)
    finally:
        session.close()

    if ok:
        print('\nSMOKE TEST COMPLETO: el S3700 habla MTP-IP. Listo para usar '
              '`python -m nikon_dl list`.')
        return 0
    print('\nSmoke test incompleto: revisar la etapa FALLA de arriba.')
    return 1


if __name__ == '__main__':
    sys.exit(main())
