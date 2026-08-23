"""Tests de deteccion automatica de la IP de la camara."""

import unittest
from unittest import mock

from nikon_dl.netdetect import (
    detect_camera_ip,
    parse_default_gateways,
    source_ip_for,
)

ROUTE_TABLE = '''Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\tMTU\tWindow\tIRTT
wlan0\t00000000\t0100A8C0\t0003\t0\t0\t0\t00000000\t0\t0\t0
wlan0\t00A800C0\t00000000\t0001\t0\t0\t0\t0000FFFF\t0\t0\t0
rmnet0\t00000000\tFE00A8C0\t0003\t0\t0\t1024\t00000000\t0\t0\t0
'''


class ParseRoutesTest(unittest.TestCase):
    def test_extrae_solo_rutas_default_con_gateway(self):
        result = parse_default_gateways(ROUTE_TABLE)
        # wlan0 default via 192.168.0.1 (hex LE 0100A8C0); rmnet0 tambien es
        # default pero su gateway se reporta igual (la eleccion por prefijo
        # es trabajo de detect_camera_ip)
        self.assertIn(('wlan0', '192.168.0.1'), result)
        # FE00A8C0 LE -> C0.A8.00.FE
        self.assertIn(('rmnet0', '192.168.0.254'), result)
        # la ruta de subred (no default) no aparece
        self.assertEqual(len(result), 2)

    def test_tabla_vacia_o_malformada(self):
        self.assertEqual(parse_default_gateways(''), [])
        self.assertEqual(parse_default_gateways('Iface\tDestination\n'), [])


class DetectCameraIpTest(unittest.TestCase):
    def test_gateway_de_la_interfaz_del_prefijo_gana(self):
        with mock.patch('nikon_dl.netdetect.source_ip_for',
                        return_value='192.168.0.33'):
            self.assertEqual(
                detect_camera_ip(route_table=ROUTE_TABLE), '192.168.0.1')

    def test_sin_gateway_en_prefijo_cae_a_punto_uno(self):
        table = ('Iface\tDestination\tGateway\n'
                 'wlan0\t00000000\tFE00A8C0\n')  # default via otra red
        with mock.patch('nikon_dl.netdetect.source_ip_for',
                        return_value='10.42.0.7'):
            self.assertEqual(detect_camera_ip(route_table=table), '10.42.0.1')

    def test_sin_red_devuelve_none(self):
        with mock.patch('nikon_dl.netdetect.source_ip_for', return_value=None):
            self.assertIsNone(detect_camera_ip(route_table=''))

    def test_source_ip_for_localhost_no_explota(self):
        # contra el loopback siempre hay ruta; solo verifica tipo
        result = source_ip_for('127.0.0.1')
        self.assertTrue(result is None or result.startswith('127.'))


if __name__ == '__main__':
    unittest.main()
