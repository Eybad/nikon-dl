"""Simulador minimo de camara Nikon MTP-IP para correr los tests sin hardware.

Implementa solo lo que los tests ejercitan: handshake dual-socket, probe,
GetDeviceInfo, OpenSession (con modo "rechaza el ID devuelto" para probar el
fallback a 0x1), GetStorageIDs, GetObjectHandles, GetObjectInfo (con errores
GeneralError configurables), GetPartialObject (con corte abrupto configurable
para probar resume/reconexion), DeleteObject y CloseSession.
"""

import socket
import struct
import threading

from nikon_dl import ptp
from nikon_dl.transport import ConnectionClosed, recv_frame, send_frame


# --- encoders de datasets (solo para tests/fake) ---

def enc_str(text):
    """String PTP real: u8 count (unidades INCLUYENDO null) + UTF-16LE.

    Verificado contra el firmware del S3700 en campo: los strings vienen
    en UTF-16LE ('S3700' = 53 00 33 00 37 00 30 00 30 00), igual que
    strutil de airnef (stringToUtf16ByteArray).
    """
    units = (text or '').encode('utf-16-le')
    return bytes([len(units) // 2 + 1]) + units + b'\x00\x00'


def enc_u16_array(values):
    return struct.pack('<I', len(values)) \
        + b''.join(struct.pack('<H', v) for v in values)


def enc_u32_array(values):
    return struct.pack('<I', len(values)) \
        + b''.join(struct.pack('<I', v) for v in values)


ALL_OPS = [ptp.OP_GetDeviceInfo, ptp.OP_OpenSession, ptp.OP_CloseSession,
           ptp.OP_GetStorageIDs, ptp.OP_GetObjectHandles, ptp.OP_GetObjectInfo,
           ptp.OP_DeleteObject, ptp.OP_GetPartialObject]


def enc_deviceinfo(model='NIKON COOLPIX FAKE',
                   vendor_ext_desc='microsoft.com: 1.0;',
                   manufacturer='Nikon', device_version='1.0'):
    body = struct.pack('<H', 100)               # StandardVersion
    body += struct.pack('<I', 6)                # VendorExtensionID (Microsoft)
    body += struct.pack('<H', 100)              # VendorExtensionVersion
    body += enc_str(vendor_ext_desc)            # VendorExtensionDesc
    body += struct.pack('<H', 0)                # FunctionalMode
    body += enc_u16_array(ALL_OPS)              # OperationsSupported
    body += enc_u16_array([])                   # EventsSupported
    body += enc_u16_array([])                   # DevicePropertiesSupported
    body += enc_u16_array([])                   # CaptureFormats
    body += enc_u16_array([ptp.FMT_JPEG_EXIF, ptp.FMT_AVI])
    body += enc_str(manufacturer) + enc_str(model) \
        + enc_str(device_version) + enc_str('SNFAKE001')
    return body


def enc_objectinfo(obj):
    body = struct.pack('<IHHIHIIIIIIIHII',
                       obj['storage'], obj['format'], 0, obj['size'],
                       0, 0, 0, 0, 0, 0, 0, obj.get('parent', 0),
                       0, 0, 0)
    body += enc_str(obj['name'])
    body += enc_str(obj.get('capture', ''))
    body += enc_str(obj.get('modified', ''))
    body += enc_str('')
    return body


def deterministic_content(size):
    base = bytes(range(256))
    return (base * (size // 256 + 1))[:size]


class FakeCamera(object):
    """Servidor TCP con el comportamiento minimo esperado por nikon-dl."""

    def __init__(self, session_id=0x11223344, reject_returned_session_id=False,
                 fail_init=False, skip_probe_response=False,
                 objectinfo_general_errors=0, truncate_partial_at=None,
                 serve_size_deficit=0):
        self.session_id = session_id
        self.reject_returned_session_id = reject_returned_session_id
        self.fail_init = fail_init
        self.skip_probe_response = skip_probe_response
        self.objectinfo_general_errors = objectinfo_general_errors
        # one-shot: al alcanzar el umbral tira la conexion y se desactiva
        self.truncate_partial_at = truncate_partial_at
        self.serve_size_deficit = serve_size_deficit

        self.objects = {}
        self.deleted = set()
        self.received_opcodes = []
        self.fallback_session_used = False
        self.close_requested = False
        self._served_partial_bytes = 0
        self._lock = threading.Lock()
        self._conns = []
        self._threads = []

    def add_object(self, handle, name, size, fmt=ptp.FMT_JPEG_EXIF,
                   storage=0x00010001, capture='20260801T101500',
                   modified='20260801T101500'):
        content_len = max(0, size - self.serve_size_deficit)
        self.objects[handle] = {
            'handle': handle, 'name': name, 'size': size, 'format': fmt,
            'storage': storage, 'capture': capture, 'modified': modified,
            'content': deterministic_content(content_len),
        }

    # --- ciclo de vida del servidor ---

    def start(self):
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind(('127.0.0.1', 0))
        self._listener.listen(4)
        self.port = self._listener.getsockname()[1]
        self._accepting = True
        self._accept_thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._accept_thread.start()

    def stop(self):
        self._accepting = False
        try:
            self._listener.close()
        except OSError:
            pass
        for conn in list(self._conns):
            try:
                conn.close()
            except OSError:
                pass
        for thread in self._threads:
            thread.join(timeout=2)
        self._conns.clear()

    def _accept_loop(self):
        while self._accepting:
            try:
                conn, _ = self._listener.accept()
            except OSError:
                return
            self._conns.append(conn)
            thread = threading.Thread(target=self._serve, args=(conn,), daemon=True)
            self._threads.append(thread)
            thread.start()

    # --- protocolo ---

    def _serve(self, conn):
        try:
            while True:
                ptype, body = recv_frame(conn)
                if ptype == ptp.INIT_CMD_REQ:
                    if self.fail_init:
                        send_frame(conn, ptp.INIT_FAIL)
                        return
                    ack = struct.pack('<I', self.session_id) + body[:16] \
                        + enc_str('FakeNikon') + struct.pack('<I', 0x00010000)
                    send_frame(conn, ptp.INIT_CMD_ACK, ack)
                elif ptype == ptp.INIT_EVT_REQ:
                    # el firmware Nikon real (WU-1a/S3700, verificado contra
                    # airnef y gphoto2) espera SOLO session ID, sin GUID
                    if len(body) != 4:
                        return  # lo ignora: cliente con formato DC-007 literal
                    send_frame(conn, ptp.INIT_EVT_ACK)
                elif ptype == ptp.PROBE_REQ:
                    if self.skip_probe_response:
                        return  # se queda mudo: el cliente debe colgar/timeout
                    send_frame(conn, ptp.PROBE_RESP)
                elif ptype == ptp.OP_REQ:
                    self._handle_op(conn, body)
        except (ConnectionClosed, ConnectionError, OSError):
            pass

    def _handle_op(self, conn, body):
        data_phase, opcode, txid = struct.unpack_from('<IHI', body)
        params = list(struct.unpack_from('<%dI' % ((len(body) - 10) // 4), body, 10))
        with self._lock:
            self.received_opcodes.append(opcode)
        handler = {
            ptp.OP_GetDeviceInfo: self._op_getdeviceinfo,
            ptp.OP_OpenSession: self._op_opensession,
            ptp.OP_CloseSession: self._op_closesession,
            ptp.OP_GetStorageIDs: self._op_getstorageids,
            ptp.OP_GetObjectHandles: self._op_getobjecthandles,
            ptp.OP_GetObjectInfo: self._op_getobjectinfo,
            ptp.OP_GetPartialObject: self._op_getpartialobject,
            ptp.OP_DeleteObject: self._op_deleteobject,
        }.get(opcode)
        if handler is None:
            self._op_resp(conn, txid, ptp.RESP_OperationNotSupported)
            return
        handler(conn, txid, params)

    def _op_resp(self, conn, txid, code, params=()):
        body = struct.pack('<HI', code, txid) \
            + b''.join(struct.pack('<I', value) for value in params)
        send_frame(conn, ptp.OP_RESP, body)

    def _send_data(self, conn, txid, data):
        send_frame(conn, ptp.START_DATA, struct.pack('<IQ', txid, len(data)))
        offset = 0
        while offset < len(data):
            chunk = data[offset:offset + 256 * 1024]
            send_frame(conn, ptp.DATA, struct.pack('<I', txid) + chunk)
            offset += len(chunk)
        send_frame(conn, ptp.END_DATA, struct.pack('<I', txid))

    def _op_getdeviceinfo(self, conn, txid, params):
        self._send_data(conn, txid, enc_deviceinfo())
        self._op_resp(conn, txid, ptp.RESP_OK)

    def _op_opensession(self, conn, txid, params):
        requested = params[0] if params else 0
        if requested == self.session_id and not self.reject_returned_session_id:
            self._op_resp(conn, txid, ptp.RESP_OK)
        elif requested == 1:
            with self._lock:
                self.fallback_session_used = True
            self._op_resp(conn, txid, ptp.RESP_OK)
        else:
            self._op_resp(conn, txid, ptp.RESP_GeneralError)

    def _op_closesession(self, conn, txid, params):
        with self._lock:
            self.close_requested = True
        self._op_resp(conn, txid, ptp.RESP_OK)

    def _op_getstorageids(self, conn, txid, params):
        storages = sorted({obj['storage'] for obj in self.objects.values()}) or [0x00010001]
        self._send_data(conn, txid, enc_u32_array(storages))
        self._op_resp(conn, txid, ptp.RESP_OK)

    def _op_getobjecthandles(self, conn, txid, params):
        storage = params[0] if params else 0
        handles = [obj['handle'] for obj in self.objects.values()
                   if obj['handle'] not in self.deleted
                   and (storage == 0 or obj['storage'] == storage)]
        self._send_data(conn, txid, enc_u32_array(handles))
        self._op_resp(conn, txid, ptp.RESP_OK)

    def _op_getobjectinfo(self, conn, txid, params):
        handle = params[0]
        if self.objectinfo_general_errors > 0:
            with self._lock:
                self.objectinfo_general_errors -= 1
            self._op_resp(conn, txid, ptp.RESP_GeneralError)
            return
        obj = self.objects.get(handle)
        if obj is None or handle in self.deleted:
            self._op_resp(conn, txid, ptp.RESP_InvalidObjectHandle)
            return
        self._send_data(conn, txid, enc_objectinfo(obj))
        self._op_resp(conn, txid, ptp.RESP_OK)

    def _op_getpartialobject(self, conn, txid, params):
        handle, offset, max_len = params[0], params[1], params[2]
        obj = self.objects.get(handle)
        if obj is None or handle in self.deleted:
            self._op_resp(conn, txid, ptp.RESP_InvalidObjectHandle)
            return
        chunk = obj['content'][offset:offset + max_len]
        if self.truncate_partial_at is not None:
            self._served_partial_bytes += len(chunk)
            if self._served_partial_bytes >= self.truncate_partial_at:
                # simula caida de Wi-Fi a mitad de transferencia: manda este
                # chunk y tira la conexion sin OperationResponse (one-shot)
                self.truncate_partial_at = None
                data = struct.pack('<I', txid) + chunk
                send_frame(conn, ptp.DATA, data)
                conn.close()
                raise ConnectionClosed('simulada')
        self._send_data(conn, txid, chunk)
        self._op_resp(conn, txid, ptp.RESP_OK)

    def _op_deleteobject(self, conn, txid, params):
        handle = params[0]
        if handle not in self.objects:
            self._op_resp(conn, txid, ptp.RESP_InvalidObjectHandle)
            return
        with self._lock:
            self.deleted.add(handle)
        self._op_resp(conn, txid, ptp.RESP_OK)
