"""Tests de downloader.py contra el simulador: descarga, resume, verificacion,
manifest incremental y reconexion a mitad de transferencia."""

import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from nikon_dl import ptp
from nikon_dl.downloader import (
    MANIFEST_NAME,
    DownloadError,
    SessionHolder,
    VerificationError,
    download_all,
    download_object,
    load_manifest,
)
from nikon_dl.session import CameraSession
from tests.fake_camera import FakeCamera, deterministic_content


def make_fake(test, **kwargs):
    fake = FakeCamera(**kwargs)
    fake.start()
    test.addCleanup(fake.stop)
    return fake


def make_holder(test, fake):
    def provider():
        return CameraSession(ip='127.0.0.1', port=fake.port,
                             connect_timeout=2.0, rw_timeout=2.0).connect()
    holder = SessionHolder(provider=provider)
    test.addCleanup(holder.close)
    return holder


def tmpdir(test):
    directory = tempfile.mkdtemp(prefix='nikondl-test-')
    test.addCleanup(lambda: None)  # los .part quedan para inspeccion manual
    return Path(directory)


class DownloadObjectTest(unittest.TestCase):
    def test_descarga_feliz_contenido_y_mtime(self):
        fake = make_fake(self)
        fake.add_object(9, 'DSCN0001.JPG', 500_000, fmt=ptp.FMT_JPEG_EXIF,
                        capture='20260815T143010')
        out = tmpdir(self)
        obj = [o for o in iter_objects(fake, 9)][0]
        path, status = download_object(make_holder(self, fake), obj, out)
        self.assertEqual(status, 'descargado')
        expected = deterministic_content(500_000)
        self.assertEqual(path.read_bytes(), expected)
        ts = datetime(2026, 8, 15, 14, 30, 10).timestamp()
        self.assertAlmostEqual(os.path.getmtime(path), ts, delta=1)

    def test_archivo_completo_existente_se_omite(self):
        fake = make_fake(self)
        fake.add_object(9, 'DSCN0001.JPG', 1000)
        out = tmpdir(self)
        obj = list(iter_objects(fake, 9))[0]
        target = out / 'DSCN0001.JPG'
        target.write_bytes(deterministic_content(1000))
        _, status = download_object(make_holder(self, fake), obj, out)
        self.assertEqual(status, 'ya-existia')

    def test_resume_desde_part_previo(self):
        fake = make_fake(self)
        fake.add_object(7, 'DSCN0002.AVI', 400_000, fmt=ptp.FMT_AVI)
        out = tmpdir(self)
        part = out / 'DSCN0002.AVI.part'
        part.write_bytes(deterministic_content(400_000)[:150_000])
        obj = list(iter_objects(fake, 7))[0]
        path, status = download_object(make_holder(self, fake), obj, out)
        self.assertEqual(status, 'reanudado')
        self.assertEqual(path.read_bytes(), deterministic_content(400_000))
        self.assertFalse(part.exists())

    def test_tamano_incorrecto_levanta_verification_error_y_conserva_part(self):
        fake = make_fake(self, serve_size_deficit=100)
        fake.add_object(8, 'DSCN0003.AVI', 50_000, fmt=ptp.FMT_AVI)
        out = tmpdir(self)
        obj = list(iter_objects(fake, 8))[0]
        with self.assertRaises(VerificationError):
            download_object(make_holder(self, fake), obj, out)
        self.assertTrue((out / 'DSCN0003.AVI.part').exists())

    def test_caida_de_conexion_a_mitad_reconecta_y_resume(self):
        fake = make_fake(self, truncate_partial_at=120_000)
        fake.add_object(6, 'DSCN0004.AVI', 400_000, fmt=ptp.FMT_AVI)
        out = tmpdir(self)
        obj = list(iter_objects(fake, 6))[0]
        path, status = download_object(make_holder(self, fake), obj, out)
        self.assertEqual(path.read_bytes(), deterministic_content(400_000))


class DownloadAllTest(unittest.TestCase):
    def setUp(self):
        self.fake = make_fake(self)
        self.fake.add_object(1, 'A.JPG', 3000, fmt=ptp.FMT_JPEG_EXIF)
        self.fake.add_object(2, 'B.AVI', 8000, fmt=ptp.FMT_AVI)
        self.fake.add_object(3, 'C.TXT', 10, fmt=0x3004)  # tipo excluido
        self.out = tmpdir(self)

    def objs(self):
        return list(iter_objects(self.fake, 1, 2, 3))

    def test_modo_new_baja_una_vez_y_luego_salta(self):
        provider = make_holder(self, self.fake)
        result = download_all(provider, self.objs(), self.out, mode='new')
        self.assertEqual(len(result['downloaded']), 2)  # TXT excluido por tipo
        result2 = download_all(provider, self.objs(), self.out, mode='new')
        self.assertEqual(len(result2['downloaded']), 0)
        self.assertEqual(len(result2['skipped']), 2)
        self.assertTrue((self.out / MANIFEST_NAME).exists())
        manifest = load_manifest(self.out)
        self.assertEqual(len(manifest), 2)

    def test_delete_after_acumula_solo_lo_verificado(self):
        provider = make_holder(self, self.fake)
        result = download_all(provider, self.objs(), self.out,
                              mode='all', delete_after=True)
        handles = sorted(obj.handle for obj in result['to_delete'])
        self.assertEqual(handles, [1, 2])

    def test_error_en_un_objeto_no_frena_los_demas(self):
        self.fake.add_object(4, 'ROTA.JPG', 999999, fmt=ptp.FMT_JPEG_EXIF,
                             capture='')  # sin fecha; contenido mas corto
        self.fake.objects[4]['content'] = b'corto'
        provider = make_holder(self, self.fake)
        result = download_all(provider, self.objs() + list(iter_objects(self.fake, 4)),
                              self.out, mode='all')
        self.assertEqual(len(result['downloaded']), 2)
        self.assertEqual(len(result['errors']), 1)


def iter_objects(fake, *handles):
    """Objetos reales de la camara via protocolo (no dicts del fake)."""
    from nikon_dl.objects import list_objects
    session = CameraSession(ip='127.0.0.1', port=fake.port,
                            connect_timeout=2.0, rw_timeout=2.0).connect()
    try:
        wanted = set(handles)
        return [o for o in list_objects(session, 0x00010001) if o.handle in wanted]
    finally:
        session.close()


if __name__ == '__main__':
    unittest.main()
