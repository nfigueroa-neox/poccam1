"""Cliente de la API de ingesta de estado de equipos (Grupo Weizhou).

Informa en tiempo real el **estado de funcionamiento** de cada máquina
(lavadora, secadora, planchadora, dobladora), a partir de lo que la IA
detecta en el panel de cada cámara.

    POST https://weizhou.vercel.app/api/equipos/estado
    x-api-key: <clave>

Diseño
------
1. **Solo transiciones.** La API pide explícitamente que se envíe cuando el
   estado *cambia*, no en cada análisis. Si la máquina lleva 10 minutos en
   uso y la IA responde `en_uso: true` en cada análisis, se envía UNA vez.
   Esto evita llenar la bitácora de eventos repetidos.

2. **El estado se recuerda por máquina**, no por análisis. Se guarda en
   disco (`estado_weizhou.json`) para que un reinicio del concentrador no
   dispare un envío redundante.

3. **Cola offline.** Si la API no responde, el envío se encola y se reintenta,
   igual que los análisis hacia la nube.

El `worker_id` local se usa como `codigo` de la máquina (el sitio puede
asignar códigos externos como `LAV-01` o reutilizar el id del worker).
"""

import json
import logging
import threading
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("concentrador")

# Niveles de estado reconocidos (la API acepta estos valores en `estado`).
ESTADO_LIBRE = "libre"
ESTADO_EN_USO = "en_uso"


class ClienteWeizhou:
    """Envía el estado de las máquinas a la API de Weizhou.

    Mantiene en disco el último estado enviado por máquina para enviar solo
    transiciones.
    """

    def __init__(self, base_url: str, api_key: str, timeout: int = 10,
                 ruta_estado: str | Path | None = None,
                 mapeo: dict | None = None):
        """
        `mapeo` traduce el `worker_id` local a los identificadores de Weizhou:

            {"panel-1": {"maquina_id": "5b16...", "camara_id": "9536..."}}

        Si un worker no está en el mapeo, se envía su `worker_id` como
        `codigo` (Weizhou lo resuelve si tiene el código externo asignado).
        """
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.mapeo = mapeo or {}
        self.ruta_estado = Path(
            ruta_estado or Path(__file__).resolve().parent.parent /
            "estado_weizhou.json")
        self._lock = threading.Lock()
        self._estados = self._leer_estados()
        # Máquinas cuyo último envío FALLÓ: su estado no está confirmado en
        # Weizhou, así que el próximo análisis debe reintentar aunque el
        # estado parezca no haber cambiado.
        self._pendientes: set[str] = set()
        # Estadística de la sesión
        self.enviados = 0
        self.omitidos = 0
        # Última respuesta cruda del destino (para diagnóstico)
        self.ultima_respuesta = ""

    # ── Persistencia del último estado enviado ─────────────────────

    def _leer_estados(self) -> dict:
        """Lee el último estado enviado por máquina ({} si no existe)."""
        try:
            if self.ruta_estado.exists():
                datos = json.loads(self.ruta_estado.read_text(encoding="utf-8"))
                if isinstance(datos, dict):
                    return datos
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("No se pudo leer %s: %s", self.ruta_estado, e)
        return {}

    def _guardar_estados(self):
        try:
            self.ruta_estado.write_text(
                json.dumps(self._estados, ensure_ascii=False, indent=2),
                encoding="utf-8")
        except OSError as e:
            logger.warning("No se pudo guardar el estado de Weizhou: %s", e)

    def ultimo_estado(self, maquina: str) -> str | None:
        """Último estado enviado para esa máquina (None si nunca se envió)."""
        with self._lock:
            return self._estados.get(maquina)

    # ── Envío ──────────────────────────────────────────────────────

    def _cabeceras(self) -> dict:
        return {"Content-Type": "application/json",
                "x-api-key": self.api_key}

    def _post(self, cuerpo: dict) -> bool:
        datos = json.dumps(cuerpo, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/api/equipos/estado", data=datos,
            headers=self._cabeceras(), method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                # La respuesta se guarda y se registra: si el destino
                # responde 2xx pero con ok:false, no debemos darlo por
                # enviado (si lo diéramos, el estado quedaría
                # desincronizado para siempre, porque solo se envían
                # transiciones).
                crudo = r.read(2000)
                estado_http = r.status
            texto = crudo.decode("utf-8", errors="replace").strip()
            self.ultima_respuesta = texto[:500]
            if not (200 <= estado_http < 300):
                logger.warning("Weizhou → HTTP %s: %s", estado_http, texto)
                return False
            # 2xx: puede traer ok:false sin usar un código de error
            try:
                cuerpo_resp = json.loads(texto) if texto else {}
            except json.JSONDecodeError:
                cuerpo_resp = None
            if isinstance(cuerpo_resp, dict) and cuerpo_resp.get("ok") is False:
                logger.error("Weizhou respondió 2xx pero con error: %s", texto)
                return False
            logger.debug("Weizhou → HTTP %s | respuesta: %s", estado_http,
                         texto or "(vacía)")
            logger.info("✅ Weizhou registró el envío (HTTP %s) | respuesta: %s",
                        estado_http, texto or "(vacía)")
            return True
        except urllib.error.HTTPError as e:
            cuerpo_error = e.read()[:300]
            self.ultima_respuesta = cuerpo_error.decode("utf-8",
                                                        errors="replace")
            # 404 = la máquina no existe en Weizhou: no tiene sentido reintentar
            if e.code in (400, 401, 404):
                logger.error("Weizhou rechazó el envío (HTTP %s): %s",
                             e.code, cuerpo_error)
            else:
                logger.warning("Weizhou → HTTP %s: %s", e.code, cuerpo_error)
            return False
        except (urllib.error.URLError, OSError) as e:
            logger.warning("Weizhou no responde: %s", e)
            self.ultima_respuesta = f"(sin respuesta: {e})"
            return False

    def enviar_si_cambia(self, maquina: str, en_uso: bool,
                         confianza: float | None = None,
                         notas: str = "", payload: dict | None = None,
                         evento_id: str | None = None) -> str:
        """Envía el estado SOLO si cambió respecto al último enviado.

        Devuelve uno de:
          • "enviado"   → se envió (transición real)
          • "sin_cambio"→ el estado es el mismo; no se envió nada
          • "error"     → se intentó y falló (queda para reintento)
        """
        nuevo = ESTADO_EN_USO if en_uso else ESTADO_LIBRE

        with self._lock:
            anterior = self._estados.get(maquina)
            pendiente = maquina in self._pendientes
            if anterior == nuevo and not pendiente:
                self.omitidos += 1
                logger.info("Weizhou: %s sin cambios ('%s'); no se envía",
                            maquina, nuevo)
                return "sin_cambio"
            if pendiente:
                logger.info("Weizhou: %s tenía un envío pendiente; reintentando",
                            maquina)

        # Identificar la máquina ante Weizhou: por UUID si está mapeada, o
        # por código externo (= worker_id) como alternativa.
        datos_maquina = self.mapeo.get(maquina, {})
        cuerpo = {
            "en_uso": bool(en_uso),
            "estado": nuevo,
            "evento": self._etiqueta_evento(anterior, nuevo),
            "origen": "poccam-concentrador",
            "timestamp": datetime.now().astimezone().isoformat(
                timespec="seconds"),
        }
        if datos_maquina.get("maquina_id"):
            cuerpo["maquina_id"] = datos_maquina["maquina_id"]
        else:
            cuerpo["codigo"] = maquina
        if datos_maquina.get("camara_id"):
            cuerpo["camara_id"] = datos_maquina["camara_id"]
        if confianza is not None:
            cuerpo["confianza"] = float(confianza)
        if notas:
            cuerpo["notas"] = notas[:200]
        if payload:
            cuerpo["payload"] = payload
        if evento_id:
            cuerpo.setdefault("payload", {})
            cuerpo["payload"]["evento_id"] = evento_id

        if not self._post(cuerpo):
            # Marcar como pendiente: si no, un fallo seguido de un cambio
            # de ida y vuelta haría que el próximo estado "parezca igual"
            # y se descarte, perdiendo la transición para siempre.
            with self._lock:
                self._pendientes.add(maquina)
            return "error"

        with self._lock:
            self._estados[maquina] = nuevo
            self._pendientes.discard(maquina)
            self._guardar_estados()
        self.enviados += 1
        logger.info("🏭 Weizhou: %s → %s%s", maquina, nuevo,
                    f" (antes {anterior})" if anterior else "")
        return "enviado"

    @staticmethod
    def _etiqueta_evento(anterior: str | None, nuevo: str) -> str:
        """Etiqueta informativa del evento (la API la usa para la bitácora)."""
        if anterior is None:
            return "estado"
        if nuevo == ESTADO_EN_USO:
            return "inicio"
        return "fin"

    # ── Procesamiento de un análisis de la IA ─────────────────────

    def procesar_analisis(self, worker_id: str, analisis: dict) -> str:
        """Traduce un análisis de la IA y lo envía si hubo transición.

        Espera que `analisis["datos"]` tenga el campo `en_uso` (esquema
        `estado_equipo_v1`). Si no lo tiene, no envía nada.
        """
        datos = analisis.get("datos") or {}
        if "en_uso" not in datos:
            logger.debug("Weizhou: análisis de %s sin campo 'en_uso'; se omite",
                         worker_id)
            return "omitido"

        confianza = datos.get("confianza")
        notas = str(datos.get("notas") or "")
        meta = analisis.get("meta") or {}

        # El JSON completo de la IA viaja en `payload` para trazabilidad.
        payload = dict(datos)
        if meta:
            payload["_meta"] = {
                "modelo": meta.get("modelo"),
                "area_px": meta.get("area_px"),
                "score": meta.get("score"),
            }

        return self.enviar_si_cambia(
            maquina=worker_id,
            en_uso=bool(datos.get("en_uso")),
            confianza=confianza if isinstance(confianza, (int, float)) else None,
            notas=notas,
            payload=payload,
            evento_id=analisis.get("evento_id"),
        )
