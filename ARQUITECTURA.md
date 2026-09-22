# Arquitectura del Backend Detector de Cambios

Documento técnico: cómo está construido el proyecto, cómo se comunican el
frontend y el backend, y cómo se extiende. Para la guía de uso ver `README.md`.

> **Alcance de este documento: el WORKER** (el proceso que corre junto a la
> cámara). El **concentrador** —que agrega varios workers, sirve el panel web
> unificado y habla con los sistemas externos (nube y Weizhou)— se documenta en
> [`concentrador/README.md`](concentrador/README.md) y en
> [`CONTRATO_NUBE.md`](CONTRATO_NUBE.md).

## 1. Visión general

**Un proceso Python por cámara.** El worker ya **no sirve el panel HTML**: expone
únicamente su **API JSON** y el **stream de video**. La interfaz la sirve el
**concentrador**, que hace proxy hacia el worker activo (ver
`concentrador/backend/panel.py`).

Tres hilos conviven en el proceso:

```mermaid
flowchart LR
    M["Hilo principal: MonitorBackend (bucle)"] --> C["Capturador"]
    M --> D["DetectorCambios"]
    M --> R["RegistradorEventos"]
    W["Hilo web: Flask (daemon)"] --> C
    W --> D
    W --> CF["Config (compartida)"]
    J["Hilo MJPEG: consumidor del stream (daemon)"] --> C
```

- **Hilo principal**: el bucle `capturar → detectar → registrar`.
- **Hilo web** (daemon): Flask con la **API JSON** (config, ROI, log, IA) y el
  stream de video. Ya no sirve HTML.
- **Hilo del capturador MJPEG** (daemon): solo existe con fuentes HTTP MJPEG;
  consume el stream en segundo plano y conserva el frame más reciente.

> Los hilos comparten **el mismo objeto `DetectorCambios`**, **el mismo
> `Config`** y **el mismo capturador**. La web actualiza parámetros en caliente
> mutando esos objetos; el monitor los lee en cada ciclo. Las lecturas/escrituras
> simples de atributos son atómicas bajo el GIL de CPython, suficiente para este
> caso (no hay estados compuestos a proteger).

## 2. Módulos

| Módulo | Responsabilidad |
|---|---|
| `main.py` | `MonitorBackend`: bucle principal, logging, arranca la web en un hilo daemon, resumen al detener |
| `backend/config.py` | `Config` (dataclass): carga de `config.yaml`, serialización (`a_dict`/`guardar`) para persistir cambios desde la web |
| `backend/capturador.py` | Fuentes de imagen: `CapturadorPantalla` (mss), `CapturadorCamara` (OpenCV USB/RTSP con reconexión), `CapturadorMJPEG` (hilo + frame más reciente). Factory `crear_capturador(config)` |
| `backend/detector.py` | `DetectorCambios`: pipeline por frame (ROI → blur → alinear → diff/ssim/mse → umbral → área → estabilidad). Referencia estable. `actualizar()` para cambios en caliente |
| `backend/web.py` | **API HTTP del worker** (JSON + video). Ya **no** sirve el panel: el HTML vive en `compartido/panel.html` y lo sirve el concentrador. Persiste el ROI (`roi.json`) y expone los SSE que consume el panel |
| `backend/registrador.py` | `RegistradorEventos`: escribe `eventos.jsonl` (append), guarda imágenes en `capturas_cambio/`, aplica `max_imagenes`, notifica al panel (SSE) |

Dependencias entre módulos:

```mermaid
flowchart LR
    main --> capturador
    main --> config
    main --> detector
    main --> registrador
    main --> web
    detector --> web["web (cargar_roi)"]
    registrador --> web["web (notificar_captura_nueva)"]
    registrador --> config
```

> Nota de acoplamiento: `detector.py` y `registrador.py` importan funciones de
> `web.py` (`cargar_roi`, `notificar_captura_nueva`). Funciona porque `web.py`
> no importa esos módulos a nivel de módulo (los recibe por parámetro en
> `crear_app`). Ver "Deudas técnicas" §8.

## 3. El bucle del monitor (`main.py`)

```
while True:
    imagen = capturador.capturar()          # siempre el frame más reciente
    resultado = detector.procesar(imagen)   # pipeline completo
    if resultado["hubo_cambio"]:
        if pasó min_intervalo_eventos:
            registrador.registrar(evento)   # imagen + JSONL + aviso SSE
    esperar hasta completar intervalo_segundos
```

## 4. Flujo de datos

### Ruta de detección (por frame)

```
cámara → CapturadorMJPEG (hilo) → capturar() → DetectorCambios.procesar()
  → ROI (roi.json, recargado en cada llamada)
  → blur en color
  → alineación (si alinear_imagenes): correlación de fase + shift + margen
  → máscara de diferencia (ssim/diff/mse) con umbral de píxel
  → excluir borde + decidir por área (min_area_px)
  → filtro temporal (frames_estables)
  → RegistradorEventos.registrar()  (solo si cambio confirmado)
  → notificar_captura_nueva() → SSE → navegador
```

### Ruta de configuración (en vivo)

```
navegador → POST /api/config → valida → detector.actualizar(params)
  → muta Config compartida → config.guardar() → config.yaml
```

El monitor lee `config.intervalo_segundos` y `config.min_intervalo_eventos` en
cada ciclo; el detector usa sus propios atributos (actualizados por la web).
Por eso los cambios se aplican **sin reiniciar**.

### Ruta del ROI

```
canvas (mouse) → POST /api/roi → roi.json → el detector recarga el archivo
  en cada procesar() (cargar_roi) → se aplica al siguiente frame
```

### Ruta de la UI (sin polling)

```
GET /                → JSON informativo (servicio, worker_id, dónde está el panel)
GET /video           → MJPEG en vivo (con ROI dibujado sobre una COPIA del frame)
GET /api/ultimas     → par antes/después del último evento, con dimensiones del ROI
GET /api/log         → eventos + sugerencias de ajuste (bajo demanda)
GET /api/eventos     → SSE: avisa cuando hay captura nueva (actualiza la UI)
GET /api/estado      → SSE: desplazamiento estimado + margen + interior/borde
GET/POST/DELETE /api/roi
GET/POST /api/config
DELETE /api/log      → limpia eventos.jsonl
GET /capturas/<nombre> → sirve una imagen (protegido contra rutas fuera de la carpeta)
```

> El panel web consume estas rutas **a través del proxy del concentrador**.

## 5. Contrato de la API

| Endpoint | Método | Entrada | Salida |
|---|---|---|---|
| `/api/roi` | GET | — | `{"region": [x,y,w,h] \| null}` |
| `/api/roi` | POST | `{"region":[x,y,w,h]}` | `{"ok":true}` |
| `/api/roi` | DELETE | — | `{"ok":true}` |
| `/api/ultimas` | GET | — | `{"antes":{archivo,url,fecha,resolucion,pixeles}\|null, "despues":{...}\|null, "hay_evento":bool}` |
| `/api/log` | GET | — | `{"min_area_px":N, "eventos":[...]}` con sugerencias |
| `/api/log` | DELETE | — | `{"ok":true}` (vacía el JSONL) |
| `/api/config` | GET | — | parámetros completos (`config.a_dict()`) + `presets` + `camara_resolucion` |
| `/api/config` | POST | `{"captura":{...},"deteccion":{...}}` (parcial) | `{"ok":true}` · `400` valor inválido · `502` si la cámara nueva no abre |
| `/api/estado-sistema` | GET | — | estado completo (solo lectura): `{config, runtime{camara_viva, camara_resolucion, preset_aplicado, rotacion_efectiva, roi, ia_activa, capturas, cambios}}` |
| `/api/rotacion` | POST | `{"rotacion": 0\|90\|180\|270}` | `{"ok":true,"rotacion":N}` |
| `/api/eventos` | GET | — | SSE: `data: <contador>` cuando hay captura nueva |
| `/api/config-eventos` | GET | — | SSE: avisa cuando la CONFIGURACIÓN cambió (panel o cliente externo) |
| `/api/estado` | GET | — | SSE (cada ~1 s): `{"alinear":bool, "dy", "dx", "activo", "margen", "area_interior", "area_borde"}` |
| `/video` | GET | — | MJPEG multipart (**solo panel interno; NO forma parte de la API pública**) |
| `/capturas/<nombre>` | GET | — | PNG del evento (**solo panel interno; NO forma parte de la API pública**) |

El frontend usa `EventSource` para `/api/eventos`, `/api/config-eventos` y
`/api/estado` (notificación push) y `fetch` para el resto. **No hay polling**:
las capturas y la configuración se actualizan solo cuando el servidor avisa.

### API pública de configuración (para clientes externos)

La API de configuración/estado (solo JSON) es consumible desde otro equipo de
la red. **No expone imagen de cámara**: `/video` y `/capturas/*` son del panel
interno.

- **Consultar parámetros**: `GET /api/config` (o `GET /api/estado-sistema`
  para incluir el estado de runtime).
- **Sobrescribir parámetros**: `POST /api/config` con un subconjunto parcial.
  Acepta **todos** los parámetros de captura (`intervalo_segundos`,
  `rotacion`, `reconectar_segundos`, `nombre_camara`) y de detección.
- **Cambiar la cámara**: incluir `camara_fuente` (y opcionalmente `fuente`)
  recrea el capturador **en vivo** (sin reiniciar el proceso). Si la cámara
  nueva no abre, se mantiene la anterior y responde `502`.
- **Sincronización**: todo cambio dispara el SSE `/api/config-eventos`, que el
  panel escucha para reflejar los cambios externos automáticamente (sin
  recargar la página).

Ejemplo de inyección desde otro equipo:

```bash
curl -X POST http://IP-DEL-SERVIDOR:5000/api/config \
  -H "Content-Type: application/json" \
  -d '{"camara_fuente": "http://192.168.1.50:8080/video", "captura": {"rotacion": 90, "intervalo_segundos": 1.0}}'
```

> Sin autenticación en esta PoC: cualquier equipo de la red local puede leer y
> escribir la configuración. No exponer fuera de la red local.

## 6. Persistencia

| Archivo | Escritor | Formato | Notas |
|---|---|---|---|
| `config.yaml` | web (`config.guardar`) | YAML | Se **regenera completo** al guardar desde la web (se pierden comentarios manuales) |
| `eventos.jsonl` | registrador | JSON por línea (append) | Toda la historia; la web lee las últimas 30 líneas |
| `capturas_cambio/` | registrador | PNG con timestamp en el nombre | Límite `max_imagenes` (elimina las más antiguas por orden de nombre) |
| `roi.json` | web | JSON | Se recarga en cada frame |

Todos ignorados por git (ver `.gitignore`).

## 7. Decisiones de diseño clave

1. **Capturador MJPEG con hilo propio**: OpenCV lee los streams MJPEG
   acumulando frames en un buffer interno (retraso de segundos). Este
   capturador consume el stream en un hilo y conserva **solo el último frame**,
   por lo que `capturar()` devuelve al instante el frame más fresco → la
   detección responde en milisegundos.
2. **El ROI se dibuja sobre una copia del frame** (`frame.copy()` en
   `generar_video`): dibujar el rectángulo sobre el frame compartido
   **contaminaba lo que analizaba el detector** (fue la causa histórica de
   falsos positivos de ~1150 px).
3. **Detección en color, no en gris**: un LED rojo sobre fondo oscuro puede
   tener el mismo valor de gris que el fondo; comparando los 3 canales no se
   pierde esa información.
4. **Referencia estable**: se compara contra la última imagen confirmada sin
   cambio (no contra el frame anterior). Se actualiza solo cuando no hay cambio
   o cuando un cambio se confirma — así un parpadeo no envenena la referencia.
5. **La vibración se compensa ANTES de comparar** (correlación de fase +
   shift), y el **borde** rellenado por el shift se **excluye** de la decisión
   (`margen`) y se reporta aparte (`area_borde`).
6. **`umbral` (0-1) = sensibilidad a nivel píxel** para ssim/diff (más bajo =
   más sensible) y corte de score para mse. `min_area_px` decide por área.
7. **Parámetros en vivo**: la web recibe el mismo objeto `Config` y
   `DetectorCambios` que el monitor; los muta y los persiste. El detector
   expone `actualizar()` para validar y aplicar cambios de detección.
8. **Panel compartido**: el HTML/JS del panel vive en `compartido/panel.html`
   (un solo archivo). Lo sirve el **concentrador**; el worker ya no sirve HTML.

## 8. Deudas técnicas / posibles refactors

- **`cargar_roi` vive en `web.py` pero lo usa el detector** → moverlo a un
  módulo compartido (p. ej. `backend/roi.py`) para invertir la dependencia.
- **Servidor de desarrollo de Flask** (`app.run`): suficiente para 1-2 clientes
  y 1 fps; para producción usar `waitress` (Windows) o `gunicorn` detrás de
  un proxy. El worker usa `threaded=True` porque el panel abre varias
  conexiones SSE simultáneas.
- **Límite de conexiones del navegador**: el panel abre 4 SSE permanentes +
  el video MJPEG. Con HTTP/1.1 el navegador permite 6 por host:puerto, así que
  en el concentrador quedan casi agotadas. Si aparecen bloqueos, el video puede
  servirse directo desde el worker (host distinto → contador propio).
- **Eventos en memoria** (`leer_log_eventos` lee el archivo completo): con
  millones de líneas convendrá un índice o base de datos ligera (SQLite).
- **Multi-cámara**: hoy es un proceso por cámara (puertos distintos), unificados
  en un solo panel por el concentrador.

## 9. Cómo extender

1. **IA bajo demanda**: ~~pendiente~~ **ya implementado**. El `RegistradorEventos`
   lanza el análisis en un hilo aparte cuando hay cambio y `ia.enabled` está
   activo (ver `backend/ia.py` y la sección `ia:` del YAML). El esquema de
   salida lo define el prompt (`worker/ia_prompt.txt`).
2. **Verificación anti-oculsión** (**evaluada y descartada**): se implementó y
   se eliminó, porque solo reportaba en el log sin descartar el análisis, así
   que no daba el ahorro buscado. Si se reañade, la vía barata es una heurística
   sobre el área del cambio (el detector ya la calcula): marcar los cambios que
   cubren casi todo el ROI. Ver la nota en `README.md` §Camino a producción.
3. **API externa**: ~~pendiente~~ **implementada**. El worker expone su API JSON
   (`backend/web.py`) y el concentrador la agrega para los sistemas externos
   (ver `CONTRATO_NUBE.md`).
4. **Shutter rápido en producción**: la vibración física durante la exposición
   causa desenfoque de movimiento que la alineación **no** puede corregir; se
   mitiga con exposición corta en la cámara y montaje amortiguado.
