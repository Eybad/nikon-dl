"""Deteccion de la IP de la camara desde la red activa, sin root.

Hallazgo de campo: la Coolpix S3700 sirve su AP en la subred 192.168.0.x
(gateway/camara en 192.168.0.1), a diferencia de los Nikon DSLR y
adaptadores WU-x documentados por Airnef que usaban 192.168.1.1.

Estrategia:
  1. source IP que el kernel eligiria para salir (connect UDP, sin paquetes)
     -> prefijo de subred de la red activa.
  2. gateway default de esa subred segun /proc/net/route -> suele ser la
     propia camara.
  3. fallback: <prefijo>.1.
"""

import socket


def source_ip_for(dest_ip):
    """IP de origen que el kernel usaria para llegar a dest_ip.

    Un connect() UDP no envia paquetes: solo resuelve ruta e interfaz.
    Devuelve None si no hay ruta.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect((dest_ip, 9))
        return sock.getsockname()[0]
    except OSError:
        return None
    finally:
        sock.close()


def parse_default_gateways(route_table_text):
    """Parsea /proc/net/route -> [(iface, gateway)] solo rutas default.

    Los campos van en hex little-endian: gateway '0100A8C0' = 192.168.0.1.
    """
    gateways = []
    for line in route_table_text.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 4:
            continue
        iface, dest_hex, gw_hex = parts[0], parts[1], parts[2]
        if dest_hex == '00000000' and gw_hex != '00000000':
            try:
                raw = bytes.fromhex(gw_hex)
                gateway = '.'.join(str(byte) for byte in reversed(raw))
            except ValueError:
                continue
            gateways.append((iface, gateway))
    return gateways


def _read_routes():
    try:
        with open('/proc/net/route', encoding='ascii') as fh:
            return fh.read()
    except OSError:
        return ''


def detect_camera_ip(route_table=None, probe_dest='192.168.1.1'):
    """IP mas probable de la camara en la red activa; None si no hay red."""
    src = source_ip_for(probe_dest)
    if src is None:
        return None
    prefix = src.rsplit('.', 1)[0]
    table = route_table if route_table is not None else _read_routes()
    for _iface, gateway in parse_default_gateways(table):
        if gateway.startswith(prefix + '.'):
            return gateway
    return prefix + '.1'
