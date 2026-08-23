#!/usr/bin/env python3
"""Diagnostico de red para nikon-dl: evidencia por capas, sin root.

Responde, en orden:
  1. ¿Por donde saldria el trafico hacia la camara? (source IP que elige
     el kernel, sin enviar paquetes: truco UDP-connect)
  2. ¿La camara responde a nivel enlace (entrada ARP completa)?
  3. ¿Que pasa exactamente en el TCP connect? (OK / REFUSED / TIMEOUT)

Con esas tres respuestas la matriz de veredictos dice donde se corta la
cadena y que accion corresponde. Uso:

    python scripts/diag_net.py [--ip 192.168.1.1] [--port 15740]
"""

import argparse
import socket
import sys
import time

sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[1]))

from nikon_dl.netdetect import parse_default_gateways  # noqa: E402


def source_ip_for(dest_ip):
    """IP de origen que el kernel usaria para llegar a dest_ip.

    Un connect() UDP no envia paquetes: solo hace que el kernel resuelva
    la ruta y elija interfaz/IP de origen.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect((dest_ip, 9))
        return sock.getsockname()[0]
    except OSError as exc:
        return None
    finally:
        sock.close()


def arp_entry(ip):
    """Entrada ARP de /proc/net/arp tras intentar trafico. flags 0x2 = completa."""
    try:
        with open('/proc/net/arp', encoding='ascii') as fh:
            rows = [line.split() for line in fh.readlines()[1:]]
    except OSError:
        return None
    for parts in rows:
        if parts and parts[0] == ip:
            return {'mac': parts[3], 'flags': parts[2], 'iface': parts[5]}
    return None


def tcp_probe(ip, port, timeout):
    started = time.monotonic()
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return 'OK', time.monotonic() - started
    except ConnectionRefusedError:
        return 'REFUSED', time.monotonic() - started
    except TimeoutError:
        return 'TIMEOUT', time.monotonic() - started
    except OSError as exc:
        return 'ERROR(%s)' % exc, time.monotonic() - started


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ip', default='192.168.1.1')
    parser.add_argument('--port', type=int, default=15740)
    parser.add_argument('--timeout', type=float, default=5.0)
    args = parser.parse_args()

    net = '.'.join(args.ip.split('.')[:3])

    print('== Capa 1: ruta hacia %s ==' % args.ip)
    src = source_ip_for(args.ip)
    if src is None:
        print('  el kernel NO tiene ruta hacia %s (sin Wi-Fi asociado?)' % args.ip)
        on_camera_net = False
    else:
        on_camera_net = src.startswith(net + '.')
        print('  source IP elegida por el kernel: %s' % src)
        print('  => %s' % ('sale por la red de la camara: BIEN'
                            if on_camera_net else
                            'NO sale por la red %s.x (datos moviles u otro Wi-Fi?)' % net))
    try:
        with open('/proc/net/route', encoding='ascii') as fh:
            gateways = parse_default_gateways(fh.read())
    except OSError:
        gateways = []
    if gateways:
        print('  gateways default: %s'
              % ', '.join('%s via %s' % (i, g) for i, g in gateways))
        print('  (en la S3700 la camara suele ser el gateway: proba --ip %s)'
              % gateways[0][1])

    print('== Capa 3: TCP connect %s:%d (timeout %.0fs) ==' % (args.ip, args.port, args.timeout))
    result, elapsed = tcp_probe(args.ip, args.port, args.timeout)
    print('  resultado: %s (%.2fs)' % (result, elapsed))

    print('== Capa 2: ARP de %s ==' % args.ip)
    entry = arp_entry(args.ip)
    if entry is None:
        print('  sin entrada ARP (la camara nunca respondio a nivel enlace)')
        link_ok = False
    else:
        link_ok = entry['flags'] == '0x2'
        print('  mac=%s flags=%s iface=%s => %s'
              % (entry['mac'], entry['flags'], entry['iface'],
                 'responde a nivel enlace' if link_ok else 'resolucion INCOMPLETA'))

    print('== Veredicto ==')
    if result == 'OK':
        print('  Red lista. Correr: python scripts/smoke_test.py')
        return 0
    if src is not None and not on_camera_net:
        print('  ACCION: el telefono no esta usando la red de la camara.')
        print('  - Verifica en Ajustes>Wi-Fi que el SSID activo sea Nikon_...')
        print('  - Activa modo avion y luego enciende SOLO Wi-Fi (mata el')
        print('    ruteo por datos moviles) y reconecta al AP.')
    elif result == 'REFUSED':
        print('  ACCION: la camara esta en la red pero nadie escucha en el puerto.')
        print('  - Re-entra al modo Wi-Fi de la camara (pantalla esperando')
        print('    conexion) y reintenta enseguida.')
    elif not link_ok:
        print('  ACCION: sin respuesta a nivel enlace (ni ARP contesta).')
        print('  - El Wi-Fi de la camara probablemente se apago por inactividad')
        print('    (~2-3 min) o la camara salio del modo conexion.')
        print('  - Reactiva el Wi-Fi EN LA CAMARA y corre este diagnostico')
        print('    dentro del primer minuto.')
    else:
        print('  ACCION: hay enlace pero los paquetes TCP se pierden.')
        print('  - VPN/firewall residual (Rethink u otro): reinicia el')
        print('    telefono si persiste.')
        print('  - Camara colgada tras un intento previo: apagala y prendela.')

    print('\nPega esta salida completa si necesitas mas ayuda.')
    return 1


if __name__ == '__main__':
    sys.exit(main())
