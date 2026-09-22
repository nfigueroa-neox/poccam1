"""Detector de Cambios para Paneles Industriales

Backend que:
  1. Pide una imagen a la cámara a intervalos configurables.
  2. Compara con la anterior (SSIM por defecto).
  3. Solo cuando detecta un cambio: guarda la imagen y registra
     el evento en eventos.jsonl (log estructurado).

Uso:
    python main.py                    # usa config.yaml
    python main.py --intervalo 0.5    # parametrizar el sondeo
    python main.py --camara 0         # cámara USB
    python main.py --camara "rtsp://..."  # cámara IP
"""

import argparse
import logging
import sys
import threading
import time

from backend.capturador import crear_capturador, rotar_frame
from backend.config import Config
from backend.detector import DetectorCambios
from backend.registrador import RegistradorEventos

logger = logging.getLogger("main")

# Segundos de espera entre reintentos de conexión con la cámara
# (si está apagada, sin red o el stream aún no arrancó).
REINTENTO_SEGUNDOS = 5.0

# Cuántas capturas sin frame nuevo antes de avisar que la cámara está
# congelada (a 2 capturas/s, 10 equivale a ~5 s sin señal).
_AVISO_FRAMES = 10


class MonitorBackend:
    """Bucle principal: capturar → detectar → registrar (si hay cambio)."""

    def __init__(self, config: Config):
        self.config = config
        self._configurar_logging()

        # Lock para intercambiar el capturador en caliente sin que el
        # bucle de monitoreo lo esté usando a la vez (cambio de cámara
        # por API sin reiniciar el proceso).
        self._lock_capturador = threading.RLock()

        # Motivo por el que no hay capturador (None si la cámara abrió bien).
        self._error_capturador: str | None = None
        self.detener = threading.Event()
        # Se activa cuando la cámara aparece, para despertar al bucle.
        self._evento_capturador = threading.Event()

        # Puede devolver None: el worker arranca igual sin cámara, para que
        # su API funcione y el panel no quede vacío sin explicación.
        self.capturador = self._crear_capturador()
        self.detector = DetectorCambios(
            metodo=config.metodo,
            umbral=config.umbral,
            min_area_px=config.min_area_px,
            blur_ksize=config.blur_ksize,
            marcar_cambios=config.marcar_cambios,
            frames_estables=config.frames_estables,
            alinear_imagenes=config.alinear_imagenes,
            max_desplazamiento=config.max_desplazamiento,
        )
        # El análisis IA arranca SIEMPRE DISPONIBLE pero DETENIDO:
        # nunca se auto-activa al iniciar (para no gastar). El botón del
        # panel lo activa por sesión. Esto sobreescribe el YAML.
        self.config.ia_enabled = False

        self.registrador = RegistradorEventos(config)

        # Detectar la resolución real del stream y aplicar el preset
        # correspondiente (uno de PRESETS_CAMARA), si está habilitado.
        self._detectar_resolucion_y_preset()

        self._iniciar_web()
        self._describir_configuracion()

        self.conteo_capturas = 0
        self.conteo_cambios = 0
        self._ultimo_evento = 0.0

        # Vigilancia de cámara congelada: capturar() devuelve el último
        # frame válido aunque la cámara se haya caído, así que se sigue el
        # contador de frames del capturador para detectarlo.
        self._frames_vistos = -1
        self._frames_sin_avanzar = 0
        self._congelada = False

        # Si arrancamos sin cámara, reintentar en segundo plano: así no hay
        # que reiniciar el proceso cuando la cámara aparezca.
        if self.capturador is None:
            threading.Thread(target=self._bucle_captura, daemon=True).start()

    def _crear_capturador(self):
        """Intenta crear el capturador UNA vez.

        Si la cámara no responde devuelve None en vez de bloquear: el worker
        debe arrancar igual, para que su API (y el panel) funcionen aunque no
        haya imagen. Si esto esperara en un bucle, el servidor web nunca se
        crearía y el panel quedaría en blanco sin explicación.

        El bucle de reconexión vive en `_bucle_captura`, que reintenta en
        segundo plano y engancha el capturador cuando la cámara aparece.
        """
        try:
            return crear_capturador(self.config)
        except RuntimeError as e:
            logger.error("❌ No se pudo abrir la fuente de imágenes: %s", e)
            logger.warning(
                "⚠️ El worker arranca SIN imagen: la API y el panel funcionan, "
                "pero no habrá detecciones hasta que la cámara responda."
            )
            self._error_capturador = str(e)
            return None

    def _bucle_captura(self):
        """Reintenta conectar la cámara en segundo plano.

        Corre en su propio hilo para no bloquear el arranque. Cuando la
        cámara aparece, la engancha y sale. Si nunca aparece, el worker sigue
        vivo reportando `sin_senal`.
        """
        while not self.detener.is_set():
            try:
                time.sleep(REINTENTO_SEGUNDOS)
            except KeyboardInterrupt:
                return
            if self.detener.is_set():
                return
            try:
                nuevo = crear_capturador(self.config)
            except RuntimeError as e:
                self._error_capturador = str(e)
                logger.debug("Cámara aún no disponible: %s", e)
                continue
            with self._lock_capturador:
                self.capturador = nuevo
            self._error_capturador = None
            logger.info("✅ Cámara conectada: el monitoreo continúa")
            with self._lock_capturador:
                _ = self._evento_capturador
            self._evento_capturador.set()
            return

    def cambiar_fuente(self, fuente: str, tipo_fuente: str = "camara"):
        """Cambia la cámara EN CALIENTE (sin reiniciar el proceso).

        Crea el capturador nuevo PRIMERO: si falla (URL mal, cámara
        apagada), mantiene el actual funcionando y lanza RuntimeError
        para que el llamador (API) devuelva el error. Si el nuevo abre
        bien, reemplaza y cierra el viejo.

        El ROI y los contadores se conservan a propósito.
        """
        viejo = self.capturador
        fuente_anterior = self.config.camara_fuente
        tipo_anterior = self.config.fuente
        self.config.fuente = tipo_fuente
        self.config.camara_fuente = str(fuente)
        try:
            # No usar _crear_capturador: aquí queremos fallar rápido y
            # devolver el error al llamador (la API del panel).
            nuevo = crear_capturador(self.config)
        except RuntimeError as e:
            # Revertir la config: el capturador viejo sigue en uso
            self.config.fuente = tipo_anterior
            self.config.camara_fuente = fuente_anterior
            logger.error("❌ No se pudo abrir la nueva cámara: %s", e)
            raise

        with self._lock_capturador:
            self.capturador = nuevo
        try:
            viejo.cerrar()
        except Exception:  # noqa: BLE001 — el viejo ya no importa
            pass
        logger.info(f"📷 Fuente de cámara cambiada a: {fuente}")
        # Con la cámara nueva cambia la resolución → re-detectar preset
        try:
            self._detectar_resolucion_y_preset()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"No se pudo re-detectar la resolución: {e}")
        # Avisar al panel: si no, seguiría mapeando el ROI con la resolución
        # anterior y el área dibujada no coincidiría con la analizada.
        self._avisar_config_cambiada()
        return True

    def _avisar_config_cambiada(self):
        """Notifica al panel que la config cambió (por SSE) para que refresque."""
        try:
            from backend.web import notificar_config_cambiada
            notificar_config_cambiada()
        except Exception as e:  # noqa: BLE001 — no debe tumbar el cambio
            logger.warning(f"No se pudo avisar del cambio de config: {e}")

    def _detectar_resolucion_y_preset(self):
        """Lee un frame del stream, detecta su resolución y aplica el
        preset del panel web que le corresponde (exacto o el más
        cercano). Solo aplica con fuente = cámara."""
        if self.config.fuente != "camara":
            return
        if self.capturador is None:
            # Sin cámara no hay resolución que detectar; se hará cuando
            # la cámara aparezca (el bucle de reconexión avisa).
            return
        from backend.web import buscar_preset

        try:
            frame = self.capturador.capturar()
            alto, ancho = frame.shape[:2]
        except Exception:
            logger.warning(
                "No se pudo leer un frame para detectar la resolución"
            )
            return

        self.config.camara_resolucion = (ancho, alto)
        preset = buscar_preset(ancho, alto)
        if preset is None:
            logger.info(f"📷 Resolución detectada: {ancho}x{alto} — sin preset")
            return

        self.config.preset_aplicado = preset["id"]
        if self.config.aplicar_preset_al_iniciar:
            # Aplicar el preset (valores de partida para esa resolución)
            self.detector.actualizar(preset["deteccion"])
            for clave, valor in preset["deteccion"].items():
                setattr(self.config, clave, valor)
            try:
                self.config.guardar()
            except OSError as e:
                logger.warning(f"No se pudo persistir el preset: {e}")
            logger.info(f"📷 Resolución detectada: {ancho}x{alto} → "
                        f"preset '{preset['nombre']}' aplicado")
        else:
            logger.info(f"📷 Resolución detectada: {ancho}x{alto} → "
                        f"preset '{preset['nombre']}' sugerido "
                        "(aplicar_preset_al_iniciar: false)")

    def _iniciar_web(self):
        """Arranca la API HTTP del worker (JSON + video).

        `threaded=True` es OBLIGATORIO: el panel usa varias conexiones SSE
        (`/api/estado`, `/api/eventos`, `/api/eventos-analisis`,
        `/api/config-eventos`) que permanecen abiertas indefinidamente. Con
        el servidor de un solo hilo, la primera conexión SSE bloquea el
        resto y el panel deja de actualizarse.
        """
        if not self.config.web_enabled:
            return
        from backend.web import crear_app
        app = crear_app(self.capturador, config=self.config,
                        detector=self.detector, monitor=self)
        hilo = threading.Thread(
            target=app.run,
            kwargs={"host": self.config.web_host,
                    "port": self.config.web_port,
                    "debug": False,
                    "use_reloader": False,
                    "threaded": True},
            daemon=True,
        )
        hilo.start()
        self._url_web = f"http://localhost:{self.config.web_port}"
        logger.info(f"🔌 API del worker: {self._url_web}  "
                    f"(el panel se sirve en el concentrador)")

    def _configurar_logging(self):
        nivel = getattr(logging, self.config.nivel_log.upper(), logging.INFO)
        logging.basicConfig(
            level=nivel,
            format="%(asctime)s | %(levelname)-7s | %(message)s",
            datefmt="%H:%M:%S",
        )

    def _describir_configuracion(self):
        if self.config.fuente == "camara":
            detalle = f"cámara '{self.config.nombre_camara}' ({self.config.camara_fuente})"
        else:
            detalle = f"pantalla (monitor {self.config.monitor})"
        if self.config.region:
            detalle += f" región {self.config.region}"

        logger.info("══════════════════════════════════════════")
        logger.info("Backend de monitoreo de paneles iniciado")
        logger.info(f"Fuente:       {detalle}")
        logger.info(f"Sondeo:       cada {self.config.intervalo_segundos}s")
        logger.info(f"Detector:     {self.config.metodo} | umbral={self.config.umbral}")
        logger.info(f"Log eventos:  {self.config.log_eventos}")
        logger.info(f"Imágenes:     {self.config.output_dir}")
        logger.info("══════════════════════════════════════════")
        if self.config.web_enabled:
            logger.info(f"Área de análisis: define la ROI en {self._url_web}")
        logger.info("Ctrl+C para detener.")
        logger.info("══════════════════════════════════════════")

    def ejecutar(self):
        primera = True

        try:
            while True:
                inicio = time.monotonic()

                # 1. PEDIR imagen a la cámara
                with self._lock_capturador:
                    capturador = self.capturador

                # Sin cámara todavía: esperar sin girar en vacío. El worker
                # sigue vivo (su API y el panel funcionan) y arranca el
                # monitoreo en cuanto la cámara aparezca.
                if capturador is None:
                    self._evento_capturador.wait(timeout=REINTENTO_SEGUNDOS)
                    self._evento_capturador.clear()
                    continue

                imagen = capturador.capturar()
                self.conteo_capturas += 1

                # Vigilar que la cámara ENTREGUE frames nuevos. Si se
                # desconecta, capturar() sigue devolviendo el último frame
                # válido (congelado), el detector no ve cambio y el sistema
                # parece "vivo" sin estarlo. Avisamos una sola vez.
                recibidos = getattr(capturador, "frames_recibidos", None)
                if recibidos is not None:
                    if recibidos != self._frames_vistos:
                        if self._congelada:  # acaba de recuperarse
                            logger.info(
                                "✅ La cámara vuelve a entregar frames nuevos")
                            self._congelada = False
                            # Descartar la referencia vieja: comparar contra
                            # un frame de hace minutos daría un cambio falso
                            # gigante y un evento + análisis de IA inútiles.
                            try:
                                self.detector.reiniciar_referencia()
                            except AttributeError:
                                pass
                        self._frames_vistos = recibidos
                        self._frames_sin_avanzar = 0
                    else:
                        self._frames_sin_avanzar += 1
                        if (self._frames_sin_avanzar == _AVISO_FRAMES
                                and not self._congelada):
                            self._congelada = True
                            logger.error(
                                "⚠️ La cámara NO entrega frames nuevos desde "
                                "hace %d capturas: la imagen está congelada. "
                                "Se sigue comparando el último frame válido; "
                                "no habrá detecciones hasta que la señal vuelva.",
                                self._frames_sin_avanzar)

                # Aplicar rotación configurada (0/90/180/270)
                if self.config.rotacion:
                    imagen = rotar_frame(imagen, self.config.rotacion)

                # Con la cámara caída no se analiza: comparar el mismo frame
                # congelado solo produiría ruido de compresión y eventos
                # falsos (y gastaría llamadas a la IA).
                if self._congelada:
                    self._esperar(inicio)
                    continue

                # 2. Comparar contra la anterior
                resultado = self.detector.procesar(imagen)

                if primera:
                    alto, ancho = imagen.shape[:2]
                    logger.info(f"Primera captura lista ({ancho}x{alto}) — "
                                f"desde ahora se comparan imágenes")
                    primera = False
                    self._esperar(inicio)
                    continue

                # 3. ¿Hubo cambio? → registrar
                if resultado["hubo_cambio"]:
                    # Freno anti-spam: ignora eventos que ocurren muy seguido
                    # (ej: un LED que parpadea cada segundo). Solo registra
                    # si pasó el tiempo mínimo desde el último evento.
                    ahora = time.monotonic()
                    if ahora - self._ultimo_evento >= \
                            self.config.min_intervalo_eventos:
                        self._ultimo_evento = ahora
                        self.conteo_cambios += 1
                        evento = {
                            # Guardar SOLO el área analizada (ROI si está
                            # definido) — así la imagen refleja exactamente
                            # lo que disparó el detector.
                            "imagen": resultado["imagen_analizada"],
                            "imagen_marcada": resultado["imagen_marcada"],
                            # Contra qué se comparó y cuándo: es lo que explica
                            # el evento en el panel (antes/después).
                            "imagen_referencia": resultado.get("imagen_referencia"),
                            "referencia_desde": resultado.get("referencia_desde"),
                            "score": resultado["score"],
                            "area_px": resultado["area_px"],
                            "area_borde": resultado.get("area_borde", 0),
                            "area_total": resultado.get("area_total", 0),
                            "metodo": self.config.metodo,
                            "camara": self._nombre_fuente(),
                            "capturas_total": self.conteo_capturas,
                        }
                        self.registrador.registrar(evento)
                    else:
                        logger.debug(
                            "Cambio ignorado (freno anti-spam activo)"
                        )
                else:
                    logger.debug(
                        f"Sin cambio (score={resultado['score']:.4f}) "
                        f"[captura {self.conteo_capturas}]"
                    )

                # 4. Estadísticas periódicas
                if self.conteo_capturas % 100 == 0:
                    tasa = self.conteo_cambios / self.conteo_capturas * 100
                    logger.info(
                        f"📊 {self.conteo_capturas} capturas | "
                        f"{self.conteo_cambios} cambios ({tasa:.1f}%)"
                    )

                self._esperar(inicio)

        except KeyboardInterrupt:
            self._resumen()
        except Exception:
            logger.exception("Error en el bucle de monitoreo")
            self._resumen()
            sys.exit(1)

    def _nombre_fuente(self) -> str:
        return self.config.nombre_camara

    def _esperar(self, inicio: float):
        """Espera el tiempo restante para respetar el intervalo configurado."""
        restante = self.config.intervalo_segundos - (time.monotonic() - inicio)
        if restante > 0:
            time.sleep(restante)

    def _resumen(self):
        logger.info("══════════════════ RESUMEN ══════════════════")
        logger.info(f"Total capturas: {self.conteo_capturas}")
        logger.info(f"Eventos registrados: {self.conteo_cambios}")
        if self.conteo_capturas > 0:
            logger.info(
                f"Tasa de cambios: "
                f"{self.conteo_cambios / self.conteo_capturas * 100:.2f}%"
            )
        logger.info(f"Log: {self.config.log_eventos}")
        logger.info("═════════════════════════════════════════════")
        self.capturador.cerrar()


def main():
    parser = argparse.ArgumentParser(
        description="Backend detector de cambios en paneles industriales"
    )
    parser.add_argument("--config", "-c", default="config.yaml",
                        help="Ruta al YAML de configuración")
    parser.add_argument("--intervalo", "-i", type=float, default=None,
                        help="Sobreescribe el intervalo de sondeo (segundos)")
    parser.add_argument("--camara", "-k", default=None,
                        help="Sobreescribe la fuente: índice USB o URL RTSP")
    parser.add_argument("--umbral", "-u", type=float, default=None,
                        help="Sobreescribe el umbral de detección (0-1)")
    parser.add_argument("--region", "-r", type=int, nargs=4, default=None,
                        help="Región de pantalla: left top width height")
    parser.add_argument("--metodo", "-m", default=None,
                        choices=["ssim", "diff", "mse"],
                        help="Método de detección")
    parser.add_argument("--marcar", action="store_true",
                        help="Dibujar contornos rojos sobre los cambios")
    args = parser.parse_args()

    config = Config.from_yaml(args.config)

    if args.intervalo is not None:
        config.intervalo_segundos = args.intervalo
    if args.umbral is not None:
        config.umbral = args.umbral
    if args.region is not None:
        config.region = list(args.region)
    if args.metodo is not None:
        config.metodo = args.metodo
    if args.marcar:
        config.marcar_cambios = True
    if args.camara is not None:
        config.fuente = "camara"
        config.camara_fuente = args.camara

    monitor = MonitorBackend(config)
    monitor.ejecutar()


if __name__ == "__main__":
    main()
