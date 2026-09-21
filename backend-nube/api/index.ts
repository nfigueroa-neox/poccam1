/**
 * API del sistema externo (Vercel Serverless Function).
 *
 * Implementa el lado servidor del contrato `CONTRATO_NUBE.md`:
 *
 *   GET  /api/concentrador/config?version=N  → config vigente para los workers
 *   POST /api/concentrador/analisis          → recibe un análisis de la IA
 *   POST /api/concentrador/estado            → heartbeat del concentrador
 *   POST /api/concentrador/config            → recibe config publicada local
 *
 *   GET  /api/workers                        → lista de workers conocidos
 *   GET  /api/analisis?worker_id=...&limit=  → consulta de análisis
 *   GET  /api/eventos?worker_id=...          → consulta de eventos
 *
 * Todas las llamadas del concentrador se autentican con Bearer token.
 */

import { supabase, autenticado, json } from '../lib/supabase.js';

// Runtime Node.js (no Edge): las variables de entorno se inyectan de forma
// fiable; en Edge se comportaban de manera inconsistente.
export const config = { runtime: 'nodejs' };

/**
 * Adaptador Vercel Node → interfaz tipo Fetch usada por el resto del código.
 * `@vercel/node` entrega (req, res) con Node streams.
 */
interface ReqNode {
  method?: string;
  url?: string;
  headers: Record<string, string | string[] | undefined>;
  on: (evento: string, cb: (trozo?: unknown) => void) => void;
}

interface ResNode {
  statusCode: number;
  setHeader: (k: string, v: string) => void;
  end: (cuerpo?: string) => void;
}

function leerCuerpo(req: ReqNode): Promise<string> {
  return new Promise((resolve) => {
    let datos = '';
    req.on('data', (t) => {
      datos += String(t);
    });
    req.on('end', () => resolve(datos));
  });
}

export default async function handler(req: ReqNode, res: ResNode): Promise<void> {
  const url = new URL(req.url ?? '/', 'https://local');
  const cuerpo = req.method === 'GET' ? '' : await leerCuerpo(req);

  const peticion = {
    method: (req.method ?? 'GET').toUpperCase(),
    url: url.toString(),
    headers: new Map(
      Object.entries(req.headers).map(([k, v]) => [k, Array.isArray(v) ? v.join(',') : String(v ?? '')]),
    ),
    json: async () => (cuerpo ? JSON.parse(cuerpo) : {}),
  };

  const respuesta = await manejar(peticion, url);
  const texto = await respuesta.text();
  res.statusCode = respuesta.status;
  res.setHeader('Content-Type', respuesta.headers.get('Content-Type') ?? 'application/json');
  res.end(texto);
}
interface Peticion {
  method: string;
  url: string;
  headers: Map<string, string>;
  json: () => Promise<Record<string, unknown>>;
}

async function manejar(peticion: Peticion, url: URL): Promise<Response> {
  const ruta = url.pathname.replace(/\/+$/, '');
  const metodo = peticion.method;

  try {
    // ── Página de inicio ────────────────────────────────────────────
    // Sin esto, abrir la URL en el navegador da el 404 seco de Vercel, que
    // no explica qué es este servicio ni qué rutas existen.
    if ((ruta === '' || ruta === '/') && metodo === 'GET') {
      return paginaInicio(url.origin);
    }

    // ── Endpoints del concentrador (requieren token) ────────────────
    if (ruta.startsWith('/api/concentrador/')) {
      if (!autenticado(peticion)) {
        return json(401, { error: 'token inválido' });
      }
      return await rutasConcentrador(ruta, metodo, peticion, url);
    }

    // ── Consultas de lectura (para dashboards) ──────────────────────
    if (ruta === '/api/workers' && metodo === 'GET') {
      return await listarWorkers();
    }
    if (ruta === '/api/analisis' && metodo === 'GET') {
      return await listarAnalisis(url);
    }
    if (ruta === '/api/eventos' && metodo === 'GET') {
      return await listarEventos(url);
    }
    // Estado de salud de las cámaras de cada worker, para dashboards
    if (ruta === '/api/camaras' && metodo === 'GET') {
      return await listarSaludCamaras(url);
    }

    // ── Configuración por cámara (sistema externo) ──────────────────
    // El sistema externo lista las cámaras, lee su config y la modifica.
    const camara = ruta.match(/^\/api\/camaras\/([^/]+)\/config(\/schema)?$/);
    if (camara) {
      const camaraId = decodeURIComponent(camara[1]);
      const esSchema = Boolean(camara[2]);
      if (esSchema && metodo === 'GET') return json(200, ESQUEMA_CONFIG);
      if (metodo === 'GET') return await leerConfigCamara(camaraId);
      if (metodo === 'POST') return await escribirConfigCamara(camaraId, peticion);
      return json(405, { error: 'método no permitido' });
    }

    if (ruta === '/api/salud' || ruta === '/api/health') {
      return json(200, { ok: true, servicio: 'poccam-backend-nube' });
    }

    return json(404, { error: 'ruta no encontrada', ruta });
  } catch (e) {
    const mensaje = e instanceof Error ? e.message : String(e);
    return json(500, { error: mensaje });
  }
}

// ── Rutas del concentrador ───────────────────────────────────────────

// ── Página de inicio ─────────────────────────────────────────────────

/**
 * Portada del servicio: explica qué es y lista las rutas disponibles.
 *
 * Existe porque abrir la raíz en un navegador es lo primero que hace
 * cualquiera que recibe la URL, y el 404 por defecto de Vercel no dice nada.
 */
function paginaInicio(origen: string): Response {
  const grupos: Array<{
    titulo: string;
    nota: string;
    rutas: Array<[string, string, string]>;
  }> = [
    {
      titulo: 'Consultar (sistema externo)',
      nota: 'Sin token. Para dashboards y tablets.',
      rutas: [
        ['GET', '/api/camaras', 'Cámaras con su id y su salud'],
        ['GET', '/api/camaras?estado=alerta', 'Solo las cámaras con problemas'],
        ['GET', '/api/analisis?limit=50', 'Análisis de la IA (máx. 500)'],
        ['GET', '/api/eventos?limit=50', 'Eventos detectados (antes de la IA)'],
        ['GET', '/api/workers', 'Workers registrados'],
      ],
    },
    {
      titulo: 'Configurar cámaras (sistema externo)',
      nota: 'Sin token. Flujo: listar → ver esquema → leer y escribir.',
      rutas: [
        ['GET', '/api/camaras/{id}/config/schema', 'Qué campos se pueden modificar'],
        ['GET', '/api/camaras/{id}/config', 'Config vigente de esa cámara'],
        ['POST', '/api/camaras/{id}/config', 'Modificar sus parámetros (fusión)'],
      ],
    },
    {
      titulo: 'Concentrador',
      nota: 'Requieren Authorization: Bearer <CONCENTRADOR_TOKEN>.',
      rutas: [
        ['GET', '/api/concentrador/config?version=N', 'Bajar configuración (304 si no cambió)'],
        ['POST', '/api/concentrador/analisis', 'Subir un análisis de la IA'],
        ['POST', '/api/concentrador/estado', 'Heartbeat del concentrador'],
        ['POST', '/api/concentrador/config', 'Publicar config hecha en local'],
      ],
    },
  ];

  const secciones = grupos
    .map(
      (g) => `
    <section>
      <h2>${g.titulo}</h2>
      <p class="nota">${g.nota}</p>
      <ul>
        ${g.rutas
          .map(
            ([m, r, d]) =>
              `<li><a href="${r.replace('{id}', 'panel-1')}">` +
              `<span class="metodo ${m.toLowerCase()}">${m}</span>` +
              `<code>${r}</code></a><span class="desc">${d}</span></li>`,
          )
          .join('')}
      </ul>
    </section>`,
    )
    .join('');

  const html = `<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>API · POC Cámaras</title>
<style>
  :root { color-scheme: dark; }
  body { margin:0; padding:32px 20px 60px; background:#12161a; color:#c8d2da;
         font:15px/1.6 system-ui,-apple-system,Segoe UI,sans-serif; }
  .caja { max-width:820px; margin:0 auto; }
  h1 { margin:0 0 4px; font-size:22px; color:#e8eef3; }
  .sub { color:#7d8b96; font-size:13px; margin-bottom:26px; }
  section { margin-bottom:26px; }
  h2 { font-size:15px; margin:0 0 2px; color:#8fd6bd; }
  .nota { margin:0 0 10px; font-size:13px; color:#7d8b96; }
  ul { list-style:none; margin:0; padding:0; }
  li { display:flex; align-items:baseline; gap:10px; flex-wrap:wrap;
       padding:7px 10px; border-radius:6px; }
  li:nth-child(odd) { background:#181d22; }
  a { text-decoration:none; display:flex; align-items:baseline; gap:10px; }
  code { color:#e8eef3; font-size:13.5px;
         font-family:ui-monospace,Consolas,monospace; }
  a:hover code { color:#8fd6bd; text-decoration:underline; }
  .metodo { font-size:11px; font-weight:700; letter-spacing:.5px; }
  .metodo.get { color:#5aa9e6; }
  .metodo.post { color:#e6a95a; }
  .desc { color:#6b7883; font-size:12.5px; }
  footer { margin-top:34px; padding-top:16px; border-top:1px solid #242b32;
           font-size:12.5px; color:#6b7883; }
  footer a { display:inline; color:#8fd6bd; }
  .aviso { background:#1c2a24; border-left:3px solid #4a8c74; padding:10px 14px;
           border-radius:5px; font-size:13px; margin-bottom:26px; color:#a8c4b8; }
</style>
</head>
<body>
<div class="caja">
  <h1>API · Sistema de cámaras</h1>
  <div class="sub">backend-nube · Vercel + Supabase</div>

  <div class="aviso">
    Esta es una <strong>API</strong>, no un panel: las rutas devuelven JSON.
    Los enlaces de abajo son navegables; las de <code>POST</code> necesitan un
    cliente HTTP (Postman, curl). Esta API nunca expone la imagen de cámara.
  </div>

  ${secciones}

  <footer>
    Estado del servicio: <a href="/api/salud">/api/salud</a> ·
    Referencia completa en <code>API_NUBE.md</code>
  </footer>
</div>
</body>
</html>`;

  return new Response(html, {
    status: 200,
    headers: { 'Content-Type': 'text/html; charset=utf-8' },
  });
}

async function rutasConcentrador(
  ruta: string,
  metodo: string,
  req: Peticion,
  url: URL,
): Promise<Response> {
  const db = supabase();

  // GET config?version=N → devuelve la config de todos los workers.
  // Si la versión no cambió, responde 304 (ahorro de ancho de banda).
  if (ruta === '/api/concentrador/config' && metodo === 'GET') {
    const versionPedida = Number(url.searchParams.get('version') ?? '-1');

    const { data, error } = await db
      .from('config_workers')
      .select('worker_id, payload, version');
    if (error) return json(500, { error: error.message });

    const filas = (data ?? []) as Array<{
      worker_id: string;
      payload: Record<string, unknown>;
      version: number;
    }>;
    // Versión global = suma de versiones por worker (cambia si alguno cambia)
    const versionGlobal = filas.reduce(
      (acc: number, f) => acc + Number(f.version),
      0,
    );

    if (versionPedida === versionGlobal) {
      return new Response(null, { status: 304 });
    }

    const workers: Record<string, unknown> = {};
    for (const f of filas) workers[f.worker_id] = f.payload;

    return json(200, { version: versionGlobal, workers });
  }

  // POST config → el concentrador publica cambios hechos en el front local
  if (ruta === '/api/concentrador/config' && metodo === 'POST') {
    const cuerpo = (await req.json()) as { workers?: Record<string, unknown> };
    const workers = cuerpo.workers ?? {};
    for (const [workerId, payload] of Object.entries(workers)) {
      await upsertConfig(db, workerId, payload as Record<string, unknown>);
    }
    return json(200, { ok: true, actualizados: Object.keys(workers).length });
  }

  // POST analisis → recibe un análisis de la IA
  if (ruta === '/api/concentrador/analisis' && metodo === 'POST') {
    const sobre = (await req.json()) as Record<string, unknown>;
    const workerId = sobre.worker_id as string | undefined;
    if (!workerId) return json(400, { error: 'falta worker_id' });

    const meta = (sobre.meta ?? {}) as Record<string, unknown>;

    // Registrar/actualizar el worker (auto-descubrimiento)
    await upsertWorker(db, workerId, sobre.esquema as string | undefined);

    const { error } = await db.from('analisis').upsert(
      {
        worker_id: workerId,
        evento_id: (sobre.evento_id as string) ?? null,
        timestamp: (sobre.timestamp as string) ?? new Date().toISOString(),
        esquema: (sobre.esquema as string) ?? null,
        datos: sobre.datos ?? {},
        modelo: (meta.modelo as string) ?? null,
        detail: (meta.detail as string) ?? null,
        latencia_ms: (meta.latencia_ms as number) ?? null,
        area_px: (meta.area_px as number) ?? null,
        score: (meta.score as number) ?? null,
      },
      { onConflict: 'evento_id', ignoreDuplicates: true },
    );
    if (error) return json(500, { error: error.message });

    return json(200, { ok: true, recibido: sobre.evento_id });
  }

  // POST estado → heartbeat con el estado de cada worker
  if (ruta === '/api/concentrador/estado' && metodo === 'POST') {
    const cuerpo = (await req.json()) as Record<string, unknown>;
    const alertas = Array.isArray(cuerpo.alertas) ? cuerpo.alertas : [];
    const { error } = await db.from('heartbeats').insert({
      concentrador_id: (cuerpo.concentrador_id as string) ?? null,
      timestamp: (cuerpo.timestamp as string) ?? new Date().toISOString(),
      workers: cuerpo.workers ?? {},
      alertas,
    });
    if (error) return json(500, { error: error.message });

    // Actualizar "ultima_vista", la salud y la config efectiva de cada worker
    const workers = (cuerpo.workers ?? {}) as Record<string, any>;
    for (const workerId of Object.keys(workers)) {
      const rt = workers[workerId] ?? {};
      await db
        .from('workers')
        .upsert(
          {
            worker_id: workerId,
            ultima_vista: new Date().toISOString(),
            camara_salud: rt.camara_salud ?? null,
            camara_motivo: rt.camara_motivo ?? null,
            config_efectiva: rt.efectiva ?? null,
          },
          { onConflict: 'worker_id' },
        );
    }
    return json(200, { ok: true, alertas: alertas.length });
  }

  return json(404, { error: 'ruta de concentrador no encontrada', ruta });
}

// ── Helpers de base de datos ─────────────────────────────────────────

/* eslint-disable @typescript-eslint/no-explicit-any */
async function upsertWorker(db: any, workerId: string, esquema?: string) {
  await db.from('workers').upsert(
    {
      worker_id: workerId,
      esquema: esquema ?? null,
      ultima_vista: new Date().toISOString(),
    },
    { onConflict: 'worker_id' },
  );
}

async function upsertConfig(
  db: any,
  workerId: string,
  payload: Record<string, unknown>,
) {
  const { data } = await db
    .from('config_workers')
    .select('version')
    .eq('worker_id', workerId)
    .maybeSingle();
  const version = Number(data?.version ?? 0) + 1;

  await db.from('config_workers').upsert(
    {
      worker_id: workerId,
      payload,
      version,
      actualizado: new Date().toISOString(),
    },
    { onConflict: 'worker_id' },
  );
}

// ── Consultas de lectura ─────────────────────────────────────────────

/**
 * Campos que el sistema externo puede modificar por cámara.
 *
 * Refleja lo que el worker acepta en `/api/externo/config`. La URL de la
 * cámara y el ROI NO están aquí a propósito: son hardware y encuadre
 * locales, y el worker los IGNORA si llegan desde fuera.
 *
 * Se expone en `GET /api/camaras/{id}/config/schema` para que un cliente
 * externo sepa qué puede enviar sin leer el código.
 */
const ESQUEMA_CONFIG = {
  descripcion:
    'Bloques de configuración que se pueden enviar a una cámara. Enviar ' +
    'solo las claves a cambiar; el resto se mantiene. El concentrador ' +
    'baja los cambios y los aplica al worker en caliente.',
  bloques: {
    deteccion: {
      descripcion: 'Cómo se decide si hubo un cambio en la imagen',
      campos: {
        metodo: {
          tipo: 'string',
          valores: ['ssim', 'diff', 'mse'],
          descripcion: 'ssim es robusto a sombras/luz; diff es más rápido',
        },
        min_area_px: {
          tipo: 'integer',
          descripcion:
            'Píxeles mínimos cambiados para disparar un evento. Es el ' +
            'filtro principal: súbelo para ignorar ruido.',
        },
        blur_ksize: {
          tipo: 'integer',
          descripcion: 'Desenfoque que elimina ruido de compresión (impar)',
        },
        umbral: { tipo: 'float', descripcion: 'Umbral de referencia' },
        marcar_cambios: {
          tipo: 'boolean',
          descripcion: 'Dibujar los contornos del cambio en la imagen',
        },
        frames_estables: {
          tipo: 'integer',
          descripcion: 'Capturas consecutivas para confirmar un cambio',
        },
        min_intervalo_eventos: {
          tipo: 'float',
          descripcion: 'Segundos mínimos entre eventos (anti-rebote)',
        },
        alinear_imagenes: {
          tipo: 'boolean',
          descripcion: 'Compensar vibración de la cámara',
        },
        max_desplazamiento: {
          tipo: 'float',
          descripcion: 'Desplazamiento máximo a corregir (píxeles)',
        },
      },
    },
    captura: {
      descripcion: 'Ritmo de captura',
      campos: {
        intervalo_segundos: {
          tipo: 'float',
          descripcion: 'Segundos entre capturas',
        },
        rotacion: {
          tipo: 'integer',
          valores: [0, 90, 180, 270],
          descripcion:
            'Grados de giro. OJO: al cambiarlo el ROI dibujado queda ' +
            'desalineado y hay que redibujarlo desde el front local.',
        },
      },
    },
    ia: {
      descripcion: 'Análisis con IA de visión',
      campos: {
        esquema: {
          tipo: 'string',
          descripcion: 'Nombre del formato del JSON que produce el prompt',
        },
        model: { tipo: 'string', descripcion: 'Modelo de visión' },
        detail: {
          tipo: 'string',
          valores: ['low', 'high', 'auto'],
          descripcion: 'Nivel de detalle que se envía a la IA',
        },
      },
    },
  },
  no_modificables: {
    'captura.camara_fuente': 'URL de la cámara: es hardware local',
    'captura.fuente': 'Tipo de fuente: local',
    roi: 'Se dibuja sobre el video en el front local',
    prompt: 'Protegido con contrasena en el worker',
  },
};

/**
 * Config vigente de una cámara.
 *
 * Devuelve el payload tal cual como lo consume el concentrador, más
 * metadatos útiles para un cliente externo.
 */
async function leerConfigCamara(camaraId: string): Promise<Response> {
  const db = supabase();
  const { data, error } = await db
    .from('config_workers')
    .select('worker_id, payload, version, actualizado')
    .eq('worker_id', camaraId)
    .maybeSingle();
  if (error) return json(500, { error: error.message });
  if (!data) {
    // La cámara existe pero nunca se le configuró nada
    return json(200, {
      camara_id: camaraId,
      config: {},
      version: 0,
      actualizado: null,
      nota: 'Sin configuración publicada: el worker usa sus valores locales',
    });
  }
  return json(200, {
    camara_id: (data as any).worker_id,
    config: (data as any).payload ?? {},
    version: (data as any).version,
    actualizado: (data as any).actualizado,
  });
}

/**
 * Modifica la config de una cámara.
 *
 * El cuerpo es `{ "config": { ...bloques } }` y se **fusiona** con lo que
 * ya había (no reemplaza el objeto completo), para que un cliente pueda
 * cambiar un solo parámetro sin reenviar todo.
 *
 * El concentrador baja el cambio en su próximo ciclo y lo aplica al worker.
 */
async function escribirConfigCamara(
  camaraId: string,
  req: Peticion,
): Promise<Response> {
  const db = supabase();

  // ¿Existe la cámara?
  const { data: existe } = await db
    .from('workers')
    .select('worker_id, nombre')
    .eq('worker_id', camaraId)
    .maybeSingle();
  if (!existe) {
    return json(404, {
      error: 'cámara no encontrada',
      camara_id: camaraId,
      sugerencia: 'Consulta GET /api/camaras para ver los ids disponibles',
    });
  }

  const cuerpo = (await req.json().catch(() => ({}))) as Record<string, any>;
  // Aceptar tanto {config:{...}} como el payload directo
  const nuevos = (cuerpo.config ?? cuerpo) as Record<string, any>;
  if (!nuevos || typeof nuevos !== 'object' || Array.isArray(nuevos)) {
    return json(400, { error: 'se esperaba un objeto de configuración' });
  }

  // Validar ANTES de guardar. El worker también valida, pero si se guardara
  // algo inválido el cliente recibiría 200 y creería que aplicó: el error
  // solo aparecería en el log del concentrador, en silencio para el cliente.
  const errores = validarConfig(nuevos);
  if (errores.length) {
    // Separar los campos prohibidos para dar un mensaje más útil
    const prohibidos = errores.filter((e) => e.includes('no se puede cambiar'));
    return json(400, {
      error: 'configuración inválida',
      errores,
      ...(prohibidos.length
        ? { no_modificables: ESQUEMA_CONFIG.no_modificables }
        : {}),
      sugerencia: 'Consulta GET /api/camaras/{id}/config/schema',
    });
  }

  // Fusión con lo existente, bloque por bloque
  const { data: actual } = await db
    .from('config_workers')
    .select('payload, version')
    .eq('worker_id', camaraId)
    .maybeSingle();
  const previo = ((actual as any)?.payload ?? {}) as Record<string, any>;
  const fusionado: Record<string, any> = { ...previo };
  for (const [bloque, valor] of Object.entries(nuevos)) {
    if (valor && typeof valor === 'object' && !Array.isArray(valor)) {
      // Si el bloque queda vacío tras rechazar campos, no se guarda
      // (evita dejar `"captura": {}` suelto en la base).
      const combinado = { ...(previo[bloque] ?? {}), ...valor };
      if (Object.keys(combinado).length === 0) delete fusionado[bloque];
      else fusionado[bloque] = combinado;
    } else {
      fusionado[bloque] = valor;
    }
  }

  const version = Number((actual as any)?.version ?? 0) + 1;
  const { error } = await db.from('config_workers').upsert(
    {
      worker_id: camaraId,
      payload: fusionado,
      version,
      actualizado: new Date().toISOString(),
    },
    { onConflict: 'worker_id' },
  );
  if (error) return json(500, { error: error.message });

  return json(200, {
    ok: true,
    camara_id: camaraId,
    version,
    aplicado: nuevos,
    nota:
      'El concentrador bajará el cambio en su próximo ciclo y lo aplicará ' +
      'al worker en caliente. La URL de la cámara nunca se cambia desde aquí.',
  });
}

/**
 * Reglas de validación por campo.
 *
 * Los rangos coinciden con los que aplica el worker en
 * `_aplicar_config` (`worker/backend/web.py`). Si se cambian allí, hay que
 * cambiarlos aquí: si no, la API aceptaría algo que el worker rechaza, y el
 * cliente creería que guardó bien.
 *
 * `tipo` solo describe el tipo para construir el mensaje de error.
 */
const REGLAS: Record<
  string,
  Record<
    string,
    { tipo: string; valores?: unknown[]; min?: number; entero?: boolean }
  >
> = {
  deteccion: {
    metodo: { tipo: 'string', valores: ['ssim', 'diff', 'mse'] },
    umbral: { tipo: 'number', min: 0 },
    min_area_px: { tipo: 'integer', min: 0, entero: true },
    blur_ksize: { tipo: 'integer', min: 0, entero: true },
    marcar_cambios: { tipo: 'boolean' },
    frames_estables: { tipo: 'integer', min: 1, entero: true },
    min_intervalo_eventos: { tipo: 'number', min: 0 },
    alinear_imagenes: { tipo: 'boolean' },
    max_desplazamiento: { tipo: 'number', min: 1 },
  },
  captura: {
    intervalo_segundos: { tipo: 'number', min: 0.01 },
    rotacion: { tipo: 'integer', valores: [0, 90, 180, 270], entero: true },
  },
  ia: {
    esquema: { tipo: 'string' },
    model: { tipo: 'string' },
    detail: { tipo: 'string', valores: ['low', 'high', 'auto'] },
  },
};

/** Campos que existen en el esquema pero NO se pueden cambiar desde afuera. */
const PROHIBIDOS = new Set([
  'camara_fuente', 'fuente', 'region', 'roi', 'prompt', 'nombre_camara',
  'worker_id', 'reconectar_segundos', 'monitor', 'aplicar_preset_al_iniciar',
]);

/**
 * Valida un payload de configuración contra REGLAS.
 *
 * Devuelve la lista de errores encontrados (vacía si todo está bien). Se
 * acumulan todos en vez de fallar al primero, para que el cliente pueda
 * corregir de una vez.
 */
function validarConfig(datos: Record<string, any>): string[] {
  const errores: string[] = [];

  for (const [bloque, valor] of Object.entries(datos)) {
    const reglas = REGLAS[bloque];

    if (!reglas) {
      errores.push(
        `bloque desconocido: "${bloque}" (válidos: ${Object.keys(REGLAS).join(', ')})`,
      );
      continue;
    }
    if (valor === null || typeof valor !== 'object' || Array.isArray(valor)) {
      errores.push(`"${bloque}" debe ser un objeto`);
      continue;
    }

    for (const [campo, v] of Object.entries(valor as Record<string, unknown>)) {
      const ruta = `${bloque}.${campo}`;
      const regla = reglas[campo];

      if (!regla) {
        if (PROHIBIDOS.has(campo)) {
          errores.push(
            `${ruta}: no se puede cambiar desde afuera ` +
              '(es hardware o encuadre local)',
          );
        } else {
          errores.push(
            `${ruta}: campo desconocido (válidos: ${Object.keys(reglas).join(', ')})`,
          );
        }
        continue;
      }

      // Valores permitidos explícitos
      if (regla.valores && !regla.valores.includes(v)) {
        errores.push(
          `${ruta}: "${String(v)}" no es válido ` +
            `(permitidos: ${regla.valores.map((x) => JSON.stringify(x)).join(', ')})`,
        );
        continue;
      }

      // Tipo booleano
      if (regla.tipo === 'boolean') {
        if (typeof v !== 'boolean') {
          errores.push(`${ruta}: se esperaba true o false`);
        }
        continue;
      }

      // Tipos numéricos
      if (regla.tipo === 'number' || regla.tipo === 'integer') {
        if (typeof v !== 'number' || !Number.isFinite(v)) {
          errores.push(`${ruta}: se esperaba un número`);
          continue;
        }
        if (regla.entero && !Number.isInteger(v)) {
          errores.push(`${ruta}: se esperaba un entero`);
          continue;
        }
        if (regla.min !== undefined && v < regla.min) {
          errores.push(`${ruta}: debe ser >= ${regla.min} (recibido ${v})`);
        }
        continue;
      }

      // Strings
      if (regla.tipo === 'string') {
        if (typeof v !== 'string' || v.trim() === '') {
          errores.push(`${ruta}: se esperaba un texto no vacío`);
        }
      }
    }
  }

  return errores;
}

async function listarWorkers(): Promise<Response> {
  const db = supabase();
  const { data, error } = await db
    .from('workers')
    .select('worker_id, nombre, esquema, ultima_vista')
    .order('worker_id');
  if (error) return json(500, { error: error.message });
  return json(200, { workers: data ?? [] });
}

/**
 * Salud de las cámaras, tomada del ÚLTIMO heartbeat de cada concentrador.
 *
 * Sirve para que un dashboard avise "esta cámara dejó de entregar imagen"
 * sin tener que interpretar el estado worker por worker.
 *
 * `GET /api/camaras`            → todas
 * `GET /api/camaras?estado=ok`  → solo las que están bien
 * `GET /api/camaras?estado=alerta` → solo las que tienen problemas
 */
async function listarSaludCamaras(url: URL): Promise<Response> {
  const db = supabase();
  const filtro = url.searchParams.get('estado');

  const { data, error } = await db
    .from('heartbeats')
    .select('concentrador_id, timestamp, workers')
    .order('timestamp', { ascending: false })
    .limit(50);
  if (error) return json(500, { error: error.message });

  // Quedarse con el heartbeat MÁS RECIENTE de cada concentrador
  const ultimoPorConc = new Map<string, any>();
  for (const fila of data ?? []) {
    const id = (fila.concentrador_id as string) ?? '(sin id)';
    if (!ultimoPorConc.has(id)) ultimoPorConc.set(id, fila);
  }

  const camaras: any[] = [];
  for (const [concentradorId, fila] of ultimoPorConc) {
    const workers = (fila.workers ?? {}) as Record<string, any>;
    for (const [workerId, rt] of Object.entries(workers)) {
      const salud = rt.camara_salud ?? (rt.camara_viva ? 'ok' : 'sin_senal');
      camaras.push({
        concentrador_id: concentradorId,
        worker_id: workerId,
        salud,
        motivo: rt.camara_motivo ?? '',
        con_deteccion: salud === 'ok',
        resolucion: rt.resolucion ?? null,
        ia_activa: rt.ia_activa ?? false,
        capturas: rt.capturas ?? 0,
        eventos: rt.eventos ?? 0,
        visto: fila.timestamp,
        // Config que corre DE VERDAD en el worker (no lo que se configuró
        // desde afuera, que puede estar incompleto).
        efectiva: rt.efectiva ?? null,
      });
    }
  }

  const alertas = camaras.filter((c) => c.salud !== 'ok');
  const resultado =
    filtro === 'ok'
      ? camaras.filter((c) => c.salud === 'ok')
      : filtro === 'alerta'
        ? alertas
        : camaras;

  return json(200, {
    camaras: resultado,
    alertas,
    total: camaras.length,
    todas_ok: alertas.length === 0,
    // Pistas para que un cliente externo sepa qué hacer después
    endpoints: {
      config: 'GET|POST /api/camaras/{camara_id}/config',
      esquema: 'GET /api/camaras/{camara_id}/config/schema',
    },
  });
}

async function listarAnalisis(url: URL): Promise<Response> {
  const db = supabase();
  const workerId = url.searchParams.get('worker_id');
  const limite = Math.min(Number(url.searchParams.get('limit') ?? '50'), 500);

  let consulta = db
    .from('analisis')
    .select('*')
    .order('timestamp', { ascending: false })
    .limit(limite);
  if (workerId) consulta = consulta.eq('worker_id', workerId);

  const { data, error } = await consulta;
  if (error) return json(500, { error: error.message });
  return json(200, { analisis: data ?? [] });
}

async function listarEventos(url: URL): Promise<Response> {
  const db = supabase();
  const workerId = url.searchParams.get('worker_id');
  const limite = Math.min(Number(url.searchParams.get('limit') ?? '50'), 500);

  let consulta = db
    .from('eventos')
    .select('*')
    .order('timestamp', { ascending: false })
    .limit(limite);
  if (workerId) consulta = consulta.eq('worker_id', workerId);

  const { data, error } = await consulta;
  if (error) return json(500, { error: error.message });
  return json(200, { eventos: data ?? [] });
}
