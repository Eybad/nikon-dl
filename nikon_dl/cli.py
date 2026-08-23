"""CLI de nikon-dl: list / download / delete."""

import argparse
import json
import sys
from pathlib import Path

from . import ptp
from .deleter import build_deletion_plan, delete_objects
from .downloader import CHUNK_SIZE, SessionHolder, download_all
from .objects import (
    human_size,
    list_all_objects,
    parse_deviceinfo,
)
from .session import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    CameraError,
    CameraSession,
)
from .transport import configure_debug, hexdump


def debug_sink(direction, payload_type, body):
    name = ptp.CONTAINER_NAMES.get(payload_type, '0x%02X' % payload_type)
    sys.stderr.write('--- %s %s (%d bytes)\n%s\n'
                     % (direction, name, len(body), hexdump(body)))


def default_out_dir():
    """Descargas de Android si existe (termux-setup-storage), sino local."""
    downloads = Path.home() / 'storage' / 'downloads'
    if downloads.is_dir():
        return downloads
    return Path.cwd() / 'nikon-dl-descargas'


def build_parser():
    parser = argparse.ArgumentParser(
        prog='nikon-dl',
        description='Descarga fotos y videos de una Nikon Coolpix Wi-Fi '
                    '(MTP-IP sobre TCP 15740).')
    parser.add_argument('--ip', default=DEFAULT_HOST,
                        help='IP de la camara (default %(default)s)')
    parser.add_argument('--port', type=int, default=DEFAULT_PORT)
    parser.add_argument('--debug', action='store_true',
                        help='vuelca todo el trafico MTP-IP en hexadecimal')

    sub = parser.add_subparsers(dest='command', required=True)

    p_list = sub.add_parser('list', help='enumera fotos/videos en la camara')
    p_list.add_argument('--json', dest='as_json', action='store_true',
                        help='salida JSON')

    p_dl = sub.add_parser('download', help='descarga archivos al telefono')
    p_dl.add_argument('--out', '-o', type=Path, default=None,
                      help='directorio destino (default: descargas de Android)')
    p_dl.add_argument('--all', action='store_true',
                      help='baja todo ignorando el manifest incremental')
    p_dl.add_argument('--type', choices=('photo', 'video', 'all'),
                      default='all')
    p_dl.add_argument('--delete-after', action='store_true',
                      help='borra de la camara lo descargado y verificado')
    p_dl.add_argument('--chunk-kb', type=int, default=CHUNK_SIZE // 1024,
                      help='tamano de chunk GetPartialObject en KB')

    p_del = sub.add_parser('delete',
                           help='borra objetos de la camara (dry-run por defecto)')
    p_del.add_argument('--handles', nargs='+', metavar='HANDLE',
                       help='handles en hex (0x9) o decimal (9)')
    p_del.add_argument('--older-than', metavar='AAAAMMDD',
                       help='solo capturas anteriores a esa fecha')
    p_del.add_argument('--type', choices=('photo', 'video', 'all'))
    p_del.add_argument('--yes', action='store_true',
                       help='ejecuta el borrado; sin esto solo lista')
    return parser


def connect_holder(args):
    """Conecta e imprime resumen. Devuelve (SessionHolder, device_info)."""
    def provider():
        return CameraSession(ip=args.ip, port=args.port).connect()

    holder = SessionHolder(provider=provider)
    session = holder.current()
    info = parse_deviceinfo(session.device_info_raw)
    # a stderr: stdout queda limpio para datos (--json / pipeo)
    print('Conectado a %s:%d | sesion 0x%X | modelo: %s'
          % (args.ip, args.port, session.session_id,
             info.get('model') or '(desconocido)'), file=sys.stderr)
    return holder, info


def describe(obj):
    date = obj.capture_date.strftime('%Y-%m-%d %H:%M') if obj.capture_date else '-'
    return '0x%08x  %-5s %10s  %s  %s' % (obj.handle, obj.kind,
                                          human_size(obj.size), date,
                                          obj.filename)


def cmd_list(args):
    holder, _ = connect_holder(args)
    try:
        objects = list_all_objects(holder.current())
    finally:
        holder.close()
    if args.as_json:
        payload = [{'handle': '0x%08x' % o.handle, 'name': o.filename,
                    'kind': o.kind, 'size': o.size,
                    'date': o.capture_date.isoformat() if o.capture_date else None}
                   for o in objects]
        print(json.dumps(payload, ensure_ascii=False, indent=1))
        return 0
    print('%d objetos:' % len(objects))
    for obj in objects:
        print(describe(obj))
    for kind in ('photo', 'video', 'other'):
        count = sum(1 for o in objects if o.kind == kind)
        if count:
            total = sum(o.size for o in objects if o.kind == kind)
            print('%s: %d (%s)' % (kind, count, human_size(total)))
    return 0


def cmd_download(args):
    out_dir = args.out or default_out_dir()
    types = ('photo', 'video') if args.type == 'all' else (args.type,)
    chunk_size = max(64, args.chunk_kb) * 1024
    holder, _ = connect_holder(args)
    try:
        objects = list_all_objects(holder.current())
        result = download_all(holder, objects, out_dir,
                              mode='all' if args.all else 'new',
                              types=types, delete_after=args.delete_after)

        if args.delete_after and result['to_delete']:
            outcome = delete_objects(holder, result['to_delete'])
            print('Borrados en camara: %d' % len(outcome['deleted']))
            for obj, exc in outcome['failed']:
                print('No se pudo borrar %s: %s' % (obj.filename, exc),
                      file=sys.stderr)
    finally:
        holder.close()

    downloaded_bytes = sum(o.size for o, _ in result['downloaded'])
    print('Listo: %d descargados (%s), %d omitidos, %d errores -> %s'
          % (len(result['downloaded']), human_size(downloaded_bytes),
             len(result['skipped']), len(result['errors']), out_dir))
    return 1 if result['errors'] else 0


def cmd_delete(args):
    if not (args.handles or args.older_than or args.type):
        print('Fail-closed: indica --handles, --older-than o --type '
              'para armar el plan de borrado.', file=sys.stderr)
        return 2
    holder, _ = connect_holder(args)
    try:
        objects = list_all_objects(holder.current())
        handles = ({int(h, 0) for h in args.handles}
                   if args.handles else None)
        types = None if args.type in (None, 'all') else (args.type,)
        plan = build_deletion_plan(objects, handles=handles,
                                   older_than=args.older_than, types=types)
        if not plan:
            print('Plan vacio: ningun objeto coincide con el filtro.')
            return 0
        print('Objetos marcados para borrado:')
        for obj in plan:
            print('  ' + describe(obj))
        if not args.yes:
            print('\nDry-run: %d objetos (%s). Re-ejecuta con --yes para borrar.'
                  % (len(plan), human_size(sum(o.size for o in plan))))
            return 0
        outcome = delete_objects(holder, plan)
        print('Borrados: %d | Fallos: %d'
              % (len(outcome['deleted']), len(outcome['failed'])))
        for obj, exc in outcome['failed']:
            print('Fallo %s: %s' % (obj.filename, exc), file=sys.stderr)
        return 1 if outcome['failed'] else 0
    finally:
        holder.close()


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.debug:
        configure_debug(debug_sink)
    handlers = {'list': cmd_list, 'download': cmd_download,
                'delete': cmd_delete}
    try:
        return handlers[args.command](args)
    except CameraError as exc:
        print('ERROR: %s' % exc, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('\nInterrumpido: los .part quedaron en disco y el proximo '
              'run reanuda desde ahi.', file=sys.stderr)
        return 130
