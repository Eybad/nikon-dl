"""Sesion MTP-IP contra camaras Nikon: handshake, transacciones, cierre seguro.

Flujo de conexion (orden estricto, verificado contra el comportamiento de
Airnef con el parque Nikon):

  1. socket primario -> InitCommandRequest(GUID, hostname, version)
     -> Ack devuelve connection number que Nikon usa como session ID.
  2. segundo socket (eventos) -> InitEventRequest(GUID, session_id) -> Ack.
  3. Probe en el socket de eventos. OBLIGATORIO: sin el la sesion se cuelga.
  4. GetDeviceInfo (funciona fuera de sesion).
  5. OpenSession(session_id). Si rechaza el ID devuelto, fallback a 0x1
     (algunos modelos como J5/P900 devuelven 0x0 y exigen 0x1; es el valor
     que usa la WMU de Nikon).

Quirks de firmware Nikon manejados aca:
  - CloseSession emitido a menos de 1 s de OpenSession deja a la camara
    sorda ante el proximo INIT hasta ciclar Wi-Fi/apagado -> se duerme lo
    que falte para completar 1 s antes de cerrar.
  - La sesion cae tras ~30 s sin trafico -> keepalive() con GetDeviceInfo.
"""

import struct
import time

from . import ptp
from .transport import (
    ConnectionClosed,
    TransportError,
    recv_frame,
    send_frame,
)

DEFAULT_HOST = '192.168.1.1'
DEFAULT_PORT = 15740
DEFAULT_CONNECT_TIMEOUT = 10.0
DEFAULT_RW_TIMEOUT = 5.0

# GUID del host: Nikon no lo valida (a diferencia de Canon/Sony). Se usa el
# valor documentado compatible con WMU, enviado como dos u64 little-endian.
DEFAULT_GUID = struct.pack('<QQ', 0x7766554433221100, 0x0000000000009988)
DEFAULT_HOSTNAME = 'nikon-dl'
DEFAULT_HOST_VERSION = 0x00010000

MIN_CLOSE_GAP = 1.0  # segundos minimos entre OpenSession y CloseSession


class CameraError(Exception):
    """Error base de comunicacion con la camara."""


class CameraConnectionError(CameraError):
    """Fallo de conexion/handshake o caida de sesion."""


class CameraResponseError(CameraError):
    """La camara respondio con un codigo distinto de OK."""

    def __init__(self, opcode, code, params=()):
        self.opcode = opcode
        self.code = code
        self.params = list(params)
        op_name = ptp.OPCODE_NAMES.get(opcode, '0x%04X' % opcode)
        super().__init__('%s respondio %s (0x%04X)' % (op_name, ptp.response_name(code), code))


def _encode_friendly_name(name):
    """u32 cantidad de caracteres (incluye null) + UTF-16LE + terminador."""
    raw = name.encode('utf-16-le')
    return struct.pack('<I', len(name) + 1) + raw + b'\x00\x00'


class TransactionResult(object):
    __slots__ = ('code', 'params', 'data', 'opcode')

    def __init__(self, code, params, data, opcode=0):
        self.code = code
        self.params = params
        self.data = data or b''
        self.opcode = opcode

    def ok(self):
        return self.code == ptp.RESP_OK

    def expect_ok(self):
        if not self.ok():
            raise CameraResponseError(self.opcode, self.code, self.params)
        return self


class CameraSession(object):
    """Sesion completa contra una camara Nikon Wi-Fi."""

    def __init__(self, ip=DEFAULT_HOST, port=DEFAULT_PORT,
                 connect_timeout=DEFAULT_CONNECT_TIMEOUT,
                 rw_timeout=DEFAULT_RW_TIMEOUT,
                 guid=DEFAULT_GUID, hostname=DEFAULT_HOSTNAME,
                 host_version=DEFAULT_HOST_VERSION):
        if len(guid) != 16:
            raise ValueError('guid debe tener 16 bytes')
        self.ip = ip
        self.port = port
        self.connect_timeout = connect_timeout
        self.rw_timeout = rw_timeout
        self.guid = guid
        self.hostname = hostname
        self.host_version = host_version

        self.session_id = None
        self.device_info_raw = None
        self._cmd_sock = None
        self._evt_sock = None
        self._txid = 0
        self._opened_at = None
        self._session_open = False

    # --- ciclo de vida ---

    def connect(self):
        """Handshake completo. Levanta CameraConnectionError con pistas utiles."""
        import socket
        try:
            self._cmd_sock = self._open_socket()
        except OSError as exc:
            raise CameraConnectionError(
                'no se pudo conectar TCP a %s:%d (%s); '
                'verifica que el telefono este conectado al AP Wi-Fi de la camara'
                % (self.ip, self.port, exc))
        try:
            self.session_id = self._init_command_channel()
            self._evt_sock = self._open_socket()
            self._init_event_channel()
            self._probe()
            res = self.transaction(ptp.OP_GetDeviceInfo)
            if not res.ok():
                raise CameraResponseError(ptp.OP_GetDeviceInfo, res.code, res.params)
            self.device_info_raw = res.data
            self._open_session(self.session_id)
        except CameraError:
            self.close(force=True)
            raise
        except OSError as exc:
            self.close(force=True)
            raise CameraConnectionError(
                'la camara acepto TCP pero fallo el handshake (%s); '
                'si no responde al INIT, cicla el Wi-Fi o apaga/enciende la camara' % exc)
        return self

    def _open_socket(self):
        import socket
        sock = socket.create_connection((self.ip, self.port), timeout=self.connect_timeout)
        sock.settimeout(self.rw_timeout)
        return sock

    def _init_command_channel(self):
        body = self.guid + _encode_friendly_name(self.hostname) \
            + struct.pack('<I', self.host_version)
        send_frame(self._cmd_sock, ptp.INIT_CMD_REQ, body)
        ptype, payload = recv_frame(self._cmd_sock)
        if ptype == ptp.INIT_FAIL:
            raise CameraConnectionError(
                'la camara rechazo el init (INIT_FAIL); cicla el Wi-Fi de la camara '
                'o apagala y prendela antes de reintentar')
        if ptype != ptp.INIT_CMD_ACK:
            raise CameraConnectionError(
                'respuesta inesperada al INIT_CMD_REQ: tipo 0x%02x' % ptype)
        # ConnectionNumber u32: Nikon lo trata como session ID de OpenSession.
        return struct.unpack_from('<I', payload)[0]

    def _init_event_channel(self):
        body = self.guid + struct.pack('<I', self.session_id)
        send_frame(self._evt_sock, ptp.INIT_EVT_REQ, body)
        ptype, _ = recv_frame(self._evt_sock)
        if ptype != ptp.INIT_EVT_ACK:
            raise CameraConnectionError(
                'respuesta inesperada al INIT_EVT_REQ: tipo 0x%02x' % ptype)

    def _probe(self):
        send_frame(self._evt_sock, ptp.PROBE_REQ)
        ptype, _ = recv_frame(self._evt_sock)
        if ptype != ptp.PROBE_RESP:
            raise CameraConnectionError(
                'sin ProbeResponse (tipo 0x%02x); sin probe la sesion MTP se cuelga' % ptype)

    def _open_session(self, session_id):
        res = self.transaction(ptp.OP_OpenSession, [session_id])
        if not res.ok():
            # Fallback: algunos Nikons devuelven un ID que luego rechazan;
            # la WMU usa 0x1 hardcodeado.
            res = self.transaction(ptp.OP_OpenSession, [1])
            if not res.ok():
                raise CameraResponseError(ptp.OP_OpenSession, res.code, res.params)
            self.session_id = 1
        self._opened_at = time.monotonic()
        self._session_open = True

    def close(self, force=False):
        """Cierra la sesion respetando el gap minimo de 1 s post-OpenSession."""
        if self._session_open and not force and self._cmd_sock is not None:
            elapsed = time.monotonic() - (self._opened_at or 0.0)
            if elapsed < MIN_CLOSE_GAP:
                time.sleep(MIN_CLOSE_GAP - elapsed)
            try:
                self.transaction(ptp.OP_CloseSession, [self.session_id])
            except CameraError:
                pass  # si ya cayo la sesion, igual cerramos los sockets
            self._session_open = False
        for attr in ('_cmd_sock', '_evt_sock'):
            sock = getattr(self, attr)
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
                setattr(self, attr, None)

    def __enter__(self):
        if self._cmd_sock is None:
            self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    # --- transacciones ---

    def _next_txid(self):
        txid = self._txid
        self._txid += 1
        return txid

    def transaction(self, opcode, params=()):
        """Operacion con data-in acumulada en memoria. Para payloads grandes
        usar transaction_stream()."""
        chunks = []
        code, resp_params = self._request(opcode, params, chunks.append)
        return TransactionResult(code, resp_params, b''.join(chunks), opcode)

    def transaction_stream(self, opcode, params, write):
        """Operacion con data-in volcada por chunks a write(bytes).
        Evita cargar archivos completos en RAM."""
        code, resp_params = self._request(opcode, params, write)
        return TransactionResult(code, resp_params, b'', opcode)

    def _request(self, opcode, params, sink):
        if self._cmd_sock is None:
            raise CameraConnectionError('sesion no conectada')
        txid = self._next_txid()
        body = struct.pack('<IHI', 1, opcode, txid)  # data_phase=1: sin data-out
        for value in params[:5]:
            body += struct.pack('<I', value & 0xFFFFFFFF)
        try:
            send_frame(self._cmd_sock, ptp.OP_REQ, body)
            return self._read_response(txid, sink)
        except TimeoutError:
            raise CameraConnectionError(
                'timeout de E/S hablando con la camara; puede haberse caido la '
                'sesion Wi-Fi (~30 s idle) o haber congestion; reintentar')
        except (ConnectionClosed, ConnectionResetError, BrokenPipeError) as exc:
            raise CameraConnectionError('conexion perdida con la camara: %s' % exc)

    def _read_response(self, txid, sink):
        while True:
            ptype, body = recv_frame(self._cmd_sock)
            if ptype == ptp.EVENT:
                continue  # evento asincronico intercalado: ignorar
            if ptype == ptp.START_DATA:
                continue  # la longitud total la conoce el caller via ObjectInfo
            if ptype in (ptp.DATA, ptp.END_DATA):
                chunk = body[4:]
                if chunk:
                    sink(chunk)
                if ptype == ptp.END_DATA:
                    continue  # sigue la OperationResponse
                continue
            if ptype == ptp.OP_RESP:
                code, rtx = struct.unpack_from('<HI', body)
                nparams = (len(body) - 6) // 4
                params = list(struct.unpack_from('<%dI' % nparams, body, 6)) if nparams else []
                return code, params
            if ptype == ptp.INIT_FAIL:
                raise CameraConnectionError('la camara mando INIT_FAIL en plena sesion')
            raise TransportError('frame inesperado tipo 0x%02x en stream de comandos' % ptype)

    # --- utilidades ---

    def keepalive(self):
        """GetDeviceInfo barato para que la camara no tire la sesion idle."""
        res = self.transaction(ptp.OP_GetDeviceInfo)
        if not res.ok():
            raise CameraResponseError(ptp.OP_GetDeviceInfo, res.code, res.params)
        return res.data
