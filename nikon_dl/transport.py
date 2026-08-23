"""Capa de transporte PTP/IP: frames con prefijo de longitud u32 little-endian.

Formato de frame (CIPA DC-007):
    u32 total_len  (incluye los 4 bytes del propio campo)
    u32 payload_type
    ...body...

El modo debug (--debug) vuelca cada frame en hexadecimal; se limita la
cantidad de bytes volcados por frame para no inundar la terminal con
payloads de 1 MB.
"""

import struct

HEADER_LEN = 4
MAX_FRAME_LEN = 64 * 1024 * 1024 + HEADER_LEN  # sanity cap anti-desincronizacion

# Sink de debug configurable desde el CLI: callable(direccion, ptype, body)
_debug_sink = None


def configure_debug(sink):
    """Registra un callback de debug. None desactiva."""
    global _debug_sink
    _debug_sink = sink


class TransportError(Exception):
    """Error de framing / protocolo a nivel transporte."""


class ConnectionClosed(TransportError):
    """El par cerro la conexion o el socket devolvio EOF."""


def recv_exact(sock, n):
    """Lee exactamente n bytes o levanta ConnectionClosed."""
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionClosed('conexion cerrada por el par (esperaba %d bytes, tengo %d)'
                                   % (n, len(buf)))
        buf += chunk
    return bytes(buf)


def send_frame(sock, payload_type, body=b''):
    total = HEADER_LEN + 4 + len(body)
    sock.sendall(struct.pack('<II', total, payload_type) + body)
    if _debug_sink:
        _debug_sink('>>', payload_type, body)


def recv_frame(sock):
    """Lee un frame completo. Devuelve (payload_type, body_sin_tipo)."""
    (total,) = struct.unpack('<I', recv_exact(sock, HEADER_LEN))
    if total < HEADER_LEN + 4 or total > MAX_FRAME_LEN:
        raise TransportError('longitud de frame invalida: %d (posible desincronizacion)' % total)
    rest = recv_exact(sock, total - HEADER_LEN)
    (ptype,) = struct.unpack('<I', rest[:4])
    body = rest[4:]
    if _debug_sink:
        _debug_sink('<<', ptype, body)
    return ptype, body


def hexdump(data, limit=512):
    """Vuelca hex+ascii estilo tcpdump. limit=None = sin recorte."""
    original_len = len(data)
    if limit is not None:
        data = data[:limit]
    lines = []
    for off in range(0, len(data), 16):
        row = data[off:off + 16]
        hx = ' '.join('%02x' % b for b in row)
        asc = ''.join(chr(b) if 32 <= b < 127 else '.' for b in row)
        lines.append('%08x  %-47s  |%s|' % (off, hx, asc))
    out = '\n'.join(lines)
    if limit is not None and original_len > limit:
        out += '\n... (%d bytes mas, recortado)' % (original_len - limit)
    return out
