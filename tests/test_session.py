"""Tests de integracion de CameraSession contra el simulador de camara."""

import unittest

from nikon_dl import ptp
from nikon_dl.objects import list_objects, parse_deviceinfo
from nikon_dl.session import (
    MIN_CLOSE_GAP,
    CameraConnectionError,
    CameraSession,
)
from tests import fake_camera
from tests.fake_camera import FakeCamera


def make_fake(test, **kwargs):
    fake = FakeCamera(**kwargs)
    fake.start()
    test.addCleanup(fake.stop)
    return fake


def make_session(fake, **kwargs):
    defaults = dict(ip='127.0.0.1', port=fake.port, connect_timeout=2.0,
                    rw_timeout=2.0)
    defaults.update(kwargs)
    return CameraSession(**defaults)


class HandshakeTest(unittest.TestCase):
    def test_handshake_completo_devuelve_session_id_y_modelo(self):
        fake = make_fake(self)
        session = make_session(fake).connect()
        self.addCleanup(session.close)
        self.assertEqual(session.session_id, 0x11223344)
        info = parse_deviceinfo(session.device_info_raw)
        self.assertEqual(info['model'], 'NIKON COOLPIX FAKE')
        # la camara recibio el probe antes del OpenSession
        self.assertIn(ptp.OP_OpenSession, fake.received_opcodes)

    def test_init_fail_da_error_con_pista_de_recuperacion(self):
        fake = make_fake(self, fail_init=True)
        with self.assertRaises(CameraConnectionError) as ctx:
            make_session(fake).connect()
        self.assertIn('cicla', str(ctx.exception))

    def test_sin_probe_response_falla_el_handshake(self):
        fake = make_fake(self, skip_probe_response=True)
        with self.assertRaises(CameraConnectionError):
            make_session(fake).connect()

    def test_fallback_session_id_a_1_cuando_la_camara_rechaza(self):
        fake = make_fake(self, reject_returned_session_id=True)
        session = make_session(fake).connect()
        self.addCleanup(session.close)
        self.assertEqual(session.session_id, 1)
        self.assertTrue(fake.fallback_session_used)


class TransaccionesTest(unittest.TestCase):
    def test_getpartialobject_devuelve_slice_correcto(self):
        fake = make_fake(self)
        fake.add_object(9, 'DSCN0001.JPG', 1024 * 1024 + 777)
        session = make_session(fake).connect()
        self.addCleanup(session.close)
        chunks = []
        res = session.transaction_stream(
            ptp.OP_GetPartialObject, [9, 1000, 4096], chunks.append)
        self.assertTrue(res.ok())
        expected = fake_camera.deterministic_content(1024 * 1024 + 777)[1000:5096]
        self.assertEqual(b''.join(chunks), expected)

    def test_opcode_desconocido_reporta_codigo(self):
        fake = make_fake(self)
        session = make_session(fake).connect()
        self.addCleanup(session.close)
        res = session.transaction(0x5FFF)
        self.assertEqual(res.code, ptp.RESP_OperationNotSupported)

    def test_keepalive_renueva_la_sesion(self):
        fake = make_fake(self)
        session = make_session(fake).connect()
        self.addCleanup(session.close)
        data = session.keepalive()
        self.assertTrue(data.startswith(b'\x64\x00'))


class EnumeracionTest(unittest.TestCase):
    def test_list_objects_trae_fotos_y_videos_sin_carpetas(self):
        fake = make_fake(self)
        fake.add_object(1, 'DSCN0001.JPG', 5000, fmt=ptp.FMT_JPEG_EXIF)
        fake.add_object(2, 'DSCN0002.AVI', 900000, fmt=ptp.FMT_AVI)
        fake.add_object(3, 'CARPETA', 0, fmt=ptp.FMT_Association)
        session = make_session(fake).connect()
        self.addCleanup(session.close)
        objs = list_objects(session, 0x00010001)
        names = sorted(obj.filename for obj in objs)
        self.assertEqual(names, ['DSCN0001.JPG', 'DSCN0002.AVI'])

    def test_objectinfo_general_error_se_reintenta(self):
        fake = make_fake(self, objectinfo_general_errors=2)
        fake.add_object(5, 'DSCN0003.AVI', 123456, fmt=ptp.FMT_AVI)
        session = make_session(fake).connect()
        self.addCleanup(session.close)
        from nikon_dl import objects as objects_mod
        sleeps = []
        original_sleep = objects_mod.time.sleep
        objects_mod.time.sleep = lambda s: sleeps.append(s)
        try:
            objs = list_objects(session, 0x00010001)
        finally:
            objects_mod.time.sleep = original_sleep
        self.assertEqual(len(objs), 1)
        self.assertGreaterEqual(len(sleeps), 2)


class CierreTest(unittest.TestCase):
    def test_close_espera_gap_minimo_antes_de_closesession(self):
        fake = make_fake(self)
        session = make_session(fake).connect()
        from nikon_dl import session as session_mod
        sleeps = []
        original_sleep = session_mod.time.sleep
        session_mod.time.sleep = lambda s: sleeps.append(s)
        try:
            session.close()
        finally:
            session_mod.time.sleep = original_sleep
        self.assertTrue(fake.close_requested)
        self.assertAlmostEqual(sleeps[0], MIN_CLOSE_GAP, delta=MIN_CLOSE_GAP)

    def test_close_posterior_al_gap_no_duerme(self):
        import time
        fake = make_fake(self)
        session = make_session(fake).connect()
        time.sleep(MIN_CLOSE_GAP + 0.05)
        from nikon_dl import session as session_mod
        sleeps = []
        original_sleep = session_mod.time.sleep
        session_mod.time.sleep = lambda s: sleeps.append(s)
        try:
            session.close()
        finally:
            session_mod.time.sleep = original_sleep
        self.assertTrue(fake.close_requested)
        self.assertEqual(sleeps, [])


if __name__ == '__main__':
    unittest.main()
