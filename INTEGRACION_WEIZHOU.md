# Integración con Weizhou

Documento dirigido al **equipo del sistema externo**: qué recibe su API desde
este sistema, con qué frecuencia y con qué contenido.

Complementa a `concentrador/README.md` (lado del emisor). **No cubre la rama de
la nube (Vercel/Supabase)**, que es un destino independiente y no afecta a esta
integración.

---

## 1. Qué recibe Weizhou

El sistema local observa los paneles de las máquinas con cámaras y determina si
cada una está **EN USO** o **LIBRE**. Cuando ese estado **cambia**, envía una
petición a:

```http
POST https://weizhou.vercel.app/api/equipos/estado
x-api-key: <clave>
Content-Type: application/json
```

Es decir, **nosotros somos el emisor**: somos el "computador externo que
informa el estado" descrito en su documentación.

---

## 2. Frecuencia: solo transiciones

Es la decisión de diseño más importante de esta integración.

**No se envía un evento por cada análisis de imagen.** Se envía **solo cuando el
estado cambia**:

```
Máquina arranca     → en_uso=true    → ENVIAR  (evento: inicio)
Sigue en uso        → en_uso=true    → (no se envía)
Sigue en uso        → en_uso=true    → (no se envía)
Máquina termina     → en_uso=false   → ENVIAR  (evento: fin)
Sigue libre         → en_uso=false   → (no se envía)
```

Esto responde a la recomendación explícita de su documentación:

> *"Solo transiciones: envía cuando el estado cambia, no en cada frame."*

**Consecuencia práctica:** su API recibe del orden de **2 eventos por ciclo de
lavado** (arranque y término), no decenas.

> El último estado enviado por máquina se persiste localmente, así un reinicio
> de nuestro proceso **no** dispara un envío redundante.

### 2.1 Garantía de entrega ante fallos

El filtro de transiciones tiene un riesgo: si un envío **falla**, y el estado
vuelve a cambiar y regresa al valor anterior, el sistema podría creer que "no
cambió" y descartar la transición **para siempre**.

```
en_uso=true   → ENVIADO ✅
en_uso=false  → FALLA ❌   (el estado local sigue siendo "en_uso")
en_uso=true   → "sin cambio" → ❌ NUNCA SE ENVÍA   ← transición perdida
```

Para evitarlo, cuando un envío falla la máquina queda marcada como **pendiente
de confirmar**, y el siguiente análisis **reintenta el envío aunque el estado
parezca igual**. La marca se limpia solo cuando Weizhou confirma con `ok:true`.

Además, una respuesta **`2xx` con `ok:false` se trata como fallo** (no como
éxito), para no dar por registrado algo que el destino rechazó.

> Ambos casos se ven en el log del concentrador: `Weizhou → HTTP 500`, `Weizhou
> respondió 2xx pero con error`, `tenía un envío pendiente; reintentando`.

---

## 3. Cuerpo de la petición

### 3.1 Campos nativos que usamos

| Campo | Valor | Notas |
|---|---|---|
| `maquina_id` | UUID | El de su sistema. Ver §4 |
| `camara_id` | UUID | La cámara asociada a esa máquina |
| `en_uso` | `true` / `false` | El dato principal |
| `estado` | `"en_uso"` / `"libre"` | Coherente con `en_uso` |
| `evento` | `"inicio"` / `"fin"` / `"estado"` | `estado` solo en el primer envío |
| `confianza` | 0 a 1 | Certeza de la detección por imagen |
| `notas` | texto (máx. 200 car.) | Qué se observó en el panel |
| `origen` | `"poccam-concentrador"` | Identificador del emisor |
| `timestamp` | ISO 8601 con zona | Momento del cambio |
| `payload` | objeto | Ver §3.2 |

**No usamos** `kg_procesados` (no estimamos peso). Tampoco enviamos
`fuera_servicio` ni `mantencion`: la detección por cámara solo distingue uso de
no-uso. **Si necesitan esos estados, se puede evaluar** (ver §6).

### 3.2 El campo `payload`

Contiene el **análisis completo de la IA**, tal cual se produjo, más metadatos
del sistema:

```json
{
  "en_uso": true,
  "confianza": 0.88,
  "notas": "Display LCD encendido: programa P01 ESTANDAR, etapa PRELAVADO, TEMP 50C y SPD 45, ciclo activo",
  "_meta": {
    "modelo": "deepseek-v4-flash-vision-exp",
    "area_px": 559,
    "score": 0.0221
  },
  "evento_id": "20260916_123040_025"
}
```

> **Pregunta abierta:** ¿el `payload` se puede **consultar o filtrar** en su
> dashboard, o es solo almacenamiento? Si necesitan explotar estos datos en
> reportes, probablemente convenga promoverlos a campos del modelo (ver §6).

### 3.3 Ejemplo completo

```json
{
  "maquina_id": "5b169038-16d9-4ca0-a9e8-a776c6d2b966",
  "camara_id": "95369cfc-8136-45e9-8ea8-27d8085abbd3",
  "en_uso": true,
  "estado": "en_uso",
  "evento": "inicio",
  "confianza": 0.88,
  "notas": "Display LCD encendido: programa P01 ESTANDAR, etapa PRELAVADO, ciclo activo",
  "origen": "poccam-concentrador",
  "timestamp": "2026-09-16T12:30:40-03:00",
  "payload": {
    "en_uso": true,
    "confianza": 0.88,
    "notas": "Display LCD encendido: programa P01 ESTANDAR, etapa PRELAVADO, ciclo activo",
    "evento_id": "20260916_123040_025"
  }
}
```

---

## 4. Identificación de máquinas

Se envía **`maquina_id` (UUID)** por cada máquina, mapeado desde el
identificador local de nuestra cámara:

| Nuestra cámara (`worker_id`) | `maquina_id` | `camara_id` | Máquina |
|---|---|---|---|
| `panel-1` | `5b169038-…` | `95369cfc-…` | Lavadora 1 |
| — | `1d89e671-…` | `1b1d1946-…` | Lavadora 2 |
| — | `ecbb643d-…` | `5c747fea-…` | Secadora 1 |
| — | `40a1c6fb-…` | `a68db92d-…` | Planchadora / Dobladora 1 |

**Hoy solo `panel-1` está operativo.** Los otros tres están preparados y se
habilitan cuando se instalen sus cámaras.

> El mapeo lo mantenemos de nuestro lado. Si prefieren que usemos **código
> externo** en vez del UUID, deben asignar un código (ej. `LAV-01`) a cada
> máquina en su sistema y avisarnos.

---

## 5. Cómo interpretar los datos

### 5.1 Qué significa la `confianza`

Es la certeza de la **detección por imagen**, no de la operación:

| Valor | Interpretación |
|---|---|
| > 0.8 | El panel se ve con claridad; clasificación sólida |
| 0.5 – 0.8 | Se ve el panel, pero hay dudas (reflejo, distancia, luz) |
| < 0.5 | Imagen poco fiable (borrosa, tapada, mal iluminada) |

**Importante:** una `confianza` baja **no invalida el evento** — el estado igual
se envía. Si quieren, pueden usar este campo para mostrar una advertencia en el
dashboard.

### 5.2 Qué significan las `notas`

Texto libre, en español, que describe **qué se observó** para justificar el
estado. Ejemplos reales:

- `"Display encendido mostrando 'EN ESPERA' (reposo), sin tiempo de ciclo activo"`
- `"Display LCD encendido: programa P01 ESTANDAR, etapa PRELAVADO, TEMP 50C y SPD 45, ciclo activo"`

Cuando no se puede determinar (cámara tapada, alguien se cruzó), se envía
`en_uso: false` con `confianza: 0.0` y una nota explicando la situación.

> ⚠️ **Aviso:** si una imagen no es utilizable, el sistema reporta **LIBRE**.
> Es una decisión conservadora para nuestra PoC. Si prefieren que en ese caso
> **no se envíe nada** (para no alterar el estado), es un cambio simple.

### 5.3 Idempotencia

Si por un fallo de red reintentáramos un envío, **el mismo `evento` puede
llegar dos veces**. La API acepta reenvíos, pero conviene que su lado sea
idempotente por `timestamp` si eso les preocupa.

---

## 6. Preguntas abiertas para su equipo

| # | Pregunta | Por qué importa |
|---|---|---|
| 1 | ¿El `payload` se puede **consultar/filtrar**, o es solo almacenamiento? | Define si vale la pena enriquecerlo |
| 2 | ¿Interesan `etapa`, `temperatura`, `programa` como **campos propios**? | El display los muestra, pero hoy van en texto libre |
| 3 | ¿Quieren que enviemos `estado: mantencion` / `fuera_servicio`? | Hoy solo distinguimos uso/no-uso |
| 4 | ¿Qué pasa si una imagen **no es utilizable**? ¿Enviar LIBRE o no enviar? | Hoy enviamos LIBRE |
| 5 | ¿Usamos `maquina_id` (UUID) o prefieren **código externo**? | Hoy usamos UUID |

---

## 7. Diagnóstico del envío

Respuestas que puede devolver su API y qué significan para nosotros:

| HTTP | Significado | Nuestra reacción |
|---|---|---|
| `200` | Aceptado | Se registra el cambio de estado |
| `400` | Falta un campo o `estado` inválido | **Es un bug nuestro**: se registra el error |
| `401` | La `x-api-key` es incorrecta | Error de configuración; se registra |
| `404` | La máquina no existe en Weizhou | Revisar el mapeo de §4 |
| `207` | Lote parcialmente fallido | No aplica: enviamos individual, no por lote |
| `5xx` / sin respuesta | Fallo temporal | Se registra; el próximo cambio de estado se enviará igual |

> **Nota:** a diferencia de otros destinos, **Weizhou no tiene cola de
> reintentos**. Si un envío falla, se pierde ese evento. El próximo cambio de
> estado sí se enviará. Si necesitan garantía de entrega, habría que añadirla.

---

## 8. Resumen en una línea

**Enviamos a Weizhou un `POST` por cada cambio de estado (máquina arranca /
termina), con el UUID de la máquina, el booleano `en_uso` y el análisis
completo de la imagen en `payload`.**
