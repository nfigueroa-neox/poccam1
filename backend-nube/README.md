# Backend nube — API del sistema externo

API serverless (Vercel · Node/TypeScript, en `api/index.ts`) que persiste en
**Supabase**. Es el punto de encuentro entre el **concentrador** de la planta y
el **sistema externo** (dashboards, tablets).

> 📖 **Referencia de todos los endpoints:** [`../API_NUBE.md`](../API_NUBE.md)
> — agrupados por quién los llama, con las respuestas reales.
>
> 📖 **Puesta en marcha paso a paso** (tablas, variables, despliegue y los
> problemas que cuestan tiempo): [`../DESPLIEGUE.md`](../DESPLIEGUE.md).

## Qué hace

| Función | Endpoint |
|---|---|
| **Sistema externo — consultar** | |
| Listar cámaras con su salud | `GET /api/camaras?estado=ok\|alerta` |
| Consultar análisis de la IA | `GET /api/analisis?worker_id=...&limit=N` |
| Consultar eventos detectados | `GET /api/eventos?worker_id=...&limit=N` |
| Listar workers conocidos | `GET /api/workers` |
| **Sistema externo — configurar** | |
| Ver qué campos se pueden tocar | `GET /api/camaras/{id}/config/schema` |
| Leer config de una cámara | `GET /api/camaras/{id}/config` |
| Modificar config de una cámara | `POST /api/camaras/{id}/config` |
| **Concentrador (requiere token)** | |
| Entregar configuración | `GET /api/concentrador/config?version=N` |
| Recibir análisis de la IA | `POST /api/concentrador/analisis` |
| Recibir heartbeat (estado de workers) | `POST /api/concentrador/estado` |
| Recibir config publicada localmente | `POST /api/concentrador/config` |
| **Utilidades** | |
| Salud del servicio | `GET /api/salud` |

> Esta API **no expone imagen de cámara**. Solo JSON de configuración, eventos y
> análisis.

## Archivos

| Archivo | Qué es |
|---|---|
| `api/index.ts` | Toda la API: enrutado, validación y acceso a Supabase |
| `lib/supabase.ts` | Cliente de Supabase (usa la Secret key) |
| `schema.sql` | **Fuente de verdad** del esquema de la base |
| `migracion_salud_camaras.sql` | Migración para bases ya creadas |
| `deploy.mjs` | Script de despliegue (ver nota abajo) |
| `vercel.json` | Rewrites: todo `/api/*` va a `api/index` |

## Variables de entorno

Se configuran en **Vercel → Project → Settings → Environment Variables**:

| Variable | De dónde sale |
|---|---|
| `SUPABASE_URL` | Supabase → Settings → API → Project URL |
| `SUPABASE_SERVICE_KEY` | Supabase → Settings → API Keys → **Secret key** (`sb_secret_...`) |
| `CONCENTRADOR_TOKEN` | Lo generás vos (largo y aleatorio) |

La `SERVICE_KEY` **nunca sale del servidor**: salta RLS y no debe usarse en un
cliente. Las rutas del sistema externo no dependen de ella.

## Conectar el concentrador

En `concentrador/config.yaml`:

```yaml
nube:
  enabled: true
  base_url: "https://tu-proyecto.vercel.app"
  token: "el-mismo-CONCENTRADOR_TOKEN"
  concentrador_id: "planta-1"
```

## Desplegar

> ⚠️ **El proyecto tiene `Root Directory = backend-nube` en Vercel**, porque en
> su momento se desplegó desde la raíz del monorepo. Eso significa que el CLI
> debe ejecutarse **desde la raíz**, no desde esta carpeta (si no, busca
> `backend-nube/backend-nube` y falla). `deploy.mjs` ya lo hace así:

```bash
cd backend-nube
node deploy.mjs deploy     # despliega a producción
node deploy.mjs whoami     # verifica la sesión
```

El token de Vercel se lee de `.vercel-token` (gitignored).

## Notas de arquitectura

- **Runtime Node.js (no Edge)**: el handler es `(req, res)` de `@vercel/node`.
  Con `runtime: 'edge'` las variables de entorno se comportaban de forma
  inconsistente (el token aparecía vacío en runtime).
- **Serverless**: no hay proceso permanente ni filesystem. Toda la persistencia
  es en Supabase.
- **`datos` es JSONB**: el JSON libre que devuelve la IA se guarda tal cual, así
  el esquema puede cambiar sin migrar la base.
- **Idempotencia**: `analisis.evento_id` y `eventos.evento_id` son `UNIQUE` — si
  el concentrador reintenta un envío (cola offline), no se duplica.
- **Config por fusión**: `POST /api/camaras/{id}/config` fusiona bloque por
  bloque, para que un cliente cambie un parámetro sin reenviar todo.
