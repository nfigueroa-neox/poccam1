# API de la Nube — Referencia completa

API serverless (Vercel + Supabase) que sirve a **dos clientes distintos**:
el **concentrador** (que corre en la planta) y el **sistema externo**
(dashboards, tablets, automatizaciones).

> **URL de producción:** `https://backend-nube.vercel.app`

---

## Índice

| Grupo | Quién llama | ¿Token? |
|---|---|---|
| [A. Sistema externo — consultar](#a-sistema-externo--consultar) | Dashboards, tablets | ❌ No |
| [B. Sistema externo — configurar](#b-sistema-externo--configurar) | Panel de administración | ❌ No |
| [C. Concentrador](#c-concentrador) | El concentrador de la planta | ✅ Sí |
| [D. Utilidades](#d-utilidades) | Cualquiera | ❌ No |

**La separación es deliberada:** el concentrador necesita escribir lo que
ocurre en la planta (y se autentica), mientras que el sistema externo solo
consulta datos y ajusta configuración. **Esta API nunca expone imagen de
cámara** — solo JSON.

---

## A. Sistema externo — consultar

### A.1 · Listar las cámaras

```http
GET /api/camaras
```

Punto de partida: devuelve las cámaras con su **id** y su salud.

| Query | Devuelve |
|---|---|
| *(sin filtro)* | Todas |
| `?estado=ok` | Solo las que funcionan |
| `?estado=alerta` | Solo las que tienen problemas |

```json
{
  "camaras": [
    { "worker_id": "panel-1", "salud": "ok", "motivo": "",
      "con_deteccion": true, "resolucion": [1920, 1080],
      "ia_activa": true, "capturas": 3867, "eventos": 35,
      "visto": "2026-09-17T17:22:31+00:00" }
  ],
  "alertas": [],
  "total": 1,
  "todas_ok": true,
  "endpoints": {
    "config":  "GET|POST /api/camaras/{camara_id}/config",
    "esquema": "GET /api/camaras/{camara_id}/config/schema"
  }
}
```

**Valores de `salud`:**

| Valor | Significado | ¿Hay detección? |
|---|---|---|
| `ok` | Todo bien | ✅ Sí |
| `congelada` | Conectada, pero sin frames nuevos | ❌ No |
| `sin_senal` | No hay ningún frame | ❌ No |
| `worker_caido` | El proceso del worker no responde | ❌ No |

> ⚠️ `camara_viva: true` **no** garantiza que la cámara funcione: el capturador
> conserva el último frame válido. Para saber si hay detección real, usar
> `salud == "ok"` (o el campo `con_deteccion`).

### A.2 · Consultar los análisis de la IA

```http
GET /api/analisis?worker_id=panel-1&limit=50
```

Es el dato principal: el JSON que produjo la IA, guardado tal cual.

| Query | Default | Límite |
|---|---|---|
| `worker_id` | *(todas)* | — |
| `limit` | 50 | máx. 500 |

```json
{
  "analisis": [
    { "id": 42, "worker_id": "panel-1",
      "evento_id": "20260917_134335_121",
      "timestamp": "2026-09-17T13:43:35-03:00",
      "esquema": "estado_equipo_v1",
      "datos": { "en_uso": true, "confianza": 0.88, "notas": "..." },
      "modelo": "deepseek-v4-flash-vision-exp",
      "detail": "low",
      "latencia_ms": 1840,
      "area_px": 102477,
      "score": 0.1258,
      "recibido": "2026-09-17T16:43:36.201Z" }
  ]
}
```

> **`datos` es opaco.** Su contenido lo define el **prompt** de cada worker y
> puede cambiar sin aviso. Para renderizar de forma genérica, iterar sus claves
> en vez de asumir campos fijos.

### A.3 · Consultar los eventos detectados

```http
GET /api/eventos?worker_id=panel-1&limit=50
```

Los **cambios de imagen** que dispararon el análisis (antes de la IA). Útil para
diagnosticar sensibilidad (`score`, `area_px`) sin gastar en análisis.

Mismos parámetros que `A.2`.

```json
{ "eventos": [
    { "id": 88, "worker_id": "panel-1",
      "evento_id": "20260917_134335_121",
      "timestamp": "2026-09-17T13:43:35-03:00",
      "score": 0.1258, "area_px": 102477,
      "area_borde": 0, "metodo": "ssim",
      "recibido": "2026-09-17T16:43:36.150Z" } ] }
```

> Devuelve la fila completa (`select('*')`): si se añaden columnas a la tabla,
> aparecerán aquí sin cambiar la API.

### A.4 · Listar los workers registrados

```http
GET /api/workers
```

Lista simple de cámaras conocidas (se registran solas al mandar su primer
análisis). Si necesitas salud, usar `A.1`.

```json
{ "workers": [
    { "worker_id": "panel-1", "nombre": null,
      "esquema": "estado_equipo_v1",
      "ultima_vista": "2026-09-17T17:22:31+00:00" } ] }
```

> No incluye la salud de la cámara: para eso usar `A.1` (que sí la trae).

---

## B. Sistema externo — configurar

Flujo en tres pasos: **listar** (A.1) → **ver qué se puede tocar** → **leer y
escribir**.

### B.1 · Ver qué campos se pueden modificar

```http
GET /api/camaras/panel-1/config/schema
```

Publica el contrato para que un cliente no dependa de leer el código.

```json
{
  "bloques": {
    "deteccion": {
      "descripcion": "Cómo se decide si hubo un cambio en la imagen",
      "campos": {
        "metodo":       { "tipo": "string",  "valores": ["ssim","diff","mse"] },
        "min_area_px":  { "tipo": "integer",
                          "descripcion": "Píxeles mínimos para disparar un evento. Es el filtro principal." },
        "blur_ksize":   { "tipo": "integer" },
        "frames_estables": { "tipo": "integer" },
        "min_intervalo_eventos": { "tipo": "float" },
        "alinear_imagenes": { "tipo": "boolean" },
        "max_desplazamiento": { "tipo": "float" }
      }
    },
    "captura": { "campos": { "intervalo_segundos": {}, "rotacion": {} } },
    "ia":      { "campos": { "esquema": {}, "model": {}, "detail": {} } }
  },
  "no_modificables": {
    "captura.camara_fuente": "URL de la cámara: es hardware local",
    "captura.fuente": "Tipo de fuente: local",
    "roi": "Se dibuja sobre el video en el front local",
    "prompt": "Protegido con contrasena en el worker"
  }
}
```

### B.2 · Leer la configuración vigente

```http
GET /api/camaras/panel-1/config
```

```json
{ "camara_id": "panel-1",
  "config": { "deteccion": { "min_area_px": 50, "blur_ksize": 5 } },
  "version": 8,
  "actualizado": "2026-09-17T17:52:00Z" }
```

Si nunca se configuró, devuelve `config: {}` y `version: 0` (el worker usa sus
valores locales).

### B.3 · Modificar la configuración

```http
POST /api/camaras/panel-1/config
Content-Type: application/json

{ "config": { "deteccion": { "min_area_px": 120 } } }
```

```json
{ "ok": true, "camara_id": "panel-1", "version": 9,
  "aplicado": { "deteccion": { "min_area_px": 120 } },
  "nota": "El concentrador bajará el cambio en su próximo ciclo..." }
```

**La escritura es una FUSIÓN, no un reemplazo.** Enviar solo
`{"deteccion":{"blur_ksize":9}}` cambia ese campo y **mantiene el resto**.
No necesitas leer antes de escribir.

Acepta tanto `{"config": {...}}` como el payload directo `{...}`.

**Errores:**

| HTTP | Cuándo | Cuerpo |
|---|---|---|
| `400` | Cuerpo no es objeto | `{"error": "se esperaba un objeto de configuración"}` |
| `400` | Solo campos prohibidos | `{"error": "solo se enviaron campos no modificables", "rechazados": [...], "detalle": {...}}` |
| `404` | La cámara no existe | `{"error": "cámara no encontrada", "sugerencia": "Consulta GET /api/camaras..."}` |
| `405` | Método distinto de GET/POST | — |

**Campos prohibidos:** se **rechazan explícitamente** (aparecen en `rechazados`)
en vez de ignorarse en silencio, para que sepas que no tuvieron efecto.

| Campo | Por qué |
|---|---|
| `captura.camara_fuente`, `captura.fuente` | La cámara es hardware local del worker |
| `roi` | Se dibuja sobre el video en el front local |
| `prompt` | Protegido con contraseña (`worker/prompt.key`) |

> ⚠️ **`captura.rotacion` es modificable pero delicado.** Cambiar el giro deja
> el ROI apuntando a otro sitio: tras cambiarlo hay que redibujarlo desde el
> front local.

### B.4 · ¿Cuánto tarda en aplicarse?

```
Sistema externo ──POST──► Supabase (config_workers, version+1)
                              │
                   polling    │ GET .../config?version=N
Concentrador ◄────────────────┘
     │ POST /api/externo/config
     ▼
  Worker (aplica en caliente, sin reiniciar)
```

El concentrador baja la config **por polling**, así que el retraso depende de su
ciclo. El worker la aplica en caliente. Se ve en su log:

```
⚙️ Config aplicada a panel-1
```

---

## C. Concentrador

**Todas exigen** la cabecera de autenticación:

```http
Authorization: Bearer <CONCENTRADOR_TOKEN>
```

Sin ella: `401 {"error": "token inválido"}`.

### C.1 · Bajar la configuración

```http
GET /api/concentrador/config?version=N
```

`version` es la última que el concentrador conoce. Si no cambió, responde
**`304` sin cuerpo** (ahorro de ancho de banda). En el primer arranque se envía
`version=-1` o `0`.

```json
{ "version": 8,
  "workers": {
    "panel-1": { "captura": { "intervalo_segundos": 0.5, "rotacion": 90 },
                 "deteccion": { "min_area_px": 50, "blur_ksize": 5 },
                 "ia": { "esquema": "estado_equipo_v1" } } } }
```

> La `version` global es la **suma** de las versiones por worker: cambia si
> cualquiera de ellos cambia.

### C.2 · Subir un análisis de la IA

```http
POST /api/concentrador/analisis
```

```json
{ "worker_id": "panel-1",
  "evento_id": "20260917_134335_121",
  "timestamp": "2026-09-17T13:43:35-03:00",
  "esquema": "estado_equipo_v1",
  "datos": { "en_uso": true, "confianza": 0.88 },
  "meta": { "modelo": "deepseek-v4-flash-vision-exp", "detail": "low",
            "latencia_ms": 1840, "area_px": 102477, "score": 0.1258 } }
```

- **Auto-registra el worker** si es la primera vez que aparece.
- **Idempotente**: `analisis.evento_id` es `UNIQUE`, así que un reintento desde
  la cola offline no duplica.
- `400` si falta `worker_id`.

### C.3 · Reportar estado (heartbeat)

```http
POST /api/concentrador/estado
```

```json
{ "concentrador_id": "concentrador-1",
  "timestamp": "2026-09-17T17:22:31+00:00",
  "workers": {
    "panel-1": { "camara_viva": true, "camara_salud": "ok", "camara_motivo": "",
                 "resolucion": [1920, 1080], "ia_activa": true,
                 "capturas": 3867, "eventos": 35 } },
  "alertas": [] }
```

Permite que el dashboard remoto muestre el estado aunque el concentrador no sea
alcanzable. Persiste en `heartbeats` y actualiza `workers.camara_salud`.

`alertas` es un atajo pre-filtrado con las cámaras que **no** están `ok`.

### C.4 · Publicar configuración local

```http
POST /api/concentrador/config
```

Mismo formato que `C.1`. Sirve para que los cambios hechos desde el **front
local** queden registrados en la nube (fuente de verdad).

---

## D. Utilidades

### D.1 · Salud del servicio

```http
GET /api/salud
GET /api/health
```

```json
{ "ok": true, "servicio": "poccam-backend-nube" }
```

### D.2 · Notas de integración

**Polling vs Realtime.** Todas las rutas de la sección A son GET: el sistema
externo **consulta**, no recibe push. Para un dashboard en vivo hay dos caminos:

| Camino | Cómo | Cuándo conviene |
|---|---|---|
| **Polling** | `GET /api/analisis` cada N s | Simple, pocos clientes |
| **Supabase Realtime** | Suscribirse a la tabla `analisis` | Muchos clientes, latencia baja |

**Reintentos.** Ante `5xx` reintentar con backoff (1s, 2s, 5s). Los `4xx` no se
reintentan sin corregir el cuerpo.

**Idempotencia.** `analisis` y `eventos` tienen `evento_id UNIQUE`: reenviar el
mismo evento no duplica.

---

## Resumen de endpoints

| Método | Ruta | Grupo | Token |
|---|---|---|---|
| `GET` | `/api/camaras` | A | — |
| `GET` | `/api/analisis?worker_id=&limit=` | A | — |
| `GET` | `/api/eventos?worker_id=&limit=` | A | — |
| `GET` | `/api/workers` | A | — |
| `GET` | `/api/camaras/{id}/config/schema` | B | — |
| `GET` | `/api/camaras/{id}/config` | B | — |
| `POST` | `/api/camaras/{id}/config` | B | — |
| `GET` | `/api/concentrador/config?version=N` | C | 🔑 |
| `POST` | `/api/concentrador/analisis` | C | 🔑 |
| `POST` | `/api/concentrador/estado` | C | 🔑 |
| `POST` | `/api/concentrador/config` | C | 🔑 |
| `GET` | `/api/salud` · `/api/health` | D | — |

---

## Documentos relacionados

| Documento | Contenido |
|---|---|
| [`CONTRATO_NUBE.md`](CONTRATO_NUBE.md) | El contrato conceptual, con ejemplos por worker |
| [`API.md`](API.md) | La API **del worker** (la que corre junto a la cámara) |
| [`INTEGRACION_WEIZHOU.md`](INTEGRACION_WEIZHOU.md) | El otro destino externo (push directo) |
| [`DESPLIEGUE.md`](DESPLIEGUE.md) | Puesta en marcha y problemas conocidos |
