"""Tests del borrado fail-closed contra el simulador."""

import unittest

from nikon_dl import ptp
from nikon_dl.deleter import build_deletion_plan, delete_objects
from nikon_dl.downloader import SessionHolder
from nikon_dl.session import CameraSession
from tests.fake_camera import FakeCamera


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


def iter_objects(fake):
    from nikon_dl.objects import list_all_objects
    session = CameraSession(ip='127.0.0.1', port=fake.port,
                            connect_timeout=2.0, rw_timeout=2.0).connect()
    try:
        return list_all_objects(session)
    finally:
        session.close()


class BuildPlanTest(unittest.TestCase):
    def setUp(self):
        self.fake = make_fake(self)
        self.fake.add_object(1, 'VIEJO1.JPG', 100, capture='20250101T090000')
        self.fake.add_object(2, 'NUEVO.JPG', 200, capture='20260801T090000')
        self.fake.add_object(3, 'VIEJO2.AVI', 300, fmt=ptp.FMT_AVI,
                             capture='20250202T090000')

    def objs(self):
        return iter_objects(self.fake)

    def test_filtro_por_handles(self):
        plan = build_deletion_plan(self.objs(), handles={1, 3})
        self.assertEqual(sorted(o.handle for o in plan), [1, 3])

    def test_filtro_por_fecha_limite(self):
        plan = build_deletion_plan(self.objs(), older_than='20260101')
        self.assertEqual(sorted(o.handle for o in plan), [1, 3])

    def test_filtro_por_tipo(self):
        plan = build_deletion_plan(self.objs(), types=('video',))
        self.assertEqual([o.handle for o in plan], [3])

    def test_sin_filtros_no_devuelve_nada_fail_closed(self):
        self.assertEqual(build_deletion_plan(self.objs()), [])


class DeleteObjectsTest(unittest.TestCase):
    def test_borra_lo_pedido_y_reporta(self):
        fake = make_fake(self)
        fake.add_object(1, 'A.JPG', 100)
        fake.add_object(2, 'B.AVI', 200, fmt=ptp.FMT_AVI)
        holder = make_holder(self, fake)
        objs = {o.handle: o for o in iter_objects(fake)}
        result = delete_objects(holder, [objs[1], objs[2]])
        self.assertEqual(len(result['deleted']), 2)
        self.assertEqual(fake.deleted, {1, 2})

    def test_handle_inexistente_queda_en_failed_sin_frenar_los_demas(self):
        import dataclasses
        fake = make_fake(self)
        fake.add_object(1, 'A.JPG', 100)
        holder = make_holder(self, fake)
        objs = iter_objects(fake)
        ghost = dataclasses.replace(objs[0], handle=999)
        result = delete_objects(holder, [ghost, objs[0]])
        self.assertEqual(len(result['failed']), 1)
        self.assertEqual(result['deleted'][0][0].handle, 1)


if __name__ == '__main__':
    unittest.main()
