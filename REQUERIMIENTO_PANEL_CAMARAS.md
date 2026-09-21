# Requerimiento: panel de administración de cámaras

**Destinatario:** equipo/IA que implementa el panel en la aplicación externa
**Versión:** 1.0 · **Fecha:** 2026-09-21
**API:** `https://backend-nube.vercel.app`

---

## 1. Objetivo

Implementar una pantalla de administración de cámaras dentro de la aplicación
externa (el dashboard de la lavandería) que permita:

1. **Ver** el estado de cada cámara (salud, métricas, configuración real).
2. **Modificar** los parámetros de detección de cada cámara.

Todo se hace contra la API REST descrita abajo. **La cámara no se ve ni se
configura desde aquí**: esta API nunca expone imagen de video, y la URL de la
cámara solo se cambia localmente en el equipo de la planta.

---

## 2. Conceptos previos

| Término | Qué es |
|---|---|
| **Cámara / worker** | Un proceso que vigila un panel. Su id es el `worker_id` |
| **Configurado** | Lo que se envió desde este panel. Puede estar **incompleto** |
| **Efectiva** | Lo que **realmente corre** en el equipo. Siempre completa |
| **RoI** | Área de la imagen que se analiza. **Local**, no se toca desde aquí |

> **Diferencia clave:** si envías solo `min_area_px`, el resto de parámetros
> siguen con los valores locales del equipo. Por eso existe `efectiva`: es la
> única forma de saber qué está corriendo de verdad. **Mostrar siempre
> `efectiva`, nunca deducir valores.**

---

## 3. Endpoints

Base: `https://backend-nube.vercel.app` · Formato: JSON · **Sin autenticación.**

### 3.1 Listar cámaras

```http
GET /api/camaras
```

Punto de entrada de la pantalla. Devuelve todas las cámaras con su estado.

| Query (opcional) | Efecto |
|---|---|
| `?estado=ok` | Solo las que funcionan |
| `?estado=alerta` | Solo las que tienen problemas |

```json
{
  "camaras": [
    {
      "worker_id": "panel-1",
      "salud": "ok",
      "motivo": "",
      "con_deteccion": true,
      "resolucion": [1920, 1080],
      "ia_activa": false,
      "capturas": 3867,
      "eventos": 35,
      "visto": "2026-09-21T19:20:26+00:00",
      "efectiva": {
        "deteccion": {
          "metodo": "ssim", "min_area_px": 50, "blur_ksize": 5,
          "umbral": 0.5, "frames_estables": 2, "min_intervalo_eventos": 1.0,
          "marcar_cambios": false, "alinear_imagenes": false,
          "max_desplazamiento": 10.0
        },
        "captura": {
          "intervalo_segundos": 0.5, "rotacion": 0, "reconectar_segundos": 180.0
        },
        "ia": {
          "enabled": false, "esquema": "estado_equipo_v1",
          "model": "deepseek-v4-flash-vision-exp", "detail": "low"
        }
      }
    }
  ],
  "alertas": [],
  "todas_ok": true,
  "endpoints": {
    "config": "GET|POST /api/camaras/{camara_id}/config",
    "esquema": "GET /api/camaras/{camara_id}/config/schema"
  }
}
```

**La lista puede tener más de un `concentrador_id`** si hay varias plantas.

### 3.2 Leer qué se puede modificar

```http
GET /api/camaras/{camara_id}/config/schema
```

Devuelve **exactamente** los campos que la API acepta. **Consumirlo al abrir
la pantalla** en vez de codificar los campos a mano: si la API cambia, el panel
se adapta solo.

```json
{
  "bloques": {
    "deteccion": {
      "descripcion": "Cómo se decide si hubo un cambio en la imagen",
      "campos": {
        "min_area_px": { "tipo": "integer", "min": 0,
          "descripcion": "Píxeles mínimos cambiados para disparar un evento..." },
        "metodo": { "tipo": "string", "valores": ["ssim","diff","mse"],
          "descripcion": "..." }
      }
    },
    "captura": { "campos": { "intervalo_segundos": { "tipo": "number", "min": 0.01 } } },
    "ia": { "campos": { "detail": { "tipo": "string", "valores": ["low","high","auto"] } } }
  },
  "no_aceptados": {
    "rotacion": "depende del encuadre: cambiarlo desalinea el área de análisis...",
    "model": "el modelo de visión está elegido y probado...",
    "umbral": "solo decide con metodo=mse; con ssim y diff manda min_area_px"
  }
}
```

Cada campo trae `tipo`, `min` cuando aplica, `valores` cuando es una lista
cerrada, y una `descripcion` para el usuario.

> **`no_aceptados` es informativo**, por si el cliente necesita explicar por
> qué un parámetro no está. El panel **no debe** listarlos como campos.

### 3.3 Leer la configuración enviada

```http
GET /api/camaras/{camara_id}/config
```

Devuelve **solo lo configurado desde aquí**, no la efectiva:

```json
{
  "camara_id": "panel-1",
  "config": { "deteccion": { "min_area_px": 50, "blur_ksize": 5 } },
  "version": 21,
  "actualizado": "2026-09-21T19:35:00Z"
}
```

> Para **mostrar** valores al usuario, usar `efectiva` de §3.1. Esta ruta sirve
> para saber qué se envió explícitamente (por ejemplo, para marcar qué campos
> fueron personalizados y cuáles están en su valor de fábrica).

Si nunca se configuró: `config: {}` y `version: 0`.

### 3.4 Modificar la configuración

```http
POST /api/camaras/{camara_id}/config
Content-Type: application/json
```

```json
{ "config": { "deteccion": { "min_area_px": 120 } } }
```

**La escritura es una FUSIÓN.** Se envía solo lo que cambia; el resto se
mantiene. No hace falta leer antes de escribir.

Respuesta correcta:

```json
{
  "ok": true,
  "camara_id": "panel-1",
  "version": 22,
  "aplicado": { "deteccion": { "min_area_px": 120 } },
  "nota": "El concentrador bajará el cambio en su próximo ciclo..."
}
```

---

## 4. Campos modificables

La API acepta **9 campos**. Son los únicos que tienen efecto desde un cliente
externo.

### 4.1 Detección — ajuste de sensibilidad

| Campo | Tipo | Rango / valores | Qué hace |
|---|---|---|---|
| **`min_area_px`** | entero | `>= 0` | **El principal.** Píxeles mínimos cambiados para disparar un evento. Súbelo para ignorar ruido |
| `metodo` | string | `ssim` \| `diff` \| `mse` | Cómo compara. `ssim` tolera sombras; `diff` es más rápido |
| `blur_ksize` | entero | `>= 0` | Desenfoque que elimina ruido de compresión |
| `frames_estables` | entero | `>= 1` | Capturas consecutivas para confirmar un cambio |
| `min_intervalo_eventos` | número | `>= 0` (seg) | Anti-rebote: tiempo mínimo entre eventos |
| `alinear_imagenes` | booleano | — | Compensar vibración de la cámara |
| `max_desplazamiento` | número | `>= 1` (px) | Solo aplica si `alinear_imagenes` es `true` |

### 4.2 Captura

| Campo | Tipo | Rango | Qué hace |
|---|---|---|---|
| `intervalo_segundos` | número | `> 0.01` | Segundos entre capturas (0.5 = 2 capturas/s) |

### 4.3 IA de visión

| Campo | Tipo | Valores | Qué hace |
|---|---|---|---|
| `detail` | string | `low` \| `high` \| `auto` | Detalle que se envía a la IA. `low` es más económico |

### 4.4 ⚠️ Campos excluyentes según `metodo`

Este es el punto que más errores causa. **Qué parámetro decide depende del
método:**

| `metodo` | Filtro que decide |
|---|---|
| `ssim` | `min_area_px` |
| `diff` | `min_area_px` |
| `mse` | `umbral` — **no disponible en la API** |

En la práctica: el proyecto usa `ssim`, así que **`min_area_px` es el único
filtro de sensibilidad**. Si algún día se cambiara a `mse`, habría que añadir
`umbral` a la API (hoy no se acepta, precisamente porque no aplica).

> El panel **no necesita** manejar esto: `umbral` no está en la API, así que no
> se puede mostrar por error.

---

## 5. Errores

Todos los errores de configuración devuelven `400` con una **lista** de
problemas (se validan todos juntos, no uno por uno):

```json
{
  "error": "configuración inválida",
  "errores": [
    "deteccion.min_area_px: debe ser >= 0 (recibido -5)",
    "deteccion.metodo: \"inventado\" no es válido (permitidos: \"ssim\", \"diff\", \"mse\")"
  ],
  "sugerencia": "Consulta GET /api/camaras/{id}/config/schema"
}
```

| HTTP | Cuándo | Qué mostrar |
|---|---|---|
| `200` | Éxito | Confirmar y **refrescar los valores** |
| `400` | Valores inválidos | Mostrar `errores[]` junto a los campos |
| `404` | La cámara no existe | "Cámara no encontrada" + refrescar la lista |
| `405` | Método incorrecto | Error de programación |
| `500` | Fallo del servidor | Reintentar con backoff |

**Importante para la UX:** la validación es del lado del servidor. El panel
**puede** validar antes para dar feedback inmediato, pero **debe manejar el
`400`** y mostrar el mensaje del servidor: es la fuente de verdad.

---

## 6. Restricciones de diseño

**La API ya aplica estas restricciones** rechazando los campos (§4). El panel no
tiene que recordarlas: si construye el formulario desde el `schema`, no puede
ofrecer algo que la API rechace.

Las razones, por si hacen falta para explicarle a un usuario:

| Campo | Por qué no está |
|---|---|
| `rotacion` | Cambiar el giro deja el área de análisis (RoI) apuntando a otro sitio de la escena, y no se puede redibujar desde aquí |
| `model` | El modelo de visión está elegido y probado |
| `umbral` | No decide con el método en uso |
| `esquema` | Define el formato del JSON de salida; lo fija quien administra la cámara |
| `marcar_cambios` | Solo dibuja contornos en la imagen, para depurar; no afecta la detección |
| `camara_fuente` | Es hardware local y **no viaja a la nube**: el panel no la verá nunca |

### Otras reglas

**1. Siempre mostrar `efectiva`, nunca `config`.** `config` puede estar
incompleto; mostrar eso daría valores falsos.

**2. La app externa no recibe notificaciones.** Todas las rutas son `GET`. Para
"tiempo real" hay dos caminos:

| Camino | Cómo | Cuándo |
|---|---|---|
| **Polling** | `GET /api/camaras` cada 10-30 s | Simple, pocos usuarios |
| **Supabase Realtime** | Suscribirse a la tabla `heartbeats` | Latencia baja, muchos usuarios |

---

## 7. Estados y cómo mostrarlos

### 7.1 Salud de la cámara

| `salud` | Significado | Color sugerido |
|---|---|---|
| `ok` | Funcionando y detectando | 🟢 |
| `congelada` | Conectada pero sin imagen nueva | 🟠 |
| `sin_senal` | La cámara no responde | 🔴 |
| `worker_caido` | El equipo de la planta está apagado o sin red | ⚪ |

> **`con_deteccion`** es el booleano listo para usar: es `true` **solo** si
> `salud == "ok"`. Si la cámara está mal, **no habrá eventos nuevos**, aunque la
> pantalla siga mostrando la última configuración.

### 7.2 Casos borde obligatorios

| Situación | Qué debe hacer el panel |
|---|---|
| `efectiva` es `null` | Mostrar "sin datos" en los parámetros; **no** usar `config` como sustituto |
| `salud != "ok"` | Avisar que no hay detección; deshabilitar o advertir en el formulario |
| `visto` es antiguo (> 2 min) | Mostrar "sin conexión desde …" |
| `capturas: 0` con `salud: "ok"` | La cámara acaba de arrancar; esperar |
| Lista vacía | "No hay cámaras configuradas" (no un error) |

### 7.3 Campos de estado (solo lectura)

| Campo | Qué mostrar |
|---|---|
| `resolucion` | `[1920, 1080]` → "1920×1080" |
| `capturas` | Capturas desde el arranque |
| `eventos` | Cambios detectados desde el arranque |
| `ia_activa` | Si el análisis con IA está encendido |
| `visto` | Último contacto (mostrar como hace X minutos) |

---

## 8. Flujo recomendado

```
1. Abrir pantalla
   └─ GET /api/camaras                    ← lista + estado + efectiva
   └─ GET /api/camaras/{id}/config/schema ← campos disponibles

2. Seleccionar una cámara
   └─ Mostrar estado (salud, resolución, contadores)
   └─ Mostrar parámetros desde `efectiva`
   └─ Marcar qué campos fueron personalizados
      (comparar `efectiva` con `config` y con los valores por defecto)

3. Editar un parámetro
   └─ Validar localmente (rangos de §4) para feedback inmediato
   └─ POST /api/camaras/{id}/config  con SOLO el campo cambiado
   └─ Si 200: confirmar y refrescar
   └─ Si 400: mostrar `errores[]` junto a los campos

4. Refrescar periódicamente
   └─ GET /api/camaras cada 10-30 s (o Supabase Realtime)
```

---

## 9. Criterios de aceptación

- [ ] La pantalla lista todas las cámaras y su salud con el color correcto.
- [ ] Muestra los parámetros desde `efectiva`; si es `null`, indica "sin datos".
- [ ] Permite editar los campos de §4.1, §4.2 y §4.3 (excepto los excluidos en §6).
- [ ] Al enviar, usa **fusión**: solo los campos modificados.
- [ ] Maneja el `400` mostrando los mensajes del servidor.
- [ ] Construye el formulario desde `GET .../config/schema`, sin codificar campos.
- [ ] Muestra únicamente los campos que devuelve `bloques` (9 en total).
- [ ] Advierte cuando `salud != "ok"`: no habrá detecciones.
- [ ] Funciona con la lista vacía y con cámaras caídas, sin romperse.

---

## 10. Verificación rápida

```bash
# Listar cámaras y ver la config efectiva
curl https://backend-nube.vercel.app/api/camaras

# Consultar los campos disponibles
curl https://backend-nube.vercel.app/api/camaras/panel-1/config/schema

# Cambiar un parámetro
curl -X POST https://backend-nube.vercel.app/api/camaras/panel-1/config \
  -H 'content-type: application/json' \
  -d '{"config":{"deteccion":{"min_area_px":120}}}'

# Probar que la validación rechaza (debe dar 400)
curl -X POST https://backend-nube.vercel.app/api/camaras/panel-1/config \
  -H 'content-type: application/json' \
  -d '{"config":{"deteccion":{"min_area_px":-5}}}'
```

Documentación completa de la API: `API_NUBE.md`.
