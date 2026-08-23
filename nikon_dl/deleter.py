"""Borrado de objetos en la camara, fail-closed por diseno.

Reglas:
  - build_deletion_plan() SIN filtros explicitos devuelve [] (nada se borra
    por accidente).
  - delete_objects() siempre borra de a uno con DeleteObject(handle, 0) y
    reporta exitos/fallos individuales: un handle invalido no frena el resto.
  - La puerta de confirmacion (--yes / --delete-after) vive en el CLI, nunca
    aca.
"""

from datetime import datetime

from . import ptp
from .downloader import SessionHolder
from .session import CameraError, CameraResponseError


def build_deletion_plan(objects, handles=None, older_than=None, types=None):
    """Selecciona candidatos a borrar.

    handles: set de object handles.
    older_than: 'AAAAMMDD'; incluye objetos capturados ANTES de esa fecha
      (usa capture_date, fallback modified_date; sin fecha => excluido).
    types: iterable de kinds ('photo', 'video', 'other').
    """
    if not (handles or older_than or types):
        return []
    limit = None
    if older_than:
        limit = datetime.strptime(older_than, '%Y%m%d')
    plan = []
    for obj in objects:
        if types is not None and obj.kind not in types:
            continue
        if handles is not None and obj.handle not in handles:
            continue
        if limit is not None:
            reference = obj.capture_date or obj.modified_date
            if reference is None or reference >= limit:
                continue
        plan.append(obj)
    return plan


def delete_objects(session_source, objects):
    """Ejecuta el borrado. Devuelve {'deleted': [(obj, None)], 'failed': [(obj, exc)]}."""
    holder = session_source if isinstance(session_source, SessionHolder) \
        else SessionHolder(provider=session_source)
    result = {'deleted': [], 'failed': []}
    for obj in objects:
        try:
            res = holder.current().transaction(ptp.OP_DeleteObject,
                                               [obj.handle, 0])
            if res.ok():
                result['deleted'].append((obj, None))
            else:
                result['failed'].append(
                    (obj, CameraResponseError(ptp.OP_DeleteObject,
                                              res.code, res.params)))
        except CameraError as exc:
            result['failed'].append((obj, exc))
    return result
