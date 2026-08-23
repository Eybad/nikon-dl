"""Descarga de objetos por GetPartialObject con resume, verificacion y manifest.

Decisiones de diseno (quirks Nikon):
  - SOLO GetPartialObject en chunks (default 1 MB): el firmware se cuelga con
    GetObject en archivos grandes, peor con video.
  - Descarga atomica: .part temporal -> verificacion -> rename final.
  - Resume: si existe un .part se continua desde su tamano (GetPartialObject
    recibe offset), con reconexion automatica ante caida de sesion Wi-Fi.
  - Verificacion: tamano contra ObjectInfo + magic bytes por tipo; una
    discrepancia levanta VerificationError y conserva el .part.
"""

import json
import os
import time
from datetime import datetime
from pathlib import Path

from . import ptp
from .objects import sanitize_filename
from .session import CameraConnectionError, CameraError, CameraSession

CHUNK_SIZE = 1024 * 1024
MANIFEST_NAME = '.nikon-dl-manifest.json'
MAX_RECONNECTS = 3


class SessionHolder(object):
    """Administra la sesion activa: lazy-connect, reconexion y cierre.

    provider: zero-arg callable que devuelve una CameraSession conectada
    (para tests contra el simulador). Si no se pasa, conecta directo.
    """

    def __init__(self, provider=None, **session_kwargs):
        self._provider = provider
        self._kwargs = session_kwargs
        self._session = None

    def current(self):
        if self._session is None:
            if self._provider is not None:
                self._session = self._provider()
            else:
                self._session = CameraSession(**self._kwargs).connect()
        return self._session

    def reconnect(self):
        if self._session is not None:
            self._session.close(force=True)
            self._session = None
        return self.current()

    def close(self):
        if self._session is not None:
            self._session.close()
            self._session = None


class DownloadError(CameraError):
    """Fallo de descarga (respuesta MTP no OK o sesion irrecuperable)."""


class VerificationError(DownloadError):
    """El archivo bajado no coincide con lo declarado por la camara."""


def magic_mismatch(kind, head):
    """Devuelve descripcion del mismatch o None si el header es consistente."""
    if kind == 'photo':
        if not head.startswith(b'\xff\xd8\xff'):
            return 'no es JPEG (header %s)' % head[:4].hex()
        return None
    if kind == 'video':
        if head[:4] == b'RIFF' and head[8:12] == b'AVI ':
            return None
        if head[4:8] == b'ftyp':
            return None
        return 'no es AVI/MP4 reconocible (header %s)' % head[:12].hex()
    return None


def manifest_key(obj):
    date = obj.capture_date.isoformat() if obj.capture_date else ''
    return '%s|%d|%s' % (obj.filename, obj.size, date)


def load_manifest(out_dir):
    path = out_dir / MANIFEST_NAME
    if not path.exists():
        return {}
    try:
        with open(path, encoding='utf-8') as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_manifest(out_dir, manifest):
    with open(out_dir / MANIFEST_NAME, 'w', encoding='utf-8') as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=1, sort_keys=True)


def download_object(session_source, obj, out_dir, progress=None,
                    resume=True, chunk_size=CHUNK_SIZE):
    """Descarga un CameraObject a out_dir. Devuelve (Path, status).

    session_source: SessionHolder (recomendado) o callable proveedor.
    status: 'descargado' | 'reanudado' | 'ya-existia'.
    """
    holder = session_source if isinstance(session_source, SessionHolder) \
        else SessionHolder(provider=session_source)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    final = out_dir / sanitize_filename(obj.filename)
    if final.exists() and final.stat().st_size == obj.size:
        return final, 'ya-existia'

    tmp = final.with_name(final.name + '.part')
    offset = 0
    if resume and tmp.exists():
        offset = tmp.stat().st_size
        if offset > obj.size:
            offset = 0  # basura de otra corrida: arrancar de nuevo
    resumed = offset > 0

    session = holder.current()
    reconnects = 0
    done = offset
    started = time.monotonic()

    def write_chunk(chunk):
        nonlocal done
        fh.write(chunk)
        done += len(chunk)
        if progress:
            progress(obj.filename, done, obj.size)

    with open(tmp, 'ab' if resumed else 'wb') as fh:
        while done < obj.size:
            want = min(chunk_size, obj.size - done)
            before = done
            try:
                res = session.transaction_stream(
                    ptp.OP_GetPartialObject, [obj.handle, done, want], write_chunk)
            except CameraConnectionError:
                fh.flush()
                reconnects += 1
                if reconnects > MAX_RECONNECTS:
                    raise DownloadError(
                        '%s: sesion caida tras %d reconexiones (offset %d/%d)'
                        % (obj.filename, MAX_RECONNECTS, done, obj.size))
                session = holder.reconnect()  # cierra la muerta y abre nueva
                continue
            if not res.ok():
                raise DownloadError('%s: GetPartialObject respondio %s'
                                    % (obj.filename, ptp.response_name(res.code)))
            if done == before:
                # la camara no devolvio bytes sin marcar error: firmware corto
                break

    elapsed = time.monotonic() - started
    actual = tmp.stat().st_size
    if actual != obj.size:
        raise VerificationError(
            '%s: tamano descargado %d != declarado %d (se conserva .part para resume)'
            % (obj.filename, actual, obj.size))

    with open(tmp, 'rb') as fh:
        head = fh.read(12)
    mismatch = magic_mismatch(obj.kind, head)
    if mismatch:
        # warning no fatal: el tamano ya coincide, el header es heuristica
        print('AVISO: %s %s' % (obj.filename, mismatch))

    timestamp = obj.capture_date or obj.modified_date
    if isinstance(timestamp, datetime):
        epoch = timestamp.timestamp()
        os.utime(tmp, (epoch, epoch))
    tmp.rename(final)

    if progress:
        progress(obj.filename, obj.size, obj.size)
    return final, ('reanudado' if resumed else 'descargado')


def download_all(session_source, objects, out_dir, mode='new',
                 types=('photo', 'video'), delete_after=False,
                 progress=None, log=None):
    """Descarga en lote. Devuelve dict con downloaded/skipped/errors/to_delete."""
    holder = session_source if isinstance(session_source, SessionHolder) \
        else SessionHolder(provider=session_source)
    def say(message):
        if log:
            log(message)

    out_dir = Path(out_dir)
    manifest = load_manifest(out_dir)
    typed = [o for o in objects if o.kind in types]
    if mode == 'all':
        pending = list(typed)
        skipped = []
    else:
        pending = [o for o in typed if manifest_key(o) not in manifest]
        skipped = [o for o in typed if o not in pending]

    result = {'downloaded': [], 'skipped': skipped, 'errors': [], 'to_delete': []}
    total_bytes = sum(o.size for o in pending)
    say('%d archivos a descargar (%s)' % (len(pending), _human(total_bytes)))

    for index, obj in enumerate(pending, 1):
        try:
            path, status = download_object(holder, obj, out_dir,
                                           progress=progress)
        except CameraError as exc:
            say('ERROR %s: %s' % (obj.filename, exc))
            result['errors'].append((obj, exc))
            continue
        result['downloaded'].append((obj, path))
        if delete_after:
            result['to_delete'].append(obj)
        entry = {
            'file': path.name,
            'size': obj.size,
            'date': obj.capture_date.isoformat() if obj.capture_date else None,
            'at': datetime.now().isoformat(timespec='seconds'),
        }
        manifest[manifest_key(obj)] = entry
        save_manifest(out_dir, manifest)
        say('[%d/%d] %s %s (%s)'
            % (index, len(pending), obj.filename, _human(obj.size), status))

    return result


def _human(num_bytes):
    value = float(num_bytes)
    for unit in ('B', 'KB', 'MB', 'GB'):
        if value < 1024 or unit == 'GB':
            return ('%d B' % value) if unit == 'B' else ('%.1f %s' % (value, unit))
        value /= 1024.0
