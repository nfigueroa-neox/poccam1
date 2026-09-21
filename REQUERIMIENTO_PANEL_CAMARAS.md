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

Devuelve el contrato de campos. **Se recomienda consumirlo al abrir la
pantalla** en vez de codificar los campos a mano: si la API añade un campo,
aparece solo.

Devuelve tres bloques de información:

| Sección | Para qué |
|---|---|
| `bloques` | Campos modificables: tipo, valores válidos, rango y descripción |
| `no_modificables` | Enviarlos da `400`. **No ofrecerlos** |
| `no_exponer_en_panel` | La API los acepta, pero **el panel no debe mostrarlos** (§6) |

```json
{
  "bloques": { "deteccion": { "campos": {
      "min_area_px": { "tipo": "integer", "min": 0,
        "descripcion": "Píxeles mínimos cambiados para disparar un evento..." }
  } } },
  "no_modificables": {
    "captura.camara_fuente": "URL de la cámara: es hardware local"
  },
  "no_exponer_en_panel": {
    "captura.rotacion": "Al cambiarlo el ROI queda desalineado...",
    "ia.model": "Está elegido y probado...",
    "deteccion.umbral": "No decide con metodo=ssim/diff..."
  }
}
```

> Usar `no_exponer_en_panel` como fuente de verdad para §6: si el backend
> cambiara de opinión, el panel se adapta solo.

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

### 4.1 Detección — ajuste de sensibilidad

| Campo | Tipo | Rango / valores | Qué hace |
|---|---|---|---|
| **`min_area_px`** | entero | `>= 0` | **El principal.** Píxeles mínimos cambiados para disparar un evento. Súbelo para ignorar ruido |
| `metodo` | string | `ssim` \| `diff` \| `mse` | Cómo compara las imágenes. `ssim` tolera sombras; `diff` es más rápido |
| `blur_ksize` | entero | `>= 0` | Desenfoque que elimina ruido de compresión |
| `frames_estables` | entero | `>= 1` | Capturas consecutivas para confirmar un cambio |
| `min_intervalo_eventos` | número | `>= 0` (seg) | Anti-rebote: tiempo mínimo entre eventos |
| `alinear_imagenes` | booleano | — | Compensar vibración de la cámara |
| `max_desplazamiento` | número | `>= 1` (px) | Solo aplica si `alinear_imagenes` es `true` |
| `marcar_cambios` | booleano | — | Dibujar los contornos del cambio en la imagen |
| `umbral` | número | `>= 0` | ⚠️ Ver §4.4 |

### 4.2 Captura

| Campo | Tipo | Rango | Qué hace |
|---|---|---|---|
| `intervalo_segundos` | número | `> 0.01` | Segundos entre capturas (0.5 = 2 capturas/s) |
| `rotacion` | entero | `0`/`90`/`180`/`270` | ⚠️ **No exponer en el panel.** Ver §6 |

### 4.3 IA de visión

| Campo | Tipo | Valores | Qué hace |
|---|---|---|---|
| `esquema` | string | texto libre | Nombre del formato del JSON que produce el prompt |
| `detail` | string | `low` \| `high` \| `auto` | Detalle que se envía a la IA. `low` es más económico |
| `model` | string | texto libre | ⚠️ **No exponer en el panel.** Ver §6 |

### 4.4 ⚠️ Campos excluyentes según `metodo`

Este es el punto que más errores causa. **Qué parámetros aplican depende del
método elegido:**

| `metodo` | Filtro que decide | Campo que NO aplica |
|---|---|---|
| `ssim` | `min_area_px` | `umbral` |
| `diff` | `min_area_px` | `umbral` |
| `mse` | **`umbral`** | **`min_area_px`** |

**Requisito:** cuando el usuario cambie `metodo`, el panel debe **deshabilitar
visualmente** el campo que no aplica (atenuarlo, con una nota). Si no, tocará un
parámetro que no hace nada y creerá que la detección está mal.

El proyecto usa `ssim`, así que en la práctica `min_area_px` manda y `umbral` no
se usa. **Recomendación: no mostrar `umbral` en absoluto.**

### 4.5 Campos NO modificables

| Campo | Por qué |
|---|---|
| `captura.camara_fuente`, `captura.fuente` | Es hardware local del equipo de la planta |
| `roi` | Se dibuja sobre el video en el panel local |
| `prompt` | Protegido con contraseña en el equipo |
| `nombre_camara`, `worker_id` | Identidad del equipo |

Enviarlos devuelve `400` (ver §5). **No ofrecerlos en el panel.**

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

La API publica estas restricciones en **`no_exponer_en_panel`** (§3.2), así que
el panel debería leerlas en vez de codificarlas. Las razones:

**1. `rotacion` no se expone.** Cambiar el giro deja el área de análisis (RoI)
apuntando a otro sitio de la escena, y quien lo cambie desde aquí no puede
redibujarla. La detección empieza a mirar basura y nadie lo nota.

**2. `model` no se expone.** El modelo de visión está elegido y probado. Que se
pueda cambiar por accidente solo añade variabilidad sin beneficio.

**3. `umbral` no se expone.** No aplica con el método en uso (§4.4).

**4. Siempre mostrar `efectiva`, nunca `config`.** `config` puede estar
incompleto; mostrar eso daría valores falsos.

**5. La app externa no recibe notificaciones.** Todas las rutas son `GET`. Para
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
- [ ] Deshabilita `umbral` cuando el método es `ssim` o `diff`.
- [ ] **No** ofrece los campos de `no_exponer_en_panel` (`rotacion`, `model`, `umbral`).
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
