"""Panel web unificado del concentrador.

Sirve el mismo panel que antes vivía en cada worker, pero **una sola vez** y
con un selector para elegir cuál worker se está viendo. Así no hay que abrir
una pestaña por cámara.

Cómo funciona
-------------
El panel (HTML + JS) vive en `compartido/panel.html` y usa rutas relativas
(`fetch('/api/config')`, `imagen.src = '/video'`...). Este módulo:

  1. Sirve el panel en `/`.
  2. Reenvía (proxy) **todas** las demás rutas al worker activo.
  3. Añade un selector de worker y las rutas `/api/workers-panel` para
     listarlos y `/api/worker-activo` para cambiarlo.

El video (`/video`) se reenvía en streaming, así el navegador lo consume igual
que cuando hablaba directo con el worker. Cuando el concentrador corre en la
misma máquina que los workers el salto es por loopback, con impacto mínimo.
"""

import json
import logging
import threading
import urllib.error
import urllib.request
from pathlib import Path

from flask import Flask, Response, jsonify, request

logger = logging.getLogger("concentrador")

# Ruta al panel compartido (mismo archivo que sirve el worker).
RAIZ = Path(__file__).resolve().parent.parent.parent
RUTA_PANEL = RAIZ / "compartido" / "panel.html"

# Cabeceras que NO se deben reenviar al worker (las gestiona el proxy).
_CABECERAS_EXCLUIDAS = {"host", "connection", "accept-encoding",
                        "content-length", "transfer-encoding"}


def leer_panel() -> str:
    """Devuelve el HTML del panel compartido."""
    try:
        return RUTA_PANEL.read_text(encoding="utf-8")
    except OSError as e:
        logger.error("No se pudo leer el panel %s: %s", RUTA_PANEL, e)
        return ("<h1>Panel no encontrado</h1>"
                f"<p>Se esperaba en: {RUTA_PANEL}</p>")


def pagina_api(workers: dict, worker_activo: str | None) -> Response:
    """Página navegable con las rutas de la API del concentrador.

    Vive en `/api` (no en `/`, que sirve el panel de la cámara) para que
    quien llegue buscando la API encuentre algo mejor que un 404.

    Las rutas del worker se obtienen de su propio `/api` cuando se puede
    consultar, así que la lista refleja lo que hay de verdad y no una copia
    que se queda desactualizada.
    """
    import html as _html

    base = f"http://{request.host}"

    propias = [
        ("GET", "/api", "Esta página"),
        ("GET", "/api/workers-panel", "Workers configurados y cuál está activo"),
        ("POST", "/api/worker-activo", "Cambiar el worker activo"),
        ("GET", "/api/salud-camaras", "Salud de TODAS las cámaras"),
        ("GET", "/", "El panel web (video + ROI + parámetros)"),
        ("GET", "/video", "Stream de video, reenviado al worker activo"),
    ]

    # Rutas del worker: se piden a su propio /api si está alcanzable
    del_worker: list = []
    error_worker = ""
    worker = workers.get(worker_activo) if worker_activo else None
    if worker is None and workers:
        worker = next(iter(workers.values()))
    if worker is not None:
        try:
            datos = worker._get("/api", timeout=5)
            if isinstance(datos, dict):
                for grupo in datos.get("grupos", []):
                    for r in grupo.get("rutas", []):
                        del_worker.append((grupo.get("titulo", ""), r))
        except Exception as e:  # noqa: BLE001 — la página se muestra igual
            error_worker = str(e)

    def fila(metodo: str, ruta: str, desc: str, activo=True) -> str:
        ruta_esc = _html.escape(ruta)
        metodo_esc = _html.escape(metodo)
        desc_esc = _html.escape(desc)
        cls = metodo.lower()
        if activo and metodo == "GET" and not ruta.startswith("/video"):
            cuerpo = (f'<a href="{ruta_esc}"><span class="metodo {cls}">'
                      f'{metodo_esc}</span><code>{ruta_esc}</code></a>')
        else:
            cuerpo = (f'<span class="metodo {cls}">{metodo_esc}</span>'
                      f'<code>{ruta_esc}</code>')
        return f'<li>{cuerpo}<span class="desc">{desc_esc}</span></li>'

    secciones = [
        '<section><h2>API del concentrador</h2>'
        '<p class="nota">Rutas propias de este proceso, en '
        f'{_html.escape(base)}.</p><ul>'
        + "".join(fila(*r) for r in propias)
        + "</ul></section>",
    ]

    if del_worker:
        por_grupo: dict = {}
        for titulo, r in del_worker:
            por_grupo.setdefault(titulo, []).append(r)
        for titulo, rutas in por_grupo.items():
            secciones.append(
                f'<section><h2>{_html.escape(titulo)}</h2>'
                '<p class="nota">Reenviadas al worker activo '
                f'(<code>{_html.escape(worker_activo or "")}</code>) '
                'a través del proxy.</p><ul>'
                + "".join(
                    fila(r.get("metodo", "GET"), r.get("ruta", ""),
                         r.get("descripcion", ""))
                    for r in rutas)
                + "</ul></section>")
    else:
        motivo = _html.escape(error_worker) if error_worker else "sin worker activo"
        secciones.append(
            '<section><h2>API del worker</h2>'
            f'<p class="nota">No disponible ({motivo}).</p></section>')

    html_doc = _PLANTILLA_API.replace("{{SECCIONES}}", "".join(secciones))
    return Response(html_doc, mimetype="text/html")


_PLANTILLA_API = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>API · Concentrador</title>
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
  .metodo.delete { color:#e07a7a; }
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
  <h1>API · Concentrador</h1>
  <div class="sub">Agrega varios workers (cámaras) y habla con la nube</div>

  <div class="aviso">
    El <strong>panel web</strong> está en <a href="/">la raíz</a>; esta página
    lista la <strong>API</strong>. Las rutas del worker se descubren en vivo,
    así que lo que ves es lo que hay. Las de <code>POST</code> necesitan un
    cliente HTTP (Postman, curl).
  </div>

  {{SECCIONES}}

  <footer>
    Referencia completa en <code>API.md</code> (worker) y
    <code>API_NUBE.md</code> (nube) ·
    <a href="/api/salud-camaras">Estado de las cámaras</a>
  </footer>
</div>
</body>
</html>"""


def _inyectar_selector(html: str) -> str:
    """Inserta el selector de workers en la barra superior del panel.

    Se hace por inyección de texto para no duplicar el HTML del panel: el
    archivo compartido sigue siendo el mismo que usa el worker.
    """
    marcador = "<h1>"
    if marcador not in html:
        return html

    selector = """
<div id="selector-workers" style="
      display:flex; align-items:center; gap:10px; flex-wrap:wrap;
      background:#252526; border:1px solid #3c3c3c; border-radius:6px;
      padding:10px 14px; margin:0 0 14px;">
  <label style="font-size:13px; color:#aaa;">Worker:</label>
  <select id="sel-worker" style="
        background:#1e1e1e; color:#eee; border:1px solid #3c3c3c;
        border-radius:4px; padding:6px 10px; font-size:13px; min-width:200px;">
    <option value="">Cargando…</option>
  </select>
  <span id="estado-worker" style="font-size:12px; color:#81c784;"></span>
</div>
<script>
// El panel usa rutas relativas, así que basta con indicar al backend qué
// worker está activo: el proxy del concentrador reenvía todo hacia él.
async function cargarWorkers() {
  const sel = document.getElementById('sel-worker');
  const est = document.getElementById('estado-worker');
  try {
    const r = await fetch('/api/workers-panel');
    const d = await r.json();
    const activo = d.activo;
    sel.innerHTML = '';
    for (const w of d.workers) {
      const op = document.createElement('option');
      op.value = w.id;
      op.textContent = w.id + (w.vivo ? '' : '  (sin conexión)');
      if (w.id === activo) op.selected = true;
      sel.appendChild(op);
    }
    if (!d.workers.length) {
      sel.innerHTML = '<option value="">Sin workers configurados</option>';
    }
    est.textContent = '';
  } catch (e) {
    est.textContent = '⚠️ No se pudo listar los workers';
    est.style.color = '#ffb74d';
  }
}

document.getElementById('sel-worker').addEventListener('change', async (ev) => {
  const est = document.getElementById('estado-worker');
  const id = ev.target.value;
  if (!id) return;
  est.textContent = 'Cambiando…';
  est.style.color = '#aaa';
  try {
    const r = await fetch('/api/worker-activo', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({id: id}),
    });
    const d = await r.json();
    if (!d.ok) throw new Error(d.error || 'error');
    est.textContent = '✅ ' + id;
    est.style.color = '#81c784';
    // Recargar para que todas las vistas apunten al worker nuevo
    setTimeout(() => location.reload(), 350);
  } catch (e) {
    est.textContent = '⚠️ ' + e.message;
    est.style.color = '#ffb74d';
  }
});

cargarWorkers();
</script>
"""
    # Insertar el selector justo antes del primer <h1>
    return html.replace(marcador, selector + marcador, 1)


class ProxyPanel:
    """Sirve el panel y hace proxy de las peticiones al worker activo.

    Mantiene el worker activo en memoria (`worker_activo`). El mapa de
    workers viene del `config.yaml` del concentrador.
    """

    def __init__(self, workers: dict, host: str = "0.0.0.0",
                 puerto: int = 8080, timeout: int = 15):
        # workers: {id: Worker} (los objetos de clientes.py)
        self.workers = workers
        self.host = host
        self.puerto = puerto
        self.timeout = timeout
        self._lock = threading.Lock()
        # El primer worker configurado arranca como activo
        self.worker_activo = next(iter(workers), None) if workers else None

        self.app = self._crear_app()

    # ── Selección de worker ────────────────────────────────────────

    def _activo(self):
        """Devuelve el objeto Worker activo (o None)."""
        with self._lock:
            if self.worker_activo is None:
                return None
            return self.workers.get(self.worker_activo)

    # ── Construcción de la app Flask ───────────────────────────────

    def _crear_app(self) -> Flask:
        app = Flask("panel-concentrador")
        # Silenciar el log de cada request (el proxy genera mucho ruido)
        logging.getLogger("werkzeug").setLevel(logging.WARNING)

        @app.route("/")
        def pagina():
            return _inyectar_selector(leer_panel())

        @app.route("/api")
        def api_documentacion():
            """Página navegable con las rutas de la API del concentrador.

            Vive en /api (no en /) porque / sirve el panel de la cámara.
            El proxy genérico de abajo captura cualquier ruta no definida,
            así que esta DEBE declararse antes para no acabar reenviada.
            """
            return pagina_api(self.workers, self.worker_activo)

        @app.route("/api/workers-panel")
        def api_workers_panel():
            """Lista los workers configurados y cuál está activo."""
            lista = []
            for wid, worker in self.workers.items():
                lista.append({"id": wid, "vivo": worker.vivo()})
            return jsonify({"workers": lista, "activo": self.worker_activo})

        @app.route("/api/worker-activo", methods=["POST"])
        def api_worker_activo():
            """Cambia el worker activo."""
            datos = request.get_json(silent=True) or {}
            nuevo = datos.get("id")
            if nuevo not in self.workers:
                return jsonify({"ok": False,
                                "error": f"worker desconocido: {nuevo}"}), 400
            with self._lock:
                self.worker_activo = nuevo
            logger.info("🔄 Worker activo: %s", nuevo)
            return jsonify({"ok": True, "activo": nuevo})

        @app.route("/api/salud-camaras")
        def api_salud_camaras():
            """
            Salud de TODAS las cámaras del concentrador, en una sola llamada.

            Pensado para monitoreo: permite saber si alguna cámara dejó de
            entregar imagen sin tener que consultar worker por worker.

            Cada worker se consulta a su vez en su `/api/salud`.
            """
            camaras = []
            for wid, worker in self.workers.items():
                try:
                    datos = worker._get("/api/salud", timeout=5)
                except Exception:  # noqa: BLE001 — se reporta como caído
                    datos = None
                if not isinstance(datos, dict):
                    camaras.append({
                        "worker_id": wid,
                        "salud": "worker_caido",
                        "motivo": "El worker no responde a la API",
                        "con_deteccion": False,
                    })
                    continue
                camaras.append({
                    "worker_id": wid,
                    "salud": datos.get("camara_salud", "desconocido"),
                    "motivo": datos.get("camara_motivo", ""),
                    "con_deteccion": bool(datos.get("con_deteccion", False)),
                    "capturas": datos.get("capturas", 0),
                    "eventos": datos.get("cambios", 0),
                })
            problemas = [c for c in camaras if c["salud"] != "ok"]
            return jsonify({
                "camaras": camaras,
                "alertas": problemas,
                "todas_ok": not problemas,
            })

        # ── Proxy genérico ─────────────────────────────────────────
        # Atrapa cualquier ruta no definida arriba y la reenvía al worker.
        @app.route("/<path:ruta>", methods=["GET", "POST", "DELETE", "PUT"])
        def proxy(ruta):
            return self._reenviar(ruta)

        @app.route("/", defaults={"ruta": ""}, methods=["GET"])
        def proxy_raiz(ruta):
            return self._reenviar(ruta)

        return app

    # ── Reenvío ────────────────────────────────────────────────────

    def _reenviar(self, ruta: str) -> Response:
        """Reenvía la petición actual al worker activo."""
        worker = self._activo()
        if worker is None:
            return jsonify({"error": "No hay worker activo configurado"}), 503

        # Query string tal cual (el panel usa ?version=..., etc.)
        qs = request.query_string.decode("utf-8")
        destino = f"{worker.url}/{ruta}"
        if qs:
            destino += f"?{qs}"

        # Cabeceras: copiar las del cliente, excepto las que gestiona Flask
        cabeceras = {
            k: v for k, v in request.headers.items()
            if k.lower() not in _CABECERAS_EXCLUIDAS
        }
        if worker.token:
            cabeceras["Authorization"] = f"Bearer {worker.token}"

        # Cuerpo (si lo hay)
        datos = request.get_data() if request.method != "GET" else None

        try:
            peticion = urllib.request.Request(
                destino, data=datos, headers=cabeceras,
                method=request.method)
            respuesta = urllib.request.urlopen(peticion, timeout=self.timeout)
        except urllib.error.HTTPError as e:
            # Errores del worker: reenviarlos tal cual (4xx/5xx)
            return Response(e.read(), status=e.code,
                            mimetype=e.headers.get("Content-Type"))
        except (urllib.error.URLError, OSError) as e:
            logger.warning("Proxy → %s falló: %s", destino, e)
            return jsonify({"error": f"Worker no disponible: {e}"}), 502

        tipo = respuesta.headers.get("Content-Type", "")
        # El video y el SSE son STREAMS: se reenvían por trozos, sin
        # bufferizar (si se acumulara, el video se vería con retraso).
        if "multipart/x-mixed-replace" in tipo or "text/event-stream" in tipo:
            # IMPORTANTE: reenviar el Content-Type COMPLETO, incluido el
            # `boundary`. Sin él el navegador no puede separar los frames
            # del MJPEG y no muestra nada.
            return Response(
                self._generar_stream(respuesta),
                status=respuesta.status,
                headers={"Content-Type": tipo,
                         "Cache-Control": "no-cache",
                         "Pragma": "no-cache",
                         "X-Accel-Buffering": "no"})

        # Respuesta normal (JSON, imágenes...): devolver completa.
        # Se conservan las cabeceras relevantes del worker (el panel usa
        # `Content-Type` para saber si es JSON, imagen, etc.).
        cuerpo = respuesta.read()
        cabeceras = {}
        for nombre in ("Content-Type", "Cache-Control", "ETag",
                       "Last-Modified", "Content-Disposition"):
            valor = respuesta.headers.get(nombre)
            if valor:
                cabeceras[nombre] = valor
        return Response(cuerpo, status=respuesta.status, headers=cabeceras)

    @staticmethod
    def _generar_stream(respuesta):
        """Itera el stream del worker trozo a trozo, sin acumular.

        Usa `read1()` en vez de `read(8192)`: `read(n)` BLOQUEA hasta juntar
        n bytes, y en un stream SSE los mensajes son de ~20 bytes y llegan
        espaciados, así que acumularlos introduciría un retraso artificial
        (el panel parecería no actualizarse). `read1()` devuelve en cuanto
        hay datos disponibles.
        """
        try:
            while True:
                trozo = respuesta.read1(65536)
                if not trozo:
                    break
                yield trozo
        except (urllib.error.URLError, OSError, ValueError):
            # ValueError: el cliente cerró la conexión (pestaña cerrada)
            pass
        finally:
            respuesta.close()

    # ── Arranque ───────────────────────────────────────────────────

    def iniciar_en_hilo(self) -> threading.Thread:
        """Arranca el panel en un hilo (no bloquea el bucle principal)."""
        hilo = threading.Thread(
            target=self.app.run,
            kwargs={"host": self.host, "port": self.puerto,
                    "debug": False, "use_reloader": False,
                    "threaded": True},
            daemon=True,
        )
        hilo.start()
        return hilo
