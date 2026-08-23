"""Constantes del protocolo MTP-IP (PTP/IP) y MTP.

Fuentes: CIPA DC-007 (capa transporte PTP/IP), PIMA 15470 / ISO 15740
(opcodes y datasets) y extensiones Microsoft MTP.
"""

# --- Tipos de contenedor PTP/IP (primera palabra u32 LE de cada frame) ---
INIT_CMD_REQ = 0x01  # InitCommandRequest (socket de comandos)
INIT_CMD_ACK = 0x02  # InitCommandAck: trae connection number (= session ID Nikon)
INIT_EVT_REQ = 0x03  # InitEventRequest (socket de eventos)
INIT_EVT_ACK = 0x04
INIT_FAIL = 0x05
OP_REQ = 0x06        # OperationRequest
OP_RESP = 0x07       # OperationResponse
EVENT = 0x08         # Evento asincronico (socket de eventos)
START_DATA = 0x09    # StartData: txid + longitud total u64
DATA = 0x0A          # Data: txid + payload
CANCEL = 0x0B
END_DATA = 0x0C      # EndData: txid + payload final opcional
PROBE_REQ = 0x0D     # ProbeRequest (socket de eventos)
PROBE_RESP = 0x0E

CONTAINER_NAMES = {
    INIT_CMD_REQ: 'InitCmdReq', INIT_CMD_ACK: 'InitCmdAck',
    INIT_EVT_REQ: 'InitEvtReq', INIT_EVT_ACK: 'InitEvtAck',
    INIT_FAIL: 'InitFail', OP_REQ: 'OpReq', OP_RESP: 'OpResp',
    EVENT: 'Event', START_DATA: 'StartData', DATA: 'Data',
    CANCEL: 'Cancel', END_DATA: 'EndData',
    PROBE_REQ: 'ProbeReq', PROBE_RESP: 'ProbeResp',
}

# --- Opcodes MTP/PTP ---
OP_GetDeviceInfo = 0x1001
OP_OpenSession = 0x1002
OP_CloseSession = 0x1003
OP_GetStorageIDs = 0x1004
OP_GetStorageInfo = 0x1005
OP_GetObjectHandles = 0x1007
OP_GetObjectInfo = 0x1008
OP_GetObject = 0x1009   # evitado: el firmware Nikon se cuelga con archivos grandes
OP_DeleteObject = 0x100A
OP_GetPartialObject = 0x101B  # via segura para leer archivos por chunks

OPCODE_NAMES = {
    OP_GetDeviceInfo: 'GetDeviceInfo', OP_OpenSession: 'OpenSession',
    OP_CloseSession: 'CloseSession', OP_GetStorageIDs: 'GetStorageIDs',
    OP_GetStorageInfo: 'GetStorageInfo', OP_GetObjectHandles: 'GetObjectHandles',
    OP_GetObjectInfo: 'GetObjectInfo', OP_GetObject: 'GetObject',
    OP_DeleteObject: 'DeleteObject', OP_GetPartialObject: 'GetPartialObject',
}

# --- Codigos de respuesta ---
RESP_OK = 0x2001
RESP_GeneralError = 0x2002
RESP_SessionNotOpen = 0x2003
RESP_InvalidTransactionID = 0x2004
RESP_OperationNotSupported = 0x2005
RESP_InvalidParameter = 0x2006
RESP_IncompleteTransfer = 0x2007
RESP_InvalidStorageID = 0x2008
RESP_InvalidObjectHandle = 0x2009
RESP_AccessDenied = 0x200F
RESP_DeviceBusy = 0x2019

RESPONSE_NAMES = {
    RESP_OK: 'OK', RESP_GeneralError: 'GeneralError',
    RESP_SessionNotOpen: 'SessionNotOpen',
    RESP_InvalidTransactionID: 'InvalidTransactionID',
    RESP_OperationNotSupported: 'OperationNotSupported',
    RESP_InvalidParameter: 'InvalidParameter',
    RESP_IncompleteTransfer: 'IncompleteTransfer',
    RESP_InvalidStorageID: 'InvalidStorageID',
    RESP_InvalidObjectHandle: 'InvalidObjectHandle',
    RESP_AccessDenied: 'AccessDenied', RESP_DeviceBusy: 'DeviceBusy',
}


def response_name(code):
    return RESPONSE_NAMES.get(code, '0x%04X' % code)


# --- Codigos de formato de objeto (PIMA 15470 + MTP) ---
FMT_Association = 0x3001  # carpeta / pseudo-objeto
FMT_AVI = 0x300A          # video del S3700 (AVI Motion-JPEG)
FMT_MPEG = 0x300B
FMT_QT = 0x300D           # QuickTime/MP4 (por si el firmware lo reporta asi)
FMT_UndefinedImage = 0x3800
FMT_JPEG_EXIF = 0x3801    # fotos del S3700
FMT_JFIF = 0x3807

PHOTO_FORMATS = frozenset({FMT_UndefinedImage, FMT_JPEG_EXIF, FMT_JFIF})
VIDEO_FORMATS = frozenset({FMT_AVI, FMT_MPEG, FMT_QT})

FORMAT_NAMES = {
    FMT_Association: 'Association', FMT_AVI: 'AVI', FMT_MPEG: 'MPEG',
    FMT_QT: 'QuickTime', FMT_UndefinedImage: 'UndefinedImage',
    FMT_JPEG_EXIF: 'JPEG/EXIF', FMT_JFIF: 'JFIF',
}


def classify_format(code):
    """Clasifica un ObjectFormatCode en photo/video/other."""
    if code in PHOTO_FORMATS:
        return 'photo'
    if code in VIDEO_FORMATS:
        return 'video'
    return 'other'


def format_name(code):
    return FORMAT_NAMES.get(code, '0x%04X' % code)
