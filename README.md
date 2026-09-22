# Backend Detector de Cambios para Paneles Industriales

Backend que pide imágenes a una cámara a intervalos configurables, compara contra
una **referencia estable** y, **solo cuando detecta un cambio real**, guarda la
imagen, la analiza con una **IA de visión** y registra el evento.

Diseñado para **paneles industriales genéricos** (display de 7 segmentos, LCD,
matrices, medidores, fondos de cualquier color): no hay nada específico de un
display en particular.

> 💡 **Por qué existe**: enviar imágenes a una IA de visión tiene costo por
> análisis. En vez de mandar imágenes periódicamente, este sistema **solo las
> envía cuando el detector confirma un cambio real** — un ahorro cercano al
> **99%**. El detector es la "alarma" que decide cuándo gastar.
>
> El **prompt es configurable**, así que el mismo sistema sirve para casos
distintos (¿está en uso?, leer un display, detectar una alarma...) sin tocar
> código. Ver [Análisis con IA](#-análisis-con-ia-de-visión).

## 📚 Documentación

| Documento | Contenido |
|---|---|
| **`README.md`** (este) | Qué hace, cómo usarlo, pipeline, parámetros y calibración |
| **[`API.md`](API.md)** | **Referencia de la API HTTP del worker**: endpoints, campos, ejemplos y SSE |
| **[`API_NUBE.md`](API_NUBE.md)** | **Referencia completa de la API de la nube**, agrupada por quién la usa (sistema externo / concentrador) |
| **[`ARQUITECTURA.md`](ARQUITECTURA.md)** | Diseño interno: módulos, hilos, decisiones y deudas técnicas |
| **[`CONTRATO_NUBE.md`](CONTRATO_NUBE.md)** | **Flujo completo y contrato con los sistemas externos**: nube y Weizhou |
| **[`INTEGRACION_WEIZHOU.md`](INTEGRACION_WEIZHOU.md)** | Documento para su equipo: qué reciben, cada cuánto y con qué contenido |
| **[`DESPLIEGUE.md`](DESPLIEGUE.md)** | Puesta en marcha en la nube, problemas conocidos y verificación |
| **[`concentrador/README.md`](concentrador/README.md)** | El panel unificado y el proxy hacia los workers |
| **[`backend-nube/README.md`](backend-nube/README.md)** | La API en Vercel: qué hace, variables y despliegue |
| **[`REQUERIMIENTO_PANEL_CAMARAS.md`](REQUERIMIENTO_PANEL_CAMARAS.md)** | **Para el equipo del dashboard externo**: qué implementar para administrar las cámaras |
| **`postman_collection.json`** | Colección de Postman lista para importar (45 requests, worker + nube) |

La API de configuración/estado (solo JSON) es consumible desde otro equipo de
la red y **no expone la imagen de cámara**. Ver `API.md` (worker) y
`API_NUBE.md` (nube) para los contratos.

## 🔄 Flujo completo

```mermaid
flowchart LR
    C[Camara] --> W[Worker]
    W -->|cambio detectado| IA[IA: en_uso]
    IA --> K[Concentrador]
    K -->|solo transiciones| WZ[Weizhou]
    K -->|todos los analisis| V[Vercel]
    WZ --> T[Tablets de la lavanderia]
    V --> S[Supabase]
    S --> D[Dashboards]
```

El **concentrador escribe en dos destinos** con propósitos distintos:

| Destino | Propósito | Frecuencia | Si falla |
|---|---|---|---|
| **Weizhou** | Estado **en vivo** de la máquina | Solo **transiciones** | Registra el error |
| **Vercel + Supabase** | **Histórico** y estadística | **Todos** los análisis | Encola y reintenta |

> Solo la nube conserva el histórico **sin rotación**. Los archivos locales del
> worker (`eventos.jsonl`, `analisis_ia/`) se reciclan.

Detalle completo del flujo en **[`CONTRATO_NUBE.md`](CONTRATO_NUBE.md)** §8.
La integración con Weizhou está en
**[`concentrador/README.md`](concentrador/README.md)**.

## ✨ Qué incluye

- **Análisis con IA de visión** (opcional, arranca detenido):
  - Se envía la imagen a la IA **solo cuando hay un cambio** (ahorro ~99%)
  - **Prompt configurable en caliente** desde el panel, con historial de los últimos 10 y **protegido con contraseña**
  - Corre en **hilo aparte** para no bloquear la captura
  - La estructura del JSON de salida la **define el prompt**, no el código
- **Panel web unificado** (`http://localhost:8080`, en el concentrador) para:
  - **Elegir la cámara** con un selector (sin abrir una pestaña por worker)
  - Ver el video en vivo y **definir el área de análisis (ROI)** con el mouse
  - Editar **todos los parámetros en tiempo real** (sin reiniciar el servicio)
  - Ver las últimas capturas y el log de eventos con sugerencias de ajuste
  - Indicador en vivo de la compensación de vibración
- **Compensación de vibración** (registro de imágenes por correlación de fase)
- Detección en **color** (no en gris: un LED rojo sobre fondo oscuro se detecta
  aunque su gris sea igual al fondo)
- Filtros anti-falsos-positivos: estabilidad temporal, área mínima, exclusión
  del borde de la alineación

## 📁 Estructura (monorepo)

```
worker/                ← WORKER: captura, detección, IA (corre junto a la cámara)
├── main.py            ← bucle principal
├── config.yaml        ← parámetros editables (se regenera al guardar desde la web)
├── backend/
│   ├── config.py      ← configuración (dataclass + YAML, guardado en vivo)
│   ├── capturador.py  ← fuente de imágenes: pantalla, cámara USB/IP, MJPEG/RTSP
│   ├── detector.py    ← pipeline de detección (ROI, vibración, diff, filtros)
│   ├── ia.py          ← análisis por visión (DeepSeek) + prompt editable y protegido
│   ├── web.py         ← API HTTP del worker (JSON + video). Ya NO sirve el panel
│   └── registrador.py ← log JSONL + guardado de imágenes y análisis
├── eventos.jsonl      ← SE GENERA: log de eventos
├── capturas_cambio/   ← SE GENERA: imágenes de los eventos
├── analisis_ia/       ← SE GENERA: JSON de los análisis de IA
└── roi.json           ← SE GENERA: el área de análisis

concentrador/          ← CONCENTRADOR: agrega varios workers y habla con la nube
├── main.py            ← bucle principal + arranca el panel unificado
├── config.yaml        ← lista de workers + panel + Weizhou + datos de la nube
├── backend/
│   ├── clientes.py    ← cliente de la API de cada worker
│   ├── nube.py        ← cliente del backend externo (Vercel)
│   ├── cola.py        ← cola offline de análisis pendientes
│   ├── panel.py       ← panel web unificado (proxy + selector de worker)
│   └── weizhou.py     ← envía el estado de las máquinas (solo transiciones)
├── nube.key           ← token de la nube (NO se versiona)
└── weizhou.key        ← API key de Weizhou (NO se versiona)

compartido/            ← PANEL HTML/JS, compartido por worker y concentrador
└── panel.html         ← la interfaz (la sirve el concentrador)

tests/                 ← mock de la nube + test de integración del concentrador

backend-nube/          ← API del sistema externo (Vercel · Node/TS + Supabase)
├── api/index.ts       ← endpoints del contrato (config, analisis, estado)
├── lib/supabase.ts    ← cliente de Supabase + auth por token
├── schema.sql         ← tablas (ejecutar en Supabase)
└── README.md          ← puesta en marcha y despliegue

README.md · API.md · API_NUBE.md · ARQUITECTURA.md · CONTRATO_NUBE.md · DESPLIEGUE.md · postman_collection.json
```

## 🚀 Inicio Rápido

Se ejecutan **dos procesos**: el worker (junto a la cámara) y el concentrador
(el panel y la conexión con la nube).

```bash
pip install -r requirements.txt
```

**Terminal 1 — el worker** (uno por cámara):

```bash
cd worker

# Cámara IP (URL HTTP MJPEG o RTSP):
python main.py --camara "http://192.168.1.144:8080/stream/frontal"
python main.py --camara "rtsp://admin:clave@192.168.1.50:554/stream1"
python main.py --camara 0                      # webcam USB

# Sin cámara: probar capturando la pantalla del computador
python main.py
```

**Terminal 2 — el concentrador** (uno solo, sirve el panel y habla con la nube):

```bash
cd concentrador
python main.py
```

El concentrador deja el panel en **`http://localhost:8080`** (accesible desde
otros equipos de la red), con un **selector** para elegir qué worker ver.

> **Si la cámara no responde al arrancar** (apagada, sin red, stream no
> iniciado), el worker **arranca igual**: su API y el panel funcionan, reporta
> `salud: "sin_senal"` y **reconecta solo** cada 5 s cuando la cámara aparece.
> Así puedes ver y editar los parámetros sin la cámara conectada. El video
> muestra un cartel de "sin señal" en vez de quedar vacío.

### Puertos

| Puerto | Qué es |
|---|---|
| **8080** | **El panel web** (concentrador). El panel está en `/` y la **documentación de la API** en `/api` |
| 5000 | API del worker (JSON + video). Ya no sirve el panel HTML; su índice está en `/api` |
| 5001, 5002... | Los siguientes workers, uno por cámara |

## 🌐 Panel web

Abre **`http://localhost:8080`** (o `http://IP-del-equipo:8080`).

Arriba verás un **selector de worker**: al cambiarlo, todo el panel (video,
ROI, parámetros, capturas, log, IA) apunta a esa cámara. Así no hay que abrir
una pestaña por worker.

| Zona | Qué hace |
|---|---|
| **Video en vivo** | Arrastra el mouse para dibujar el **rectángulo de análisis (ROI)**. Solo lo que está dentro se analiza: el fondo (personas, luces, movimiento) se ignora por completo. Botones 💾 Guardar área / 🗑️ Quitar área. Al **rotar** la imagen (↺/↻ 90°) el recorte se redibuja con las dimensiones del video ya girado. Si **cambias de cámara o de resolución**, el panel **avisa** de que el área puede haber quedado desalineada (sus coordenadas son píxeles absolutos), sin borrarla: puede seguir sirviendo si la cámara nueva apunta al mismo sitio |
| **Indicador de vibración** | Muestra el desplazamiento estimado en vivo: `✅ Sin vibración (dy, dx)` / `⚠️ Vibración detectada` / `🚫 Compensación DESACTIVADA`. Incluye el margen de borde y el desglose interior/borde de la última comparación |
| **⚙️ Parámetros (en vivo)** | Agrupados por tema para encontrarlos rápido: **🪪 Identidad del equipo** (Worker ID) · **📷 Captura** (intervalo de sondeo) · **🎯 Detección** (método, `min_area_px`, umbral, blur, frames estables, min. entre eventos, marcar cambios) · **📳 Vibración** (compensar + máx. desplazamiento) · **🤖 Análisis con IA** (esquema y detalle). Cada campo se aplica al terminar de editarlo (Enter o clic fuera) y **se guarda en `config.yaml`** — sin reiniciar. Los campos que no aplican quedan **atenuados y deshabilitados** (`min_area_px` con método `mse`, `máx. desplazamiento` sin vibración). Select **📷 Preset**: configuración de partida según la resolución de la cámara (VGA, HD, FullHD, 4 MP, 5 MP, 4K) que ajusta `min_area_px`, `blur` y `max_desplazamiento`. Al iniciar, el backend **detecta la resolución del stream** y aplica el preset que le corresponde. Botones: 💾 Aplicar todo y 🔄 Recargar valores |
| **📸 Comparación del último cambio** | El par **ANTES / DESPUÉS**: las dos imágenes que el detector realmente comparó, cada una con su fecha (con milisegundos), su **resolución** y su **total de píxeles**. Esos dos últimos son del **área analizada** (el recorte del ROI, no el frame completo), así que sirven para ver sobre cuántos píxeles se hizo la comparación y calibrar `min_area_px`. Antes se mostraban "las 2 últimas capturas", que eran dos eventos distintos sin relación entre sí |
| **📋 Log de cambios** | Cada evento con su área de píxeles, score y una **sugerencia de ajuste** (p. ej. "sube min_area_px a X"). Botón para limpiar |
| **🤖 Análisis IA** | Botón para **activar/desactivar** el análisis (con confirmación visual). Muestra la salida del último análisis y un **historial de los últimos 10**. Debajo, el **editor del prompt** (protegido con contraseña: arranca 🔒 **bloqueado** y se re-bloquea al guardar) con botón de guardado en caliente y una ventana de **prompts almacenados** para reutilizarlos |

**Parámetros condicionales**: el panel deshabilita (atenúa) los parámetros que
no aplican en el modo actual — p. ej. `min_area_px` no aplica con el método
`mse`, y `max_desplazamiento` solo aplica con la compensación de vibración
activa.

### 📖 Documentación navegable: `http://localhost:8080/api`

Lista las rutas de la API del concentrador y, **en vivo**, las del worker
activo. Útil para no tener que leer el código ni buscar en los `.md`.

| URL | Qué muestra |
|---|---|
| **`http://localhost:8080/api`** | Rutas del concentrador **+ las del worker activo** |
| `http://localhost:5000/api` | Índice JSON del worker (para clientes) |
| `https://backend-nube.vercel.app` | Rutas de la API en la nube |

> Las rutas del worker se descubren pidiendo su propio `/api`, así que la
> página refleja lo que hay de verdad. Si el worker está caído, lo indica en
> lugar de fallar.

## ⚙️ Cómo funciona el pipeline (por frame)

Cada ciclo de captura procesa la imagen así:

```mermaid
flowchart TD
    A["1. Capturar frame"] --> B["2. Recortar ROI (solo el área definida)"]
    B --> C["3. Desenfocar en color (blur_ksize)"]
    C --> D["4. Compensar vibración (si alinear_imagenes)"]
    D --> E["5. Diferencia vs referencia (ssim / diff / mse)"]
    E --> F["6. Máscara con umbral de píxel (umbral 0-1)"]
    F --> G["7. Excluir borde de alineación + área mínima (min_area_px)"]
    G --> H["8. Filtro temporal (frames_estables)"]
    H --> I{"¿Cambio confirmado?"}
    I -- Sí --> J["Guardar imagen + evento + aviso SSE al panel"]
    I -- No --> A
```

Detalle de cada etapa:

1. **Capturar** — el capturador MJPEG mantiene un hilo de fondo leyendo el
   stream y conserva **solo el frame más reciente** (latencia mínima; OpenCV
   por sí solo acumula frames viejos y retrasa la detección).
2. **ROI** — se descarta todo lo que esté fuera del rectángulo definido en la
   web. El detector **nunca ve** el fondo.
3. **Desenfoque** — elimina ruido del sensor y de la compresión JPEG.
   Se aplica por igual a la referencia y al frame actual.
4. **Compensación de vibración** *(opcional)* — antes de comparar, la
   correlación de fase estima cuánto se movió la cámara (X/Y, sub-píxel) y
   desplaza el frame actual **para cuadrarlo con la referencia**. Así la
   vibración no aparece como cambio. Devuelve también un **margen** (el borde
   que quedó "inventado" por el relleno), que la siguiente etapa descarta.
5. **Diferencia** — según el método:
   - `ssim` *(recomendado)*: métrica estructural, robusta a luz/sombras/ruido
   - `diff`: resta píxel a píxel en color (útil para debug)
   - `mse`: error cuadrático medio (no usa área; no recomendado)
6. **Umbral de píxel** — `umbral` (0-1) define cuán distinto debe ser un píxel
   para contarse como cambiado. **Más bajo = más sensible**. En `ssim`, 0.5
   equivale al corte clásico.
7. **Área** — se excluye la franja del borde de la alineación (ahí el shift
   rellena píxeles que no son reales) y se exige que el área de cambio supere
   `min_area_px`. El área de borde se reporta aparte (`area_borde`).
8. **Estabilidad** — el cambio debe persistir `frames_estables` capturas
   consecutivas para confirmarse (un parpadeo o ruido momentáneo no pasa).

**Referencia**: el detector compara contra la **última imagen estable** (no
contra el frame anterior). Se actualiza cuando no hay cambio y cuando un cambio
se confirma; **no** se actualiza durante un cambio pendiente de confirmación.

## 🤖 Análisis con IA de visión

El detector decide **cuándo** algo cambió. La IA decide **qué** cambió.

### El ahorro que justifica el diseño

Enviar imágenes a una IA de visión tiene costo por análisis. Este sistema no
manda imágenes periódicamente: **solo envía la imagen cuando el detector
confirma un cambio real**.

```
Sin este sistema:  1 imagen cada N segundos  →  miles de análisis/día
Con este sistema:  solo cuando cambia el panel  →  decenas/día
```

Como el panel de una máquina está quieto la mayor parte del tiempo, el ahorro
ronda el **99%** del costo de análisis. El detector es la "alarma" que decide
cuándo vale la pena gastar.

> ⚠️ **La IA es opcional y arranca DETENIDA.** Se activa con un botón del panel
> (o por API). Nunca se auto-activa, para no gastar sin que alguien lo decida.

### El flujo del análisis

```mermaid
flowchart TD
    A[Detector confirma un cambio] --> B[Guarda la imagen del evento]
    B --> C{¿IA activa?}
    C -- No --> Z[Fin: solo queda el evento en el log]
    C -- Sí --> D[Envia la imagen a la IA en un HILO APARTE]
    D --> E[La IA responde un JSON]
    E --> F[Guarda el análisis en analisis_ia/]
    F --> G[Avisa al panel por SSE]
```

Dos detalles de diseño:

- **El análisis corre en un hilo aparte**: la llamada a la IA tarda segundos y
  ocurre por internet. Si bloqueara el bucle de captura, se perderían frames y
  la detección se retrasaría.
- **Un análisis por evento**: el resultado queda ligado a la imagen que lo
  originó (`evento_id`), así se puede auditar después.

### El prompt es configurable

**Esto es lo que hace al sistema genérico**: el backend no impone qué debe
devolver la IA. Solo garantiza que la respuesta sea **JSON válido**; el
**contenido lo define el prompt**.

El prompt vive en `worker/ia_prompt.txt` y se edita desde el panel web en
tiempo real (hay también un historial de los últimos 10 prompts, para
reutilizar uno anterior).

**El prompt está protegido con contraseña.** La contraseña vive en
`worker/prompt.key` (gitignored, igual que `ia.key`). El panel arranca
**bloqueado** siempre: hay que pulsar 🔓 Desbloquear e ingresarla para poder
ver o editar el prompt y el historial. Al guardar (o al aplicar un prompt del
historial) **se vuelve a bloquear** automáticamente.

```bash
# Definir/crear la contraseña (una vez)
echo "mi-clave-secreta" > worker/prompt.key
# Quitarla (deja el prompt libre, sin protección)
del worker\prompt.key      # Windows
rm worker/prompt.key       # Linux/macOS
```

Sin `prompt.key` el prompt queda libre (útil en desarrollo). Es una barrera de
**conveniencia** para que un operador no cambie por accidente el comportamiento
de la IA, no una protección criptográfica. Ver `API.md` §4.1.

**Ejemplo real de este proyecto** (vigilancia de si una máquina está en uso):

```
Eres un asistente que analiza UNA imagen de una cámara que vigila el panel
una máquina industrial (lavadora, secadora, planchadora o dobladora).

Tu tarea es determinar si la máquina está EN USO o LIBRE.

Responde SOLO con un JSON válido, con esta estructura EXACTA:
{
  "en_uso": false,
  "confianza": 0.0,
  "notas": ""
}
```

Y la IA responde:

```json
{
  "en_uso": true,
  "confianza": 0.88,
  "notas": "Display LCD encendido: programa P01 ESTANDAR, etapa PRELAVADO, ciclo activo"
}
```

### Cambiar de caso de uso sin tocar código

Como la estructura la define el prompt, **el mismo sistema sirve para otros
casos** cambiando solo el texto:

| Caso de uso | Qué pedirle a la IA |
|---|---|
| ¿La máquina está en uso? | `{"en_uso": bool, "confianza": float}` |
| Leer un valor de un display | `{"valor": "083", "unidad": "C"}` |
| ¿Hay una persona frente al panel? | `{"hay_persona": bool}` |
| ¿La máquina muestra una alarma? | `{"alarma": bool, "codigo": ""}` |

No hay que cambiar el backend, ni la base de datos, ni el transporte: el JSON
libre viaja en el campo `datos` y se guarda como **JSONB** (ver
`CONTRATO_NUBE.md`).

### Dónde queda el resultado

| Lugar | Qué guarda |
|---|---|
| `worker/analisis_ia/*.json` | El análisis local (se mantienen los últimos 10) |
| **Supabase** | **Histórico permanente** (no se rota) |
| El panel web | Bloque "Análisis IA" + historial de los últimos 10 |

El archivo local tiene este formato (el "sobre genérico"):

```json
{
  "tipo": "analisis_ia",
  "worker_id": "panel-1",
  "evento_id": "20260916_123040_025",
  "timestamp": "2026-09-16T12:30:40.025-03:00",
  "esquema": "estado_equipo_v1",
  "datos": { "en_uso": true, "confianza": 0.88, "notas": "..." },
  "meta": {
    "modelo": "deepseek-v4-flash-vision-exp",
    "area_px": 559,
    "score": 0.0221
  }
}
```

**`datos`** es el JSON que devolvió la IA (libre). **`meta`** son los metadatos
del sistema (qué modelo, cuánto área cambió, con qué score). El campo
**`esquema`** es un nombre que declara qué estructura tiene `datos`, para que
los dashboards externos sepan cómo interpretarlo.

### Modelo y configuración

| Parámetro | Default | Descripción |
|---|---|---|
| `enabled` | `false` | Arranca detenido; se activa desde el panel |
| `model` | `deepseek-v4-flash-vision-exp` | Modelo de visión |
| `detail` | `auto` | **Cómo procesa el proveedor la imagen que se le envía.** `auto`/`high`: la recibe **a tamaño real** (el área de análisis tal cual, que es lo que conviene al leer un display). `low`: la redimensiona a **512×512**, lo que **deforma** cualquier recorte que no sea cuadrado (un ROI de 500×120 se estira 4× en vertical) |
| `esquema` | `estado_equipo_v1` | Nombre del formato de `datos` |

**La API key no se guarda en `config.yaml`** (ese archivo se versiona). Se lee
de la variable de entorno `DEEPSEEK_API_KEY` o del archivo `worker/ia.key`
(ignorado por git).

### Cuándo la IA no puede determinar el estado

Si la imagen no es utilizable (cámara tapada, alguien se cruzó, mal
alumbrado), el prompt pide responder con **confianza baja** y una nota
explicando la situación, en vez de inventar una clasificación:

```json
{
  "en_uso": false,
  "confianza": 0.0,
  "notas": "No se distingue el panel: la imagen está tapada por una persona"
}
```

La `confianza` permite que un dashboard muestre una advertencia en esos casos.

## 🎛️ Parámetros (`config.yaml`)

Todos se pueden editar en vivo desde el panel web (se aplican y se guardan solos).

### Captura

| Parámetro | Default | Descripción |
|---|---|---|
| `fuente` | `pantalla` | `pantalla` o `camara` |
| `camara_fuente` | `0` | índice USB, URL RTSP o URL HTTP MJPEG |
| `nombre_camara` | `camara` | nombre legible que queda en el log de eventos |
| `intervalo_segundos` | `1.0` | **cada cuánto pedir imagen a la cámara** (0.25 = 4 fps de prueba) |
| `aplicar_preset_al_iniciar` | `true` | al iniciar, detecta la resolución real del stream y aplica el preset correspondiente |
| `region` / `monitor` | — | solo para captura de pantalla |

### Detección

| Parámetro | Default | Descripción |
|---|---|---|
| `metodo` | `ssim` | `ssim` (recomendado) / `diff` / `mse` |
| `umbral` | `0.5` | sensibilidad a nivel píxel (0-1). **Más bajo = más sensible**. Aplica a los 3 métodos |
| `min_area_px` | `100` | área mínima de cambio para disparar (no aplica con `mse`) |
| `blur_ksize` | `5` | desenfoque (impar). Elimina ruido de compresión |
| `frames_estables` | `2` | capturas consecutivas para confirmar un cambio (anti-parpadeo) |
| `min_intervalo_eventos` | `5.0` | segundos mínimos entre eventos (freno anti-spam) |
| `marcar_cambios` | `false` | guarda además una imagen con los cambios resaltados en rojo (debug) |

### Compensación de vibración (registro de imágenes)

| Parámetro | Default | Descripción |
|---|---|---|
| `alinear_imagenes` | `false` | activa la alineación de imágenes antes de comparar |
| `max_desplazamiento` | `10.0` | límite de desplazamiento (px) a corregir. Por encima no corrige: es un cambio real de escena |

- La vibración típica de máquinas es 1–10 px → con 10–20 px estás cubierto.
- **No corrige desenfoque por movimiento**: si la cámara se mueve durante la
  exposición, los segmentos se ven más anchos (es un efecto físico del
  sensor). La solución real es **shutter rápido** en la cámara + montaje con
  amortiguación.
- La franja del borde que deja el relleno del desplazamiento se **excluye
  automáticamente** de la decisión y se reporta como `area_borde`.

- La franja del borde que deja el relleno del desplazamiento se **excluye
  automáticamente** de la decisión y se reporta como `area_borde`.

### Registro

| Parámetro | Default | Descripción |
|---|---|---|
| `log_eventos` | `eventos.jsonl` | log estructurado (1 línea JSON por evento) |
| `save_changes` | `true` | guardar la imagen de cada evento |
| `output_dir` | `capturas_cambio` | carpeta de imágenes |
| `max_imagenes` | `20` | máximo de archivos en la carpeta (0 = sin límite) |
| `nivel` | `INFO` | `DEBUG` para ver todas las comparaciones |

### Análisis con IA

| Parámetro | Default | Descripción |
|---|---|---|
| `enabled` | `false` | Arranca **detenido**; se activa desde el panel |
| `model` | `deepseek-v4-flash-vision-exp` | Modelo de visión |
| `detail` | `auto` | **Cómo procesa el proveedor la imagen que se le envía.** `auto`/`high`: tamaño real del área de análisis. `low`: la redimensiona a **512×512**, deformando cualquier recorte no cuadrado |
| `esquema` | `estado_equipo_v1` | Nombre del formato de `datos` (lo define el prompt) |

**El prompt NO está aquí**: vive en `worker/ia_prompt.txt` (editable en caliente
desde el panel, con historial de los últimos 10 y **protegido con contraseña**
mediante `worker/prompt.key`). Detalle en
[Análisis con IA](#-análisis-con-ia-de-visión).

> ⚠️ Al guardar parámetros desde la web, `config.yaml` se regenera completo
> (pierde los comentarios escritos a mano, pero el formato se conserva).

## 📋 El log de eventos (`eventos.jsonl`)

Un evento = una línea JSON. Ejemplo:

```json
{"evento_id": "20260803_123456_789", "timestamp": "2026-08-03T12:34:56.789",
 "camara": "panel-1", "metodo": "ssim", "score": 0.0193,
 "area_px": 547, "area_borde": 0,
 "imagen_original": "capturas_cambio\\evento_..._original.png",
 "imagen_marcada": "", "capturas_total": 12}
```

- `area_px` es el área de cambio **del contenido real** (interior).
- `area_borde` es la parte del área que cayó en la franja de la alineación
  (artefacto del shift) — si es grande, el evento pudo ser causado por
  vibración, no por el panel.

Procesar después con pandas / jq / Power BI:

```python
import pandas as pd
df = pd.read_json("eventos.jsonl", lines=True)
df["timestamp"] = pd.to_datetime(df["timestamp"])
```

## 📷 Imágenes por evento

- Siempre: `evento_<fecha>_original.png` → la imagen tal cual (la que enviarías a la IA)
- Con `marcar_cambios: true`: además `evento_<fecha>_marcado.png` → con contornos rojos sobre los cambios

`max_imagenes` mantiene la carpeta acotada (con `marcar_cambios` cada evento
ocupa 2 archivos, así el límite equivale a la mitad de eventos).

## 🔧 Calibración (desde el panel web)

1. **Define el ROI**: dibuja el rectángulo sobre el display. Si el área queda
   chica o mal ubicada, cambios reales no se detectarán (y viceversa).
2. **Ajusta `min_area_px`**: mira el log — cada evento reporta su área. Si ves
   eventos que no deberían existir, súbelo; si un cambio real no dispara, bájalo.
3. **Ajusta `umbral`** (sensibilidad a nivel píxel): déjalo en 0.5 para empezar;
   bájalo si los cambios sutiles no se detectan, súbelo si el ruido cuenta
   demasiados píxeles.
4. **`frames_estables`**: 2 es un buen punto medio (0.5 s de confirmación a 4 fps).
5. **Vibración**: si la cámara está en una máquina que vibra, activa
   `alinear_imagenes` y observa el indicador del panel — debe mostrar el
   desplazamiento estimado y el desglose interior/borde.
6. **Método `diff`** para debug visual (activa también `marcar_cambios` para ver
   los contornos rojos exactos de lo que cambió).

## 🏭 Camino a producción

### Ya implementado

- **Análisis con IA solo cuando hay cambio** (ahorro ~99%) — ver
  [Análisis con IA](#-análisis-con-ia-de-visión)
- **Prompt configurable en caliente**, con historial y **protección por contraseña**
- **API HTTP** del worker + **panel unificado** en el concentrador
- **Multi-cámara**: un proceso por cámara, unificados en un panel con selector
- **Integración con los sistemas externos** (nube y Weizhou)

### Pasos siguientes

1. **Cámara industrial fija** apuntando al panel, con **shutter rápido** para
   minimizar el desenfoque por vibración (hoy se usa una cámara IP de prueba)
2. **Despliegue como servicio** (Docker / systemd) en la máquina de planta
3. **Ampliar los campos del análisis**: el display ya muestra programa, etapa y
   temperatura; hoy van en texto libre dentro de `notas` y podrían promoverse a
   campos propios para estadística (ver `INTEGRACION_WEIZHOU.md` §6)
4. **Verificación anti-oclusión** (evaluada y descartada por ahora): el
   detector ya calcula el área del cambio, así que una heurística que marque
   los cambios que cubren casi todo el ROI sería barata de añadir si hiciera
   falta

## 📡 Cámara desconectada: qué hace el sistema

La captura corre en un hilo aparte que **sigue entregando el último frame
válido** aunque el stream se caiga, para no morir en cada corte de WiFi. Eso
tiene una consecuencia importante: el sistema puede **parecer** funcionando con
la cámara apagada.

Por eso ahora se vigila y se registra:

| Situación | Qué se ve |
|---|---|
| Se pierde la señal | `❌ Cámara 'panel-1' SIN SEÑAL: <motivo>` (una sola vez) |
| La imagen queda congelada | `⚠️ La cámara NO entrega frames nuevos desde hace N capturas` |
| Vuelve la señal | `✅ Cámara 'panel-1' RECUPERADA tras N s sin señal` |
| Vuelve a haber frames | `✅ La cámara vuelve a entregar frames nuevos` |

**Con la cámara caída no se generan eventos ni se llama a la IA**: comparar el
mismo frame congelado solo produciría ruido de compresión y gastaría análisis
inútiles. Al recuperarse, el detector **descarta su imagen de referencia** para
no reportar un cambio falso gigante entre el frame viejo y el nuevo.

El estado es consultable en `GET /api/estado-sistema`:

```json
"runtime": {
  "camara_viva": true,
  "camara_congelada": false,
  "camara_salud": "ok",
  "camara_motivo": ""
}
```

> `camara_viva: true` solo significa que **hay** un frame disponible. Si
> `camara_congelada` es `true`, ese frame es viejo y la cámara está caída.

### Consultar la salud desde la API

Hay un endpoint dedicado, pensado para monitoreo externo:

**`GET /api/salud`** (en cada worker) — informe ligero, sin el resto de la config:

```json
{
  "worker_id": "panel-1",
  "camara_salud": "ok",
  "camara_motivo": "",
  "camara_viva": true,
  "camara_congelada": false,
  "fuente": "rtsp://...",
  "capturas": 3223,
  "cambios": 35,
  "con_deteccion": true
}
```

**`GET /api/salud-camaras`** (en el concentrador) — salud de **todas** las cámaras
en una sola llamada:

```json
{
  "camaras": [
    {"worker_id": "panel-1", "salud": "congelada",
     "motivo": "La cámara está conectada pero dejó de entregar frames nuevos",
     "con_deteccion": false, "capturas": 3223, "eventos": 35}
  ],
  "alertas": [ /* solo las que tienen problemas */ ],
  "todas_ok": false
}
```

**`GET /api/camaras`** (en la nube) — lo mismo, desde el último heartbeat:

| Query | Devuelve |
|---|---|
| *(sin filtro)* | Todas las cámaras |
| `?estado=ok` | Solo las que están bien |
| `?estado=alerta` | Solo las que tienen problemas |

### Valores de salud

| `salud` | Significado | ¿Hay detección? |
|---|---|---|
| `ok` | Todo bien | ✅ Sí |
| `congelada` | Conectada pero sin frames nuevos (imagen vieja) | ❌ No |
| `sin_senal` | No hay ningún frame disponible | ❌ No |
| `worker_caido` | El proceso del worker no responde (solo desde el concentrador/nube) | ❌ No |

> Los endpoints **responden `200` siempre** (es un informe, no un error HTTP),
> para que puedas distinguir "la cámara está mal" de "la API no responde".
> El veredicto está en `camara_salud` / `salud`.
