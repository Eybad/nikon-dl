"""Tests end-to-end del CLI (python -m nikon_dl) contra el simulador."""

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from nikon_dl import ptp
from nikon_dl.cli import main
from tests.fake_camera import FakeCamera


class CliTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeCamera()
        self.fake.add_object(1, 'DSCN0001.JPG', 4096)
        self.fake.add_object(2, 'DSCN0002.AVI', 8192, fmt=ptp.FMT_AVI,
                             capture='20260820T120000')
        self.fake.start()
        self.addCleanup(self.fake.stop)
        self.out = Path(tempfile.mkdtemp(prefix='nikondl-cli-'))
        self.base = ['--ip', '127.0.0.1', '--port', str(self.fake.port)]

    def run_cli(self, *extra):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = main(self.base + list(extra))
        return code, stdout.getvalue()

    def test_list_muestra_fotos_y_videos(self):
        code, output = self.run_cli('list')
        self.assertEqual(code, 0)
        self.assertIn('DSCN0001.JPG', output)
        self.assertIn('DSCN0002.AVI', output)
        self.assertIn('photo: 1', output)
        self.assertIn('video: 1', output)

    def test_list_json_valido(self):
        import json
        code, output = self.run_cli('list', '--json')
        self.assertEqual(code, 0)
        payload = json.loads(output)
        self.assertEqual(len(payload), 2)

    def test_download_baja_y_manifest_evita_rebajar(self):
        code, output = self.run_cli('download', '--out', str(self.out))
        self.assertEqual(code, 0)
        self.assertTrue((self.out / 'DSCN0001.JPG').exists())
        self.assertTrue((self.out / 'DSCN0002.AVI').exists())
        code2, output2 = self.run_cli('download', '--out', str(self.out))
        self.assertEqual(code2, 0)
        self.assertIn('0 descargados', output2)

    def test_delete_dry_run_no_borra_y_yes_borra(self):
        code, output = self.run_cli('delete', '--handles', '0x1')
        self.assertEqual(code, 0)
        self.assertIn('Dry-run', output)
        self.assertEqual(self.fake.deleted, set())
        code2, _ = self.run_cli('delete', '--handles', '0x1', '--yes')
        self.assertEqual(code2, 0)
        self.assertEqual(self.fake.deleted, {1})

    def test_delete_sin_filtros_es_error_fail_closed(self):
        code, _ = self.run_cli('delete')
        self.assertEqual(code, 2)


if __name__ == '__main__':
    unittest.main()
