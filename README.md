# nikon-dl

Descargá fotos **y videos** de tu Nikon Coolpix S3700 por Wi-Fi directo al celular, sin PC y sin la app oficial.

`nikon-dl` es un CLI en Python puro (stdlib, cero dependencias) que habla el protocolo MTP-IP de las cámaras Nikon Wi-Fi directamente desde Termux. Existe porque la app Wireless Mobile Utility de Nikon [excluye explícitamente a la S3700 de la descarga de video](https://downloadcenter.nikonimglib.com/en/download/sw/197.html) — este cliente no tiene esa limitación.

## Características

- **Fotos y videos**: descarga JPEG y AVI (Motion-JPEG), clasificados automáticamente
- **Descarga incremental**: el manifest `.nikon-dl-manifest.json` evita re-bajar lo ya descargado (`--new`, default)
- **Resume real**: ante caída de Wi-Fi continúa desde donde cortó gracias a los `.part` y `GetPartialObject` con offset
- **Reconexión automática**: hasta 3 reconexiones por archivo sin perder progreso
- **Verificación**: tamaño contra ObjectInfo + magic bytes (JPEG/AVI) antes de confirmar cada archivo
- **Borrado fail-closed**: dry-run por defecto; `--delete-after` solo borra lo descargado y verificado en esa misma corrida
- **Modo debug**: `--debug` vuelca todo el tráfico MTP-IP en hexadecimal para diagnosticar

## Requisitos

- Python 3.8+ (probado en Termux con 3.14)
- Teléfono conectado al AP Wi-Fi de la cámara

Sin dependencias externas: solo stdlib (`socket`, `struct`, `argparse`, `json`).

## Puesta en marcha

1. En la cámara: menú de reproducción → botón **Wi-Fi**
2. En Android: conectate al AP `Nikon_...`
3. Cuando avise "red sin internet", elegí **mantener conexión**

> [!TIP]
> Antes de transferencias largas corré `termux-wake-lock` y dejá la pantalla encendida: si Android duerme el proceso o cambia a datos móviles, se corta la transferencia (aunque el resume te salva).

### Primera vez: smoke test

Validá el enlace contra la cámara física antes de usar el CLI completo:

```sh
python scripts/smoke_test.py
```

Reporta por etapas: **A** TCP connect → **B** handshake INIT/session ID → **C** sesión completa (probe, OpenSession, GetDeviceInfo, storages, objetos). Si una etapa falla, indica hasta dónde llegó y una pista de recuperación.

## Uso

```sh
python -m nikon_dl list                        # qué hay en la cámara
python -m nikon_dl download                    # baja lo nuevo (incremental)
python -m nikon_dl download --type video       # solo videos
python -m nikon_dl download --all -o ~/storage/downloads
python -m nikon_dl download --delete-after     # baja, verifica y libera la tarjeta
python -m nikon_dl delete                      # dry-run del borrado
python -m nikon_dl delete --older-than 20260101 --yes
```

Los archivos bajan por defecto a `~/storage/downloads` (si existe) o a `./nikon-dl-descargas`.

> [!NOTE]
> La IP de la cámara se **auto-detecta** de la red activa (gateway del AP). La S3700 usa la subred `192.168.0.x` — la cámara está en `192.168.0.1`, no en `192.168.1.1` como los Nikon DSLR documentados por Airnef. Forzala con `--ip` si hace falta.

| Comando | Descripción |
|---|---|
| `list [--json]` | Enumera fotos/videos con handle, tamaño y fecha |
| `download` | Descarga incremental (`--all` fuerza todo, `--type photo\|video\|all`) |
| `delete` | Borra de la cámara (`--handles`, `--older-than AAAAMMDD`, `--type`; exige `--yes`) |

Flags globales: `--ip` (default `192.168.1.1`), `--port` (default `15740`), `--debug`.

> [!WARNING]
> El borrado en la cámara es irreversible. `delete` siempre hace dry-run salvo que pases `--yes`, y `--delete-after` nunca borra archivos cuya verificación falló.

## Cómo funciona

La cámara expone un servidor MTP-IP (PTP/IP, CIPA DC-007) en `192.168.1.1:15740`. El cliente abre dos sockets (comandos + eventos), hace el handshake con probe obligatorio, abre sesión y transfiere con `GetPartialObject` en chunks de 1 MB.

Quirks de firmware Nikon que el código maneja explícitamente:

- `GetObject` se cuelga con archivos grandes (peor con video) → solo `GetPartialObject`
- `CloseSession` a menos de 1 s de `OpenSession` deja la cámara sorda → gap mínimo garantizado
- `GetObjectInfo` puede responder `GeneralError` sobre objetos recién creados → retry 5 s
- Sesión cae tras ~30 s idle → keepalive con `GetDeviceInfo`
- Algunos modelos rechazan el session ID devuelto → fallback automático a `0x1`

Implementación propia desde especificaciones públicas (CIPA/PIMA/MTP); sin código de terceros, licencia MIT.

## Tests

```sh
python -m unittest discover -s tests -v
```

La suite corre contra un simulador TCP (`tests/fake_camera.py`) que implementa el protocolo: handshake, probe, errores configurables, cortes de conexión a mitad de transferencia. No necesita la cámara.

## Estructura

```
nikon_dl/
├── transport.py   frames u32 LE length-prefix, timeouts, hexdump debug
├── ptp.py         constantes MTP/PTP-IP (payloads, opcodes, formatos)
├── session.py     handshake dual-socket, transacciones, close seguro
├── objects.py     enumeración storages/handles, parseo ObjectInfo
├── downloader.py  chunks, resume .part, verificación, manifest
├── deleter.py     borrado fail-closed
└── cli.py         list / download / delete
scripts/
└── smoke_test.py  validación graduada contra la cámara real
tests/
└── fake_camera.py simulador MTP-IP para la suite
```

## Limitaciones conocidas

- La S3700 nunca fue probada por terceros con este protocolo (la Coolpix P340, misma generación WMU, funciona con parámetros default). Si el smoke test no pasa, `--debug` muestra exactamente dónde cortó.
- Velocidad esperada en el orden de ~2 MB/s por Wi-Fi (referencia de otros modelos Nikon).
