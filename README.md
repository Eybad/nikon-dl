# nikon-dl

Cliente CLI en Python 3 (stdlib only) para descargar **fotos y videos** del
Nikon Coolpix S3700 por Wi-Fi (protocolo MTP-IP / PTP-IP, puerto TCP 15740),
directamente a un celular con Termux. La app oficial Wireless Mobile Utility
(WMU) excluye explicitamente a la S3700 de la descarga de video; este cliente
habla el protocolo directamente.

Implementacion propia a partir de especificaciones publicas (CIPA DC-007 /
PIMA 15470 / MTP) y quirks de firmware Nikon documentados. Sin codigo de
terceros. Licencia MIT.

## Requisitos

- Python 3.8+ (probado con 3.14 en Termux). Sin dependencias externas.
- El telefono conectado al AP Wi-Fi de la camara.

## Conexion Wi-Fi (paso manual en Android)

1. En la camara: activar Wi-Fi (menu de reproduccion -> boton Wi-Fi).
2. En Android: Ajustes > Wi-Fi > conectar al AP `Nikon_...`.
3. Cuando Android avise "red sin internet", elegir **mantener conexion**
   (o desactivar "cambiar automaticamente a datos moviles").
4. En Termux, antes de transferencias largas:
   ```
   termux-wake-lock
   ```
   y mantener la pantalla encendida para que Android no duerma el proceso.

La IP de la camara es `192.168.1.1` (default del firmware).

## Uso

```bash
# desde la raiz del proyecto
python -m nikon_dl list                       # enumera fotos/videos en la camara
python -m nikon_dl download                   # baja lo nuevo (incremental)
python -m nikon_dl download --all --out DIR   # baja todo
python -m nikon_dl download --type video      # solo videos (AVI)
python -m nikon_dl download --delete-after    # baja + verifica + borra de la camara
python -m nikon_dl delete                     # dry-run: lista lo que borraria
python -m nikon_dl delete --handles 0x0009 --yes
```

Flags utiles:

- `--ip` : IP de la camara (default `192.168.1.1`).
- `--debug` : vuelca en hexadecimal todo el trafico MTP-IP.
- `download --new` (default): usa `.nikon-dl-manifest.json` en el directorio
  de salida para no re-bajar archivos ya descargados.

## Smoke test (primera vez)

Antes de usar el CLI completo, validar el enlace contra la camara fisica:

```bash
python scripts/smoke_test.py [--ip 192.168.1.1]
```

Reporta por etapas: (a) TCP connect, (b) handshake INIT_CMD_REQ/ACK +
session ID, (c) sesion completa (eventos + probe + OpenSession +
GetDeviceInfo). Si falla una etapa, indica hasta donde llego.

## Diseno / quirks Nikon manejados

- Descarga **solo** con `GetPartialObject` en chunks de 1 MB: el firmware
  Nikon se cuelga con `GetObject` en archivos grandes (peor con video).
- `CloseSession` nunca se emite a menos de 1 s despues de `OpenSession`
  (bug: deja a la camara sorda hasta ciclar Wi-Fi).
- `GetObjectInfo` reintenta ante `GeneralError` hasta 5 s (objetos recien
  creados).
- Keepalive con `GetDeviceInfo` cada >=5 s durante esperas largas (la sesion
  cae tras ~30 s idle).
- Probe obligatorio en el socket de eventos tras el handshake.
- Fallback de session ID a `0x1` si la camara rechaza el ID devuelto.
- Borrado fail-closed: dry-run por defecto; `--delete-after` solo borra
  objetos descargados y verificados en esa misma corrida.

## Estructura

```
nikon_dl/
  transport.py   frames u32 LE length-prefix, timeouts, hexdump debug
  ptp.py         constantes MTP/PTP-IP (payloads, opcodes, formatos)
  session.py     handshake 2 sockets, transacciones, keepalive, close seguro
  objects.py     enumeracion storages/handles, parseo ObjectInfo
  downloader.py  GetPartialObject chunked, resume .part, verificacion, manifest
  deleter.py     DeleteObject fail-closed
scripts/
  smoke_test.py  validacion graduada contra la camara real
tests/
  fake_camera.py simulador TCP minimo del protocolo (para tests sin hardware)
```

## Tests

```bash
python -m unittest discover -s tests -v
```

Los tests corren contra un simulador local; no necesitan la camara.

## Limitaciones conocidas

- La S3700 nunca fue probada por terceros con este protocolo (la Coolpix
  P340, misma generacion WMU, funciona con parametros default). Si el smoke
  test no pasa, `--debug` vuelca el trafico para diagnosticar.
- Velocidad esperada: orden de ~2 MB/s por Wi-Fi (referencia de otros modelos
  Nikon).
