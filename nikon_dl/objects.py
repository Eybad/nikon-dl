"""Enumeracion de objetos en la camara y parseo de datasets MTP.

Datasets segun PIMA 15470 / ISO 15740:
  - strings: u8 cantidad de caracteres (incluye el null final) + UTF-8
  - arrays de u16/u32: u32 cantidad de elementos + elementos
"""

import re
import struct
import time
from dataclasses import dataclass, field
from datetime import datetime

from . import ptp
from .session import CameraResponseError


@dataclass
class CameraObject:
    handle: int
    storage_id: int
    format_code: int
    size: int
    filename: str
    capture_date: object = None   # datetime | None
    modified_date: object = None  # datetime | None
    parent: int = 0
    kind: str = field(default='other')

    def __post_init__(self):
        self.kind = ptp.classify_format(self.format_code)


# --- primitivas de parseo ---

def parse_ptp_string(data, off):
    """String PTP/MTP segun ISO 15740 y firmware real Nikon.

    Layout: u8 cantidad de unidades (INCLUYE el null final) seguido por
    esas unidades en UTF-16LE. Se consumen 1 + 2*count bytes.
    Variante count==0: solo el byte de count, sin payload.

    Verificado en campo contra el S3700: 'S3700' llega como
    06 53 00 33 00 37 00 30 00 30 00 00 00. Airnef hace lo mismo
    (strutil.stringFromUtf16ByteArray).
    """
    if off >= len(data):
        return '', off
    count = data[off]
    if count == 0:
        return '', off + 1
    raw = data[off + 1:off + 1 + 2 * count]
    text = raw[:2 * (count - 1)].decode('utf-16-le', errors='replace')
    return text, off + 1 + 2 * count


def parse_u16_array(data, off):
    (count,) = _unpack(data, off, 4, '<I')
    off += 4
    values = list(struct.unpack('<%dH' % count, data[off:off + 2 * count])) if count else []
    return values, off + 2 * count


def parse_u32_array(data, off):
    (count,) = _unpack(data, off, 4, '<I')
    off += 4
    values = list(struct.unpack('<%dI' % count, data[off:off + 4 * count])) if count else []
    return values, off + 4 * count


def _unpack(data, off, size, fmt):
    if off + size > len(data):
        raise ValueError('dataset truncado: esperaba %d bytes en offset %d' % (size, off))
    return struct.unpack(fmt, data[off:off + size])


# --- datasets ---

OBJECTINFO_FIXED_LEN = 52


def parse_objectinfo(body, handle):
    """Parsea el dataset ObjectInfo (PIMA 15470) de un objeto."""
    (storage_id, format_code, protection, size, thumb_fmt, thumb_size,
     thumb_w, thumb_h, img_w, img_h, depth, parent,
     assoc_type, assoc_desc, seq_num) = struct.unpack('<IHHIHIIIIIIIHII',
                                                      body[:OBJECTINFO_FIXED_LEN])
    off = OBJECTINFO_FIXED_LEN
    filename, off = parse_ptp_string(body, off)
    capture_raw, off = parse_ptp_string(body, off)
    modified_raw, off = parse_ptp_string(body, off)
    return CameraObject(
        handle=handle,
        storage_id=storage_id,
        format_code=format_code,
        size=size,
        filename=filename,
        capture_date=parse_ptp_date(capture_raw),
        modified_date=parse_ptp_date(modified_raw),
        parent=parent,
    )


def parse_deviceinfo(body):
    """Parsea DeviceInfo; devuelve dict con los campos utiles."""
    info = {}
    off = 0
    (standard_version,) = _unpack(body, off, 2, '<H'); off += 2
    (vendor_ext_id,) = _unpack(body, off, 4, '<I'); off += 4
    (vendor_ext_ver,) = _unpack(body, off, 2, '<H'); off += 2
    desc, off = parse_ptp_string(body, off)
    (functional_mode,) = _unpack(body, off, 2, '<H'); off += 2
    info['standard_version'] = standard_version
    info['vendor_extension_id'] = vendor_ext_id
    info['vendor_extension_desc'] = desc
    info['functional_mode'] = functional_mode
    for key in ('operations_supported', 'events_supported',
                'device_properties_supported', 'capture_formats', 'image_formats'):
        info[key], off = parse_u16_array(body, off)
    for key in ('manufacturer', 'model', 'device_version', 'serial_number'):
        info[key], off = parse_ptp_string(body, off)
    return info


DATE_RE = re.compile(r'^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})$')


def parse_ptp_date(text):
    """Convierte 'YYYYMMDDTHHMMSS' de MTP a datetime; devuelve None si invalido."""
    if not text:
        return None
    match = DATE_RE.match(text.strip())
    if not match:
        return None
    try:
        return datetime(*[int(g) for g in match.groups()])
    except ValueError:
        return None


def sanitize_filename(name):
    """Nombre seguro para el filesystem del telefono."""
    cleaned = re.sub(r'[\\/\x00-\x1f]', '_', name or '').strip()
    if not cleaned or cleaned.startswith('.'):
        cleaned = 'objeto_' + cleaned.lstrip('.') if cleaned else 'objeto_sin_nombre'
    return cleaned


# --- operaciones de enumeracion ---

def list_storages(session):
    """StorageIDs presentes (SD, memoria interna...)."""
    res = session.transaction(ptp.OP_GetStorageIDs)
    res.expect_ok()
    ids, _ = parse_u32_array(res.data, 0)
    return ids


def get_object_info_retry(session, handle, max_wait=5.0, interval=0.25):
    """GetObjectInfo con retry: Nikon puede responder GeneralError si el
    objeto fue creado recientemente (justo despues de una captura)."""
    deadline = time.monotonic() + max_wait
    while True:
        res = session.transaction(ptp.OP_GetObjectInfo, [handle])
        if res.ok():
            return parse_objectinfo(res.data, handle)
        if res.code != ptp.RESP_GeneralError or time.monotonic() >= deadline:
            raise CameraResponseError(ptp.OP_GetObjectInfo, res.code, res.params)
        time.sleep(interval)


def list_objects(session, storage_id):
    """Objetos de un storage, sin carpetas (Association)."""
    res = session.transaction(ptp.OP_GetObjectHandles, [storage_id, 0, 0])
    res.expect_ok()
    handles, _ = parse_u32_array(res.data, 0)
    objects = []
    for handle in handles:
        obj = get_object_info_retry(session, handle)
        if obj.format_code == ptp.FMT_Association:
            continue
        objects.append(obj)
    return objects


def list_all_objects(session):
    """Todos los objetos de todos los storages."""
    result = []
    for storage_id in list_storages(session):
        result.extend(list_objects(session, storage_id))
    return result


def human_size(num_bytes):
    value = float(num_bytes)
    for unit in ('B', 'KB', 'MB', 'GB'):
        if value < 1024 or unit == 'GB':
            return '%.1f %s' % (value, unit) if unit != 'B' else '%d B' % int(value)
        value /= 1024.0
