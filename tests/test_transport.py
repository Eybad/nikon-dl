"""Tests de la capa de transporte: framing u32 LE length-prefix."""

import socket
import struct
import unittest

from nikon_dl.transport import (
    ConnectionClosed,
    TransportError,
    hexdump,
    recv_exact,
    recv_frame,
    send_frame,
)


def socketpair():
    a, b = socket.socketpair()
    a.settimeout(5)
    b.settimeout(5)
    return a, b


class TransportFramingTest(unittest.TestCase):
    def test_frame_roundtrip_conserva_tipo_y_body(self):
        a, b = socketpair()
        try:
            body = struct.pack('<IQ', 7, 12345) + b'\x00\xff\x10'
            send_frame(a, 0x09, body)
            ptype, received = recv_frame(b)
            self.assertEqual(ptype, 0x09)
            self.assertEqual(received, body)
        finally:
            a.close()
            b.close()

    def test_frame_sin_body(self):
        a, b = socketpair()
        try:
            send_frame(a, 0x0D)  # ProbeRequest va sin payload
            ptype, received = recv_frame(b)
            self.assertEqual((ptype, received), (0x0D, b''))
        finally:
            a.close()
            b.close()

    def test_longitud_en_prefix_incluye_el_propio_campo(self):
        a, b = socketpair()
        try:
            send_frame(a, 0x06, b'abcd')
            raw = recv_exact(b, 4)
            (total,) = struct.unpack('<I', raw)
            # 4 (campo longitud) + 4 (tipo) + 4 (body)
            self.assertEqual(total, 12)
        finally:
            a.close()
            b.close()

    def test_longitud_absurda_levanta_transport_error(self):
        a, b = socketpair()
        try:
            a.sendall(struct.pack('<I', 10 ** 9))
            with self.assertRaises(TransportError):
                recv_frame(b)
        finally:
            a.close()
            b.close()

    def test_eof_levanta_connection_closed(self):
        a, b = socketpair()
        try:
            a.close()
            with self.assertRaises(ConnectionClosed):
                recv_frame(b)
        finally:
            b.close()


class HexdumpTest(unittest.TestCase):
    def test_formato_offset_hex_ascii(self):
        out = hexdump(b'GET\x00\xff', limit=None)
        self.assertIn('00000000', out)
        self.assertIn('47 45 54 00 ff', out)
        self.assertIn('|GET..|', out)

    def test_recorte_indica_bytes_restantes(self):
        data = bytes(range(256)) * 4  # 1024 bytes
        out = hexdump(data, limit=32)
        self.assertIn('bytes mas', out)


if __name__ == '__main__':
    unittest.main()
