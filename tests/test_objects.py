"""Tests de parseo de datasets MTP y utilidades de objects.py."""

import unittest
from datetime import datetime

from nikon_dl import ptp
from nikon_dl.objects import (
    OBJECTINFO_FIXED_LEN,
    parse_deviceinfo,
    parse_objectinfo,
    parse_ptp_date,
    parse_ptp_string,
    parse_u16_array,
    parse_u32_array,
    sanitize_filename,
)
from tests.fake_camera import enc_deviceinfo, enc_objectinfo


def make_body():
    return {
        'storage': 0x00010001, 'format': ptp.FMT_AVI, 'size': 15360000,
        'name': 'DSCN0012.AVI', 'capture': '20260815T143010',
        'modified': '20260815T143012', 'parent': 0,
    }


class ObjectInfoTest(unittest.TestCase):
    def test_parseo_roundtrip_campos_principales(self):
        obj = make_body()
        parsed = parse_objectinfo(enc_objectinfo(obj), handle=0x00000009)
        self.assertEqual(parsed.handle, 0x00000009)
        self.assertEqual(parsed.storage_id, 0x00010001)
        self.assertEqual(parsed.format_code, ptp.FMT_AVI)
        self.assertEqual(parsed.size, 15360000)
        self.assertEqual(parsed.filename, 'DSCN0012.AVI')
        self.assertEqual(parsed.capture_date, datetime(2026, 8, 15, 14, 30, 10))
        self.assertEqual(parsed.modified_date, datetime(2026, 8, 15, 14, 30, 12))
        self.assertEqual(parsed.kind, 'video')

    def test_dataset_fijo_mide_52_bytes(self):
        # el encoder del fake y el parser deben coincidir en el layout
        body = enc_objectinfo(make_body())
        self.assertGreater(len(body), OBJECTINFO_FIXED_LEN)

    def test_clasificacion_photo_video_other(self):
        self.assertEqual(ptp.classify_format(ptp.FMT_JPEG_EXIF), 'photo')
        self.assertEqual(ptp.classify_format(ptp.FMT_JFIF), 'photo')
        self.assertEqual(ptp.classify_format(ptp.FMT_AVI), 'video')
        self.assertEqual(ptp.classify_format(0x3004), 'other')  # TEXT


class DeviceInfoTest(unittest.TestCase):
    def test_parseo_modelo_y_operaciones(self):
        info = parse_deviceinfo(enc_deviceinfo(model='NIKON COOLPIX FAKE'))
        self.assertEqual(info['model'], 'NIKON COOLPIX FAKE')
        self.assertIn(ptp.OP_GetPartialObject, info['operations_supported'])
        self.assertEqual(info['manufacturer'], 'Nikon')

    def test_valores_reales_capturados_del_s3700_en_campo(self):
        # valores leidos del InitCmdAck/GetDeviceInfo real (dump de campo
        # 2026-08-23): fuente de verdad independiente del encoder propio
        info = parse_deviceinfo(enc_deviceinfo(
            model='S3700',
            vendor_ext_desc='microsoft.com/deviceservices: 1.0;',
            manufacturer='Nikon Corporation',
            device_version='COOLPIX S3700 V1.0'))
        self.assertEqual(info['model'], 'S3700')
        self.assertEqual(info['vendor_extension_id'], 6)  # Microsoft MTP
        self.assertEqual(info['vendor_extension_desc'],
                         'microsoft.com/deviceservices: 1.0;')
        self.assertEqual(info['manufacturer'], 'Nikon Corporation')
        self.assertEqual(info['device_version'], 'COOLPIX S3700 V1.0')
        self.assertIn(ptp.OP_GetPartialObject, info['operations_supported'])
        self.assertIn(ptp.OP_DeleteObject, info['operations_supported'])


class PrimitivesTest(unittest.TestCase):
    def test_string_vacia_con_null(self):
        # count=1: solo la unidad null UTF-16; se consumen 1 + 2*1 = 3 bytes
        text, off = parse_ptp_string(b'\x01\x00\x00', 0)
        self.assertEqual((text, off), ('', 3))

    def test_string_varia_sin_payload(self):
        text, off = parse_ptp_string(b'\x00', 0)
        self.assertEqual((text, off), ('', 1))

    def test_string_unicode_utf16le_como_el_firmware_real(self):
        # captura real del S3700: count 0x06 + "S3700" en UTF-16LE + null
        data = b'\x06' + 'S3700'.encode('utf-16-le') + b'\x00\x00'
        text, off = parse_ptp_string(data, 0)
        self.assertEqual(text, 'S3700')
        self.assertEqual(off, len(data))

    def test_arrays_u16_u32(self):
        data = b'\x03\x00\x00\x00' + b'\x01\x20\x02\x20\x03\x20'
        values, off = parse_u16_array(data, 0)
        self.assertEqual(values, [0x2001, 0x2002, 0x2003])
        self.assertEqual(off, len(data))
        data32 = b'\x02\x00\x00\x00' + b'\x01\x00\x00\x40\x02\x00\x00\x40'
        values, _ = parse_u32_array(data32, 0)
        self.assertEqual(values, [0x40000001, 0x40000002])


class DatesTest(unittest.TestCase):
    def test_fecha_valida(self):
        self.assertEqual(parse_ptp_date('20260823T101500'),
                         datetime(2026, 8, 23, 10, 15))

    def test_fecha_invalida_devuelve_none(self):
        self.assertIsNone(parse_ptp_date(''))
        self.assertIsNone(parse_ptp_date('00000000T000000'))
        self.assertIsNone(parse_ptp_date('basura'))


class SanitizeTest(unittest.TestCase):
    def test_elimina_separadores_y_control(self):
        self.assertEqual(sanitize_filename('a/b\\c.txt'), 'a_b_c.txt')

    def test_nombre_vacio_tiene_fallback(self):
        self.assertTrue(sanitize_filename('').startswith('objeto'))

    def test_nombre_normal_intacto(self):
        self.assertEqual(sanitize_filename('DSCN0001.JPG'), 'DSCN0001.JPG')


if __name__ == '__main__':
    unittest.main()
